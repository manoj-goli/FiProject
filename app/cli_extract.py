# app/cli_extract.py
import json
import os
import re
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai.types import Part

from normalize import parse_amount, normalize_amount
from categorize import categorize

from categorize import categorize, is_bookkeeping

from analytics import summarize



# Load .env from repo root (or current working directory)
load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

EXTRACT_PROMPT = """
You are extracting *transaction line items* from a Canadian bank statement PDF.

Return ONLY valid JSON (no markdown, no commentary) in this exact shape:

{
  "bank": "<bank name>",
  "account_type": "credit_card" | "deposit_account",
  "transactions": [
    {"date":"YYYY-MM-DD","description":"...","amount": -12.34}
  ]
}

Rules:
- Extract ONLY actual transactions (exclude balances, payments due, interest summaries, totals, rewards, messages).
- Keep the sign exactly as the statement implies:
  - For deposit accounts: withdrawals are usually negative, deposits positive (as shown).
  - For credit cards: purchases positive, payments/credits negative (as shown).
- Normalize date to YYYY-MM-DD. If year is missing, infer from statement context.
- Keep description short but faithful (1 line).
"""

def safe_json_load(text: str) -> dict:
    """
    The model *should* return strict JSON, but this defensively extracts the
    first JSON object if extra text appears.
    """
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("No JSON object found in model output.")
    return json.loads(m.group(0))

def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

def to_csv_rows(bank: str, account_type: str, txns: list) -> list:
    """
    Output columns:
    Date, Category, Merchant/Description, Amount, Bank

    Normalization convention:
    - Expenses/outflows => positive
    - Income/credits/refunds/payments-in => negative
    """
    rows = []
    for t in txns:
        date = t.get("date")
        desc = (t.get("description") or "").strip()

        # Parse raw amount coming from the model (may be string/float)
        raw_amt = parse_amount(t.get("amount"))

        # Normalize sign based on bank + account_type + description
        norm_amt = normalize_amount(bank, account_type, desc, raw_amt)

        # Categorize based on description + normalized amount
        cat = categorize(desc, norm_amt)


        rows.append({
            "Date": date,
            "Category": cat,
            "Merchant/Description": desc,
            "Amount": norm_amt,
            "Bank": bank,
            "IsBookkeeping": is_bookkeeping(cat)
        })
    return rows

def main():
    # Required env vars
    project_id = require_env("GCP_PROJECT")
    location = os.getenv("GCP_LOCATION", "us-central1")
    bucket = require_env("GCS_BUCKET")

    # Optional env vars (let you run different statements without editing code)
    bank_name = os.getenv("BANK_NAME", "Scotiabank")          # e.g., "RBC", "Scotiabank"
    pdf_object = os.getenv("PDF_OBJECT", "incoming/nov_2025_scotia.pdf")
    fallback_account_type = os.getenv("ACCOUNT_TYPE", "")     # "credit_card" or "deposit_account"

    # Build GCS URI (bucket env var should be just the bucket name, no gs://)
    pdf_gcs_uri = f"gs://{bucket}/{pdf_object}"

    # Vertex AI client (uses Application Default Credentials locally)
    client = genai.Client(vertexai=True, project=project_id, location=location)

    pdf_part = Part.from_uri(file_uri=pdf_gcs_uri, mime_type="application/pdf")
    prompt = EXTRACT_PROMPT.replace("<bank name>", bank_name)

    resp = client.models.generate_content(
        model=MODEL,
        contents=[pdf_part, prompt],
    )

    data = safe_json_load(resp.text)

    bank = data.get("bank") or bank_name
    account_type = (data.get("account_type") or fallback_account_type or "").strip()
    txns = data.get("transactions", [])

    df = pd.DataFrame(to_csv_rows(bank, account_type, txns))
    out = f"transactions_{bank}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df.to_csv(out, index=False)

    print(f"PDF: {pdf_gcs_uri}")
    print(f"Bank: {bank} | account_type: {account_type or 'unknown'}")
    print(f"Wrote {len(df)} rows -> {out}")

if __name__ == "__main__":
    main()
