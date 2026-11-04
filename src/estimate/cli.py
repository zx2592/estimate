"""Research pipeline entry point.

    python -m estimate.cli events         # download Polymarket earnings events, build event table
    python -m estimate.cli fetch-poly     # trades + hourly price history per settled market
    python -m estimate.cli panel          # release time, entry point, prediction-market features
    python -m estimate.cli h1             # calibration vs base rates at the proper entry time
    python -m estimate.cli fetch-prices   # Yahoo Finance daily bars for every ticker + SPY
    python -m estimate.cli h2             # direction / magnitude tests
    python -m estimate.cli upcoming       # open EPS markets reporting in the next days
    python -m estimate.cli snapshot-options   # IBKR chain snapshot for events entering today (run ~15:45 ET)
    python -m estimate.cli ibkr-vol-history   # IBKR 30-day IV / HV history for every ticker
    python -m estimate.cli h5             # implied vs realized earnings move from IBKR IV30
    python -m estimate.cli h7             # put-skew test on collected snapshots
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


def cmd_upcoming(args):
    from estimate.events import build_upcoming
    raw = pm.fetch_earnings_events(closed=False)
    (DATA / "raw").mkdir(parents=True, exist_ok=True)
    (DATA / "raw" / "poly_earn_events_closed_false.json").write_text(json.dumps(raw))
    up = build_upcoming(raw, dt.datetime.now(ET).date(), args.days)
    up.to_parquet(DATA / "upcoming.parquet")
    cols = ["ticker", "basis", "est_date", "timing", "entry_date", "reaction_date", "volume"]
    print(up[cols].to_string(index=False) if len(up) else "no upcoming EPS markets")


def cmd_snapshot_options(args):
    from estimate.ingest import ibkr, yahoo
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(ET).date()
    up = pd.read_parquet(DATA / "upcoming.parquet")
    todo = up[up.entry_date == today]
    out_dir = DATA / "options" / "snapshots" / str(today)
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = todo.drop_duplicates("ticker")  # GAAP and non-GAAP markets share one chain
    print(f"{len(todo)} tickers enter on {today}; source={args.source}")
    ib = ibkr.connect(args.host, args.port, args.client_id, args.market_data_type) if args.source == "ibkr" else None
    try:
        for ev in todo.itertuples():
            f = out_dir / f"{ev.ticker}.parquet"
            if f.exists():
                continue
            try:
                if ib is not None:
                    df = ibkr.snapshot_chain(ib, ev.ticker, ev.reaction_date)
                else:
                    df = yahoo.snapshot_chain(ev.ticker, ev.reaction_date)
                df.assign(source=args.source).to_parquet(f)
                print(f"  {ev.ticker}: {len(df)} contracts, {df.iv.notna().mean():.0%} with IV")
            except Exception as err:  # noqa: BLE001
                print(f"  {ev.ticker}: FAILED {err}")
    finally:
        if ib is not None:
            ib.disconnect()


def cmd_ibkr_vol_history(args):
    from estimate.ingest import ibkr
    tickers = sorted(set(pd.read_parquet(DATA / "events.parquet").ticker))
    out_dir = DATA / "ibkr" / "vol"
    out_dir.mkdir(parents=True, exist_ok=True)
    ib = ibkr.connect(args.host, args.port, args.client_id, args.market_data_type)
    try:
        for i, t in enumerate(tickers, 1):
            f = out_dir / f"{t}.parquet"
            if not f.exists():
                try:
                    df = ibkr.underlying_vol_history(ib, t)
                    if len(df):
                        df.to_parquet(f)
                    else:
                        print(f"  {t}: no data")
                except Exception as err:  # noqa: BLE001
                    print(f"  {t}: FAILED {err}")
            if i % 25 == 0:
                print(f"  {i}/{len(tickers)}", flush=True)
    finally:
        ib.disconnect()


def cmd_h5(_):
    from estimate.research.h5 import report
    report(pd.read_parquet(DATA / "panel.parquet"), PRICES, DATA / "ibkr" / "vol")


def cmd_h7(_):
    from estimate.research.h7 import evaluate, load_snapshot_features, report
    panel = pd.read_parquet(DATA / "panel.parquet")
    feats = load_snapshot_features(DATA / "options" / "snapshots", panel)
    report(evaluate(panel, feats, PRICES) if len(feats) else pd.DataFrame())


def _ibkr_args(p):
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497, help="TWS paper 7497, live 7496; Gateway 4002 / 4001")
    p.add_argument("--client-id", type=int, default=17)
    p.add_argument("--market-data-type", type=int, default=3, help="1 live, 3 delayed, 4 delayed-frozen")


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
    u = sub.add_parser("upcoming")
    u.add_argument("--days", type=int, default=10)
    u.set_defaults(fn=cmd_upcoming)
    so = sub.add_parser("snapshot-options")
    so.add_argument("--date", help="entry date, default today (ET)")
    so.add_argument("--source", choices=["ibkr", "yahoo"], default="ibkr")
    _ibkr_args(so)
    so.set_defaults(fn=cmd_snapshot_options)
    vh = sub.add_parser("ibkr-vol-history")
    _ibkr_args(vh)
    vh.set_defaults(fn=cmd_ibkr_vol_history)
    sub.add_parser("h5").set_defaults(fn=cmd_h5)
    sub.add_parser("h7").set_defaults(fn=cmd_h7)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
