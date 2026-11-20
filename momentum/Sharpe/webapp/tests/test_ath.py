"""
All-time high (closing basis): Sharpe.py backfills it once per ticker into ATH_cache.json and writes
ATH / ATH_DATE / PCT_FROM_ATH to the CALCS sheet; the dashboards only read that sheet.
"""
import datetime
import json

import numpy as np
import openpyxl
import pandas as pd
import pytest

import momentum_lib as ml

D = datetime.date


def _series(*pairs):
    return pd.Series({D.fromisoformat(d): v for d, v in pairs})


def test_ath_from_closes_picks_highest_positive_close_and_its_date():
    assert ml.ath_from_closes(_series(("2020-01-01", 100.0), ("2021-06-01", 250.5), ("2022-01-01", 90.0))) == (250.5, D(2021, 6, 1))
    assert ml.ath_from_closes(_series(("2020-01-01", np.nan), ("2020-01-02", 0.0))) is None
    assert ml.ath_from_closes(pd.Series(dtype=float)) is None


def _fetcher(history, unmatched=(), failed=None, log=None):
    def fetch(symbols):
        if log is not None:
            log.append(list(symbols))
        return ({s: history[s] for s in symbols if s in history},
                [s for s in symbols if s in unmatched], dict(failed or {}))
    return fetch


def test_backfill_fills_missing_tickers_only_and_marks_the_unmatched():
    cache = {"HAVE": {"ath": 10.0, "date": "2019-01-01", "full": True}}
    calls = []
    hist = {"NEW": _series(("2018-01-01", 40.0), ("2024-01-01", 55.0))}
    stats = ml.update_ath_cache(cache, ["HAVE", "NEW", "GONE"], _fetcher(hist, unmatched={"GONE"}, log=calls),
                                today=D(2026, 11, 20), log=lambda *_: None)
    assert calls == [["NEW", "GONE"]]                                  # HAVE is not fetched again
    assert cache["NEW"] == {"ath": 55.0, "date": "2024-01-01", "full": True}
    assert cache["GONE"] == {"tried": "2026-11-20"}
    assert cache["HAVE"]["ath"] == 10.0
    assert (stats["filled"], stats["no_data"], stats["aborted"]) == (1, 1, False)


def test_a_ticker_with_no_data_is_not_retried_for_a_week():
    cache = {"GONE": {"tried": "2026-11-18"}}
    calls = []
    ml.update_ath_cache(cache, ["GONE"], _fetcher({}, log=calls), today=D(2026, 11, 20), log=lambda *_: None)
    assert calls == []
    ml.update_ath_cache(cache, ["GONE"], _fetcher({}, unmatched={"GONE"}, log=calls), today=D(2026, 11, 26), log=lambda *_: None)
    assert calls == [["GONE"]]


def test_failed_calls_are_retried_next_run_and_a_circuit_break_stops_the_backfill():
    cache, calls, saves = {}, [], []
    hist = {"A": _series(("2024-01-01", 5.0))}
    fetch = _fetcher(hist, failed={"B": "rate limited", "C": "skipped: bulk fetch aborted early"}, log=calls)
    stats = ml.update_ath_cache(cache, ["A", "B", "C", "D"], fetch, chunk=3, today=D(2026, 11, 20),
                                on_chunk=lambda c: saves.append(dict(c)), log=lambda *_: None)
    assert stats["aborted"] is True and calls == [["A", "B", "C"]]    # D (second chunk) never attempted
    assert "B" not in cache and "C" not in cache                       # failures are left for the next run
    assert cache["A"]["full"] and len(saves) == 1                      # progress saved after the chunk


