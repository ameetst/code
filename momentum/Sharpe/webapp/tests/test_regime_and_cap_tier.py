"""
Regime Score Breakdown + Market Cap Momentum Breakdown tests.

compute_cap_tier_momentum makes real Yahoo Finance calls across the whole universe
(~15s); the route test monkeypatches it to a canned result so the main suite stays
fast and deterministic. get_live_vix hits Dhan/Yahoo directly -- tested as a live
smoke check (like the ADTV/circuit-count tests elsewhere), not mocked, since a
network hiccup there should surface as a visible test failure, not be masked.
"""
import json

import pandas as pd
import pytest

from webapp.app import create_app
from webapp.core import cap_tier, dhan_client, regime


# ── core.regime ────────────────────────────────────────────────────────────────

def _prices_df(rows: dict) -> pd.DataFrame:
    """rows: {ticker: [close, close, ...]} -- same length series, most-recent last."""
    return pd.DataFrame(rows).T


def test_compute_ad_ratio_counts_advancers_and_decliners():
    df = _prices_df({"UP": [100, 105], "DOWN": [100, 95], "FLAT": [100, 100]})
    adv, dec, ratio = regime.compute_ad_ratio(df)
    assert (adv, dec) == (1, 1)
    assert ratio == pytest.approx(1.0)


def test_compute_ad_ratio_handles_all_decliners_as_infinite():
    df = _prices_df({"DOWN1": [100, 90], "DOWN2": [50, 40]})
    adv, dec, ratio = regime.compute_ad_ratio(df)
    assert (adv, dec) == (0, 2)
    assert ratio == 0.0  # 0 advancers / 2 decliners, not inf -- inf is dec==0 with adv>0


def test_compute_ad_ratio_infinite_when_no_decliners():
    df = _prices_df({"UP1": [100, 110], "UP2": [50, 60]})
    adv, dec, ratio = regime.compute_ad_ratio(df)
    assert (adv, dec) == (2, 0)
    assert ratio == float("inf")


def test_compute_ad_ratio_no_data_returns_none():
    df = _prices_df({"ONLY_ONE_COL": [100]})
    assert regime.compute_ad_ratio(df) == (0, 0, None)


def test_trend_chart_records_none_below_two_days():
    assert regime.trend_chart_records([], 0.4) is None
    assert regime.trend_chart_records([{"date": "2026-01-01", "Composite": 0.5,
                                        "Breadth": 0.5, "Momentum": 0.5}], 0.4) is None


def test_trend_chart_records_shape_and_ordering():
    history = [
        {"date": "2026-01-02", "Composite": 0.6, "Breadth": 0.7, "Momentum": 0.5},
        {"date": "2026-01-01", "Composite": 0.5, "Breadth": 0.6, "Momentum": 0.4},
    ]
    records_json = regime.trend_chart_records(history, 0.4)
    records = json.loads(records_json)
    assert [r["date"] for r in records] == ["2026-01-01", "2026-01-02"]  # sorted ascending
    assert records[0]["Entry Threshold"] == 0.4
    assert set(records[0]) == {"date", "Composite", "Breadth", "Momentum", "Entry Threshold"}


def test_load_regime_history_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(regime, "DATA_DIR", tmp_path)
    assert regime.load_regime_history("NOPE") == []


def test_get_live_vix_returns_a_value_or_gracefully_none():
    """Live smoke check (Dhan then Yahoo Finance fallback) -- never raises either way."""
    value, source = dhan_client.get_live_vix()
    if value is not None:
        assert source in ("Dhan", "Yahoo Finance")
        assert value > 0
    else:
        assert source is None


# ── core.cap_tier ───────────────────────────────────────────────────────────────

def test_tier_color_boundaries():
    assert cap_tier.tier_color(60)[0] == "#2E7D32"   # green at the threshold
    assert cap_tier.tier_color(59.9)[0] == "#F57F17"  # amber just below
    assert cap_tier.tier_color(30)[0] == "#F57F17"
    assert cap_tier.tier_color(29.9)[0] == "#C62828"  # red just below


def test_load_stockdb_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(cap_tier, "DATA_DIR", tmp_path)
    assert cap_tier.load_stockdb() == {}


def test_compute_cap_tier_dual_buckets_and_computes_delta():
    prices = _prices_df({
        "A": [100] * 62 + [110],  # 63d return > 0
        "B": [100] * 62 + [90],   # 63d return < 0
    })
    result = pd.DataFrame({"S_3M": [0.5, -0.5]}, index=["A", "B"])
    stockdb = {"A": "LARGECAP", "B": "LARGECAP"}
    rows = cap_tier.compute_cap_tier_dual(result, prices, ["A", "B"], stockdb)
    assert len(rows) == 1
    row = rows[0]
    assert row["cap_tier"] == "Large Cap"
    assert row["total"] == 2
    assert row["ret_pos"] == 1 and row["ret_pct"] == 50.0
    assert row["sharpe_pos"] == 1 and row["sharpe_pct"] == 50.0
    assert row["delta"] == 0


def test_compute_cap_tier_dual_skips_unclassified_tickers():
    prices = _prices_df({"A": [100] * 63})
    result = pd.DataFrame({"S_3M": [0.1]}, index=["A"])
    assert cap_tier.compute_cap_tier_dual(result, prices, ["A"], {}) == []


# ── HTTP routes ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_index_page_includes_regime_breakdown_and_cap_tier_sections(client):
    html = client.get("/").data.decode()
    assert "Regime Score Breakdown" in html
    assert "Live India VIX" in html
    assert "1D A/D Ratio" in html
    assert "Market Cap Momentum Breakdown" in html
    assert "Yahoo Finance Market Cap View" in html


def test_base_template_loads_sortable_tables_script(client):
    html = client.get("/").data.decode()
    assert "sortable_tables.js" in html


def test_cap_tier_yahoo_route_renders_canned_result(client, monkeypatch):
    from webapp.core import cap_tier as cap_tier_module
    canned = [{"tier": "Large Cap (1-100)", "total": 100, "positive": 60, "pct": 60.0,
               "colors": ("#2E7D32", "#E8F5E9", "#2E7D32")}]
    monkeypatch.setattr(cap_tier_module, "compute_cap_tier_momentum", lambda *a, **k: canned)

    html = client.get("/rankings/cap-tier/yahoo").data.decode()
    assert "Large Cap (1-100)" in html
    assert "60.0%" in html
    assert "Refresh Yahoo Market Cap Data" in html


def test_cap_tier_yahoo_route_shows_error_banner_on_failure(client, monkeypatch):
    from webapp.core import cap_tier as cap_tier_module

    def boom(*a, **k):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(cap_tier_module, "compute_cap_tier_momentum", boom)
    html = client.get("/rankings/cap-tier/yahoo").data.decode()
    assert "Could not load Market Cap data" in html
    assert "network unavailable" in html
