"""
Clenow Score column on Full Universe Rankings: read from the CALCS sheet of Sharpe.py's
<universe>_rankings.xlsx (it is not part of the ranking result), shown right after Res Mom,
sortable, and simply absent when the workbook has no such column.
"""
import re

import openpyxl
import pandas as pd
import pytest

import momentum_lib as ml
from webapp.app import create_app
from webapp.core import config_store, rankings


def _workbook(path, rows, with_clenow=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CALCS"
    ws.append(["NSEAll . Full Calculations . title row"])
    ws.append(["RANK", "TICKER", "RES_MOM"] + (["CLENOW_SCORE"] if with_clenow else []))
    for r in rows:
        ws.append(list(r) if with_clenow else list(r)[:3])
    wb.save(path)


def test_loader_reads_the_calcs_sheet_and_handles_blanks(tmp_path):
    p = tmp_path / "TESTU_rankings.xlsx"
    _workbook(p, [(1, "AAA", 0.5, 1234.56), (2, "BBB", 0.1, None), (3, "CCC", 0.2, -12.0)])
    s, stamp = ml.load_clenow_scores_from_workbook(p)
    assert s["AAA"] == pytest.approx(1234.56) and s["CCC"] == pytest.approx(-12.0)
    assert pd.isna(s["BBB"])
    assert stamp is not None


@pytest.mark.parametrize("make", ["missing", "no_column", "no_sheet"])
def test_loader_returns_none_when_unavailable(tmp_path, make):
    p = tmp_path / "TESTU_rankings.xlsx"
    if make == "no_column":
        _workbook(p, [(1, "AAA", 0.5)], with_clenow=False)
    elif make == "no_sheet":
        openpyxl.Workbook().save(p)
    assert ml.load_clenow_scores_from_workbook(p) == (None, None)


def test_loader_picks_up_a_regenerated_workbook(tmp_path):
    import os
    p = tmp_path / "TESTU_rankings.xlsx"
    _workbook(p, [(1, "AAA", 0.5, 10.0)])
    assert ml.load_clenow_scores_from_workbook(p)[0]["AAA"] == pytest.approx(10.0)
    _workbook(p, [(1, "AAA", 0.5, 99.0)])
    os.utime(p, (os.path.getmtime(p) + 5, os.path.getmtime(p) + 5))   # mtime is the cache key
    assert ml.load_clenow_scores_from_workbook(p)[0]["AAA"] == pytest.approx(99.0)


@pytest.fixture(scope="module")
def bundle_cfg():
    cfg = config_store.load_config()
    return rankings.get_bundle(cfg), cfg


def test_column_sits_right_after_res_mom_and_only_when_available(bundle_cfg):
    bundle, _ = bundle_cfg
    base = rankings.full_display_columns(bundle)
    assert "CLENOW_SCORE" not in base
    with_c = rankings.full_display_columns(bundle, clenow=pd.Series(dtype=float))
    assert with_c.index("CLENOW_SCORE") == with_c.index("RES_MOM") + 1
    assert [c for c in with_c if c != "CLENOW_SCORE"] == base
    assert "CLENOW_SCORE" in rankings.sort_options(pd.Series(dtype=float))
    assert "CLENOW_SCORE" not in rankings.sort_options(None)


def test_rows_show_one_decimal_dash_for_missing_and_sort_descending(bundle_cfg):
    bundle, cfg = bundle_cfg
    tickers = list(bundle.result.index[:3])
    clenow = pd.Series({tickers[0]: 10.04, tickers[1]: 250.66})   # third ticker has no score
    cols = rankings.full_display_columns(bundle, clenow)
    i = cols.index("CLENOW_SCORE")

    rows = rankings.full_rankings_rows(bundle, "All", "RANK", 10, float(cfg["rel_dd_breach_threshold"]),
                                        search=",".join(tickers), clenow=clenow)
    by_ticker = {r["ticker"]: r["cells"][i] for r in rows}
    assert by_ticker[tickers[0]] == "10.0"
    assert by_ticker[tickers[1]] == "250.7"
    assert by_ticker[tickers[2]] == "—"

    rows = rankings.full_rankings_rows(bundle, "All", "CLENOW_SCORE", 10,
                                        float(cfg["rel_dd_breach_threshold"]),
                                        search=",".join(tickers), clenow=clenow)
    assert [r["ticker"] for r in rows][:2] == [tickers[1], tickers[0]]   # highest first, blanks last


def test_sort_by_clenow_falls_back_to_rank_without_data(bundle_cfg):
    bundle, cfg = bundle_cfg
    rows = rankings.full_rankings_rows(bundle, "All", "CLENOW_SCORE", 5,
                                        float(cfg["rel_dd_breach_threshold"]))
    assert len(rows) == 5
    ranks = [int(r["cells"][0]) for r in rows]
    assert ranks == sorted(ranks)


def test_http_table_has_clenow_header_after_res_mom_and_note(monkeypatch):
    series = pd.Series({"ZZZ": 1.0})
    monkeypatch.setattr(rankings, "load_clenow", lambda cfg: (series, pd.Timestamp("2026-11-19 09:45")))
    html = create_app().test_client().get("/rankings/full/table?top_n=10").data.decode()
    heads = re.findall(r"<th[^>]*>(.*?)</th>", html)
    assert heads.index("Clenow Score") == heads.index("Res Mom") + 1
    assert "re-run Sharpe.py to refresh" in html


def test_http_table_explains_when_clenow_is_unavailable(monkeypatch):
    monkeypatch.setattr(rankings, "load_clenow", lambda cfg: (None, None))
    html = create_app().test_client().get("/rankings/full/table?top_n=10").data.decode()
    assert "Clenow Score" not in re.findall(r"<th[^>]*>(.*?)</th>", html)
    assert "Clenow Score is not shown" in html
