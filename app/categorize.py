# app/categorize.py
import re

# Helper: compile patterns once
def _m(p: str, s: str) -> bool:
    return re.search(p, s, flags=re.IGNORECASE) is not None

# Category rules are ordered: first match wins.
# Keep patterns broad but not too broad.
RULES = [
    # --- Non-spend / bookkeeping first ---
    ("Credit Card Payment", [
        r"\bcrd\.?\s*card\b", r"\bcredit\s*card\b", r"\bbill\s*payment\b",
        r"\bmb-?credit\s*card/loc\s*pay\b", r"\bloc\s*pay\b"
    ]),
    ("Transfers", [
        r"\be-?transfer\b", r"\binterac\b", r"\bcustomer\s*transfer\b",
        r"\bbr\s*to\s*br\b"
    ]),
    ("Income", [
        r"\bpayroll\b", r"\bsalary\b"
    ]),
    ("Refund", [
        r"\bpos\s*return\b", r"\brefund\b", r"\breversal\b"
    ]),
    ("Fees", [
        r"\bservice\s*charge\b", r"\bmonthly\s*fees\b", r"\bmonthly\s*fee\b", r"\bfee\s*rebate\b"
    ]),

    # --- Bills / recurring ---
    ("Utilities", [
        r"\bhydro\s*ottawa\b", r"\benbridge\b", r"\benercare\b",
        r"\breliance\b", r"\bwater\b", r"\bgas\b"
    ]),
    ("Phone/Internet", [
        r"\bfido\b", r"\brogers\b", r"\bbell\b", r"\btelus\b", r"\bvirgin\b", r"\bkoodo\b"
    ]),
    ("Insurance", [
        r"\bmanulife\b", r"\bsun\s*life\b", r"\binsurance\b"
    ]),
    ("Investments", [
        r"\bws\s*investments\b", r"\binvestment\b"
    ]),

    # --- Shopping & groceries ---
    ("Groceries", [
        r"\bt&?t\b", r"\bsupermarket\b", r"\bindian\s*supermarket\b",
        r"\bthe\s*indian\s*supermarket\b", r"\bshoppers\s*drug\s*mart\b"
    ]),
    ("Wholesale", [
        r"\bcostco\b"
    ]),
    ("Subscriptions", [
        r"\bamazon\s*prime\b", r"\bprime\b", r"\bchatgpt\b", r"\bopenai\b"
    ]),
    ("Retail", [
        r"\bwalmart\b", r"\bdollarama\b", r"\bamazon\b"
    ]),

    # --- Eating out / fast food ---
    ("Eating Out", [
        r"\bkfc\b", r"\btaco\s*bell\b", r"\btim\s*hortons\b", r"\bstarbucks\b",
        r"\bbiryani\b"
    ]),
]

def categorize(description: str, amount: float) -> str:
    d = (description or "").strip()

    # Normalize some bank prefixes so rules match easier (optional but helpful)
    # e.g., "Pos Purchase Fpos ..." -> remove leading transaction-type markers
    d2 = re.sub(r"^(pos\s*purchase|pos\s*return|withdrawal|deposit|misc\s*payment|utility\s*bill\s*pmt|telephone\s*bill\s*pmt)\s+", "", d, flags=re.IGNORECASE)

    # Always classify CC payments and transfers regardless of sign
    for cat, patterns in RULES:
        for p in patterns:
            if _m(p, d2) or _m(p, d):
                # If amount is negative and looks like income, keep Income
                # (but Credit Card Payment / Transfers should still win because they’re “bookkeeping”)
                return cat

    # If nothing matched, use sign-based fallback
    if amount < 0:
        return "Credit/Income"
    return "Other"


BOOKKEEPING_CATEGORIES = {
    "Transfers",
    "Credit Card Payment",
}

def is_bookkeeping(category: str) -> bool:
    return category in BOOKKEEPING_CATEGORIES
