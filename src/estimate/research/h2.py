"""H2: do prediction-market signals at entry predict the earnings-day stock move?

Entry is the last regular close before the release. The reaction is measured to the
next session's open (gap) and close, both in excess of SPY over the same window.
Also reports H3 (uncertainty vs move size) since it needs the same returns.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from estimate.research.h1 import add_baselines, usable

FEATURES = ["p0", "edge_vs_ticker", "dp24", "dp72", "flow72"]


def _bars(cache_dir: Path, ticker: str) -> pd.DataFrame | None:
    f = cache_dir / f"{ticker}.parquet"
    if not f.exists():
        return None
    df = pd.read_parquet(f)
    return df if len(df) else None


def add_returns(p: pd.DataFrame, cache_dir: Path) -> pd.DataFrame:
    spy = _bars(cache_dir, "SPY")
    if spy is None:
        raise RuntimeError(f"no SPY bars in {cache_dir}; run fetch-polygon first")
    out = []
    cache: dict[str, pd.DataFrame | None] = {}
    for r in p.itertuples():
        b = cache.setdefault(r.ticker, _bars(cache_dir, r.ticker))
        e, x = pd.Timestamp(r.entry_date), pd.Timestamp(r.reaction_date)
        rec = dict(ret_gap=np.nan, ret_cc=np.nan, xret_gap=np.nan, xret_cc=np.nan)
        if b is not None and e in b.index and x in b.index and e in spy.index and x in spy.index:
            c0, o1, c1 = b.at[e, "close"], b.at[x, "open"], b.at[x, "close"]
            s0, so, sc = spy.at[e, "close"], spy.at[x, "open"], spy.at[x, "close"]
            rec = dict(ret_gap=o1 / c0 - 1, ret_cc=c1 / c0 - 1,
                       xret_gap=(o1 / c0) - (so / s0), xret_cc=(c1 / c0) - (sc / s0))
        out.append(rec)
    return pd.concat([p.reset_index(drop=True), pd.DataFrame(out)], axis=1)


def _auc_ci(y, s, n_boot=1000, seed=0):
    y, s = np.asarray(y), np.asarray(s)
    rng = np.random.default_rng(seed)
    base = roc_auc_score(y, s)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            boots.append(roc_auc_score(y[i], s[i]))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return base, lo, hi


def walk_forward(d: pd.DataFrame, target: str, min_train_quarters: int = 2) -> pd.DataFrame:
    """Train on all earlier quarters, predict the next one. Returns out-of-sample predictions."""
    qs = sorted(d.quarter.unique())
    preds = []
    for i, q in enumerate(qs):
        if i < min_train_quarters:
            continue
        tr, te = d[d.quarter.isin(qs[:i])], d[d.quarter == q]
        if tr[target].nunique() < 2 or len(te) == 0:
            continue
        m = make_pipeline(StandardScaler(), LogisticRegression(C=0.5))
        m.fit(tr[FEATURES], tr[target])
        preds.append(te.assign(prob=m.predict_proba(te[FEATURES])[:, 1]))
    return pd.concat(preds) if preds else pd.DataFrame()


def report(panel: pd.DataFrame, cache_dir: Path) -> pd.DataFrame:
    p = add_baselines(usable(panel))
    p["edge_vs_ticker"] = p.p0 - p.base_ticker
    p = add_returns(p, cache_dir)
    d = p.dropna(subset=["xret_cc"]).copy()
    d[["dp24", "dp72"]] = d[["dp24", "dp72"]].fillna(0.0)
    d["flow72"] = d["flow72"].fillna(0.0)
    d["up"] = (d.xret_cc > 0).astype(int)
    print(f"events with returns: {len(d)} of {len(p)}")

    print("\n[Mechanism] excess close-to-close return by outcome")
    m = d.groupby("yes").agg(n=("up", "size"), mean_xret=("xret_cc", "mean"), median_xret=("xret_cc", "median"),
                             p_up=("up", "mean"))
    print(m.round(4).to_string())
    d["surprise"] = d.yes - d.p0
    for col in ("yes", "surprise"):
        rho, pv = stats.spearmanr(d[col], d.xret_cc)
        print(f"  spearman({col}, xret_cc) = {rho:+.3f} (p={pv:.3g})")

    print("\n[Ex ante] single-feature association with the excess move")
    rows = []
    for f in FEATURES:
        for tgt in ("xret_gap", "xret_cc"):
            s = d.dropna(subset=[tgt])
            rho, pv = stats.spearmanr(s[f], s[tgt])
            auc, lo, hi = _auc_ci((s[tgt] > 0).astype(int), s[f])
            rows.append(dict(feature=f, target=tgt, spearman=rho, p_value=pv, auc=auc, auc_lo=lo, auc_hi=hi))
    print(pd.DataFrame(rows).round(4).to_string(index=False))

    for f in ("edge_vs_ticker", "dp72"):
        print(f"\n[Quintiles] {f}")
        d["qbin"] = pd.qcut(d[f].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
        print(d.groupby("qbin", observed=True).agg(n=("up", "size"), mean_f=(f, "mean"), mean_xret=("xret_cc", "mean"),
                                                  p_up=("up", "mean")).round(4).to_string())

    print("\n[Walk-forward logistic] features:", FEATURES)
    oos = walk_forward(d, "up")
    if len(oos):
        auc = roc_auc_score(oos.up, oos.prob)
        hit = ((oos.prob > 0.5) == oos.up.astype(bool)).mean()
        k = int(((oos.prob > 0.5) == oos.up.astype(bool)).sum())
        bt = stats.binomtest(k, len(oos), 0.5, alternative="greater")
        print(f"  OOS n={len(oos)} AUC={auc:.4f} hit rate={hit:.4f} (binomial p vs 50%: {bt.pvalue:.3g}); "
              f"base up-rate {oos.up.mean():.4f}")
        per_q = oos.groupby("quarter").apply(lambda s: pd.Series(dict(
            n=len(s), auc=roc_auc_score(s.up, s.prob) if s.up.nunique() > 1 else np.nan)), include_groups=False)
        print(per_q.round(4).to_string())
        verdict = "PASS" if auc > 0.56 and bt.pvalue < 0.05 else "FAIL"
        print(f"  H2 gate (OOS AUC > 0.56 and hit rate significant): {verdict}")

    print("\n[H3] uncertainty vs move size")
    d["entropy"] = d.p0 * (1 - d.p0)
    rho, pv = stats.spearmanr(d.entropy, d.xret_cc.abs())
    print(f"  spearman(p0*(1-p0), |xret_cc|) = {rho:+.3f} (p={pv:.3g})")
    d["ebin"] = pd.qcut(d.entropy.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    print(d.groupby("ebin", observed=True).agg(n=("up", "size"), mean_entropy=("entropy", "mean"),
                                              mean_abs_xret=("xret_cc", lambda s: s.abs().mean())).round(4).to_string())
    return d
