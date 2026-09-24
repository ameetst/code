"""
Tradelog & MTM tests.

Two layers, deliberately:
  1. Pure core.tradelog logic (validate_tradelog_integrity, calculate_holdings_and_pnl,
     save_tradelog/sync_to_positions_ledger against a tmp_path) -- fast, no real data.
  2. HTTP routes against the live N750 data -- read-only, since settings.DATA_DIR is a
     plain constant bound by value in several core modules at import time (not a live
     `settings.DATA_DIR` reference), so redirecting it mid-test-run would mean
     monkeypatching each of those modules individually. That's worth doing before
     flipping SHARPE_READ_ONLY=0 for real, but isn't needed to prove the read-only
     guard itself, which is what layer 2 checks here.
"""
import json

import pytest

from webapp import settings
from webapp.app import create_app
from webapp.core import tradelog


# ── Layer 1: pure logic, no disk ──────────────────────────────────────────────

def _tx(ticker, action, qty, price, date, tx_id=None):
    return {"id": tx_id or f"{ticker}-{date}-{action}", "date": date,
            "timestamp": f"{date}T00:00:00", "ticker": ticker, "action": action,
            "quantity": qty, "price": price}


def test_validate_tradelog_integrity_accepts_valid_sequence():
    txs = [_tx("FOO", "BUY", 10, 100.0, "2026-01-01"), _tx("FOO", "SELL", 5, 110.0, "2026-01-05")]
    is_valid, err = tradelog.validate_tradelog_integrity(txs)
    assert is_valid and err == ""


def test_validate_tradelog_integrity_rejects_oversell():
    txs = [_tx("FOO", "BUY", 10, 100.0, "2026-01-01"), _tx("FOO", "SELL", 15, 110.0, "2026-01-05")]
    is_valid, err = tradelog.validate_tradelog_integrity(txs)
    assert not is_valid
    assert "FOO" in err and "exceeds" in err


def test_calculate_holdings_and_pnl_average_cost_and_realized_pnl():
    txs = [
        _tx("FOO", "BUY", 10, 100.0, "2026-01-01"),
        _tx("FOO", "BUY", 10, 120.0, "2026-01-02"),   # avg cost now 110
        _tx("FOO", "SELL", 5, 130.0, "2026-01-03"),   # realises 5 * (130-110) = 100
    ]
    result = tradelog.calculate_holdings_and_pnl(txs, latest_prices={"FOO": 150.0})
    assert result["realized_pnl"] == pytest.approx(100.0)
    h = result["active_holdings"]["FOO"]
    assert h["qty"] == pytest.approx(15.0)
    assert h["avg_price"] == pytest.approx(110.0)
    metrics = result["holdings_metrics"][0]
    assert metrics["Current Price"] == 150.0
    assert metrics["Unrealized PnL"] == pytest.approx(15 * (150.0 - 110.0))


def test_calculate_holdings_and_pnl_full_sell_closes_position():
    txs = [_tx("FOO", "BUY", 10, 100.0, "2026-01-01"), _tx("FOO", "SELL", 10, 120.0, "2026-01-02")]
    result = tradelog.calculate_holdings_and_pnl(txs)
    assert result["active_holdings"] == {}
    assert result["realized_pnl"] == pytest.approx(200.0)


def test_save_and_load_tradelog_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(tradelog, "DATA_DIR", tmp_path)
    txs = [_tx("FOO", "BUY", 10, 100.0, "2026-01-01")]
    tradelog.save_tradelog("TESTUNIV", txs)
    assert (tmp_path / "TESTUNIV_tradelog.json").exists()
    assert tradelog.load_tradelog("TESTUNIV") == txs
    # a second save must back up the first version, not clobber silently
    tradelog.save_tradelog("TESTUNIV", txs + [_tx("FOO", "SELL", 5, 110.0, "2026-01-02")])
    assert json.loads((tmp_path / "TESTUNIV_tradelog.bak").read_text()) == txs


def test_sync_to_positions_ledger_writes_only_open_positions(tmp_path):
    active_holdings = {
        "FOO": {"qty": 15.0, "avg_price": 110.0, "first_buy_date": None, "total_cost": 1650.0},
    }
    ledger_path = tmp_path / "TESTUNIV_positions_ledger.json"
    tradelog.sync_to_positions_ledger(ledger_path, active_holdings)
    written = json.loads(ledger_path.read_text())
    assert set(written) == {"FOO"}
    assert written["FOO"]["qty"] == 15.0
    assert written["FOO"]["entry_price"] == 110.0


# ── Layer 2: HTTP routes against live data, read-only guard ──────────────────

@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_tradelog_page_renders_and_matches_core_calc(client):
    from webapp.core import config_store, rankings

    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, bundle.latest_prices)

    r = client.get("/tradelog")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Tradelog" in html
    # each held ticker appears at least once (it may also repeat in Transaction History,
    # which lists every trade, not just open positions)
    for h in pnl["holdings_metrics"]:
        assert f"<td>{h['Ticker']}</td>" in html


def test_row_form_prefills_from_query_params(client):
    from webapp.core import config_store, rankings
    bundle = rankings.get_bundle(config_store.load_config())
    ticker = bundle.prices_df.index[0]

    html = client.get(f"/tradelog/row-form?ticker={ticker}&qty=42&price=99.5").data.decode()
    assert f'value="{ticker}" selected' in html
    assert 'value="42"' in html
    assert 'value="99.5"' in html


