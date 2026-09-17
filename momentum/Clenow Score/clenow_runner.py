"""
clenow_runner.py
=================
One-command weekly orchestrator around Clenow.py's ranking engine.

Clenow.py itself is NOT modified — it is imported and run exactly as it
already works from the command line. This script adds the layer the book
doesn't give you for free: a permanent trade log, an auto-compounding
equity ledger, an auto-maintained holdings.csv, and an HTML performance
dashboard — so a Wednesday morning is one command instead of a spreadsheet
session.

See TRACKING_SYSTEM_PLAN.md (in this folder) for the full design write-up
and the reasoning behind each choice below.

USAGE — the whole weekly ritual:
    python clenow_runner.py --file N750_updated.xlsx

FIRST-EVER RUN:
    If trade_log.csv doesn't exist yet, this is a bootstrap run. If
    holdings.csv already has hand-entered positions in it (the old
    regime), every row is migrated into trade_log.csv as an OPEN trade.
    The legacy holdings.csv format never recorded share counts, so you
    must provide them once via a companion file `initial_shares.csv`
    (columns: ticker,shares) sitting next to this script — the script
    tells you exactly what's missing and stops rather than guess at your
    real position sizes.

    You also need to supply --seed_equity on this first run (the total
    account equity to start the ledger from). It's remembered after that.

WHAT HAPPENS EACH RUN, IN ORDER:
    1. Load trade_log.csv / equity_history.csv (bootstrap if this is the
       first-ever run).
    2. Rewrite holdings.csv cleanly from the currently-OPEN trades (ISO
       dates, plain numbers — no Excel artifacts).
    3. Call Clenow.rank() — the unmodified ranking engine — using that
       clean holdings.csv and this run's starting equity for sizing.
    4. Reconcile signals against the trade log: every SELL closes its
       matching OPEN trade (fills exit price/date/reason, computes P&L);
       every BUY neither already held opens a new trade at that day's
       ATR-sized share count. HOLD positions are left completely
       untouched — share counts are fixed at entry until exit, per your
       call on that design question.
    5. Append one row to equity_history.csv: this run's realized P&L
       compounds into the next run's starting equity automatically.
    6. Rewrite holdings.csv again to reflect this run's opens/closes.
    7. Compute performance statistics from the full trade history.
    8. Render an HTML dashboard (equity curve vs. NIFTY500 buy-and-hold,
       a win/loss chart, a drawdown chart, and a stats panel) — one dated
       snapshot plus reports/dashboard_latest.html.
    9. Print a terminal summary: this run's BUYs/SELLs, realized P&L, new
       equity, and headline stats.

SAFETY:
    --dry_run runs the whole pipeline and prints what would happen
    without writing any file. Use this the first few times until you
    trust it.
    Re-running for a date already recorded in equity_history.csv is
    refused unless you pass --force (prevents double-booking the same
    week's trades if you run it twice by mistake).

CASH DEPOSITS/WITHDRAWALS:
    Equity compounds automatically from realized P&L. If you add or
    remove real cash from the account, tell the script explicitly with
    --cash_flow (positive for a deposit, negative for a withdrawal) on
    that run only — otherwise it would misread the cash movement as a
    trading gain or loss.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import Clenow  # unmodified — used purely as a library

# ── Dhan integration (optional) ─────────────────────────────────────────
# dhan_datahq lives as a sibling checkout under the same Github root:
#   Github/code/momentum/Clenow Score/clenow_runner.py   (this file)
#   Github/dhan_datahq/dhandata/                          (shared library)
# Falls back gracefully (DHAN_AVAILABLE = False) if dhandata isn't
# installed/importable, or its token cache isn't set up — the price
# fetch below checks this flag and falls back to the xlsx close, same as
# any other live-quote failure, rather than crashing the run.
_DHAN_DIR = Path(__file__).resolve().parent.parent.parent / "dhan_datahq"
if str(_DHAN_DIR) not in sys.path:
    sys.path.insert(0, str(_DHAN_DIR))
try:
    import dhandata as dh
    DHAN_AVAILABLE = True
    _DHAN_IMPORT_ERROR = None
except Exception as _dhan_import_err:
    dh = None
    DHAN_AVAILABLE = False
    _DHAN_IMPORT_ERROR = str(_dhan_import_err)

# ── Paths ────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
HOLDINGS_PATH = HERE / "holdings.csv"
TRADE_LOG_PATH = HERE / "trade_log.csv"
EQUITY_HISTORY_PATH = HERE / "equity_history.csv"
RANKED_OUTPUT_PATH = HERE / "clenow_ranked.csv"
INITIAL_SHARES_PATH = HERE / "initial_shares.csv"
CONFIG_PATH = HERE / "clenow_config.json"
REPORTS_DIR = HERE / "reports"

DEFAULT_CONFIG = {"price_provider": "yfinance"}  # or "dhan"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            cfg = {}
    else:
        cfg = {}
    return {**DEFAULT_CONFIG, **cfg}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

TRADE_LOG_COLUMNS = [
    "trade_id", "ticker", "entry_date", "entry_price", "entry_price_source", "shares",
    "entry_reason", "exit_date", "exit_price", "exit_price_source", "exit_reason",
    "holding_days", "pnl_amount", "pnl_pct", "status",
]
EQUITY_HISTORY_COLUMNS = [
    "run_date", "starting_equity", "realized_pnl_this_run",
    "cash_flow_adjustment", "ending_equity", "deployed_capital",
    "cash_available", "open_positions", "nifty500_close",
]
HOLDINGS_COLUMNS = ["ticker", "entry_date", "entry_price", "shares", "trade_id"]


# ── State I/O ────────────────────────────────────────────────────────────

def load_trade_log() -> pd.DataFrame:
    if TRADE_LOG_PATH.exists():
        df = pd.read_csv(TRADE_LOG_PATH, dtype={"trade_id": str, "ticker": str})
    else:
        df = pd.DataFrame(columns=TRADE_LOG_COLUMNS)
    for c in TRADE_LOG_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    # object dtype throughout: columns mix strings/NaN/numbers over the
    # life of a trade (e.g. exit_date is NaN while OPEN, a string once
    # CLOSED) and a numeric-inferred dtype would reject that assignment.
    return df[TRADE_LOG_COLUMNS].astype(object)


def load_equity_history() -> pd.DataFrame:
    if EQUITY_HISTORY_PATH.exists():
        df = pd.read_csv(EQUITY_HISTORY_PATH)
    else:
        df = pd.DataFrame(columns=EQUITY_HISTORY_COLUMNS)
    for c in EQUITY_HISTORY_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    return df[EQUITY_HISTORY_COLUMNS]


def write_holdings_csv(trade_log: pd.DataFrame) -> None:
    open_rows = trade_log[trade_log["status"] == "OPEN"][HOLDINGS_COLUMNS]
    open_rows.to_csv(HOLDINGS_PATH, index=False)


# ── One-time legacy migration ────────────────────────────────────────────

def load_legacy_holdings_robust(path: Path) -> pd.DataFrame:
    """
    Best-effort parser for the OLD hand-edited holdings.csv: drops blank
    rows/unnamed Excel columns, strips thousands-separator commas from
    prices, and parses dates day-first (Indian convention) rather than
    pandas' month-first default — this is exactly the ambiguity that
    turned '02-09-2026' into 9 Feb instead of 2 Sep in the old loader.
    """
    raw = pd.read_csv(path, dtype=str)
    raw.columns = [str(c).strip() for c in raw.columns]
    keep = [c for c in ["ticker", "entry_date", "entry_price"] if c in raw.columns]
    raw = raw[keep].dropna(how="all")
    raw = raw[raw["ticker"].notna() & (raw["ticker"].astype(str).str.strip() != "")]
    raw["ticker"] = raw["ticker"].astype(str).str.strip().str.upper()

    def parse_price(x):
        if pd.isna(x):
            return np.nan
        try:
            return float(str(x).replace(",", "").strip())
        except ValueError:
            return np.nan

    raw["entry_price"] = raw["entry_price"].apply(parse_price) if "entry_price" in raw.columns else np.nan
    raw["entry_date"] = (
        pd.to_datetime(raw["entry_date"], errors="coerce", dayfirst=True)
        if "entry_date" in raw.columns else pd.NaT
    )
    return raw.reset_index(drop=True)


def bootstrap_from_legacy() -> pd.DataFrame:
    """
    First-ever run only: seeds trade_log.csv from the existing hand-edited
    holdings.csv, if any. Raises SystemExit with a clear, actionable
    message rather than guessing at data it can't know (share counts,
    ambiguous dates/prices it can't parse).
    """
    if not HOLDINGS_PATH.exists():
        return pd.DataFrame(columns=TRADE_LOG_COLUMNS)

    legacy = load_legacy_holdings_robust(HOLDINGS_PATH)
    if not len(legacy):
        return pd.DataFrame(columns=TRADE_LOG_COLUMNS)

    if not INITIAL_SHARES_PATH.exists():
        tickers = ", ".join(legacy["ticker"].tolist())
        raise SystemExit(
            f"\nFirst-ever run: found {len(legacy)} existing position(s) in "
            f"holdings.csv ({tickers}) but the old file never recorded share "
            f"counts, and {INITIAL_SHARES_PATH.name} doesn't exist.\n\n"
            f"Create {INITIAL_SHARES_PATH.name} next to this script with two "
            f"columns, ticker and shares, for each ticker above (your actual "
            f"filled quantity), then re-run this exact command."
        )

    shares_df = pd.read_csv(INITIAL_SHARES_PATH, dtype={"ticker": str})
    shares_df["ticker"] = shares_df["ticker"].str.strip().str.upper()
    shares_map = dict(zip(shares_df["ticker"], shares_df["shares"]))

    missing_shares = [t for t in legacy["ticker"] if t not in shares_map]
    if missing_shares:
        raise SystemExit(
            f"\n{INITIAL_SHARES_PATH.name} is missing share counts for: "
            f"{', '.join(missing_shares)}. Add them and re-run."
        )
    bad_dates = legacy.loc[legacy["entry_date"].isna(), "ticker"].tolist()
    if bad_dates:
        raise SystemExit(
            f"\nCouldn't parse entry_date for: {', '.join(bad_dates)} in "
            f"holdings.csv. Use an unambiguous YYYY-MM-DD format and re-run."
        )
    bad_prices = legacy.loc[legacy["entry_price"].isna(), "ticker"].tolist()
    if bad_prices:
        raise SystemExit(
            f"\nCouldn't parse entry_price for: {', '.join(bad_prices)} in "
            f"holdings.csv. Fix the values (plain numbers, no currency "
            f"symbols) and re-run."
        )

    rows = []
    for _, r in legacy.iterrows():
        entry_date_iso = r["entry_date"].date().isoformat()
        rows.append(dict(
            trade_id=f"{r['ticker']}-{entry_date_iso.replace('-', '')}",
            ticker=r["ticker"],
            entry_date=entry_date_iso,
            entry_price=float(r["entry_price"]),
            entry_price_source="Manual (migrated from legacy holdings.csv)",
            shares=int(shares_map[r["ticker"]]),
            entry_reason="Migrated from legacy holdings.csv",
            exit_date=np.nan, exit_price=np.nan, exit_price_source=np.nan, exit_reason=np.nan,
            holding_days=np.nan, pnl_amount=np.nan, pnl_pct=np.nan,
            status="OPEN",
        ))
    trade_log = pd.DataFrame(rows, columns=TRADE_LOG_COLUMNS).astype(object)
    print(f"Bootstrapped {len(trade_log)} open position(s) from legacy holdings.csv:")
    print(trade_log[["ticker", "entry_date", "entry_price", "shares"]].to_string(index=False))
    print()
    return trade_log


# ── Ranking (calls Clenow.py, unmodified) ───────────────────────────────

def run_ranking(filepath: str, top_n: int, account_value: float, risk_factor: float, min_liquidity: float):
    Clenow.rank(
        filepath, top_n, str(RANKED_OUTPUT_PATH),
        account_value=account_value,
        holdings_path=str(HOLDINGS_PATH) if HOLDINGS_PATH.exists() else None,
        risk_factor=risk_factor,
        min_liquidity=min_liquidity,
    )
    ranked = pd.read_csv(RANKED_OUTPUT_PATH)

    # Cheap second read purely to recover today's index close for the
    # equity ledger / benchmark — keeps Clenow.py's public surface as-is.
    _, index_series, _ = Clenow.load_data(filepath)
    market_ok, idx_close, idx_ma200 = Clenow.check_market_filter(index_series)
    return ranked, market_ok, idx_close


# ── Live price fetch (yfinance or Dhan) ─────────────────────────────────
#
# Momentum score, MA filters, and ATR still come entirely from the xlsx
# history — that needs 90+ days of daily closes a quote API doesn't hand
# you in one simple call, and changing that data source would change the
# ranking math itself. This section only decides what price gets BOOKED
# into trade_log.csv for an actual BUY/SELL this run, so the number you
# see for "what you paid/received" reflects today's real market instead
# of however stale the xlsx's last column happens to be.
#
# Provider is chosen via --price_provider / clenow_config.json (see
# load_config() above) — "yfinance" (default) or "dhan". Both are fetched
# in one batched call per run for every ticker that's about to trade,
# rather than one request per ticker.

_YFINANCE_AVAILABLE = None  # cached after the first check
_YAHOO_SYMBOL_OVERRIDES = {
    # "NSETICKER": "YAHOO_SYMBOL",   # add entries here as you discover
    # a ticker whose NSE symbol doesn't match Yahoo's 1:1 (+ ".NS").
}


def _check_yfinance_available() -> bool:
    global _YFINANCE_AVAILABLE
    if _YFINANCE_AVAILABLE is None:
        try:
            import yfinance  # noqa: F401
            _YFINANCE_AVAILABLE = True
        except ImportError:
            _YFINANCE_AVAILABLE = False
    return _YFINANCE_AVAILABLE


def yahoo_symbol(ticker: str) -> str:
    return f"{_YAHOO_SYMBOL_OVERRIDES.get(ticker, ticker)}.NS"


def _fetch_live_prices_yfinance(tickers: list) -> tuple[dict, dict]:
    """One yfinance.Ticker(...).fast_info lookup per ticker (yfinance has
    no bulk quote call for this). Returns (prices, statuses)."""
    if not tickers:
        return {}, {}
    if not _check_yfinance_available():
        return {}, {t: "yfinance not installed" for t in tickers}
    import yfinance as yf
    prices, statuses = {}, {}
    for t in tickers:
        try:
            # NOTE: fast_info is dict-*like* but its dict-style keys are
            # camelCase ("lastPrice") while attribute access is snake_case
            # ("last_price") — .get("last_price") silently returns None.
            # Attribute access is the one that actually works.
            price = getattr(yf.Ticker(yahoo_symbol(t)).fast_info, "last_price", None)
            if price is None or (isinstance(price, float) and np.isnan(price)) or price <= 0:
                statuses[t] = "no live price returned"
                continue
            prices[t] = float(price)
            statuses[t] = "live (yfinance)"
        except Exception as e:
            statuses[t] = f"fetch error ({e.__class__.__name__})"
    return prices, statuses


def _fetch_live_prices_dhan(tickers: list) -> tuple[dict, dict]:
    """One batched /marketfeed/ltp call via dhandata.resolve_and_get_ltp
    for every ticker at once. Returns (prices, statuses)."""
    if not tickers:
        return {}, {}
    if not DHAN_AVAILABLE:
        return {}, {t: f"dhan not available ({_DHAN_IMPORT_ERROR})" for t in tickers}
    try:
        prices, unmatched = dh.resolve_and_get_ltp(tickers)
    except Exception as e:
        return {}, {t: f"dhan fetch error ({e.__class__.__name__})" for t in tickers}
    unmatched_set = set(unmatched)
    statuses = {}
    for t in tickers:
        if t in prices:
            statuses[t] = "live (dhan)"
        elif t in unmatched_set:
            statuses[t] = "dhan: ticker not resolved to a securityId"
        else:
            statuses[t] = "dhan: no live price returned"
    return prices, statuses


def fetch_live_prices_bulk(tickers: list, provider: str) -> tuple[dict, dict]:
    """
    Best-effort live quotes for every ticker in `tickers`, in one batched
    call. Returns (prices, statuses): `prices` has an entry only for
    tickers with a usable live quote; `statuses` has a human-readable
    reason for every ticker, used to fill trade_log's *_price_source
    column whether or not the live quote succeeded.
    """
    tickers = list(dict.fromkeys(tickers))  # de-dupe, keep order
    if provider == "dhan":
        return _fetch_live_prices_dhan(tickers)
    return _fetch_live_prices_yfinance(tickers)


def resolve_execution_price(ticker: str, fallback_price: float, use_live: bool,
                             live_prices: dict, live_statuses: dict):
    """
    Returns (price, source_label) for one ticker, given the bulk-fetched
    `live_prices`/`live_statuses` from fetch_live_prices_bulk(). Falls
    back to the xlsx-derived last_close on any failure, per the confirmed
    fallback policy — a bad/missing quote should never block a trade, it
    should just be flagged.
    """
    if not use_live:
        return fallback_price, "xlsx last_close (live price disabled)"
    price = live_prices.get(ticker)
    if price is not None:
        return price, live_statuses.get(ticker, "live")
    return fallback_price, f"fallback: xlsx last_close ({live_statuses.get(ticker, 'no live price')})"


# ── Reconciliation: signals -> trade log ────────────────────────────────

def reconcile_trades(trade_log: pd.DataFrame, ranked: pd.DataFrame, run_date_obj: date,
                      starting_equity: float, cash_flow: float = 0.0,
                      use_live_prices: bool = True, price_provider: str = "yfinance"):
    """
    SELL closes the matching OPEN trade; BUY (not already held) opens a
    new one at the ATR-sized share count Clenow.rank() computed. HOLD is
    untouched — share counts stay fixed from entry to exit, by design.

    The price actually booked for a SELL/BUY is a live quote (yfinance or
    Dhan, per `price_provider`) when available, falling back to the
    xlsx's last_close otherwise — see resolve_execution_price(). Ranking,
    filters, and sizing all still come entirely from the xlsx history via
    Clenow.rank(); only the booked transaction price can come from a live
    source.

    New BUYs are capped so their total cost doesn't exceed actual cash on
    hand: cash_for_buys = starting_equity + this run's realized P&L +
    cash_flow - capital tied up in continuing (untouched) holdings.
    Clenow.rank()'s ATR-based sizing gives each candidate equal risk per
    ATR, not equal dollar exposure, so a run full of low-volatility names
    can otherwise ask for more capital than the account has — that used
    to go unflagged and silently push cash_available negative. Candidates
    are filled whole (never partially resized) in rank order, best first,
    skipping (not shrinking) any that don't fit so every filled position
    keeps its intended risk profile.
    """
    trade_log = trade_log.copy().astype(object)
    ranked_by_ticker = ranked.set_index("ticker")

    open_mask = trade_log["status"] == "OPEN"
    open_tickers = set(trade_log.loc[open_mask, "ticker"])

    sell_tickers = set(ranked.loc[ranked["signal"] == "SELL", "ticker"]) & open_tickers
    missing_tickers = sorted(t for t in open_tickers if t not in ranked_by_ticker.index)
    buy_tickers = set(ranked.loc[ranked["signal"] == "BUY", "ticker"]) - open_tickers

    live_prices, live_statuses = fetch_live_prices_bulk(
        sorted(sell_tickers | buy_tickers), price_provider
    ) if use_live_prices else ({}, {})

    closed_ids = []
    realized_pnl = 0.0
    for t in sell_tickers:
        idx = trade_log[(trade_log["ticker"] == t) & (trade_log["status"] == "OPEN")].index
        if not len(idx):
            continue
        i = idx[0]
        row = ranked_by_ticker.loc[t]
        entry_price = float(trade_log.at[i, "entry_price"])
        shares = float(trade_log.at[i, "shares"])
        exit_price, exit_price_source = resolve_execution_price(
            t, float(row["last_close"]), use_live_prices, live_prices, live_statuses
        )
        entry_date = pd.to_datetime(trade_log.at[i, "entry_date"]).date()
        holding_days = (run_date_obj - entry_date).days
        pnl_amount = (exit_price - entry_price) * shares
        pnl_pct = (exit_price / entry_price - 1) * 100 if entry_price else np.nan
        realized_pnl += pnl_amount

        trade_log.at[i, "exit_date"] = run_date_obj.isoformat()
        trade_log.at[i, "exit_price"] = round(exit_price, 4)
        trade_log.at[i, "exit_price_source"] = exit_price_source
        trade_log.at[i, "exit_reason"] = row.get("signal_reason", "")
        trade_log.at[i, "holding_days"] = holding_days
        trade_log.at[i, "pnl_amount"] = round(pnl_amount, 2)
        trade_log.at[i, "pnl_pct"] = round(pnl_pct, 2)
        trade_log.at[i, "status"] = "CLOSED"
        closed_ids.append(trade_log.at[i, "trade_id"])

    # Capital already committed to positions carried forward untouched
    # (sold trades above are already flipped to CLOSED, so this mask
    # naturally excludes them).
    continuing_mask = trade_log["status"] == "OPEN"
    continuing_capital = float((
        trade_log.loc[continuing_mask, "entry_price"].astype(float)
        * trade_log.loc[continuing_mask, "shares"].astype(float)
    ).sum())
    cash_for_buys = max(0.0, starting_equity + realized_pnl + cash_flow - continuing_capital)

    candidates = []
    skipped_no_size = []
    for t in sorted(buy_tickers, key=lambda tk: ranked_by_ticker.loc[tk, "rank"]):
        row = ranked_by_ticker.loc[t]
        shares = row.get("approx_shares", np.nan)
        if pd.isna(shares) or shares <= 0:
            skipped_no_size.append(t)
            continue
        price, source = resolve_execution_price(
            t, float(row["last_close"]), use_live_prices, live_prices, live_statuses
        )
        candidates.append(dict(ticker=t, shares=int(shares), price=price, source=source,
                                reason=row.get("signal_reason", "")))

    new_rows = []
    skipped_insufficient_cash = []
    remaining_cash = cash_for_buys
    for c in candidates:
        cost = c["shares"] * c["price"]
        if cost > remaining_cash:
            skipped_insufficient_cash.append(c["ticker"])
            continue
        remaining_cash -= cost
        trade_id = f"{c['ticker']}-{run_date_obj.strftime('%Y%m%d')}"
        new_rows.append(dict(
            trade_id=trade_id, ticker=c["ticker"],
            entry_date=run_date_obj.isoformat(), entry_price=round(c["price"], 4),
            entry_price_source=c["source"],
            shares=c["shares"], entry_reason=c["reason"],
            exit_date=np.nan, exit_price=np.nan, exit_price_source=np.nan, exit_reason=np.nan,
            holding_days=np.nan, pnl_amount=np.nan, pnl_pct=np.nan,
            status="OPEN",
        ))

    if new_rows:
        new_df = pd.DataFrame(new_rows, columns=TRADE_LOG_COLUMNS).astype(object)
        trade_log = pd.concat([trade_log, new_df], ignore_index=True).astype(object)

    closed_now = trade_log[trade_log["trade_id"].isin(closed_ids)]
    opened_now = trade_log[trade_log["trade_id"].isin([r["trade_id"] for r in new_rows])]

    return trade_log, closed_now, opened_now, missing_tickers, skipped_no_size, skipped_insufficient_cash


# ── Performance statistics ──────────────────────────────────────────────

def compute_performance_stats(trade_log: pd.DataFrame, equity_history: pd.DataFrame) -> dict:
    stats = {}
    closed = trade_log[trade_log["status"] == "CLOSED"].copy()
    n_closed = len(closed)
    stats["n_closed_trades"] = n_closed

    if n_closed:
        wins = closed[closed["pnl_amount"] > 0]
        losses = closed[closed["pnl_amount"] <= 0]
        stats["win_rate_pct"] = round(100 * len(wins) / n_closed, 1)
        stats["avg_win_pct"] = round(wins["pnl_pct"].mean(), 2) if len(wins) else 0.0
        stats["avg_loss_pct"] = round(losses["pnl_pct"].mean(), 2) if len(losses) else 0.0
        gross_profit = wins["pnl_amount"].sum()
        gross_loss = -losses["pnl_amount"].sum()
        stats["profit_factor"] = round(gross_profit / gross_loss, 2) if gross_loss > 0 else np.nan
        stats["avg_holding_days_win"] = round(wins["holding_days"].mean(), 1) if len(wins) else np.nan
        stats["avg_holding_days_loss"] = round(losses["holding_days"].mean(), 1) if len(losses) else np.nan
    else:
        stats.update(dict(win_rate_pct=np.nan, avg_win_pct=np.nan, avg_loss_pct=np.nan,
                           profit_factor=np.nan, avg_holding_days_win=np.nan, avg_holding_days_loss=np.nan))

    eq = equity_history.copy()
    eq["ending_equity"] = pd.to_numeric(eq["ending_equity"], errors="coerce")
    if len(eq):
        start_equity = float(eq.iloc[0]["starting_equity"])
        end_equity = float(eq.iloc[-1]["ending_equity"])
        stats["total_return_pct"] = round((end_equity / start_equity - 1) * 100, 2) if start_equity else np.nan

        n_days = (pd.to_datetime(eq.iloc[-1]["run_date"]) - pd.to_datetime(eq.iloc[0]["run_date"])).days
        # Annualizing off a handful of days extrapolates noise into a
        # dramatic-looking number — only report CAGR once there's at
        # least ~10 weeks of real history to compound from.
        if start_equity > 0 and n_days >= 70:
            years = n_days / 365.25
            stats["cagr_pct"] = round(((end_equity / start_equity) ** (1 / years) - 1) * 100, 2)
        else:
            stats["cagr_pct"] = np.nan

        running_max = eq["ending_equity"].cummax()
        drawdown = (eq["ending_equity"] - running_max) / running_max * 100
        stats["max_drawdown_pct"] = round(drawdown.min(), 2)
        stats["current_drawdown_pct"] = round(drawdown.iloc[-1], 2)

        idx_vals = pd.to_numeric(eq["nifty500_close"], errors="coerce")
        if idx_vals.notna().any():
            idx0 = idx_vals.dropna().iloc[0]
            benchmark_equity = start_equity * (idx_vals / idx0)
            stats["benchmark_return_pct"] = round(
                (benchmark_equity.dropna().iloc[-1] / start_equity - 1) * 100, 2
            )
        else:
            stats["benchmark_return_pct"] = np.nan
    else:
        stats.update(dict(total_return_pct=np.nan, cagr_pct=np.nan, max_drawdown_pct=np.nan,
                           current_drawdown_pct=np.nan, benchmark_return_pct=np.nan))

    open_rows = trade_log[trade_log["status"] == "OPEN"]
    stats["open_positions"] = len(open_rows)
    return stats


# ── Dashboard (self-contained HTML, inline SVG, no external deps) ──────

PALETTE = dict(
    blue="#2a78d6", blue_dark="#3987e5",
    orange="#eb6834", orange_dark="#d95926",
    red="#e34948", red_dark="#e66767",
    good="#0ca30c", critical="#d03b3b",
    surface="#fcfcfb", surface_dark="#1a1a19",
    text_primary="#0b0b0b", text_primary_dark="#ffffff",
    text_secondary="#52514e", text_secondary_dark="#c3c2b7",
    muted="#898781", grid="#e1e0d9", grid_dark="#2c2c2a",
)


def _svg_line_chart(series: dict, width=760, height=260, pad=36) -> str:
    """series: {name: (values list, color)}. All series share one x-axis of equal length."""
    all_vals = [v for vals, _ in series.values() for v in vals if v is not None and not np.isnan(v)]
    if not all_vals:
        return "<p class='empty'>Not enough history yet.</p>"
    vmin, vmax = min(all_vals), max(all_vals)
    if vmin == vmax:
        vmin -= 1
        vmax += 1
    span = vmax - vmin
    n = max(len(vals) for vals, _ in series.values())

    def xy(i, v):
        x = pad + (i / max(n - 1, 1)) * (width - 2 * pad)
        y = height - pad - ((v - vmin) / span) * (height - 2 * pad)
        return x, y

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img">']
    # gridlines
    for gy in range(5):
        y = pad + gy * (height - 2 * pad) / 4
        parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width-pad}" y2="{y:.1f}" class="grid"/>')
    for name, (vals, color) in series.items():
        pts = [xy(i, v) for i, v in enumerate(vals) if v is not None and not np.isnan(v)]
        if not pts:
            continue
        path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round">'
                      f'<title>{name}</title></path>')
        lx, ly = pts[-1]
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}"><title>{name}: {vals[-1]:,.0f}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_drawdown_chart(drawdown_pct: list, width=760, height=160, pad=36) -> str:
    vals = [v for v in drawdown_pct if v is not None and not np.isnan(v)]
    if not vals:
        return "<p class='empty'>Not enough history yet.</p>"
    vmin = min(0.0, min(vals))
    vmax = 0.0
    span = max(vmax - vmin, 1e-6)
    n = len(vals)

    def xy(i, v):
        x = pad + (i / max(n - 1, 1)) * (width - 2 * pad)
        y = pad + ((vmax - v) / span) * (height - 2 * pad)
        return x, y

    zero_y = pad
    pts = [xy(i, v) for i, v in enumerate(vals)]
    area = f"M {pts[0][0]:.1f},{zero_y:.1f} " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L {pts[-1][0]:.1f},{zero_y:.1f} Z"
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img">'
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width-pad}" y2="{zero_y:.1f}" class="baseline"/>'
        f'<path d="{area}" fill="{PALETTE["red"]}" fill-opacity="0.15" stroke="none"/>'
        f'<path d="{line}" fill="none" stroke="{PALETTE["red"]}" stroke-width="2"/>'
        f'</svg>'
    )


def _svg_trade_bar_chart(closed: pd.DataFrame, width=760, height=220, pad=36) -> str:
    if not len(closed):
        return "<p class='empty'>No closed trades yet.</p>"
    vals = closed["pnl_pct"].tolist()
    labels = closed["ticker"].tolist()
    vmax = max(1.0, max((v for v in vals if v is not None), default=1.0))
    vmin = min(-1.0, min((v for v in vals if v is not None), default=-1.0))
    span = vmax - vmin
    n = len(vals)
    bar_w = max(4, (width - 2 * pad) / n * 0.6)
    zero_y = pad + (vmax / span) * (height - 2 * pad)

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img">',
             f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{width-pad}" y2="{zero_y:.1f}" class="baseline"/>']
    for i, (v, label) in enumerate(zip(vals, labels)):
        cx = pad + (i + 0.5) / n * (width - 2 * pad)
        y_top = pad + ((vmax - max(v, 0)) / span) * (height - 2 * pad)
        y_bot = pad + ((vmax - min(v, 0)) / span) * (height - 2 * pad)
        color = PALETTE["good"] if v >= 0 else PALETTE["critical"]
        parts.append(
            f'<rect x="{cx - bar_w/2:.1f}" y="{y_top:.1f}" width="{bar_w:.1f}" '
            f'height="{max(y_bot - y_top, 1):.1f}" fill="{color}" rx="2">'
            f'<title>{label}: {v:+.2f}%</title></rect>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _build_open_positions_mtm(trade_log: pd.DataFrame, ranked: Optional[pd.DataFrame]) -> pd.DataFrame:
    """
    Open positions (from trade_log) marked to market against this run's
    `last_close` (from Clenow.rank()'s output — real OHLC-CSV closes when
    that's the input, same price used for this run's ranking/stops; not a
    live intraday quote — see mtm.py for that). Returns a DataFrame with
    one row per OPEN position, current_price/mtm_pnl/mtm_pnl_pct as NaN
    for anything missing from `ranked` (e.g. delisted, or no ranked data
    at all yet).
    """
    open_pos = trade_log[trade_log["status"] == "OPEN"].copy()
    if not len(open_pos):
        return open_pos.assign(current_price=[], mtm_pnl=[], mtm_pnl_pct=[])

    last_close_map = (
        ranked.set_index("ticker")["last_close"] if ranked is not None and len(ranked) else pd.Series(dtype=float)
    )
    open_pos["entry_price"] = open_pos["entry_price"].astype(float)
    open_pos["shares"] = open_pos["shares"].astype(float)
    open_pos["current_price"] = open_pos["ticker"].map(last_close_map)
    open_pos["mtm_pnl"] = (open_pos["current_price"] - open_pos["entry_price"]) * open_pos["shares"]
    open_pos["mtm_pnl_pct"] = (open_pos["current_price"] / open_pos["entry_price"] - 1) * 100
    return open_pos


def render_dashboard(trade_log: pd.DataFrame, equity_history: pd.DataFrame, stats: dict,
                      run_date: str, ranked: Optional[pd.DataFrame] = None) -> Path:
    eq = equity_history.copy()
    eq["ending_equity"] = pd.to_numeric(eq["ending_equity"], errors="coerce")
    idx_vals = pd.to_numeric(eq["nifty500_close"], errors="coerce") if len(eq) else pd.Series(dtype=float)
    benchmark = None
    if len(eq) and idx_vals.notna().any():
        idx0 = idx_vals.dropna().iloc[0]
        start_equity = float(eq.iloc[0]["starting_equity"])
        benchmark = (start_equity * (idx_vals / idx0)).tolist()

    equity_series = {"Strategy equity": (eq["ending_equity"].tolist(), PALETTE["blue"])}
    if benchmark:
        equity_series["NIFTY500 buy & hold"] = (benchmark, PALETTE["orange"])

    running_max = eq["ending_equity"].cummax() if len(eq) else pd.Series(dtype=float)
    drawdown = ((eq["ending_equity"] - running_max) / running_max * 100).tolist() if len(eq) else []

    closed = trade_log[trade_log["status"] == "CLOSED"].copy()
    open_pos = _build_open_positions_mtm(trade_log, ranked)
    open_pos_priced = open_pos[open_pos["current_price"].notna()] if len(open_pos) else open_pos

    def stat_html(label, value, is_pct=False, good_if_positive=True):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            disp = "—"
            cls = ""
        else:
            disp = f"{value:+.2f}%" if is_pct else f"{value:,.2f}" if isinstance(value, float) else f"{value}"
            cls = ""
            if is_pct and value != 0:
                positive = value > 0
                cls = "delta-good" if (positive == good_if_positive) else "delta-bad"
        return f'<div class="stat"><div class="stat-label">{label}</div><div class="stat-value {cls}">{disp}</div></div>'

    stats_html = "".join([
        stat_html("Total return", stats.get("total_return_pct"), is_pct=True),
        stat_html("CAGR", stats.get("cagr_pct"), is_pct=True),
        stat_html("vs NIFTY500 B&H", stats.get("benchmark_return_pct"), is_pct=True),
        stat_html("Win rate", stats.get("win_rate_pct"), is_pct=True, good_if_positive=True) if not np.isnan(stats.get("win_rate_pct", np.nan)) else stat_html("Win rate", None),
        stat_html("Avg win", stats.get("avg_win_pct"), is_pct=True),
        stat_html("Avg loss", stats.get("avg_loss_pct"), is_pct=True, good_if_positive=False),
        stat_html("Profit factor", stats.get("profit_factor")),
        stat_html("Max drawdown", stats.get("max_drawdown_pct"), is_pct=True, good_if_positive=False),
        stat_html("Closed trades", stats.get("n_closed_trades")),
        stat_html("Open positions", stats.get("open_positions")),
    ])

    def _pnl_cls(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
        return "delta-good" if v > 0 else ("delta-bad" if v < 0 else "")

    def _open_pos_row(r):
        has_price = pd.notna(r.current_price)
        current_disp = f"{r.current_price:,.2f}" if has_price else "—"
        pnl_disp = f"{r.mtm_pnl:+,.2f}" if has_price else "—"
        pnl_pct_disp = f"{r.mtm_pnl_pct:+.2f}%" if has_price else "—"
        cls = _pnl_cls(r.mtm_pnl_pct) if has_price else ""
        return (f"<tr><td>{r.ticker}</td><td>{r.entry_date}</td>"
                f"<td>{r.entry_price:,.2f}</td><td>{current_disp}</td>"
                f"<td>{int(r.shares)}</td>"
                f"<td class='{cls}'>{pnl_disp}</td><td class='{cls}'>{pnl_pct_disp}</td></tr>")

    open_positions_table = (
        "<p class='empty'>No open positions.</p>" if not len(open_pos) else
        "<table><tr><th>Ticker</th><th>Entry date</th><th>Buy price</th><th>Current price</th>"
        "<th>Shares</th><th>MTM P&amp;L (₹)</th><th>MTM P&amp;L (%)</th></tr>"
        + "".join(_open_pos_row(r) for r in open_pos.sort_values("mtm_pnl_pct", ascending=False, na_position="last").itertuples())
        + "</table>"
    )

    if len(open_pos_priced):
        total_cost = float(open_pos_priced["entry_price"].mul(open_pos_priced["shares"]).sum())
        total_value = float(open_pos_priced["current_price"].mul(open_pos_priced["shares"]).sum())
        total_mtm = total_value - total_cost
        total_mtm_pct = (total_value / total_cost - 1) * 100 if total_cost else float("nan")
        mtm_cls = _pnl_cls(total_mtm_pct)
        unpriced_note = (f" &middot; {len(open_pos) - len(open_pos_priced)} position(s) have no current price"
                          " (missing from this run's ranked data) and are excluded from the totals below"
                          if len(open_pos) > len(open_pos_priced) else "")
        open_positions_totals = (
            f"<div class='mtm-total'>Total cost basis: ₹{total_cost:,.2f} &nbsp;·&nbsp; "
            f"Market value: ₹{total_value:,.2f} &nbsp;·&nbsp; "
            f"Unrealized P&amp;L: <span class='{mtm_cls}'>₹{total_mtm:+,.2f} ({total_mtm_pct:+.2f}%)</span>"
            f"{unpriced_note}</div>"
        )
    else:
        open_positions_totals = ""

    html = f"""<title>Clenow Momentum — Performance Dashboard</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface: {PALETTE['surface']}; --text-primary: {PALETTE['text_primary']};
    --text-secondary: {PALETTE['text_secondary']}; --muted: {PALETTE['muted']};
    --grid: {PALETTE['grid']};
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--surface); color: var(--text-primary);
    padding: 24px; max-width: 820px; margin: 0 auto;
  }}
  @media (prefers-color-scheme: dark) {{
    .viz-root {{
      color-scheme: dark;
      --surface: {PALETTE['surface_dark']}; --text-primary: {PALETTE['text_primary_dark']};
      --text-secondary: {PALETTE['text_secondary_dark']}; --muted: {PALETTE['muted']};
      --grid: {PALETTE['grid_dark']};
    }}
  }}
  .viz-root h1 {{ font-size: 20px; margin-bottom: 2px; }}
  .viz-root .subtitle {{ color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }}
  .viz-root h2 {{ font-size: 14px; color: var(--text-secondary); margin: 28px 0 8px; font-weight: 600; }}
  .viz-root .card {{ border: 1px solid var(--grid); border-radius: 10px; padding: 12px; }}
  .viz-root .stats-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }}
  .viz-root .stat {{ border: 1px solid var(--grid); border-radius: 8px; padding: 10px; }}
  .viz-root .stat-label {{ font-size: 11px; color: var(--muted); margin-bottom: 4px; }}
  .viz-root .stat-value {{ font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .viz-root .delta-good {{ color: {PALETTE['good']}; }}
  .viz-root .delta-bad {{ color: {PALETTE['critical']}; }}
  .viz-root .chart-svg {{ width: 100%; height: auto; }}
  .viz-root .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .viz-root .baseline {{ stroke: var(--muted); stroke-width: 1; }}
  .viz-root .legend {{ display: flex; gap: 16px; font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .viz-root .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .viz-root .swatch {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  .viz-root .empty {{ color: var(--muted); font-size: 13px; padding: 30px 0; text-align: center; }}
  .viz-root table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }}
  .viz-root th, .viz-root td {{ text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--grid); }}
  .viz-root th {{ color: var(--muted); font-weight: 500; }}
  .viz-root .mtm-total {{ font-size: 12px; color: var(--text-secondary); margin-top: 10px; }}
  .viz-root .mtm-total .delta-good, .viz-root .mtm-total .delta-bad {{ font-weight: 600; }}
</style>
<div class="viz-root">
  <h1>Clenow Momentum — Performance Dashboard</h1>
  <div class="subtitle">As of {run_date} &middot; {stats.get('n_closed_trades', 0)} closed trades &middot; {stats.get('open_positions', 0)} open positions</div>

  <h2>Headline stats</h2>
  <div class="stats-grid">{stats_html}</div>

  <h2>Open positions — mark to market</h2>
  <div class="card">
    {open_positions_table}
    {open_positions_totals}
  </div>
  <div class="subtitle" style="margin-top:4px;margin-bottom:0;">Current price is this run's <code>last_close</code> from the price file, not a live intraday quote — run <code>python mtm.py</code> for a live check.</div>

  <h2>Equity curve</h2>
  <div class="card">
    <div class="legend">
      <span><span class="swatch" style="background:{PALETTE['blue']}"></span>Strategy equity</span>
      {'<span><span class="swatch" style="background:' + PALETTE['orange'] + '"></span>NIFTY500 buy &amp; hold</span>' if benchmark else ''}
    </div>
    {_svg_line_chart(equity_series)}
  </div>

  <h2>Drawdown</h2>
  <div class="card">{_svg_drawdown_chart(drawdown)}</div>

  <h2>Closed trade P&amp;L (%)</h2>
  <div class="card">{_svg_trade_bar_chart(closed)}</div>

  <h2>Recent closed trades</h2>
  <div class="card">
    <table>
      <tr><th>Ticker</th><th>Entry</th><th>Exit</th><th>Days</th><th>P&amp;L %</th><th>Exit reason</th></tr>
      {"".join(f"<tr><td>{r.ticker}</td><td>{r.entry_date}</td><td>{r.exit_date}</td><td>{int(r.holding_days) if pd.notna(r.holding_days) else ''}</td><td class='{'delta-good' if r.pnl_pct > 0 else ('delta-bad' if r.pnl_pct < 0 else '')}'>{r.pnl_pct:+.2f}%</td><td>{r.exit_reason}</td></tr>" for r in closed.sort_values('exit_date', ascending=False).head(20).itertuples())}
    </table>
  </div>
</div>
"""

    REPORTS_DIR.mkdir(exist_ok=True)
    dated_path = REPORTS_DIR / f"dashboard_{run_date}.html"
    latest_path = REPORTS_DIR / "dashboard_latest.html"
    dated_path.write_text(html, encoding="utf-8")
    latest_path.write_text(html, encoding="utf-8")
    return latest_path


# ── Terminal summary ─────────────────────────────────────────────────────

def print_summary(run_date, closed_now, opened_now, trade_log, equity_row, stats,
                   missing_tickers, skipped_no_size, skipped_insufficient_cash,
                   dashboard_path, market_ok, price_provider):
    print(f"\n{'═'*64}")
    print(f"  CLENOW WEEKLY RUN — {run_date}")
    print(f"{'═'*64}")
    print(f"  Price provider      : {price_provider}")
    print(f"  Market regime       : {'ABOVE 200-day MA ✓' if market_ok else 'BELOW 200-day MA ✗ (no new buys)'}")
    print(f"  Starting equity     : ₹{equity_row['starting_equity']:,.2f}")
    print(f"  Realized P&L (run)  : ₹{equity_row['realized_pnl_this_run']:,.2f}")
    print(f"  Ending equity       : ₹{equity_row['ending_equity']:,.2f}")
    print(f"  Deployed capital    : ₹{equity_row['deployed_capital']:,.2f}")
    print(f"  Cash available      : ₹{equity_row['cash_available']:,.2f}")

    if len(closed_now):
        print(f"\n  SELL — closed this run ({len(closed_now)}):")
        print(closed_now[["ticker", "exit_price", "exit_price_source", "pnl_amount", "pnl_pct", "exit_reason"]].to_string(index=False))
    else:
        print("\n  SELL — none this run.")

    if len(opened_now):
        print(f"\n  BUY — opened this run ({len(opened_now)}):")
        print(opened_now[["ticker", "entry_price", "entry_price_source", "shares", "entry_reason"]].to_string(index=False))
    else:
        print("\n  BUY — none this run.")

    if skipped_no_size:
        print(f"\n  ⚠ Ranked as BUY but skipped (no sizeable ATR yet): {', '.join(skipped_no_size)}")

    if skipped_insufficient_cash:
        print(f"\n  ⚠ Ranked as BUY but skipped (insufficient cash at full ATR size): "
              f"{', '.join(skipped_insufficient_cash)}")

    if missing_tickers:
        print(f"\n  ⚠ Currently OPEN but missing from this week's price file — "
              f"investigate manually (delisted/renamed?), left OPEN: {', '.join(missing_tickers)}")

    open_now = trade_log[trade_log["status"] == "OPEN"]
    print(f"\n  Current book ({len(open_now)} open):")
    if len(open_now):
        print(open_now[["ticker", "entry_date", "entry_price", "shares"]].to_string(index=False))

    print(f"\n  Performance-to-date:")
    print(f"    Total return      : {stats['total_return_pct']:+.2f}%" if not np.isnan(stats.get('total_return_pct', np.nan)) else "    Total return      : —")
    print(f"    CAGR              : {stats['cagr_pct']:+.2f}%" if not np.isnan(stats.get('cagr_pct', np.nan)) else "    CAGR              : —")
    print(f"    vs NIFTY500 B&H   : {stats['benchmark_return_pct']:+.2f}%" if not np.isnan(stats.get('benchmark_return_pct', np.nan)) else "    vs NIFTY500 B&H   : —")
    print(f"    Win rate          : {stats['win_rate_pct']:.1f}%" if not np.isnan(stats.get('win_rate_pct', np.nan)) else "    Win rate          : — (no closed trades yet)")
    print(f"    Max drawdown      : {stats['max_drawdown_pct']:+.2f}%" if not np.isnan(stats.get('max_drawdown_pct', np.nan)) else "    Max drawdown      : —")
    print(f"\n  ✓ Dashboard        : {dashboard_path}")
    print(f"  ✓ Trade log        : {TRADE_LOG_PATH}")
    print(f"  ✓ Equity history   : {EQUITY_HISTORY_PATH}")
    print(f"  ✓ Holdings (clean) : {HOLDINGS_PATH}")
    print(f"{'═'*64}\n")


# ── CLI / main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Clenow weekly runner — ranking + trade log + equity + dashboard")
    parser.add_argument("--file", required=True, help="Path to this week's price workbook")
    parser.add_argument("--top_n", type=int, default=Clenow.DEFAULT_TOP_N)
    parser.add_argument("--risk_factor", type=float, default=Clenow.DEFAULT_RISK_FACTOR)
    parser.add_argument("--min_liquidity", type=float, default=Clenow.DEFAULT_MIN_AVG_DAILY_VALUE)
    parser.add_argument("--seed_equity", type=float, default=1_000_000.0,
                         help="Starting equity — only used the very first time equity_history.csv is created")
    parser.add_argument("--cash_flow", type=float, default=0.0,
                         help="Real cash deposited (+) or withdrawn (-) this run; does not include trading P&L")
    parser.add_argument("--run_date", default=None, help="Override today's date (YYYY-MM-DD) — mainly for testing")
    parser.add_argument("--dry_run", action="store_true", help="Show what would happen; write nothing")
    parser.add_argument("--force", action="store_true", help="Allow re-running for a date already in equity_history.csv")
    parser.add_argument("--no_browser", action="store_true",
                         help="Don't auto-open dashboard_latest.html in the browser when the run finishes "
                              "(useful for scheduled/headless runs, e.g. Windows Task Scheduler)")
    parser.add_argument("--no_live_price", action="store_true",
                         help="Disable live-quote lookup; always use the xlsx's last_close for entries/exits")
    parser.add_argument("--price_provider", choices=["yfinance", "dhan"], default=None,
                         help="Live-quote source for actual BUY/SELL fills. Persists in "
                              f"{CONFIG_PATH.name} once passed — omit to keep using whatever's "
                              "already configured there (yfinance if never set).")
    args = parser.parse_args()

    config = load_config()
    if args.price_provider:
        config["price_provider"] = args.price_provider
        save_config(config)
    price_provider = config["price_provider"]

    use_live_prices = not args.no_live_price
    if use_live_prices and price_provider == "yfinance" and not _check_yfinance_available():
        print("⚠ yfinance is not installed — live prices are disabled for this run; "
              "every entry/exit will fall back to the xlsx's last_close.\n"
              "  Install it with: pip install yfinance\n")
    if use_live_prices and price_provider == "dhan" and not DHAN_AVAILABLE:
        print(f"⚠ dhandata is not available ({_DHAN_IMPORT_ERROR}) — live prices are disabled "
              "for this run; every entry/exit will fall back to the xlsx's last_close.\n")

    run_date_obj = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()
    run_date = run_date_obj.isoformat()

    trade_log = load_trade_log()
    equity_history = load_equity_history()

    if not TRADE_LOG_PATH.exists() and not len(trade_log):
        trade_log = bootstrap_from_legacy()

    if not args.force and len(equity_history) and run_date in equity_history["run_date"].astype(str).values:
        print(f"⚠ equity_history.csv already has a row for {run_date}. Pass --force to redo it.")
        sys.exit(1)

    starting_equity = (
        float(equity_history.iloc[-1]["ending_equity"])
        if len(equity_history) else float(args.seed_equity)
    )

    write_holdings_csv(trade_log)

    ranked, market_ok, idx_close = run_ranking(
        args.file, args.top_n, starting_equity, args.risk_factor, args.min_liquidity
    )

    trade_log, closed_now, opened_now, missing_tickers, skipped_no_size, skipped_insufficient_cash = reconcile_trades(
        trade_log, ranked, run_date_obj, starting_equity, cash_flow=args.cash_flow,
        use_live_prices=use_live_prices, price_provider=price_provider
    )

    realized_pnl_this_run = float(closed_now["pnl_amount"].sum()) if len(closed_now) else 0.0
    open_rows = trade_log[trade_log["status"] == "OPEN"]
    deployed_capital = float((open_rows["entry_price"].astype(float) * open_rows["shares"].astype(float)).sum())
    ending_equity = starting_equity + realized_pnl_this_run + args.cash_flow

    equity_row = dict(
        run_date=run_date,
        starting_equity=starting_equity,
        realized_pnl_this_run=realized_pnl_this_run,
        cash_flow_adjustment=args.cash_flow,
        ending_equity=ending_equity,
        deployed_capital=deployed_capital,
        cash_available=ending_equity - deployed_capital,
        open_positions=len(open_rows),
        nifty500_close=idx_close,
    )
    equity_history_new = pd.concat([equity_history, pd.DataFrame([equity_row])], ignore_index=True)

    stats = compute_performance_stats(trade_log, equity_history_new)

    if args.dry_run:
        print("\n[DRY RUN — nothing written]")
        print_summary(run_date, closed_now, opened_now, trade_log, equity_row, stats,
                       missing_tickers, skipped_no_size, skipped_insufficient_cash,
                       REPORTS_DIR / "dashboard_latest.html (not written)", market_ok, price_provider)
        return

    save_trade_log(trade_log)
    save_equity_history(equity_history_new)
    write_holdings_csv(trade_log)
    dashboard_path = render_dashboard(trade_log, equity_history_new, stats, run_date, ranked)
    print_summary(run_date, closed_now, opened_now, trade_log, equity_row, stats,
                   missing_tickers, skipped_no_size, skipped_insufficient_cash,
                   dashboard_path, market_ok, price_provider)

    if not args.no_browser:
        import webbrowser
        webbrowser.open(dashboard_path.resolve().as_uri())


def save_trade_log(df: pd.DataFrame) -> None:
    df[TRADE_LOG_COLUMNS].to_csv(TRADE_LOG_PATH, index=False)


def save_equity_history(df: pd.DataFrame) -> None:
    df[EQUITY_HISTORY_COLUMNS].to_csv(EQUITY_HISTORY_PATH, index=False)


if __name__ == "__main__":
    main()
