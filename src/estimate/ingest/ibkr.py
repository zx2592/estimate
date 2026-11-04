"""Interactive Brokers data via TWS / IB Gateway (ib_async). Runs where the gateway runs.

The API does not serve data for expired option contracts, so option snapshots must be
collected forward in time. Underlying implied / historical volatility history is
available for past dates.

Ports: TWS paper 7497, TWS live 7496, Gateway paper 4002, Gateway live 4001.
Market data type: 1 live (needs an OPRA subscription for options), 3 delayed, 4 delayed-frozen.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd


def connect(host: str = "127.0.0.1", port: int = 7497, client_id: int = 17, market_data_type: int = 3):
    from ib_async import IB

    ib = IB()
    ib.connect(host, port, clientId=client_id, readonly=True)
    ib.reqMarketDataType(market_data_type)
    return ib


def _num(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return x if math.isfinite(x) and x > -1 else float("nan")  # IB uses -1 for "no value"


def snapshot_chain(ib, ticker: str, reaction_date: dt.date, n_expiries: int = 2,
                   strike_band: float = 0.25, batch: int = 80) -> pd.DataFrame:
    """Quotes, model IV and delta for the first expiries on or after the reaction day."""
    from ib_async import Option, Stock

    stk = Stock(ticker.replace(".", " "), "SMART", "USD")
    if not ib.qualifyContracts(stk) or not stk.conId:
        raise RuntimeError(f"cannot qualify {ticker}")
    [st] = ib.reqTickers(stk)
    spot = _num(st.marketPrice())
    if math.isnan(spot):
        spot = _num(st.close)
    if math.isnan(spot):
        raise RuntimeError(f"no spot price for {ticker}")
    chains = [c for c in ib.reqSecDefOptParams(stk.symbol, "", stk.secType, stk.conId) if c.exchange == "SMART"]
    if not chains:
        raise RuntimeError(f"no option chain for {ticker}")
    chain = next((c for c in chains if c.tradingClass == stk.symbol), chains[0])
    exps = sorted(e for e in chain.expirations if e >= reaction_date.strftime("%Y%m%d"))[:n_expiries]
    strikes = [k for k in chain.strikes if abs(k / spot - 1) <= strike_band]
    wanted = [Option(stk.symbol, e, k, r, "SMART", tradingClass=chain.tradingClass, multiplier=chain.multiplier)
              for e in exps for k in strikes for r in ("C", "P")]
    contracts = [c for c in ib.qualifyContracts(*wanted) if c is not None and c.conId]
    rows = []
    for i in range(0, len(contracts), batch):  # stay under the default 100 market-data lines
        for tk in ib.reqTickers(*contracts[i:i + batch]):
            g = tk.modelGreeks
            rows.append(dict(
                ticker=ticker, expiry=dt.datetime.strptime(tk.contract.lastTradeDateOrContractMonth, "%Y%m%d").date(),
                strike=float(tk.contract.strike), right=tk.contract.right,
                bid=_num(tk.bid), ask=_num(tk.ask), last=_num(tk.last), volume=_num(tk.volume),
                iv=_num(g.impliedVol) if g else float("nan"), delta=_num(g.delta) if g else float("nan"),
                und=_num(g.undPrice) if g and g.undPrice else spot,
            ))
    df = pd.DataFrame(rows)
    df["snapshot_ts"] = pd.Timestamp.now(tz="UTC")
    return df


def underlying_vol_history(ib, ticker: str, duration: str = "2 Y") -> pd.DataFrame:
    """Daily 30-day implied volatility and historical volatility of the stock."""
    from ib_async import Stock, util

    stk = Stock(ticker.replace(".", " "), "SMART", "USD")
    ib.qualifyContracts(stk)
    out = {}
    for what in ("OPTION_IMPLIED_VOLATILITY", "HISTORICAL_VOLATILITY"):
        bars = ib.reqHistoricalData(stk, "", duration, "1 day", what, useRTH=True, formatDate=1)
        df = util.df(bars)
        if df is not None and len(df):
            out[what.lower()] = df.set_index("date")["close"]
    df = pd.DataFrame(out)
    df.index = pd.to_datetime(df.index)
    return df.rename(columns={"option_implied_volatility": "iv30", "historical_volatility": "hv30"})
