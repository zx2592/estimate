"""Preliminary H1 test: calibration of Polymarket EPS-beat markets vs a base rate.

Reads the closed-events file written by poly_fetch_events.py, pulls hourly price
history for the final 4 days of each settled EPS market, and compares the Brier
score of the market price at T-72h / T-24h / T-6h (T = market endDate) with an
out-of-sample base rate (beat rate of all previous quarters).

Usage: python scripts/phase0/poly_calibration.py DATA_DIR
"""
import collections, datetime as dt, json, re, statistics, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CLOB = "https://clob.polymarket.com"
UA = {"User-Agent": "estimate-research/0.1"}
SLUG = re.compile(r"([a-z0-9]+)-quarterly-earnings-(gaap|nongaap)-eps-(\d\d)-(\d\d)-(\d{4})")


def get(url):
    for attempt in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception:  # noqa: BLE001
            time.sleep(1 + 2 ** attempt)
    return {}


def ts(iso):
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def jobs(events):
    for e in events:
        m0 = SLUG.match(e["slug"])
        if not m0:
            continue
        m = e["markets"][0]
        try:
            res, tok = json.loads(m["outcomePrices"]), json.loads(m["clobTokenIds"])[0]
        except Exception:  # noqa: BLE001
            continue
        if res not in (["1", "0"], ["0", "1"]):
            continue
        yield dict(slug=e["slug"], tic=m0.group(1), basis=m0.group(2), end=ts(m["endDate"]),
                   yes=int(res[0] == "1"), tok=tok, vol=float(m.get("volumeNum") or 0))


def add_prices(j):
    # prices-history rejects windows that are too long; 4 days is accepted
    q = dict(market=j["tok"], startTs=j["end"] - 4 * 86400, endTs=j["end"], fidelity=60)
    hist = get(f"{CLOB}/prices-history?{urllib.parse.urlencode(q)}").get("history", [])

    def at(t):
        before = [p for p in hist if p["t"] <= t]
        return before[-1]["p"] if before else None

    j.update(p72=at(j["end"] - 72 * 3600), p24=at(j["end"] - 24 * 3600), p6=at(j["end"] - 6 * 3600))
    return j


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def quarter(t):
    d = dt.datetime.fromtimestamp(t, dt.UTC)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def report(rows):
    rows = [r for r in rows if r["p24"] is not None]
    for r in rows:
        r["q"] = quarter(r["end"])
    qs = sorted({r["q"] for r in rows})
    prior = {}
    for i, q in enumerate(qs):
        prev = [r["yes"] for r in rows if r["q"] in qs[:i]]
        prior[q] = sum(prev) / len(prev) if prev else 0.75
    print(f"n={len(rows)} beat rate={sum(r['yes'] for r in rows) / len(rows):.3f}")
    for h in ("p72", "p24", "p6"):
        sub = [r for r in rows if r[h] is not None]
        bm = brier([r[h] for r in sub], [r["yes"] for r in sub])
        bb = brier([prior[r["q"]] for r in sub], [r["yes"] for r in sub])
        print(f"{h}: Brier market={bm:.4f} base={bb:.4f} BSS={1 - bm / bb:+.3f}")
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[min(int(r["p24"] * 10), 9)].append(r)
    print("p24 bucket   n  mean_p  actual")
    for k in sorted(buckets):
        v = buckets[k]
        print(f"{k / 10:.1f}-{(k + 1) / 10:.1f} {len(v):5d}  {sum(r['p24'] for r in v) / len(v):.3f}  "
              f"{sum(r['yes'] for r in v) / len(v):.3f}")
    print("quarter  n  beat  brier_mkt  brier_base  median_vol")
    for q in qs:
        v = [r for r in rows if r["q"] == q]
        print(q, len(v), f"{sum(r['yes'] for r in v) / len(v):.3f}",
              f"{brier([r['p24'] for r in v], [r['yes'] for r in v]):.4f}",
              f"{brier([prior[q]] * len(v), [r['yes'] for r in v]):.4f}",
              round(statistics.median(r["vol"] for r in v)))


if __name__ == "__main__":
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    events = json.loads((data / "poly_earn_events_closed_true.json").read_text())
    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(add_prices, list(jobs(events))))
    (data / "poly_calibration_rows.json").write_text(json.dumps(rows))
    report(rows)
