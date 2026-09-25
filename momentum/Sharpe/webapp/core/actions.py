"""
Actions Monitor. Ported from the tab_exits block of sharpe_dashboard_dhan.py:
exit-signal evaluation for held positions, and inverse-vol-weighted entry
candidates for the cash freed up (or already available) after those exits.

Read-only difference from the dashboard: load_ledger reads the positions
ledger as-is and never re-syncs it from the tradelog first.
"""
import datetime
import json

import pandas as pd

import momentum_lib as ml

from webapp.core import config_store, rankings

HOLD_LOCK_DAYS = 28
finite = rankings.finite  # reuse the NaN/inf -> None helper


def load_ledger(universe: str) -> dict:
    """{ticker: {entry_date: date, entry_price: float}} from the positions ledger."""
    path = config_store.positions_ledger_path(universe)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    ledger = {}
    for t, rec in raw.items():
        try:
            ledger[t] = {
                "entry_date": datetime.date.fromisoformat(rec["entry_date"]),
                "entry_price": float(rec["entry_price"]),
            }
        except (KeyError, ValueError, TypeError):
            pass
    return ledger


def evaluate_exits(bundle: rankings.Bundle, cfg: dict, ledger: dict,
                    holdings_metrics: list[dict], today: datetime.date) -> dict:
    result = bundle.result
    hm_by_ticker = {h["Ticker"]: h for h in holdings_metrics}
    rel_dd_breach_threshold = float(cfg["rel_dd_breach_threshold"])
    hold_rank_buffer = int(cfg["hold_rank_buffer"])
    circuit_filter_enabled = bool(cfg["circuit_filter_enabled"])
    circuit_threshold = int(cfg["circuit_threshold"])
    eq_series_filter = bool(cfg["eq_series_filter"])

    rows = []
    for ticker, rec in ledger.items():
        held_days = (today - rec["entry_date"]).days
        in_result = ticker in result.index
        rank_val = finite(result.loc[ticker, "RANK"]) if in_result else None
        pct52 = finite(result.loc[ticker, "PCT_FROM_52H"]) if in_result else None
        reldd = finite(result.loc[ticker, "REL_52H_DD"]) if in_result else None

        is_52h_breach = pct52 is not None and pct52 < rankings.ELIGIBLE_52H_FLOOR
        is_reldd_breach = reldd is not None and reldd < rel_dd_breach_threshold

        is_circuit_breach = False
        if circuit_filter_enabled and in_result and "TOTAL_CIRCUIT_HITS" in result.columns:
            c_hits = finite(result.loc[ticker, "TOTAL_CIRCUIT_HITS"])
            if c_hits is not None and c_hits >= circuit_threshold:
                is_circuit_breach = True

        is_series_breach = False
        if eq_series_filter and in_result and "SERIES" in result.columns:
            series_val = result.loc[ticker, "SERIES"]
            if pd.notna(series_val) and str(series_val).strip() != "EQ":
                is_series_breach = True

        is_adtv_breach = (in_result and "ADTV_ELIGIBLE" in result.columns
                          and not bool(result.loc[ticker, "ADTV_ELIGIBLE"]))

        if is_52h_breach:
            trigger, action = "52H_BREACH", "⚠️ SELL IMMEDIATELY (52H drop)"
        elif is_circuit_breach:
            trigger, action = "CIRCUIT_BREACH", "⚠️ SELL IMMEDIATELY (Circuit limit)"
        elif is_series_breach:
            trigger, action = "SERIES_BREACH", "⚠️ SELL IMMEDIATELY (Non-EQ series)"
        elif is_adtv_breach:
            trigger, action = "ADTV_BREACH", "⚠️ SELL IMMEDIATELY (Low MDTV)"
        elif rank_val is None:
            trigger, action = "FILTER_BREACH", "⚠️ SELL IMMEDIATELY"
        elif is_reldd_breach and held_days >= HOLD_LOCK_DAYS:
            trigger, action = "REL_DD_BREACH", "\U0001f53b SELL (relative 52H drawdown)"
        elif is_reldd_breach and held_days < HOLD_LOCK_DAYS:
            trigger, action = "REL_DD_LOCK", f"\U0001f512 Locked (Rel DD, {held_days}/{HOLD_LOCK_DAYS}d)"
        elif rank_val is not None and rank_val > hold_rank_buffer and held_days >= HOLD_LOCK_DAYS:
            trigger, action = "RANK_EXIT", "\U0001f53b SELL (rank dropped)"
        elif rank_val is not None and rank_val > hold_rank_buffer and held_days < HOLD_LOCK_DAYS:
            trigger, action = "HOLD_LOCK", f"\U0001f512 Locked ({held_days}/{HOLD_LOCK_DAYS}d)"
        else:
            trigger, action = "HEALTHY", "✅ HOLD"

        hm = hm_by_ticker.get(ticker)
        curr_price = bundle.latest_prices.get(ticker, rec["entry_price"])
        shares = 0.0
        if hm:
            shares = hm["Qty"]
            curr_price = hm["Current Price"]
        unrealised_pnl = (curr_price - rec["entry_price"]) * shares

        if "SELL IMMEDIATELY" in action:
            row_class = "row-sell-immediate"
        elif "SELL" in action:
            row_class = "row-sell"
        elif "Locked" in action:
            row_class = "row-locked"
        else:
            row_class = "row-healthy"

        rows.append({
            "ticker": ticker, "action": action, "trigger": trigger, "row_class": row_class,
            "rank": int(rank_val) if rank_val is not None else None,
            "pct_52h": round(pct52, 1) if pct52 is not None else None,
            "rel_52h_dd": round(reldd, 1) if reldd is not None else None,
            "days_held": held_days,
            "entry_date": rec["entry_date"].isoformat(),
            "unrealised_pnl": round(unrealised_pnl, 0),
        })

    exit_triggers = [r for r in rows if "BREACH" in r["trigger"] or "RANK_EXIT" in r["trigger"]]
    return {"rows": rows, "exit_triggers": exit_triggers, "n_exits": len(exit_triggers)}


