"""Co-relation Matrix tab: shared corr_matrix module + Flask route."""
import numpy as np
import pandas as pd
import pytest

import corr_matrix


def _prices(n=300, seed=0, short_ticker=None):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n)
    common = rng.normal(0, 0.01, n)
    data = {t: 100 * np.exp(np.cumsum(common * b + rng.normal(0, 0.01, n)))
            for t, b in {"BBB": 1.0, "AAA": 1.0, "CCC": -1.0}.items()}
    df = pd.DataFrame(data, index=dates).T
    if short_ticker:
        df.loc[short_ticker, df.columns[:-10]] = np.nan   # only 10 days of history
    return df


def test_tickers_are_alphabetical_and_matrix_is_symmetric_in_value():
    m = corr_matrix.compute(_prices(), ["CCC", "AAA", "BBB"])
    assert m["tickers"] == ["AAA", "BBB", "CCC"]
    assert np.allclose(m["F"], m["F"].T, equal_nan=True)
    assert np.allclose(np.diag(m["F"]), 1.0)
    assert m["F"][0, 1] > 0.2 and m["F"][0, 2] < -0.2   # shared factor, opposite sign for CCC
    assert np.allclose(m["D"], m["R"] - m["F"], equal_nan=True)


def test_empty_and_single_holding_give_messages_not_charts():
    html, msg = corr_matrix.render(_prices(), [])
    assert html is None and "No open positions" in msg
    html, msg = corr_matrix.render(_prices(), ["AAA"])
    assert html is None and "at least two" in msg


def test_unpriced_holding_is_listed_not_dropped_silently():
    html, msg = corr_matrix.render(_prices(), ["AAA", "BBB", "ZZZ"])
    assert msg is None and "No price data for: ZZZ" in html


def test_short_history_is_greyed_and_noted():
    m = corr_matrix.compute(_prices(short_ticker="CCC"), ["AAA", "BBB", "CCC"])
    assert m["short"] == ["CCC"]
    assert np.isnan(m["F"][0, 2])                       # < MIN_OBS overlap -> n/a
    html = corr_matrix.build_html(m)
    assert "cm-short" in html and "&dagger;" in html and "cm-na" in html


def test_colour_scales_match_the_approved_heatmap_end_points():
    assert corr_matrix.corr_color(1.0) == "rgb(5,48,97)"      # RdBu dark blue
    assert corr_matrix.corr_color(-1.0) == "rgb(103,0,31)"    # RdBu dark red
    assert corr_matrix.change_color(0.4) == "rgb(127,59,8)"   # PuOr_r brown/orange end
    assert corr_matrix.change_color(-0.4) == "rgb(45,0,75)"   # PuOr_r purple end


@pytest.fixture(scope="module")
def client():
    from webapp.app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_correlation_tab_is_after_performance_tracker_and_renders(client):
    html = client.get("/correlation").data.decode()
    assert html.index("Performance Tracker") < html.index("Co-relation Matrix")
    assert 'class="active">Co-relation Matrix' in html
    assert "cm-grid" in html or "No open positions" in html
