# bot2.py — TRUMSCAM_VN_BOT | Admin & transaction monitor
# language: Python 3.11+, file: bot2.py, target: python-telegram-bot v20+

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode

from config import BOT1_TOKEN, BOT2_TOKEN, SEED_ADMIN_IDS
from telegram import Bot as _TgBot
import db, pay

_service_bot = _TgBot(token=BOT1_TOKEN)
log = logging.getLogger(__name__)


def authorized(uid: int) -> bool:
    return db.is_admin(uid) or uid in SEED_ADMIN_IDS


# ─────────────────────────── KEYBOARDS ──────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊  Thống Kê",             callback_data="a:stats")],
        [InlineKeyboardButton("👥  Danh Sách User",        callback_data="a:users")],
        [InlineKeyboardButton("🔑  Đơn Key Đang Chờ",     callback_data="a:orders")],
        [InlineKeyboardButton("💰  Cộng Tiền Thủ Công",   callback_data="a:credit")],
        [InlineKeyboardButton("🔨  Ban / Unban User",      callback_data="a:ban")],
        [
            InlineKeyboardButton("🔍  Tìm Theo ID",       callback_data="a:find_id"),
            InlineKeyboardButton("🔍  Tìm Theo @",        callback_data="a:find_user"),
        ],
        [InlineKeyboardButton("📢  Broadcast",             callback_data="a:broadcast")],
        [InlineKeyboardButton("⚙️  Quản Lý Admin",        callback_data="a:admins")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Quay Lại", callback_data="a:main")]])


def kb_order_actions(orders: list) -> InlineKeyboardMarkup:
    rows = []
    for o in orders[:10]:
        short = o["order_id"]
        rows.append([
            InlineKeyboardButton(f"✅ {short}", callback_data=f"a:done:{o['order_id']}"),
            InlineKeyboardButton(f"❌ {short}", callback_data=f"a:cancel:{o['order_id']}"),
        ])
    rows.append([InlineKeyboardButton("◀️ Quay Lại", callback_data="a:main")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────── HELPERS ────────────────────────────────

async def send_or_edit(update: Update, text: str,
                        kb: InlineKeyboardMarkup | None = None):
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=kb, parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


def fmt_user_detail(user: dict, orders: list | None = None) -> str:
    """Render thông tin user đầy đủ — dùng chung cho find và tx notify."""
    uname  = user.get("username") or "─"
    fname  = user.get("full_name") or "─"
    uid    = user["user_id"]
    ban    = "🚫 Có" if user.get("is_banned") else "✅ Không"
    txt = (
        f"👤 <b>THÔNG TIN USER</b>\n"
        f"─────────────────────────────\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📛 Username: @{uname}\n"
        f"👤 Tên: {fname}\n"
        f"💰 Số dư: <b>{pay.fmt(user['balance'])}</b>\n"
        f"📦 Tổng đơn: {user['total_orders']}\n"
        f"💸 Đã chi: {pay.fmt(user['total_spent'])}\n"
        f"🔨 Bị ban: {ban}\n"
    )
    if orders:
        txt += "\n<b>5 đơn gần nhất:</b>\n"
        for o in orders:
            txt += f"• <code>{o['order_id']}</code> — {o['service_name']} — {pay.fmt(o['amount'])}\n"
    return txt


# ─────────────────────────── DASHBOARD ──────────────────────────────

async def show_dashboard(update: Update, _: ContextTypes.DEFAULT_TYPE):
    s = db.stats()
    await send_or_edit(
        update,
        "🎛️ <b>TRUMSCAM_VN_BOT — DASHBOARD</b>\n"
        "─────────────────────────────\n"
        f"👥 Tổng user:    <b>{s['users']}</b>\n"
        f"🚫 Bị ban:       <b>{s['banned']}</b>\n"
        f"📦 Tổng đơn:     <b>{s['orders']}</b>\n"
        f"✅ Hoàn thành:   <b>{s['done']}</b>\n"
        f"⏳ Đang xử lý:   <b>{s['pending']}</b>\n"
        f"🔑 Keys đã bán:  <b>{s['keys']}</b>\n"
        f"💰 Tổng thu:     <b>{pay.fmt(s['revenue'])}</b>\n"
        f"💸 Tổng chi:     <b>{pay.fmt(s['spent'])}</b>\n",
        kb_main()
    )


# ─────────────────────────── COMMANDS ───────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not authorized(uid):
        await update.message.reply_text("⛔ Không có quyền truy cập!")
        return
    if uid in SEED_ADMIN_IDS:
        db.add_admin(uid, update.effective_user.username or "")
    await show_dashboard(update, ctx)


async def cmd_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /user <user_id hoặc @username>")
        return
    q     = ctx.args[0].lstrip("@")
    users = db.find_user_by_username_or_id(q)
    if not users:
        await update.message.reply_text("❌ Không tìm thấy user!")
        return
    for user in users[:3]:
        orders = db.get_orders(user["user_id"], 5)
        await update.message.reply_text(
            fmt_user_detail(user, orders), parse_mode=ParseMode.HTML
        )


async def cmd_addbal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /addbal <uid> <amount>")
        return
    uid    = int(ctx.args[0])
    amount = int(ctx.args[1])
    db.credit(uid, amount, ref_id="ADMIN_CMD", desc="Admin cộng tiền")
    user = db.get_user(uid)
    await update.message.reply_text(
        f"✅ Đã cộng {pay.fmt(amount)} cho <code>{uid}</code>\n"
        f"💼 Số dư mới: {pay.fmt(user['balance']) if user else 'N/A'}",
        parse_mode=ParseMode.HTML
    )
    if user:
        try:
            await ctx.bot.send_message(
                uid,
                f"💰 <b>CỘNG TIỀN TÀI KHOẢN</b>\n"
                f"Số tiền: <b>{pay.fmt(amount)}</b>\n"
                f"Số dư mới: <b>{pay.fmt(user['balance'])}</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


# ─────────────────────────── CALLBACKS ──────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    data = q.data

    if not authorized(uid):
        await q.answer("⛔ Không có quyền!", show_alert=True)
        return

    if data == "a:main":
        await show_dashboard(update, ctx)
        return

    if data == "a:stats":
        s = db.stats()
        await send_or_edit(update,
            "📊 <b>THỐNG KÊ HỆ THỐNG</b>\n"
            "─────────────────────────────\n"
            f"👥 Users:        <b>{s['users']}</b>\n"
            f"🚫 Banned:       <b>{s['banned']}</b>\n"
            f"📦 Orders:       <b>{s['orders']}</b>\n"
            f"✅ Done:         <b>{s['done']}</b>\n"
            f"⏳ Pending:      <b>{s['pending']}</b>\n"
            f"🔑 Keys bán:     <b>{s['keys']}</b>\n"
            f"💰 Revenue:      <b>{pay.fmt(s['revenue'])}</b>\n"
            f"💸 Spent:        <b>{pay.fmt(s['spent'])}</b>\n",
            kb_back()
        )
        return

    if data == "a:users":
        users = db.all_users(50)
        if not users:
            await send_or_edit(update, "Chưa có user.", kb_back())
            return
        text = f"👥 <b>DANH SÁCH USER</b> ({len(users)})\n\n"
        for u in users[:25]:
            ban_tag = " 🚫" if u.get("is_banned") else ""
            text += (
                f"├ <code>{u['user_id']}</code>{ban_tag}"
                f" @{u.get('username','─')}\n"
                f"│  💰{pay.fmt(u['balance'])} 📦{u['total_orders']}\n"
            )
        await send_or_edit(update, text, kb_back())
        return

    if data == "a:orders":
        orders = db.get_pending_orders(20)
        if not orders:
            await send_or_edit(update, "✅ Không có đơn nào đang xử lý.", kb_back())
            return
        text = f"📦 <b>ĐƠN ĐANG XỬ LÝ</b> ({len(orders)})\n\n"
        for o in orders:
            text += (
                f"🆔 <code>{o['order_id']}</code>\n"
                f"   👤 UID:{o['user_id']}  📦 {o['service_name']}\n"
                f"   💰 {pay.fmt(o['amount'])}  ⏰ {o['created_at'][:16]}\n\n"
            )
        await send_or_edit(update, text, kb_order_actions(orders))
        return

    if data.startswith("a:done:"):
        oid   = data[7:]
        order = db.get_order(oid)
        if not order:
            await q.answer("Không tìm thấy đơn!", show_alert=True)
            return
        db.set_order_status(oid, "completed", "Hoàn thành bởi admin")
        try:
            await q.bot.send_message(
                order["user_id"],
                f"✅ <b>ĐƠN HÀNG HOÀN THÀNH!</b>\n"
                f"🆔 Mã đơn: <code>{oid}</code>\n"
                f"📦 DV: {order['service_name']}\n\n"
                "Cảm ơn bạn đã sử dụng dịch vụ! 🎉",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        await q.answer(f"✅ Done: {oid}", show_alert=True)
        return

    if data.startswith("a:cancel:"):
        oid   = data[9:]
        order = db.get_order(oid)
        if not order:
            await q.answer("Không tìm thấy đơn!", show_alert=True)
            return
        refund = int(order["amount"] * 0.8)
        db.set_order_status(oid, "cancelled", "Admin huỷ — hoàn 80%")
        db.credit(order["user_id"], refund, ref_id=oid, desc="Hoàn tiền 80%")
        try:
            await q.bot.send_message(
                order["user_id"],
                f"❌ <b>ĐƠN HÀNG BỊ HUỶ</b>\n"
                f"🆔 Mã đơn: <code>{oid}</code>\n"
                f"💸 Hoàn: <b>{pay.fmt(refund)}</b> (80%)\n\n"
                "Xin lỗi vì sự bất tiện!",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        await q.answer(f"❌ Cancelled: {oid}", show_alert=True)
        return

    if data == "a:credit":
        ctx.user_data["admin_state"] = "credit_uid"
        await send_or_edit(update,
            "💰 <b>CỘNG TIỀN CHO USER</b>\n\nNhập User ID:", kb_back())
        return

    if data == "a:ban":
        ctx.user_data["admin_state"] = "ban_uid"
        await send_or_edit(update,
            "🔨 <b>BAN / UNBAN USER</b>\n\nNhập User ID:", kb_back())
        return

    if data == "a:broadcast":
        ctx.user_data["admin_state"] = "broadcast"
        await send_or_edit(update,
            "📢 <b>BROADCAST</b>\n\nNhập nội dung (hỗ trợ HTML):", kb_back())
        return

    if data == "a:admins":
        admins = db.get_admins()
        text   = "⚙️ <b>QUẢN LÝ ADMIN</b>\n\n"
        for a in admins:
            text += f"• <code>{a['user_id']}</code> — @{a.get('username','─')}\n"
        text += "\nNhập User ID để thêm admin mới:"
        ctx.user_data["admin_state"] = "add_admin"
        await send_or_edit(update, text, kb_back())
        return

    # ── Tìm theo ID ───────────────────────────────────────────────
    if data == "a:find_id":
        ctx.user_data["admin_state"] = "find_by_id"
        await send_or_edit(update,
            "🔍 <b>TÌM KIẾM THEO ID</b>\n\nNhập User ID (số):", kb_back())
        return

    # ── Tìm theo username ─────────────────────────────────────────
    if data == "a:find_user":
        ctx.user_data["admin_state"] = "find_by_username"
        await send_or_edit(update,
            "🔍 <b>TÌM KIẾM THEO USERNAME</b>\n\nNhập @username hoặc tên:", kb_back())
        return


# ─────────────────────────── MESSAGES ───────────────────────────────

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not authorized(uid):
        return

    state = ctx.user_data.get("admin_state")
    if not state:
        await show_dashboard(update, ctx)
        return

    text = update.message.text.strip()

    async def ask(next_state: str, prompt: str):
        ctx.user_data["admin_state"] = next_state
        await update.message.reply_text(prompt, parse_mode=ParseMode.HTML)

    # ── credit uid ────────────────────────────────────────────────
    if state == "credit_uid":
        if not text.isdigit():
            await update.message.reply_text("❌ ID không hợp lệ!")
            return
        ctx.user_data["credit_target"] = int(text)
        await ask("credit_amount", f"💰 Nhập số tiền cộng cho UID <code>{text}</code>:")
        return

    if state == "credit_amount":
        target  = ctx.user_data.get("credit_target")
        cleaned = text.replace(".", "").replace(",", "")
        if not cleaned.isdigit() or not target:
            await update.message.reply_text("❌ Số tiền không hợp lệ!")
            return
        amount = int(cleaned)
        db.credit(target, amount, ref_id="ADMIN", desc="Admin cộng tiền thủ công")
        user   = db.get_user(target)
        ctx.user_data["admin_state"] = None
        await update.message.reply_text(
            f"✅ Đã cộng <b>{pay.fmt(amount)}</b> cho <code>{target}</code>\n"
            f"💼 Số dư mới: <b>{pay.fmt(user['balance']) if user else 'N/A'}</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        if user:
            try:
                await ctx.bot.send_message(
                    target,
                    f"💰 <b>CỘNG TIỀN TÀI KHOẢN</b>\n"
                    f"Số tiền: <b>{pay.fmt(amount)}</b>\n"
                    f"Số dư mới: <b>{pay.fmt(user['balance'])}</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        return

    # ── ban ───────────────────────────────────────────────────────
    if state == "ban_uid":
        if not text.isdigit():
            await update.message.reply_text("❌ ID không hợp lệ!")
            return
        target = int(text)
        user   = db.get_user(target)
        if not user:
            await update.message.reply_text("❌ User không tồn tại!")
            return
        new_status = 0 if user.get("is_banned") else 1
        db.ban_user(target, new_status)
        action = "BAN" if new_status else "UNBAN"
        ctx.user_data["admin_state"] = None
        await update.message.reply_text(
            f"✅ Đã {action} user <code>{target}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return

    # ── broadcast ─────────────────────────────────────────────────
    if state == "broadcast":
        users  = db.all_users(10000)
        sent   = 0
        failed = 0
        ctx.user_data["admin_state"] = None
        for u in users:
            try:
                await _service_bot.send_message(
                    u["user_id"], text, parse_mode=ParseMode.HTML
                )
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(
            f"📢 <b>Broadcast xong!</b>\n✅ Thành công: {sent}\n❌ Lỗi: {failed}",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return

    # ── add admin ─────────────────────────────────────────────────
    if state == "add_admin":
        if not text.isdigit():
            await update.message.reply_text("❌ ID không hợp lệ!")
            return
        new_uid = int(text)
        db.add_admin(new_uid, "")
        ctx.user_data["admin_state"] = None
        await update.message.reply_text(
            f"✅ Đã thêm <code>{new_uid}</code> làm admin!",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return

    # ── tìm theo ID ───────────────────────────────────────────────
    if state == "find_by_id":
        if not text.isdigit():
            await update.message.reply_text("❌ ID phải là số nguyên!")
            return
        target = int(text)
        user   = db.get_user(target)
        ctx.user_data["admin_state"] = None
        if not user:
            await update.message.reply_text("❌ Không tìm thấy user!", reply_markup=kb_main())
            return
        orders = db.get_orders(target, 5)
        keys   = db.get_keys_by_user(target)
        txt    = fmt_user_detail(user, orders)
        if keys:
            txt += "\n<b>Keys đã mua:</b>\n"
            for k in keys[:5]:
                txt += f"• <code>{k['key_value']}</code> — {k['package_name']} — {k['expires_at'][:16]}\n"
        await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb_main())
        return

    # ── tìm theo username ─────────────────────────────────────────
    if state == "find_by_username":
        ctx.user_data["admin_state"] = None
        q_str  = text.strip().lstrip("@")
        users  = db.find_user_by_username_or_id(q_str)
        if not users:
            await update.message.reply_text(
                f"❌ Không tìm thấy user nào khớp '{q_str}'!",
                reply_markup=kb_main()
            )
            return
        for user in users[:5]:
            orders = db.get_orders(user["user_id"], 5)
            keys   = db.get_keys_by_user(user["user_id"])
            txt    = fmt_user_detail(user, orders)
            if keys:
                txt += "\n<b>Keys đã mua:</b>\n"
                for k in keys[:3]:
                    txt += f"• <code>{k['key_value']}</code> — {k['package_name']} — {k['expires_at'][:16]}\n"
            await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb_main())
        return

    await show_dashboard(update, ctx)


# ─────────────────────────── BUILD ──────────────────────────────────

def build_app() -> Application:
    app = Application.builder().token(BOT2_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("user",   cmd_user))
    app.add_handler(CommandHandler("addbal", cmd_addbal))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
