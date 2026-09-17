"""
Four-Strategy Momentum Backtest
================================
Compares 4 composite scoring strategies on the NSE `History_updated.xlsx`
universe (747 tickers incl. NIFTY500 benchmark, DATA/VOLUME sheets in the
same layout momentum_lib.py expects):

  1. Z Equal Weight    - AP Momentum, 20/20/20/20/20 z-score blend of
                          12-1 mom / 6-1 mom / 52W-high / Clenow / vol-adj mom
  2. Z Weighted v1      - AP Momentum, 30/20/20/20/10 (PRIMARY)
  3. Z Weighted v2      - AP Momentum, 15/20/15/25/25 (Trend+Vol tilted)
  4. Sharpe Composite   - Sharpe.py's live signal: equal-weighted z-score of
                          12M/9M/6M/3M annualised Sharpe ratio (RFR = 7%)

All 4 strategies run through the SAME backtest engine for a clean,
signal-only comparison: monthly rebalance, top-N by composite score,
equal-weighted holdings until the next rebalance, no transaction costs.

This intentionally does NOT replicate Sharpe.py's live turnover discipline
(28-day hold lock, rank-exit buffer), dynamic position count, regime gating,
or eligibility filters (ADTV / EQ-series / circuit-hit) -- those are
portfolio-construction / risk-management layers on top of the signal, not
part of the signal itself. This backtest isolates "which composite score
would have picked the better stocks," not "how the live system trades it."

Usage:
    python backtest_4_strategies.py
"""

import sys
import warnings
warnings.filterwarnings("ignore")

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

AP_DIR = Path(__file__).resolve().parent
SHARPE_DIR = AP_DIR.parent / "Sharpe"
sys.path.insert(0, str(AP_DIR))
sys.path.insert(0, str(SHARPE_DIR))

import zscore_backtest as zb          # AP Momentum signal engine
import momentum_lib as ml             # Sharpe.py signal library

RESULTS_DIR = AP_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

DATA_FILE = AP_DIR / "History_updated.xlsx"

TOP_N = 20
REBALANCE_FREQ = "ME"
START_DATE = "2017-11-01"       # skips the ~14-month signal warm-up period
RFR_ANNUAL = 0.07               # matches Sharpe.py's live RFR assumption
TRADING_DAYS = 252
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}

MIN_HISTORY = zb.MIN_HISTORY     # 278 trading days -- shared gate, all 4 strategies

WEIGHTS_Z = {
    "Z Equal Weight": {
        "mom_12_1": 0.20, "mom_6_1": 0.20, "high_52w": 0.20,
        "clenow": 0.20, "vol_adj": 0.20,
    },
    "Z Weighted v1 (PRIMARY)": {
        "mom_12_1": 0.30, "mom_6_1": 0.20, "high_52w": 0.20,
        "clenow": 0.20, "vol_adj": 0.10,
    },
    "Z Weighted v2 (Trend+Vol)": {
        "mom_12_1": 0.15, "mom_6_1": 0.20, "high_52w": 0.15,
        "clenow": 0.25, "vol_adj": 0.25,
    },
}
STRATEGY_NAMES = list(WEIGHTS_Z.keys()) + ["Sharpe Composite"]


# ── DATA LOADING ────────────────────────────────────────────────────────────

def load_price_matrix():
    """Load History_updated.xlsx via momentum_lib and return a date-indexed,
    ticker-columned wide price matrix (incl. NIFTY500), matching the shape
    zscore_backtest's engine expects."""
    print(f"Loading {DATA_FILE.name} ...")
    prices_df, nifty_series, stock_tickers, dates = ml.load_prices(str(DATA_FILE))
    wide = prices_df.T   # date index, ticker columns
    wide["NIFTY500"] = nifty_series
    wide.index = pd.to_datetime(wide.index)
    wide = wide.sort_index()

    # The sheet's date columns are generated from WORKDAY() (skips weekends
    # only, not NSE holidays), so ~5% of columns are exchange-holiday dates
    # where every single ticker -- including NIFTY500 -- is blank. Left in,
    # the backtest's date-snapping can land a rebalance exactly on one of
    # these all-NaN rows, corrupting that period's forward return for every
    # strategy. Drop them: NIFTY500 is NaN if and only if it's a holiday row.
    before = len(wide)
    wide = wide.dropna(subset=["NIFTY500"])
    dropped = before - len(wide)

    print(f"  {len(stock_tickers)} stock tickers, "
          f"{wide.index.min().date()} -> {wide.index.max().date()} "
          f"({len(wide)} trading days, {dropped} exchange-holiday rows dropped)")
    return wide, stock_tickers


