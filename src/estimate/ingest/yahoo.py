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
    todo = [t for t in tickers if not (cache_dir / f"{t}.parquet").exists()]
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
