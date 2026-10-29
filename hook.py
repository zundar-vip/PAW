# hook.py — SePay polling + webhook
import asyncio
import logging
import datetime

import aiohttp
from aiohttp import web

from config import WEBHOOK_PORT, SEPAY_TOKEN, SEPAY_POLL_INTERVAL, ACCOUNT_NO
import db
import pay

log = logging.getLogger(__name__)

SEPAY_API_BASE  = "https://userapi.sepay.vn"
SEPAY_TX_LIST   = f"{SEPAY_API_BASE}/v2/transactions"

_credit_fn = None
_notify_admin_tx_fn = None
_seen_tx_ids: set[str] = set()


def set_credit_callback(fn):
    global _credit_fn
    _credit_fn = fn


def set_notify_admin_callback(fn):
    global _notify_admin_tx_fn
    _notify_admin_tx_fn = fn


# ─────────────────────────── SEPAY POLLING ──────────────────────────

async def poll_sepay_loop():
    log.info(f"SePay polling started — interval={SEPAY_POLL_INTERVAL}s")
    await _init_seen()

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await _poll_once(session)
            except asyncio.CancelledError:
                log.info("SePay polling cancelled")
                return
            except Exception as e:
                log.error(f"SePay poll error: {e}")
            await asyncio.sleep(SEPAY_POLL_INTERVAL)


async def _init_seen():
    try:
        async with aiohttp.ClientSession() as s:
            txs = await _fetch_transactions(s, limit=50)
            for tx in txs:
                _seen_tx_ids.add(str(tx.get("id", "")))
        log.info(f"SePay: seeded {len(_seen_tx_ids)} known transactions")
    except Exception as e:
        log.warning(f"SePay init_seen failed (continuing): {e}")


async def _fetch_transactions(session: aiohttp.ClientSession, limit: int = 20) -> list[dict]:
    headers = {"Authorization": f"Bearer {SEPAY_TOKEN}"}
    params  = {
        "limit":          limit,
        "account_number": ACCOUNT_NO.replace(" ", ""),
    }
    async with session.get(SEPAY_TX_LIST, headers=headers, params=params,
                           timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            text = await resp.text()
            log.warning(f"SePay API HTTP {resp.status}: {text[:300]}")
            return []

        body = await resp.json(content_type=None)

        st = body.get("status")
        if st and st != "success":
            log.warning(f"SePay API status != success: {str(body)[:300]}")
            return []

        err = body.get("error")
        if err is not None and err != 0:
            log.warning(f"SePay API error: {str(body)[:300]}")
            return []

        txs = body.get("data") or body.get("messages", {}).get("data") or []
        if not isinstance(txs, list):
            log.warning(f"SePay unexpected data type: {type(txs)} — {str(txs)[:200]}")
            return []
        return txs


def _tx_amount_in(tx: dict) -> int:
    return int(tx.get("amount_in") or tx.get("transferAmount") or 0)


def _tx_content(tx: dict) -> str:
    return str(tx.get("transaction_content") or tx.get("content") or "")


async def _poll_once(session: aiohttp.ClientSession):
    txs = await _fetch_transactions(session, limit=20)
    for tx in txs:
        tx_id = str(tx.get("id", ""))
        if not tx_id or tx_id in _seen_tx_ids:
            continue
        _seen_tx_ids.add(tx_id)
        amount_in = _tx_amount_in(tx)
        content   = _tx_content(tx)
        if amount_in <= 0:
            continue
        await _process({"amount": amount_in, "description": content}, source="poll")


# ─────────────────────────── WEBHOOK ────────────────────────────────

async def handle_sepay(req: web.Request) -> web.Response:
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"success": False}, status=400)

    tx_id = str(body.get("id", ""))
    if tx_id and tx_id in _seen_tx_ids:
        return web.json_response({"success": True})
    if tx_id:
        _seen_tx_ids.add(tx_id)

    if body.get("transferType", "in") != "in":
        return web.json_response({"success": True})

    amount  = int(body.get("transferAmount") or body.get("credit") or body.get("amount_in") or 0)
    content = str(body.get("content") or body.get("transaction_content") or "")

    if amount > 0:
        await _process({"amount": amount, "description": content}, source="webhook")
    return web.json_response({"success": True})


async def health(_: web.Request) -> web.Response:
    return web.Response(text="OK")


# ─────────────────────────── CORE PROCESS ───────────────────────────

async def _process(rec: dict, source: str = "?"):
    amount = int(rec.get("amount", 0))
    desc   = str(rec.get("description", ""))
    if amount <= 0:
        return

    ref = pay.parse_ref(desc)
    pmt = db.get_payment_by_ref(ref) if ref else None
    uid = pmt["user_id"] if pmt else None
    user = db.get_user(uid) if uid else None
    matched = (pmt is not None and pmt.get("status") == "pending")

    if _notify_admin_tx_fn:
        try:
            await _notify_admin_tx_fn(amount, desc, user, ref, matched)
        except Exception as e:
            log.error(f"notify_admin_tx error: {e}")

    if not ref:
        log.debug(f"[{source}] No ref in: {desc!r}")
        return
    if not pmt:
        log.debug(f"[{source}] No pending payment for ref={ref}")
        return
    if pmt["status"] != "pending":
        log.debug(f"[{source}] Already processed: {ref}")
        return

    expected = pmt["amount"]
    if amount < expected:
        log.warning(f"[{source}] Underpayment: ref={ref} expected={expected} got={amount}")

    log.info(f"[{source}] ✅ Payment confirmed: ref={ref} uid={uid} amount={amount}")
    if _credit_fn:
        await _credit_fn(uid, amount, ref, pmt["payment_id"])


