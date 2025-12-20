# app/normalize.py
import re

INCOME_HINTS = [
    r"\bpayroll\b", r"\bsalary\b", r"\bdeposit\b", r"\binterest\b",
    r"\brefund\b", r"\breversal\b", r"\bcredit\b", r"\bcr\b",
    r"\bpos return\b"
]

CREDIT_CARD_PAYMENT_HINTS = [
    r"\bcrd\.?\s*card\b", r"\bcredit\s*card\b", r"\bbill\s*payment\b", r"\bloc\s*pay\b"
]

def parse_amount(x) -> float:
    s = str(x).strip()

    # Handle (12.34) as negative
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    # remove $, commas, spaces
    s = s.replace("$", "").replace(",", "").strip()

    # keep digits, dot, +/- only
    s = re.sub(r"[^0-9.\-\+]", "", s)

    # collapse multiple dots (keep first)
    if s.count(".") > 1:
        first = s.find(".")
        s = s[:first+1] + s[first+1:].replace(".", "")

    if s in ("", "-", "+", "."):
        raise ValueError(f"Unparseable amount: {x}")

    val = float(s)
    return -abs(val) if neg else val


def looks_like_income(desc: str) -> bool:
    d = (desc or "").lower()
    return any(re.search(p, d) for p in INCOME_HINTS)

def looks_like_cc_payment(desc: str) -> bool:
    d = (desc or "").lower()
    return any(re.search(p, d) for p in CREDIT_CARD_PAYMENT_HINTS)

def normalize_amount(bank: str, account_type: str, description: str, raw_amount: float) -> float:
    """
    Output convention:
      - Expenses/outflows => positive
      - Income/credits/refunds/payments-in => negative
    """
    b = (bank or "").lower()
    t = (account_type or "").lower()
    d = (description or "").lower()
    amt = float(raw_amount)

    # Deposit account (RBC CHQ / Scotia debit): statement often shows withdrawals as negative
    if t == "deposit_account" or ("rbc" in b and t == ""):
        # deposits/refunds/income should become negative
        if looks_like_income(description):
            return -abs(amt)

        # everything else is an outflow/expense => positive
        return abs(amt)

    # Credit card: purchases are expenses (+), payments/refunds are credits (-)
    if t == "credit_card" or ("scotia" in b and t == ""):
        if looks_like_income(description) or looks_like_cc_payment(description):
            return -abs(amt)
        return abs(amt)

    # fallback heuristic
    if looks_like_income(description) or looks_like_cc_payment(description):
        return -abs(amt)
    return abs(amt)
