# app/normalize.py
import re
from typing import Any


# ----------------------------
# Heuristic hints (case-insensitive)
# ----------------------------
INCOME_HINTS = [
    # common income/credits
    r"\bpayroll\b",
    r"\bsalary\b",
    r"\bdeposit\b",
    r"\binterest\b",
    r"\brefund\b",
    r"\breversal\b",
    r"\bcredit\b",
    r"\bcr\b",
    r"\bpos\s+return\b",

    # specific patterns you called out
    r"\bmonthly\s*fee\s*rebate\b",

    # specific patterns you called out
    r"\bmisc\s*payment\s*sunlife\b",

    # OCR often turns EI -> El (capital i -> lowercase L)
    r"\be[il]\s*canada\b",
    r"\bc[il]\s*canada\b",
    r"\bei\s+benefit\b",
    r"\bemployment\s+insurance\b",
    r"\bservice\s+canada\b",
]

CREDIT_CARD_PAYMENT_HINTS = [
    r"\bcrd\.?\s*card\b",
    r"\bcredit\s*card\b",
    r"\bbill\s*payment\b",
    r"\bloc\s*pay\b",
    r"\bcard\s*payment\b",
]


_income_re = re.compile("|".join(INCOME_HINTS), re.IGNORECASE)
_ccpay_re = re.compile("|".join(CREDIT_CARD_PAYMENT_HINTS), re.IGNORECASE)


# ----------------------------
# Amount parsing (more robust)
# ----------------------------
def parse_amount(x: Any) -> float:
    """
    Parse an amount string into a float.

    Handles:
      - $ and commas
      - (12.34) -> -12.34
      - 12.34-  -> -12.34   (trailing minus)
      - "12.34 CR" / "12.34 DR" (CR => negative, DR => positive)
      - OCR noise (keeps digits, dot, +, -)
    """
    s = "" if x is None else str(x).strip()
    if not s:
        raise ValueError(f"Unparseable amount: {x!r}")

    neg = False

    # Parentheses indicate negative
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    # Trailing minus like "123.45-"
    if s.endswith("-"):
        neg = True
        s = s[:-1].strip()

    # CR/DR markers (common in statements)
    # Treat CR as credit/inflow (negative per your convention)
    # DR as debit/outflow (positive)
    if re.search(r"\bcr\b", s, flags=re.IGNORECASE):
        neg = True
        s = re.sub(r"\bcr\b", "", s, flags=re.IGNORECASE).strip()
    if re.search(r"\bdr\b", s, flags=re.IGNORECASE):
        # DR explicitly means debit; do not force negative
        s = re.sub(r"\bdr\b", "", s, flags=re.IGNORECASE).strip()

    # remove currency symbols, commas, spaces
    s = s.replace("$", "").replace(",", "").strip()

    # keep digits, dot, +/- only (strip other OCR artifacts)
    s = re.sub(r"[^0-9.\-\+]", "", s)

    # collapse multiple dots (keep first)
    if s.count(".") > 1:
        first = s.find(".")
        s = s[: first + 1] + s[first + 1 :].replace(".", "")

    if s in ("", "-", "+", "."):
        raise ValueError(f"Unparseable amount: {x!r}")

    val = float(s)

    # Apply negative convention if needed
    return -abs(val) if neg else val


# ----------------------------
# Heuristic matchers
# ----------------------------
def looks_like_income(desc: str) -> bool:
    return bool(_income_re.search(desc or ""))


def looks_like_cc_payment(desc: str) -> bool:
    return bool(_ccpay_re.search(desc or ""))


# ----------------------------
# Normalization
# ----------------------------
def normalize_amount(bank: str, account_type: str, description: str, raw_amount: float) -> float:
    """
    Output convention:
      - Expenses/outflows => positive
      - Income/credits/refunds/payments-in => negative

    NOTE:
    - This uses description-based heuristics only.
    - If your PDF extraction can provide separate Debit/Credit columns,
      use those instead (more reliable than text hints).
    """
    b = (bank or "").lower()
    t = (account_type or "").lower()
    amt = float(raw_amount)

    # Deposit accounts (RBC CHQ / Scotia debit)
    if t == "deposit_account" or ("rbc" in b and t == ""):
        # credits/income should be negative
        return -abs(amt) if looks_like_income(description) else abs(amt)

    # Credit cards
    if t == "credit_card" or ("scotia" in b and t == ""):
        # refunds/payments should be negative; purchases positive
        if looks_like_income(description) or looks_like_cc_payment(description):
            return -abs(amt)
        return abs(amt)

    # fallback heuristic
    if looks_like_income(description) or looks_like_cc_payment(description):
        return -abs(amt)
    return abs(amt)