def test_table_pct_from_ath_uses_last_close_and_is_zero_at_a_new_high():
    dates = [D(2026, 11, 17), D(2026, 11, 18), D(2026, 11, 19)]
    prices = pd.DataFrame({dates[0]: [80.0, 100.0, 7.0], dates[1]: [90.0, 120.0, 8.0], dates[2]: [75.0, 130.0, np.nan]},
                          index=["DOWN", "NEWHIGH", "NOCACHE"])
    cache = {"DOWN": {"ath": 100.0, "date": "2024-05-01", "full": True},
             "NEWHIGH": {"ath": 125.0, "date": "2025-02-02", "full": True}}
    t = ml.compute_ath_table(prices, ["DOWN", "NEWHIGH", "NOCACHE"], cache)
    assert t.loc["DOWN", "PCT_FROM_ATH"] == pytest.approx(-25.0)
    assert t.loc["DOWN", "ATH_DATE"] == "2024-05-01"
    assert t.loc["NEWHIGH", "ATH"] == 130.0 and t.loc["NEWHIGH", "ATH_DATE"] == "2026-11-19"   # workbook close beat the cache
    assert t.loc["NEWHIGH", "PCT_FROM_ATH"] == pytest.approx(0.0)
    assert cache["NEWHIGH"]["ath"] == 130.0                                                   # kept for when it rolls out of the workbook
    assert np.isnan(t.loc["NOCACHE", "ATH"]) and np.isnan(t.loc["NOCACHE", "PCT_FROM_ATH"])   # no backfill: blank, not a 1-year high


def test_cache_round_trip_is_atomic_and_tolerates_junk(tmp_path):
    p = tmp_path / "ATH_cache.json"
    assert ml.load_ath_cache(p) == {}
    ml.save_ath_cache(p, {"A": {"ath": 1.5, "date": "2020-01-01", "full": True}})
    assert ml.load_ath_cache(p)["A"]["ath"] == 1.5 and not (tmp_path / "ATH_cache.json.tmp").exists()
    p.write_text("not json")
    assert ml.load_ath_cache(p) == {}
    p.write_text("[1, 2]")
    assert ml.load_ath_cache(p) == {}


