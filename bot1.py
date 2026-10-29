# bot1.py — PAWNOVA KEY SHOP
import uuid, json, logging, os, asyncio, random, re as _re, time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)
from telegram.constants import ParseMode
from telegram import Bot as _TgBot

from config import (BOT1_TOKEN, BOT2_TOKEN, ADMIN_CHAT_ID,
                    ACCOUNT_NAME as BANK_NAME, ACCOUNT_NO as BANK_ACC,
                    PAWNOVA_WEB_URL)
import db, pay, svc, hook

log = logging.getLogger(__name__)
_admin_bot = _TgBot(token=BOT2_TOKEN)

_last_bot_msg = {}


async def _delete_bot_msg(bot, uid):
    mid = _last_bot_msg.pop(uid, None)
    if mid:
        try:
            await bot.delete_message(uid, mid)
        except Exception:
            pass


def _is_main_kb(kb):
    if not kb or not hasattr(kb, 'inline_keyboard'):
        return False
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data == "menu:buy_key":
                return True
    return False


def _has_back_to_main(kb):
    if not kb or not hasattr(kb, 'inline_keyboard'):
        return False
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data == "back:main":
                return True
    return False


def _with_menu(kb):
    if kb is None:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 MENU", callback_data="back:main")]])
    if _is_main_kb(kb) or _has_back_to_main(kb):
        return kb
    rows = [list(r) for r in kb.inline_keyboard]
    rows.append([InlineKeyboardButton("🏠 MENU", callback_data="back:main")])
    return InlineKeyboardMarkup(rows)


def _is_public_url(url: str) -> bool:
    if not url:
        return False
    u = url.strip().lower()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    m = _re.search(r"://([^/:?#]+)", u)
    if not m:
        return False
    host = m.group(1)
    if host in ("localhost", "0.0.0.0") or host.endswith(".localhost"):
        return False
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        if a == 127: return False
        if a == 10: return False
        if a == 169 and b == 254: return False
        if a == 172 and 16 <= b <= 31: return False
        if a == 192 and b == 168: return False
    return True


