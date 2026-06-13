"""Planning-horizon helpers: German public holidays + effective-days math."""

from __future__ import annotations

import datetime as _dt

import holidays as _holidays

from . import config


def _calendar(years):
    return _holidays.country_holidays("DE", subdiv=config.GERMAN_STATE, years=list(years))


def calendar(years):
    """Public: a DE holidays object for membership tests (date in cal)."""
    return _calendar(years)


def holidays_in_range(d0: _dt.date, d1: _dt.date) -> list[tuple[_dt.date, str]]:
    """German public holidays between d0 and d1 inclusive, as (date, name)."""
    cal = _calendar(range(d0.year, d1.year + 1))
    out = []
    cur = d0
    one = _dt.timedelta(days=1)
    while cur <= d1:
        name = cal.get(cur)
        if name:
            out.append((cur, name))
        cur += one
    return out


def effective_days(start: str, end: str):
    """
    Returns (d0, d1, total_days, holidays) for an inclusive date range.
    total_days counts both endpoints; holidays is the (date, name) list to exclude.
    """
    d0 = _dt.date.fromisoformat(start)
    d1 = _dt.date.fromisoformat(end)
    if d1 < d0:
        d0, d1 = d1, d0
    total = (d1 - d0).days + 1
    hol = holidays_in_range(d0, d1)
    return d0, d1, total, hol
