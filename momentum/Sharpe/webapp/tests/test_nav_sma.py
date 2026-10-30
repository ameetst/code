"""50-trading-day SMA of the portfolio NAV: the shared helper, what is saved in the NAV log,
and what the webapp / Streamlit chart plot."""
import datetime
import json
from pathlib import Path

import pytest

import momentum_lib as ml
from webapp import settings
from webapp.core import performance

ROOT = Path(__file__).resolve().parents[2]
START = datetime.date(2026, 1, 5)


def _entries(navs, *, price_dates=None):
    """One entry per NAV on consecutive calendar days; price_dates defaults to the entry date."""
    out = []
    for i, nav in enumerate(navs):
        d = (START + datetime.timedelta(days=i)).isoformat()
        out.append({"date": d, "price_date": (price_dates[i] if price_dates else d),
                    "portfolio_nav": nav, "benchmark_nav": 100.0, "portfolio_ret": 0.0,
                    "benchmark_ret": 0.0, "n_held": 10, "invested_frac": 0.5, "qty_weighted": True})
    return out


# ── helper ────────────────────────────────────────────────────────────────────

def test_sma_is_none_until_50_trading_days_then_the_plain_mean():
    navs = [100.0 + i for i in range(60)]
    sma = ml.nav_sma(_entries(navs))
    assert all(v is None for v in sma[:49])
    assert sma[49] == pytest.approx(sum(navs[:50]) / 50)
    assert sma[59] == pytest.approx(sum(navs[10:60]) / 50)  # window slides


def test_repeated_price_dates_count_as_one_trading_day_with_the_last_nav():
    # 49 distinct days, then a weekend re-run repeating day 48's price_date: still 49 days -> None,
    # and the repeat replaces that day's NAV instead of adding a 50th observation.
    navs = [100.0 + i for i in range(49)]
    ents = _entries(navs)
    ents.append({**ents[-1], "date": (START + datetime.timedelta(days=49)).isoformat(),
                 "portfolio_nav": 200.0})  # same price_date as the previous entry
    assert ml.nav_sma(ents)[-1] is None
    # one more genuine trading day -> 50 distinct days; day 48 counts with NAV 200, not 148
    ents.append({**ents[-1], "date": (START + datetime.timedelta(days=50)).isoformat(),
                 "price_date": "2026-03-01", "portfolio_nav": 150.0})
    expected = (sum(navs[:48]) + 200.0 + 150.0) / 50
    assert ml.nav_sma(ents)[-1] == pytest.approx(expected, abs=1e-4)


def test_input_order_is_preserved_and_missing_price_date_falls_back_to_date():
    ents = _entries([100.0 + i for i in range(50)])
    for e in ents:
        del e["price_date"]
    shuffled = list(reversed(ents))
    sma = ml.nav_sma(shuffled)
    assert sma[0] == pytest.approx(sum(100.0 + i for i in range(50)) / 50)  # newest entry first
    assert all(v is None for v in sma[1:])


def test_resolved_prefers_the_saved_value_including_null_and_computes_when_the_key_is_absent():
    ents = _entries([100.0 + i for i in range(52)])
    ents[50][ml.NAV_SMA_KEY] = 123.0
    ents[51][ml.NAV_SMA_KEY] = None  # explicitly "not enough history" -- respected as saved
    res = ml.nav_sma_resolved(ents)
    assert res[50] == 123.0 and res[51] is None
    assert res[49] == ml.nav_sma(ents)[49]  # key absent -> computed


# ── what is saved in the NAV log ──────────────────────────────────────────────

def test_saved_sma_matches_the_helper_on_every_equity_history_file():
    files = [f for f in Path(settings.DATA_DIR).glob("*_equity_history.json")]
    checked = 0
    for f in files:
        ents = json.loads(f.read_text())
        if not any(ml.NAV_SMA_KEY in e for e in ents):
            continue  # a data copy that predates the field
        checked += 1
        assert all(ml.NAV_SMA_KEY in e for e in ents), f.name
        want = ml.nav_sma(ents)
        for e, w in zip(ents, want):
            assert (e[ml.NAV_SMA_KEY] is None and w is None) or abs(e[ml.NAV_SMA_KEY] - w) < 5e-5, (f.name, e["date"])


def test_sharpe_py_saves_the_sma_with_each_new_entry():
    src = (ROOT / "Sharpe.py").read_text(encoding="utf-8")
    assert "new_entry[ml.NAV_SMA_KEY] = ml.nav_sma(eq_history + [new_entry])[-1]" in src
    assert src.index("new_entry[ml.NAV_SMA_KEY]") < src.index("eq_history.append(new_entry)")


# ── webapp ────────────────────────────────────────────────────────────────────

def test_webapp_view_carries_the_sma_in_records_and_the_detail_table():
    ents = _entries([100.0 + i * 0.1 for i in range(60)])
    view = performance.build_view(ents)
    recs = json.loads(view["records_json"].replace("<\\/", "</"))
    assert recs[48]["sma"] is None and recs[49]["sma"] is not None
    assert view["detail_rows"][48]["sma"] is None and view["detail_rows"][59]["sma"] == recs[59]["sma"] == pytest.approx(
        ml.nav_sma(ents)[59], abs=1e-3)


def test_performance_page_shows_the_sma_column_and_data():
    from webapp.app import create_app
    html = create_app().test_client().get("/performance").get_data(as_text=True)
    assert "NAV 50D SMA" in html
    assert '"sma":' in html


# ── Streamlit chart + dashboards ──────────────────────────────────────────────

def _chart_html(monkeypatch, with_sma):
    import pandas as pd
    import equity_chart
    seen = {}
    monkeypatch.setattr(equity_chart.components, "html", lambda html, **k: seen.setdefault("html", html))
    df = pd.DataFrame(_entries([100.0 + i * 0.1 for i in range(60)]))
    df["date"] = pd.to_datetime(df["date"])
    if with_sma:
        df["nav_sma50"] = ml.nav_sma(_entries([100.0 + i * 0.1 for i in range(60)]))
    equity_chart.render_equity_curve(df)
    return seen["html"]


def test_streamlit_chart_plots_the_sma_line_when_values_exist(monkeypatch):
    html = _chart_html(monkeypatch, True)
    assert "Portfolio 50-day SMA (" in html and "sparsePathFor('sma')" in html
    assert '"sma": null' in html and '"sma": 10' in html  # null before 50 days, numeric after


def test_streamlit_chart_hides_the_sma_legend_without_values(monkeypatch):
    assert "Portfolio 50-day SMA" not in _chart_html(monkeypatch, False)


@pytest.mark.parametrize("name", ["sharpe_dashboard.py", "sharpe_dashboard_dhan.py"])
def test_dashboards_feed_the_sma_to_the_chart_and_the_detail_table(name):
    src = (ROOT / name).read_text(encoding="utf-8")
    assert '_eq_df["nav_sma50"] = ml.nav_sma_resolved(' in src
    assert '"NAV 50D SMA"' in src
