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
