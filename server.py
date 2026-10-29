import os, json, time, secrets, hashlib
from datetime import datetime, timedelta
from aiohttp import web, ClientSession, ClientTimeout

import db
from config import (PAWNOVA_WEB_PORT, ADMIN_PASSWORD, _TUNNEL_BASE, DB_PATH, ADMIN_CHAT_ID)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GAME_API_URLS = {
    "hu":          _TUNNEL_BASE + "/lc79/api/tx",
    "md5":         _TUNNEL_BASE + "/lc79/api/md5",
    "max_hu":      _TUNNEL_BASE + "/max789/api/tx",
    "max_md5":     _TUNNEL_BASE + "/max789/api/md5",
    "789club":     _TUNNEL_BASE + "/789club/api/789",
    "sunwin":      _TUNNEL_BASE + "/sunwin/api/tx",
    "68gb_hu":     _TUNNEL_BASE + "/68tx/api/tx",
    "68gb_md5":    _TUNNEL_BASE + "/68md5/api/md5",
    "b52_hu":      _TUNNEL_BASE + "/b52/api/tx",
    "b52_md5":     _TUNNEL_BASE + "/b52/api/md5",
    "hitclub_hu":  _TUNNEL_BASE + "/hitclub/api/tx",
    "hitclub_md5": _TUNNEL_BASE + "/hitclub/api/md5",
    "betvip_hu":   _TUNNEL_BASE + "/betvip/api/tx",
    "betvip_md5":  _TUNNEL_BASE + "/betvip/api/md5",
}

_sessions     = {}
_ctv_sessions = {}
_rate         = {}
_aff_ready    = False

_predict_cache = {
    "hu":  {"ready": False, "stale": True, "source_session": None,
            "source_checked_at": None, "last_error": None},
    "md5": {"ready": False, "stale": True, "source_session": None,
            "source_checked_at": None, "last_error": None},
}


def _new_token():
    return secrets.token_urlsafe(32)


def _is_admin(request):
    tok = request.cookies.get("paw_session")
    if not tok:
        return False
    s = _sessions.get(tok)
    if not s or not s.get("admin"):
        return False
    if time.time() - s["created"] > 86400 * 3:
        _sessions.pop(tok, None)
        return False
    return True


def _get_ctv(request):
    tok = request.cookies.get("ctv_session")
    if not tok:
        return None
    s = _ctv_sessions.get(tok)
    if not s:
        return None
    if time.time() - s["created"] > 86400 * 3:
        _ctv_sessions.pop(tok, None)
        return None
    return s.get("ctv")


def _auth_error(msg, status=401):
    return web.json_response({"ok": False, "error": msg}, status=status)


