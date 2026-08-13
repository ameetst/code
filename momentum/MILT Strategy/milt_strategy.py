"""
milt_strategy.py
=================
Rule-based implementation of Rakesh Pujara's "CWA MILT 25" (Momentum
Investing Long Term) strategy, as described on the Sanjay Kathuria Podcast
EP70. Full rule writeup: CWA_MILT_25_Momentum_Strategy_Summary.txt.

Rules implemented
------------------
Universe   : N750 (top 750 NSE stocks by index membership — already
             enforces the ~2000 Cr market-cap / liquidity floor the
             strategy calls for; no separate filter applied here).
Timeframe  : Weekly bars (Friday close). Scans run after Friday close;
             all entries/exits execute at the following Monday's open.
Entry      : Weekly close > Bollinger upper band (30-period SMA + 3.7 x
             stdev) on Friday -> buy at Monday's open.
Sizing     : Equal-weight 4% of *current mark-to-market portfolio equity*
             (cash + open positions) per new position, max 25 positions.
             Equity compounds run over run via MILT_portfolio_state.json
             (cash balance) -- this is NOT 4% of a fixed starting capital.
Tie-break  : If more than the available slots trigger in one week, keep
             the top candidates by 12-month ROC.
Exits (ANY of the following, evaluated on Friday close)      :
  1. Stop loss      : close <= 0.80 x entry_price (20% drawdown from entry)
  2. Trend reversal : close < 23-week SMA
  3. Vertical fall   : close < (highest weekly close since entry) -
     protection        1.8 x ATR(14)   [chandelier-style trailing stop]
No rebalancing of winners; FIFO — existing holdings are never displaced
by new signals, only removed via the exit rules above.

Assumptions flagged (not stated verbatim in the source video/summary)
-----------------------------------------------------------------------
* Bollinger Band period: the source video only stated the 3.7 SD multiplier,
  not the basis period. Originally defaulted to the textbook 20 weeks; since
  changed to 30 weeks after backtesting a sweep (20/30/40/50/60/70) showed
  30 gave the best risk-adjusted result (Sharpe 1.45, CAGR +31.97%) on a
  smooth, non-spiky curve -- see backtest/milt_variant_backtest.py and
  MILT_Implementation_Summary.txt for the sweep results. This remains a
  tuned choice, not a stated rule -- re-validate if the market regime shifts.
* The "ATR 14-period, 1.8x multiplier" exit is implemented as a chandelier
  trailing stop (peak close since entry - 1.8*ATR), which matches the
  stated purpose ("lock in gains before a vertical move gives it all
  back") better than a fixed distance from the entry price.
* Tie-break ranking uses 12-month price ROC (Rate of Change) -- the
  strategy doc allows either RS or 12M ROC; ROC is used here since it
  needs no extra data beyond what's already loaded.

Usage
-----
    python milt_strategy.py [--file MILT_N750_updated.xlsx] [--dry-run]
                             [--update] [--ledger PATH] [--state PATH]

Persistent state (created/updated on each non-dry-run call):
  MILT_positions_ledger.json  — open positions (entry price, shares, peak close)
  MILT_portfolio_state.json   — cash balance (drives compounding position sizing)
  MILT_tradelog.json          — closed-trade history
  MILT_equity_history.json    — one NAV snapshot per calendar day run

Configuration (MILT_config.json, editable via the GUI's Configuration tab
or by hand): capital, max_positions, bb_window, bb_std, stop_loss_pct,
atr_period, atr_mult. Falls back to CONFIG_DEFAULTS if the file is absent.
Every script that imports these constants from milt_strategy (backtest/*.py,
milt_gui.py) picks up whatever is currently configured, since the config is
loaded once at import time into the same-named module constants below.
"""

import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import momentum_lib as ml

# ── STRATEGY CONFIG ────────────────────────────────────────────────────────────
# The parameters below are user-configurable (via MILT_config.json, or the
# GUI's Configuration tab) -- CONFIG_DEFAULTS holds the current
# backtest-validated values (see backtest/milt_variant_backtest.py's sweep:
# 20/30/40/50/60/70 tested for bb_window, 30 gave the best Sharpe 1.45 /
# CAGR +31.97% on a smooth, non-spiky curve). Values not exposed for
# configuration (POSITION_PCT, MA_PERIOD, ROC_LOOKBACK_DAYS) stay as fixed
# constants below the config block.
DEFAULT_FILE = "MILT_N750_updated.xlsx"
CONFIG_FILE  = "MILT_config.json"

