"""
ETF dashboard (Flask) -- non-UI logic
======================================
Tradelog persistence, average-cost P&L, live allocation, and config / trade
validation. Ported from etf_dashboard.py (Streamlit) with the st.* calls
replaced by return values / exceptions. Nothing in here imports Flask or
Streamlit, so it can be tested on its own.
"""

import datetime
import json
import math
import shutil
import sys
from pathlib import Path

import pandas as pd

# The ranking engine lives one directory up.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import etf_momentum_ranking as emr  # noqa: E402

ETF_TRADELOG_FILE    = ROOT / "ETF_tradelog.json"
ETF_POSITIONS_LEDGER = ROOT / "ETF_positions_ledger.json"


class ValidationError(ValueError):
    """User-facing rejection (bad form value, inconsistent tradelog, ...)."""


# =========================================================
# JSON PERSISTENCE
# =========================================================
def safe_write_json(path, data):
    """Atomic JSON write: write to .tmp, backup existing to .bak, rename .tmp -> target."""
    path = Path(path)
    tmp_path = path.with_suffix(".tmp")
    bak_path = path.with_suffix(".bak")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        if path.exists():
            shutil.copy2(path, bak_path)
        shutil.move(str(tmp_path), str(path))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_tradelog() -> tuple[list, list]:
    """Load ETF_tradelog.json; auto-creates an empty file if missing.
    Returns (tradelog, notices) -- notices are warnings to show the user."""
    if not ETF_TRADELOG_FILE.exists():
        safe_write_json(ETF_TRADELOG_FILE, [])
        return [], []
    try:
        with open(ETF_TRADELOG_FILE) as f:
            return json.load(f), []
    except Exception as e:
        notices = [f"Error loading tradelog: {e}"]
        bak = ETF_TRADELOG_FILE.with_suffix(".bak")
        if bak.exists():
            try:
                with open(bak) as f:
                    notices.append("Recovered tradelog from ETF_tradelog.bak.")
                    return json.load(f), notices
            except Exception:
                pass
        return [], notices


def save_tradelog(tradelog: list):
    """Persist the tradelog to ETF_tradelog.json (raises on failure)."""
    safe_write_json(ETF_TRADELOG_FILE, tradelog)


# =========================================================
# TRADELOG MATHS
# =========================================================
def _tx_sort_key(tx):
    return (tx.get("date", ""), tx.get("timestamp", ""))


def validate_tradelog_integrity(transactions: list):
    """Replay all transactions chronologically; check no ticker ever goes negative.
    Returns (is_valid: bool, error_message: str)."""
    try:
        sorted_txs = sorted(transactions, key=_tx_sort_key)
    except Exception:
        sorted_txs = transactions
    holdings = {}
    for tx in sorted_txs:
        ticker  = tx["ticker"]
        action  = tx["action"].upper()
        qty     = float(tx["quantity"])
        current = holdings.get(ticker, 0.0)
        if action == "BUY":
            holdings[ticker] = current + qty
        elif action == "SELL":
            if qty > current + 1e-9:
                return False, (f"{ticker}: SELL {qty:.0f} exceeds holding "
                               f"{current:.0f} on {tx.get('date', '?')}")
            holdings[ticker] = current - qty
    return True, ""


