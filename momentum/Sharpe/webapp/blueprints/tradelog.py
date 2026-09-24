"""
Tradelog & MTM. Ported from sharpe_dashboard_dhan.py's tab_tradelog block.

Every mutating route (add/edit/delete) checks settings.READ_ONLY itself and, when
blocked, re-renders the same content partial with an explanatory banner and HTTP
200 -- not a 403 -- because HTMX does not swap in error-status response bodies by
default, and a blocked write is an expected state here, not a server error.

The row-click-to-prefill flow: clicking an Active Holdings row does
hx-get /tradelog/row-form?ticker=..&qty=..&price=.. which swaps just the
Log New Transaction form (#trade-form-wrapper). Every other mutation (add,
edit, delete) swaps the whole #tradelog-content div, since it can change
holdings, history and the form's ticker list all at once.
"""
import datetime
import uuid

from flask import Blueprint, render_template, request

from webapp import settings
from webapp.core import config_store, dhan_client, rankings, tradelog

bp = Blueprint("tradelog", __name__)


def _effective_prices(bundle: rankings.Bundle) -> dict:
    """bundle.latest_prices (the workbook's last close), overridden by whatever
    the "Refresh Live Market Prices" button last fetched -- same override
    semantics as the dashboard's st.session_state.live_prices. Shared with
    Rankings' LTP column via dhan_client.apply_cached_prices()."""
    return dhan_client.apply_cached_prices(bundle.latest_prices)


def _build_context(universe: str, bundle: rankings.Bundle, *, form_ticker=None,
                    form_qty=None, form_price=None, error=None, success=None,
                    editing_id=None) -> dict:
    live_prices = _effective_prices(bundle)
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, live_prices)
    holdings_metrics = pnl["holdings_metrics"]
    cached_live = dhan_client.get_cached_live_prices()

    total_invested = sum(h["Cost Value"] for h in holdings_metrics)
    total_market = sum(h["Market Value"] for h in holdings_metrics)
    total_unrealized = total_market - total_invested
    total_unrealized_pct = (total_unrealized / total_invested * 100) if total_invested > 0 else 0.0

    holdings_rows = sorted(holdings_metrics, key=lambda h: h["Unrealized PnL"], reverse=True)
    for h in holdings_rows:
        h["row_class"] = ("row-regime" if h["Unrealized PnL"] > 0
                           else "row-breach" if h["Unrealized PnL"] < 0 else "")
        fbd = h["First Buy Date"]
        h["First Buy Date"] = fbd.isoformat() if hasattr(fbd, "isoformat") else fbd

    history_rows = []
    for tx in reversed(tl):
        history_rows.append({
            **tx,
            "total_value": tx["quantity"] * tx["price"],
            "row_class": "row-regime" if tx["action"].upper() == "BUY" else "row-breach",
        })

    stock_tickers = list(bundle.prices_df.index)
    default_ticker = form_ticker if form_ticker in stock_tickers else (stock_tickers[0] if stock_tickers else "")
    default_qty = form_qty if form_qty else 10
    default_price = form_price if form_price is not None else round(live_prices.get(default_ticker, 0.0), 2)

    editing_tx = next((tx for tx in tl if tx["id"] == editing_id), None) if editing_id else None

    return {
        "universe": universe,
        "total_invested": total_invested, "total_market": total_market,
        "total_unrealized": total_unrealized, "total_unrealized_pct": total_unrealized_pct,
        "realized_pnl": pnl["realized_pnl"],
        "holdings_rows": holdings_rows,
        "history_rows": history_rows,
        "stock_tickers": stock_tickers,
        "form": {"ticker": default_ticker, "qty": default_qty, "price": default_price},
        "error": error, "success": success,
        "editing_tx": editing_tx,
        "today": datetime.date.today().isoformat(),
        "live_price_source": cached_live["source"] if cached_live else None,
    }


def _bundle_and_universe():
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    return bundle, universe


@bp.route("/tradelog")
def index():
    bundle, universe = _bundle_and_universe()
    ctx = _build_context(universe, bundle)
    return render_template("tradelog.html", **ctx)


@bp.route("/tradelog/row-form")
def row_form():
    bundle, universe = _bundle_and_universe()
    ctx = _build_context(
        universe, bundle,
        form_ticker=request.args.get("ticker"),
        form_qty=request.args.get("qty", type=int),
        form_price=request.args.get("price", type=float),
    )
    return render_template("_trade_form.html", **ctx)


@bp.route("/tradelog/edit-form/<tx_id>")
def edit_form(tx_id):
    bundle, universe = _bundle_and_universe()
    ctx = _build_context(universe, bundle, editing_id=tx_id)
    return render_template("_edit_form.html", **ctx)


@bp.route("/tradelog/refresh-prices", methods=["POST"])
def refresh_prices():
    """Fetches live LTP for currently-held tickers (Dhan, Yahoo Finance fallback) and
    caches it -- overriding the workbook's last price everywhere in this tab until the
    next refresh. Not gated by READ_ONLY: it's a live external read with no file write,
    same category as the VIX/cap-tier fetches elsewhere."""
    bundle, universe = _bundle_and_universe()
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, _effective_prices(bundle))
    held_tickers = [h["Ticker"] for h in pnl["holdings_metrics"]]

    success, error = None, None
    if not held_tickers:
        error = "No active holdings to refresh."
    else:
        result = dhan_client.refresh_live_prices(held_tickers)
        if result["prices"]:
            success = f"Live prices refreshed from {result['source']}."
            if result["unmatched"]:
                success += (f" {len(result['unmatched'])} ticker(s) could not be resolved: "
                            f"{', '.join(result['unmatched'])}.")
        else:
            error = "Could not fetch live prices from Dhan or Yahoo Finance. Try again shortly."

    ctx = _build_context(universe, bundle, error=error, success=success)
    return render_template("_tradelog_content.html", **ctx)