async def _send_admin(text):
    try:
        await _admin_bot.send_message(ADMIN_CHAT_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning(f"admin notify: {e}")


async def notify_admin_tx(amount, desc, user, ref, matched):
    if matched and user:
        txt = (f"💰 <b>GIAO DỊCH VÀO</b> ✅\n"
               f"💵 <b>{pay.fmt(amount)}</b>\n"
               f"📝 <code>{desc[:120]}</code>\n"
               f"👤 UID: <code>{user['user_id']}</code> @{user.get('username') or '─'}\n"
               f"💼 Số dư: <b>{pay.fmt(user['balance'])}</b>")
    else:
        txt = (f"💰 <b>GIAO DỊCH VÀO</b> ⚠️\n"
               f"💵 <b>{pay.fmt(amount)}</b>\n"
               f"📝 <code>{desc[:120]}</code>\n"
               f"📌 Ref: <code>{ref or '—'}</code>")
    await _send_admin(txt)


def txt_welcome(u):
    name = u.get("full_name") or u.get("username") or "Khách"
    return (
        "╔═══════════════════════════╗\n"
        "║  🔑  <b>Bot bán key paw</b>  🔑  ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👋 Chào <b>{name}</b>!\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"💰 Số dư: <b>{pay.fmt(u['balance'])}</b>\n"
        f"📦 Tổng đơn: <b>{u['total_orders']}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  <b>VUI LÒNG CHỌN THAO TÁC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )


def txt_profile(u):
    return (
        "👤 <b>THÔNG TIN TÀI KHOẢN</b>\n"
        "─────────────────────────\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"👤 Tên: <b>{u.get('full_name','─')}</b>\n"
        f"📛 Username: @{u.get('username','─')}\n"
        f"💰 Số dư: <b>{pay.fmt(u['balance'])}</b>\n"
        f"📦 Tổng đơn: <b>{u['total_orders']}</b>\n"
        f"💸 Đã chi: <b>{pay.fmt(u['total_spent'])}</b>"
    )


def txt_qr_payment(amount, ref):
    return (
        "╭───────────────────────╮\n"
        "    💳 <b>THANH TOÁN QR</b>\n"
        "╰───────────────────────╯\n"
        f"💰 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        f"🏦 Ngân hàng: <b>BIDV</b>\n"
        f"👤 Chủ TK: <b>{BANK_NAME}</b>\n"
        f"🔢 Số TK: <code>{BANK_ACC}</code>\n"
        "─────────────────────────\n"
        "📌 <b>NỘI DUNG CHUYỂN KHOẢN:</b>\n"
        f"<code>{ref}</code>\n"
        "─────────────────────────\n"
        "⏰ Hết hạn sau <b>15 phút</b>\n"
        "✅ Sau khi chuyển khoản, nhấn <b>🔄 KIỂM TRA THANH TOÁN</b>"
    )


def kb_main():
    rows = [
        [InlineKeyboardButton("🔑  MUA KEY",       callback_data="menu:buy_key")],
        [InlineKeyboardButton("🗝️  KEY CỦA TÔI",   callback_data="menu:my_keys")],
        [InlineKeyboardButton("🎁  NHẬN KEY FREE", callback_data="menu:free_key")],
        [InlineKeyboardButton("💳  NẠP TIỀN",      callback_data="menu:topup"),
         InlineKeyboardButton("👤  TÀI KHOẢN",     callback_data="menu:profile")],
    ]
    if _is_public_url(PAWNOVA_WEB_URL):
        rows.append([InlineKeyboardButton("🌐  MỞ WEBSITE", url=PAWNOVA_WEB_URL)])
    else:
        rows.append([InlineKeyboardButton("🌐  XEM LINK WEB", callback_data="menu:show_web")])
    return InlineKeyboardMarkup(rows)


def kb_packages():
    rows = [[InlineKeyboardButton(f"{p['name']}  ─  {pay.fmt(p['price'])}", callback_data=f"pkg:{pid}")]
            for pid, p in svc.KEY_PACKAGES.items()]
    rows.append([InlineKeyboardButton("◀️  QUAY LẠI", callback_data="back:main")])
    return InlineKeyboardMarkup(rows)


def kb_confirm_key(ref):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ XÁC NHẬN & THANH TOÁN", callback_data=f"confirm_key:{ref}")],
        [InlineKeyboardButton("◀️ QUAY LẠI", callback_data="back:main")],
    ])


def kb_payment(ref):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 KIỂM TRA THANH TOÁN", callback_data=f"checkpay:{ref}")],
        [InlineKeyboardButton("❌ HUỶ THANH TOÁN",         callback_data=f"cancelpay:{ref}")],
    ])


def kb_topup():
    rows, row = [], []
    for a in [50_000, 100_000, 200_000, 500_000, 1_000_000]:
        row.append(InlineKeyboardButton(pay.fmt(a), callback_data=f"tamt:{a}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows += [[InlineKeyboardButton("✏️ NHẬP SỐ KHÁC", callback_data="tamt:custom")],
             [InlineKeyboardButton("◀️ QUAY LẠI", callback_data="back:main")]]
    return InlineKeyboardMarkup(rows)


def kb_back(t="main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ QUAY LẠI", callback_data=f"back:{t}")]])


async def reply(update, text, kb=None, parse_mode=ParseMode.HTML):
    bot = update.get_bot()
    uid = update.effective_user.id

    await _delete_bot_msg(bot, uid)

    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
    elif update.message:
        txt = update.message.text or ""
        if not txt.startswith("/start"):
            try:
                await update.message.delete()
            except Exception:
                pass

    kb = _with_menu(kb)
    msg = await bot.send_message(uid, text, reply_markup=kb, parse_mode=parse_mode)
    _last_bot_msg[uid] = msg.message_id
    return msg


async def ensure_user(update):
    u = update.effective_user
    user = db.get_user(u.id)
    if not user:
        user = db.upsert_user(u.id, u.username or "", u.full_name or "")
    return user


async def show_main(update, _):
    user = await ensure_user(update)
    await reply(update, txt_welcome(user), kb_main())


FREE_ROLLS = [(0.60, 0), (0.80, 10), (0.85, 20), (0.90, 25), (0.93, 30), (1.00, 40)]


