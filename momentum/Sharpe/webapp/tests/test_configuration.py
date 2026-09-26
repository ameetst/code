"""
Configuration tests.

The Save route recomputes the ranking bundle synchronously with whatever config was
submitted, so a real (non-read-only) save test would both mutate the live, shared
dashboard_config.json (also used by Sharpe.py and the Streamlit dashboards) and pay
another ~11s compute. Real saves are therefore only tested against tmp_path via
core.configuration.save() directly, monkeypatched -- never through the live route.
"""
import json

import pytest

from webapp import settings
from webapp.app import create_app
from webapp.core import config_store, configuration, rankings


def test_clamp_bounds_both_directions():
    assert configuration.clamp("min_n", 1) == 3       # below min
    assert configuration.clamp("min_n", 999) == 10     # above max
    assert configuration.clamp("min_n", 7) == 7        # within range, unchanged
    assert configuration.clamp("capital", 1) == 100_000  # only a floor, no ceiling
    assert configuration.clamp("capital", 10_000_000) == 10_000_000
    assert configuration.clamp("min_cmp", -5) == 0        # below min
    assert configuration.clamp("min_cmp", 5000) == 1000   # above max
    assert configuration.clamp("min_cmp", 25) == 25       # within range, unchanged
    assert configuration.clamp("min_market_cap", -5) == 0            # below min
    assert configuration.clamp("min_market_cap", 5_000_000) == 2_000_000  # above max
    assert configuration.clamp("min_market_cap", 500) == 500         # within range, unchanged


def test_strategy_params_reflects_cfg_values():
    cfg = dict(min_n=5, max_n=20, capital=1_000_000, rel_dd_breach_threshold=-15,
               min_turnover=2.0, min_cmp=15.0, min_market_cap=500.0, eq_series_filter=True,
               circuit_filter_enabled=False, circuit_threshold=30, hold_rank_buffer=40,
               file="N750_updated.xlsx")
    universe = config_store.universe_name(cfg["file"])
    params = configuration.strategy_params(cfg, universe)
    assert params["Top N"] == "Dynamic (5–20)"
    assert params["Series EQ Filter"] == "Enabled"
    assert params["Circuit Filter"] == "Disabled"
    assert params["Rank Buffer"] == "40"
    assert "Rs 50,000" in params["Max Position Size"]  # 1,000,000 / 20
    assert params["Min CMP Filter"] == ">= Rs 15 (new entries only, holdings unaffected)"
    assert params["Min Market Cap Filter"] == ">= Rs 500 Cr (new entries only, holdings unaffected)"

    cfg["min_cmp"] = 0
    cfg["min_market_cap"] = 0
    params = configuration.strategy_params(cfg, universe)
    assert params["Min CMP Filter"] == "Disabled"
    assert params["Min Market Cap Filter"] == "Disabled"


def test_adtv_pass_counts_and_circuit_exceed_against_live_bundle():
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    adtv = configuration.adtv_pass_counts(bundle, float(cfg["min_turnover"]))
    assert adtv is not None  # N750_updated.xlsx has a VOLUME sheet
    assert 0 <= adtv["pass_12m"] <= adtv["total"]
    assert adtv["total"] == len(bundle.result)

    exceed = configuration.circuit_hit_exceed_count(bundle, 20)
    assert exceed is not None
    assert 0 <= exceed <= len(bundle.result)


def test_cmp_pass_count_against_live_bundle():
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)

    assert configuration.cmp_pass_count(bundle, 0) is None       # disabled
    assert configuration.cmp_pass_count(bundle, -5) is None      # disabled

    cmp = configuration.cmp_pass_count(bundle, 10.0)
    assert cmp is not None
    assert cmp["total"] == len(bundle.result)
    assert 0 <= cmp["pass"] <= cmp["total"]
    # A higher threshold can only exclude more (or equal) tickers, never fewer.
    cmp_high = configuration.cmp_pass_count(bundle, 100_000.0)
    assert cmp_high["pass"] <= cmp["pass"]


def test_series_and_band_are_sourced_from_stockdb_not_price_band_list():
    """Regression check for the Price_Band_List.csv -> STOCKDB.csv migration: HFCL is
    classified EQ in the retired Price_Band_List.csv but BE in STOCKDB.csv, so this
    proves SERIES is actually read from the new file, not a stale/cached old value."""
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    assert "HFCL" in bundle.result.index
    assert bundle.result.loc["HFCL", "SERIES"] == "BE"


def test_mcap_pass_count_against_live_bundle():
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)

    assert configuration.mcap_pass_count(bundle, 0) is None       # disabled
    assert configuration.mcap_pass_count(bundle, -5) is None      # disabled

    mcap = configuration.mcap_pass_count(bundle, 500.0)
    assert mcap is not None
    assert mcap["total"] == len(bundle.result)
    assert 0 <= mcap["pass"] <= mcap["total"]
    # A higher threshold can only exclude more (or equal) tickers, never fewer.
    mcap_high = configuration.mcap_pass_count(bundle, 1_000_000.0)
    assert mcap_high["pass"] <= mcap["pass"]


def test_save_writes_only_known_keys_to_tmp_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    cfg = {**{k: v for k, v in config_store.load_config().items()}, "bogus_key": "should not persist"}
    configuration.save(cfg)
    saved_path = tmp_path / "dashboard_config.json"
    assert saved_path.exists()
    saved = json.loads(saved_path.read_text())
    assert "bogus_key" not in saved
    assert saved["min_n"] == cfg["min_n"]


# ── HTTP routes against live data, read-only guard ────────────────────────────

@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_configuration_page_renders_current_values(client):
    cfg = config_store.load_config()
    html = client.get("/config-page").data.decode()
    assert "Configuration" in html
    assert f'value="{int(cfg["min_n"])}"' in html
    assert f'value="{int(cfg["max_n"])}"' in html


def test_save_route_is_blocked_read_only_and_touches_no_file(client):
    assert settings.READ_ONLY is True
    cfg_path = configuration.config_path()
    before = cfg_path.stat().st_mtime_ns if cfg_path.exists() else None

    r = client.post("/config-page/save", data={"min_n": "3", "max_n": "30"})
    assert r.status_code == 200
    assert "read-only" in r.data.decode().lower()

    after = cfg_path.stat().st_mtime_ns if cfg_path.exists() else None
    assert before == after
