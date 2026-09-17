"""
ETF Momentum Backtest -- CURRENT live algo vs Buy & Hold Nifty 500 (CLEANED)
=====================================================================================
EVALUATION ONLY. Re-runs etf_backtest_current_vs_nifty500.py's exact engine
(imported, not duplicated) after detecting and neutralising bad prints in
History_ETFs.xlsx -- the same kind of decimal-place data glitches found in
the earlier ETF.xlsx-based backtests (documented there in
DATA_QUALITY_ISSUES_v13.csv).

Detection: for every ticker, flag any single-day log-return with
|log_ret| > LOG_RET_THRESHOLD (default 1.2, i.e. a >232% one-day move --
far beyond anything a real ETF NAV does, but well inside the ~ln(10)=2.30
and ~ln(100)=4.61 signatures a 10x/100x decimal shift produces). Each
flagged (TICKER, BAD_DATE) cell is nulled and forward-filled, exactly like
the earlier cleaning pass, and the flagged list is written out for audit.

Usage:
  python etf_backtest_current_vs_nifty500_clean.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_current_vs_nifty500 as bt  # noqa: E402

LOG_RET_THRESHOLD = 1.2   # ~e^1.2 = 3.3x single-day move
RECOVERY_WINDOW = 10      # trading days to look for a round-trip recovery
RECOVERY_TOL = 0.15       # within 15% of the pre-jump price counts as "recovered"


def detect_and_clean(prices: pd.DataFrame):
    """
    For each ticker, walk forward through big single-day moves
    (|log-return| > LOG_RET_THRESHOLD). Two distinct patterns show up in
    this data:

      1. TRANSIENT glitch -- price jumps by ~10x/100x for 1-3 days then
         jumps back close to the pre-jump level (e.g. AUTOIETF one day,
         NV20 two days). Fix: null the bad day(s), forward-fill.

      2. PERMANENT rebase -- price jumps by ~10x and NEVER comes back
         (e.g. MIDCAPIETF drops 10x on 2023-12-19 and stays there for the
         remaining 3 years -- a vendor decimal/unit rebase, not a blip).
         Nulling+ffilling only shifts the discontinuity one day later and
         leaves the whole rebased tail wrong. Fix: rescale every price
         from that day onward by the detected power-of-10 factor so the
         series reconnects with its own pre-break level.

    Returns (cleaned_prices, audit_df).
    """
    cleaned = prices.copy()
    audit = []

    for ticker in prices.columns:
        s = cleaned[ticker].copy()
        i = 1
        n = len(s)
        while i < n:
            prev_price = s.iloc[i - 1]
            cur_price = s.iloc[i]
            if pd.isna(prev_price) or pd.isna(cur_price) or prev_price <= 0 or cur_price <= 0:
                i += 1
                continue
            log_ret = np.log(cur_price / prev_price)
            if abs(log_ret) <= LOG_RET_THRESHOLD:
                i += 1
                continue

            # Look for a round-trip recovery within RECOVERY_WINDOW days
            recovered_at = None
            for j in range(i, min(i + RECOVERY_WINDOW, n)):
                if j == i:
                    continue
                pj = s.iloc[j]
                if pd.notna(pj) and abs(pj / prev_price - 1) < RECOVERY_TOL:
                    recovered_at = j
                    break

            if recovered_at is not None:
                for k in range(i, recovered_at):
                    audit.append({"TICKER": ticker, "TYPE": "TRANSIENT_NULLED",
                                   "DATE": prices.index[k].date(),
                                   "BAD_PRICE": prices.loc[prices.index[k], ticker],
                                   "REF_PRICE": prev_price})
                    s.iloc[k] = np.nan
                s = s.ffill()
                i = recovered_at
            else:
                factor = prev_price / cur_price
                pow10 = round(np.log10(abs(factor)))
                if pow10 == 0:
                    i += 1
                    continue
                scale = 10.0 ** pow10
                audit.append({"TICKER": ticker, "TYPE": f"PERMANENT_RESCALE_x{scale:g}",
                               "DATE": prices.index[i].date(),
                               "BAD_PRICE": cur_price, "REF_PRICE": prev_price})
                s.iloc[i:] = s.iloc[i:] * scale
                i += 1

        cleaned[ticker] = s

    return cleaned, pd.DataFrame(audit)


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)

    print(f"\n[clean] Scanning for single-day |log-return| > {LOG_RET_THRESHOLD} "
          f"(~{np.exp(LOG_RET_THRESHOLD):.1f}x move), classifying transient vs permanent ...")
    prices, audit = detect_and_clean(prices_raw)
    audit = audit.sort_values(["TICKER", "DATE"]).reset_index(drop=True)
    audit.to_csv(_BACKTEST_DIR / "DATA_QUALITY_ISSUES.csv", index=False)
    n_transient = (audit["TYPE"] == "TRANSIENT_NULLED").sum() if len(audit) else 0
    n_rescale = len(audit) - n_transient
    print(f"        {len(audit)} fixes across {audit['TICKER'].nunique() if len(audit) else 0} tickers "
          f"({n_transient} transient days nulled, {n_rescale} permanent rescales) -> DATA_QUALITY_ISSUES.csv")

    print(f"\n{'='*70}\n  [CLEAN] Fetching regime/benchmark series\n{'='*70}")
    all_dates = prices.index
    regime_s, ema50, ema100 = bt.fetch_benchmark_series(all_dates)

    print(f"\n{'='*70}\n  [CLEAN] Running CURRENT algo\n{'='*70}")
    res = bt.run_strategy(meta, prices, regime_s, ema50, ema100)
    res["equity"].to_csv(_BACKTEST_DIR / "strategy_equity_clean.csv")
    res["trades"].to_csv(_BACKTEST_DIR / "strategy_trade_log_clean.csv", index=False)
    m_strat = bt.compute_metrics(res["equity"]["equity"])
    bt.print_metrics("CURRENT ALGO (cleaned)", m_strat)

    print(f"\n{'='*70}\n  [CLEAN] Buy & Hold -- {bt.BENCHMARK_TICKER}\n{'='*70}")
    bh_eq = bt.run_benchmark(regime_s)
    bh_eq.to_frame("equity").to_csv(_BACKTEST_DIR / "benchmark_equity_clean.csv")
    m_bh = bt.compute_metrics(bh_eq)
    bt.print_metrics("BUY & HOLD NIFTY 500", m_bh)

    # ---- residual outlier check ----
    for name, series in [("strategy (cleaned)", res["equity"]["equity"]), ("benchmark", bh_eq)]:
        r = series.pct_change().dropna()
        extreme = r[r.abs() > 0.15]
        if len(extreme):
            print(f"\n  [!] {name}: {len(extreme)} residual daily move(s) > 15%:")
            print(extreme.to_string())
        else:
            print(f"\n  [ok] {name}: no daily move > 15% remaining.")

    # ---- load the uncleaned run for a 3-way comparison ----
    old_eq = pd.read_csv(_BACKTEST_DIR / "strategy_equity.csv", index_col=0, parse_dates=True)["equity"]
    m_old = bt.compute_metrics(old_eq)

    # ---- comparison chart ----
    fig, ax = plt.subplots(figsize=(13, 7))
    for name, series, color in [
        ("CURRENT algo -- uncleaned", old_eq, "#7f8c8d"),
        ("CURRENT algo -- cleaned", res["equity"]["equity"], "#c0392b"),
        (f"Buy & Hold {bt.BENCHMARK_TICKER} (Nifty 500)", bh_eq, "#2980b9"),
    ]:
        norm = series / series.iloc[0] * 100
        ax.plot(norm.index, norm, label=name, color=color, linewidth=1.3)
    ax.set_title("ETF Momentum: CURRENT algo (bad-print-cleaned) vs Buy & Hold Nifty 500")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_equity_curve_clean.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / 'comparison_equity_curve_clean.png'}")

    # ---- summary table ----
    print(f"\n{'='*70}\n  SUMMARY: {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*70}")
    print(f"  {'Metric':<20}{'Algo (uncleaned)':>20}{'Algo (cleaned)':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("CAGR", "cagr", "{:.2%}"), ("Total Return", None, None),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"), ("Final Equity", "final", "INR {:,.0f}"),
    ]:
        if key is None:
            o = f"{(m_old['final']/m_old['initial']-1):.2%}"
            c = f"{(m_strat['final']/m_strat['initial']-1):.2%}"
            b = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            o = fmt.format(m_old[key]); c = fmt.format(m_strat[key]); b = fmt.format(m_bh[key])
        print(f"  {label:<20}{o:>20}{c:>18}{b:>18}")


if __name__ == "__main__":
    main()
