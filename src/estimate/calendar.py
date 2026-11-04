"""NYSE trading calendar for 2025-2026 (full-day closures only; early closes are ignored)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HOLIDAYS = {dt.date.fromisoformat(d) for d in (
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
)}


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def prev_trading_day(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def next_trading_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def close_ts(d: dt.date) -> int:
    """Unix timestamp of the 16:00 ET close on date d."""
    return int(dt.datetime.combine(d, dt.time(16, 0), ET).timestamp())


def entry_and_reaction_days(report_date: dt.date, timing: str) -> tuple[dt.date, dt.date]:
    """Last close before the release, and the first session that trades on the news.

    BMO (before market open): entry = previous session's close, reaction = report day.
    AMC (after market close): entry = report day's close, reaction = next session.
    """
    if timing == "BMO":
        react = report_date if is_trading_day(report_date) else next_trading_day(report_date)
        return prev_trading_day(react), react
    entry = report_date if is_trading_day(report_date) else prev_trading_day(report_date)
    return entry, next_trading_day(entry)
