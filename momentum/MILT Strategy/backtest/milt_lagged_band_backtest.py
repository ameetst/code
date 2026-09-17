"""
milt_lagged_band_backtest.py
==============================
Decisive test following milt_lookback_diagnostics.py / _2.py: those found
that compute_bollinger_upper() computes the band's mean/std over a window
that INCLUDES the current bar, so a breakout week inflates its own band.
A "lagged" band (computed on the PRIOR `window` weeks only) caught all 9
named FY25 gainers at BB_STD=3.7 in raw signal-frequency terms -- this
script runs that definition through the FULL portfolio engine (same
20% stop, 23W MA exit, ATR chandelier, 4% sizing, max 25 positions, 12M ROC
tie-break as live MILT) to see whether the extra signals are net alpha or
net noise once risk controls and position limits are applied.

Everything reuses milt_backtest.py's engine and metrics unchanged -- only
the bb_upper column's formula differs.

Usage
-----
    python milt_lagged_band_backtest.py                      (BB_WINDOW=30, BB_STD=3.7)
    python milt_lagged_band_backtest.py --bb-window 20
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import BB_WINDOW as LIVE_BB_WINDOW, BB_STD as LIVE_BB_STD, MA_PERIOD, ATR_PERIOD
from milt_backtest import (
    screen_split_artifacts, run_backtest,
    compute_metrics, compute_benchmark_metrics, trade_stats,
    DEFAULT_FILE, DEFAULT_CAPITAL,
)


def compute_bollinger_upper_lagged(close: pd.Series, window: int, num_std: float) -> pd.Series:
    """Band computed on the PRIOR `window` bars only -- this bar's own close
    never contributes to its own band (unlike ml.compute_bollinger_upper)."""
    prior = close.shift(1)
    mid = prior.rolling(window).mean()
    std = prior.rolling(window).std(ddof=0)
    return mid + num_std * std


def build_all_weekly_lagged(ohlc: dict, bb_window: int, bb_std: float, exclude: set = None) -> dict:
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
            if wdf.empty or len(wdf) < bb_window + 2:
                continue
            wdf = wdf.copy()
            wdf["bb_upper"] = compute_bollinger_upper_lagged(wdf["close"], bb_window, bb_std)
            wdf["sma23"]    = ml.compute_sma(wdf["close"], MA_PERIOD)
            wdf["atr"]      = ml.compute_atr(wdf["high"], wdf["low"], wdf["close"], ATR_PERIOD)
            wdf["roc_12m"]  = wdf["close"] / wdf["close"].shift(52) - 1.0
            weekly[t] = wdf
    return weekly


def main():
    parser = argparse.ArgumentParser(description="MILT backtest with a lagged (non-self-referential) Bollinger band.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--bb-window", type=int, default=LIVE_BB_WINDOW)
    parser.add_argument("--bb-std", type=float, default=LIVE_BB_STD)
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found.")
        return

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars\n")

    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)\n")

    print(f"Building weekly indicators with LAGGED BB_WINDOW={args.bb_window} "
          f"BB_STD={args.bb_std} (band uses close.shift(1).rolling(w), MA={MA_PERIOD}w, ATR={ATR_PERIOD}w) ...")
    weekly = build_all_weekly_lagged(ohlc, args.bb_window, args.bb_std, exclude=flagged)
    print(f"  {len(weekly)} tickers with usable weekly history\n")

    print("Running walk-forward weekly backtest ...")
    equity_df, trades_df, master_dates = run_backtest(weekly, args.capital)
    print(f"  Simulated {len(equity_df)} weeks "
          f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]})")
    print(f"  {len(trades_df)} closed trades\n")

    std_tag = str(args.bb_std).replace(".", "p")
    tag = f"lagged_bbw{args.bb_window}_std{std_tag}"
    equity_csv = BACKTEST_DIR / f"milt_{tag}_equity.csv"
    trades_csv = BACKTEST_DIR / f"milt_{tag}_trades.csv"
    equity_df.to_csv(equity_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    print(f"Saved {equity_csv.name}, {trades_csv.name}\n")

    metrics = compute_metrics(equity_df, args.capital)
    tstats = trade_stats(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], args.capital)

    print(f"{'='*70}\nMILT -- LAGGED BAND (BB_WINDOW={args.bb_window}, BB_STD={args.bb_std}) -- BACKTEST RESULTS\n{'='*70}")
    print(f"  Period            : {metrics['start_date']} -> {metrics['end_date']} ({metrics['years']} yrs)")
    print(f"  CAGR              : {metrics['cagr_pct']:+.2f}%")
    print(f"  Annualised vol    : {metrics['ann_vol_pct']:.2f}%")
    print(f"  Sharpe (rf 7%)    : {metrics['sharpe']}")
    print(f"  Max drawdown      : {metrics['max_drawdown_pct']:.2f}%")
    print(f"  Trades            : {tstats['n_trades']}")
    if tstats["n_trades"]:
        print(f"  Win rate          : {tstats['win_rate_pct']}%")
        print(f"  Avg win / loss    : {tstats['avg_win_pct']:+.2f}% / {tstats['avg_loss_pct']:+.2f}%")
        print(f"  Best / worst trade: {tstats['best_trade_pct']:+.2f}% / {tstats['worst_trade_pct']:+.2f}%")
        print(f"  Avg hold period   : {tstats['avg_hold_weeks']} weeks")
        print(f"  Exit reasons      : {tstats['exit_reason_counts']}")
    if bench:
        print(f"  NIFTY500 CAGR (same window): {bench['cagr_pct']:+.2f}%")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