CONFIG_DEFAULTS = {
    "capital":       1_500_000,
    "max_positions": 25,
    "bb_window":     30,    # weeks
    "bb_std":        3.7,
    "stop_loss_pct": 0.20,  # 20% drop from entry price
    "atr_period":    14,    # weeks
    "atr_mult":      1.8,
}


def load_milt_config(path: str = None) -> dict:
    """Load strategy parameters from MILT_config.json, merged over CONFIG_DEFAULTS."""
    path = path or CONFIG_FILE
    cfg = dict(CONFIG_DEFAULTS)
    p = Path(path)
    if p.exists():
        try:
            with open(p, "r") as f:
                saved = json.load(f)
            cfg.update({k: v for k, v in saved.items() if k in CONFIG_DEFAULTS})
        except Exception:
            pass  # fall back to defaults on any read error
    return cfg


def save_milt_config(cfg: dict, path: str = None) -> Path:
    """Save only known keys to MILT_config.json."""
    path = path or CONFIG_FILE
    to_save = {k: cfg[k] for k in CONFIG_DEFAULTS if k in cfg}
    p = Path(path)
    with open(p, "w") as f:
        json.dump(to_save, f, indent=2)
    return p


_cfg = load_milt_config()
CAPITAL       = _cfg["capital"]
MAX_POSITIONS = _cfg["max_positions"]
BB_WINDOW     = _cfg["bb_window"]
BB_STD        = _cfg["bb_std"]
STOP_LOSS_PCT = _cfg["stop_loss_pct"]
ATR_PERIOD    = _cfg["atr_period"]
ATR_MULT      = _cfg["atr_mult"]

# Not exposed for configuration -- unchanged from the strategy doc / earlier tuning.
POSITION_PCT      = 0.04   # 4% equal-weight initial allocation
MA_PERIOD         = 23     # weeks -- 23-week trend exit
ROC_LOOKBACK_DAYS = 252    # ~12 months of daily bars, for tie-break

LEDGER_FILE   = "MILT_positions_ledger.json"
TRADELOG_FILE = "MILT_tradelog.json"
STATE_FILE    = "MILT_portfolio_state.json"
EQUITY_HISTORY_FILE = "MILT_equity_history.json"


# ── LEDGER (same pattern as Sharpe.py: JSON + atomic write + .bak) ────────────

def load_ledger(path: str) -> dict:
    """
    Returns dict: { ticker: {entry_date, entry_price, shares,
                              peak_close, entry_week} }
    """
    p = Path(path)
    if not p.exists():
        print(f"  Ledger not found at '{path}' -- starting with empty ledger.")
        return {}
    with open(p, "r") as f:
        raw = json.load(f)
    ledger = {}
    for ticker, rec in raw.items():
        try:
            ledger[ticker] = {
                "entry_date":  datetime.date.fromisoformat(rec["entry_date"]),
                "entry_price": float(rec["entry_price"]),
                "shares":      int(rec["shares"]),
                "peak_close":  float(rec["peak_close"]),
                "entry_week":  rec["entry_week"],
            }
        except (KeyError, ValueError) as e:
            print(f"  Warning: skipping malformed ledger entry for {ticker}: {e}")
    print(f"  Ledger loaded: {len(ledger)} open position(s) from '{path}'")
    return ledger


def save_ledger(ledger: dict, path: str):
    serialisable = {
        ticker: {
            "entry_date":  rec["entry_date"].isoformat(),
            "entry_price": rec["entry_price"],
            "shares":      rec["shares"],
            "peak_close":  rec["peak_close"],
            "entry_week":  rec["entry_week"],
        }
        for ticker, rec in ledger.items()
    }
    p = Path(path)
    tmp_path = p.with_suffix(".tmp")
    bak_path = p.with_suffix(".bak")
    try:
        with open(tmp_path, "w") as f:
            json.dump(serialisable, f, indent=2, default=str)
        if p.exists():
            shutil.copy2(p, bak_path)
        shutil.move(str(tmp_path), str(p))
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise e
    print(f"  Ledger saved: {len(ledger)} position(s) -> '{path}'")


def append_tradelog(record: dict, path: str = TRADELOG_FILE):
    p = Path(path)
    trades = []
    if p.exists():
        with open(p, "r") as f:
            trades = json.load(f)
    trades.append(record)
    with open(p, "w") as f:
        json.dump(trades, f, indent=2, default=str)


# ── PORTFOLIO STATE (cash balance, for compounding position sizing) ───────────
# New entries are sized at 4% of *current* mark-to-market equity (cash + open
# positions), not a fixed capital figure -- matching milt_backtest.py and the
# NAV-compounding framing in the strategy doc. Without this, every entry would
# forever be sized off the day-one capital, never growing (or shrinking) with
# the portfolio's actual performance.