def roll_free_key():
    r = random.random()
    for thr, minutes in FREE_ROLLS:
        if r < thr:
            return minutes
    return 0


async def _deliver_key(bot, uid, lic, pkg_name, amount):
    await _delete_bot_msg(bot, uid)

    text = (
        "╔═══════════════════════════╗\n"
        "║  🎉 <b>THANH TOÁN THÀNH CÔNG</b>  ║\n"
        "╚═══════════════════════════╝\n\n"
        f"📦 Gói: <b>{pkg_name}</b>\n"
        f"💵 Số tiền: <b>{pay.fmt(amount)}</b>\n"
        "─────────────────────────\n"
        "🔑 <b>KEY CỦA BẠN:</b>\n"
        f"<code>{lic['key_value']}</code>\n"
        "─────────────────────────\n"
        "🌐 <b>HƯỚNG DẪN:</b>\n"
        f"1. Mở website: <code>{PAWNOVA_WEB_URL}</code>\n"
        "2. Dán key vào ô nhập → nhấn KÍCH HOẠT\n"
        "3. Key chỉ dùng được trên 1 thiết bị — không chia sẻ"
    )
    msg = await bot.send_message(uid, text, parse_mode=ParseMode.HTML, reply_markup=kb_main())
    _last_bot_msg[uid] = msg.message_id


