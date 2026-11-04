"""Research pipeline entry point.

    python -m estimate.cli events         # download Polymarket earnings events, build event table
    python -m estimate.cli fetch-poly     # trades + hourly price history per settled market
    python -m estimate.cli panel          # release time, entry point, prediction-market features
    python -m estimate.cli h1             # calibration vs base rates at the proper entry time
    python -m estimate.cli fetch-prices   # Yahoo Finance daily bars for every ticker + SPY
    python -m estimate.cli h2             # direction / magnitude tests
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from estimate.calendar import ET
from estimate.events import build_events
from estimate.ingest import polymarket as pm

DATA = Path("data")
PRICES = DATA / "prices" / "daily"


def cmd_events(_):
    raw = DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for closed in (True, False):
        evs = pm.fetch_earnings_events(closed)
        (raw / f"poly_earn_events_closed_{str(closed).lower()}.json").write_text(json.dumps(evs))
        print(f"closed={closed}: {len(evs)} events")
    evs = json.loads((raw / "poly_earn_events_closed_true.json").read_text())
    df = build_events(evs)
    df = df[df.resolved].reset_index(drop=True)
    df.to_parquet(DATA / "events.parquet")
    print(f"settled EPS markets: {len(df)}, tickers: {df.ticker.nunique()}")


def _fetch_one(ev) -> str:
    tdir, hdir = DATA / "poly" / "trades", DATA / "poly" / "hist"
    tpath, hpath = tdir / f"{ev.condition_id}.json", hdir / f"{ev.yes_token}.json"
    try:
        if not tpath.exists():
            tpath.write_text(json.dumps(pm.fetch_trades(ev.condition_id)))
        if not hpath.exists():
            start = int(dt.datetime.combine(ev.est_date - dt.timedelta(days=8), dt.time(0), ET).timestamp())
            end = int(dt.datetime.combine(ev.est_date + dt.timedelta(days=3), dt.time(0), ET).timestamp())
            hpath.write_text(json.dumps(pm.fetch_price_history(ev.yes_token, start, end, 60)))
        return "ok"
    except Exception as err:  # noqa: BLE001
        return f"{ev.slug}: {err}"


def cmd_fetch_poly(args):
    ev = pd.read_parquet(DATA / "events.parquet")
    (DATA / "poly" / "trades").mkdir(parents=True, exist_ok=True)
    (DATA / "poly" / "hist").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(args.workers) as ex:
        res = list(ex.map(_fetch_one, ev.itertuples(index=False)))
    errs = [r for r in res if r != "ok"]
    print(f"fetched {len(res) - len(errs)}/{len(res)}; errors: {len(errs)}")
    for e in errs[:10]:
        print("  ", e)


def cmd_panel(_):
    from estimate.research.panel import build_panel
    ev = pd.read_parquet(DATA / "events.parquet")
    p = build_panel(ev, DATA)
    p.to_parquet(DATA / "panel.parquet")
    print(f"panel rows: {len(p)}, with entry price: {p.p0.notna().sum()}")


def cmd_h1(_):
    from estimate.research.h1 import report
    report(pd.read_parquet(DATA / "panel.parquet"))


def cmd_fetch_prices(_):
    from estimate.ingest.yahoo import fetch_daily
    p = pd.read_parquet(DATA / "panel.parquet").dropna(subset=["entry_date"])
    start = min(p.entry_date) - dt.timedelta(days=10)
    end = min(dt.date.today(), max(p.reaction_date) + dt.timedelta(days=10))
    tickers = ["SPY"] + sorted(set(p.ticker))
    print(f"{len(tickers)} tickers, {start}..{end}")
    missing = fetch_daily(tickers, start, end, PRICES)
    print(f"done; no data for {len(missing)}: {missing[:30]}")


def cmd_h2(_):
    from estimate.research.h2 import report
    report(pd.read_parquet(DATA / "panel.parquet"), PRICES)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="estimate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("events").set_defaults(fn=cmd_events)
    f = sub.add_parser("fetch-poly")
    f.add_argument("--workers", type=int, default=4)
    f.set_defaults(fn=cmd_fetch_poly)
    sub.add_parser("panel").set_defaults(fn=cmd_panel)
    sub.add_parser("h1").set_defaults(fn=cmd_h1)
    sub.add_parser("fetch-prices").set_defaults(fn=cmd_fetch_prices)
    sub.add_parser("h2").set_defaults(fn=cmd_h2)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
