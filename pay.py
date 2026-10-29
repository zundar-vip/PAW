import re, urllib.parse
from config import BANK_ID, ACCOUNT_NO, ACCOUNT_NAME, SEPAY_QR_TEMPLATE

SEPAY_QR_BASE = "https://qr.sepay.vn/img"


def qr_url(amount: int, ref: str) -> str:
    acc = ACCOUNT_NO.replace(" ", "")
    return (f"{SEPAY_QR_BASE}?acc={acc}&bank={BANK_ID}&amount={amount}"
            f"&des={urllib.parse.quote(ref, safe='')}&template={SEPAY_QR_TEMPLATE}")


def fmt(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


def parse_ref(text: str):
    m = re.search(r"(KEY[A-Z0-9]{6,16}|NAPTIEN[A-Z0-9]{6,16}|ORDER[A-Z0-9]{6,16}|SVC[A-Z0-9]{8,16})", text.upper())
    return m.group(1) if m else None