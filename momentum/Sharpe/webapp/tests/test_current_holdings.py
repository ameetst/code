"""
actions.current_holdings: the Actions page / Correlation matrix / nav dot read what is ACTUALLY held
(tradelog), not the positions-ledger file, which can carry never-bought placeholder names (older
Sharpe.py runs staged their recommendations there) or lack a name you still hold.
"""
import datetime
import json

import pytest

from webapp.core import actions, config_store, tradelog


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tradelog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_store, "DATA_DIR", tmp_path)
    return tmp_path


def _tx(ticker, action, qty, price, date):
    return {"id": f"{ticker}{date}{action}", "date": date, "timestamp": f"{date}T10:00:00",
            "ticker": ticker, "action": action, "quantity": qty, "price": price}


def _write(d, name, obj):
    (d / name).write_text(json.dumps(obj))


def test_ledger_placeholders_are_ignored_and_a_held_name_missing_from_the_ledger_is_included(data_dir):
    _write(data_dir, "TESTU_tradelog.json", [
        _tx("AAA", "BUY", 10, 100.0, "2026-09-01"),
        _tx("BBB", "BUY", 5, 200.0, "2026-09-02"),
        _tx("BBB", "SELL", 5, 210.0, "2026-10-01"),     # fully sold -> not held
        _tx("CCC", "BUY", 20, 50.0, "2026-08-01"),      # held, but absent from the ledger file
    ])
    _write(data_dir, "TESTU_positions_ledger.json", {
        "AAA": {"entry_date": "2026-09-01", "entry_price": 100.0, "qty": 10},
        "PLACEHOLDER": {"entry_date": "2026-11-12", "entry_price": 9.0, "qty": None},   # never bought
    })

    held = actions.current_holdings("TESTU")

    assert set(held) == {"AAA", "CCC"}
    assert held["CCC"] == {"entry_date": datetime.date(2026, 8, 1), "entry_price": 50.0}


def test_entry_price_is_the_average_cost_and_date_the_first_buy_of_the_open_lot(data_dir):
    _write(data_dir, "TESTU_tradelog.json", [
        _tx("AAA", "BUY", 10, 100.0, "2026-09-01"),
        _tx("AAA", "BUY", 10, 120.0, "2026-09-10"),
    ])
    held = actions.current_holdings("TESTU")
    assert held["AAA"]["entry_price"] == pytest.approx(110.0)
    assert held["AAA"]["entry_date"] == datetime.date(2026, 9, 1)


def test_falls_back_to_the_ledger_file_only_when_there_is_no_tradelog(data_dir):
    _write(data_dir, "TESTU_positions_ledger.json",
           {"AAA": {"entry_date": "2026-09-01", "entry_price": 100.0}})
    assert set(actions.current_holdings("TESTU")) == {"AAA"}


def test_everything_sold_means_no_holdings_even_if_the_ledger_still_lists_names(data_dir):
    _write(data_dir, "TESTU_tradelog.json", [
        _tx("AAA", "BUY", 10, 100.0, "2026-09-01"),
        _tx("AAA", "SELL", 10, 110.0, "2026-10-01"),
    ])
    _write(data_dir, "TESTU_positions_ledger.json",
           {"AAA": {"entry_date": "2026-09-01", "entry_price": 100.0}})
    assert actions.current_holdings("TESTU") == {}
