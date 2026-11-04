"""Turn raw Polymarket earnings events into one row per settled EPS-beat market."""
from __future__ import annotations

import datetime as dt
import json
import re

import pandas as pd

from estimate.calendar import ET

_SLUG_MDY = re.compile(r"^([a-z0-9]+)-quarterly-earnings-(gaap|nongaap)-eps-(\d\d)-(\d\d)-(\d{4})")
_SLUG_YMD = re.compile(r"^(?:will-)?([a-z0-9]+)-(?:quarterly-earnings-(gaap|nongaap)-eps|beat-earnings-estimate)-(\d{4})-(\d\d)-(\d\d)")
_TICKER_Q = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)")
_THRESH = re.compile(r"EPS[^$]{0,80}?is (-?)\$(-?[\d,]*\.?\d+)")


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def parse_event(e: dict) -> dict | None:
    slug = e.get("slug", "")
    if len(e.get("markets") or []) != 1:
        return None
    m = e["markets"][0]
    if a := _SLUG_MDY.match(slug):
        tic, basis, date = a.group(1), a.group(2), dt.date(int(a.group(5)), int(a.group(3)), int(a.group(4)))
    elif a := _SLUG_YMD.match(slug):
        tic, basis, date = a.group(1), a.group(2), dt.date(int(a.group(3)), int(a.group(4)), int(a.group(5)))
    else:
        return None
    desc = m.get("description") or ""
    if basis is None:
        basis = "nongaap" if re.search(r"non-GAAP|adjusted", desc, re.I) else "gaap"
    if q := _TICKER_Q.search(m.get("question") or ""):
        tic = q.group(1)
    thr = None
    if t := _THRESH.search(desc):
        thr = float(t.group(2).replace(",", "")) * (-1 if t.group(1) else 1)
    try:
        outcomes = json.loads(m["outcomes"])
        prices = json.loads(m["outcomePrices"])
        tokens = json.loads(m["clobTokenIds"])
    except (KeyError, TypeError, ValueError):
        return None
    if outcomes[:2] != ["Yes", "No"]:
        return None
    resolved = prices in (["1", "0"], ["0", "1"])
    return dict(
        slug=slug, ticker=tic.upper(), basis=basis, est_date=date, threshold=thr,
        end_ts=_ts(m.get("endDate")), created_ts=_ts(m.get("createdAt")),
        closed=bool(m.get("closed")), resolved=resolved, yes=int(prices[0] == "1") if resolved else None,
        condition_id=m.get("conditionId"), yes_token=tokens[0], no_token=tokens[1],
        volume=float(m.get("volumeNum") or 0),
    )


def build_events(raw_events: list[dict]) -> pd.DataFrame:
    rows = [r for r in map(parse_event, raw_events) if r]
    df = pd.DataFrame(rows).drop_duplicates("condition_id")
    end_et = pd.to_datetime(df["end_ts"], unit="s", utc=True).dt.tz_convert(ET)
    df["end_date_et"] = end_et.dt.date
    df["end_hour_et"] = end_et.dt.hour + end_et.dt.minute / 60
    return df.reset_index(drop=True)


def timing_from_et(ts_et: pd.Timestamp) -> tuple[dt.date, str]:
    """Map a release time (ET) to (report date, BMO/AMC/DMH)."""
    h = ts_et.hour + ts_et.minute / 60
    d = ts_et.date()
    if h < 4:  # after-midnight releases belong to the previous evening's session
        return d - dt.timedelta(days=1), "AMC"
    if h < 9.5:
        return d, "BMO"
    if h < 16:
        return d, "DMH"
    return d, "AMC"


def build_upcoming(raw_open_events: list[dict], today: dt.date, days: int = 10) -> pd.DataFrame:
    """Open EPS markets reporting within `days`, with timing taken from the market endDate."""
    from estimate.calendar import entry_and_reaction_days

    rows = [r for r in map(parse_event, raw_open_events) if r and not r["resolved"]]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[(df.est_date >= today) & (df.est_date <= today + dt.timedelta(days=days))].copy()
    end_et = pd.to_datetime(df.end_ts, unit="s", utc=True).dt.tz_convert(ET)
    df["timing"] = [timing_from_et(t)[1] for t in end_et]
    df["timing"] = df.timing.replace({"DMH": "BMO"})
    days_ = [entry_and_reaction_days(d, t) for d, t in zip(df.est_date, df.timing)]
    df["entry_date"] = [e for e, _ in days_]
    df["reaction_date"] = [r for _, r in days_]
    return df.sort_values(["entry_date", "ticker"]).reset_index(drop=True)
