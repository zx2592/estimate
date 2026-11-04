"""H5: is the option-implied earnings move cheap or rich relative to the realized move?

Uses IBKR's daily 30-day implied volatility of each stock (constant maturity, ~21 trading
days). The earnings day's implied variance is backed out from how much IV30 is elevated
by the event:

    21 * iv_pre^2 = 20 * iv_base^2 + 252 * var_event
    implied_move  = sqrt(2 / pi) * sqrt(var_event)       # expected |return| on the event day

Two choices of iv_base:
  * ante: IV30 about 35 calendar days before entry, whose window ends before the event.
    Uses only information available at entry, so it is the primary measure.
  * post: IV30 at the reaction close. More precise, but biased: after a large move IV
    stays high, the event variance looks smaller, and straddles look too profitable.

Long-straddle return proxy (front expiry just after the event): |realized move| / implied_move - 1.
This ignores bid/ask and the residual time value at exit.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from estimate.research.h1 import add_baselines, usable
from estimate.research.h2 import add_returns

WINDOW = 21
TRADING_DAYS = 252
BASE_LAG_DAYS = 35
SQRT_2_PI = math.sqrt(2 / math.pi)


def implied_event_move(iv_pre: float, iv_base: float) -> float:
    var_event = (WINDOW * iv_pre ** 2 - (WINDOW - 1) * iv_base ** 2) / TRADING_DAYS
    return SQRT_2_PI * math.sqrt(var_event) if var_event > 0 else float("nan")


def _iv_series(vol_dir: Path, ticker: str) -> pd.Series | None:
    f = vol_dir / f"{ticker}.parquet"
    if not f.exists():
        return None
    s = pd.read_parquet(f).get("iv30")
    if s is None or s.dropna().empty:
        return None
    s = s.dropna().sort_index()
    if s.median() > 5:  # guard against percent units
        s = s / 100
    return s


def _asof(s: pd.Series, when: pd.Timestamp) -> float:
    v = s.loc[:when]
    return float(v.iloc[-1]) if len(v) else float("nan")


def add_implied_moves(d: pd.DataFrame, vol_dir: Path) -> pd.DataFrame:
    rows = []
    cache: dict[str, pd.Series | None] = {}
    for r in d.itertuples():
        s = cache.setdefault(r.ticker, _iv_series(vol_dir, r.ticker))
        rec = dict(iv_pre=np.nan, iv_post=np.nan, iv_base=np.nan)
        if s is not None:
            e, x = pd.Timestamp(r.entry_date), pd.Timestamp(r.reaction_date)
            if e in s.index and x in s.index:
                rec = dict(iv_pre=float(s[e]), iv_post=float(s[x]),
                           iv_base=_asof(s, e - pd.Timedelta(days=BASE_LAG_DAYS)))
        rows.append(rec)
    d = pd.concat([d.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    d["implied_ante"] = [implied_event_move(a, b) for a, b in zip(d.iv_pre, d.iv_base)]
    d["implied_post"] = [implied_event_move(a, b) for a, b in zip(d.iv_pre, d.iv_post)]
    d["realized"] = d.ret_cc.abs()
    for k in ("ante", "post"):
        d[f"straddle_ret_{k}"] = d.realized / d[f"implied_{k}"] - 1
    return d


def add_history_score(d: pd.DataFrame, shrink: float = 2.0) -> pd.DataFrame:
    """cheapness = implied_ante / the company's own past realized earnings moves (shrunk to the cross-section)."""
    d = d.sort_values("release_ts").reset_index(drop=True)
    hist = []
    for i, r in enumerate(d.itertuples()):
        prev = d.iloc[:i]
        prev = prev[prev.release_ts < r.release_ts - 86400]
        pool = prev.realized.dropna()
        if len(pool) < 50:
            hist.append(np.nan)
            continue
        own = prev[prev.ticker == r.ticker].realized.dropna()
        hist.append((own.sum() + shrink * pool.median()) / (len(own) + shrink))
    d["hist_move"] = hist
    d["cheapness"] = d.implied_ante / d.hist_move
    return d


