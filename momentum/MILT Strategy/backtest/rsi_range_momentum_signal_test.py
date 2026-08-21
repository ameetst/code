"""
rsi_range_momentum_signal_test.py
===================================
Replicates the signal-quality methodology from Arthur Hill's "Finding
Consistent Trends with Strong Momentum" (SSRN 3412429, Feb 2019) on the
N750 NSE universe, instead of the S&P 500 the paper tested on.

This is a signal-quality test, NOT a strategy backtest: no portfolio engine,
no position sizing, no stops, no ranking/tie-break. For every ticker it finds
every occurrence of three RSI(14) signals across five lookback windows
(25/50/75/100/125 trading days) and measures Success Rate, Average Advance,
Average Decline and Profit/Loss Ratio -- exactly the paper's own metrics --
to see whether Hill's findings hold on this universe. milt_strategy.py is
not imported or touched by this script.

Signals tested (bull-only -- the paper found bearish RSI signals had no
predictive edge on the S&P 500, so this replication skips them):
  1. RSI Bull Range          : RSI has not dipped below 40 in the last N days
  2. RSI Bull Momentum       : RSI has reached >= 70 at some point in the last N days
  3. RSI Bull Range-Momentum : both of the above simultaneously

Methodology (matches the paper's "Testing, Methodology and Data" section):
  * RSI(14), Wilder's smoothing (momentum_lib.compute_rsi)
  * Signal generated on the close; entry/exit executed at the next available
    daily Open (the paper's exact convention)
  * A "signal" is a contiguous run of the boolean condition being True: it
    starts the day the condition first turns True and ends the day it first
    breaks. Return = open-at-signal-end / open-at-signal-start - 1.
  * Success = positive return (these are all bullish signals).
  * P/L Ratio = mean(winning returns) / abs(mean(losing returns))
  * Signals still open when the data window ends are excluded from the
    return stats (counted separately as "open_at_end") to avoid scoring an
    unresolved, right-censored trade.

Known deviations from the paper (read the results with these in mind):
  * Only ~5 years of daily history are available here (2021-08 to 2026-08,
    via MILT_N750_backtest.xlsx) vs. the paper's 20-year, 4-market-cycle
    S&P 500 window -- expect far fewer signal instances (especially at
    N=100/125) and a result shaped mostly by one bull regime, not a full
    cycle. This is a first read on whether the effect exists here, not a
    robust multi-cycle validation.
  * Same split-artifact screen as milt_backtest.py (screen_split_artifacts)
    is reused to exclude ~12/750 tickers with unadjusted-split price cliffs.
  * No transaction costs / slippage, matching the paper.

Usage
-----
    python rsi_range_momentum_signal_test.py [--file MILT_N750_backtest.xlsx] [--plot]
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
from milt_backtest import screen_split_artifacts   # read-only import, same as cwa25s_daily_backtest.py

DEFAULT_FILE = str(BACKTEST_DIR / "MILT_N750_backtest.xlsx")

RSI_PERIOD       = 14
RANGE_FLOOR      = 40.0
MOMENTUM_CEILING = 70.0
LOOKBACKS        = [25, 50, 75, 100, 125]

SIGNAL_TYPES  = ["bull_range", "bull_momentum", "bull_range_momentum"]
SIGNAL_LABELS = {
    "bull_range":          "RSI Bull Range",
    "bull_momentum":       "RSI Bull Momentum",
    "bull_range_momentum": "RSI Bull Range-Momentum",
}


# ── DATA PREP ────────────────────────────────────────────────────────────────

def build_ticker_frames(ohlc: dict, exclude: set) -> dict:
    """
    Per-ticker daily close/open + RSI(14), for tickers with enough history
    for the largest lookback (125) plus RSI warmup.
    """
    min_len = max(LOOKBACKS) + RSI_PERIOD + 5
    frames = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        if ohlc["open"] is None or t not in ohlc["open"].index or t not in ohlc["close"].index:
            continue
        close = ohlc["close"].loc[t].dropna()
        if len(close) < min_len:
            continue
        df = pd.DataFrame({
            "close": ohlc["close"].loc[t],
            "open":  ohlc["open"].loc[t],
        }).dropna(subset=["close"])
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df["rsi"] = ml.compute_rsi(df["close"], RSI_PERIOD)
        frames[t] = df
    return frames


# ── SIGNAL EVENT EXTRACTION ───────────────────────────────────────────────────

def extract_signal_events(active: pd.Series) -> tuple:
    """
    Walk a boolean Series (indexed by date) and return (events, n_open).

    events  : list of (start_date, end_date) -- start_date is the day the
              condition first turned True, end_date is the day it first
              turned False again (the paper's entry/exit trigger days).
    n_open  : 1 if a run is still True at the end of the series (excluded
              from stats), else 0.
    """
    events = []
    start = None
    idx = active.index
    vals = active.values
    for i in range(len(vals)):
        if vals[i] and start is None:
            start = idx[i]
        elif not vals[i] and start is not None:
            events.append((start, idx[i]))
            start = None
    n_open = 1 if start is not None else 0
    return events, n_open


def _next_open(df: pd.DataFrame, after_date) -> tuple:
    later = df[df.index > after_date]
    if later.empty:
        return None, None
    return later.index[0], float(later.iloc[0]["open"])


def run_signal_test(frames: dict) -> tuple:
    """
    For every ticker / signal type / lookback, extract signal events and
    compute the next-open-to-next-open return.

    Returns (results_df, open_counts):
      results_df  : one row per resolved signal
      open_counts : {(signal_type, lookback): n_open_at_data_end}
    """
    rows = []
    open_counts = {(st, n): 0 for st in SIGNAL_TYPES for n in LOOKBACKS}

    for ticker, df in frames.items():
        rsi = df["rsi"]
        for signal_type in SIGNAL_TYPES:
            for n in LOOKBACKS:
                if signal_type == "bull_range":
                    active = ml.rsi_range_active(rsi, n, RANGE_FLOOR)
                elif signal_type == "bull_momentum":
                    active = ml.rsi_momentum_active(rsi, n, MOMENTUM_CEILING)
                else:
                    active = ml.rsi_range_momentum_active(rsi, n, RANGE_FLOOR, MOMENTUM_CEILING)

                events, n_open = extract_signal_events(active)
                open_counts[(signal_type, n)] += n_open

                for start_date, end_date in events:
                    entry_date, entry_price = _next_open(df, start_date)
                    if entry_date is None or entry_price is None or entry_price <= 0:
                        continue
                    exit_date, exit_price = _next_open(df, end_date)
                    if exit_date is None or exit_price is None or exit_date <= entry_date:
                        continue
                    ret_pct = (exit_price / entry_price - 1.0) * 100.0
                    rows.append({
                        "ticker": ticker, "signal_type": signal_type, "lookback": n,
                        "signal_start": start_date.date().isoformat(),
                        "signal_end":   end_date.date().isoformat(),
                        "entry_date": entry_date.date().isoformat(), "entry_price": entry_price,
                        "exit_date":  exit_date.date().isoformat(),  "exit_price":  exit_price,
                        "return_pct": round(ret_pct, 2),
                    })

    return pd.DataFrame(rows), open_counts


# ── AGGREGATION ────────────────────────────────────────────────────────────────

def summarize(results_df: pd.DataFrame, open_counts: dict) -> pd.DataFrame:
    summary_rows = []
    for signal_type in SIGNAL_TYPES:
        for n in LOOKBACKS:
            sub = results_df[(results_df["signal_type"] == signal_type) & (results_df["lookback"] == n)]
            n_signals = len(sub)
            row = {
                "signal": SIGNAL_LABELS[signal_type], "lookback_days": n,
                "total_signals": n_signals, "open_at_end": open_counts.get((signal_type, n), 0),
            }
            if n_signals == 0:
                row.update(success_rate_pct=None, avg_advance_pct=None,
                           avg_decline_pct=None, profit_loss_ratio=None)
                summary_rows.append(row)
                continue

            wins   = sub[sub["return_pct"] > 0]
            losses = sub[sub["return_pct"] <= 0]
            success_rate = len(wins) / n_signals * 100.0
            avg_adv = float(wins["return_pct"].mean()) if len(wins) else 0.0
            avg_dec = float(losses["return_pct"].mean()) if len(losses) else 0.0
            pl_ratio = (avg_adv / abs(avg_dec)) if avg_dec != 0 else np.nan

            row.update(
                success_rate_pct=round(success_rate, 1),
                avg_advance_pct=round(avg_adv, 2),
                avg_decline_pct=round(avg_dec, 2),
                profit_loss_ratio=round(pl_ratio, 2) if pd.notna(pl_ratio) else None,
            )
            summary_rows.append(row)
    return pd.DataFrame(summary_rows)


# ── MAIN ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RSI Bull Range/Momentum signal-quality test (Hill 2019 methodology) on N750.")
    parser.add_argument("--file", default=DEFAULT_FILE)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()

    if not Path(args.file).exists():
        print(f"ERROR: {args.file} not found.")
        return

    print(f"Loading {args.file} ...")
    ohlc = ml.load_ohlc(args.file)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars "
          f"({ohlc['dates'][0]} -> {ohlc['dates'][-1]})\n")

    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)\n")

    min_len = max(LOOKBACKS) + RSI_PERIOD + 5
    print(f"Computing RSI({RSI_PERIOD}) and building per-ticker frames (need >= {min_len} bars) ...")
    frames = build_ticker_frames(ohlc, exclude=flagged)
    print(f"  {len(frames)} tickers with usable history\n")

    print("Extracting signal events and computing next-open returns "
          f"({len(SIGNAL_TYPES)} signal types x {len(LOOKBACKS)} lookbacks x {len(frames)} tickers) ...")
    results_df, open_counts = run_signal_test(frames)
    print(f"  {len(results_df)} resolved signals\n")

    results_csv = BACKTEST_DIR / "rsi_range_momentum_signal_test_results.csv"
    results_df.to_csv(results_csv, index=False)

    summary_df = summarize(results_df, open_counts)
    summary_csv = BACKTEST_DIR / "rsi_range_momentum_signal_test_summary.csv"
    summary_df.to_csv(summary_csv, index=False)
    print(f"Saved {results_csv.name}, {summary_csv.name}\n")

    print(f"{'='*95}")
    print(f"RSI RANGE / MOMENTUM SIGNAL TEST -- N750, "
          f"{ohlc['dates'][0]} -> {ohlc['dates'][-1]}  (Hill 2019 methodology)")
    print(f"{'='*95}")
    for signal_type in SIGNAL_TYPES:
        print(f"\n{SIGNAL_LABELS[signal_type]}")
        print(f"{'Lookback':>10} {'Signals':>9} {'Open':>6} {'Success%':>10} "
              f"{'AvgAdv%':>9} {'AvgDec%':>9} {'P/L Ratio':>10}")
        sub = summary_df[summary_df["signal"] == SIGNAL_LABELS[signal_type]]
        for _, r in sub.iterrows():
            def fmt(v, spec="%.2f"):
                return (spec % v) if v is not None and pd.notna(v) else "n/a"
            print(f"{r['lookback_days']:>10} {r['total_signals']:>9} {r['open_at_end']:>6} "
                  f"{fmt(r['success_rate_pct'], '%.1f'):>10} "
                  f"{fmt(r['avg_advance_pct']):>9} {fmt(r['avg_decline_pct']):>9} "
                  f"{fmt(r['profit_loss_ratio']):>10}")
    print(f"\n{'='*95}")

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 6))
        markers = {"bull_range": "o", "bull_momentum": "s", "bull_range_momentum": "^"}
        colors  = {"bull_range": "tab:green", "bull_momentum": "tab:blue", "bull_range_momentum": "tab:red"}
        for signal_type in SIGNAL_TYPES:
            sub = summary_df[(summary_df["signal"] == SIGNAL_LABELS[signal_type]) &
                              summary_df["success_rate_pct"].notna()]
            if sub.empty:
                continue
            ax.scatter(sub["success_rate_pct"], sub["profit_loss_ratio"],
                       label=SIGNAL_LABELS[signal_type], marker=markers[signal_type],
                       color=colors[signal_type], s=70, zorder=3)
            for _, r in sub.iterrows():
                ax.annotate(f"{int(r['lookback_days'])}d",
                           (r["success_rate_pct"], r["profit_loss_ratio"]),
                           fontsize=8, xytext=(5, 5), textcoords="offset points")
        ax.axhline(2.0, color="gray", linestyle="--", linewidth=0.8)
        ax.axvline(50.0, color="gray", linestyle="--", linewidth=0.8)
        ax.set_xlabel("Success Rate (%)")
        ax.set_ylabel("Profit/Loss Ratio")
        ax.set_title("RSI Bull Range/Momentum Signal Quality -- N750")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        png_path = BACKTEST_DIR / "rsi_range_momentum_signal_test_scatter.png"
        fig.savefig(png_path, dpi=120)
        print(f"Saved {png_path.name}")


if __name__ == "__main__":
    main()
