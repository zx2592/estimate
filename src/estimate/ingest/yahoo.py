"""Daily stock bars from Yahoo Finance via yfinance, cached as one parquet file per ticker.

Prices are split-adjusted but not dividend-adjusted (yfinance auto_adjust=False),
which is what a one-day earnings reaction needs.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def yahoo_symbol(ticker: str) -> str:
    """Yahoo uses '-' for share classes: BRK.A -> BRK-A."""
    return ticker.replace(".", "-").upper()


def split_download(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Turn a yf.download(group_by='ticker') frame into {symbol: OHLCV frame}."""
    out = {}
    for sym in symbols:
        if isinstance(raw.columns, pd.MultiIndex):
            if sym not in raw.columns.get_level_values(0):
                continue
            df = raw[sym]
        else:  # single-ticker downloads come back flat
            df = raw
        df = df.rename(columns=str.lower)[COLUMNS].dropna(subset=["open", "close"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index).tz_localize(None).normalize(), name="date")
        out[sym] = df
    return out


def fetch_daily(tickers: list[str], start: dt.date, end: dt.date, cache_dir: Path,
                chunk: int = 50, pause: float = 2.0) -> list[str]:
    """Download and cache bars for tickers not yet cached. Returns tickers Yahoo had no data for."""
    import yfinance as yf

    cache_dir.mkdir(parents=True, exist_ok=True)
    def stale(t: str) -> bool:
        f = cache_dir / f"{t}.parquet"
        if not f.exists():
            return True
        idx = pd.read_parquet(f, columns=["close"]).index
        # refetch when the cache stops short of the requested range (allow weekends / holidays)
        return len(idx) == 0 or idx.max() < pd.Timestamp(end) - pd.Timedelta(days=4) or idx.min() > pd.Timestamp(start) + pd.Timedelta(days=4)

    todo = [t for t in tickers if stale(t)]
    missing = []
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        syms = [yahoo_symbol(t) for t in batch]
        raw = yf.download(syms, start=start, end=end + dt.timedelta(days=1), auto_adjust=False,
                          actions=False, group_by="ticker", threads=True, progress=False)
        frames = split_download(raw, syms)
        for t, s in zip(batch, syms):
            df = frames.get(s)
            if df is None or df.empty:
                missing.append(t)
                continue
            df.to_parquet(cache_dir / f"{t}.parquet")
        print(f"  {min(i + chunk, len(todo))}/{len(todo)}", flush=True)
        time.sleep(pause)
    return missing


def snapshot_chain(ticker: str, reaction_date: dt.date, n_expiries: int = 2, strike_band: float = 0.25) -> pd.DataFrame:
    """Free fallback to IBKR: Yahoo option chain, with IV and delta recomputed from the bid/ask mid.

    Yahoo's own impliedVolatility comes from stale last prices, so it is kept only for reference.
    """
    import yfinance as yf

    from estimate.options import bs_delta, implied_vol

    tk = yf.Ticker(yahoo_symbol(ticker))
    spot = float(tk.fast_info["lastPrice"])
    exps = sorted(e for e in tk.options if dt.date.fromisoformat(e) >= reaction_date)[:n_expiries]
    if not exps:
        raise RuntimeError(f"no Yahoo expiries on or after {reaction_date} for {ticker}")
    today = dt.date.today()
    rows = []
    for e in exps:
        ch = tk.option_chain(e)
        exp = dt.date.fromisoformat(e)
        t = max((exp - today).days, 0) / 365 + 1 / 365
        for right, df in (("C", ch.calls), ("P", ch.puts)):
            df = df[(df.strike / spot - 1).abs() <= strike_band]
            for r in df.itertuples():
                bid, ask = float(r.bid or 0), float(r.ask or 0)
                iv = implied_vol((bid + ask) / 2, spot, float(r.strike), t, right) if bid > 0 and ask >= bid else float("nan")
                rows.append(dict(ticker=ticker, expiry=exp, strike=float(r.strike), right=right, bid=bid, ask=ask,
                                 last=float(r.lastPrice or float("nan")), volume=float(r.volume or 0),
                                 open_interest=float(r.openInterest or 0), iv=iv,
                                 delta=bs_delta(spot, float(r.strike), t, iv, right) if iv == iv else float("nan"),
                                 und=spot, yahoo_iv=float(r.impliedVolatility or float("nan"))))
    out = pd.DataFrame(rows)
    out["snapshot_ts"] = pd.Timestamp.now(tz="UTC")
    return out
