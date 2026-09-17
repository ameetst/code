"""
Sub-period analysis: ETF momentum algo vs Buy & Hold Nifty 500, 2018-01-01 -> 2020-12-31
=====================================================================================
EVALUATION ONLY. Does NOT re-run the engines -- reuses the equity curves already
computed on the cleaned History_ETFs.xlsx data (strategy_weekly_baseline_equity.csv,
the live 6M/3M-scoring weekly hold-and-replace engine, and benchmark_equity_clean.csv)
and slices them to this specific window, which covers two real drawdowns:
  - The 2018 IL&FS default / mid-cap-smallcap liquidity crash
  - The Feb-Mar 2020 COVID crash and the initial 2020 recovery

Sub-period metrics (CAGR, max drawdown, vol, Sharpe) are recomputed fresh from
the window's own starting value and running peak -- NOT inherited from
whatever drawdown state existed going into 2018 -- so this measures how each
approach actually behaved during this specific stretch.

Usage:
  python etf_backtest_subperiod_2018_2020.py
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
START = "2018-01-01"
END = "2020-12-31"


def compute_metrics(eq: pd.Series) -> dict:
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    initial, final = eq.iloc[0], eq.iloc[-1]
    cagr = (final / initial) ** (1 / years) - 1
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = dd.min()
    max_dd_date = dd.idxmin()
    daily_ret = eq.pct_change().dropna()
    vol = daily_ret.std() * np.sqrt(252)
    sharpe = cagr / vol if vol > 0 else 0.0
    return {"years": years, "initial": initial, "final": final, "cagr": cagr,
            "max_dd": max_dd, "max_dd_date": max_dd_date, "vol": vol, "sharpe": sharpe}


def print_metrics(name: str, m: dict):
    print(f"\n  {name}")
    print(f"    Total Return    : {(m['final']/m['initial']-1):>9.2%}")
    print(f"    CAGR            : {m['cagr']:>9.2%}")
    print(f"    Max Drawdown    : {m['max_dd']:>9.2%}  (trough: {m['max_dd_date'].date()})")
    print(f"    Volatility      : {m['vol']:>9.2%}")
    print(f"    Sharpe (simple) : {m['sharpe']:>9.2f}")


def main():
    strat = pd.read_csv(_BACKTEST_DIR / "strategy_weekly_baseline_equity.csv",
                         index_col=0, parse_dates=True)["equity"]
    bh = pd.read_csv(_BACKTEST_DIR / "benchmark_equity_clean.csv",
                      index_col=0, parse_dates=True)["equity"]

    strat_w = strat.loc[START:END]
    bh_w = bh.loc[START:END]

    print(f"{'='*70}\n  SUB-PERIOD: {strat_w.index[0].date()} -> {strat_w.index[-1].date()}\n"
          f"  (2018 IL&FS/mid-cap crash + Feb-Mar 2020 COVID crash)\n{'='*70}")

    m_strat = compute_metrics(strat_w)
    print_metrics("CURRENT ALGO (weekly, live 6M/3M scoring)", m_strat)

    m_bh = compute_metrics(bh_w)
    print_metrics("BUY & HOLD NIFTY 500", m_bh)

    fig, ax = plt.subplots(figsize=(13, 7))
    strat_norm = strat_w / strat_w.iloc[0] * 100
    bh_norm = bh_w / bh_w.iloc[0] * 100
    ax.plot(strat_norm.index, strat_norm, label="CURRENT algo (weekly)", color="#c0392b", linewidth=1.5)
    ax.plot(bh_norm.index, bh_norm, label="Buy & Hold Nifty 500", color="#2980b9", linewidth=1.5)
    ax.axvspan(pd.Timestamp("2018-08-01"), pd.Timestamp("2019-03-01"), color="grey", alpha=0.12, label="IL&FS / mid-cap crash")
    ax.axvspan(pd.Timestamp("2020-02-15"), pd.Timestamp("2020-04-15"), color="orange", alpha=0.15, label="COVID crash")
    ax.set_title("ETF Momentum Algo vs Buy & Hold Nifty 500 -- 2018-2020 (real drawdown period)")
    ax.set_ylabel("Growth of 100")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.xticks(rotation=45)
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_2018_2020_drawdown.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / 'comparison_2018_2020_drawdown.png'}")

    print(f"\n{'='*70}\n  SUMMARY: {START} -> {END}\n{'='*70}")
    print(f"  {'Metric':<20}{'CURRENT algo':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("Total Return", None, None), ("CAGR", "cagr", "{:.2%}"),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"),
    ]:
        if key is None:
            a = f"{(m_strat['final']/m_strat['initial']-1):.2%}"
            b = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            a = fmt.format(m_strat[key]); b = fmt.format(m_bh[key])
        print(f"  {label:<20}{a:>18}{b:>18}")


if __name__ == "__main__":
    main()
