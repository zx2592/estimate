"""Build the per-event research panel: release time, entry point, and prediction-market features."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from estimate.calendar import ET, close_ts, entry_and_reaction_days
from estimate.events import timing_from_et

HOUR = 3600


def load_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def trades_frame(trades: list[dict]) -> pd.DataFrame:
    """Normalise trades on either token to YES-equivalent price and signed YES dollars."""
    if not trades:
        return pd.DataFrame(columns=["ts", "p_yes", "usd", "signed_usd"])
    t = pd.DataFrame(trades)
    t["ts"] = t["timestamp"].astype(int)
    is_yes = t["outcome_index"].astype(int) == 0
    t["p_yes"] = np.where(is_yes, t["price"], 1 - t["price"])
    t["usd"] = t["size"] * t["price"]
    buy = t["side"].str.upper() == "BUY"
    # Buying YES or selling NO is a bet on a beat.
    t["signed_usd"] = np.where(buy == is_yes, t["usd"], -t["usd"])
    return t.sort_values("ts")[["ts", "p_yes", "usd", "signed_usd"]].reset_index(drop=True)


def detect_release(ev: pd.Series, t: pd.DataFrame, min_usd: float = 300.0) -> dict:
    """Release time from the busiest trading hour near the estimated date.

    Falls back to the market endDate when it lands on the estimated date and the
    busiest hour is too thin to trust.
    """
    lo = int(dt.datetime.combine(ev.est_date - dt.timedelta(days=1), dt.time(12), ET).timestamp())
    hi = int(dt.datetime.combine(ev.est_date + dt.timedelta(days=3), dt.time(0), ET).timestamp())
    w = t[(t.ts >= lo) & (t.ts < hi)]
    peak_ts, peak_usd = None, 0.0
    if len(w):
        by_hour = w.groupby(w.ts // HOUR)["usd"].sum()
        peak_hour, peak_usd = int(by_hour.idxmax()), float(by_hour.max())
        first_in_hour = w[(w.ts // HOUR) == peak_hour].ts.min()
        peak_ts = int(first_in_hour)
    end_ok = ev.end_date_et == ev.est_date
    if peak_ts is not None and peak_usd >= min_usd:
        src, ts = "volume_peak", peak_ts
    elif end_ok:
        src, ts = "end_date", int(ev.end_ts)
    else:
        return dict(release_src="none")
    rdate, timing = timing_from_et(pd.Timestamp(ts, unit="s", tz="UTC").tz_convert(ET))
    out = dict(release_src=src, release_ts=ts, report_date=rdate, timing=timing, peak_usd=peak_usd)
    if end_ok:
        _, end_timing = timing_from_et(pd.Timestamp(int(ev.end_ts), unit="s", tz="UTC").tz_convert(ET))
        out["end_timing"] = end_timing
    return out


def price_at(hist: list[dict], ts: int) -> float | None:
    before = [p["p"] for p in hist if p["t"] <= ts]
    return float(before[-1]) if before else None


def build_panel(events: pd.DataFrame, data_dir: Path) -> pd.DataFrame:
    rows = []
    for ev in events.itertuples(index=False):
        trades = trades_frame(load_json(data_dir / "poly" / "trades" / f"{ev.condition_id}.json") or [])
        hist = load_json(data_dir / "poly" / "hist" / f"{ev.yes_token}.json") or []
        rel = detect_release(ev, trades)
        row = dict(ev._asdict()) | rel
        if rel["release_src"] != "none" and abs((rel["report_date"] - ev.est_date).days) <= 3:
            timing = "BMO" if rel["timing"] == "DMH" else rel["timing"]
            entry, react = entry_and_reaction_days(rel["report_date"], timing)
            t0 = close_ts(entry)
            if t0 > rel["release_ts"]:  # entry must precede the release
                t0 = rel["release_ts"] - 15 * 60
            pre = trades[(trades.ts > t0 - 72 * HOUR) & (trades.ts <= t0)]
            row |= dict(
                entry_date=entry, reaction_date=react, t0=t0,
                p0=price_at(hist, t0), p24=price_at(hist, t0 - 24 * HOUR), p72=price_at(hist, t0 - 72 * HOUR),
                p_post=price_at(hist, rel["release_ts"] + 2 * HOUR),
                flow72_usd=float(pre.signed_usd.sum()), vol72_usd=float(pre.usd.sum()), n_trades72=len(pre),
                vol_total_usd=float(trades.usd.sum()),
            )
        rows.append(row)
    p = pd.DataFrame(rows)
    p["dp24"] = p["p0"] - p["p24"]
    p["dp72"] = p["p0"] - p["p72"]
    p["flow72"] = p["flow72_usd"] / p["vol72_usd"].where(p["vol72_usd"] > 0)
    d = pd.to_datetime(p["report_date"])
    p["quarter"] = d.dt.year.astype("Int64").astype(str) + "-Q" + ((d.dt.month - 1) // 3 + 1).astype("Int64").astype(str)
    return p