async def _notify_admin_sold(uid, lic, pkg_name, amount, ref, via):
    user = db.get_user(uid)
    uname = (user.get("username") or "─") if user else "─"
    await _send_admin(
        f"🔑 <b>KEY ĐÃ BÁN</b> — {via}\n"
        f"👤 User: @{uname} (<code>{uid}</code>)\n"
        f"📦 Gói: <b>{pkg_name}</b>\n"
        f"💵 Giá: <b>{pay.fmt(amount)}</b>\n"
        f"🔑 Key: <code>{lic['key_value']}</code>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


async def show_qr_key(update, ctx, amount, order_data):
    bot = update.get_bot()
    user = await ensure_user(update)
    uid = user["user_id"]

    ref = f"KEY{uuid.uuid4().hex[:8].upper()}"
    qr = pay.qr_url(amount, ref)
    cap = txt_qr_payment(amount, ref)
    kb = _with_menu(kb_payment(ref))

    await _delete_bot_msg(bot, uid)
    if update.callback_query:
        try: await update.callback_query.message.delete()
        except Exception: pass
    elif update.message:
        try: await update.message.delete()
        except Exception: pass

    try:
        sent = await bot.send_photo(uid, photo=qr, caption=cap,
                                     reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        sent = await bot.send_message(uid, cap, reply_markup=kb, parse_mode=ParseMode.HTML)

    _last_bot_msg[uid] = sent.message_id
    db.new_payment(uid, amount, ref, order_data, sent.message_id)
    asyncio.create_task(hook.watch_payment_ref(ref, uid))
    await _send_admin(
        f"🔔 <b>QR CHỜ THANH TOÁN</b>\n"
        f"👤 UID: <code>{uid}</code>\n"
        f"📦 Gói: <b>{order_data.get('pkg_name')}</b>\n"
        f"💵 <b>{pay.fmt(amount)}</b>\n"
        f"📌 Ref: <code>{ref}</code>"
    )


async def show_qr_topup(update, ctx, amount):
    bot = update.get_bot()
    user = await ensure_user(update)
    uid = user["user_id"]

    ref = f"NAPTIEN{uuid.uuid4().hex[:8].upper()}"
    qr = pay.qr_url(amount, ref)
    cap = (
        "💳 <b>NẠP TIỀN VÀO SỐ DƯ</b>\n"
        f"💰 <b>{pay.fmt(amount)}</b>\n"
        f"🏦 BIDV · {BANK_NAME}\n"
        f"🔢 <code>{BANK_ACC}</code>\n\n"
        f"📌 Nội dung: <code>{ref}</code>\n"
        "⏰ 15 phút · tự cộng tiền\n"
        "✅ Sau khi chuyển khoản, nhấn <b>🔄 KIỂM TRA THANH TOÁN</b>"
    )
    kb = _with_menu(kb_payment(ref))

    await _delete_bot_msg(bot, uid)
    if update.callback_query:
        try: await update.callback_query.message.delete()
        except Exception: pass
    elif update.message:
        try: await update.message.delete()
        except Exception: pass

    try:
        sent = await bot.send_photo(uid, photo=qr, caption=cap,
                                     reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        sent = await bot.send_message(uid, cap, reply_markup=kb, parse_mode=ParseMode.HTML)

    _last_bot_msg[uid] = sent.message_id
    db.new_payment(uid, amount, ref, {}, sent.message_id)
    asyncio.create_task(hook.watch_payment_ref(ref, uid))


async def cmd_start(update, ctx):
    user = await ensure_user(update)
    if user.get("is_banned"):
        await update.message.reply_text("⛔ Tài khoản đã bị khoá.")
        return

    uid = user["user_id"]
    bot = ctx.bot

    await _delete_bot_msg(bot, uid)

    msg = await bot.send_message(uid, txt_welcome(user),
                                  reply_markup=kb_main(), parse_mode=ParseMode.HTML)
    _last_bot_msg[uid] = msg.message_id


async def cmd_balance(update, ctx):
    user = await ensure_user(update)
    await reply(update, f"💰 Số dư: <b>{pay.fmt(user['balance'])}</b>")


async def cmd_mykeys(update, ctx):
    user = await ensure_user(update)
    lic = [x for x in db.list_licenses(q=str(user["user_id"]))
           if int(x.get("telegram_id") or 0) == user["user_id"]]
    if not lic:
        await reply(update, "🗝️ Bạn chưa có key nào.")
        return
    txt = "🗝️ <b>KEY CỦA BẠN</b>\n\n"
    for k in lic[:10]:
        hsd = k.get("expires_at") or "Chưa kích hoạt"
        txt += (f"🔑 <code>{k['key_value']}</code>\n"
                f"   📦 {k.get('package_name') or 'Custom'}\n"
                f"   ⏰ {hsd}\n\n")
    await reply(update, txt)


async def on_callback(update, ctx):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = await ensure_user(update)

    if user.get("is_banned"):
        await q.answer("⛔ Tài khoản bị khoá!", show_alert=True)
        return

    if data.startswith("back:"):
        if data[5:] == "main":
            await show_main(update, ctx)
        return

    if data.startswith("menu:"):
        sec = data[5:]
        if sec == "buy_key":
            await reply(update, "🔑 <b>CHỌN GÓI KEY</b>\n\nChọn gói phù hợp:", kb_packages())
        elif sec == "my_keys":
            await cmd_mykeys(update, ctx)
        elif sec == "topup":
            await reply(update, "💳 <b>NẠP TIỀN</b>\n\nChọn mệnh giá:", kb_topup())
        elif sec == "profile":
            await reply(update, txt_profile(user))
        elif sec == "show_web":
            await reply(update,
                "🌐 <b>ĐỊA CHỈ WEBSITE</b>\n"
                "─────────────────────────\n"
                f"<code>{PAWNOVA_WEB_URL}</code>\n\n"
                "📱 <b>Bấm giữ để copy</b> rồi dán vào trình duyệt.\n\n"
                "💡 Khi deploy lên domain public (VD <code>https://pawnova.vn</code>), "
                "link sẽ tự đổi thành nút bấm được.")
        elif sec == "free_key":
            try:
                member = await ctx.bot.get_chat_member(chat_id="@PAW8386", user_id=user["user_id"])
                is_joined = member.status in ['member', 'administrator', 'creator']
            except Exception as e:
                log.warning(f"check join error: {e}")
                is_joined = False

            if not is_joined:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("1️⃣ THAM GIA GROUP", url="https://t.me/PAW8386")],
                    [InlineKeyboardButton("2️⃣ CHECK ĐÃ THAM GIA", callback_data="menu:free_key")],
                    [InlineKeyboardButton("◀️ QUAY LẠI", callback_data="back:main")]
                ])
                await reply(update, "⚠️ <b>BẠN CHƯA THAM GIA GROUP</b>\n\nVui lòng tham gia group https://t.me/PAW8386 để nhận key free!", kb)
                return

            ctx.user_data["await"] = "free_key_proofs"
            ctx.user_data["proof_ids"] = []

            caption = "MỌI NGƯỜI CẦN ĐẶT MUA KEY SỬ DỤNG TOOL ,  KEY FREE THEO THỜI HẠN THÌ VÀO BOT @PawBanKey_bot VÀ THAM GIA GROUP https://t.me/PAW8386 ĐỂ ĐƯỢC THAM GIA CÁC EVENT NHẬN THƯỞNG HÀNG TUẦN + PHÁT CODE GAME TX"

            base_dir = os.path.dirname(os.path.abspath(__file__))
            anh1 = os.path.join(base_dir, "NHIEMVU", "ANH1.jpg")
            anh2 = os.path.join(base_dir, "NHIEMVU", "ANH2.jpg")

            try:
                media_group = [
                    InputMediaPhoto(media=open(anh1, 'rb'), caption=caption),
                    InputMediaPhoto(media=open(anh2, 'rb'))
                ]
                await ctx.bot.send_media_group(chat_id=user["user_id"], media=media_group)

                await reply(update, "📸 <b>HƯỚNG DẪN NHẬN KEY:</b>\n\n1. Gửi 2 ảnh trên kèm nội dung vào 5 group (trên 1k thành viên).\n2. Chụp màn hình (screenshot) các bài đăng đó gửi lại vào đây.\n\nGửi đủ 5 ảnh khác nhau để nhận key!", kb_back("main"))
            except FileNotFoundError:
                await reply(update, "❌ <b>LỖI:</b> Không tìm thấy file ảnh trong thư mục NHIEMVU.")
            return
        return

    if data.startswith("pkg:"):
        pid = data[4:]
        p = svc.KEY_PACKAGES.get(pid)
        if not p:
            await q.answer("Gói không tồn tại!", show_alert=True)
            return
        ref = f"KEY{uuid.uuid4().hex[:8].upper()}"
        ctx.user_data["key_pending"] = {"ref": ref, "pkg_id": pid, "pkg_name": p["name"],
                                        "price": p["price"], "hours": p["hours"]}
        bal = user["balance"]
        can = bal >= p["price"]
        status = ("✅ <b>Số dư đủ — trừ trực tiếp</b>" if can
                  else f"⚠️ <b>Thiếu {pay.fmt(p['price']-bal)} → thanh toán QR</b>")
        txt = (
            "📋 <b>XÁC NHẬN MUA KEY</b>\n"
            f"📦 <b>{p['name']}</b>\n"
            f"💵 <b>{pay.fmt(p['price'])}</b>\n"
            f"⏰ <b>{p['hours']} giờ</b>\n"
            f"💼 Số dư: <b>{pay.fmt(bal)}</b>\n\n{status}"
        )
        await reply(update, txt, kb_confirm_key(ref))
        return

    if data.startswith("confirm_key:"):
        ref = data[12:]
        pend = ctx.user_data.get("key_pending")
        if not pend or pend.get("ref") != ref:
            await q.answer("Phiên hết hạn, chọn lại!", show_alert=True)
            return
        pid = pend["pkg_id"]; pkg_name = pend["pkg_name"]
        price = pend["price"]; hours = pend["hours"]
        
        if not db.deduct(user["user_id"], price, ref_id=ref, desc=f"Mua {pkg_name}"):
            await show_qr_key(update, ctx, price,
                              {"pkg_id": pid, "pkg_name": pkg_name, "hours": hours})
            return
            
        try:
            lic = db.create_license(label=f"Bot · {user.get('username') or user['user_id']}",
                                    hours=hours, telegram_id=user["user_id"],
                                    package_id=pid, package_name=pkg_name)
        except Exception as e:
            log.error(f"create_license error: {e}")
            db.credit(user["user_id"], price, ref_id=ref, desc=f"Hoàn tiền lỗi mua {pkg_name}")
            await reply(update, "❌ Có lỗi xảy ra khi tạo key. Đã hoàn lại tiền vào số dư. Vui lòng thử lại!")
            return
        try:
            db.new_order(user["user_id"], pid, pkg_name, price, lic["key_value"], status="completed")
        except Exception as e:
            log.error(f"new_order failed (key vẫn giao): {e}")

        ctx.user_data.pop("key_pending", None)
        await _deliver_key(ctx.bot, user["user_id"], lic, pkg_name, price)
        await _notify_admin_sold(user["user_id"], lic, pkg_name, price, ref, "Số dư")
        return

    if data.startswith("checkpay:"):
        ref = data[9:]
        now_ts = time.time()
        last = ctx.user_data.get("last_check_ts", 0)
        if now_ts - last < 3:
            await q.answer("⏳ Vui lòng chờ 3 giây rồi thử lại", show_alert=True)
            return
        ctx.user_data["last_check_ts"] = now_ts

        pmt = db.get_payment_by_ref(ref)
        if not pmt:
            try:
                c = db._conn()
                row = c.execute(
                    "SELECT status FROM pending_payments WHERE order_ref=?",
                    (ref,)).fetchone()
                c.close()
            except Exception:
                row = None
            if row and row["status"] == "completed":
                await q.answer("✅ Đơn đã hoàn tất, key đã gửi!", show_alert=True)
            elif row and row["status"] == "expired":
                await q.answer("⏰ QR đã hết hạn (15 phút). Vui lòng tạo đơn mới.", show_alert=True)
            else:
                await q.answer("❌ Không tìm thấy đơn", show_alert=True)
            await show_main(update, ctx)
            return

        try:
            res = await hook.check_payment_now(ref)
        except Exception as e:
            log.error(f"checkpay callback: {e}")
            await q.answer("⚠️ Lỗi kết nối ngân hàng, thử lại sau", show_alert=True)
            return

        if res.get("found"):
            await q.answer("✅ ĐÃ NHẬN THANH TOÁN — Key đang được gửi!", show_alert=True)
            return

        reason = res.get("reason", "not_yet")
        if reason == "fetch_error":
            await q.answer(
                "⚠️ Không kết nối được cổng ngân hàng.\nThử lại sau 10-20s.",
                show_alert=True)
        elif reason == "expired":
            await q.answer("⏰ QR đã hết hạn. Vui lòng tạo đơn mới.", show_alert=True)
        else:
            await q.answer(
                "⏳ Chưa thấy giao dịch.\n\n"
                "✔ Kiểm tra ĐÚNG số tiền?\n"
                "✔ Kiểm tra ĐÚNG nội dung CK (đủ mã)?\n"
                "✔ Đã chuyển khoản chưa?\n\n"
                "Ngân hàng có thể trễ 5-30s. Thử lại sau 10-20s.",
                show_alert=True)
        return

    if data.startswith("cancelpay:"):
        pmt = db.get_payment_by_ref(data[10:])
        if pmt:
            db.expire_payment(pmt["payment_id"])
        await reply(update, "❌ Đã huỷ thanh toán.", kb_main())
        return

    if data.startswith("tamt:"):
        val = data[5:]
        if val == "custom":
            ctx.user_data["await"] = "topup_custom"
            await reply(update,
                "✏️ <b>NHẬP SỐ TIỀN</b>\n\nTối thiểu: <b>10.000đ</b>\nVD: 150000 hoặc 150K",
                kb_back("main"))
            return
        await show_qr_topup(update, ctx, int(val))
        return


