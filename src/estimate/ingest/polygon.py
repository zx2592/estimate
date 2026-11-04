"""Polygon.io daily bars with an on-disk parquet cache.

Reads the API key from POLYGON_API_KEY. POLYGON_BASE_URL overrides the host
(Polygon also serves the same API under its newer Massive brand).
POLYGON_RPM sets the request budget per minute (default 5, the free-tier limit).
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd

from estimate.http import RateLimiter, get_json

_limiter: RateLimiter | None = None


def _config():
    global _limiter
    key = os.environ.get("POLYGON_API_KEY")
    if not key:
        raise RuntimeError("POLYGON_API_KEY is not set")
    if _limiter is None:
        _limiter = RateLimiter(float(os.environ.get("POLYGON_RPM", "5")))
    return os.environ.get("POLYGON_BASE_URL", "https://api.polygon.io").rstrip("/"), key


def daily_bars(ticker: str, start: dt.date, end: dt.date, cache_dir: Path) -> pd.DataFrame:
    """Split-adjusted daily OHLCV indexed by session date. Empty frame if Polygon has no data."""
    cache = cache_dir / f"{ticker}.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        if len(df) and df.index.min() <= pd.Timestamp(start) and df.index.max() >= pd.Timestamp(end) - pd.Timedelta(days=5):
            return df.loc[str(start):str(end)]
    base, key = _config()
    url = f"{base}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    d = get_json(url, dict(adjusted="true", sort="asc", limit=50000, apiKey=key), limiter=_limiter)
    rows = d.get("results") or []
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex([], name="date"))
    else:
        df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
        df = df.rename(columns=dict(o="open", h="high", l="low", c="close", v="volume")).set_index("date")[
            ["open", "high", "low", "close", "volume"]]
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    return df
