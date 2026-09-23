"""
Tradelog loading, the holdings/P&L engine, and the write path (save/validate/sync).
Ported from sharpe_dashboard_dhan.py (load_tradelog, save_tradelog,
calculate_holdings_and_pnl, validate_tradelog_integrity, sync_to_positions_ledger).

load_tradelog differs from the dashboard in one way: it never creates the file
when it's missing (that write happens on first use, gated behind require_writable
at the call site instead of implicitly on every read).
"""
import datetime
import json

from webapp.core.storage import safe_write_json
from webapp.settings import DATA_DIR


def load_tradelog(universe: str) -> list[dict]:
    path = DATA_DIR / f"{universe}_tradelog.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        bak_path = path.with_suffix(".bak")
        if bak_path.exists():
            try:
                with open(bak_path) as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        return []


def calculate_holdings_and_pnl(transactions: list[dict], latest_prices: dict | None = None) -> dict:
    """Replays BUY/SELL transactions chronologically into open positions + realised P&L
    (average-cost accounting). Verbatim port of the dashboard's function of the same name."""
    try:
        sorted_txs = sorted(transactions, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_txs = transactions

    holdings: dict = {}
    realized_pnl = 0.0
    realized_pnl_by_ticker: dict = {}

    for tx in sorted_txs:
        ticker = tx["ticker"]
        action = tx["action"].upper()
        qty = float(tx["quantity"])
        price = float(tx["price"])
        tx_date = tx.get("date", "")
        if isinstance(tx_date, str):
            try:
                tx_date = datetime.date.fromisoformat(tx_date)
            except ValueError:
                tx_date = datetime.date.today()

        if ticker not in holdings:
            holdings[ticker] = {"qty": 0.0, "avg_price": 0.0, "first_buy_date": None, "total_cost": 0.0}
        h = holdings[ticker]
        t_pnl = realized_pnl_by_ticker.get(ticker, 0.0)

        if action == "BUY":
            if h["qty"] == 0:
                h["first_buy_date"] = tx_date
            h["total_cost"] += qty * price
            h["qty"] += qty
            h["avg_price"] = h["total_cost"] / h["qty"]
        elif action == "SELL":
            if h["qty"] > 0:
                sell_qty = min(qty, h["qty"])
                pnl = sell_qty * (price - h["avg_price"])
                realized_pnl += pnl
                t_pnl += pnl
                h["qty"] -= sell_qty
                h["total_cost"] = h["qty"] * h["avg_price"]
                if h["qty"] == 0:
                    h["avg_price"] = 0.0
                    h["first_buy_date"] = None
        realized_pnl_by_ticker[ticker] = t_pnl

    active_holdings = {t: h for t, h in holdings.items() if h["qty"] > 0}

    unrealized_pnl = 0.0
    holdings_metrics = []
    for ticker, h in active_holdings.items():
        curr_price = h["avg_price"]
        if latest_prices is not None and ticker in latest_prices:
            curr_price = latest_prices[ticker]
        market_val = h["qty"] * curr_price
        u_pnl = market_val - h["total_cost"]
        unrealized_pnl += u_pnl
        u_pnl_pct = (u_pnl / h["total_cost"] * 100) if h["total_cost"] > 0 else 0.0
        holdings_metrics.append({
            "Ticker": ticker, "Qty": h["qty"], "Avg Price": h["avg_price"],
            "Current Price": curr_price, "Cost Value": h["total_cost"],
            "Market Value": market_val, "Unrealized PnL": u_pnl,
            "Unrealized PnL %": u_pnl_pct, "First Buy Date": h["first_buy_date"],
        })

    return {
        "active_holdings": active_holdings, "holdings_metrics": holdings_metrics,
        "realized_pnl": realized_pnl, "realized_pnl_by_ticker": realized_pnl_by_ticker,
        "unrealized_pnl": unrealized_pnl,
    }


def validate_tradelog_integrity(transactions: list[dict]) -> tuple[bool, str]:
    """Replays all transactions chronologically and checks no ticker ever goes negative.
    Verbatim port of the dashboard's function of the same name."""
    try:
        sorted_txs = sorted(transactions, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_txs = transactions
    holdings: dict = {}
    for tx in sorted_txs:
        ticker = tx["ticker"]
        action = tx["action"].upper()
        qty = float(tx["quantity"])
        current = holdings.get(ticker, 0.0)
        if action == "BUY":
            holdings[ticker] = current + qty
        elif action == "SELL":
            if qty > current + 1e-9:  # small epsilon for float tolerance
                return False, (f"{ticker}: SELL of {qty:.0f} shares exceeds "
                               f"holding of {current:.0f} shares on {tx.get('date', '?')}")
            holdings[ticker] = current - qty
    return True, ""


def save_tradelog(universe: str, transactions: list[dict]) -> None:
    """Writes {universe}_tradelog.json atomically. Caller must gate this behind
    require_writable — it performs no read-only check itself."""
    safe_write_json(DATA_DIR / f"{universe}_tradelog.json", transactions)


def sync_to_positions_ledger(ledger_path, active_holdings: dict) -> None:
    """Rewrites the positions ledger from the current open-holdings state (average-cost
    entry price/date + qty per ticker). Caller must gate this behind require_writable."""
    serialisable = {}
    for ticker, h in active_holdings.items():
        if h["qty"] > 0:
            entry_date = h["first_buy_date"]
            if isinstance(entry_date, (datetime.date, datetime.datetime)):
                entry_date = entry_date.isoformat()
            serialisable[ticker] = {
                "entry_date": entry_date,
                "entry_price": float(h["avg_price"]),
                "qty": float(h["qty"]),
            }
    safe_write_json(ledger_path, serialisable)
