# app/sheets.py
from __future__ import annotations

import re
from typing import List, Tuple, Optional, Dict

import google.auth
from googleapiclient.discovery import build

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def get_sheets_service():
    creds, _ = google.auth.default(scopes=SHEETS_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheet_title_exists(service, spreadsheet_id: str, title: str) -> Tuple[bool, Optional[int]]:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        props = s.get("properties", {})
        if props.get("title") == title:
            return True, props.get("sheetId")
    return False, None


def ensure_sheet(service, spreadsheet_id: str, title: str) -> int:
    exists, sheet_id = _sheet_title_exists(service, spreadsheet_id, title)
    if exists and sheet_id is not None:
        return sheet_id

    req = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=req
    ).execute()
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def get_last_row(service, spreadsheet_id: str, sheet_title: str) -> int:
    """
    Returns last non-empty row index (1-based). If empty, returns 0.
    """
    rng = f"'{sheet_title}'!A:A"
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=rng
    ).execute().get("values", [])
    return len(values)


def find_section_anchor(service, spreadsheet_id: str, sheet_title: str, section_header: str) -> Optional[int]:
    """
    Search column A for a section header like: "=== RBC CHQ ==="
    Return the row number (1-based) where it exists, else None.
    """
    rng = f"'{sheet_title}'!A:A"
    col = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=rng
    ).execute().get("values", [])

    target = section_header.strip()
    for i, row in enumerate(col, start=1):
        if row and row[0].strip() == target:
            return i
    return None


def append_values(service, spreadsheet_id: str, sheet_title: str, start_cell: str, values: List[List[str]]):
    rng = f"'{sheet_title}'!{start_cell}"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rng,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def _a1(row: int, col: int) -> str:
    # col: 1=A, 2=B, ...
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def set_background(
    service,
    spreadsheet_id: str,
    sheet_id: int,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    rgb: Dict[str, float],
):
    """
    Applies background color to a grid range. Colors use 0..1 floats.
    """
    req = {
        "requests": [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row - 1,
                        "endRowIndex": end_row - 1,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col - 1,
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": rgb}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        ]
    }
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=req).execute()


def write_section_summary(service, spreadsheet_id: str, sheet_title: str, anchor_row: int, lines: list[str]):
    """
    Writes summary lines into columns H and I, starting at anchor_row.
    Each line becomes:
      - If it contains ":", split into two columns (H=label, I=value)
      - Else put whole line in column H
    """
    values: list[list[str]] = []
    for line in lines[:6]:  # keep compact
        if ":" in line:
            left, right = line.split(":", 1)
            values.append([left.strip(), right.strip()])
        else:
            values.append([line.strip(), ""])

    start_cell = _a1(anchor_row, 8)  # H column
    append_values(service, spreadsheet_id, sheet_title, start_cell, values)


def upsert_card_section(
    service,
    spreadsheet_id: str,
    month_title: str,
    sheet_id: int,
    card_label: str,
    header_color: Dict[str, float],
    rows: List[List[str]],
) -> int:
    """
    Each card/account gets a section in the month tab:

    Row N:   === <card_label> ===   (colored)
    Row N+1: Date | Category | Merchant/Description | Amount | Bank | IsBookkeeping
    Row N+2.. : data

    If section exists, append below existing data within that section (not mixing cards).
    If not, create new section at bottom.

    Returns the section header row number (anchor row).
    """
    section_header = f"XXXXXX {card_label} XXXXXX"
    anchor = find_section_anchor(service, spreadsheet_id, month_title, section_header)

    if anchor is None:
        # create at bottom; keep some space at top for your own notes if you want
        last = max(get_last_row(service, spreadsheet_id, month_title), 1)
        start_row = last + 2  # leave a blank row

        table_header = [["Date", "Category", "Merchant/Description", "Amount", "Bank", "IsBookkeeping"]]
        payload = [[section_header]] + table_header + rows

        append_values(service, spreadsheet_id, month_title, _a1(start_row, 1), payload)

        # color the section header row across A:F
        set_background(
            service, spreadsheet_id, sheet_id,
            start_row, start_row + 1, 1, 7,
            header_color
        )

        return start_row

    # section exists: append below current data (scan down column A from anchor)
    colA = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{month_title}'!A{anchor}:A"
    ).execute().get("values", [])

    # anchor row is section header, anchor+1 is table header
    # data starts at anchor+2
    append_at = anchor + 2
    for i, r in enumerate(colA[2:], start=anchor + 2):
        if not r or not r[0].strip():
            append_at = i
            break
        append_at = i + 1

    append_values(service, spreadsheet_id, month_title, _a1(append_at, 1), rows)
    return anchor
