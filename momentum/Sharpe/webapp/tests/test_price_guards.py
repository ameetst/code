"""
Guards against a damaged price workbook (empty NIFTY500 row / missing ticker prices) quietly
degrading the numbers: load_prices fails loudly, unpriced holdings are valued at cost instead
of 0, and the webapp shows a readable error instead of computing on missing data.
"""
import ast
import datetime
from pathlib import Path

import openpyxl
import pytest

import momentum_lib as ml
from webapp.core import dhan_client, tradelog

ROOT = Path(__file__).resolve().parents[2]
DAYS = [datetime.datetime(2026, 10, d) for d in (19, 20, 21, 22, 23)]  # 20th = holiday column


def _workbook(path, nifty, stocks=None):
    stocks = stocks or {"AAA": [10, None, 11, 12, 13], "BBB": [20, None, 21, 22, 23]}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DATA"
    ws.append(["TICKER", *DAYS])
    for t, row in stocks.items():
        ws.append([t, *row])
    ws.append(["NIFTY500", *nifty])
    wb.save(path)
    return path


def test_load_prices_accepts_a_populated_benchmark(tmp_path):
    p = _workbook(tmp_path / "ok.xlsx", [100, None, 101, 102, 103])  # holiday column empty for all
    prices, nifty, tickers, dates = ml.load_prices(str(p))
    assert tickers == ["AAA", "BBB"] and nifty.notna().sum() == 4


def test_load_prices_rejects_an_empty_benchmark_row(tmp_path):
    p = _workbook(tmp_path / "nonifty.xlsx", [None] * 5)
    with pytest.raises(ml.PriceDataError, match="no prices at all"):
        ml.load_prices(str(p))


def test_load_prices_rejects_a_benchmark_missing_the_latest_populated_date(tmp_path):
    p = _workbook(tmp_path / "stale.xlsx", [100, None, 101, 102, None])
    with pytest.raises(ml.PriceDataError, match="2026-10-23"):
        ml.load_prices(str(p))


def test_load_prices_rejects_a_missing_benchmark_row(tmp_path):
    p = tmp_path / "norow.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DATA"
    ws.append(["TICKER", *DAYS])
    ws.append(["AAA", 10, None, 11, 12, 13])
    wb.save(p)
    with pytest.raises(ml.PriceDataError, match="No NIFTY500"):
        ml.load_prices(str(p))


# ── unpriced holdings are valued at cost, never at 0 ──────────────────────────

TXS = [
    {"ticker": "HAS", "action": "BUY", "quantity": 10, "price": 100.0, "date": "2026-10-01"},
    {"ticker": "NOPX", "action": "BUY", "quantity": 62, "price": 1590.5, "date": "2026-10-01"},
]


def _check(calc):
    out = calc(TXS, {"HAS": 110.0})  # NOPX absent from the price dict
    by = {h["Ticker"]: h for h in out["holdings_metrics"]}
    assert by["NOPX"]["Current Price"] == 1590.5 and by["NOPX"]["Unrealized PnL"] == 0
    assert out["unrealized_pnl"] == pytest.approx(100.0)  # only HAS: 10 * (110 - 100)
    assert out["missing_price_tickers"] == ["NOPX"]
    for bad in (0.0, float("nan"), None, -5.0):
        r = calc(TXS, {"HAS": 110.0, "NOPX": bad})
        assert r["unrealized_pnl"] == pytest.approx(100.0), bad
        assert r["missing_price_tickers"] == ["NOPX"], bad
    assert calc(TXS, None)["missing_price_tickers"] == []  # no price dict at all: not a "missing" case


def test_webapp_pnl_values_unpriced_holdings_at_cost():
    _check(tradelog.calculate_holdings_and_pnl)


@pytest.mark.parametrize("name", ["sharpe_dashboard.py", "sharpe_dashboard_dhan.py"])
def test_dashboard_pnl_values_unpriced_holdings_at_cost(name):
    """Streamlit files run on import, so lift the function out of the source and exec it."""
    src = (ROOT / name).read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "calculate_holdings_and_pnl")
    ns = {"datetime": datetime}
    exec(compile(ast.Module([fn], []), name, "exec"), ns)
    _check(ns["calculate_holdings_and_pnl"])


@pytest.mark.parametrize("name", ["sharpe_dashboard.py", "sharpe_dashboard_dhan.py"])
def test_dashboards_leave_unpriced_tickers_out_of_latest_prices(name):
    src = (ROOT / name).read_text(encoding="utf-8")
    assert "for ticker in stock_tickers}" not in src.split("latest_prices =")[1].split("\n")[0]
    assert ":= get_latest_price(t, prices_df)) > 0}" in src


def test_cached_live_prices_fill_a_workbook_ticker_the_file_has_no_price_for(monkeypatch):
    monkeypatch.setattr(dhan_client, "get_cached_live_prices",
                        lambda: {"prices": {"NOPX": 1500.0, "ZERO": 0.0, "OTHER": 9.0}, "source": "test"})
    merged = dhan_client.apply_cached_prices({"HAS": 110.0}, known={"HAS", "NOPX", "ZERO"})
    assert merged == {"HAS": 110.0, "NOPX": 1500.0}  # zero live price ignored; OTHER not in workbook
    assert dhan_client.apply_cached_prices({"HAS": 110.0}) == {"HAS": 110.0}  # no `known`: only existing keys


# ── webapp surfaces the problem instead of computing on bad data ─────────────

def test_webapp_shows_a_readable_503_for_bad_price_data(monkeypatch):
    from webapp.app import create_app
    from webapp.core import rankings

    def boom(cfg):
        raise ml.PriceDataError("NIFTY500 benchmark row is empty")

    monkeypatch.setattr(rankings, "get_bundle", boom)
    resp = create_app().test_client().get("/rankings/full")
    assert resp.status_code == 503
    assert "Price data problem" in resp.get_data(as_text=True)
    assert "NIFTY500 benchmark row is empty" in resp.get_data(as_text=True)