def _to_float(v, default=None):
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default=None):
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ensure_affiliate_table():
    global _aff_ready
    if _aff_ready:
        return
    try:
        c = db._conn()
        c.execute(
            "CREATE TABLE IF NOT EXISTS affiliates ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "telegram_id INTEGER UNIQUE, "
            "username TEXT DEFAULT '', "
            "referral_code TEXT UNIQUE, "
            "commission_percent REAL DEFAULT 10, "
            "active INTEGER DEFAULT 1, "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        c.commit()
        c.close()
        _aff_ready = True
    except Exception:
        pass


@web.middleware
async def _error_mw(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        try:
            import traceback
            traceback.print_exc()
        except Exception:
            pass
        return web.json_response({"ok": False, "error": (str(e) or "Lỗi máy chủ")[:300]}, status=500)


def _require_license(request):
    key = (request.headers.get("X-API-Key") or "").strip()
    dev = (request.headers.get("X-Device-ID") or "").strip()
    if not key or not dev:
        return None, _auth_error("Thiếu key hoặc device id")
    lic = db.get_license_by_key(key)
    if not lic:
        return None, _auth_error("Key không tồn tại")
    if not lic.get("active"):
        return None, _auth_error("Key đã bị khoá", 403)
    if not lic.get("activated"):
        return None, _auth_error("Key chưa kích hoạt", 403)
    if lic.get("device_id") != dev:
        return None, _auth_error("Key đã gắn với thiết bị khác", 403)
    exp = lic.get("expires_at") or ""
    if exp:
        try:
            if datetime.now() > datetime.fromisoformat(exp):
                return None, _auth_error("Key đã hết hạn", 403)
        except Exception:
            pass
    return lic, None


def _rate_ok(lic_id, game=""):
    now = time.time()
    bucket = f"{lic_id}:{game}"
    if now - _rate.get(bucket, 0) < 0.35:
        return False
    _rate[bucket] = now
    return True


def _parse_pred(raw_bytes):
    try:
        d = json.loads(raw_bytes)
    except Exception:
        return None
    if isinstance(d, dict) and isinstance(d.get("data"), dict):
        d = d["data"]
    a = d.get("analysis") if isinstance(d.get("analysis"), dict) else d

    def pick(keys):
        for src in (a, d):
            if not isinstance(src, dict):
                continue
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return None

    def norm(v):
        if v is None:
            return ""
        s = str(v).upper().strip()
        for ch in ("À","Á","Ạ","Ả","Ã","Â","Ầ","Ấ","Ậ","Ẩ","Ẫ","Ă","Ằ","Ắ","Ặ","Ẳ","Ẵ"):
            s = s.replace(ch, "A")
        s = s.replace("Đ", "D")
        for ch in ("Ì","Í","Ị","Ỉ","Ĩ"):
            s = s.replace(ch, "I")
        for ch in ("Ù","Ú","Ụ","Ủ","Ũ"):
            s = s.replace(ch, "U")
        if "TAI" in s or "OVER" in s or "BIG" in s or s == "T":
            return "TAI"
        if "XIU" in s or "UNDER" in s or "SMALL" in s or s == "X":
            return "XIU"
        return ""

    return {
        "pred":   norm(pick(["prediction","predict","du_doan","dudoan","prediction_side"])),
        "target": pick(["target_session","target_id","next_session","phien_tiep","phien_du_doan","target"]),
        "top":    pick(["top_id","session","phien","last_session"]),
        "res":    norm(pick(["last_side","last_result","prev_result","ket_qua_truoc","actual"])),
    }


def _track(lic_id, game, raw):
    p = _parse_pred(raw)
    if not p or not p["pred"] or p["target"] is None:
        return p
    db.upsert_prediction(lic_id, game, str(p["target"]), p["pred"])
    if p["top"] is not None and p["res"]:
        db.grade_predictions(game, str(p["top"]), p["res"])
    return p


async def serve_tool(request):
    return web.FileResponse(os.path.join(BASE_DIR, "tool.html"))


async def serve_admin(request):
    return web.FileResponse(os.path.join(BASE_DIR, "admin.html"))


async def api_activate(request):
    try:
        data = await request.json()
    except Exception:
        return _auth_error("JSON không hợp lệ", 400)
    key = str(data.get("key", "")).strip()
    dev = str(data.get("device_id", "")).strip()
    if not key or not dev:
        return _auth_error("Thiếu key hoặc device_id", 400)
    lic = db.get_license_by_key(key)
    if not lic:
        return _auth_error("Key không tồn tại", 404)
    if not lic.get("active"):
        return _auth_error("Key đã bị khoá", 403)
    if lic.get("device_id") and lic["device_id"] != dev:
        return _auth_error("Key đã gắn thiết bị khác, liên hệ admin để gỡ", 403)
    if not lic.get("activated"):
        db.activate_license(lic["key_id"], dev)
        lic = db.get_license_by_key(key)
    return web.json_response({
        "ok": True,
        "plan": lic.get("package_name") or "Custom",
        "hours": (lic.get("minutes") or 0) / 60,
        "expires_at": lic.get("expires_at") or "",
        "license_id": lic.get("key_id"),
        "key_prefix": lic["key_value"][:8] + "••••",
    })


async def api_predict(request):
    lic, err = _require_license(request)
    if err:
        return err
    game = request.query.get("game", "")
    url = GAME_API_URLS.get(game)
    if not url:
        return web.json_response({"ok": False, "error": "Game không tồn tại"}, status=404)
    if not _rate_ok(lic["key_id"], game):
        return web.json_response({"ok": False, "error": "Quá nhanh"}, status=429)
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as s:
            async with s.get(url, headers={"User-Agent": "PAWNOVA/1.0"}) as r:
                raw = await r.read()
        p = _track(lic["key_id"], game, raw)
        if game in _predict_cache:
            _predict_cache[game].update({
                "ready": bool(p and p.get("pred")),
                "stale": False,
                "source_session": (p or {}).get("top"),
                "source_checked_at": datetime.now().isoformat(timespec="seconds"),
                "last_error": None,
            })
        try:
            parsed = json.loads(raw)
        except Exception:
            return web.json_response({"ok": True, "raw": raw.decode(errors="ignore")})
        if not isinstance(parsed, dict):
            parsed = {"data": parsed}
        parsed["ok"] = True
        return web.json_response(parsed)
    except Exception as e:
        if game in _predict_cache:
            _predict_cache[game]["stale"] = True
            _predict_cache[game]["last_error"] = str(e)[:200]
            _predict_cache[game]["source_checked_at"] = datetime.now().isoformat(timespec="seconds")
        return web.json_response({"ok": False, "error": "Nguồn game lỗi: " + str(e)}, status=502)


async def api_history(request):
    lic, err = _require_license(request)
    if err:
        return err
    game = request.query.get("game", "")
    if not game:
        return web.json_response({"ok": False, "error": "Thiếu game"}, status=400)
    url = GAME_API_URLS.get(game)
    if url and _rate_ok(lic["key_id"], game + ":hist"):
        try:
            async with ClientSession(timeout=ClientTimeout(total=5)) as s:
                async with s.get(url, headers={"User-Agent": "PAWNOVA/1.0"}) as r:
                    raw = await r.read()
            _track(lic["key_id"], game, raw)
        except Exception:
            pass
    rows = db.get_predictions_for_license(lic["key_id"], game, 40)
    return web.json_response({"ok": True, "history": rows})


async def api_admin_login(request):
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    if str(d.get("password", "")) != ADMIN_PASSWORD:
        return _auth_error("Sai mật khẩu", 401)
    tok = _new_token()
    _sessions[tok] = {"admin": True, "created": time.time()}
    resp = web.json_response({"ok": True})
    resp.set_cookie("paw_session", tok, httponly=True, samesite="Lax", max_age=86400 * 3)
    return resp


async def api_admin_logout(request):
    tok = request.cookies.get("paw_session")
    if tok:
        _sessions.pop(tok, None)
    resp = web.json_response({"ok": True})
    resp.del_cookie("paw_session")
    return resp


async def api_admin_summary(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        c = db._conn()
        total = c.execute("SELECT COUNT(*) FROM keys").fetchone()[0]
        now = datetime.now()
        active = expired = 0
        for x in c.execute("SELECT activated, expires_at FROM keys").fetchall():
            if not x["activated"]:
                continue
            try:
                if datetime.fromisoformat(x["expires_at"] or "") < now:
                    expired += 1
                else:
                    active += 1
            except Exception:
                active += 1
        pending = c.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('pending','processing')"
        ).fetchone()[0]
        rev = c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM orders WHERE status IN ('completed','approved')"
        ).fetchone()[0]
        c.close()
    except Exception as e:
        return _auth_error("Lỗi truy vấn: " + str(e)[:100], 500)
    return web.json_response({"ok": True, "stats": {
        "total": total, "active": active, "expired": expired,
        "pending_orders": pending, "revenue": rev}})


async def api_admin_licenses_get(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        rows = db.list_licenses(q=request.query.get("q", ""))
    except Exception as e:
        return _auth_error("Lỗi truy vấn: " + str(e)[:100], 500)
    out = []
    now = datetime.now()
    for x in rows:
        exp_iso = x.get("expires_at") or ""
        act = bool(x.get("activated"))
        expired = False
        if act and exp_iso:
            try:
                expired = datetime.fromisoformat(exp_iso) < now
            except Exception:
                expired = False
        out.append({
            "id": x["key_id"],
            "label": x.get("label") or "",
            "key_prefix": (x["key_value"] or "")[:8],
            "active": bool(x.get("active")),
            "activated": act,
            "expired": expired,
            "unactivated": not act,
            "device_id": x.get("device_id") or "",
            "telegram_id": x.get("telegram_id") or 0,
            "expires_at": exp_iso,
            "duration_hours": (x.get("minutes") or 0) / 60,
            "created_at": x.get("created_at"),
        })
    return web.json_response({"ok": True, "licenses": out})


async def api_admin_license_create(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    label = str(d.get("label", "")).strip()
    hours = _to_float(d.get("hours", 24), 24)
    if not label:
        return _auth_error("Thiếu tên", 400)
    if hours is None or hours <= 0 or hours > 8760:
        return _auth_error("Số giờ không hợp lệ", 400)
    try:
        lic = db.create_license(label=label, hours=hours)
    except Exception as e:
        return _auth_error("Không tạo được key: " + str(e)[:100], 500)
    if not lic:
        return _auth_error("Không tạo được key", 500)
    try:
        db.log_event("create_license", f"Admin tạo key {lic['key_value']} · {label} · {hours}h")
    except Exception:
        pass
    return web.json_response({"ok": True, "id": lic["key_id"], "key": lic["key_value"], "hours": hours})


async def api_admin_license_toggle(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    lid = request.match_info.get("id", "")
    if not lid:
        return _auth_error("Thiếu ID", 400)
    try:
        db.toggle_license(lid)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    try:
        db.log_event("toggle_license", f"Admin toggle key {lid}")
    except Exception:
        pass
    return web.json_response({"ok": True})


async def api_admin_license_extend(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    lid = request.match_info.get("id", "")
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    days = _to_int(d.get("days", 0), 0)
    if not days or days <= 0 or days > 3650:
        return _auth_error("Số ngày không hợp lệ", 400)
    if not db.get_license_by_id(lid):
        return _auth_error("Không tìm thấy key", 404)
    try:
        db.extend_license(lid, days * 1440)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    try:
        db.log_event("extend_license", f"+{days}d cho key {lid}")
    except Exception:
        pass
    return web.json_response({"ok": True})


async def api_admin_license_reset(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    lid = request.match_info.get("id", "")
    try:
        db.reset_device(lid)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    try:
        db.log_event("reset_device", f"Gỡ thiết bị key {lid}")
    except Exception:
        pass
    return web.json_response({"ok": True})


async def api_admin_orders(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        c = db._conn()
        rows = c.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
        c.close()
    except Exception as e:
        return _auth_error("Lỗi truy vấn: " + str(e)[:100], 500)
    status_map = {
        "pending": "PENDING", "processing": "PENDING",
        "completed": "APPROVED", "approved": "APPROVED",
        "cancelled": "REJECTED", "rejected": "REJECTED",
    }
    out = []
    for o in rows:
        d = dict(o)
        out.append({
            "id": d["order_id"],
            "telegram_id": d["user_id"],
            "username": "",
            "package_days": 0,
            "amount": d["amount"],
            "status": status_map.get(d["status"], "PENDING"),
            "created_at": d["created_at"],
        })
    return web.json_response({"ok": True, "orders": out})


async def api_admin_order_approve(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    oid = request.match_info.get("id", "")
    try:
        order = db.get_order(oid)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    if not order:
        return _auth_error("Không tìm thấy đơn", 404)
    if order["status"] in ("completed", "approved"):
        return _auth_error("Đơn đã xử lý", 400)
    try:
        db.set_order_status(oid, "completed", "Admin duyệt")
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    delivered = False
    try:
        from bot1 import _admin_bot
        try:
            key_val = order.get("extra_data", "") or ""
            amt = f"{int(order.get('amount',0)):,}".replace(",", ".")
            msg = (
                f"✅ <b>ĐƠN HÀNG ĐÃ DUYỆT</b>\n"
                f"🆔 Đơn: <code>{oid}</code>\n"
                f"📦 DV: {order.get('service_name','')}\n"
                f"💰 {amt}đ"
            )
            if key_val:
                msg += f"\n🔑 Key: <code>{key_val}</code>"
            await _admin_bot.send_message(order["user_id"], msg, parse_mode="HTML")
            delivered = True
        except Exception:
            pass
    except Exception:
        pass
    try:
        db.log_event("order_approve", f"Duyệt đơn {oid}")
    except Exception:
        pass
    return web.json_response({"ok": True, "delivered": delivered})


async def api_admin_order_reject(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    oid = request.match_info.get("id", "")
    try:
        order = db.get_order(oid)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    if not order:
        return _auth_error("Không tìm thấy đơn", 404)
    try:
        d = await request.json()
    except Exception:
        d = {}
    reason = str(d.get("reason", "Chưa xác nhận được thanh toán"))[:300]
    try:
        db.set_order_status(oid, "cancelled", reason)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    delivered = False
    try:
        from bot1 import _admin_bot
        try:
            await _admin_bot.send_message(
                order["user_id"],
                f"❌ <b>ĐƠN HÀNG BỊ TỪ CHỐI</b>\n🆔 Đơn: <code>{oid}</code>\n📝 Lý do: {reason}",
                parse_mode="HTML"
            )
            delivered = True
        except Exception:
            pass
    except Exception:
        pass
    try:
        db.log_event("order_reject", f"Từ chối đơn {oid}: {reason}")
    except Exception:
        pass
    return web.json_response({"ok": True, "delivered": delivered})


async def api_admin_events(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        events = db.get_events(200)
    except Exception:
        events = []
    return web.json_response({"ok": True, "events": events})


async def api_admin_system(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    db_path = os.environ.get("PAWNOVA_DB_PATH", DB_PATH)
    lic_count = 0
    ev_count = 0
    ord_count = 0
    pred_count = 0
    try:
        lic_count = len(db.list_licenses())
    except Exception:
        pass
    try:
        ev_count = len(db.get_events(1000))
    except Exception:
        pass
    try:
        c = db._conn()
        ord_count = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        tbl = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
        ).fetchone()
        if tbl:
            pred_count = c.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        c.close()
    except Exception:
        pass
    return web.json_response({"ok": True, "storage": {
        "path": db_path,
        "persistent_path_configured": bool(os.environ.get("PAWNOVA_DB_PATH")),
        "backup_exists": os.path.exists(db_path),
    }, "counts": {
        "licenses": lic_count,
        "orders": ord_count,
        "events": ev_count,
        "prediction_logs": pred_count,
    }, "poll_seconds": 10})


async def api_admin_backup(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    db_path = os.environ.get("PAWNOVA_DB_PATH", DB_PATH)
    if not os.path.exists(db_path):
        return _auth_error("DB không tồn tại", 404)
    try:
        return web.FileResponse(
            db_path,
            headers={"Content-Disposition": 'attachment; filename="pawnova-backup.sqlite3"'},
        )
    except Exception as e:
        return _auth_error(str(e)[:100], 500)


async def api_admin_monitor_key(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    dev = str(d.get("device_id", "")).strip()
    if not dev:
        return _auth_error("Thiếu device_id", 400)
    try:
        lic = db.get_or_create_monitor_license(dev)
    except Exception as e:
        return _auth_error("Không tạo được key monitor: " + str(e)[:100], 500)
    if not lic:
        return _auth_error("Không tạo được key monitor", 500)
    return web.json_response({"ok": True, "key": lic["key_value"], "license_id": lic["key_id"]})


async def api_admin_users(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    q = request.query.get("q", "").strip()
    try:
        rows = db.find_user_by_username_or_id(q) if q else db.all_users(100)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    now = datetime.now()
    out = []
    for r in rows:
        try:
            keys = db.get_keys_by_user(r["user_id"])
        except Exception:
            keys = []
        active = 0
        last = None
        for k in keys:
            exp = k.get("expires_at") or ""
            try:
                if k.get("activated") and exp and datetime.fromisoformat(exp) > now:
                    active += 1
            except Exception:
                pass
            ct = k.get("created_at") or ""
            if not last or ct > last:
                last = ct
        out.append({
            "telegram_id": r["user_id"],
            "key_count": len(keys),
            "active_keys": active,
            "last_key_created": last,
        })
    return web.json_response({"ok": True, "users": out})


async def api_admin_affiliates(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    q = request.query.get("q", "").strip()
    _ensure_affiliate_table()
    try:
        c = db._conn()
        if q:
            rows = c.execute(
                "SELECT * FROM affiliates WHERE username LIKE ? "
                "OR CAST(telegram_id AS TEXT) LIKE ? "
                "OR referral_code LIKE ? ORDER BY id DESC",
                (f"%{q}%", f"%{q}%", f"%{q}%"),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM affiliates ORDER BY id DESC").fetchall()
        c.close()
    except Exception:
        rows = []
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "telegram_id": r["telegram_id"],
            "username": r["username"],
            "referral_code": r["referral_code"],
            "commission_percent": r["commission_percent"],
            "active": bool(r["active"]),
            "referred_orders": 0,
            "approved_orders": 0,
            "approved_revenue": 0,
            "earned_commission": 0,
        })
    return web.json_response({"ok": True, "affiliates": out})


async def api_admin_affiliate_create(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    _ensure_affiliate_table()
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    tg_id = _to_int(d.get("telegram_id", 0), 0)
    uname = str(d.get("username", "")).strip()
    comm = _to_float(d.get("commission_percent", 10), 10)
    if not tg_id:
        return _auth_error("Thiếu Telegram ID", 400)
    if comm is None or comm < 0 or comm > 100:
        return _auth_error("Hoa hồng không hợp lệ", 400)
    code = "AFF" + secrets.token_hex(4).upper()
    try:
        c = db._conn()
        c.execute(
            "INSERT INTO affiliates(telegram_id,username,referral_code,commission_percent) "
            "VALUES(?,?,?,?)",
            (tg_id, uname, code, comm),
        )
        aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit()
        c.close()
    except Exception as e:
        return _auth_error("Không tạo được CTV: " + str(e)[:120], 500)
    try:
        db.log_event("create_affiliate", f"Tạo CTV {uname} ({tg_id}) · {code}")
    except Exception:
        pass
    return web.json_response({"ok": True, "id": aid, "referral_code": code})


async def api_admin_affiliate_toggle(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    _ensure_affiliate_table()
    i = _to_int(request.match_info.get("id"))
    if not i:
        return _auth_error("ID không hợp lệ", 400)
    try:
        c = db._conn()
        c.execute(
            "UPDATE affiliates SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?",
            (i,),
        )
        c.commit()
        c.close()
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    return web.json_response({"ok": True})


async def api_admin_ctv_list(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        accounts = db.ctv_list()
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    out = []
    for a in accounts:
        out.append({
            "id": a["id"],
            "username": a["username"],
            "commission_percent": a["commission_percent"],
            "daily_quota": a["daily_quota"],
            "active": a["active"],
            "last_login": a["last_login"],
            "key_count": 0,
        })
    return web.json_response({"ok": True, "ctv": out})


async def api_admin_ctv_create(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    u = str(d.get("username", "")).strip()
    p = str(d.get("password", ""))
    if len(u) < 3:
        return _auth_error("Username quá ngắn", 400)
    if len(p) < 8:
        return _auth_error("Mật khẩu ≥ 8 ký tự", 400)
    try:
        if db.ctv_get_by_username(u):
            return _auth_error("Username đã tồn tại", 409)
    except Exception:
        pass
    comm = _to_float(d.get("commission_percent", 10), 10)
    quota = _to_int(d.get("daily_quota", 20), 20)
    tg_id = _to_int(d.get("telegram_id", 0), 0) or 0
    if comm is None or comm < 0 or comm > 100:
        return _auth_error("Hoa hồng không hợp lệ", 400)
    if quota is None or quota < 1 or quota > 500:
        return _auth_error("Quota không hợp lệ", 400)
    ph = hashlib.sha256(p.encode()).hexdigest()
    try:
        db.ctv_create(u, ph, comm, quota, tg_id)
    except Exception as e:
        return _auth_error("Không tạo được CTV: " + str(e)[:120], 500)
    try:
        db.log_event("create_ctv", f"Admin tạo CTV {u}")
    except Exception:
        pass
    return web.json_response({"ok": True})


async def api_admin_ctv_toggle(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    i = _to_int(request.match_info.get("id"))
    if not i:
        return _auth_error("ID không hợp lệ", 400)
    try:
        if not db.ctv_get_by_id(i):
            return _auth_error("Không tìm thấy CTV", 404)
        db.ctv_toggle(i)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    return web.json_response({"ok": True})


async def api_admin_ctv_password(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    i = _to_int(request.match_info.get("id"))
    if not i:
        return _auth_error("ID không hợp lệ", 400)
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    p = str(d.get("password", ""))
    if len(p) < 8:
        return _auth_error("Mật khẩu ≥ 8 ký tự", 400)
    try:
        if not db.ctv_get_by_id(i):
            return _auth_error("Không tìm thấy CTV", 404)
        db.ctv_set_password(i, hashlib.sha256(p.encode()).hexdigest())
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    return web.json_response({"ok": True})


async def api_admin_broadcast(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    msg = str(d.get("message", "")).strip()
    if not msg:
        return _auth_error("Thiếu nội dung", 400)
    if len(msg) > 3500:
        return _auth_error("Nội dung quá dài", 400)
    try:
        users = db.all_users(10000)
    except Exception:
        users = []
    try:
        from bot1 import _admin_bot
    except Exception as e:
        return _auth_error("Không nạp được bot: " + str(e)[:100], 500)
    sent = failed = 0
    for u in users:
        try:
            await _admin_bot.send_message(u["user_id"], msg, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    try:
        db.log_event("broadcast", f"Broadcast: {sent}/{len(users)} sent, {failed} failed")
    except Exception:
        pass
    return web.json_response({"ok": True, "sent": sent, "failed": failed, "recipients": len(users)})


async def api_admin_diagnose(request):
    if not _is_admin(request):
        return _auth_error("Chưa đăng nhập")
    games = {}
    try:
        async with ClientSession(timeout=ClientTimeout(total=8)) as s:
            for g, url in GAME_API_URLS.items():
                t0 = time.time()
                info = {
                    "url_set": True, "status": 0, "ms": 0, "json": False,
                    "fetch_error": None, "ready": False, "cache_error": None,
                }
                try:
                    async with s.get(url) as r:
                        info["status"] = r.status
                        try:
                            info["json"] = (r.content_type or "").startswith("application/json")
                        except Exception:
                            info["json"] = False
                        raw = await r.read()
                        info["ready"] = r.status == 200 and raw[:1] in (b"{", b"[")
                        if not info["ready"] and raw:
                            info["cache_error"] = raw[:120].decode("utf-8", "ignore")
                except Exception as e:
                    info["fetch_error"] = str(e)[:200]
                info["ms"] = int((time.time() - t0) * 1000)
                games[g] = info
    except Exception as e:
        return _auth_error("Lỗi diagnose: " + str(e)[:100], 500)
    return web.json_response({"ok": True, "engine": "aiohttp-proxy", "games": games})


async def api_ctv_login(request):
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    u = str(d.get("username", "")).strip()
    p = str(d.get("password", ""))
    if not u or not p:
        return _auth_error("Thiếu thông tin", 400)
    try:
        acc = db.ctv_get_by_username(u)
    except Exception as e:
        return _auth_error(str(e)[:100], 500)
    if not acc:
        return _auth_error("Sai tài khoản hoặc mật khẩu", 401)
    if not acc.get("active"):
        return _auth_error("Tài khoản bị khoá", 403)
    ph = hashlib.sha256(p.encode()).hexdigest()
    if acc["password_hash"] != ph:
        return _auth_error("Sai tài khoản hoặc mật khẩu", 401)
    tok = _new_token()
    _ctv_sessions[tok] = {"ctv": acc["id"], "created": time.time()}
    try:
        db.ctv_update_last_login(acc["id"])
    except Exception:
        pass
    resp = web.json_response({"ok": True, "ctv": {"id": acc["id"], "username": acc["username"]}})
    resp.set_cookie("ctv_session", tok, httponly=True, samesite="Lax", max_age=86400 * 3)
    return resp


async def api_ctv_logout(request):
    tok = request.cookies.get("ctv_session")
    if tok:
        _ctv_sessions.pop(tok, None)
    resp = web.json_response({"ok": True})
    resp.del_cookie("ctv_session")
    return resp


async def api_ctv_me(request):
    cid = _get_ctv(request)
    if not cid:
        return _auth_error("Chưa đăng nhập")
    try:
        acc = db.ctv_get_by_id(cid)
    except Exception:
        acc = None
    if not acc:
        return _auth_error("Không tìm thấy tài khoản", 404)
    used_today = 0
    key_count = 0
    active_keys = 0
    try:
        c = db._conn()
        today = datetime.now().strftime("%Y-%m-%d")
        rows = c.execute(
            "SELECT * FROM keys WHERE telegram_id=? ORDER BY created_at DESC LIMIT 500",
            (acc.get("telegram_id") or 0,),
        ).fetchall()
        c.close()
        now = datetime.now()
        for r in rows:
            key_count += 1
            if (r["created_at"] or "").startswith(today):
                used_today += 1
            exp = r["expires_at"] or ""
            try:
                if r["activated"] and exp and datetime.fromisoformat(exp) > now:
                    active_keys += 1
            except Exception:
                pass
    except Exception:
        pass
    return web.json_response({"ok": True, "ctv": {
        "id": acc["id"],
        "username": acc["username"],
        "commission_percent": acc["commission_percent"],
        "daily_quota": acc["daily_quota"],
        "used_today": used_today,
        "key_count": key_count,
        "active_keys": active_keys,
    }})


async def api_ctv_licenses(request):
    cid = _get_ctv(request)
    if not cid:
        return _auth_error("Chưa đăng nhập")
    try:
        acc = db.ctv_get_by_id(cid)
    except Exception:
        acc = None
    if not acc:
        return _auth_error("Không tìm thấy tài khoản", 404)
    try:
        c = db._conn()
        rows = c.execute(
            "SELECT * FROM keys WHERE telegram_id=? ORDER BY created_at DESC LIMIT 200",
            (acc.get("telegram_id") or 0,),
        ).fetchall()
        c.close()
    except Exception:
        rows = []
    now = datetime.now()
    out = []
    for x in rows:
        exp_iso = x["expires_at"] or ""
        act = bool(x["activated"])
        expired = False
        if act and exp_iso:
            try:
                expired = datetime.fromisoformat(exp_iso) < now
            except Exception:
                expired = False
        out.append({
            "id": x["key_id"],
            "label": x.get("label") or "",
            "key_prefix": (x["key_value"] or "")[:8],
            "active": bool(x["active"]),
            "activated": act,
            "expired": expired,
            "unactivated": not act,
            "expires_at": exp_iso,
            "duration_hours": (x.get("minutes") or 0) / 60,
        })
    return web.json_response({"ok": True, "licenses": out})


async def api_ctv_license_create(request):
    cid = _get_ctv(request)
    if not cid:
        return _auth_error("Chưa đăng nhập")
    try:
        acc = db.ctv_get_by_id(cid)
    except Exception:
        acc = None
    if not acc:
        return _auth_error("Không tìm thấy tài khoản", 404)
    if not acc.get("active"):
        return _auth_error("Tài khoản bị khoá", 403)
    try:
        d = await request.json()
    except Exception:
        return _auth_error("JSON lỗi", 400)
    label = str(d.get("label", "")).strip()
    hours = _to_float(d.get("hours", 24), 24)
    if not label:
        return _auth_error("Thiếu tên khách", 400)
    if hours is None or hours <= 0 or hours > 8760:
        return _auth_error("Số giờ không hợp lệ", 400)
    today = datetime.now().strftime("%Y-%m-%d")
    used_today = 0
    try:
        c = db._conn()
        for r in c.execute(
            "SELECT created_at FROM keys WHERE telegram_id=?",
            (acc.get("telegram_id") or 0,),
        ).fetchall():
            if (r["created_at"] or "").startswith(today):
                used_today += 1
        c.close()
    except Exception:
        pass
    quota = acc.get("daily_quota") or 20
    if used_today >= quota:
        return _auth_error(f"Đã đạt giới hạn {quota} key/ngày", 403)
    try:
        lic = db.create_license(
            label=f"{label} [CTV{acc['id']}]",
            hours=hours,
            telegram_id=acc.get("telegram_id") or 0,
        )
    except Exception as e:
        return _auth_error("Không tạo được key: " + str(e)[:100], 500)
    if not lic:
        return _auth_error("Không tạo được key", 500)
    try:
        db.log_event("ctv_key", f"CTV {acc['username']} tạo key {lic['key_value']} · {label} · {hours}h")
    except Exception:
        pass
    try:
        from bot1 import _admin_bot
        try:
            await _admin_bot.send_message(
                ADMIN_CHAT_ID,
                f"🤝 <b>CTV TẠO KEY</b>\n"
                f"👤 CTV: <b>{acc['username']}</b>\n"
                f"🔑 Key: <code>{lic['key_value']}</code>\n"
                f"📝 Khách: {label}\n"
                f"⏰ {hours}h",
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception:
        pass
    return web.json_response({"ok": True, "id": lic["key_id"], "key": lic["key_value"], "hours": hours})


async def api_health(request):
    return web.json_response({
        "ok": True, "status": "online", "ts": int(time.time()),
        "predictions": {
            "hu":  dict(_predict_cache["hu"]),
            "md5": dict(_predict_cache["md5"]),
        },
    })


def build_paw_server():
    app = web.Application(client_max_size=1024 * 256, middlewares=[_error_mw])
    app.router.add_get("/", serve_tool)
    app.router.add_get("/admin", serve_admin)
    app.router.add_post("/api/activate", api_activate)
    app.router.add_get("/api/predict", api_predict)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/health", api_health)
    app.router.add_post("/api/admin/login", api_admin_login)
    app.router.add_post("/api/admin/logout", api_admin_logout)
    app.router.add_get("/api/admin/summary", api_admin_summary)
    app.router.add_get("/api/admin/licenses", api_admin_licenses_get)
    app.router.add_post("/api/admin/licenses", api_admin_license_create)
    app.router.add_post("/api/admin/licenses/{id}/toggle", api_admin_license_toggle)
    app.router.add_post("/api/admin/licenses/{id}/extend", api_admin_license_extend)
    app.router.add_post("/api/admin/licenses/{id}/reset-device", api_admin_license_reset)
    app.router.add_get("/api/admin/orders", api_admin_orders)
    app.router.add_post("/api/admin/orders/{id}/approve", api_admin_order_approve)
    app.router.add_post("/api/admin/orders/{id}/reject", api_admin_order_reject)
    app.router.add_get("/api/admin/events", api_admin_events)
    app.router.add_get("/api/admin/system", api_admin_system)
    app.router.add_get("/api/admin/backup", api_admin_backup)
    app.router.add_post("/api/admin/monitor-key", api_admin_monitor_key)
    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_get("/api/admin/affiliates", api_admin_affiliates)
    app.router.add_post("/api/admin/affiliates", api_admin_affiliate_create)
    app.router.add_post("/api/admin/affiliates/{id}/toggle", api_admin_affiliate_toggle)
    app.router.add_get("/api/admin/ctv", api_admin_ctv_list)
    app.router.add_post("/api/admin/ctv", api_admin_ctv_create)
    app.router.add_post("/api/admin/ctv/{id}/toggle", api_admin_ctv_toggle)
    app.router.add_post("/api/admin/ctv/{id}/password", api_admin_ctv_password)
    app.router.add_get("/api/admin/diagnose", api_admin_diagnose)
    app.router.add_post("/api/admin/broadcast", api_admin_broadcast)
    app.router.add_post("/api/ctv/login", api_ctv_login)
    app.router.add_post("/api/ctv/logout", api_ctv_logout)
    app.router.add_get("/api/ctv/me", api_ctv_me)
    app.router.add_get("/api/ctv/licenses", api_ctv_licenses)
    app.router.add_post("/api/ctv/licenses", api_ctv_license_create)
    return app