def calculate_holdings_and_pnl(transactions: list, latest_prices: dict | None = None) -> dict:
    """Average-cost P&L engine -- exact port from etf_dashboard.py / sharpe_dashboard.py."""
    try:
        sorted_txs = sorted(transactions, key=_tx_sort_key)
    except Exception:
        sorted_txs = transactions

    holdings: dict = {}
    realized_pnl = 0.0
    realized_pnl_by_ticker: dict = {}

    for tx in sorted_txs:
        ticker   = tx["ticker"]
        action   = tx["action"].upper()
        qty      = float(tx["quantity"])
        price    = float(tx["price"])
        tx_date  = tx.get("date", "")
        if isinstance(tx_date, str):
            try:
                tx_date = datetime.date.fromisoformat(tx_date)
            except Exception:
                tx_date = datetime.date.today()

        if ticker not in holdings:
            holdings[ticker] = {"qty": 0.0, "avg_price": 0.0,
                                "first_buy_date": None, "total_cost": 0.0}
        h      = holdings[ticker]
        t_pnl  = realized_pnl_by_ticker.get(ticker, 0.0)

        if action == "BUY":
            if h["qty"] == 0:
                h["first_buy_date"] = tx_date
            h["total_cost"] += qty * price
            h["qty"]        += qty
            h["avg_price"]   = h["total_cost"] / h["qty"]
        elif action == "SELL":
            if h["qty"] > 0:
                sell_qty = min(qty, h["qty"])
                pnl      = sell_qty * (price - h["avg_price"])
                realized_pnl += pnl
                t_pnl        += pnl
                h["qty"]     -= sell_qty
                h["total_cost"] = h["qty"] * h["avg_price"]
                if h["qty"] == 0:
                    h["avg_price"]      = 0.0
                    h["first_buy_date"] = None
        realized_pnl_by_ticker[ticker] = t_pnl

    active_holdings = {t: h for t, h in holdings.items() if h["qty"] > 0}
    unrealized_pnl  = 0.0
    holdings_metrics = []

    for ticker, h in active_holdings.items():
        curr_price = h["avg_price"]
        if latest_prices and ticker in latest_prices:
            curr_price = latest_prices[ticker]
        market_val  = h["qty"] * curr_price
        u_pnl       = market_val - h["total_cost"]
        unrealized_pnl += u_pnl
        u_pnl_pct   = (u_pnl / h["total_cost"] * 100) if h["total_cost"] > 0 else 0.0
        holdings_metrics.append({
            "Ticker":            ticker,
            "Qty":               h["qty"],
            "Avg Price":         h["avg_price"],
            "Current Price":     curr_price,
            "Cost Value":        h["total_cost"],
            "Market Value":      market_val,
            "Unrealized PnL":    u_pnl,
            "Unrealized PnL %":  u_pnl_pct,
            "First Buy Date":    h["first_buy_date"],
        })

    return {
        "active_holdings":        active_holdings,
        "holdings_metrics":       holdings_metrics,
        "realized_pnl":           realized_pnl,
        "realized_pnl_by_ticker": realized_pnl_by_ticker,
        "unrealized_pnl":         unrealized_pnl,
    }


def sync_to_positions_ledger(active_holdings: dict):
    """Write active ETF holdings to ETF_positions_ledger.json.
    Skips the write (and the .bak rotation) when the ledger is already up to date."""
    serialisable = {}
    for ticker, h in active_holdings.items():
        if h["qty"] > 0:
            entry_date_str = h["first_buy_date"]
            if isinstance(entry_date_str, (datetime.date, datetime.datetime)):
                entry_date_str = entry_date_str.isoformat()
            serialisable[ticker] = {
                "entry_date":  entry_date_str,
                "entry_price": float(h["avg_price"]),
            }
    try:
        with open(ETF_POSITIONS_LEDGER) as f:
            if json.load(f) == serialisable:
                return
    except Exception:
        pass
    safe_write_json(ETF_POSITIONS_LEDGER, serialisable)


def get_etf_latest_price(ticker: str, prices_df: pd.DataFrame) -> float:
    """Return last non-NaN price for ticker from loaded price data."""
    if ticker in prices_df.columns:
        series = prices_df[ticker].dropna()
        if not series.empty:
            return float(series.iloc[-1])
    return 0.0


def build_live_allocation(ranking, regime, active_holdings, prices):
    """
    Weekly hold-and-replace allocation seeded from the REAL tradelog holdings
    (not a stateless from-scratch top-N pick), so a risk-off week does not show
    an all-CASH table while you are fully invested in positions that still pass
    every exit rule.
    """
    rank_by_ticker = {row["TICKER"]: row for _, row in ranking.iterrows()}
    prev_alloc = []
    for ticker, h in active_holdings.items():
        if h.get("qty", 0) <= 0:
            continue
        row = rank_by_ticker.get(ticker)
        etf_name = row["ETF_NAME"] if row is not None else ticker
        sector = row["SECTOR"] if row is not None else "OTHER"
        current_price = get_etf_latest_price(ticker, prices)
        peak = emr.compute_holding_peak(ticker, h.get("first_buy_date"), prices, current_price)
        prev_alloc.append({"ticker": ticker, "etf_name": etf_name, "sector": sector, "peak": peak})
    return emr.build_allocation(ranking, regime, prev_allocation=prev_alloc, prices=prices)