def _boot_ci(x: np.ndarray, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    return tuple(np.percentile([rng.choice(x, len(x)).mean() for _ in range(n)], [2.5, 97.5]))


def report(panel: pd.DataFrame, prices_dir: Path, vol_dir: Path) -> pd.DataFrame:
    p = add_baselines(usable(panel))
    p = add_returns(p, prices_dir).dropna(subset=["ret_cc"])
    p = p.sort_values("vol_total_usd", ascending=False).drop_duplicates(["ticker", "entry_date"])
    d = add_history_score(add_implied_moves(p, vol_dir))
    have = d.dropna(subset=["implied_ante"])
    print(f"events with returns: {len(d)}; with IV30 around the event: {d.iv_pre.notna().sum()}; "
          f"with ex-ante implied move: {len(have)}")
    if have.empty:
        print("no IV history found; run ibkr-vol-history first")
        return d

    print("\n[Sanity] IV30 run-up and crush (medians)")
    print(f"  iv_base {have.iv_base.median():.3f} -> iv_pre {have.iv_pre.median():.3f} -> iv_post {have.iv_post.median():.3f}")

    print("\n[Pricing] realized vs implied earnings move")
    rows = []
    for k in ("ante", "post"):
        s = d.dropna(subset=[f"implied_{k}"])
        x = s[f"straddle_ret_{k}"].to_numpy()
        lo, hi = _boot_ci(x)
        rows.append(dict(base=k, n=len(s), median_implied=s[f"implied_{k}"].median(), median_realized=s.realized.median(),
                         realized_gt_implied=(s.realized > s[f"implied_{k}"]).mean(),
                         straddle_ret_mean=x.mean(), ci_lo=lo, ci_hi=hi))
    print(pd.DataFrame(rows).round(4).to_string(index=False))

    s = have.dropna(subset=["cheapness"]).copy()
    print(f"\n[Predictive] cheapness = implied_ante / past realized moves (n={len(s)})")
    if len(s) >= 50:
        rho, pv = stats.spearmanr(s.cheapness, s.straddle_ret_ante)
        print(f"  spearman(cheapness, straddle_ret_ante) = {rho:+.3f} (p={pv:.3g}); H5 expects negative")
        s["qbin"] = pd.qcut(s.cheapness.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        q = s.groupby("qbin", observed=True).agg(n=("cheapness", "size"), cheapness=("cheapness", "mean"),
                                                 straddle_ret=("straddle_ret_ante", "mean"),
                                                 realized_gt_implied=("realized", lambda r: (r > s.loc[r.index, "implied_ante"]).mean()))
        print(q.round(4).to_string())
        cheap = s[s.qbin == 1].straddle_ret_ante.to_numpy()
        lo, hi = _boot_ci(cheap)
        mono = stats.spearmanr(q.index.astype(int), q.straddle_ret).statistic
        verdict = "PASS" if lo > 0 and rho < 0 and pv < 0.05 else "FAIL"
        print(f"  cheapest quintile straddle return {cheap.mean():+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}]; "
              f"quintile monotonicity {mono:+.2f} -> H5 {verdict}")
        by_q = s.groupby("quarter").apply(lambda g: stats.spearmanr(g.cheapness, g.straddle_ret_ante).statistic
                                          if len(g) > 20 else np.nan, include_groups=False)
        print("  spearman by quarter:", by_q.round(3).to_dict())

    print("\n[Prediction market x options] does Polymarket conviction relate to realized / implied?")
    have = have.assign(ratio=have.realized / have.implied_ante, entropy=have.p0 * (1 - have.p0))
    rho, pv = stats.spearmanr(have.entropy, have.ratio)
    print(f"  spearman(p0*(1-p0), realized/implied) = {rho:+.3f} (p={pv:.3g})")
    have["pbin"] = pd.cut(have.p0, [0, .5, .8, .9, 1.0], include_lowest=True)
    print(have.groupby(["pbin", "yes"], observed=True).agg(n=("ratio", "size"), median_ratio=("ratio", "median"),
                                                          mean_straddle_ret=("straddle_ret_ante", "mean")).round(3).to_string())
    return d
