# db.py
import sqlite3
import uuid
import secrets
import json
from datetime import datetime, timedelta
from typing import Optional
from config import DB_PATH, ORDER_PREFIX


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    c = _conn()
    c.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS users (
        user_id      INTEGER PRIMARY KEY,
        username     TEXT    DEFAULT '',
        full_name    TEXT    DEFAULT '',
        balance      INTEGER DEFAULT 0,
        total_spent  INTEGER DEFAULT 0,
        total_orders INTEGER DEFAULT 0,
        is_banned    INTEGER DEFAULT 0,
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id     TEXT PRIMARY KEY,
        user_id      INTEGER,
        service_id   TEXT,
        service_name TEXT,
        amount       INTEGER,
        extra_data   TEXT    DEFAULT '',
        status       TEXT    DEFAULT 'pending',
        note         TEXT    DEFAULT '',
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP,
        updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS transactions (
        tx_id        TEXT    PRIMARY KEY,
        user_id      INTEGER,
        kind         TEXT,
        amount       INTEGER,
        ref_id       TEXT    DEFAULT '',
        description  TEXT    DEFAULT '',
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS pending_payments (
        payment_id  TEXT    PRIMARY KEY,
        user_id     INTEGER,
        amount      INTEGER,
        order_ref   TEXT    UNIQUE,
        order_data  TEXT    DEFAULT '{}',
        status      TEXT    DEFAULT 'pending',
        message_id  INTEGER DEFAULT 0,
        created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
        expires_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS keys (
        key_id       TEXT    PRIMARY KEY,
        key_value    TEXT    UNIQUE,
        package_id   TEXT    DEFAULT '',
        package_name TEXT    DEFAULT '',
        user_id      INTEGER,
        days         INTEGER DEFAULT 0,
        activated_at TEXT    DEFAULT CURRENT_TIMESTAMP,
        expires_at   TEXT    DEFAULT '',
        created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS admins (
        user_id   INTEGER PRIMARY KEY,
        username  TEXT    DEFAULT '',
        added_at  TEXT    DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS predictions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        license_id      INTEGER,
        game            TEXT    NOT NULL,
        target_session  TEXT    NOT NULL,
        prediction      TEXT,
        actual          TEXT    DEFAULT '',
        grade           TEXT    DEFAULT 'PENDING',
        created_at      TEXT    DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(license_id, game, target_session)
    );
    CREATE INDEX IF NOT EXISTS idx_pred_lic ON predictions(license_id, game);

    CREATE TABLE IF NOT EXISTS free_key_log (
        user_id  INTEGER,
        day      TEXT,
        result   TEXT,
        PRIMARY KEY(user_id, day)
    );

    CREATE TABLE IF NOT EXISTS free_key_proofs (
        file_id  TEXT PRIMARY KEY,
        user_id  INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ctv_accounts (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        username           TEXT UNIQUE NOT NULL,
        password_hash      TEXT NOT NULL,
        commission_percent REAL DEFAULT 10,
        daily_quota        INTEGER DEFAULT 20,
        telegram_id        INTEGER DEFAULT 0,
        active             INTEGER DEFAULT 1,
        created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
        last_login         TEXT
    );
    """)
    for sql in [
        "ALTER TABLE keys ADD COLUMN label TEXT DEFAULT ''",
        "ALTER TABLE keys ADD COLUMN device_id TEXT DEFAULT ''",
        "ALTER TABLE keys ADD COLUMN active INTEGER DEFAULT 1",
        "ALTER TABLE keys ADD COLUMN activated INTEGER DEFAULT 0",
        "ALTER TABLE keys ADD COLUMN telegram_id INTEGER DEFAULT 0",
        "ALTER TABLE keys ADD COLUMN minutes INTEGER DEFAULT 0",
    ]:
        try: c.execute(sql)
        except sqlite3.OperationalError: pass

    c.execute("UPDATE keys SET activated=1 WHERE (activated=0 OR activated IS NULL) AND expires_at IS NOT NULL AND expires_at != ''")
    c.execute("UPDATE keys SET minutes = days*24*60 WHERE (minutes=0 OR minutes IS NULL) AND days > 0")
    c.commit()
    c.close()


def _gen_key() -> str:
    raw = secrets.token_hex(8).upper()
    return f"PAW-{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def _norm_key(s: str) -> str:
    return (s or "").strip().upper().replace("-", "").replace(" ", "") \
             .replace("`", "").replace("'", "").replace(".", "")


def _key_variants(norm: str) -> set:
    out = {norm}
    if norm.startswith("PAW") and len(norm) == 19:
        b = norm[3:]
        out.add(f"PAW-{b[0:4]}-{b[4:8]}-{b[8:12]}-{b[12:16]}")
    elif len(norm) == 16:
        out.add(f"{norm[0:4]}-{norm[4:8]}-{norm[8:12]}-{norm[12:16]}")
        out.add(f"PAW-{norm[0:4]}-{norm[4:8]}-{norm[8:12]}-{norm[12:16]}")
    return out


def get_user(uid):
    c = _conn(); r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone(); c.close()
    return dict(r) if r else None


def upsert_user(uid, username, full_name):
    c = _conn()
    c.execute("""INSERT INTO users(user_id,username,full_name) VALUES(?,?,?)
                 ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name""",
              (uid, username or "", full_name or ""))
    c.commit(); c.close()
    return get_user(uid)


def find_user_by_username_or_id(q):
    c = _conn(); q = q.strip().lstrip('@'); out = []
    if q.isdigit():
        r = c.execute("SELECT * FROM users WHERE user_id=?", (int(q),)).fetchone()
        if r: out = [dict(r)]
    if not out:
        out = [dict(r) for r in c.execute(
            "SELECT * FROM users WHERE LOWER(username) LIKE LOWER(?) OR LOWER(full_name) LIKE LOWER(?) LIMIT 10",
            (f"%{q}%", f"%{q}%")).fetchall()]
    c.close(); return out


def credit(uid, amount, ref_id="", desc=""):
    c = _conn()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
    c.execute("INSERT INTO transactions(tx_id,user_id,kind,amount,ref_id,description) VALUES(?,?,?,?,?,?)",
              (uuid.uuid4().hex[:10].upper(), uid, "credit", amount, ref_id, desc))
    c.commit(); c.close()


def deduct(uid, amount, ref_id="", desc=""):
    c = _conn()
    r = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    if not r or r[0] < amount:
        c.close(); return False
    c.execute("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE user_id=?", (amount, amount, uid))
    c.execute("INSERT INTO transactions(tx_id,user_id,kind,amount,ref_id,description) VALUES(?,?,?,?,?,?)",
              (uuid.uuid4().hex[:10].upper(), uid, "debit", -amount, ref_id, desc))
    c.commit(); c.close(); return True


def ban_user(uid, banned=1):
    c = _conn(); c.execute("UPDATE users SET is_banned=? WHERE user_id=?", (banned, uid)); c.commit(); c.close()


def all_users(limit=100):
    c = _conn(); r = c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall(); c.close()
    return [dict(x) for x in r]


def new_order(uid, service_id, service_name, amount, extra="", status="pending"):
    oid = f"{ORDER_PREFIX}{datetime.now().strftime('%d%m%H%M')}{uuid.uuid4().hex[:4].upper()}"
    c = _conn()
    c.execute(
        "INSERT INTO orders(order_id,user_id,service_id,service_name,amount,extra_data,status)"
        " VALUES(?,?,?,?,?,?,?)",
        (oid, uid, service_id, service_name, amount, extra, status))
    c.execute("UPDATE users SET total_orders=total_orders+1 WHERE user_id=?", (uid,))
    c.commit(); c.close(); return oid


def set_order_status(oid, status, note=""):
    c = _conn()
    c.execute("UPDATE orders SET status=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE order_id=?", (status, note, oid))
    c.commit(); c.close()


def get_order(oid):
    c = _conn(); r = c.execute("SELECT * FROM orders WHERE order_id=?", (oid,)).fetchone(); c.close()
    return dict(r) if r else None


def get_orders(user_id, limit=20):
    c = _conn()
    r = c.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)).fetchall()
    c.close(); return [dict(x) for x in r]


def get_keys_by_user(user_id):
    c = _conn()
    r = c.execute(
        "SELECT * FROM keys WHERE telegram_id=? ORDER BY created_at DESC",
        (user_id,)).fetchall()
    c.close(); return [dict(x) for x in r]


def get_pending_orders(limit=30):
    c = _conn()
    r = c.execute("SELECT * FROM orders WHERE status IN ('pending','processing') ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    c.close(); return [dict(x) for x in r]


def new_payment(uid, amount, order_ref, order_data, msg_id=0):
    pid = uuid.uuid4().hex[:8].upper()
    exp = (datetime.now() + timedelta(minutes=15)).isoformat()
    c = _conn()
    c.execute("INSERT INTO pending_payments(payment_id,user_id,amount,order_ref,order_data,message_id,expires_at) VALUES(?,?,?,?,?,?,?)",
              (pid, uid, amount, order_ref, json.dumps(order_data), msg_id, exp))
    c.commit(); c.close(); return pid


def get_payment_by_ref(ref):
    c = _conn(); r = c.execute("SELECT * FROM pending_payments WHERE order_ref=? AND status='pending'", (ref,)).fetchone(); c.close()
    return dict(r) if r else None


def get_payment_by_id(pid):
    c = _conn(); r = c.execute("SELECT * FROM pending_payments WHERE payment_id=?", (pid,)).fetchone(); c.close()
    return dict(r) if r else None


def complete_payment(pid) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE pending_payments SET status='completed' WHERE payment_id=? AND status='pending'",
        (pid,)
    )
    changed = cur.rowcount > 0
    c.commit()
    c.close()
    return changed


def expire_payment(pid):
    c = _conn(); c.execute("UPDATE pending_payments SET status='expired' WHERE payment_id=?", (pid,)); c.commit(); c.close()


def get_expired_payments():
    c = _conn(); now = datetime.now().isoformat()
    r = c.execute("SELECT * FROM pending_payments WHERE status='pending' AND expires_at < ?", (now,)).fetchall()
    c.close(); return [dict(x) for x in r]


def create_license(label, minutes=0, hours=None, telegram_id=0, package_id="", package_name=""):
    if hours is not None and not minutes:
        minutes = int(round(hours * 60))
    if not minutes:
        minutes = 24 * 60
    kid = uuid.uuid4().hex[:8].upper()
    val = _gen_key()
    now = datetime.now().isoformat()
    c = _conn()
    c.execute("""INSERT INTO keys(key_id,key_value,package_id,package_name,user_id,days,minutes,
                 activated_at,expires_at,created_at,label,active,activated,telegram_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (kid, val, package_id, package_name, telegram_id or 0,
               max(1, minutes // 1440), minutes, now, "", now,
               label or "", 1, 0, telegram_id or 0))
    c.commit(); c.close()
    return {"key_id": kid, "key_value": val, "minutes": minutes}


def get_license_by_key(key):
    if not key:
        return None
    raw = str(key).strip().upper()
    norm = _norm_key(key)
    if not norm:
        return None

    c = _conn()
    for cand in _key_variants(norm):
        r = c.execute("SELECT * FROM keys WHERE key_value=?", (cand,)).fetchone()
        if r:
            c.close(); return dict(r)
    r = c.execute("SELECT * FROM keys WHERE key_value=?", (raw,)).fetchone()
    if r:
        c.close(); return dict(r)

    target = norm
    for row in c.execute("SELECT key_id, key_value FROM keys").fetchall():
        if _norm_key(row["key_value"]) == target:
            r = c.execute("SELECT * FROM keys WHERE key_id=?", (row["key_id"],)).fetchone()
            c.close(); return dict(r) if r else None

    c.close()
    return None


def get_license_by_id(lid):
    c = _conn(); r = c.execute("SELECT * FROM keys WHERE key_id=?", (lid,)).fetchone(); c.close()
    return dict(r) if r else None


def activate_license(lid, device_id):
    c = _conn()
    now = datetime.now()
    r = c.execute("SELECT minutes FROM keys WHERE key_id=?", (lid,)).fetchone()
    if not r:
        c.close(); return None
    minutes = r[0] or 0
    exp = (now + timedelta(minutes=minutes)).isoformat()
    c.execute("UPDATE keys SET device_id=?, activated=1, activated_at=?, expires_at=? WHERE key_id=?",
              (device_id, now.isoformat(), exp, lid))
    c.commit(); c.close()
    return get_license_by_id(lid)


def toggle_license(lid):
    c = _conn()
    c.execute("UPDATE keys SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE key_id=?", (lid,))
    c.commit(); c.close()


def extend_license(lid, minutes):
    c = _conn()
    r = c.execute("SELECT activated,minutes,expires_at FROM keys WHERE key_id=?", (lid,)).fetchone()
    if not r:
        c.close(); return
    if not r["activated"]:
        new_min = (r["minutes"] or 0) + minutes
        c.execute("UPDATE keys SET minutes=? WHERE key_id=?", (new_min, lid))
    else:
        try: base = datetime.fromisoformat(r["expires_at"])
        except Exception: base = datetime.now()
        new_exp = (base + timedelta(minutes=minutes)).isoformat()
        c.execute("UPDATE keys SET expires_at=?, minutes=minutes+? WHERE key_id=?", (new_exp, minutes, lid))
    c.commit(); c.close()


def reset_device(lid):
    c = _conn()
    c.execute("UPDATE keys SET device_id='', activated=0, activated_at='', expires_at='' WHERE key_id=?", (lid,))
    c.commit(); c.close()


def list_licenses(q="", limit=500):
    c = _conn()
    if q:
        qn = _norm_key(q)
        like = f"%{q}%"
        r = c.execute(
            "SELECT * FROM keys WHERE label LIKE ? OR key_value LIKE ? OR telegram_id LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit)).fetchall()
        if not r and qn:
            r = c.execute("SELECT * FROM keys WHERE key_value LIKE ? ORDER BY created_at DESC LIMIT ?",
                          (f"%{qn}%", limit)).fetchall()
    else:
        r = c.execute("SELECT * FROM keys ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    c.close(); return [dict(x) for x in r]


def get_or_create_monitor_license(device_id):
    c = _conn()
    r = c.execute("SELECT * FROM keys WHERE label='__MONITOR__' LIMIT 1").fetchone()
    if r:
        lic = dict(r)
        if not lic["activated"] or lic["device_id"] != device_id:
            c.execute("UPDATE keys SET device_id=?, activated=1, activated_at=?, expires_at=? WHERE key_id=?",
                      (device_id, datetime.now().isoformat(),
                       (datetime.now() + timedelta(days=3650)).isoformat(), lic["key_id"]))
            c.commit()
        c.close()
        return get_license_by_id(lic["key_id"])
    c.close()
    lic = create_license("__MONITOR__", minutes=5256000, telegram_id=0)
    activate_license(lic["key_id"], device_id)
    return get_license_by_id(lic["key_id"])


def upsert_prediction(license_id, game, target, prediction):
    if not target:
        return
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO predictions(license_id,game,target_session,prediction) VALUES(?,?,?,?)",
                  (license_id, game, str(target), prediction))
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def grade_predictions(game, target, actual):
    if not target or not actual:
        return
    c = _conn()
    c.execute("""UPDATE predictions SET actual=?, grade=CASE WHEN prediction=? THEN 'WIN' ELSE 'LOSE' END
                 WHERE game=? AND target_session=? AND grade='PENDING'""",
              (actual, actual, game, str(target)))
    c.commit(); c.close()


def get_predictions_for_license(license_id, game, limit=40):
    c = _conn()
    r = c.execute("""SELECT target_session AS target_session_id, prediction, actual, grade, created_at
                     FROM predictions WHERE license_id=? AND game=?
                     ORDER BY id DESC LIMIT ?""", (license_id, game, limit)).fetchall()
    c.close(); return [dict(x) for x in r]


def can_get_free_key(uid):
    c = _conn()
    day = datetime.now().strftime('%Y-%m-%d')
    r = c.execute("SELECT 1 FROM free_key_log WHERE user_id=? AND day=?", (uid, day)).fetchone()
    c.close(); return r is None


def log_free_key(uid, result):
    c = _conn()
    day = datetime.now().strftime('%Y-%m-%d')
    c.execute("INSERT OR REPLACE INTO free_key_log(user_id,day,result) VALUES(?,?,?)", (uid, day, result))
    c.commit(); c.close()


def add_free_key_proof(file_id, user_id):
    c = _conn()
    try:
        c.execute("INSERT INTO free_key_proofs(file_id, user_id) VALUES(?,?)", (file_id, user_id))
        c.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        c.close()


def is_admin(uid):
    c = _conn(); r = c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone(); c.close()
    return r is not None


def add_admin(uid, username=""):
    c = _conn(); c.execute("INSERT OR IGNORE INTO admins(user_id,username) VALUES(?,?)", (uid, username)); c.commit(); c.close()


def get_admins():
    c = _conn(); r = c.execute("SELECT * FROM admins").fetchall(); c.close(); return [dict(x) for x in r]


def stats():
    c = _conn(); s = {}
    for k, sql in [
        ("users", "SELECT COUNT(*) FROM users"),
        ("orders", "SELECT COUNT(*) FROM orders"),
        ("done", "SELECT COUNT(*) FROM orders WHERE status='completed'"),
        ("pending", "SELECT COUNT(*) FROM orders WHERE status IN ('pending','processing')"),
        ("revenue", "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE kind='credit'"),
        ("spent", "SELECT COALESCE(SUM(total_spent),0) FROM users"),
        ("banned", "SELECT COUNT(*) FROM users WHERE is_banned=1"),
        ("keys", "SELECT COUNT(*) FROM keys"),
    ]:
        s[k] = c.execute(sql).fetchone()[0]
    c.close(); return s


def ctv_create(username, password_hash, commission=10, quota=20, telegram_id=0):
    c = _conn()
    c.execute("INSERT INTO ctv_accounts(username,password_hash,commission_percent,daily_quota,telegram_id) VALUES(?,?,?,?,?)",
              (username, password_hash, commission, quota, telegram_id))
    c.commit(); c.close()


def ctv_get_by_username(u):
    c = _conn(); r = c.execute("SELECT * FROM ctv_accounts WHERE username=?", (u,)).fetchone(); c.close()
    return dict(r) if r else None


def ctv_get_by_id(i):
    c = _conn(); r = c.execute("SELECT * FROM ctv_accounts WHERE id=?", (i,)).fetchone(); c.close()
    return dict(r) if r else None


def ctv_list():
    c = _conn(); r = c.execute("SELECT * FROM ctv_accounts ORDER BY id DESC").fetchall(); c.close()
    return [dict(x) for x in r]


def ctv_update_last_login(i):
    c = _conn(); c.execute("UPDATE ctv_accounts SET last_login=? WHERE id=?", (datetime.now().isoformat(), i)); c.commit(); c.close()


def ctv_toggle(i):
    c = _conn(); c.execute("UPDATE ctv_accounts SET active=CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?", (i,)); c.commit(); c.close()


def ctv_set_password(i, ph):
    c = _conn(); c.execute("UPDATE ctv_accounts SET password_hash=? WHERE id=?", (ph, i)); c.commit(); c.close()


def log_event(kind, message):
    try:
        c = _conn()
        c.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        c.execute("INSERT INTO events(kind,message) VALUES(?,?)", (kind, message))
        c.commit(); c.close()
    except Exception:
        pass


def get_events(limit=100):
    try:
        c = _conn()
        c.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, message TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        r = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        c.close(); return [dict(x) for x in r]
    except Exception:
        return []
