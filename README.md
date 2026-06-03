# InventoryBot

A Telegram **Mini App** + CLI to read/update the inventory Google Sheet by
**item name + column**.

Sheet ID is a secret — supplied via `SPREADSHEET_ID` env var (or a git-ignored
`sheet_id.txt` for local dev). Not committed.

## Two surfaces
- **Telegram Mini App** (`app/` + `static/`) — column-first list, search, supplier
  filter, inline edit, and a per-supplier "To Order" list. See [DEPLOY.md](DEPLOY.md).
- **CLI** (`inventory.py`) — scriptable get/set for one cell. See "CLI" below.

## Sheet layout

- Header labels on **row 10**, item data from **row 12** down.
- Item name = **column A** (matched case-insensitive, trimmed).
- Input columns: A name, B qty/type, C packing, **D Qty**, E source, **F per-piece util**, M checkbox.
- Formula columns (computed by the sheet, do not hand-edit): **G** Days, **H** Needed, **I** Fulfilled days, **J** Actual needed, **K** Orderable, **L** order text.
- Column "computed" status is detected dynamically (majority of cells are formulas),
  with a per-cell guard so a stray `=1/5` in an input column is also protected.

## Architecture

```
Telegram ──webhook──► FastAPI (app/main.py) ──► Google Sheets (service account)
  user ──MiniApp────► /app (static/) ──fetch──► /api/grid, /api/cell
```
- Whole grid read in one call + 30 s cache (`app/sheets.py`); writes via USER_ENTERED
  so formulas recompute natively; cache invalidated on write.
- Every API request validates Telegram `initData` HMAC and checks the whitelist
  (`app/auth.py`, `app/config.py:WHITELISTED_IDS`).

## CLI

Editing an input cell (e.g. Qty/D) makes the sheet auto-recalculate H–L — the script does nothing extra. Writing a formula column is blocked unless `--force`.

## Setup

1. Install deps:
   ```
   pip install -r requirements.txt
   ```
2. Google Cloud: create a project, **enable the Google Sheets API**, create a **service account**, add a **JSON key**, download it.
3. Save the JSON as `service_account.json` here (or set `INVENTORY_SA_FILE` to its path).
4. Open the JSON, copy `client_email`, and **share the sheet with that email as Editor**.

> Note: "anyone with link can edit" lets humans edit in a browser, but the Sheets **API still requires credentials** — hence the service account.

## Usage

```
python inventory.py list-columns                 # show columns + headers
python inventory.py list-items                   # show all item names

python inventory.py get --item "Cream Cheese" --column Qty
python inventory.py get --item "Rice" --column K          # by letter
python inventory.py get --item "Rice" --column "Orderable"  # by header

python inventory.py set --item "Cream Cheese" --column Qty --value 5
python inventory.py set --item "Cream Cheese" --column D --value 5
python inventory.py set --item "Cream Cheese" --column M --value TRUE
```

`--column` accepts a letter (`D`), a 1-based index (`4`), or a header substring (`qty`).
Values are coerced: numbers → numbers, `TRUE/FALSE` → checkbox bool, `=A1+1` → formula, else text.