async def on_message(update, ctx):
    state = ctx.user_data.get("await")

    if not state:
        await show_main(update, ctx)
        return

    if state == "free_key_proofs":
        if update.message.photo:
            photo_id = update.message.photo[-1].file_unique_id
            if db.add_free_key_proof(photo_id, update.effective_user.id):
                ctx.user_data.setdefault("proof_ids", []).append(photo_id)
                count = len(ctx.user_data["proof_ids"])
                if count >= 5:
                    if not db.can_get_free_key(update.effective_user.id):
                        await reply(update, "🕐 <b>HÔM NAY BẠN ĐÃ NHẬN KEY FREE RỒI</b>\n\nQuay lại vào ngày mai nhé!")
                        ctx.user_data["await"] = None
                        return

                    minutes = roll_free_key()
                    if minutes == 0: minutes = 15
                    lic = db.create_license(label=f"Free NV {minutes}p · {update.effective_user.username or update.effective_user.id}", minutes=minutes, telegram_id=update.effective_user.id)
                    db.log_free_key(update.effective_user.id, f"win:{minutes}")
                    await reply(update, f"🎉 <b>CHÚC MỪNG BẠN!</b>\n═════════════════════════════\n🎁 Bạn nhận được: <b>KEY {minutes} PHÚT</b>\n═════════════════════════════\n🔑 <code>{lic['key_value']}</code>\n\n🌐 Mở: <code>{PAWNOVA_WEB_URL}</code>")
                    ctx.user_data["await"] = None
                else:
                    await reply(update, f"✅ Đã nhận ảnh {count}/5. Hãy gửi thêm {5-count} ảnh (chụp ở các group khác nhau) để nhận key!")
            else:
                await reply(update, "❌ Ảnh này đã được sử dụng hoặc trùng lặp. Vui lòng gửi ảnh khác!")
        else:
            await reply(update, "⚠️ Vui lòng gửi ảnh (screenshot) làm minh chứng.")
        return

    text = update.message.text.strip()

    def clean(s):
        return s.replace(".", "").replace(",", "").replace("k", "000").replace("K", "000")

    if state == "topup_custom":
        c = clean(text)
        if not c.isdigit():
            await reply(update, "❌ Nhập số tiền hợp lệ!")
            return
        amt = int(c)
        if amt < 10_000:
            await reply(update, "❌ Tối thiểu <b>10.000đ</b>!")
            return
        ctx.user_data["await"] = None
        await show_qr_topup(update, ctx, amt)
        return

    await show_main(update, ctx)


