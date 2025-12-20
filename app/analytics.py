# app/analytics.py
import re
import pandas as pd

def _clean_merchant(desc: str) -> str:
    """
    Normalize merchant strings a bit so top-merchants isn't split by prefixes.
    """
    d = (desc or "").strip()

    # Remove common prefixes from Scotia debit style
    d = re.sub(r"^(pos\s*purchase|pos\s*return|withdrawal|deposit)\s+", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^(fpos\s+)", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^(misc\s*payment|utility\s*bill\s*pmt|telephone\s*bill\s*pmt)\s+", "", d, flags=re.IGNORECASE)

    # Collapse extra spaces
    d = re.sub(r"\s+", " ", d).strip()
    return d

def summarize(df: pd.DataFrame) -> list[str]:
    """
    Returns 4 short lines:
      - Total spend (excluding bookkeeping + non-positive)
      - Top category by spend
      - Eating Out spend
      - Top 3 merchants by spend
    """
    # Ensure types
    df = df.copy()
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0.0)
    df["IsBookkeeping"] = df["IsBookkeeping"].astype(str).str.lower().isin(["true", "1", "yes"])

    # Spend definition
    spend = df[(df["Amount"] > 0) & (~df["IsBookkeeping"])].copy()

    total_spend = float(spend["Amount"].sum())

    if len(spend) == 0:
        return [
            "Total spend: $0.00",
            "Top category: N/A",
            "Eating Out: $0.00",
            "Top merchants: N/A",
        ]

    # Top category
    cat_spend = spend.groupby("Category")["Amount"].sum().sort_values(ascending=False)
    top_cat = cat_spend.index[0]
    top_cat_amt = float(cat_spend.iloc[0])

    # Eating out
    eating_out = float(spend[spend["Category"].str.lower() == "eating out"]["Amount"].sum())

    # Top merchants
    spend.loc[:,"MerchantClean"] = spend["Merchant/Description"].apply(_clean_merchant)
    merch_spend = spend.groupby("MerchantClean")["Amount"].sum().sort_values(ascending=False).head(3)
    top_merch_str = ", ".join([f"{m} (${a:.2f})" for m, a in merch_spend.items()]) or "N/A"

    return [
        f"Total spend (excl. transfers/CC payments): ${total_spend:.2f}",
        f"Top category: {top_cat} (${top_cat_amt:.2f})",
        f"Eating Out: ${eating_out:.2f}",
        f"Top merchants: {top_merch_str}",
    ]
