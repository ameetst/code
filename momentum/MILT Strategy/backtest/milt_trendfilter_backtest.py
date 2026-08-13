"""
milt_trendfilter_backtest.py
==============================
Isolated test: does adding an EMA(100-week) entry trend filter to MILT's
current rules (BB_WINDOW=30, 3.7 sigma, 23-week MA exit, ATR 14/1.8x
recompute-style chandelier, 20% stop) improve returns?

Everything is identical to milt_variant_backtest.py --bb-window 30 (the
current live MILT config) EXCEPT one added AND condition on entry:
    close > bb_upper  AND  close > EMA(ema_period)
This isolates the trend-filter question alone -- it does NOT also adopt
CWA 2.5-Sigma's other differences (ratcheting chandelier, EMA exit,
crossover-only entry, BB(50,2.5)).

Usage
-----
    python milt_trendfilter_backtest.py --ema-period 100
    python milt_trendfilter_backtest.py --ema-period 100 --no-filter   (control run)
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import (
    BB_STD, MA_PERIOD, ATR_PERIOD, ATR_MULT, STOP_LOSS_PCT,
    MAX_POSITIONS, POSITION_PCT,
)
from milt_backtest import (
    screen_split_artifacts, _next_available,
    compute_metrics, compute_benchmark_metrics, trade_stats,
    DEFAULT_FILE, DEFAULT_CAPITAL,
)

BB_WINDOW = 30  # current live MILT setting


def build_all_weekly_ema(ohlc: dict, ema_period: int, exclude: set = None) -> dict:
    exclude = exclude or set()
    weekly = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        wdf = ml.build_weekly_indicators(
            ohlc, t, bb_window=BB_WINDOW, bb_std=BB_STD,
            ma_period=MA_PERIOD, atr_period=ATR_PERIOD,
        )
        if wdf is None or len(wdf) < max(BB_WINDOW, ema_period) + 1:
            continue
        wdf = wdf.copy()
        wdf["ema_trend"] = ml.compute_ema(wdf["close"], ema_period)
        wdf["roc_12m"] = wdf["close"] / wdf["close"].shift(52) - 1.0
        weekly[t] = wdf
    return weekly


def run_backtest(weekly: dict, capital: float, use_filter: bool):
    master_dates = sorted(set().union(*[set(df.index) for df in weekly.values()]))
    warmup = MA_PERIOD
    if len(master_dates) <= warmup + 1:
        raise ValueError("Not enough weekly history to backtest.")

    cash = capital
    positions: dict[str, dict] = {}
    trades = []
    equity_curve = []

    for i in range(warmup, len(master_dates) - 1):
        date = master_dates[i]

        for t, pos in positions.items():
            wdf = weekly.get(t)
            px = wdf.loc[date, "close"] if (wdf is not None and date in wdf.index) else np.nan
            pos["last_price"] = px if pd.notna(px) else pos["last_price"]

        # ── exits (identical to MILT: stop > trend(23W) > ATR chandelier) ──────
        exit_tickers = []
        for t, pos in list(positions.items()):
            wdf = weekly.get(t)
            if wdf is None or date not in wdf.index:
                continue
            bar = wdf.loc[date]
            close = bar["close"]
            if pd.isna(close):
                continue
            pos["peak_close"] = max(pos["peak_close"], close)

            reason = None
            if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
                reason = "stop_loss_20pct"
            elif pd.notna(bar["sma23"]) and close < bar["sma23"]:
                reason = "trend_reversal_23wma"
            elif pd.notna(bar["atr"]) and close < pos["peak_close"] - ATR_MULT * bar["atr"]:
                reason = "atr_chandelier_stop"

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

        # ── entries: MILT's BB breakout, optionally AND close > EMA(trend) ─────
        available_slots = MAX_POSITIONS - len(positions)
        if available_slots > 0:
            candidates = []
            for t, wdf in weekly.items():
                if t in positions or date not in wdf.index:
                    continue
                bar = wdf.loc[date]
                if pd.isna(bar["bb_upper"]) or pd.isna(bar["close"]):
                    continue
                if bar["close"] <= bar["bb_upper"]:
                    continue
                if use_filter:
                    if pd.isna(bar["ema_trend"]) or bar["close"] <= bar["ema_trend"]:
                        continue
                candidates.append((t, bar["close"], bar["roc_12m"]))

            candidates.sort(key=lambda c: (c[2] if pd.notna(c[2]) else -999), reverse=True)
            selected = candidates[:available_slots]

            equity_after_exits = cash + sum(p["shares"] * p["last_price"] for p in positions.values())
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
                positions[t] = {
                    "entry_date": exec_date.date(), "entry_price": exec_price,
                    "shares": shares, "peak_close": exec_price,
                    "last_price": exec_price, "signal_date": date,
                }

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


def main():
    parser = argparse.ArgumentParser(description="Test an EMA trend filter added to MILT.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--ema-period", type=int, default=100, help="EMA period in WEEKS")
    parser.add_argument("--no-filter", action="store_true", help="Control run: same code path, filter disabled")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found.")
        return

    use_filter = not args.no_filter
    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)")

    label = f"WITH EMA({args.ema_period}w) trend filter" if use_filter else "WITHOUT trend filter (control)"
    print(f"Building weekly indicators -- {label} ...")
    weekly = build_all_weekly_ema(ohlc, args.ema_period, exclude=flagged)
    print(f"  {len(weekly)} tickers with usable weekly history\n")

    print("Running walk-forward weekly backtest ...")
    equity_df, trades_df, master_dates = run_backtest(weekly, args.capital, use_filter)
    print(f"  Simulated {len(equity_df)} weeks "
          f"({equity_df['date'].iloc[0]} -> {equity_df['date'].iloc[-1]})")
    print(f"  {len(trades_df)} closed trades\n")

    tag = f"ema{args.ema_period}" if use_filter else "nofilter_control"
    equity_csv = BACKTEST_DIR / f"milt_trendfilter_{tag}_equity.csv"
    trades_csv = BACKTEST_DIR / f"milt_trendfilter_{tag}_trades.csv"
    equity_df.to_csv(equity_csv, index=False)
    trades_df.to_csv(trades_csv, index=False)
    print(f"Saved {equity_csv.name}, {trades_csv.name}\n")

    metrics = compute_metrics(equity_df, args.capital)
    tstats = trade_stats(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], args.capital)

    print(f"{'='*70}\nMILT {label} -- BACKTEST RESULTS\n{'='*70}")
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
