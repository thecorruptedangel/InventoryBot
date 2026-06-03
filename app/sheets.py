"""
Data layer over the inventory Google Sheet.

Design for a UI:
  * read the WHOLE grid in one API call and cache it (per-cell reads are too slow)
  * detect formula (computed) columns once, expose them read-only
  * write via USER_ENTERED so the sheet recomputes formulas natively
  * invalidate cache on write; re-read just the edited row to return fresh formula values
"""

from __future__ import annotations

import threading
import time

import gspread

from . import config


class SheetError(Exception):
    pass


class FormulaCellError(SheetError):
    """Attempt to overwrite a computed/formula column."""


# --- connection (lazy singleton) -------------------------------------------
_ws_lock = threading.Lock()
_ws = None


def worksheet():
    global _ws
    with _ws_lock:
        if _ws is not None:
            return _ws
        info = config.service_account_info()
        try:
            if info:
                gc = gspread.service_account_from_dict(info)
            else:
                gc = gspread.service_account(filename=config.service_account_file())
            _ws = gc.open_by_key(config.SPREADSHEET_ID).get_worksheet(config.WORKSHEET_INDEX)
        except Exception as exc:  # noqa: BLE001 - surface a clean error to API layer
            raise SheetError(f"Cannot open sheet: {exc}") from exc
        return _ws


# --- helpers ----------------------------------------------------------------
def col_letter(idx: int) -> str:
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _at(rows: list[list], r: int, c: int):
    """1-based cell access into a ragged grid; '' if absent."""
    if 0 <= r - 1 < len(rows):
        row = rows[r - 1]
        if 0 <= c - 1 < len(row):
            return row[c - 1]
    return ""


def _coerce(value):
    """Pass real numbers/bools through; leave strings/formulas for Sheets to parse."""
    if isinstance(value, (int, float, bool)):
        return value
    v = str(value).strip()
    if v.upper() in ("TRUE", "FALSE"):
        return v.upper() == "TRUE"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return value


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_SHEET_ERRORS = ("#DIV/0!", "#N/A", "#REF!", "#VALUE!", "#NAME?", "#NUM!", "#ERROR!", "#NULL!")


def _clean(value):
    """Turn spreadsheet error values (#DIV/0! etc.) into '' so the UI shows '—'."""
    if isinstance(value, str):
        v = value.strip()
        if any(v.startswith(e) for e in _SHEET_ERRORS):
            return ""
    return "" if value is None else value


# --- grid read + cache ------------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict | None = None
_cache_ts: float = 0.0

_RANGE = f"A1:{config.LAST_COL_LETTER}{config.SCAN_LAST_ROW}"


def _read_grid() -> dict:
    ws = worksheet()
    try:
        values = ws.get(_RANGE, value_render_option="UNFORMATTED_VALUE")
        formulas = ws.get(_RANGE, value_render_option="FORMULA")
    except Exception as exc:  # noqa: BLE001
        raise SheetError(f"Read failed: {exc}") from exc

    header = values[config.HEADER_ROW - 1] if len(values) >= config.HEADER_ROW else []

    # Which columns are computed? -> the MAJORITY of populated data cells are
    # formulas. (A single stray "=1/5" in an otherwise hand-typed column like
    # Qty must NOT make the whole column read-only.)
    computed = [False] * (config.NUM_COLS + 1)
    for c in range(1, config.NUM_COLS + 1):
        formula_n = nonempty_n = 0
        for r in range(config.DATA_START_ROW, config.SCAN_LAST_ROW + 1):
            cell = _at(formulas, r, c)
            if cell == "" or cell is None:
                continue
            nonempty_n += 1
            if isinstance(cell, str) and cell.startswith("="):
                formula_n += 1
        computed[c] = nonempty_n >= 3 and formula_n > nonempty_n / 2

    columns = []
    for c in range(1, config.NUM_COLS + 1):
        letter = col_letter(c)
        if letter in config.EXCLUDED_COLS:
            continue
        label = str(_at([header], 1, c) or "").strip()
        columns.append(
            {
                "index": c,
                "letter": letter,
                "header": label or letter,
                "computed": computed[c],
            }
        )

    items = []
    for r in range(config.DATA_START_ROW, config.SCAN_LAST_ROW + 1):
        name = str(_at(values, r, config.COL_NAME) or "").strip()
        if not name:
            continue
        cells = {}
        for c in range(1, config.NUM_COLS + 1):
            letter = col_letter(c)
            if letter in config.EXCLUDED_COLS:
                continue
            cells[letter] = _clean(_at(values, r, c))
        items.append(
            {
                "row": r,
                "name": name,
                "source": str(_at(values, r, config.COL_SOURCE) or "").strip(),
                "orderable": _to_float(_at(values, r, config.COL_ORDERABLE)),
                "values": cells,
            }
        )

    planning_days = None
    try:
        planning_days = int(_to_float(_at(values, 1, config.COL_ORDERABLE)))  # K1
    except (TypeError, ValueError):
        planning_days = None

    return {
        "columns": columns,
        "items": items,
        "planningDays": planning_days,
        "generatedAt": time.time(),
    }


