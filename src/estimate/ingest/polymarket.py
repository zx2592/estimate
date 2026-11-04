"""Polymarket public data: earnings events (Gamma), price history (CLOB), trades (Data API v2)."""
from __future__ import annotations

import time

from estimate.http import get_json

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com/v2"
EARNINGS_TAG_ID = 1013
MAX_HISTORY_WINDOW = 14 * 86400  # prices-history rejects longer startTs/endTs windows


def fetch_earnings_events(closed: bool) -> list[dict]:
    """All events tagged 'earnings'. Gamma caps pages at 100, so page until empty."""
    out, offset = [], 0
    while True:
        page = get_json(f"{GAMMA}/events", dict(tag_id=EARNINGS_TAG_ID, closed=str(closed).lower(),
                                                 limit=100, offset=offset))
        if not page:
            return out
        out += page
        offset += len(page)
        time.sleep(0.25)


def fetch_price_history(token_id: str, start_ts: int, end_ts: int, fidelity_min: int = 60) -> list[dict]:
    """[{t, p}] for one outcome token. `interval=max` returns nothing for settled markets."""
    out = []
    t = start_ts
    while t < end_ts:
        stop = min(end_ts, t + MAX_HISTORY_WINDOW)
        d = get_json(f"{CLOB}/prices-history", dict(market=token_id, startTs=t, endTs=stop, fidelity=fidelity_min))
        out += d.get("history", [])
        t = stop
    seen, uniq = set(), []
    for p in sorted(out, key=lambda x: x["t"]):
        if p["t"] not in seen:
            seen.add(p["t"])
            uniq.append(p)
    return uniq


def fetch_trades(condition_id: str, max_pages: int = 200) -> list[dict]:
    """All public trades for a market (both outcome tokens), newest first."""
    out, cursor = [], None
    for _ in range(max_pages):
        d = get_json(f"{DATA}/trades", dict(condition=condition_id, limit=1000, cursor=cursor))
        out += d.get("data", [])
        pag = d.get("pagination") or {}
        cursor = pag.get("next_cursor")
        if not pag.get("has_more") or not cursor:
            break
    keep = ("side", "outcome_index", "size", "price", "timestamp", "proxy_wallet", "transaction_hash")
    return [{k: t.get(k) for k in keep} for t in out]