# ─────────────────────────── PER-PAYMENT WATCHER ────────────────────

async def watch_payment_ref(ref: str, uid: int, poll_interval: int = 5, timeout_secs: int = 900):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_secs
    await asyncio.sleep(3)

    async with aiohttp.ClientSession() as session:
        while True:
            if loop.time() > deadline:
                log.debug(f"watch_payment_ref: timeout ref={ref}")
                return
            pmt = db.get_payment_by_ref(ref)
            if pmt is None:
                return
            try:
                txs = await _fetch_transactions(session, limit=20)
                for tx in txs:
                    tx_id = str(tx.get("id", ""))
                    if not tx_id or tx_id in _seen_tx_ids:
                        continue
                    amount_in = _tx_amount_in(tx)
                    content   = _tx_content(tx)
                    if amount_in <= 0:
                        continue
                    if pay.parse_ref(content) != ref:
                        continue
                    _seen_tx_ids.add(tx_id)
                    log.info(f"watch_payment_ref: matched tx_id={tx_id} ref={ref}")
                    await _process({"amount": amount_in, "description": content}, source="watch")
                    return
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning(f"watch_payment_ref poll error ref={ref}: {e}")
            await asyncio.sleep(poll_interval)


# ─────────────────────────── MANUAL CHECK (FIX TREO QR) ─────────────

async def check_payment_now(ref: str) -> dict:
    """Kiểm tra giao dịch ngay lập tức khi user bấm nút Check Bank"""
    # 1. Kiểm tra trạng thái trong DB trước
    try:
        c = db._conn()
        row = c.execute("SELECT status FROM pending_payments WHERE order_ref=?", (ref,)).fetchone()
        c.close()
        if row:
            if row["status"] == "completed":
                return {"found": True, "reason": "already_completed"}
            elif row["status"] == "expired":
                return {"found": False, "reason": "expired"}
    except Exception:
        pass

    pmt = db.get_payment_by_ref(ref)
    if not pmt:
        return {"found": False, "reason": "not_found"}

    # 2. Kiểm tra hết hạn
    if pmt.get("expires_at"):
        try:
            if datetime.datetime.now() > datetime.datetime.fromisoformat(pmt["expires_at"]):
                return {"found": False, "reason": "expired"}
        except Exception:
            pass

    # 3. Gọi API SePay để quét ngay lập tức
    try:
        async with aiohttp.ClientSession() as session:
            txs = await _fetch_transactions(session, limit=20)
            for tx in txs:
                amount_in = _tx_amount_in(tx)
                content = _tx_content(tx)
                if amount_in <= 0:
                    continue
                if pay.parse_ref(content) == ref:
                    tx_id = str(tx.get("id", ""))
                    if tx_id not in _seen_tx_ids:
                        _seen_tx_ids.add(tx_id)
                        # Kích hoạt xử lý tạo key
                        await _process({"amount": amount_in, "description": content}, source="manual_check")
                    return {"found": True}
    except Exception as e:
        log.error(f"check_payment_now error: {e}")
        return {"found": False, "reason": "fetch_error"}

    return {"found": False, "reason": "not_yet"}


# ─────────────────────────── QR EXPIRE LOOP ─────────────────────────

async def _handle_expired(bot, pmt: dict, kb_main_fn):
    uid    = pmt["user_id"]
    msg_id = pmt.get("message_id", 0)
    if msg_id:
        try:
            await bot.delete_message(chat_id=uid, message_id=msg_id)
        except Exception:
            pass

    user = db.get_user(uid)
    if user:
        name = user.get("full_name") or user.get("username") or "Khách"
        welcome = (
            "⏰ <b>QR CODE ĐÃ HẾT HẠN</b>\n"
            "─────────────────────────\n"
            "Mã QR hết hạn sau <b>15 phút</b> không có giao dịch.\n\n"
            f"👋 Chào <b>{name}</b>!\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💰 Số dư: <b>{pay.fmt(user['balance'])}</b>"
        )
    else:
        welcome = "⏰ <b>QR CODE ĐÃ HẾT HẠN</b>\nVui lòng chọn lại nếu muốn tiếp tục."

    try:
        await bot.send_message(uid, welcome, parse_mode="HTML", reply_markup=kb_main_fn())
    except Exception as e:
        log.warning(f"expire menu uid={uid}: {e}")


async def expire_qr_loop(bot, kb_main_fn):
    while True:
        await asyncio.sleep(60)
        try:
            expired = db.get_expired_payments()
            for pmt in expired:
                db.expire_payment(pmt["payment_id"])
                asyncio.create_task(_handle_expired(bot, pmt, kb_main_fn))
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.error(f"expire loop: {e}")


# ─────────────────────────── SERVER ─────────────────────────────────

def build_server() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook/sepay", handle_sepay)
    app.router.add_get("/health",         health)
    return app