def get_grid(force: bool = False) -> dict:
    global _cache, _cache_ts
    with _cache_lock:
        fresh = _cache is not None and (time.time() - _cache_ts) < config.GRID_CACHE_TTL
        if fresh and not force:
            return _cache
        data = _read_grid()
        _cache = data
        _cache_ts = time.time()
        return data


def invalidate():
    global _cache
    with _cache_lock:
        _cache = None


def _column_meta() -> dict[str, dict]:
    return {col["letter"]: col for col in get_grid()["columns"]}


# --- write ------------------------------------------------------------------
def set_cell(row: int, col_letter_ref: str, value, force: bool = False) -> dict:
    """
    Write one input cell, then re-read its row so callers get recomputed
    formula values back. Refuses formula columns unless force=True.
    """
    col_letter_ref = col_letter_ref.upper()
    meta = _column_meta()
    info = meta.get(col_letter_ref)
    if info is None:
        raise SheetError(f"Unknown column {col_letter_ref!r}")
    if info["computed"] and not force:
        raise FormulaCellError(
            f"Column {col_letter_ref} ({info['header']}) is computed; edit an input column instead."
        )

    ws = worksheet()
    a1 = f"{col_letter_ref}{row}"

    # Per-cell guard: even within an input column a single cell may hold a
    # formula (e.g. "=1/5"). Don't silently flatten it unless force.
    if not force:
        try:
            existing = ws.get(a1, value_render_option="FORMULA")
            raw = existing[0][0] if existing and existing[0] else ""
        except Exception:  # noqa: BLE001 - non-fatal; fall through to write
            raw = ""
        if isinstance(raw, str) and raw.startswith("="):
            raise FormulaCellError(
                f"{a1} currently holds a formula ({raw}); pass force to overwrite."
            )

    try:
        ws.update(a1, [[_coerce(value)]], value_input_option="USER_ENTERED")
        invalidate()
        fresh = ws.get(
            f"A{row}:{config.LAST_COL_LETTER}{row}",
            value_render_option="UNFORMATTED_VALUE",
        )
    except Exception as exc:  # noqa: BLE001
        raise SheetError(f"Write failed: {exc}") from exc

    row_vals = fresh[0] if fresh else []
    values = {}
    for c in range(1, config.NUM_COLS + 1):
        letter = col_letter(c)
        if letter in config.EXCLUDED_COLS:
            continue
        v = row_vals[c - 1] if c - 1 < len(row_vals) else ""
        values[letter] = _clean(v)
    return {
        "row": row,
        "values": values,
        "orderable": _to_float(row_vals[config.COL_ORDERABLE - 1])
        if len(row_vals) >= config.COL_ORDERABLE
        else 0.0,
    }


def set_planning_days(days: int) -> int:
    """Write the master 'plan for N days' cell (K1). Re-drives every item's order math."""
    days = int(days)
    if days < 1 or days > 365:
        raise SheetError("Planning days must be between 1 and 365")
    ws = worksheet()
    try:
        ws.update(config.PLANNING_CELL, [[days]], value_input_option="USER_ENTERED")
        invalidate()
    except Exception as exc:  # noqa: BLE001
        raise SheetError(f"Write failed: {exc}") from exc
    return days