# ── SHARPE COMPOSITE SIGNAL (mirrors Sharpe.py / momentum_lib.compute_sharpe) ─

def compute_sharpe_composite(prices_up_to_t0, tickers, windows, rfr_daily,
                              trading_days, min_history):
    """Equal-weighted cross-sectional z-score of multi-window annualised
    Sharpe ratio -- the same composite Sharpe.py ranks its live universe by.
    Applies the same MIN_HISTORY gate as the Z-score strategies so all 4
    strategies score the same eligible set of names at every rebalance."""
    raw = {}
    for tkr in tickers:
        if tkr not in prices_up_to_t0.columns:
            continue
        s = prices_up_to_t0[tkr].dropna()
        if len(s) < min_history:
            continue
        raw[tkr] = {label: ml._sharpe_ratio(s, w, rfr_daily, trading_days)
                    for label, w in windows.items()}
    if not raw:
        return pd.Series(dtype=float)

    df = pd.DataFrame.from_dict(raw, orient="index")
    z = pd.DataFrame(index=df.index)
    for label in windows:
        z[label] = ml._cross_section_z(df[label])
    z = z.fillna(0.0)   # missing window -> universe average, same as momentum_lib
    return z.mean(axis=1)


# ── BACKTEST ENGINE (4 strategies sharing one loop) ─────────────────────────

def run_backtest(prices, tickers, top_n, rebalance_freq, start_date):
    rebal_dates = prices.resample(rebalance_freq).last().index
    trading_index = prices.index
    snapped = []
    for d in rebal_dates:
        idx = trading_index[trading_index <= d]
        if len(idx) > 0:
            snapped.append(idx[-1])
    snapped = sorted(set(snapped))
    snapped = [d for d in snapped if d >= pd.to_datetime(start_date)]

    period_returns = {name: [] for name in STRATEGY_NAMES}
    period_dates = []
    period_start_dates = []
    holdings_log = {name: {} for name in STRATEGY_NAMES}
    rfr_daily = RFR_ANNUAL / TRADING_DAYS

    for i in range(len(snapped) - 1):
        t0, t1 = snapped[i], snapped[i + 1]
        prices_up_to_t0 = prices.loc[:t0]

        raw_z = zb.compute_raw_signals(prices_up_to_t0, tickers)
        sharpe_composite = compute_sharpe_composite(
            prices_up_to_t0, tickers, SHARPE_WINDOWS, rfr_daily,
            TRADING_DAYS, MIN_HISTORY)

        fwd_rets = (prices.loc[t1] / prices.loc[t0]) - 1.0

        def pick_and_score(composite):
            top_names = composite.sort_values(ascending=False).head(top_n).index.tolist()
            valid = [n for n in top_names if n in fwd_rets.index and not pd.isna(fwd_rets[n])]
            port_ret = fwd_rets[valid].mean() if valid else 0.0
            return port_ret, top_names

        if raw_z.empty or len(raw_z) < top_n:
            for name in WEIGHTS_Z:
                period_returns[name].append(0.0)
        else:
            z = zb.zscore_cross_section(raw_z)
            for name, w in WEIGHTS_Z.items():
                composite = sum(z[col].fillna(0) * wt for col, wt in w.items())
                composite = composite.reindex(raw_z.index)
                ret, names = pick_and_score(composite)
                period_returns[name].append(ret)
                holdings_log[name][t0] = names

        if len(sharpe_composite) < top_n:
            period_returns["Sharpe Composite"].append(0.0)
        else:
            ret, names = pick_and_score(sharpe_composite)
            period_returns["Sharpe Composite"].append(ret)
            holdings_log["Sharpe Composite"][t0] = names

        period_dates.append(t1)
        period_start_dates.append(t0)
        if (i + 1) % 12 == 0 or i == len(snapped) - 2:
            print(f"  Rebalance {i+1}/{len(snapped)-1}  ({t0.date()} -> {t1.date()})")

    returns_df = pd.DataFrame(period_returns, index=period_dates)
    return returns_df, holdings_log, period_start_dates


