"""Option-chain features for earnings events: implied move, skew, and a put-spread trade.

Input is one snapshot of a chain as a DataFrame with columns
    expiry (date), strike, right ('C'/'P'), bid, ask, iv, delta, und (spot)
taken at the entry close. Only the first expiry on or after the reaction day is used.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

RATE = 0.04  # flat risk-free rate; small effect at one- to two-week tenors


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(s: float, k: float, t: float, iv: float, right: str, r: float = RATE) -> float:
    if t <= 0 or iv <= 0:
        return max(0.0, (s - k) if right == "C" else (k - s))
    d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if right == "C":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def bs_delta(s: float, k: float, t: float, iv: float, right: str, r: float = RATE) -> float:
    if t <= 0 or iv <= 0:
        return float((s > k) if right == "C" else -(s < k))
    d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t))
    return _ncdf(d1) if right == "C" else _ncdf(d1) - 1


def implied_vol(price: float, s: float, k: float, t: float, right: str, r: float = RATE) -> float:
    """Black-Scholes implied volatility by bisection; NaN if the price is outside no-arbitrage bounds."""
    lo, hi = 1e-4, 10.0
    if not (price > 0 and t > 0) or price < bs_price(s, k, t, lo, right, r) or price > bs_price(s, k, t, hi, right, r):
        return float("nan")
    for _ in range(100):
        mid = (lo + hi) / 2
        if bs_price(s, k, t, mid, right, r) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def front_expiry(chain: pd.DataFrame, reaction_date: dt.date) -> dt.date | None:
    exps = sorted(e for e in pd.to_datetime(chain.expiry).dt.date.unique() if e >= reaction_date)
    return exps[0] if exps else None


def _interp_iv_at_delta(side: pd.DataFrame, target: float) -> float:
    s = side.dropna(subset=["delta", "iv"]).sort_values("delta")
    if len(s) < 2 or not (s.delta.min() <= target <= s.delta.max()):
        return float("nan")
    return float(np.interp(target, s.delta, s.iv))


def chain_features(chain: pd.DataFrame, reaction_date: dt.date, asof: dt.date) -> dict:
    """Implied move, ATM IV, 25-delta skew and an ATM / 25-delta put spread, all at mid."""
    exp = front_expiry(chain, reaction_date)
    if exp is None:
        return {}
    c = chain[pd.to_datetime(chain.expiry).dt.date == exp].copy()
    c = c[(c.bid > 0) & (c.ask >= c.bid)]
    if c.empty:
        return {}
    spot = float(c.und.dropna().median())
    t = max((exp - asof).days, 0) / 365 + 1 / 365  # count the expiry day itself
    c["mid"] = (c.bid + c.ask) / 2
    missing = c.delta.isna() & c.iv.notna()
    c.loc[missing, "delta"] = [bs_delta(spot, k, t, v, r) for k, v, r in c.loc[missing, ["strike", "iv", "right"]].values]
    calls, puts = c[c.right == "C"], c[c.right == "P"]
    both = sorted(set(calls.strike) & set(puts.strike))
    if not both:
        return {}
    k_atm = min(both, key=lambda k: abs(k - spot))
    call_atm, put_atm = calls[calls.strike == k_atm].iloc[0], puts[puts.strike == k_atm].iloc[0]
    straddle = call_atm.mid + put_atm.mid
    out = dict(expiry=exp, spot=spot, days_to_expiry=(exp - asof).days, k_atm=k_atm,
               implied_move=straddle / spot, atm_iv=np.nanmean([call_atm.iv, put_atm.iv]),
               put25_iv=_interp_iv_at_delta(puts, -0.25), call25_iv=_interp_iv_at_delta(calls, 0.25))
    out["put_skew"] = out["put25_iv"] - out["atm_iv"]
    out["rr25"] = out["call25_iv"] - out["put25_iv"]
    # Put spread: long the highest strike at or below spot, short the put nearest 25 delta below it.
    longs = puts[puts.strike <= spot].sort_values("strike")
    if len(longs):
        lp = longs.iloc[-1]
        below = puts[(puts.strike < lp.strike) & puts.delta.notna()]
        if len(below):
            sp = below.iloc[(below.delta + 0.25).abs().argsort().iloc[0]]
            out |= dict(ps_long_k=float(lp.strike), ps_short_k=float(sp.strike),
                        ps_cost_mid=float(lp.mid - sp.mid), ps_cost_cross=float(lp.ask - sp.bid),
                        ps_width=float(lp.strike - sp.strike))
    return out


def put_spread_payoff(long_k: float, short_k: float, s_exit: float) -> float:
    """Intrinsic value at exit. Ignores remaining time value, which is small for weekly expiries."""
    return max(0.0, long_k - s_exit) - max(0.0, short_k - s_exit)
