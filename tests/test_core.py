import datetime as dt

import pandas as pd

from estimate.calendar import entry_and_reaction_days
from estimate.events import parse_event, timing_from_et
from estimate.research.panel import trades_frame


def test_entry_reaction_bmo_monday():
    # BMO on Monday: enter Friday close, react Monday.
    assert entry_and_reaction_days(dt.date(2026, 11, 2), "BMO") == (dt.date(2026, 10, 30), dt.date(2026, 11, 2))


def test_entry_reaction_amc_before_holiday():
    # AMC on the Wednesday before Thanksgiving: react on Friday.
    assert entry_and_reaction_days(dt.date(2026, 11, 25), "AMC") == (dt.date(2026, 11, 25), dt.date(2026, 11, 27))


def test_timing_from_et():
    et = "America/New_York"
    assert timing_from_et(pd.Timestamp("2026-11-04 07:00", tz=et)) == (dt.date(2026, 11, 4), "BMO")
    assert timing_from_et(pd.Timestamp("2026-11-04 16:05", tz=et)) == (dt.date(2026, 11, 4), "AMC")
    assert timing_from_et(pd.Timestamp("2026-11-05 00:30", tz=et)) == (dt.date(2026, 11, 4), "AMC")


def test_parse_event_slug_and_description():
    e = {"slug": "rklb-quarterly-earnings-gaap-eps-08-10-2026-neg0pt08", "markets": [{
        "question": "Will Rocket Lab (RKLB) beat quarterly earnings?",
        "description": "The Street consensus estimate for Rocket Lab's GAAP EPS for the relevant quarter is -$0.08 as of",
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0", "1"]', "clobTokenIds": '["1", "2"]',
        "conditionId": "0xabc", "endDate": "2026-08-10T21:00:00Z", "volumeNum": 10}]}
    r = parse_event(e)
    assert r["ticker"] == "RKLB" and r["basis"] == "gaap" and r["threshold"] == -0.08 and r["yes"] == 0


def test_trades_frame_signs():
    t = trades_frame([
        {"side": "BUY", "outcome_index": 0, "size": 10, "price": 0.6, "timestamp": 1},   # buy YES  -> +6
        {"side": "BUY", "outcome_index": 1, "size": 10, "price": 0.4, "timestamp": 2},   # buy NO   -> -4
        {"side": "SELL", "outcome_index": 1, "size": 5, "price": 0.4, "timestamp": 3},   # sell NO  -> +2
    ])
    assert list(t.signed_usd.round(6)) == [6.0, -4.0, 2.0]
    assert list(t.p_yes.round(6)) == [0.6, 0.6, 0.6]


def test_yahoo_symbol_and_split_download():
    from estimate.ingest.yahoo import split_download, yahoo_symbol

    assert yahoo_symbol("BRK.A") == "BRK-A"
    idx = pd.to_datetime(["2026-11-02", "2026-11-03"])
    cols = pd.MultiIndex.from_product([["SPY", "BRK-A"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
    raw = pd.DataFrame([[1, 2, 0.5, 1.5, 1.4, 10] * 2, [2, 3, 1.5, 2.5, 2.4, 20] * 2], index=idx, columns=cols)
    out = split_download(raw, ["SPY", "BRK-A", "MISSING"])
    assert set(out) == {"SPY", "BRK-A"}
    assert list(out["SPY"].columns) == ["open", "high", "low", "close", "volume"]
    assert out["BRK-A"].loc["2026-11-03", "close"] == 2.5  # unadjusted-for-dividends Close, not Adj Close


def _synthetic_chain(spot=100.0, days=7, skew=0.004):
    """Chain priced with Black-Scholes and an IV smile that rises for lower strikes."""
    from estimate.options import bs_delta, bs_price

    asof = dt.date(2026, 11, 4)
    exp = asof + dt.timedelta(days=days)
    t = (days + 1) / 365
    rows = []
    for k in range(80, 121):
        iv = 0.60 + skew * (spot - k)
        for r in ("C", "P"):
            px = bs_price(spot, k, t, iv, r)
            rows.append(dict(expiry=exp, strike=float(k), right=r, bid=px * 0.98, ask=px * 1.02,
                             iv=iv, delta=bs_delta(spot, k, t, iv, r), und=spot))
    return pd.DataFrame(rows), asof


def test_chain_features_on_synthetic_smile():
    from estimate.options import chain_features

    chain, asof = _synthetic_chain()
    f = chain_features(chain, asof + dt.timedelta(days=1), asof)
    assert f["k_atm"] == 100.0
    assert abs(f["atm_iv"] - 0.60) < 1e-9
    assert f["put_skew"] > 0 and f["rr25"] < 0  # downside smile
    # ATM straddle ~ 0.8 * sigma * sqrt(t) of spot
    assert abs(f["implied_move"] - 0.8 * 0.60 * (8 / 365) ** 0.5) < 0.01
    assert f["ps_long_k"] == 100.0 and f["ps_short_k"] < 100.0
    assert f["ps_cost_cross"] > f["ps_cost_mid"] > 0


def test_put_spread_payoff():
    from estimate.options import put_spread_payoff

    assert put_spread_payoff(100, 95, 101) == 0
    assert put_spread_payoff(100, 95, 97) == 3
    assert put_spread_payoff(100, 95, 80) == 5


def test_implied_vol_roundtrip():
    from estimate.options import bs_price, implied_vol

    px = bs_price(100, 95, 10 / 365, 0.7, "P")
    assert abs(implied_vol(px, 100, 95, 10 / 365, "P") - 0.7) < 1e-6


def test_implied_event_move():
    import math

    from estimate.research.h5 import implied_event_move

    # No elevation -> event day is an ordinary day: sqrt(2/pi) * iv / sqrt(252)
    assert abs(implied_event_move(0.4, 0.4) - math.sqrt(2 / math.pi) * 0.4 / math.sqrt(252)) < 1e-12
    # A 5% event-day standard deviation added to a 40% base
    iv_pre = math.sqrt((20 * 0.4 ** 2 + 252 * 0.05 ** 2) / 21)
    assert abs(implied_event_move(iv_pre, 0.4) - math.sqrt(2 / math.pi) * 0.05) < 1e-12
    assert math.isnan(implied_event_move(0.3, 0.5))
