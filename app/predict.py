"""
Smart ordering prediction.

For each item with a per-day utilization, work out how long current stock lasts and
whether it survives until the supplier's next ordering day (suppliers closed on
German public holidays). Consumption is assumed every calendar day; no safety buffer.
"""

from __future__ import annotations

import datetime as _dt
import math

from . import planning


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _next_order(start: _dt.date, weekdays: set[int], cal, *, inclusive: bool):
    """Next date (>= or > start) whose weekday is allowed and is not a holiday."""
    if not weekdays:
        return None
    d = start if inclusive else start + _dt.timedelta(days=1)
    for _ in range(370):
        if d.weekday() in weekdays and d not in cal:
            return d
        d += _dt.timedelta(days=1)
    return None


def predict(grid: dict, cfg: dict[str, dict], today: _dt.date | None = None) -> dict:
    today = today or _dt.date.today()
    cal = planning.calendar(range(today.year, today.year + 2))

    # Per-supplier next order date (computed once).
    suppliers: dict[str, dict] = {}
    for name, c in cfg.items():
        wk = set(c.get("days", []))
        nxt = _next_order(today, wk, cal, inclusive=True)
        suppliers[name] = {
            "days": sorted(wk),
            "lead": int(c.get("lead", 0) or 0),
            "nextOrder": nxt.isoformat() if nxt else None,
        }

    items = []
    for it in grid["items"]:
        src = it["source"]
        util = _to_float(it["values"].get("F"))
        qty = _to_float(it["values"].get("D"))
        unit = str(it["values"].get("C") or "").strip()

        rec = {
            "row": it["row"], "name": it["name"], "source": src, "unit": unit,
            "qty": qty, "util": util,
            "hasData": util is not None and util > 0 and qty is not None,
            "scheduled": False, "stockDays": None, "nextOrder": None,
            "daysUntilArrival": None, "atRisk": False, "suggest": 0, "runOutDays": None,
        }

        if not rec["hasData"]:
            items.append(rec)
            continue

        stock_days = qty / util
        rec["stockDays"] = round(stock_days, 1)
        rec["runOutDays"] = math.floor(stock_days)

        c = cfg.get(src)
        wk = set(c.get("days", [])) if c else set()
        lead = int(c.get("lead", 0) or 0) if c else 0
        if not wk:
            items.append(rec)            # no schedule -> can't predict order timing
            continue

        rec["scheduled"] = True
        next_order = _next_order(today, wk, cal, inclusive=True)
        following = _next_order(next_order, wk, cal, inclusive=False) if next_order else None
        if not next_order or not following:
            items.append(rec)
            continue

        arrival = next_order + _dt.timedelta(days=lead)
        following_arrival = following + _dt.timedelta(days=lead)
        days_to_cover = (arrival - today).days
        gap_days = (following_arrival - arrival).days

        rec["nextOrder"] = next_order.isoformat()
        rec["daysUntilArrival"] = days_to_cover
        rec["atRisk"] = stock_days < days_to_cover     # no buffer

        projected = max(0.0, qty - util * max(0, days_to_cover))
        need = math.ceil(util * gap_days - projected)
        rec["suggest"] = max(0, need)
        items.append(rec)

    at_risk = sum(1 for r in items if r["atRisk"])
    return {
        "today": today.isoformat(),
        "weekday": today.weekday(),
        "items": items,
        "suppliers": suppliers,
        "atRiskCount": at_risk,
    }
