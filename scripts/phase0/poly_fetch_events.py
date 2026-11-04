"""Download all Polymarket events tagged 'earnings' (tag id 1013), open and closed.

Usage: python scripts/phase0/poly_fetch_events.py OUT_DIR
"""
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

GAMMA = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "estimate-research/0.1"}  # Cloudflare blocks the default Python UA
EARNINGS_TAG_ID = 1013


def get(path, **q):
    url = f"{GAMMA}{path}?{urllib.parse.urlencode(q)}"
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))
        except Exception as err:  # noqa: BLE001
            last = err
            time.sleep(2 ** attempt)
    raise last


def fetch(closed: bool):
    out, offset = [], 0
    while True:
        page = get("/events", tag_id=EARNINGS_TAG_ID, closed=str(closed).lower(), limit=100, offset=offset)
        if not page:  # API caps page size at 100, so page until empty
            return out
        out += page
        offset += len(page)
        time.sleep(0.25)


if __name__ == "__main__":
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    out_dir.mkdir(parents=True, exist_ok=True)
    for closed in (False, True):
        events = fetch(closed)
        (out_dir / f"poly_earn_events_closed_{str(closed).lower()}.json").write_text(json.dumps(events))
        print(f"closed={closed}: {len(events)} events")
