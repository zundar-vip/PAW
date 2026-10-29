import asyncio
import logging
import datetime
from typing import Optional

import aiohttp
from aiohttp import web

from config import WEBHOOK_PORT, SEPAY_TOKEN, SEPAY_POLL_INTERVAL, ACCOUNT_NO
import db
import pay

log = logging.getLogger(__name__)

SEPAY_ENDPOINTS = [
    ("https://userapi.sepay.vn/v2/transactions", "v2"),
    ("https://my.sepay.vn/userapi/transactions/list", "userapi"),
]

_credit_fn = None
_notify_admin_tx_fn = None

_seen_tx_ids: set[str] = set()
_first_fetch_logged = False


def set_credit_callback(fn):
    global _credit_fn
    _credit_fn = fn


def set_notify_admin_callback(fn):
    global _notify_admin_tx_fn
    _notify_admin_tx_fn = fn


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
                log.error(f"SePay poll error: {e}", exc_info=True)
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
    global _first_fetch_logged

    headers = {
        "Authorization": f"Bearer {SEPAY_TOKEN.strip()}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    for url, tag in SEPAY_ENDPOINTS:
        params = {"limit": limit}
        if tag == "userapi":
            params["account_number"] = ACCOUNT_NO.replace(" ", "")

        try:
            async with session.get(url, headers=headers, params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                text = await resp.text()

                if resp.status != 200:
                    log.debug(f"SePay[{tag}] HTTP {resp.status}: {text[:200]}")
                    continue

                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    log.warning(f"SePay[{tag}] non-JSON: {text[:200]}")
                    continue

                if not _first_fetch_logged:
                    log.info(f"SePay[{tag}] first response keys: {list(body.keys()) if isinstance(body, dict) else type(body)}")
                    if isinstance(body, dict) and body:
                        for k in ("transactions", "data", "messages"):
                            v = body.get(k)
                            if isinstance(v, list) and v:
                                log.info(f"  └ {k}[0] keys: {list(v[0].keys())}")
                                break
                    _first_fetch_logged = True

                txs = _extract_tx_list(body)
                if txs:
                    log.debug(f"SePay[{tag}] → {len(txs)} tx")
                    return txs

                err = body.get("error") if isinstance(body, dict) else None
                if err not in (None, 0, "0", False, ""):
                    log.debug(f"SePay[{tag}] error field: {err}")

        except Exception as e:
            log.debug(f"SePay[{tag}] exception: {e}")
            continue

    log.warning("SePay: all endpoints returned empty/error")
    return []


def _extract_tx_list(body) -> list[dict]:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []

    for path in (
        ("transactions",),
        ("data",),
        ("messages", "transactions"),
        ("messages", "data"),
        ("data", "transactions"),
        ("result", "transactions"),
        ("result", "data"),
    ):
        cur = body
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and isinstance(cur, list) and cur:
            return cur

    return []


def _tx_amount_in(tx: dict) -> int:
    for k in ("amount_in", "transferAmount", "credit", "amount"):
        v = tx.get(k)
        if v is None:
            continue
        try:
            n = int(v)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return 0


def _tx_content(tx: dict) -> str:
    return str(
        tx.get("transaction_content")
        or tx.get("content")
        or tx.get("description")
        or ""
    )


def _tx_ref(tx: dict) -> Optional[str]:
    ref = pay.parse_ref(_tx_content(tx))
    if ref:
        return ref
    code = str(tx.get("code") or "").strip()
    if code:
        return pay.parse_ref(code.upper())
    return None


async def _poll_once(session: aiohttp.ClientSession):
    txs = await _fetch_transactions(session, limit=20)
    if not txs:
        return

    new_count = 0
    for tx in txs:
        tx_id = str(tx.get("id", ""))
        if not tx_id or tx_id in _seen_tx_ids:
            continue
        _seen_tx_ids.add(tx_id)
        new_count += 1

        amount_in = _tx_amount_in(tx)
        if amount_in <= 0:
            continue

        content = _tx_content(tx)
        log.info(f"[poll] new tx id={tx_id} amount={amount_in} content={content[:80]!r}")
        await _process({"amount": amount_in, "description": content}, source="poll")

    if new_count:
        log.info(f"[poll] processed {new_count} new tx")


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

    transfer_type = body.get("transferType", "in")
    if transfer_type != "in":
        return web.json_response({"success": True})

    amount  = int(body.get("transferAmount") or body.get("credit") or body.get("amount_in") or 0)
    content = str(body.get("content") or body.get("transaction_content") or "")

    if amount > 0:
        await _process({"amount": amount, "description": content}, source="webhook")

    return web.json_response({"success": True})


async def health(_: web.Request) -> web.Response:
    return web.Response(text="OK")


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


async def check_payment_now(ref: str, limit: int = 30) -> dict:
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

    if pmt.get("expires_at"):
        try:
            if datetime.datetime.now() > datetime.datetime.fromisoformat(pmt["expires_at"]):
                return {"found": False, "reason": "expired"}
        except Exception:
            pass

    try:
        async with aiohttp.ClientSession() as session:
            txs = await _fetch_transactions(session, limit=limit)
        
        for tx in txs:
            if _tx_ref(tx) != ref:
                continue
            amount = _tx_amount_in(tx)
            if amount <= 0:
                continue
            
            tx_id = str(tx.get("id", ""))
            _seen_tx_ids.add(tx_id)
            
            log.info(f"[manual_check] matched tx_id={tx_id} ref={ref}")
            await _process({"amount": amount, "description": _tx_content(tx)}, source="manual_check")
            return {"found": True, "reason": "success"}
            
    except Exception as e:
        log.error(f"check_payment_now error: {e}")
        return {"found": False, "reason": "fetch_error"}

    return {"found": False, "reason": "not_yet"}


async def watch_payment_ref(ref: str, uid: int,
                            poll_interval: int = 5,
                            timeout_secs: int = 900):
    deadline = asyncio.get_event_loop().time() + timeout_secs
    await asyncio.sleep(3)

    async with aiohttp.ClientSession() as session:
        while True:
            if asyncio.get_event_loop().time() > deadline:
                log.debug(f"watch_payment_ref: timeout ref={ref}")
                return

            if db.get_payment_by_ref(ref) is None:
                return

            try:
                txs = await _fetch_transactions(session, limit=20)
                for tx in txs:
                    if _tx_ref(tx) != ref:
                        continue
                    amount = _tx_amount_in(tx)
                    if amount <= 0:
                        continue
                    tx_id = str(tx.get("id", ""))
                    _seen_tx_ids.add(tx_id)
                    log.info(f"watch_payment_ref: matched tx_id={tx_id} ref={ref}")
                    await _process({"amount": amount, "description": _tx_content(tx)},
                                   source="watch")
                    return

            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning(f"watch_payment_ref poll error ref={ref}: {e}")

            await asyncio.sleep(poll_interval)


async def _handle_expired(bot, pmt: dict, kb_main_fn):
    fresh = db.get_payment_by_id(pmt["payment_id"])
    if fresh and fresh.get("status") == "completed":
        log.info(f"skip expired notify: {pmt['payment_id']} already completed")
        return

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
            "─────────────────────────────\n"
            "Mã QR hết hạn không có giao dịch.\n\n"
        
            f"👋 Chào <b>{name}</b>!\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💰 Số dư: <b>{pay.fmt(user['balance'])}</b>\n\n"
            "⬇️ Chọn thao tác bên dưới:"
        )
    else:
        welcome = "⏰ <b>QR CODE ĐÃ HẾT HẠN</b>\n\nVui lòng chọn lại."

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


def build_server() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook/sepay", handle_sepay)
    app.router.add_get("/health",         health)
    return app
