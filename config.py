import os

BOT1_TOKEN = "8584438475:AAHerrh2RY39ceYkBAY9bx3M4W25MHzZsuA"
BOT2_TOKEN = "8737824447:AAFpiL44qvblA7lWu4v88C_U_OL2QRhYvj4"

# ── Bank (BIDV theo QR) ───────────────────────────────────
BANK_ID       = "BIDV"
ACCOUNT_NO    = "8970267100"
ACCOUNT_NAME  = "NGUYEN VAN HOA"

# ── SePay (token mới) ─────────────────────────────────────
SEPAY_TOKEN         = "THHTB20KWBMJA66SBMDILOJVECN2N5PFGEFUXZ7WEWGJEVDACRC3UWHY3UXUM4NZ"
SEPAY_QR_TEMPLATE   = "compact"
SEPAY_POLL_INTERVAL = 10

# ── Bots / Admin ──────────────────────────────────────────
SEED_ADMIN_IDS: list[int] = [7125723417, 8217369095]
ADMIN_CHAT_ID: int = 8217369095
PAYMENT_EXPIRY_MINS = 15

# ── DB / Web ──────────────────────────────────────────────
DB_PATH   = os.environ.get("PAWNOVA_DB_PATH", "service_bot.db")
ORDER_PREFIX = "SVC"
WEBHOOK_PORT = 8080

# Cổng web chính (dùng cho localhost:8000)
PAWNOVA_WEB_PORT = int(os.environ.get("PAWNOVA_WEB_PORT", "8000"))
PAWNOVA_WEB_URL  = os.environ.get("PAWNOVA_WEB_URL", "http://localhost:8000")

# Mật khẩu admin panel — đổi trước khi deploy
ADMIN_PASSWORD = os.environ.get("PAW_ADMIN_PASSWORD", "pawnova2024")

# Nguồn game
_TUNNEL_BASE = os.environ.get("PAW_TUNNEL_BASE", "http://180.93.102.202:5000").rstrip("/")