def _calcs_workbook(path, with_ath=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CALCS"
    ws.append(["title row"])
    ws.append(["RANK", "TICKER"] + (["ATH", "ATH_DATE", "PCT_FROM_ATH"] if with_ath else []))
    ws.append([1, "AAA"] + ([200.0, "2025-03-04", -12.5] if with_ath else []))
    ws.append([2, "BBB"] + ([None, None, None] if with_ath else []))
    wb.save(path)


def test_workbook_reader_returns_the_three_columns_and_blanks_as_nan(tmp_path):
    p = tmp_path / "X_rankings.xlsx"
    _calcs_workbook(p)
    df, stamp = ml.load_ath_from_workbook(p)
    assert stamp is not None
    assert df.loc["AAA", "PCT_FROM_ATH"] == pytest.approx(-12.5) and df.loc["AAA", "ATH_DATE"] == "2025-03-04"
    assert np.isnan(df.loc["BBB", "PCT_FROM_ATH"])


def test_workbook_reader_returns_none_without_the_columns_or_the_file(tmp_path):
    p = tmp_path / "old_rankings.xlsx"
    _calcs_workbook(p, with_ath=False)
    assert ml.load_ath_from_workbook(p) == (None, None)
    assert ml.load_ath_from_workbook(tmp_path / "missing.xlsx") == (None, None)


# ── webapp display (the dashboards only read the CALCS columns) ───────────────
import re

from webapp.app import create_app
from webapp.core import config_store, rankings


@pytest.fixture(scope="module")
def bundle_cfg():
    cfg = config_store.load_config()
    return rankings.get_bundle(cfg), cfg


def _ath_frame(values):
    return pd.DataFrame({"ATH": [200.0] * len(values), "ATH_DATE": ["2025-03-04"] * len(values),
                         "PCT_FROM_ATH": list(values.values())}, index=list(values))


def test_column_sits_right_after_52h_and_only_when_available(bundle_cfg):
    bundle, _ = bundle_cfg
    base = rankings.full_display_columns(bundle)
    assert "PCT_FROM_ATH" not in base and "PCT_FROM_ATH" not in rankings.sort_options()
    with_ath = rankings.full_display_columns(bundle, ath=_ath_frame({"X": 1.0}))
    assert with_ath.index("PCT_FROM_ATH") == with_ath.index("PCT_FROM_52H") + 1
    assert [c for c in with_ath if c != "PCT_FROM_ATH"] == base
    assert "PCT_FROM_ATH" in rankings.sort_options(ath=_ath_frame({"X": 1.0}))


def test_rows_show_signed_one_decimal_tooltip_dash_for_missing_and_sort_closest_to_ath_first(bundle_cfg):
    bundle, cfg = bundle_cfg
    tickers = list(bundle.result.index[:3])
    ath = _ath_frame({tickers[0]: -18.44, tickers[1]: 0.0, tickers[2]: float("nan")})
    cols = rankings.full_display_columns(bundle, ath=ath)
    i = cols.index("PCT_FROM_ATH")
    kw = dict(search=",".join(tickers), ath=ath)
    rows = {r["ticker"]: r for r in rankings.full_rankings_rows(
        bundle, "All", "RANK", 10, float(cfg["rel_dd_breach_threshold"]), **kw)}
    assert [rows[t]["cells"][i] for t in tickers] == ["-18.4", "0.0", "—"]
    assert rows[tickers[0]]["titles"][i] == "ATH Rs 200.00 on 04-Mar-25"
    ordered = rankings.full_rankings_rows(bundle, "All", "PCT_FROM_ATH", 10,
                                          float(cfg["rel_dd_breach_threshold"]), **kw)
    assert [r["ticker"] for r in ordered][:2] == [tickers[1], tickers[0]]   # 0.0 (at ATH) before -18.4; blank last


def test_http_table_header_and_note(monkeypatch):
    ath = _ath_frame({"ZZZ": -5.0})
    monkeypatch.setattr(rankings, "load_ath", lambda cfg: (ath, pd.Timestamp("2026-11-19 09:45")))
    html = create_app().test_client().get("/rankings/full/table?top_n=10").data.decode()
    heads = re.findall(r"<th[^>]*>(.*?)</th>", html)
    assert heads.index("% From ATH") == heads.index("% From 52H") + 1
    assert "highest daily close on record" in html


def test_http_table_explains_when_ath_is_unavailable(monkeypatch):
    monkeypatch.setattr(rankings, "load_ath", lambda cfg: (None, None))
    html = create_app().test_client().get("/rankings/full/table?top_n=10").data.decode()
    assert "% From ATH" not in re.findall(r"<th[^>]*>(.*?)</th>", html)
    assert "% From ATH is not shown" in html


def test_active_holdings_table_has_no_ath_column(monkeypatch, bundle_cfg):
    bundle, _ = bundle_cfg
    frame = _ath_frame({t: -12.34 for t in bundle.result.index})
    monkeypatch.setattr(rankings, "load_ath", lambda cfg: (frame, pd.Timestamp("2026-11-19")))
    html = create_app().test_client().get("/").data.decode()
    assert "% From ATH" not in html[html.index('id="holdings-content"'):]


# ── Full Rankings: "Current holdings only" ────────────────────────────────────

def test_held_filter_keeps_only_held_names_ignores_top_n_and_combines_with_search(bundle_cfg):
    bundle, cfg = bundle_cfg
    thr = float(cfg["rel_dd_breach_threshold"])
    tickers = list(bundle.result.index)
    held = {tickers[0], tickers[400], "NOT_IN_UNIVERSE"}              # one beyond any top-100 cut
    rows = rankings.full_rankings_rows(bundle, "All", "RANK", 10, thr, held=held)
    assert {r["ticker"] for r in rows} == {tickers[0], tickers[400]}  # top_n=10 did not cut the second
    rows = rankings.full_rankings_rows(bundle, "All", "RANK", 10, thr, held=held, search=tickers[400])
    assert [r["ticker"] for r in rows] == [tickers[400]]
    assert rankings.full_rankings_rows(bundle, "All", "RANK", 10, thr, held=set()) == []
    assert len(rankings.full_rankings_rows(bundle, "All", "RANK", 10, thr)) == 10   # no filter: unchanged


def test_http_checkbox_and_filtered_table(monkeypatch):
    from webapp.core import actions
    client = create_app().test_client()
    page = client.get("/rankings/full").data.decode()
    assert re.search(r'<input type="checkbox" name="held" value="1">', page) and "Current holdings only" in page

    held = {"DIVISLAB": {}, "NOT_RANKED": {}}
    monkeypatch.setattr(actions, "current_holdings", lambda universe: held)
    html = client.get("/rankings/full/table?top_n=10&held=1").data.decode()
    tickers = re.findall(r"<tr class=\"[^\"]*\">\s*<td>([A-Z0-9_&-]+)</td>", html)
    assert tickers == ["DIVISLAB"]                                   # NOT_RANKED has no row
    assert "Current holdings only: " in html and "2 held names" in html
    assert "1 held name not in this universe" in html

    html = client.get("/rankings/full/table?top_n=10").data.decode()          # box unticked
    assert "Current holdings only: " not in html
