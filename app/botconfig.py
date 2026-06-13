"""
Per-supplier ordering schedule, stored in a dedicated 'BotConfig' worksheet so it
persists and is shared across the team (Render's free disk is ephemeral).

Schema (row 1 = header):
    supplier | order_days | lead_days
    "Metro"  | "0,3"      | "1"
order_days = comma-separated weekday numbers, Monday=0 .. Sunday=6.
"""

from __future__ import annotations

import threading
import time

import gspread

from . import sheets

CONFIG_SHEET = "BotConfig"
_HEADER = ["supplier", "order_days(Mon=0..Sun=6)", "lead_days"]

_lock = threading.Lock()
_cache: dict | None = None
_cache_ts = 0.0
_TTL = 30.0


def _ws():
    ss = sheets.spreadsheet()
    try:
        return ss.worksheet(CONFIG_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=CONFIG_SHEET, rows=100, cols=4)
        ws.update("A1", [_HEADER], value_input_option="RAW")
        return ws


def _parse_days(raw: str) -> list[int]:
    out = []
    for tok in str(raw).split(","):
        tok = tok.strip()
        if tok.isdigit() and 0 <= int(tok) <= 6:
            out.append(int(tok))
    return sorted(set(out))


def get_config(force: bool = False) -> dict[str, dict]:
    """Return {supplier: {'days': [int], 'lead': int}}."""
    global _cache, _cache_ts
    with _lock:
        if _cache is not None and not force and (time.time() - _cache_ts) < _TTL:
            return _cache
    try:
        rows = _ws().get_all_values()
    except Exception as exc:  # noqa: BLE001
        raise sheets.SheetError(f"Config read failed: {exc}") from exc
    cfg: dict[str, dict] = {}
    for r in rows[1:]:
        name = (r[0] if r else "").strip()
        if not name:
            continue
        days = _parse_days(r[1]) if len(r) > 1 else []
        lead_raw = (r[2] if len(r) > 2 else "0").strip()
        lead = int(lead_raw) if lead_raw.lstrip("-").isdigit() else 0
        cfg[name] = {"days": days, "lead": max(0, lead)}
    with _lock:
        _cache = cfg
        _cache_ts = time.time()
    return cfg


def set_config(mapping: dict[str, dict]) -> dict[str, dict]:
    """Overwrite the whole config tab from {supplier:{days:[int],lead:int}}."""
    clean: dict[str, dict] = {}
    rows = [_HEADER]
    for name, c in mapping.items():
        name = str(name).strip()
        if not name:
            continue
        days = sorted({int(d) for d in c.get("days", []) if 0 <= int(d) <= 6})
        lead = max(0, int(c.get("lead", 0) or 0))
        clean[name] = {"days": days, "lead": lead}
        rows.append([name, ",".join(str(d) for d in days), str(lead)])
    try:
        ws = _ws()
        ws.clear()
        ws.update("A1", rows, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        raise sheets.SheetError(f"Config write failed: {exc}") from exc
    with _lock:
        global _cache, _cache_ts
        _cache = clean
        _cache_ts = time.time()
    return clean
