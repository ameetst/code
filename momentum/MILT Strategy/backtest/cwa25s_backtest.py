"""
cwa25s_backtest.py
===================
Walk-forward weekly backtest of the "CWA 2.5-Sigma" entry/exit rules (see
../CWA 2.5 Sigma.txt for the source Pine Script), wrapped in the SAME
portfolio engine as milt_backtest.py (N750 universe, 4% of current equity
per position, max 25 positions, 12-month ROC tie-break, no rebalancing) so
the comparison against MILT isolates just the entry/exit RULE differences,
not portfolio-construction differences.

CWA 2.5-Sigma rules, as translated from the Pine Script
---------------------------------------------------------
Entry : crossover(close, bb_upper) AND close > EMA(100)
          bb_upper = SMA(close, 50) + 2.5 * stdev(close, 50)
        "Crossover" = fresh cross this bar only (close_t > bb_upper_t AND
        close_{t-1} <= bb_upper_{t-1}) -- unlike MILT's plain level check
        (close > bb_upper), which re-flags every week a stock stays above
        the band.
Exit  : ANY of (checked in this priority order, matching the Pine script's
        label priority TSL > EMA > ATR):
          1. hard_stop_20pct : close <= entry_price * 0.80
          2. ema100_break    : close < EMA(100)
          3. atr_ratchet_stop: close < trailing_stop, where trailing_stop is
             a RATCHETING running max of (close_t - 1.8*ATR14_t) since entry
             -- i.e. it only ever moves up, never down, even if ATR later
             expands. This differs from MILT's chandelier, which recomputes
             (peak_close_since_entry - 1.8*ATR_current) fresh every week and
             CAN loosen if ATR expands after the peak.

Timeframe choice
-----------------
The Pine Script itself declares no weekly resampling -- it runs on whatever
chart timeframe it's applied to (most likely daily by default in
TradingView). This backtest instead uses WEEKLY bars, resampled from the
same MILT_N750_backtest.xlsx dataset MILT's backtest uses, so this is an
apples-to-apples comparison of the two rule sets under an identical
timeframe and portfolio wrapper -- not a claim about how the indicator is
meant to be traded on TradingView. A daily-bar version would be a separate,
higher-frequency backtest.

Caveats: identical to milt_backtest.py -- survivorship bias (today's N750
list projected backward), no transaction costs/slippage, and the same
split/bonus data-quality screen is applied. See milt_backtest.py's
docstring for full detail; not repeated here.

Usage
-----
    python cwa25s_backtest.py [--file MILT_N750_backtest.xlsx] [--plot]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR     = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import MAX_POSITIONS, POSITION_PCT, STOP_LOSS_PCT, ATR_MULT, ATR_PERIOD
from milt_backtest import (
    screen_split_artifacts, _next_available,
    compute_metrics, compute_benchmark_metrics, trade_stats,
)

DEFAULT_FILE = str(BACKTEST_DIR / "MILT_N750_backtest.xlsx")
DEFAULT_CAPITAL = 1_500_000
RFR_ANNUAL = 0.07

# CWA 2.5-Sigma's own parameters (independent of MILT's BB_WINDOW/BB_STD/MA_PERIOD)
BB_WINDOW  = 50
BB_STD     = 2.5
EMA_PERIOD = 100
# ATR_PERIOD (14) and ATR_MULT (1.8) and STOP_LOSS_PCT (20%) are identical
# to MILT's, so reused directly from milt_strategy rather than redefined.


# ── DATA PREP ───────────────────────────────────────────────────────────────────

def build_all_weekly_cwa(ohlc: dict, exclude: set = None) -> dict:
    """
    Weekly bars + CWA-specific indicators (bb_upper 50/2.5, ema100, atr14,
    12M ROC, and a precomputed crossover flag) for every ticker.
    """
    exclude = exclude or set()
    weekly = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        for field in ("open", "high", "low", "close"):
            df = ohlc[field]
            if df is None or t not in df.index:
                break
        else:
            wdf = ml.resample_weekly_ohlc(
                ohlc["open"].loc[t], ohlc["high"].loc[t],
                ohlc["low"].loc[t], ohlc["close"].loc[t],
            )
            if wdf.empty or len(wdf) < BB_WINDOW + 1:
                continue
            wdf = wdf.copy()
            wdf["bb_upper"] = ml.compute_bollinger_upper(wdf["close"], BB_WINDOW, BB_STD)
            wdf["ema100"]   = ml.compute_ema(wdf["close"], EMA_PERIOD)
            wdf["atr"]      = ml.compute_atr(wdf["high"], wdf["low"], wdf["close"], ATR_PERIOD)
            wdf["roc_12m"]  = wdf["close"] / wdf["close"].shift(52) - 1.0
            # Fresh cross above the band THIS bar only (not still-above-from-last-week)
            prev_close    = wdf["close"].shift(1)
            prev_bb_upper = wdf["bb_upper"].shift(1)
            wdf["crossover"] = (wdf["close"] > wdf["bb_upper"]) & (prev_close <= prev_bb_upper)
            weekly[t] = wdf
    return weekly


# ── BACKTEST LOOP ─────────────────────────────────────────────────────────────

def run_backtest(weekly: dict, capital: float):
    master_dates = sorted(set().union(*[set(df.index) for df in weekly.values()]))
    warmup = EMA_PERIOD  # weeks -- the longest lookback CWA needs (EMA100 > BB50 > ATR14)
    if len(master_dates) <= warmup + 1:
        raise ValueError("Not enough weekly history to backtest "
                          f"(have {len(master_dates)} weeks, need > {warmup + 1}).")

    cash = capital
    positions: dict[str, dict] = {}
    trades = []
    equity_curve = []

    for i in range(warmup, len(master_dates) - 1):
        date = master_dates[i]

        # ── mark-to-market equity ──────────────────────────────────────────────
        for t, pos in positions.items():
            wdf = weekly.get(t)
            px = wdf.loc[date, "close"] if (wdf is not None and date in wdf.index) else np.nan
            pos["last_price"] = px if pd.notna(px) else pos["last_price"]

        # ── 1. exits (priority: hard stop > EMA100 break > ATR ratchet) ───────
        exit_tickers = []
        for t, pos in list(positions.items()):
            wdf = weekly.get(t)
            if wdf is None or date not in wdf.index:
                continue
            bar = wdf.loc[date]
            close = bar["close"]
            if pd.isna(close):
                continue

            # Ratchet the trailing stop up (never down), using this bar's ATR
            if pd.notna(bar["atr"]):
                candidate = close - ATR_MULT * bar["atr"]
                if candidate > pos["trailing_stop"]:
                    pos["trailing_stop"] = candidate

            reason = None
            if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
                reason = "hard_stop_20pct"
            elif pd.notna(bar["ema100"]) and close < bar["ema100"]:
                reason = "ema100_break"
            elif close < pos["trailing_stop"]:
                reason = "atr_ratchet_stop"

            if reason:
                exit_tickers.append((t, reason))

        for t, reason in exit_tickers:
            wdf = weekly[t]
            exec_date, exec_price = _next_available(wdf, date)
            if exec_date is None:
                continue
            pos = positions.pop(t)
            proceeds = pos["shares"] * exec_price
            cash += proceeds
            hold_weeks = master_dates.index(exec_date) - master_dates.index(pos["signal_date"])
            trades.append({
                "ticker": t, "reason": reason,
                "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
                "exit_date": exec_date.date().isoformat(), "exit_price": exec_price,
                "shares": pos["shares"], "hold_weeks": hold_weeks,
                "pnl_pct": round((exec_price / pos["entry_price"] - 1) * 100, 2),
                "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
            })

        # ── 2. entries: fresh crossover + close > EMA100 ───────────────────────
        available_slots = MAX_POSITIONS - len(positions)
        if available_slots > 0:
            candidates = []
            for t, wdf in weekly.items():
                if t in positions or date not in wdf.index:
                    continue
                bar = wdf.loc[date]
                if pd.isna(bar["close"]) or pd.isna(bar["ema100"]):
                    continue
                if bool(bar["crossover"]) and bar["close"] > bar["ema100"]:
                    candidates.append((t, bar["close"], bar["roc_12m"]))

            candidates.sort(key=lambda c: (c[2] if pd.notna(c[2]) else -999), reverse=True)
            selected = candidates[:available_slots]

            equity_after_exits = cash + sum(
                p["shares"] * p["last_price"] for p in positions.values()
            )
            alloc = equity_after_exits * POSITION_PCT

            for t, sig_close, roc in selected:
                wdf = weekly[t]
                exec_date, exec_price = _next_available(wdf, date)
                if exec_date is None or exec_price <= 0:
                    continue
                shares = int(alloc // exec_price)
                if shares <= 0:
                    continue
                cost = shares * exec_price
                if cost > cash:
                    continue
                cash -= cost
                # Chandelier initialised the same way the Pine script does:
                # trailingStop := close - atrFactor*atrVal, evaluated at entry
                entry_bar = wdf.loc[date]
                entry_atr = entry_bar["atr"] if pd.notna(entry_bar["atr"]) else 0.0
                positions[t] = {
                    "entry_date": exec_date.date(), "entry_price": exec_price,
                    "shares": shares, "last_price": exec_price,
                    "signal_date": date,
                    "trailing_stop": exec_price - ATR_MULT * entry_atr,
                }

        # ── 3. equity curve ─────────────────────────────────────────────────────
        pos_value = sum(p["shares"] * p["last_price"] for p in positions.values())
        nav = cash + pos_value
        equity_curve.append({
            "date": date.date().isoformat(), "nav": nav,
            "n_positions": len(positions),
            "invested_frac": (nav - cash) / nav if nav > 0 else 0.0,
        })

    for t, pos in positions.items():
        final_price = pos["last_price"]
        proceeds = pos["shares"] * final_price
        hold_weeks = len(master_dates) - 1 - master_dates.index(pos["signal_date"])
        trades.append({
            "ticker": t, "reason": "open_at_backtest_end",
            "entry_date": pos["entry_date"].isoformat(), "entry_price": pos["entry_price"],
            "exit_date": master_dates[-1].date().isoformat(), "exit_price": final_price,
            "shares": pos["shares"], "hold_weeks": hold_weeks,
            "pnl_pct": round((final_price / pos["entry_price"] - 1) * 100, 2),
            "pnl_rs": round(proceeds - pos["shares"] * pos["entry_price"], 2),
        })

    return pd.DataFrame(equity_curve), pd.DataFrame(trades), master_dates


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest the CWA 2.5-Sigma rules.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found.")
        return

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars "
          f"({ohlc['dates'][0]} -> {ohlc['dates'][-1]})\n")

    print("Screening for unadjusted split/bonus artifacts ...")
    flagged = screen_split_artifacts(ohlc)
    print(f"  {len(flagged)} ticker(s) excluded\n")

    print("Building weekly OHLC + CWA 2.5-Sigma indicators (BB50/2.5sigma, EMA100, ATR14) ...")
    weekly = build_all_weekly_cwa(ohlc, exclude=flagged)
    print(f"  {len(weekly)} tickers with usable weekly history "
          f"(need {EMA_PERIOD}+ weeks for EMA100 to warm up)\n")

    print("Running walk-forward weekly backtest ...")
    equity_df, trades_df, master_dates = run_backtest(weekly, args.capital)
    print(f"  Simulated {len(equity_df)} weeks "
          f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]})")
    print(f"  {len(trades_df)} closed trades\n")

    equity_csv = BACKTEST_DIR / "cwa25s_backtest_equity.csv"
    trades_csv = BACKTEST_DIR / "cwa25s_backtest_trades.csv"
    equity_df.to_csv(equity_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    print(f"Saved {equity_csv.name}, {trades_csv.name}\n")

    metrics = compute_metrics(equity_df, args.capital)
    tstats = trade_stats(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], args.capital)

    print(f"{'='*70}\nCWA 2.5-SIGMA -- BACKTEST RESULTS\n{'='*70}")
    print(f"  Period            : {metrics['start_date']} -> {metrics['end_date']} "
          f"({metrics['years']} yrs)")
    print(f"  Start NAV         : Rs {metrics['start_nav']:,.0f}")
    print(f"  End NAV           : Rs {metrics['end_nav']:,.0f}")
    print(f"  Total return      : {metrics['total_return_pct']:+.2f}%")
    print(f"  CAGR              : {metrics['cagr_pct']:+.2f}%")
    print(f"  Annualised vol    : {metrics['ann_vol_pct']:.2f}%")
    print(f"  Sharpe (rf {RFR_ANNUAL*100:.0f}%)   : {metrics['sharpe']}")
    print(f"  Max drawdown      : {metrics['max_drawdown_pct']:.2f}%")
    print()
    print(f"  Trades executed   : {tstats['n_trades']}")
    if tstats["n_trades"]:
        print(f"  Win rate          : {tstats['win_rate_pct']}%")
        print(f"  Avg win / loss    : {tstats['avg_win_pct']:+.2f}% / {tstats['avg_loss_pct']:+.2f}%")
        print(f"  Best / worst trade: {tstats['best_trade_pct']:+.2f}% / {tstats['worst_trade_pct']:+.2f}%")
        print(f"  Avg hold period   : {tstats['avg_hold_weeks']} weeks")
        print(f"  Exit reasons      : {tstats['exit_reason_counts']}")
    print()
    if bench:
        print(f"  --- NIFTY500 buy & hold, same period ---")
        print(f"  CAGR              : {bench['cagr_pct']:+.2f}%")
        print(f"  Max drawdown      : {bench['max_drawdown_pct']:.2f}%")
    print(f"{'='*70}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(pd.to_datetime(equity_df["date"]), equity_df["nav"], label="CWA 2.5-Sigma")
        ax.set_title("CWA 2.5-Sigma Backtest — Equity Curve")
        ax.set_ylabel("NAV (Rs)")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        equity_png = BACKTEST_DIR / "cwa25s_backtest_equity.png"
        fig.savefig(equity_png, dpi=120)
        print(f"Saved {equity_png.name}")


if __name__ == "__main__":
    main()
