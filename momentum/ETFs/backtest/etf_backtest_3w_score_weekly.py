"""
ETF Momentum Backtest -- Weekly cadence (live rules) with 3M/6M/12M equal-weighted
scoring vs the live 6M/3M scoring, vs Buy & Hold Nifty 500
=====================================================================================
EVALUATION ONLY -- backtest-only variant, NOT implemented in etf_momentum_ranking.py.

Rule 4 (cadence) reverts to the live weekly hold-and-replace behaviour:
reuses emr.build_allocation() unchanged (exit -> replace with the
next-ranked ETF the same week, exactly the live/"WEEKLY" engine from
etf_backtest_current_vs_nifty500.py).

Rule 2 (scoring) changes to an equal-weighted composite of Z-scored Sharpe
ratios over 3M/6M/12M windows (instead of the live 0.5*Z(6M)+0.5*Z(3M)):
    composite = mean( Z(Sharpe_3M), Z(Sharpe_6M), Z(Sharpe_12M) ), skipping
    whichever window lacks enough history for a given ETF (same
    partial-availability fallback style as the live scoring, not a strict
    3W requirement).

Everything else (52wk-high screen, tiered regime, sector cap, all three
exit rules) is untouched -- reuses the live emr functions directly.

Usage:
  python etf_backtest_3w_score_weekly.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_current_vs_nifty500 as bt              # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean  # noqa: E402

emr = bt.emr
CONFIG = bt.CONFIG

SCORE_WINDOWS = {"3M": 63, "6M": 126, "12M": 252}


def _zscore(series: pd.Series) -> pd.Series:
    mu, sig = series.mean(), series.std()
    if sig == 0 or np.isnan(sig):
        return pd.Series(0.0, index=series.index)
    return (series - mu) / sig


def build_ranking_3w(meta: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Same screen (52wk-high proximity) as the live build_ranking(), but scores
    with an equal-weighted composite of Z(Sharpe_3M), Z(Sharpe_6M), Z(Sharpe_12M)
    instead of the live 0.5/0.5 6M+3M blend."""
    records = []
    for _, row in meta.iterrows():
        ticker = row["TICKER"]
        if ticker not in prices.columns:
            continue
        s = prices[ticker]
        close = float(s.iloc[-1]) if len(s) > 0 else np.nan
        high_52w = float(s.tail(252).max()) if len(s) > 0 else np.nan

        sharpes = {label: emr.sharpe_score(s, days) for label, days in SCORE_WINDOWS.items()}

        if pd.notna(close) and pd.notna(high_52w) and high_52w > 0:
            pct_from_high = (high_52w - close) / high_52w
            high_pass = pct_from_high <= CONFIG.MAX_DRAWDOWN_FROM_HIGH
        else:
            pct_from_high = np.nan
            high_pass = True

        rec = {
            "TICKER": ticker, "ETF_NAME": row["ETF_NAME"],
            "SECTOR": emr.classify_sector(row["ETF_NAME"], ticker),
            "CLOSE": close, "PCT_FROM_HIGH": pct_from_high * 100 if pd.notna(pct_from_high) else np.nan,
            "SCREEN_PASS": high_pass,
        }
        for label, val in sharpes.items():
            rec[f"SHARPE_{label}"] = val
        records.append(rec)

    df = pd.DataFrame(records)
    if df.empty:
        return df

    inv_mask = df["SCREEN_PASS"]
    z_cols = {}
    for label in SCORE_WINDOWS:
        z = pd.Series(np.nan, index=df.index)
        if inv_mask.sum() > 0:
            z.loc[inv_mask] = _zscore(df.loc[inv_mask, f"SHARPE_{label}"])
        z_cols[label] = z
    zdf = pd.DataFrame(z_cols)

    df["_WTD_INV"] = np.nan
    df.loc[inv_mask, "_WTD_INV"] = zdf.loc[inv_mask].mean(axis=1, skipna=True)

    inv = df[df["SCREEN_PASS"]].copy()
    if len(inv) > 0:
        inv["RANK_INVESTABLE"] = inv["_WTD_INV"].rank(ascending=False, na_option="bottom").astype(int)
        df = df.merge(inv[["TICKER", "RANK_INVESTABLE"]], on="TICKER", how="left")
    else:
        df["RANK_INVESTABLE"] = np.nan

    return df.drop(columns=["_WTD_INV"], errors="ignore")


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    print("\n[clean] Applying the same transient/permanent bad-print fix as before ...")
    prices, audit = detect_and_clean(prices_raw)
    print(f"        {len(audit)} fixes applied ({(audit['TYPE']=='TRANSIENT_NULLED').sum()} transient, "
          f"{(audit['TYPE']!='TRANSIENT_NULLED').sum()} permanent rescale)")

    all_dates = prices.index
    regime_s, ema50, ema100 = bt.fetch_benchmark_series(all_dates)

    print(f"\n{'='*70}\n  BASELINE -- WEEKLY, live scoring (0.5*Z6M + 0.5*Z3M)\n{'='*70}")
    res_base = bt.run_strategy(meta, prices, regime_s, ema50, ema100)  # default ranking_fn = emr.build_ranking
    res_base["equity"].to_csv(_BACKTEST_DIR / "strategy_weekly_baseline_equity.csv")
    res_base["trades"].to_csv(_BACKTEST_DIR / "strategy_weekly_baseline_trade_log.csv", index=False)
    m_base = bt.compute_metrics(res_base["equity"]["equity"])
    bt.print_metrics("WEEKLY -- live scoring (6M/3M)", m_base)

    print(f"\n{'='*70}\n  NEW -- WEEKLY, 3M/6M/12M equal-weighted scoring\n{'='*70}")
    res_3w = bt.run_strategy(meta, prices, regime_s, ema50, ema100, ranking_fn=build_ranking_3w)
    res_3w["equity"].to_csv(_BACKTEST_DIR / "strategy_weekly_3w_equity.csv")
    res_3w["trades"].to_csv(_BACKTEST_DIR / "strategy_weekly_3w_trade_log.csv", index=False)
    m_3w = bt.compute_metrics(res_3w["equity"]["equity"])
    bt.print_metrics("WEEKLY -- 3M/6M/12M equal-weighted scoring", m_3w)

    print(f"\n{'='*70}\n  Buy & Hold -- {bt.BENCHMARK_TICKER}\n{'='*70}")
    bh_eq = bt.run_benchmark(regime_s)
    m_bh = bt.compute_metrics(bh_eq)
    bt.print_metrics("BUY & HOLD NIFTY 500", m_bh)

    for name, series in [("baseline (6M/3M)", res_base["equity"]["equity"]),
                          ("3W (3M/6M/12M)", res_3w["equity"]["equity"])]:
        r = series.pct_change().dropna()
        extreme = r[r.abs() > 0.15]
        if len(extreme):
            print(f"\n  [!] {name}: {len(extreme)} residual daily move(s) > 15%:")
            print(extreme.to_string())

    fig, ax = plt.subplots(figsize=(13, 7))
    for name, series, color in [
        ("WEEKLY -- live scoring (6M/3M)", res_base["equity"]["equity"], "#7f8c8d"),
        ("WEEKLY -- 3M/6M/12M equal-weighted", res_3w["equity"]["equity"], "#c0392b"),
        (f"Buy & Hold {bt.BENCHMARK_TICKER}", bh_eq, "#2980b9"),
    ]:
        norm = series / series.iloc[0] * 100
        ax.plot(norm.index, norm, label=name, color=color, linewidth=1.3)
    ax.set_title("ETF Momentum: 6M/3M vs 3M/6M/12M scoring (weekly hold-and-replace) vs Buy & Hold")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_3w_score_vs_baseline.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / 'comparison_3w_score_vs_baseline.png'}")

    print(f"\n{'='*70}\n  SUMMARY: {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*70}")
    print(f"  {'Metric':<20}{'6M/3M (live)':>18}{'3M/6M/12M':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("CAGR", "cagr", "{:.2%}"), ("Total Return", None, None),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"), ("Final Equity", "final", "INR {:,.0f}"),
    ]:
        if key is None:
            b = f"{(m_base['final']/m_base['initial']-1):.2%}"
            n = f"{(m_3w['final']/m_3w['initial']-1):.2%}"
            h = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            b = fmt.format(m_base[key]); n = fmt.format(m_3w[key]); h = fmt.format(m_bh[key])
        print(f"  {label:<20}{b:>18}{n:>18}{h:>18}")

    n_buys_b = (res_base["trades"]["TYPE"] == "BUY").sum() if len(res_base["trades"]) else 0
    n_buys_n = (res_3w["trades"]["TYPE"] == "BUY").sum() if len(res_3w["trades"]) else 0
    print(f"\n  Total BUY trades -- 6M/3M: {n_buys_b}   3M/6M/12M: {n_buys_n}")


if __name__ == "__main__":
    main()
