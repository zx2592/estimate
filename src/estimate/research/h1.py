"""H1: are Polymarket EPS-beat prices at the entry point better calibrated than base rates?"""
from __future__ import annotations

import numpy as np
import pandas as pd


def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def add_baselines(p: pd.DataFrame, shrink: float = 4.0) -> pd.DataFrame:
    """Out-of-sample priors using only events that settled before each event's report date.

    base_basis: beat rate of all earlier markets with the same GAAP / non-GAAP basis.
    base_ticker: the company's own earlier beat rate, shrunk toward base_basis.
    """
    p = p.sort_values("release_ts").reset_index(drop=True)
    bb, bt = [], []
    for i, r in enumerate(p.itertuples()):
        prev = p.iloc[:i]
        prev = prev[prev.release_ts < r.release_ts - 86400]
        same = prev[prev.basis == r.basis]
        b = same.yes.mean() if len(same) >= 30 else 0.74
        own = prev[prev.ticker == r.ticker]
        bb.append(b)
        bt.append((own.yes.sum() + shrink * b) / (len(own) + shrink))
    p["base_basis"], p["base_ticker"] = bb, bt
    return p


def usable(p: pd.DataFrame) -> pd.DataFrame:
    return p.dropna(subset=["p0", "release_ts"]).copy()


def report(panel: pd.DataFrame) -> pd.DataFrame:
    p = add_baselines(usable(panel))
    print(f"usable events: {len(p)} of {len(panel)}; beat rate {p.yes.mean():.3f}")
    print("release source:", p.release_src.value_counts().to_dict(), "| timing:", p.timing.value_counts().to_dict())
    agree = p.dropna(subset=["end_timing"])
    agree = agree[agree.release_src == "volume_peak"]
    print(f"timing agrees with endDate hour: {(agree.timing == agree.end_timing).mean():.3f} (n={len(agree)})")
    post = p.dropna(subset=["p_post"])
    print(f"price 2h after detected release within 0.1 of outcome: {((post.p_post - post.yes).abs() < 0.1).mean():.3f}")
    print(f"entry price already within 0.03 of 0/1 (possible leakage): {((p.p0 < 0.03) | (p.p0 > 0.97)).mean():.3f}")

    print("\nBrier at entry (lower is better)")
    rows = []
    for name, sub in [("all", p), ("gaap", p[p.basis == "gaap"]), ("nongaap", p[p.basis == "nongaap"])]:
        bm, b1, b2 = brier(sub.p0, sub.yes), brier(sub.base_basis, sub.yes), brier(sub.base_ticker, sub.yes)
        rows.append(dict(subset=name, n=len(sub), market=bm, base_basis=b1, base_ticker=b2,
                         bss_vs_basis=1 - bm / b1, bss_vs_ticker=1 - bm / b2))
    print(pd.DataFrame(rows).round(4).to_string(index=False))

    print("\nBy quarter")
    q = p.groupby("quarter").apply(lambda s: pd.Series(dict(
        n=len(s), beat=s.yes.mean(), market=brier(s.p0, s.yes), base_ticker=brier(s.base_ticker, s.yes),
        med_vol=s.vol_total_usd.median())), include_groups=False)
    print(q.round(4).to_string())

    print("\nCalibration at entry")
    p["bucket"] = pd.cut(p.p0, np.linspace(0, 1, 11), include_lowest=True)
    c = p.groupby("bucket", observed=True).agg(n=("yes", "size"), mean_p=("p0", "mean"), actual=("yes", "mean"))
    print(c.round(3).to_string())
    return p