# ── MAIN ─────────────────────────────────────────────────────────────────

def main():
    prices, tickers = load_price_matrix()

    print(f"\nRunning backtest: {len(STRATEGY_NAMES)} strategies, "
          f"top {TOP_N}, {REBALANCE_FREQ} rebalance, from {START_DATE} ...")
    returns_df, holdings_log, period_start_dates = run_backtest(
        prices, tickers, TOP_N, REBALANCE_FREQ, START_DATE)

    # Benchmark: NIFTY500 buy & hold over the exact same t0->t1 windows
    bench_series = prices["NIFTY500"]
    bench_period_rets = pd.Series(
        [(bench_series[t1] / bench_series[t0]) - 1.0
         for t0, t1 in zip(period_start_dates, returns_df.index)],
        index=returns_df.index)
    returns_df["NIFTY500 Buy & Hold"] = bench_period_rets

    curves = returns_df.apply(zb.equity_curve)

    print("\n" + "=" * 78)
    print("PERFORMANCE SUMMARY")
    print("=" * 78)
    stats_table = {col: zb.performance_stats(returns_df[col].dropna())
                   for col in returns_df.columns}
    stats_df = pd.DataFrame(stats_table).T
    pct_cols = ["Total Return", "CAGR", "Ann. Volatility", "Max Drawdown", "Win Rate"]
    display_df = stats_df.copy()
    for c in pct_cols:
        display_df[c] = display_df[c].apply(lambda x: f"{x:.1%}")
    display_df["Sharpe Ratio"] = stats_df["Sharpe Ratio"].apply(lambda x: f"{x:.2f}")
    display_df["Calmar Ratio"] = stats_df["Calmar Ratio"].apply(lambda x: f"{x:.2f}")
    print(display_df.to_string())

    stats_df.to_csv(RESULTS_DIR / "performance_summary.csv")
    returns_df.to_csv(RESULTS_DIR / "period_returns.csv")
    curves.to_csv(RESULTS_DIR / "equity_curves.csv")

    plt.figure(figsize=(12, 6.5))
    colors = {"Z Equal Weight": "#1f77b4", "Z Weighted v1 (PRIMARY)": "#ff7f0e",
              "Z Weighted v2 (Trend+Vol)": "#2ca02c", "Sharpe Composite": "#d62728",
              "NIFTY500 Buy & Hold": "#7f7f7f"}
    for col in curves.columns:
        style = "--" if "Buy & Hold" in col else "-"
        lw = 1.6 if "Buy & Hold" in col else 2.2
        plt.plot(curves.index, curves[col], style, label=col, linewidth=lw,
                  color=colors.get(col))
    plt.yscale("log")
    plt.title(f"NSE Momentum Strategy Comparison: Equity Curves (Top {TOP_N}, Monthly Rebalance)")
    plt.ylabel("Growth of ₹1 (log scale)")
    plt.xlabel("Date")
    plt.legend()
    plt.grid(alpha=0.3, which="both")
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / "equity_curves.png", dpi=150)

    print(f"\nSaved outputs to {RESULTS_DIR}:")
    print("  performance_summary.csv, period_returns.csv, equity_curves.csv, equity_curves.png")


if __name__ == "__main__":
    main()
