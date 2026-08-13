"""
compare_strategies.py
======================
Side-by-side comparison of MILT vs CWA 2.5-Sigma backtest results (both run
on the same MILT_N750_backtest.xlsx dataset, same portfolio engine --
4% of equity, max 25 positions, 12M ROC tie-break -- differing only in
entry/exit rules; see milt_backtest.py and cwa25s_backtest.py docstrings).

Prints two tables:
  1. Each strategy over its OWN full simulated window (CWA's window is
     shorter -- it needs a 100-week EMA warmup vs MILT's 23-week MA warmup).
  2. Both strategies restricted to their OVERLAPPING window only, for a
     time-period-matched comparison (fairer, since returns over different
     historical stretches aren't directly comparable).

Usage
-----
    python compare_strategies.py
"""

import sys
from pathlib import Path

import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_backtest import compute_metrics, compute_benchmark_metrics, trade_stats, DEFAULT_CAPITAL


def load_run(prefix: str):
    equity_df = pd.read_csv(BACKTEST_DIR / f"{prefix}_equity.csv")
    trades_df = pd.read_csv(BACKTEST_DIR / f"{prefix}_trades.csv")
    return equity_df, trades_df


def row(label, milt_val, cwa_val, fmt="{:.2f}"):
    m = fmt.format(milt_val) if milt_val is not None else "n/a"
    c = fmt.format(cwa_val) if cwa_val is not None else "n/a"
    print(f"  {label:<22} {m:>16} {c:>16}")


def print_table(title, milt_metrics, milt_tstats, cwa_metrics, cwa_tstats, milt_bench=None, cwa_bench=None):
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    print(f"  {'Metric':<22} {'MILT':>16} {'CWA 2.5-Sigma':>16}")
    print(f"  {'-'*22} {'-'*16} {'-'*16}")
    print(f"  {'Period':<22} {milt_metrics['start_date']+' to '+milt_metrics['end_date']:>16.16} "
          f"{cwa_metrics['start_date']+' to '+cwa_metrics['end_date']:>16.16}")
    row("Years", milt_metrics["years"], cwa_metrics["years"])
    row("Total return %", milt_metrics["total_return_pct"], cwa_metrics["total_return_pct"], "{:+.2f}%")
    row("CAGR %", milt_metrics["cagr_pct"], cwa_metrics["cagr_pct"], "{:+.2f}%")
    row("Ann. volatility %", milt_metrics["ann_vol_pct"], cwa_metrics["ann_vol_pct"], "{:.2f}%")
    row("Sharpe (rf 7%)", milt_metrics["sharpe"], cwa_metrics["sharpe"], "{:.2f}")
    row("Max drawdown %", milt_metrics["max_drawdown_pct"], cwa_metrics["max_drawdown_pct"], "{:.2f}%")
    print()
    row("Trades", milt_tstats.get("n_trades", 0), cwa_tstats.get("n_trades", 0), "{:.0f}")
    row("Win rate %", milt_tstats.get("win_rate_pct"), cwa_tstats.get("win_rate_pct"), "{:.1f}%")
    row("Avg win %", milt_tstats.get("avg_win_pct"), cwa_tstats.get("avg_win_pct"), "{:+.2f}%")
    row("Avg loss %", milt_tstats.get("avg_loss_pct"), cwa_tstats.get("avg_loss_pct"), "{:+.2f}%")
    row("Best trade %", milt_tstats.get("best_trade_pct"), cwa_tstats.get("best_trade_pct"), "{:+.2f}%")
    row("Worst trade %", milt_tstats.get("worst_trade_pct"), cwa_tstats.get("worst_trade_pct"), "{:+.2f}%")
    row("Avg hold (weeks)", milt_tstats.get("avg_hold_weeks"), cwa_tstats.get("avg_hold_weeks"), "{:.1f}")
    if milt_bench and cwa_bench:
        print()
        row("NIFTY500 CAGR %", milt_bench["cagr_pct"], cwa_bench["cagr_pct"], "{:+.2f}%")
        row("NIFTY500 max DD %", milt_bench["max_drawdown_pct"], cwa_bench["max_drawdown_pct"], "{:.2f}%")


