"""Ticker search on the Full Rankings tab."""
import re

import pytest

from webapp.app import create_app
from webapp.core import config_store, rankings


@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


@pytest.fixture(scope="module")
def bundle():
    return rankings.get_bundle(config_store.load_config())


def _tickers(html):
    return re.findall(r"<tr class=\"[^\"]*\">\s*<td>([^<]+)</td>", html)


def _get(client, **params):
    r = client.get("/rankings/full/table", query_string=params)
    assert r.status_code == 200
    return r.data.decode()


def test_page_has_a_search_box_wired_to_the_table(client):
    html = client.get("/rankings/full").data.decode()
    assert 'id="ticker-search"' in html and 'name="q"' in html
    assert "input from:#ticker-search" in html and "submit" in html  # live typing + Enter doesn't navigate


def test_search_is_case_insensitive_and_partial(client, bundle):
    ticker = rankings._visible_and_hidden(bundle, 5)[0][0]
    for q in (ticker, ticker.lower(), ticker[:3].lower()):
        assert ticker in _tickers(_get(client, q=q, top_n=10)), q
    assert all("SB" in t for t in _tickers(_get(client, q="sb")))


def test_search_finds_a_ticker_outside_the_show_top_n_cap(client, bundle):
    far = [t for t, r in bundle.result["RANK"].items() if r == r and r > 300][0]
    assert far not in _tickers(_get(client, top_n=10))               # not in the top-10 table
    assert _tickers(_get(client, q=far, top_n=10)) == [far] or far in _tickers(_get(client, q=far, top_n=10))


def test_several_terms_separated_by_comma_or_space(client, bundle):
    a, b = [t for t in bundle.result.index if bundle.result.loc[t, "RANK"] == bundle.result.loc[t, "RANK"]][:2]
    for q in (f"{a},{b}", f"{a} {b}", f" {a} , {b.lower()} "):
        got = _tickers(_get(client, q=q))
        assert a in got and b in got, q


def test_no_match_says_so_and_explains_the_eligibility_filter(client):
    html = _get(client, q="ZZZZNOPE")
    assert "No tickers match" in html and _tickers(html) == []
    html = _get(client, q="ZZZZNOPE", eligibility="Eligible only")
    assert "try “All”" in html


def test_search_still_honours_the_eligibility_dropdown(client, bundle):
    dq = [t for t in bundle.result.index if bundle.result.loc[t, "PCT_FROM_52H"] < rankings.ELIGIBLE_52H_FLOOR][0]
    assert dq in _tickers(_get(client, q=dq, eligibility="All"))
    assert dq not in _tickers(_get(client, q=dq, eligibility="Eligible only"))


def test_without_a_search_the_table_is_unchanged(client):
    html = _get(client, top_n=40)
    assert len(_tickers(html)) == 40 and "🔎" not in html


def test_search_terms_helper():
    assert rankings.search_terms(" sbc, hfcl  tbz ,") == ["SBC", "HFCL", "TBZ"]
    assert rankings.search_terms("") == [] and rankings.search_terms(None) == []


# ── shared helpers + the Streamlit dashboards ─────────────────────────────────

def test_shared_helpers_in_momentum_lib():
    import pandas as pd
    import momentum_lib as ml
    assert ml.ticker_search_terms(" sbc, hfcl  tbz ,") == ["SBC", "HFCL", "TBZ"]
    assert ml.ticker_search_terms("") == [] and ml.ticker_search_terms(None) == []
    df = pd.DataFrame({"TICKER": ["SBC", "SBIN", "HFCL", "TBZ"], "x": [1, 2, 3, 4]})
    assert list(ml.filter_tickers_by_search(df, ml.ticker_search_terms("sb"))["TICKER"]) == ["SBC", "SBIN"]
    assert list(ml.filter_tickers_by_search(df, ml.ticker_search_terms("hfcl tbz"))["TICKER"]) == ["HFCL", "TBZ"]
    assert ml.filter_tickers_by_search(df, []) is df  # no terms -> untouched


def test_webapp_and_dashboards_share_one_implementation():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert rankings.search_terms("a, b") == ["A", "B"]
    for name in ("sharpe_dashboard.py", "sharpe_dashboard_dhan.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert 'key="full_rank_search"' in src and "ml.ticker_search_terms(" in src, name
        assert "ml.filter_tickers_by_search(calcs_df, _search_terms)" in src, name
        # top N must not be applied while searching, and the slider is disabled then
        assert "if not _search_terms:" in src and "disabled=bool(_search_terms)" in src, name
