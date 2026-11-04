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
