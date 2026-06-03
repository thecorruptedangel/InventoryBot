"""
InventoryBot — read/update a single inventory Google Sheet by item name + column.

Layout assumptions (do not add/remove rows):
    HEADER_ROW = 10      header labels
    DATA_START = 12      first item row
    Item name lives in column A (case-insensitive, trimmed match).
    Formula columns (H..L: Needed/Fulfilled/Actual/Orderable/Order-text) are
    computed by the sheet. Writing an INPUT cell (e.g. Qty/D, Util/F, Days/G)
    makes the sheet auto-recalculate those formulas — the script never has to.
    Writing a FORMULA column would replace its formula with a static value, so
    that is blocked unless --force is passed.

Auth: Google service account. Create one in Google Cloud, enable the Google
Sheets API, download the JSON key, then SHARE the sheet with the service
account's client_email (Editor). Point SERVICE_ACCOUNT_FILE / env at the JSON.

Requires:  pip install gspread google-auth
"""

from __future__ import annotations

import argparse
import os
import sys

import gspread

# --- config -----------------------------------------------------------------
def _read_sheet_id() -> str:
    """Secret. From env SPREADSHEET_ID, else a git-ignored sheet_id.txt beside this file."""
    v = os.environ.get("SPREADSHEET_ID")
    if v:
        return v.strip()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sheet_id.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


SPREADSHEET_ID = _read_sheet_id()
WORKSHEET_INDEX = 0          # first tab
HEADER_ROW = 10
DATA_START_ROW = 12
NAME_COL = 1                 # column A holds the item name

def _discover_sa_file() -> str:
    """Env var wins; else service_account.json; else any *.json here that is a SA key."""
    env = os.environ.get("INVENTORY_SA_FILE")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "service_account.json")
    if os.path.exists(default):
        return default
    import glob
    import json
    for path in glob.glob(os.path.join(here, "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh).get("type") == "service_account":
                    return path
        except (ValueError, OSError):
            continue
    return default  # report this missing path


SERVICE_ACCOUNT_FILE = _discover_sa_file()


# --- connection -------------------------------------------------------------
def open_sheet():
    if not SPREADSHEET_ID:
        sys.exit("No sheet id. Set SPREADSHEET_ID env var or create sheet_id.txt here.")
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        sys.exit(
            f"Service account file not found: {SERVICE_ACCOUNT_FILE}\n"
            "Set INVENTORY_SA_FILE env var or drop service_account.json here, "
            "and share the sheet with its client_email (Editor)."
        )
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    return gc.open_by_key(SPREADSHEET_ID).get_worksheet(WORKSHEET_INDEX)


# --- column / row resolution -----------------------------------------------
def column_letter(idx: int) -> str:
    """1 -> A, 2 -> B, ..."""
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def header_labels(ws) -> list[str]:
    """Header label per column index (1-based list position 0=col A)."""
    return ws.row_values(HEADER_ROW)


def resolve_column(ws, ref: str) -> int:
    """
    Accept a column reference as:
      - a letter:        'D', 'd', 'AA'
      - a 1-based index: '4'
      - a header label substring (case-insensitive): 'qty', 'orderable'
    Returns the 1-based column index.
    """
    ref = ref.strip()
    if not ref:
        sys.exit("Empty column reference.")

    # letter form: A..Z / AA.. (max 2 chars, else it's a header word like "Qty")
    if ref.isalpha() and len(ref) <= 2:
        idx = 0
        for ch in ref.upper():
            idx = idx * 26 + (ord(ch) - 64)
        return idx

    # numeric index
    if ref.isdigit():
        return int(ref)

    # header substring
    labels = header_labels(ws)
    low = ref.lower()
    matches = [i + 1 for i, lab in enumerate(labels) if low in lab.strip().lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"No column header matches {ref!r}. Use --list-columns to see options.")
    cols = ", ".join(f"{column_letter(m)}({labels[m-1].strip()})" for m in matches)
    sys.exit(f"Ambiguous column {ref!r}; matches: {cols}. Use the letter instead.")


def find_item_row(ws, item: str) -> int:
    """1-based sheet row for an item name (trimmed, case-insensitive). Exit if missing."""
    names = ws.col_values(NAME_COL)            # whole column A
    target = item.strip().lower()
    for i, name in enumerate(names, start=1):
        if i < DATA_START_ROW:
            continue
        if name.strip().lower() == target:
            return i
    sys.exit(f"Item {item!r} not found. Use --list-items to see names.")


def is_formula_cell(ws, row: int, col: int) -> bool:
    raw = ws.cell(row, col, value_render_option="FORMULA").value
    return isinstance(raw, str) and raw.startswith("=")


# --- operations -------------------------------------------------------------
def cmd_list_columns(ws):
    for i, lab in enumerate(header_labels(ws), start=1):
        print(f"{column_letter(i):>3}  {i:>2}  {lab.strip()!r}")


def cmd_list_items(ws):
    names = ws.col_values(NAME_COL)
    for i, name in enumerate(names, start=1):
        if i >= DATA_START_ROW and name.strip():
            print(name.strip())


def cmd_get(ws, item: str, column: str):
    row = find_item_row(ws, item)
    col = resolve_column(ws, column)
    # UNFORMATTED_VALUE returns the recalculated result for formula cells.
    val = ws.cell(row, col, value_render_option="UNFORMATTED_VALUE").value
    print(val if val is not None else "")


def cmd_set(ws, item: str, column: str, value: str, force: bool):
    row = find_item_row(ws, item)
    col = resolve_column(ws, column)
    cell = f"{column_letter(col)}{row}"

    if is_formula_cell(ws, row, col) and not force:
        sys.exit(
            f"{cell} contains a FORMULA (computed column). Overwriting replaces "
            f"the formula with a static value. Re-run with --force to override.\n"
            f"Tip: edit an input column instead (e.g. Qty/D) and the sheet "
            f"recalculates this automatically."
        )

    # USER_ENTERED: '5' -> number, '=A1+1' -> formula, 'TRUE' -> bool, like typing in UI.
    ws.update_acell(cell, _coerce(value))
    print(f"Set {cell} ({item!r} / col {column_letter(col)}) = {value}")


def _coerce(value: str):
    """Let Sheets interpret, but pass real numbers/bools through cleanly."""
    v = value.strip()
    if v.upper() in ("TRUE", "FALSE"):
        return v.upper() == "TRUE"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return value  # string or formula


# --- cli --------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Read/update the inventory Google Sheet.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-columns", help="Print column letter/index/header.")
    sub.add_parser("list-items", help="Print all item names.")

    g = sub.add_parser("get", help="Read one cell by item name + column.")
    g.add_argument("--item", required=True)
    g.add_argument("--column", required=True, help="letter (D), index (4), or header (Qty)")

    s = sub.add_parser("set", help="Write one cell by item name + column.")
    s.add_argument("--item", required=True)
    s.add_argument("--column", required=True, help="letter (D), index (4), or header (Qty)")
    s.add_argument("--value", required=True)
    s.add_argument("--force", action="store_true", help="allow overwriting a formula cell")

    args = p.parse_args()
    ws = open_sheet()

    if args.cmd == "list-columns":
        cmd_list_columns(ws)
    elif args.cmd == "list-items":
        cmd_list_items(ws)
    elif args.cmd == "get":
        cmd_get(ws, args.item, args.column)
    elif args.cmd == "set":
        cmd_set(ws, args.item, args.column, args.value, args.force)


if __name__ == "__main__":
    main()
