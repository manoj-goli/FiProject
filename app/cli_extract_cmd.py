# app/cli_extract_cmd.py
#
# Run statement extraction + normalization + categorization using command-line args
# and optionally write results into Google Sheets (monthly tabs + card/account sections).
#
# Examples:
#   python .\app\cli_extract_cmd.py --bank RBC --type deposit_account --gcs "gs://statementsbucket/incoming/RBC_CHQ_statement.pdf"
#   python .\app\cli_extract_cmd.py --bank RBC --type deposit_account --gcs "gs://statementsbucket/incoming/RBC_CHQ_statement.pdf" --sheet --label "RBC CHQ"
#   python .\app\cli_extract_cmd.py --bank Scotiabank --type credit_card --local ".\Nov_2025_Scotia_Amex.pdf" --sheet --label "Scotia Amex"
#
# Required .env vars:
#   GCP_PROJECT=...
# Optional .env vars:
#   GCP_LOCATION=us-central1
#   GEMINI_MODEL=gemini-2.5-flash
#   SHEET_ID=<spreadsheet id>

import argparse
import json
import os
import re
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai.types import Part

from normalize import parse_amount, normalize_amount
from categorize import categorize, is_bookkeeping
from analytics import summarize

from sheets import (
    get_sheets_service,
    ensure_sheet,
    upsert_card_section,
    write_section_summary,
)

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
    rows = []
    for t in txns:
        date = t.get("date")
        desc = (t.get("description") or "").strip()

        raw_amt = parse_amount(t.get("amount"))
        norm_amt = normalize_amount(bank, account_type, desc, raw_amt)
        cat = categorize(desc, norm_amt)

        rows.append({
            "Date": date,
            "Category": cat,
            "Merchant/Description": desc,
            "Amount": norm_amt,
            "Bank": bank,
            "IsBookkeeping": is_bookkeeping(cat),
        })
    return rows


def build_pdf_part(local_path: str | None, gcs_uri: str | None) -> tuple[Part, str]:
    """
    Returns (Part, source_label)
    """
    if gcs_uri:
        return Part.from_uri(file_uri=gcs_uri, mime_type="application/pdf"), gcs_uri

    if not local_path:
        raise ValueError("Provide either --local PATH or --gcs gs://...")

    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Local PDF not found: {local_path}")

    with open(local_path, "rb") as f:
        pdf_bytes = f.read()

    return Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), local_path


def infer_month(df: pd.DataFrame) -> str:
    """
    Infers month tab name like YYYY-MM from the most common transaction month.
    """
    if "Date" not in df.columns or df.empty:
        return datetime.now().strftime("%Y-%m")

    s = df["Date"].dropna().astype(str).str.slice(0, 7)
    s = s[s.str.match(r"^\d{4}-\d{2}$", na=False)]
    if s.empty:
        return datetime.now().strftime("%Y-%m")
    return s.mode().iloc[0]


def main():
    parser = argparse.ArgumentParser(
        description="Extract bank statement transactions -> normalized categorized CSV (and optional Google Sheets write)"
    )
    parser.add_argument("--bank", required=True, help='Bank name, e.g. "RBC" or "Scotiabank"')
    parser.add_argument("--type", required=True, choices=["credit_card", "deposit_account"],
                        help="Account type: credit_card or deposit_account")

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--local", help="Local PDF path, e.g. .\\Nov_2025_RBC_CHQ.pdf")
    src.add_argument("--gcs", help="GCS URI, e.g. gs://bucket/incoming/file.pdf")

    parser.add_argument("--out", default=None, help="Optional output CSV filename")

    # Google Sheets options
    parser.add_argument("--sheet", action="store_true", help="Write results into Google Sheets")
    parser.add_argument("--sheet-id", default=os.getenv("SHEET_ID"),
                        help="Spreadsheet ID (or set SHEET_ID in .env)")
    parser.add_argument("--month", default=None, help="Override month tab name like 2025-11")
    parser.add_argument("--label", default=None,
                        help="Section label (e.g., 'RBC CHQ', 'Scotia Debit', 'Scotia Amex')")

    args = parser.parse_args()

    project_id = require_env("GCP_PROJECT")
    location = os.getenv("GCP_LOCATION", "us-central1")

    client = genai.Client(vertexai=True, project=project_id, location=location)

    pdf_part, source_label = build_pdf_part(args.local, args.gcs)
    prompt = EXTRACT_PROMPT.replace("<bank name>", args.bank)

    resp = client.models.generate_content(
        model=MODEL,
        contents=[pdf_part, prompt],
    )

    data = safe_json_load(resp.text)

    bank = data.get("bank") or args.bank
    account_type = (data.get("account_type") or args.type).strip()
    txns = data.get("transactions", [])

    df = pd.DataFrame(to_csv_rows(bank, account_type, txns))

    # Write CSV
    out = args.out
    if not out:
        safe_bank = re.sub(r"[^A-Za-z0-9]+", "_", bank).strip("_")
        out = f"transactions_{safe_bank}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df.to_csv(out, index=False)

    # Optional: write to Google Sheets (per-section analytics in H–I)
    if args.sheet:
        if not args.sheet_id:
            raise RuntimeError("Missing spreadsheet id. Provide --sheet-id or set SHEET_ID in .env")

        month = args.month or infer_month(df)
        label = args.label or f"{bank} ({account_type})"

        # Simple color palette (0..1 floats)
        COLORS = {
            "RBC": {"red": 0.86, "green": 0.93, "blue": 1.0},
            "Scotiabank": {"red": 0.98, "green": 0.92, "blue": 0.86},
            "Default": {"red": 0.92, "green": 0.92, "blue": 0.92},
        }
        header_color = COLORS.get(bank, COLORS["Default"])

        service = get_sheets_service()
        sheet_gid = ensure_sheet(service, args.sheet_id, month)

        # Write/append the section for this card/account
        rows = df[["Date", "Category", "Merchant/Description", "Amount", "Bank", "IsBookkeeping"]].values.tolist()
        anchor_row = upsert_card_section(service, args.sheet_id, month, sheet_gid, label, header_color, rows)

        # Write 3–4 analytics lines next to THIS section (H–I)
        write_section_summary(service, args.sheet_id, month, anchor_row, summarize(df))

        print(f"Wrote to Google Sheet tab '{month}' section '{label}' (summary in H–I)")

    # Console output
    print(f"Source: {source_label}")
    print(f"Bank: {bank} | account_type: {account_type}")
    print(f"Wrote {len(df)} rows -> {out}")
    print("\n".join(summarize(df)))


if __name__ == "__main__":
    main()