"""
Cash Ledger tests. Same two-layer split as test_tradelog.py: pure logic against
tmp_path/synthetic data, then HTTP routes against live data (read-only guard only).
"""
import json

import pytest

from webapp import settings
from webapp.app import create_app
from webapp.core import cash_ledger


def _entry(entry_type, amount, date, note="", entry_id=None):
    return {"id": entry_id or f"{entry_type}-{date}", "date": date,
            "timestamp": f"{date}T00:00:00", "type": entry_type, "amount": amount, "note": note}


# ── Layer 1: pure logic, no disk ──────────────────────────────────────────────

def test_compute_summary_splits_inflows_and_outflows():
    entries = [
        _entry("Deposit", 100_000, "2026-01-01"),
        _entry("Dividend", 500, "2026-01-05"),
        _entry("Withdrawal", 20_000, "2026-01-10"),
        _entry("Fees/Charges", 100, "2026-01-15"),
    ]
    summary = cash_ledger.compute_summary(entries)
    assert summary["total_in"] == pytest.approx(100_500)
    assert summary["total_out"] == pytest.approx(20_100)
    assert summary["net"] == pytest.approx(80_400)


def test_running_balance_is_chronological_and_signed():
    entries = [
        _entry("Withdrawal", 5_000, "2026-01-10"),
        _entry("Deposit", 100_000, "2026-01-01"),  # out of order on purpose
    ]
    balances = cash_ledger.running_balance(entries)
    assert [e["date"] for e in balances] == ["2026-01-01", "2026-01-10"]
    assert balances[0]["balance"] == pytest.approx(100_000)
    assert balances[1]["balance"] == pytest.approx(95_000)


def test_save_and_load_cash_ledger_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cash_ledger, "DATA_DIR", tmp_path)
    entries = [_entry("Deposit", 50_000, "2026-01-01")]
    cash_ledger.save_cash_ledger("TESTUNIV", entries)
    assert (tmp_path / "TESTUNIV_cash_ledger.json").exists()
    assert cash_ledger.load_cash_ledger("TESTUNIV") == entries
    cash_ledger.save_cash_ledger("TESTUNIV", entries + [_entry("Withdrawal", 1000, "2026-01-02")])
    assert json.loads((tmp_path / "TESTUNIV_cash_ledger.bak").read_text()) == entries


def test_load_cash_ledger_missing_file_returns_empty_without_creating_it(tmp_path, monkeypatch):
    monkeypatch.setattr(cash_ledger, "DATA_DIR", tmp_path)
    assert cash_ledger.load_cash_ledger("NOPE") == []
    assert not (tmp_path / "NOPE_cash_ledger.json").exists()


# ── Layer 2: HTTP routes against live data, read-only guard ──────────────────

@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_cash_ledger_page_renders(client):
    r = client.get("/cash")
    assert r.status_code == 200
    assert b"Cash Ledger" in r.data


@pytest.mark.parametrize("path", ["/cash/add", "/cash/delete"])
def test_mutating_routes_are_blocked_read_only_and_touch_nothing(client, path):
    assert settings.READ_ONLY is True
    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}

    r = client.post(path, data={"type": "Deposit", "amount": "1000", "entry_id": "x"})
    assert r.status_code == 200
    assert "read-only" in r.data.decode().lower()

    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after


def test_edit_route_is_blocked_read_only_for_a_real_entry(client):
    from webapp.core import config_store
    universe = config_store.universe_name(config_store.resolve_file(config_store.load_config()))
    entries = cash_ledger.load_cash_ledger(universe)
    if not entries:
        pytest.skip("no entries in the live cash ledger to edit")

    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}

    r = client.post(f"/cash/edit/{entries[0]['id']}", data={"type": "Deposit", "amount": "1.0"})
    assert r.status_code == 200
    assert "read-only" in r.data.decode().lower()

    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after


def test_edit_form_shows_the_requested_entry(client):
    from webapp.core import config_store
    universe = config_store.universe_name(config_store.resolve_file(config_store.load_config()))
    entries = cash_ledger.load_cash_ledger(universe)
    if not entries:
        pytest.skip("no entries in the live cash ledger")

    entry = entries[0]
    html = client.get(f"/cash/edit-form/{entry['id']}").data.decode()
    assert entry["type"] in html
