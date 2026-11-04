"""H7: when Polymarket strongly expects a beat, is the downside tail underpriced by put skew?

Pre-registered design (fixed before any data is collected, no tuning):
  * Entry: chain snapshot at the entry close (see estimate.calendar), first expiry on or
    after the reaction day.
  * Trade: buy the highest put strike at or below spot, sell the put nearest 25 delta below.
  * Exit: reaction-day close, valued at intrinsic.
  * Groups: p0 >= 0.8 ("high conviction") vs p0 < 0.8.
  * Pass: high-conviction mean return after crossing the spread > 0, bootstrap 95% lower
    bound > 0, with at least 100 events.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from estimate.options import chain_features, put_spread_payoff

HIGH_P0 = 0.8
MIN_EVENTS = 100


def load_snapshot_features(snap_dir: Path, panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ev in panel.dropna(subset=["entry_date"]).itertuples():
        f = snap_dir / str(ev.entry_date) / f"{ev.ticker}.parquet"
        if not f.exists():
            continue
        feats = chain_features(pd.read_parquet(f), ev.reaction_date, ev.entry_date)
        if feats:
            rows.append(dict(condition_id=ev.condition_id) | feats)
    return pd.DataFrame(rows)


def _boot_mean_ci(x: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    m = [rng.choice(x, len(x)).mean() for _ in range(n)]
    return tuple(np.percentile(m, [2.5, 97.5]))


def evaluate(panel: pd.DataFrame, feats: pd.DataFrame, prices_dir: Path) -> pd.DataFrame:
    d = panel.merge(feats, on="condition_id")
    # GAAP and non-GAAP markets on the same report share one trade: keep the more liquid market.
    d = d.sort_values("vol_total_usd", ascending=False).drop_duplicates(["ticker", "entry_date"])
    closes = []
    for r in d.itertuples():
        f = prices_dir / f"{r.ticker}.parquet"
        b = pd.read_parquet(f) if f.exists() else None
        x = pd.Timestamp(r.reaction_date)
        closes.append(float(b.at[x, "close"]) if b is not None and x in b.index else np.nan)
    d["s_exit"] = closes
    d = d.dropna(subset=["s_exit", "ps_cost_mid", "p0"])
    d = d[d.ps_cost_mid > 0].copy()
    d["payoff"] = [put_spread_payoff(a, b, s) for a, b, s in zip(d.ps_long_k, d.ps_short_k, d.s_exit)]
    d["ret_mid"] = d.payoff / d.ps_cost_mid - 1
    d["ret_cross"] = d.payoff / d.ps_cost_cross - 1
    d["move"] = d.s_exit / d.spot - 1
    d["exceed_down"] = d.move < -d.implied_move
    d["group"] = np.where(d.p0 >= HIGH_P0, "high_p0", "low_p0")
    return d


def report(d: pd.DataFrame) -> None:
    if d.empty:
        print("no events with both an option snapshot and a settled outcome yet")
        return
    print(f"events: {len(d)}  (gate needs >= {MIN_EVENTS} high-p0 events)")
    g = d.groupby("group").agg(n=("ret_mid", "size"), p0=("p0", "mean"), miss_rate=("yes", lambda s: 1 - s.mean()),
                               put_skew=("put_skew", "mean"), implied_move=("implied_move", "mean"),
                               down_exceed=("exceed_down", "mean"), ret_mid=("ret_mid", "mean"),
                               ret_cross=("ret_cross", "mean"))
    print(g.round(4).to_string())
    rho, pv = stats.spearmanr(d.p0, d.put_skew, nan_policy="omit")
    print(f"does skew already rise with p0? spearman(p0, put_skew) = {rho:+.3f} (p={pv:.3g})")
    h = d[d.group == "high_p0"].ret_cross.to_numpy()
    if len(h) >= 10:
        lo, hi = _boot_mean_ci(h)
        verdict = "PASS" if len(h) >= MIN_EVENTS and lo > 0 else ("FAIL" if len(h) >= MIN_EVENTS else "INSUFFICIENT DATA")
        print(f"high-p0 put spread, crossing the spread: mean {h.mean():+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}] -> {verdict}")
