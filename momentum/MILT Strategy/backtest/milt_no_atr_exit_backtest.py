"""
milt_no_atr_exit_backtest.py
==============================
Isolated test: what happens if the ATR chandelier exit is removed entirely,
keeping only the other two MILT exit rules (20% hard stop, 23-week MA
trend-reversal)? Everything else (BB_WINDOW=30, BB_STD=3.7, entry rule,
4% of current equity, max 25 positions, 12M ROC tie-break) is identical to
the current live config.

Motivation: the ATR chandelier accounts for 64.3% of all exits in the
current backtest (108/168 trades) -- by far the dominant exit mechanism.
This isolates its contribution by removing it and nothing else.

Usage
-----
    python milt_no_atr_exit_backtest.py
"""

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
    BB_WINDOW, BB_STD, MA_PERIOD, ATR_PERIOD, STOP_LOSS_PCT,
    MAX_POSITIONS, POSITION_PCT,
)
from milt_backtest import (
    screen_split_artifacts, _next_available,
    compute_metrics, compute_benchmark_metrics, trade_stats,
    DEFAULT_FILE, DEFAULT_CAPITAL,
)


def build_all_weekly(ohlc: dict, exclude: set = None) -> dict:
    exclude = exclude or set()
    weekly = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        wdf = ml.build_weekly_indicators(
            ohlc, t, bb_window=BB_WINDOW, bb_std=BB_STD,
            ma_period=MA_PERIOD, atr_period=ATR_PERIOD,
        )
        if wdf is None or len(wdf) < BB_WINDOW + 1:
            continue
        wdf = wdf.copy()
        wdf["roc_12m"] = wdf["close"] / wdf["close"].shift(52) - 1.0
        weekly[t] = wdf
    return weekly


def run_backtest_no_atr(weekly: dict, capital: float):
    """Identical to milt_backtest.run_backtest() except the ATR chandelier
    exit branch is removed -- only stop_loss_20pct and trend_reversal_23wma
    can trigger an exit. peak_close is still tracked (harmless, just unused
    for exits) so the trade-record shape stays consistent."""
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

        # ── exits: ONLY stop_loss_20pct and trend_reversal_23wma ───────────────
        exit_tickers = []
        for t, pos in list(positions.items()):
            wdf = weekly.get(t)
            if wdf is None or date not in wdf.index:
                continue
            bar = wdf.loc[date]
            close = bar["close"]
            if pd.isna(close):
                continue
            pos["peak_close"] = max(pos["peak_close"], close)  # tracked, not used for exits

            reason = None
            if close <= pos["entry_price"] * (1 - STOP_LOSS_PCT):
                reason = "stop_loss_20pct"
            elif pd.notna(bar["sma23"]) and close < bar["sma23"]:
                reason = "trend_reversal_23wma"
            # ATR chandelier branch intentionally removed

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

        # ── entries: identical to MILT ──────────────────────────────────────────
        available_slots = MAX_POSITIONS - len(positions)
        if available_slots > 0:
            candidates = []
            for t, wdf in weekly.items():
                if t in positions or date not in wdf.index:
                    continue
                bar = wdf.loc[date]
                if pd.isna(bar["bb_upper"]) or pd.isna(bar["close"]):
                    continue
                if bar["close"] > bar["bb_upper"]:
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
    if not Path(DEFAULT_FILE).exists():
        print(f"ERROR: {DEFAULT_FILE} not found.")
        return

    print(f"Loading {DEFAULT_FILE} ...")
    ohlc = ml.load_ohlc(DEFAULT_FILE)
    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)")

    weekly = build_all_weekly(ohlc, exclude=flagged)
    print(f"{len(weekly)} tickers usable\n")

    print("Running walk-forward backtest WITHOUT the ATR chandelier exit "
          "(only 20% stop + 23W MA trend reversal remain) ...")
    equity_df, trades_df, master_dates = run_backtest_no_atr(weekly, DEFAULT_CAPITAL)
    print(f"  Simulated {len(equity_df)} weeks, {len(trades_df)} closed trades\n")

    equity_df.to_csv(BACKTEST_DIR / "milt_no_atr_exit_equity.csv", index=False)
    trades_df.to_csv(BACKTEST_DIR / "milt_no_atr_exit_trades.csv", index=False)

    metrics = compute_metrics(equity_df, DEFAULT_CAPITAL)
    tstats = trade_stats(trades_df)
    bench = compute_benchmark_metrics(
        ohlc["nifty"], equity_df["date"].iloc[0], equity_df["date"].iloc[-1], DEFAULT_CAPITAL)

    print(f"{'='*70}\nMILT -- NO ATR CHANDELIER EXIT (20% stop + 23W MA only)\n{'='*70}")
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
        print(f"  Avg hold period   : {trades_df['hold_weeks'].mean():.1f} weeks")
        print(f"  Exit reasons      : {tstats['exit_reason_counts']}")
    if bench:
        print(f"  NIFTY500 CAGR (same window): {bench['cagr_pct']:+.2f}%")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