# =========================================================
# TRADE INPUT VALIDATION + MUTATIONS
# =========================================================
def parse_trade(payload: dict, valid_tickers) -> dict:
    """Validate a trade form payload; returns cleaned fields (ticker, action, date, quantity, price)."""
    ticker = str(payload.get("ticker", "")).strip()
    if ticker not in valid_tickers:
        raise ValidationError(f"Unknown ETF ticker: {ticker or '(blank)'}")

    action = str(payload.get("action", "")).strip().upper()
    if action not in ("BUY", "SELL"):
        raise ValidationError("Action must be BUY or SELL.")

    try:
        date = datetime.date.fromisoformat(str(payload.get("date", "")).strip())
    except ValueError:
        raise ValidationError("Transaction date must be a valid YYYY-MM-DD date.")

    try:
        qty = float(payload.get("quantity"))
    except (TypeError, ValueError):
        raise ValidationError("Quantity must be a number.")
    if not math.isfinite(qty) or qty != int(qty) or qty < 1:
        raise ValidationError("Quantity must be a whole number of at least 1.")

    try:
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        raise ValidationError("Price must be a number.")
    if not math.isfinite(price) or price < 0.01:
        raise ValidationError("Price must be at least 0.01.")

    return {"ticker": ticker, "action": action, "date": date.isoformat(),
            "quantity": int(qty), "price": price}


def check_integrity(candidate: list, what: str):
    ok, err = validate_tradelog_integrity(candidate)
    if not ok:
        raise ValidationError(f"{what} rejected -- inconsistent holdings: {err}")


# =========================================================
# CONFIG VALIDATION
# =========================================================
# Numeric fields exposed on the Configuration tab (same bounds as the Streamlit form).
NUM_FIELDS = {
    "TOP_N":                  dict(kind="int",   min=1,    max=20,   step=1),
    "SECTOR_CAP":             dict(kind="int",   min=1,    max=10,   step=1),
    "WINDOW_6M":              dict(kind="int",   min=20,   max=252,  step=1),
    "WINDOW_3M":              dict(kind="int",   min=10,   max=126,  step=1),
    "SHARPE_W6M":             dict(kind="float", min=0.0,  max=1.0,  step=0.1),
    "SHARPE_W3M":             dict(kind="float", min=0.0,  max=1.0,  step=0.1),
    "MAX_DRAWDOWN_FROM_HIGH": dict(kind="float", min=0.05, max=0.50, step=0.05),
    "TREND_FAST_EMA_WINDOW":  dict(kind="int",   min=10,   max=200,  step=5),
    "TREND_EMA_WINDOW":       dict(kind="int",   min=20,   max=400,  step=10),
    "DAILY_RF_ANNUAL":        dict(kind="float", min=0.0,  max=0.20, step=0.01),
    "TSL_THRESHOLD":          dict(kind="float", min=0.01, max=0.30, step=0.01),
    "EXIT_MAX_DD_FROM_HIGH":  dict(kind="float", min=0.05, max=0.50, step=0.05),
    "EXIT_MAX_RANK":          dict(kind="int",   min=5,    max=100,  step=1),
}


def parse_config(payload: dict, current_cfg: dict) -> dict:
    """Validate form values and merge them over the current config.

    Merging over `current_cfg` (rather than rebuilding a dict field by field)
    keeps every key the form doesn't edit -- INPUT_FILE, OUTPUT_FILE, ANNUALIZE,
    TOP_N_PARTIAL, HISTORY_PERIODS -- intact when strategy_config.json is rewritten.
    """
    new_cfg = dict(current_cfg)

    for key, spec in NUM_FIELDS.items():
        if key not in payload:
            continue
        try:
            val = float(payload[key])
        except (TypeError, ValueError):
            raise ValidationError(f"{key}: must be a number.")
        if not math.isfinite(val) or not spec["min"] <= val <= spec["max"]:
            raise ValidationError(f"{key}: must be between {spec['min']} and {spec['max']}.")
        if spec["kind"] == "int":
            if val != int(val):
                raise ValidationError(f"{key}: must be a whole number.")
            val = int(val)
        new_cfg[key] = val

    if "REGIME_TICKER" in payload:
        regime_ticker = str(payload["REGIME_TICKER"]).strip()
        if not regime_ticker:
            raise ValidationError("REGIME_TICKER: must not be blank.")
        new_cfg["REGIME_TICKER"] = regime_ticker
    if "REGIME_INDEX_TICKER" in payload:
        new_cfg["REGIME_INDEX_TICKER"] = str(payload["REGIME_INDEX_TICKER"]).strip()
    if "REGIME_FALLBACKS" in payload:
        raw = payload["REGIME_FALLBACKS"]
        parts = raw.split(",") if isinstance(raw, str) else raw
        new_cfg["REGIME_FALLBACKS"] = [str(t).strip() for t in parts if str(t).strip()]

    return new_cfg
