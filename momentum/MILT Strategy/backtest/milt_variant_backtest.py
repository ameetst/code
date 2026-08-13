"""
milt_variant_backtest.py
=========================
Parameter-sensitivity variant of milt_backtest.py: lets you override the
Bollinger Band basis window AND/OR the sigma multiplier (milt_strategy.py's
current live values are BB_WINDOW=30, BB_STD=3.7) while keeping every other
rule identical to MILT (23-week MA exit, ATR 14/1.8x chandelier, 20% stop,
4% of current equity, max 25 positions, 12M ROC tie-break). Reuses
milt_backtest.py's portfolio engine and metrics functions unchanged -- only
the indicator-building step differs.

Usage
-----
    python milt_variant_backtest.py --bb-window 50
    python milt_variant_backtest.py --bb-window 30 --bb-std 2.5
    python milt_variant_backtest.py --bb-window 20 --bb-std 3.7  (original baseline)
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
from milt_strategy import BB_STD as DEFAULT_BB_STD, MA_PERIOD, ATR_PERIOD
from milt_backtest import (
    screen_split_artifacts, run_backtest,
    compute_metrics, compute_benchmark_metrics, trade_stats,
    DEFAULT_FILE, DEFAULT_CAPITAL,
)


def build_all_weekly_variant(ohlc: dict, bb_window: int, bb_std: float, exclude: set = None) -> dict:
    exclude = exclude or set()
    weekly = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        wdf = ml.build_weekly_indicators(
            ohlc, t, bb_window=bb_window, bb_std=bb_std,
            ma_period=MA_PERIOD, atr_period=ATR_PERIOD,
        )
        if wdf is None or len(wdf) < bb_window + 1:
            continue
        wdf = wdf.copy()
        wdf["roc_12m"] = wdf["close"] / wdf["close"].shift(52) - 1.0
        weekly[t] = wdf
    return weekly


def main():
    parser = argparse.ArgumentParser(description="MILT backtest with a variant Bollinger window/sigma.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--bb-window", type=int, default=20,
                        help="Bollinger basis period in WEEKS (MILT default: 20)")
    parser.add_argument("--bb-std", type=float, default=DEFAULT_BB_STD,
                        help=f"Bollinger sigma multiplier (MILT default: {DEFAULT_BB_STD})")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found.")
        return

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars\n")

    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)\n")

    print(f"Building weekly indicators with BB_WINDOW={args.bb_window} "
          f"BB_STD={args.bb_std} (MA={MA_PERIOD}w, ATR={ATR_PERIOD}w) ...")
    weekly = build_all_weekly_variant(ohlc, args.bb_window, args.bb_std, exclude=flagged)
    print(f"  {len(weekly)} tickers with usable weekly history\n")

    print("Running walk-forward weekly backtest ...")
    equity_df, trades_df, master_dates = run_backtest(weekly, args.capital)
    print(f"  Simulated {len(equity_df)} weeks "
          f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]})")
    print(f"  {len(trades_df)} closed trades\n")

    std_tag = str(args.bb_std).replace(".", "p")
    tag = f"bbw{args.bb_window}_std{std_tag}"
    equity_csv = BACKTEST_DIR / f"milt_variant_{tag}_equity.csv"
    trades_csv = BACKTEST_DIR / f"milt_variant_{tag}_trades.csv"
    equity_df.to_csv(equity_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    print(f"Saved {equity_csv.name}, {trades_csv.name}\n")

    metrics = compute_metrics(equity_df, args.capital)
    tstats = trade_stats(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], args.capital)

    print(f"{'='*70}\nMILT (BB_WINDOW={args.bb_window}, BB_STD={args.bb_std}) -- BACKTEST RESULTS\n{'='*70}")
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
    if bench:
        print(f"  NIFTY500 CAGR (same window): {bench['cagr_pct']:+.2f}%")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