_app = None


async def credit_from_webhook(uid, amount, ref, pmt_id):
    pmt = db.get_payment_by_id(pmt_id)
    db.complete_payment(pmt_id)
    if not (_app and pmt):
        return
    bot = _app.bot
    msg_id = pmt.get("message_id", 0) if pmt else 0

    if msg_id:
        try: await bot.delete_message(chat_id=uid, message_id=msg_id)
        except Exception: pass
    if _last_bot_msg.get(uid) == msg_id:
        _last_bot_msg.pop(uid, None)

    if ref.startswith("KEY"):
        try:
            raw = pmt.get("order_data", "{}")
            od = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            od = {}
        pkg_id   = od.get("pkg_id", "key_1d")
        pkg_name = od.get("pkg_name", "Key")
        hours    = int(od.get("hours", 24))
        lic = db.create_license(label=f"Bot QR · {uid}", hours=hours,
                                telegram_id=uid, package_id=pkg_id, package_name=pkg_name)
        try:
            db.new_order(uid, pkg_id, pkg_name, amount, lic["key_value"], status="completed")
        except Exception as e:
            log.error(f"new_order failed (key vẫn giao): {e}")
        try:
            await _deliver_key(bot, uid, lic, pkg_name, amount)
        except Exception as e:
            log.error(f"deliver: {e}")
        await _notify_admin_sold(uid, lic, pkg_name, amount, ref, "QR")
        return

    db.credit(uid, amount, ref_id=ref, desc="Nạp tiền QR")
    user = db.get_user(uid)
    if not user:
        return
    try:
        msg = await bot.send_message(uid,
            f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n"
            f"💰 <b>{pay.fmt(amount)}</b>\n"
            f"💼 Số dư mới: <b>{pay.fmt(user['balance'])}</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_main())
        _last_bot_msg[uid] = msg.message_id
    except Exception:
        pass
    await _send_admin(f"✅ <b>NẠP TIỀN</b>\nUID <code>{uid}</code> · {pay.fmt(amount)}\n"
                      f"Số dư: <b>{pay.fmt(user['balance'])}</b>")


async def _error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.error(f"Handler error: {ctx.error}", exc_info=ctx.error)


def build_app():
    global _app
    app = Application.builder().token(BOT1_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("mykeys", cmd_mykeys))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO, on_message))
    app.add_error_handler(_error_handler)
    _app = app
    return app