@pytest.mark.parametrize("path,method", [
    ("/tradelog/add", "post"),
    ("/tradelog/delete", "post"),
])
def test_mutating_routes_are_blocked_read_only_and_touch_nothing(client, path, method):
    assert settings.READ_ONLY is True  # sanity: this test is meaningless otherwise
    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}

    r = getattr(client, method)(path, data={"ticker": "AAA", "action": "BUY",
                                             "quantity": "1", "price": "1.0", "tx_id": "x"})
    assert r.status_code == 200  # not 403 -- HTMX must be able to swap this in
    assert "read-only" in r.data.decode().lower()

    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after


def test_edit_route_is_blocked_read_only_for_a_real_transaction(client):
    from webapp.core import config_store
    universe = config_store.universe_name(config_store.resolve_file(config_store.load_config()))
    tl = tradelog.load_tradelog(universe)
    if not tl:
        pytest.skip("no transactions in the live tradelog to edit")

    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}

    r = client.post(f"/tradelog/edit/{tl[0]['id']}", data={"quantity": "1", "price": "1.0"})
    assert r.status_code == 200
    assert "read-only" in r.data.decode().lower()

    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after


def test_edit_form_shows_the_requested_transaction(client):
    from webapp.core import config_store
    universe = config_store.universe_name(config_store.resolve_file(config_store.load_config()))
    tl = tradelog.load_tradelog(universe)
    if not tl:
        pytest.skip("no transactions in the live tradelog")

    tx = tl[0]
    html = client.get(f"/tradelog/edit-form/{tx['id']}").data.decode()
    assert tx["ticker"] in html
    assert f'value="{tx["quantity"]}"' in html


# ── Refresh Live Market Prices ────────────────────────────────────────────────

def test_effective_prices_merges_cached_live_prices_over_the_workbook_price(monkeypatch):
    from webapp.blueprints.tradelog import _effective_prices
    from webapp.core import dhan_client, rankings

    bundle = rankings.Bundle(
        prices_df=None, result=None, regime_score=0.0, regime_detail={}, dates=[],
        latest_prices={"FOO": 100.0, "BAR": 200.0},
    )
    monkeypatch.setattr(dhan_client, "_live_price_cache",
                         {"value": {"prices": {"FOO": 111.0}, "source": "Dhan", "unmatched": []}})
    merged = _effective_prices(bundle)
    assert merged == {"FOO": 111.0, "BAR": 200.0}  # FOO overridden, BAR untouched


def test_effective_prices_ignores_a_cached_ticker_not_in_the_workbook(monkeypatch):
    from webapp.blueprints.tradelog import _effective_prices
    from webapp.core import dhan_client, rankings

    bundle = rankings.Bundle(
        prices_df=None, result=None, regime_score=0.0, regime_detail={}, dates=[],
        latest_prices={"FOO": 100.0},
    )
    monkeypatch.setattr(dhan_client, "_live_price_cache",
                         {"value": {"prices": {"DELISTED": 5.0}, "source": "Dhan", "unmatched": []}})
    assert _effective_prices(bundle) == {"FOO": 100.0}


def test_refresh_live_prices_returns_a_value_or_gracefully_fails():
    """Live smoke check (Dhan then Yahoo Finance fallback), same pattern as get_live_vix
    -- never raises, and a successful fetch populates the cache."""
    from webapp.core import dhan_client

    result = dhan_client.refresh_live_prices(["RELIANCE"])
    assert set(result) == {"prices", "source", "unmatched"}
    if result["prices"]:
        assert result["source"] in ("Dhan", "Yahoo Finance")
        assert dhan_client.get_cached_live_prices() == result
    else:
        assert result["source"] is None


def test_refresh_prices_route_renders_and_is_not_gated_by_read_only(client):
    """Not a write route (no file touched), so it must work even while READ_ONLY."""
    assert settings.READ_ONLY is True
    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}

    r = client.post("/tradelog/refresh-prices")
    assert r.status_code == 200
    html = r.data.decode()
    # The page always shows a static "submitting will show why it's blocked" caption on
    # the trade form regardless of this route; what must NOT appear is the actual
    # write-guard banner text used by add/edit/delete when they're blocked.
    assert "webapp is read-only right now" not in html.lower()

    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after  # a live price fetch never touches a data file


def test_refresh_prices_button_and_source_caption_are_in_the_page(client):
    html = client.get("/tradelog").data.decode()
    assert "Refresh Live Market Prices" in html
    assert 'hx-post="/tradelog/refresh-prices"' in html


def test_cached_live_price_also_overrides_the_rankings_ltp_column(client, monkeypatch):
    """The dashboard's price override was global (one button, effect visible everywhere
    via st.session_state) -- confirms the webapp's shared cache reaches Top-N Rankings
    too, not just Tradelog."""
    from webapp.core import config_store, dhan_client, rankings

    bundle = rankings.get_bundle(config_store.load_config())
    ticker = bundle.prices_df.index[0]
    fake_price = round(bundle.latest_prices.get(ticker, 100.0) * 2 + 1, 2)  # unmistakably not the real price

    monkeypatch.setattr(dhan_client, "_live_price_cache",
                         {"value": {"prices": {ticker: fake_price}, "source": "Dhan", "unmatched": []}})

    html = client.get(f"/rankings/table?limit={len(bundle.result)}").data.decode()
    assert f"Rs {fake_price:,.2f}" in html
