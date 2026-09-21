"""
ETF Momentum Backtest -- MOM_ACCEL screen on top of the CURRENT live rules.
=====================================================================================
EVALUATION ONLY -- backtest-only variant, NOT implemented in etf_momentum_ranking.py.

Screen tested:   PCT_FROM_HIGH <= 25%   AND   MOM_ACCEL > 0

MOM_ACCEL (same definition as etf_momentum_weekly.py):
    raw   = Sharpe_1M + Sharpe_3M - Sharpe_6M      (rf = 0, annualised; 21/63/126d)
            (Sharpe_6M treated as 0 if unavailable; NaN if 1M or 3M unavailable)
    accel = cross-sectional z-score of raw over the ETFs that pass the 52wk-high screen
    pass  = accel > 0

Runs three variants on the identical engine used by
etf_backtest_rank_cutoff_fixed_vs_pct.py (binary Nifty500 > 50EMA regime, weekly
hold-and-replace, top 5, sector cap 1, exit: 52wk DD>25% / rank>EXIT_MAX_RANK / TSL 5%):

  BASELINE   : live screen (52wk-high only)
  ACCEL      : screen = 52wk-high AND MOM_ACCEL>0. Accel-failers are unranked
               (RANK_INVESTABLE = NaN), so they cannot be bought, but an already
               held ETF is not force-exited merely for failing accel (same way
               the live code treats a NaN rank).
  ACCEL+EXIT : as ACCEL, but a held ETF that fails accel is also exited
               (rank forced to 9999 -> trips the rank exit).

Usage:
  python etf_backtest_mom_accel_filter.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_rank_cutoff_fixed_vs_pct as rc                     # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean   # noqa: E402

bt = rc.bt
emr = rc.emr
CONFIG = rc.CONFIG

WINDOW_1M = 21
SUBPERIOD_START, SUBPERIOD_END = rc.SUBPERIOD_START, rc.SUBPERIOD_END


def _zscore(s: pd.Series) -> pd.Series:
    mu, sig = s.mean(), s.std()
    if sig == 0 or np.isnan(sig):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sig


def make_ranking_fn(mode: str):
    """mode: 'accel' (unranked if accel fails) or 'accel_exit' (forced-exit rank)."""

    def ranking_fn(meta, hist):
        df = emr.build_ranking(meta, hist)
        if df.empty:
            return df

        raw = []
        for t in df["TICKER"]:
            s = hist[t] if t in hist.columns else pd.Series(dtype=float)
            sh1 = emr.sharpe_score(s, WINDOW_1M, daily_rf=0.0)
            sh3 = emr.sharpe_score(s, CONFIG.WINDOW_3M, daily_rf=0.0)
            sh6 = emr.sharpe_score(s, CONFIG.WINDOW_6M, daily_rf=0.0)
            if np.isnan(sh1) or np.isnan(sh3):
                raw.append(np.nan)
            else:
                raw.append((sh1 + sh3) - (0.0 if np.isnan(sh6) else sh6))
        df["MOM_ACCEL_RAW"] = raw

        pool = df["SCREEN_PASS"].astype(bool)
        df["MOM_ACCEL_Z"] = np.nan
        if pool.sum() > 0:
            df.loc[pool, "MOM_ACCEL_Z"] = _zscore(df.loc[pool, "MOM_ACCEL_RAW"])
        accel_pass = df["MOM_ACCEL_Z"] > 0

        final_pass = pool & accel_pass
        # Re-rank among the accel-passers, preserving the live composite order.
        new_rank = pd.Series(np.nan, index=df.index)
        new_rank[final_pass] = df.loc[final_pass, "RANK_INVESTABLE"].rank(method="first")
        if mode == "accel_exit":
            new_rank[pool & ~accel_pass] = 9999
        df["RANK_INVESTABLE"] = new_rank
        df["SCREEN_PASS"] = final_pass
        return df

    return ranking_fn


def yearly_returns(eq: pd.Series) -> pd.Series:
    ye = eq.groupby(eq.index.year).last()
    prev = ye.shift(1)
    prev.iloc[0] = eq.iloc[0]
    return ye / prev - 1


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    print("\n[clean] Applying the same transient/permanent bad-print fix as before ...")
    prices, audit = detect_and_clean(prices_raw)
    print(f"        {len(audit)} fixes applied")

    all_dates = prices.index
    regime_s, ema50, _ = bt.fetch_benchmark_series(all_dates)

    runs = {}
    for name, fn in [("BASELINE", emr.build_ranking),
                     ("ACCEL", make_ranking_fn("accel")),
                     ("ACCEL+EXIT", make_ranking_fn("accel_exit"))]:
        print(f"\n{'='*70}\n  {name}\n{'='*70}")
        res = rc.run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed", ranking_fn=fn)
        tag = name.replace("+", "_").lower()
        res["equity"].to_csv(_BACKTEST_DIR / f"momaccel_{tag}_equity.csv")
        res["trades"].to_csv(_BACKTEST_DIR / f"momaccel_{tag}_trade_log.csv", index=False)
        m = rc.compute_metrics(res["equity"]["equity"])
        rc.print_metrics(name, m)
        runs[name] = (res, m)

    bh_eq = bt.run_benchmark(regime_s)
    m_bh = rc.compute_metrics(bh_eq)

    names = list(runs)
    hdr = f"  {'Metric':<20}" + "".join(f"{n:>16}" for n in names) + f"{'Buy&Hold N500':>16}"

    def table(title, eqs, mets):
        print(f"\n{'='*len(hdr)}\n  {title}\n{'='*len(hdr)}\n{hdr}")
        rows = [("CAGR", lambda m: f"{m['cagr']:.2%}"),
                ("Total Return", lambda m: f"{m['final']/m['initial']-1:.2%}"),
                ("Max Drawdown", lambda m: f"{m['max_dd']:.2%}"),
                ("Volatility", lambda m: f"{m['vol']:.2%}"),
                ("Sharpe (simple)", lambda m: f"{m['sharpe']:.2f}")]
        for label, f in rows:
            print(f"  {label:<20}" + "".join(f"{f(m):>16}" for m in mets))

    table(f"FULL PERIOD  {all_dates[0].date()} -> {all_dates[-1].date()}",
          None, [runs[n][1] for n in names] + [m_bh])
    print(f"  {'Final Equity (INR)':<20}" + "".join(f"{runs[n][1]['final']:>16,.0f}" for n in names)
          + f"{m_bh['final']:>16,.0f}")

    sub = [rc.compute_metrics(runs[n][0]["equity"]["equity"].loc[SUBPERIOD_START:SUBPERIOD_END]) for n in names]
    sub.append(rc.compute_metrics(bh_eq.loc[SUBPERIOD_START:SUBPERIOD_END]))
    table(f"SUB-PERIOD {SUBPERIOD_START} -> {SUBPERIOD_END}", None, sub)

    print(f"\n  Trade counts:")
    for n in names:
        tr = runs[n][0]["trades"]
        buys = int((tr["TYPE"] == "BUY").sum()) if len(tr) else 0
        sells = tr[tr["TYPE"] == "SELL"] if len(tr) else tr
        win = (sells["NET_PNL"] > 0).mean() if len(sells) else float("nan")
        print(f"    {n:<12} buys={buys:<4} sells={len(sells):<4} win-rate={win:.1%}")

    yr = pd.DataFrame({n: yearly_returns(runs[n][0]["equity"]["equity"]) for n in names})
    yr["Buy&Hold N500"] = yearly_returns(bh_eq)
    print("\n  Calendar-year returns:")
    print((yr * 100).round(1).to_string())

    fig, ax = plt.subplots(figsize=(13, 7))
    colors = {"BASELINE": "#7f8c8d", "ACCEL": "#c0392b", "ACCEL+EXIT": "#27ae60"}
    for n in names:
        s = runs[n][0]["equity"]["equity"]
        ax.plot(s.index, s / s.iloc[0] * 100, label=n, color=colors[n], linewidth=1.3)
    ax.plot(bh_eq.index, bh_eq / bh_eq.iloc[0] * 100, label="Buy & Hold Nifty 500",
            color="#2980b9", linewidth=1.1, alpha=0.8)
    ax.set_title("ETF Momentum: MOM_ACCEL screen vs baseline (binary regime, weekly)")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    out = _BACKTEST_DIR / "comparison_mom_accel.png"
    fig.savefig(out, dpi=140)
    print(f"\nChart -> {out}")


if __name__ == "__main__":
    main()