def load_state(path: str, default_capital: float) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"  No portfolio state at '{path}' -- initialising cash = Rs {default_capital:,.0f}.")
        return {"cash": default_capital}
    with open(p, "r") as f:
        state = json.load(f)
    print(f"  Portfolio state loaded: cash = Rs {state['cash']:,.0f} from '{path}'")
    return state


def save_state(state: dict, path: str):
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_equity_history(path: str) -> list:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r") as f:
        return json.load(f)


def append_equity_history(history: list, record: dict, path: str):
    # one record per calendar day -- overwrite if we already ran today
    history = [h for h in history if h["date"] != record["date"]]
    history.append(record)
    with open(path, "w") as f:
        json.dump(history, f, indent=2, default=str)


# ── SIGNAL ENGINE ──────────────────────────────────────────────────────────────

def build_all_weekly(ohlc: dict) -> dict:
    """Weekly OHLC + indicators for every ticker. Returns {ticker: DataFrame}."""
    weekly = {}
    skipped = 0
    for t in ohlc["tickers"]:
        wdf = ml.build_weekly_indicators(
            ohlc, t,
            bb_window=BB_WINDOW, bb_std=BB_STD,
            ma_period=MA_PERIOD, atr_period=ATR_PERIOD,
        )
        if wdf is None or len(wdf) < BB_WINDOW + 1:
            skipped += 1
            continue
        weekly[t] = wdf
    print(f"  Weekly indicators built for {len(weekly)} tickers "
          f"({skipped} skipped -- insufficient OHLC history)")
    return weekly


def compute_roc_12m(close_df: pd.DataFrame, tickers: list) -> pd.Series:
    """12-month (approx. 252 trading day) price rate-of-change, for tie-breaking."""
    out = {}
    for t in tickers:
        px = close_df.loc[t].dropna() if t in close_df.index else pd.Series(dtype=float)
        if len(px) > ROC_LOOKBACK_DAYS:
            out[t] = px.iloc[-1] / px.iloc[-ROC_LOOKBACK_DAYS] - 1.0
        else:
            out[t] = np.nan
    return pd.Series(out)


def latest_completed_bar(wdf: pd.DataFrame, asof: datetime.date = None):
    """
    The most recent weekly bar whose Friday label is <= `asof` (default:
    today). Guards against picking up a still-forming current week: if you
    run this script on, say, Monday or Tuesday (after --update has already
    pulled that day's fresh daily bar), pandas' W-FRI resample will have
    started a new bucket for "this Friday" using only 1-2 days of data --
    using .iloc[-1] blindly would evaluate signals against that incomplete
    week instead of the last *actually completed* Friday close.

    Returns a pd.Series (the row) or None if no bar exists on/before asof.
    """
    if asof is None:
        asof = datetime.date.today()
    valid = wdf[wdf.index.date <= asof]
    if valid.empty:
        return None
    return valid.iloc[-1]


def next_monday_open(open_s: pd.Series, after_date) -> tuple:
    """
    First available daily Open strictly after `after_date` (the Friday the
    signal fired). Returns (exec_date, exec_price) or (None, None) if no
    later trading day exists in the data yet (signal pending execution).
    """
    px = open_s.dropna()
    px.index = pd.to_datetime(px.index)
    after_ts = pd.Timestamp(after_date)
    later = px[px.index > after_ts]
    if later.empty:
        return None, None
    return later.index[0].date(), float(later.iloc[0])


def evaluate_exits(ledger: dict, weekly: dict, asof: datetime.date = None) -> list:
    """
    Check the 3 exit rules on the latest *completed* weekly bar (Friday <=
    asof) for every held position. Returns list of exit dicts:
      {ticker, reason, signal_week, signal_close}
    """
    exits = []
    for ticker, pos in ledger.items():
        wdf = weekly.get(ticker)
        if wdf is None or wdf.empty:
            continue
        last = latest_completed_bar(wdf, asof)
        if last is None:
            continue
        close = last["close"]

        # Update running peak close since entry (needed for the chandelier stop)
        peak = max(pos["peak_close"], close)
        pos["peak_close"] = peak

        reason = None
        if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
            reason = f"20% stop loss (close {close:.2f} <= {pos['entry_price']*(1-STOP_LOSS_PCT):.2f})"
        elif pd.notna(last["sma23"]) and close < last["sma23"]:
            reason = f"trend reversal (close {close:.2f} < 23W MA {last['sma23']:.2f})"
        elif pd.notna(last["atr"]):
            chandelier = peak - ATR_MULT * last["atr"]
            if close < chandelier:
                reason = (f"ATR vertical-fall stop (close {close:.2f} < "
                          f"peak {peak:.2f} - {ATR_MULT}xATR14 {last['atr']:.2f} = {chandelier:.2f})")

        if reason:
            exits.append({
                "ticker": ticker,
                "reason": reason,
                "signal_week": last.name.date().isoformat(),
                "signal_close": float(close),
            })
    return exits