def entry_candidates(bundle: rankings.Bundle, cfg: dict, ledger: dict,
                      holdings_metrics: list[dict], exit_tickers: set,
                      n_new_positions: int) -> dict:
    """Inverse-vol-weighted allocation across the top-ranked names not currently held,
    capped per position at Max Position Size and scaled pro-rata if cash is short."""
    result = bundle.result
    capital = float(cfg["capital"])
    max_n = int(cfg["max_n"])
    max_position_size_inr = capital / max_n if max_n else 0.0

    held_tickers = set(ledger.keys())
    candidates = []
    for ticker in result.index:
        if ticker in held_tickers:
            continue
        if len(candidates) >= n_new_positions:
            break
        candidates.append(ticker)

    if not candidates:
        return {"status": "no_candidates"}

    cost_basis_after_exits = sum(h["Cost Value"] for h in holdings_metrics if h["Ticker"] not in exit_tickers)
    available_cash = max(0.0, capital - cost_basis_after_exits)
    if available_cash <= 0:
        return {"status": "no_cash"}

    raw_weights = {}
    for t in candidates:
        comp = finite(result.loc[t, "COMPOSITE"]) if t in result.index else None
        comp = comp if comp is not None else 1.0
        mean_vol = rankings.mean_volatility(t, bundle.prices_df)
        raw_weights[t] = comp / mean_vol if mean_vol and mean_vol > 0 else comp

    total_w = sum(raw_weights.values())
    weights = {}
    for t in raw_weights:
        nw = raw_weights[t] / total_w if total_w > 0 else 1.0 / len(raw_weights)
        capped_alloc = min(nw * capital, max_position_size_inr)
        weights[t] = capped_alloc / capital if capital > 0 else 0.0

    total_needed = sum(weights[t] * capital for t in weights)
    if total_needed > available_cash * 1.01:  # small tolerance, matches the dashboard
        insufficient_cash = True
        scale = available_cash / total_needed
    else:
        insufficient_cash = False
        scale = 1.0

    try:
        circuit_df = ml.compute_circuit_hits(bundle.prices_df, candidates, str(rankings.BAND_CSV), lookback_period=252)
    except Exception:
        circuit_df = None

    rows = []
    for t in candidates:
        if t not in weights:
            continue
        inv_amount = min(weights[t] * capital * scale, available_cash)
        ltp = bundle.latest_prices.get(t, 0.0)
        qty = int(inv_amount // ltp) if ltp > 0 else 0
        rank_val = finite(result.loc[t, "RANK"]) if t in result.index else None
        pct52 = finite(result.loc[t, "PCT_FROM_52H"]) if t in result.index else None

        circuit_hits = None
        if circuit_df is not None and t in circuit_df.index:
            circuit_hits = f"{int(circuit_df.loc[t, 'UC_COUNT'])} / {int(circuit_df.loc[t, 'LC_COUNT'])}"

        rows.append({
            "ticker": t, "rank": int(rank_val) if rank_val is not None else None,
            "pct_52h": round(pct52, 1) if pct52 is not None else None,
            "inv_amount": round(inv_amount, 0), "qty": qty, "circuit_hits": circuit_hits,
        })

    if not rows:
        return {"status": "no_allocatable"}

    return {
        "status": "ok", "rows": rows, "available_cash": available_cash,
        "total_allocation": sum(r["inv_amount"] for r in rows),
        "insufficient_cash": insufficient_cash,
    }
