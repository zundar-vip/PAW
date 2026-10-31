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
    """
    Extract payment ref from bank transfer description.
    Handles: spaces between chars, lowercase, dashes, surrounding text.
    Pass 1: word-boundary match (clean QR content).
    Pass 2: no-boundary match (ref fused with surrounding chars).
    Pass 3: space/dash-tolerant match (customer typed manually with gaps).
    """
    t = text.upper()
    _pattern = r"(KEY[A-Z0-9]{6,16}|NAPTIEN[A-Z0-9]{6,16}|ORDER[A-Z0-9]{6,16}|SVC[A-Z0-9]{8,16})"

    # Pass 1 — clean match with word boundary
    m = re.search(r"\b" + _pattern + r"\b", t)
    if m:
        return m.group(1)

    # Pass 2 — no boundary (ref fused with surrounding text)
    m = re.search(_pattern, t)
    if m:
        return m.group(1)

    # Pass 3 — spaces/dashes inserted between chars (manual bank transfer)
    m = re.search(r"(KEY|NAPTIEN|ORDER|SVC)\s*[-\s]*([A-Z0-9][A-Z0-9\s\-]{5,30}?)(?:\s|$|\b)", t)
    if m:
        prefix = m.group(1)
        suffix = re.sub(r"[\s\-]", "", m.group(2))
        candidate = prefix + suffix
        if 9 <= len(candidate) <= 19:
            return candidate

    return None