def main():
    milt_eq, milt_tr = load_run("milt_backtest")
    cwa_eq, cwa_tr = load_run("cwa25s_backtest")

    ohlc = ml.load_ohlc(str(BACKTEST_DIR / "MILT_N750_backtest.xlsx"))

    # ── Table 1: each strategy's own full window ────────────────────────────────
    milt_metrics = compute_metrics(milt_eq, DEFAULT_CAPITAL)
    cwa_metrics  = compute_metrics(cwa_eq, DEFAULT_CAPITAL)
    milt_tstats  = trade_stats(milt_tr)
    cwa_tstats   = trade_stats(cwa_tr)
    milt_bench = compute_benchmark_metrics(ohlc["nifty"], milt_eq["date"].iloc[0], milt_eq["date"].iloc[-1], DEFAULT_CAPITAL)
    cwa_bench  = compute_benchmark_metrics(ohlc["nifty"], cwa_eq["date"].iloc[0], cwa_eq["date"].iloc[-1], DEFAULT_CAPITAL)

    print_table("TABLE 1: EACH STRATEGY'S OWN FULL SIMULATED WINDOW\n"
                "(different start dates -- CWA needs a longer EMA100 warmup)",
                milt_metrics, milt_tstats, cwa_metrics, cwa_tstats, milt_bench, cwa_bench)

    # ── Table 2: matched (overlapping) window only ──────────────────────────────
    overlap_start = max(milt_eq["date"].iloc[0], cwa_eq["date"].iloc[0])
    overlap_end   = min(milt_eq["date"].iloc[-1], cwa_eq["date"].iloc[-1])
    print(f"\nOverlapping window for a time-matched comparison: {overlap_start} -> {overlap_end}")

    milt_eq_m = milt_eq[(milt_eq["date"] >= overlap_start) & (milt_eq["date"] <= overlap_end)].reset_index(drop=True)
    cwa_eq_m  = cwa_eq[(cwa_eq["date"] >= overlap_start) & (cwa_eq["date"] <= overlap_end)].reset_index(drop=True)

    # Re-base each equity curve to start at the same capital at the overlap start,
    # so CAGR/vol/drawdown reflect ONLY the matched window, not the earlier run-up.
    milt_eq_m = milt_eq_m.copy()
    cwa_eq_m = cwa_eq_m.copy()
    milt_eq_m["nav"] = milt_eq_m["nav"] / milt_eq_m["nav"].iloc[0] * DEFAULT_CAPITAL
    cwa_eq_m["nav"] = cwa_eq_m["nav"] / cwa_eq_m["nav"].iloc[0] * DEFAULT_CAPITAL

    milt_metrics_m = compute_metrics(milt_eq_m, DEFAULT_CAPITAL)
    cwa_metrics_m  = compute_metrics(cwa_eq_m, DEFAULT_CAPITAL)

    milt_tr_m = milt_tr[(milt_tr["exit_date"] >= overlap_start) & (milt_tr["exit_date"] <= overlap_end)]
    cwa_tr_m  = cwa_tr[(cwa_tr["exit_date"] >= overlap_start) & (cwa_tr["exit_date"] <= overlap_end)]
    milt_tstats_m = trade_stats(milt_tr_m)
    cwa_tstats_m  = trade_stats(cwa_tr_m)

    bench_m = compute_benchmark_metrics(ohlc["nifty"], overlap_start, overlap_end, DEFAULT_CAPITAL)

    print_table("TABLE 2: TIME-MATCHED WINDOW ONLY (fair comparison)",
                milt_metrics_m, milt_tstats_m, cwa_metrics_m, cwa_tstats_m, bench_m, bench_m)

    print(f"\n{'='*70}")
    print("Both use identical portfolio rules (4% of current equity, max 25 "
          "positions, 12M ROC tie-break, weekly bars from the same dataset) "
          "-- differences above come ONLY from the entry/exit signal logic.")


if __name__ == "__main__":
    main()