@bp.route("/tradelog/add", methods=["POST"])
def add():
    bundle, universe = _bundle_and_universe()

    if settings.READ_ONLY:
        ctx = _build_context(universe, bundle, error="This webapp is read-only right now "
                              "— trade entry stays in the Streamlit dashboard for now.")
        return render_template("_tradelog_content.html", **ctx)

    ticker = request.form.get("ticker", "")
    action = request.form.get("action", "BUY").upper()
    date_str = request.form.get("date") or datetime.date.today().isoformat()
    try:
        qty = int(request.form.get("quantity", 0))
        price = float(request.form.get("price", 0))
    except ValueError:
        qty, price = 0, 0.0

    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, _effective_prices(bundle))
    curr_qty = pnl["active_holdings"].get(ticker, {}).get("qty", 0.0)

    error = None
    if ticker not in bundle.prices_df.index:
        error = f"Unknown ticker: {ticker}"
    elif qty <= 0 or price <= 0:
        error = "Quantity and price must be greater than zero."
    elif action == "SELL" and qty > curr_qty:
        error = (f"Cannot SELL {qty} shares of {ticker} — you only hold "
                 f"{curr_qty:.0f} shares. Trade not recorded.")

    success = None
    if not error:
        candidate = tl + [{
            "id": str(uuid.uuid4()), "date": date_str,
            "timestamp": datetime.datetime.now().isoformat(),
            "ticker": ticker, "action": action, "quantity": qty, "price": price,
        }]
        is_valid, err_msg = tradelog.validate_tradelog_integrity(candidate)
        if not is_valid:
            error = f"Trade rejected — would cause inconsistent state: {err_msg}"
        else:
            tradelog.save_tradelog(universe, candidate)
            new_calc = tradelog.calculate_holdings_and_pnl(candidate, _effective_prices(bundle))
            tradelog.sync_to_positions_ledger(config_store.positions_ledger_path(universe),
                                               new_calc["active_holdings"])
            success = f"Recorded {action} {qty} shares of {ticker} @ Rs {price:.2f}."

    ctx = _build_context(universe, bundle, error=error, success=success)
    return render_template("_tradelog_content.html", **ctx)


@bp.route("/tradelog/edit/<tx_id>", methods=["POST"])
def edit(tx_id):
    bundle, universe = _bundle_and_universe()

    if settings.READ_ONLY:
        ctx = _build_context(universe, bundle, error="This webapp is read-only right now "
                              "— editing trades stays in the Streamlit dashboard for now.")
        return render_template("_tradelog_content.html", **ctx)

    tl = tradelog.load_tradelog(universe)
    idx = next((i for i, tx in enumerate(tl) if tx["id"] == tx_id), None)

    error, success = None, None
    if idx is None:
        error = "Transaction not found — it may have already been deleted."
    else:
        try:
            qty = int(request.form.get("quantity", 0))
            price = float(request.form.get("price", 0))
        except ValueError:
            qty, price = 0, 0.0
        ticker = request.form.get("ticker", tl[idx]["ticker"])
        action = request.form.get("action", tl[idx]["action"]).upper()
        date_str = request.form.get("date") or tl[idx]["date"]

        if qty <= 0 or price <= 0:
            error = "Quantity and price must be greater than zero."
        else:
            candidate = [tx.copy() for tx in tl]
            candidate[idx].update({"ticker": ticker, "action": action,
                                    "date": date_str, "quantity": qty, "price": price})
            is_valid, err_msg = tradelog.validate_tradelog_integrity(candidate)
            if not is_valid:
                error = (f"Edit rejected — would cause inconsistent holdings: {err_msg}. "
                         f"The original transaction was not modified.")
            else:
                tradelog.save_tradelog(universe, candidate)
                new_calc = tradelog.calculate_holdings_and_pnl(candidate, _effective_prices(bundle))
                tradelog.sync_to_positions_ledger(config_store.positions_ledger_path(universe),
                                                   new_calc["active_holdings"])
                success = "Transaction updated and positions ledger synced."

    ctx = _build_context(universe, bundle, error=error, success=success)
    return render_template("_tradelog_content.html", **ctx)


@bp.route("/tradelog/delete", methods=["POST"])
def delete():
    bundle, universe = _bundle_and_universe()

    if settings.READ_ONLY:
        ctx = _build_context(universe, bundle, error="This webapp is read-only right now "
                              "— deleting trades stays in the Streamlit dashboard for now.")
        return render_template("_tradelog_content.html", **ctx)

    ids_to_delete = set(request.form.getlist("tx_id"))
    tl = tradelog.load_tradelog(universe)

    error, success = None, None
    if not ids_to_delete:
        error = "No transactions selected."
    else:
        candidate = [tx for tx in tl if tx["id"] not in ids_to_delete]
        is_valid, err_msg = tradelog.validate_tradelog_integrity(candidate)
        if not is_valid:
            error = (f"Deletion rejected — removing these transaction(s) would cause "
                     f"inconsistent holdings: {err_msg}. No transactions were deleted.")
        else:
            tradelog.save_tradelog(universe, candidate)
            new_calc = tradelog.calculate_holdings_and_pnl(candidate, _effective_prices(bundle))
            tradelog.sync_to_positions_ledger(config_store.positions_ledger_path(universe),
                                               new_calc["active_holdings"])
            success = f"Deleted {len(ids_to_delete)} transaction(s) and synced positions ledger."

    ctx = _build_context(universe, bundle, error=error, success=success)
    return render_template("_tradelog_content.html", **ctx)