def evaluate_entries(weekly: dict, held: set, asof: datetime.date = None) -> list:
    """
    Scan every non-held ticker's latest *completed* weekly bar (Friday <=
    asof) for a Bollinger breakout (close > bb_upper). Returns list of
    candidate dicts: {ticker, signal_week, signal_close, bb_upper}
    """
    candidates = []
    for ticker, wdf in weekly.items():
        if ticker in held:
            continue
        last = latest_completed_bar(wdf, asof)
        if last is None:
            continue
        if pd.isna(last["bb_upper"]):
            continue
        if last["close"] > last["bb_upper"]:
            candidates.append({
                "ticker": ticker,
                "signal_week": last.name.date().isoformat(),
                "signal_close": float(last["close"]),
                "bb_upper": float(last["bb_upper"]),
            })
    return candidates


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MILT weekly momentum strategy scan.")
    parser.add_argument("--file", default=DEFAULT_FILE,
                        help=f"OHLCV workbook (default: {DEFAULT_FILE})")
    parser.add_argument("--ledger", default=LEDGER_FILE,
                        help=f"Position ledger path (default: {LEDGER_FILE})")
    parser.add_argument("--state", default=STATE_FILE,
                        help=f"Portfolio cash-state path (default: {STATE_FILE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print recommendations without saving ledger/state changes")
    parser.add_argument("--update", action="store_true",
                        help="Run milt_update_prices.py first to refresh OHLCV data")
    args = parser.parse_args()

    if args.update:
        print(f"{'='*70}\nSTEP 1: Refreshing OHLCV data via milt_update_prices.py\n{'='*70}\n")
        script = Path(__file__).resolve().parent / "milt_update_prices.py"
        ret = subprocess.run([sys.executable, str(script)], cwd=str(script.parent))
        if ret.returncode != 0:
            print(f"ERROR: milt_update_prices.py failed (exit {ret.returncode})")
            sys.exit(1)
        print()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found. Run with --update first, or "
              f"run milt_update_prices.py directly.")
        sys.exit(1)

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | "
          f"{len(ohlc['dates'])} daily date columns "
          f"({ohlc['dates'][0]} -> {ohlc['dates'][-1]})\n")

    print("Building weekly OHLC + indicators (Bollinger/ATR/23W MA) ...")
    weekly = build_all_weekly(ohlc)
    print()

    print("Loading position ledger ...")
    ledger = load_ledger(args.ledger)
    print()

    print("Loading portfolio cash state ...")
    state = load_state(args.state, default_capital=CAPITAL)
    cash = state["cash"]
    print()

    asof = datetime.date.today()

    # ── 1. Exits ────────────────────────────────────────────────────────────────
    print(f"Evaluating exit rules on held positions (as of {asof}) ...")
    exits = evaluate_exits(ledger, weekly, asof=asof)
    for e in exits:
        print(f"  EXIT  {e['ticker']:<16} {e['reason']}")
    if not exits:
        print("  No exits triggered.")
    print()

    executed_exits = []
    for e in exits:
        ticker = e["ticker"]
        signal_date = datetime.date.fromisoformat(e["signal_week"])
        exec_date, exec_price = next_monday_open(ohlc["open"].loc[ticker], signal_date)
        if exec_date is None:
            print(f"  NOTE: {ticker} exit signalled but no Monday-open data yet "
                  f"(pending execution) -- keeping in ledger for now.")
            continue
        pos = ledger.pop(ticker)
        proceeds = pos["shares"] * exec_price
        cash += proceeds
        pnl_pct = exec_price / pos["entry_price"] - 1.0
        executed_exits.append({
            "ticker": ticker, "reason": e["reason"],
            "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
            "exit_date": exec_date.isoformat(), "exit_price": exec_price,
            "shares": pos["shares"], "pnl_pct": round(pnl_pct * 100, 2),
            "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
        })

    # ── 2. Entries ──────────────────────────────────────────────────────────────
    available_slots = MAX_POSITIONS - len(ledger)
    print(f"Scanning for entry signals ({available_slots} slot(s) available) ...")
    candidates = evaluate_entries(weekly, held=set(ledger.keys()), asof=asof)
    print(f"  {len(candidates)} candidate breakout(s) found")

    if candidates and available_slots > 0:
        roc = compute_roc_12m(ohlc["close"], [c["ticker"] for c in candidates])
        for c in candidates:
            c["roc_12m"] = float(roc.get(c["ticker"], np.nan))
        candidates.sort(key=lambda c: (c["roc_12m"] if pd.notna(c["roc_12m"]) else -999),
                        reverse=True)
        selected = candidates[:available_slots]
        if len(candidates) > available_slots:
            print(f"  {len(candidates)} signals > {available_slots} slot(s) -- "
                  f"ranking by 12M ROC, keeping top {available_slots}")
    else:
        selected = []

    # Size new entries off *current* mark-to-market equity (cash + remaining
    # positions, marked at their latest weekly close), not the static CAPITAL
    # constant -- so allocations compound with the portfolio's actual growth,
    # matching milt_backtest.py.
    mtm_positions = sum(
        pos["shares"] * weekly[t].iloc[-1]["close"]
        for t, pos in ledger.items() if t in weekly
    )
    equity = cash + mtm_positions
    alloc = equity * POSITION_PCT
    print(f"  Mark-to-market equity: Rs {equity:,.0f} (cash Rs {cash:,.0f} + "
          f"positions Rs {mtm_positions:,.0f}) -> 4% allocation = Rs {alloc:,.0f}\n")

    executed_entries = []
    for c in selected:
        ticker = c["ticker"]
        signal_date = datetime.date.fromisoformat(c["signal_week"])
        exec_date, exec_price = next_monday_open(ohlc["open"].loc[ticker], signal_date)
        if exec_date is None:
            print(f"  NOTE: {ticker} entry signalled but no Monday-open data yet "
                  f"(pending execution) -- skipping this run.")
            continue
        shares = int(alloc // exec_price)
        if shares <= 0:
            continue
        cost = shares * exec_price
        if cost > cash:
            print(f"  NOTE: {ticker} entry skipped -- insufficient cash "
                  f"(need Rs {cost:,.0f}, have Rs {cash:,.0f}).")
            continue
        cash -= cost
        ledger[ticker] = {
            "entry_date": exec_date, "entry_price": exec_price,
            "shares": shares, "peak_close": exec_price,
            "entry_week": c["signal_week"],
        }
        executed_entries.append({**c, "exec_date": exec_date.isoformat(),
                                 "exec_price": exec_price, "shares": shares})
        print(f"  ENTER {ticker:<16} signal week {c['signal_week']} close {c['signal_close']:.2f} "
              f"(BB upper {c['bb_upper']:.2f}) -> buy {shares} sh @ {exec_price:.2f} on {exec_date}")

    print()

    # ── 3. Final mark-to-market + save ─────────────────────────────────────────
    final_mtm = sum(
        pos["shares"] * weekly[t].iloc[-1]["close"]
        for t, pos in ledger.items() if t in weekly
    )
    final_equity = cash + final_mtm
    today_str = datetime.date.today().isoformat()

    if args.dry_run:
        print("[DRY RUN] Ledger/state changes NOT saved.")
    else:
        save_ledger(ledger, args.ledger)
        save_state({"cash": cash}, args.state)
        for rec in executed_exits:
            append_tradelog(rec)
        history = load_equity_history(EQUITY_HISTORY_FILE)
        append_equity_history(history, {
            "date": today_str, "nav": round(final_equity, 2),
            "cash": round(cash, 2), "n_positions": len(ledger),
        }, EQUITY_HISTORY_FILE)

    # ── 4. Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Exits executed   : {len(executed_exits)}")
    for e in executed_exits:
        print(f"    {e['ticker']:<16} {e['pnl_pct']:+.2f}%  ({e['reason']})")
    print(f"  Entries executed : {len(executed_entries)}")
    for e in executed_entries:
        print(f"    {e['ticker']:<16} {e['shares']} sh @ {e['exec_price']:.2f}")
    n_held = len(ledger)
    print(f"  Open positions   : {n_held} / {MAX_POSITIONS}")
    print(f"  Cash             : Rs {cash:,.0f}")
    print(f"  Positions (MTM)  : Rs {final_mtm:,.0f}")
    print(f"  Total equity     : Rs {final_equity:,.0f}")
    print(f"  Return vs Rs {CAPITAL:,.0f} starting capital: "
          f"{(final_equity/CAPITAL - 1)*100:+.2f}%")


if __name__ == "__main__":
    main()
