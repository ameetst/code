"""
ETF Momentum Backtest -- MOM_ACCEL screen on the CURRENT live (Sharpe + Clenow blend) ranking.
=====================================================================================
EVALUATION ONLY -- backtest-only, NOT implemented in etf_momentum_ranking.py.

Same method as etf_backtest_mom_accel_filter.py (which was run on the older Sharpe-only
ranking), now applied on top of the live emr.build_ranking (0.5*Sharpe composite +
0.5*Clenow-90D z, LIQUID excluded, 52wk-high screen). Engine: binary Nifty500>50EMA regime,
weekly hold-and-replace, top 5, sector cap 1, exits 25%-off-high / rank>EXIT_MAX_RANK / 5% TSL.

MOM_ACCEL = z-score (over the 52wk-high-pass pool) of Sharpe_1M + Sharpe_3M - Sharpe_6M
(rf=0; 21/63/126d); screen = MOM_ACCEL_z > 0.

  BASELINE   : live ranking
  ACCEL      : accel-failers unranked (cannot be bought); held ETFs not force-exited for it
  ACCEL+EXIT : as ACCEL, plus a held ETF failing accel is exited (rank forced to 9999)

Usage:
  python etf_backtest_mom_accel_live.py
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
from etf_backtest_no_entry_filter import _zscore, yearly_returns, trade_stats  # noqa: E402

bt, emr, CONFIG = rc.bt, rc.emr, rc.CONFIG
WINDOW_1M = 21
_CACHE: dict = {}


def _base(meta, hist):
    """Live ranking + raw rf=0 Sharpes (1M/3M/6M), cached per as-of date."""
    key = hist.index[-1]
    if key not in _CACHE:
        df = emr.build_ranking(meta, hist)
        if not df.empty:
            s1, s3, s6 = [], [], []
            for t in df["TICKER"]:
                s = hist[t] if t in hist.columns else pd.Series(dtype=float)
                s1.append(emr.sharpe_score(s, WINDOW_1M, daily_rf=0.0))
                s3.append(emr.sharpe_score(s, CONFIG.WINDOW_3M, daily_rf=0.0))
                s6.append(emr.sharpe_score(s, CONFIG.WINDOW_6M, daily_rf=0.0))
            df["S1"], df["S3"], df["S6"] = s1, s3, s6
        _CACHE[key] = df
    return _CACHE[key].copy()


def make_fn(mode):
    def fn(meta, hist):
        df = _base(meta, hist)
        if mode == "base" or df.empty:
            return df
        raw = df["S1"] + df["S3"] - df["S6"].fillna(0.0)
        raw[df["S1"].isna() | df["S3"].isna()] = np.nan
        pool = df["SCREEN_PASS"].astype(bool)
        z = pd.Series(np.nan, index=df.index)
        if pool.sum() > 0:
            z[pool] = _zscore(raw[pool])
        passed = pool & (z > 0)
        new_rank = pd.Series(np.nan, index=df.index)
        new_rank[passed] = df.loc[passed, "RANK_INVESTABLE"].rank(method="first")
        if mode == "accel_exit":
            new_rank[pool & ~passed] = 9999
        df["RANK_INVESTABLE"] = new_rank
        df["SCREEN_PASS"] = passed
        return df
    return fn


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, _ = detect_and_clean(prices_raw)
    all_dates = prices.index
    regime_s, ema50, _ = bt.fetch_benchmark_series(all_dates)

    variants = [("BASELINE (live)", "base"), ("ACCEL", "accel"), ("ACCEL+EXIT", "accel_exit")]
    runs = {}
    for name, mode in variants:
        print(f"\n[run] {name}", flush=True)
        res = rc.run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed", ranking_fn=make_fn(mode))
        tag = mode
        res["equity"].to_csv(_BACKTEST_DIR / f"momaccel_live_{tag}_equity.csv")
        res["trades"].to_csv(_BACKTEST_DIR / f"momaccel_live_{tag}_trade_log.csv", index=False)
        runs[name] = res
        m = rc.compute_metrics(res["equity"]["equity"])
        print(f"      CAGR={m['cagr']:.2%} MaxDD={m['max_dd']:.1%} Sharpe={m['sharpe']:.2f}", flush=True)

    bh = bt.run_benchmark(regime_s)
    names = list(runs)
    cols = names + ["Buy&Hold N500"]
    eqs = {n: runs[n]["equity"]["equity"] for n in names}
    eqs["Buy&Hold N500"] = bh

    def table(slicer):
        rows = {}
        for n in cols:
            m = rc.compute_metrics(slicer(eqs[n]))
            rows[n] = {"CAGR": f"{m['cagr']:.2%}", "Total return": f"{m['final']/m['initial']-1:.1%}",
                       "Max drawdown": f"{m['max_dd']:.1%}", "Volatility": f"{m['vol']:.1%}",
                       "Sharpe (CAGR/vol)": f"{m['sharpe']:.2f}", "Final equity (INR)": f"{m['final']:,.0f}"}
        return pd.DataFrame(rows)

    pd.set_option("display.width", 200)
    print(f"\n{'='*90}\nFULL PERIOD {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*90}")
    print(table(lambda e: e).to_string())
    print(f"\n{'='*90}\nSUB-PERIOD {rc.SUBPERIOD_START} -> {rc.SUBPERIOD_END}\n{'='*90}")
    print(table(lambda e: e.loc[rc.SUBPERIOD_START:rc.SUBPERIOD_END]).to_string())

    ts = pd.DataFrame({n: trade_stats(runs[n]["trades"]) for n in names}).astype(object)
    ts.index = ["Buys", "Win rate", "Avg hold (days)", "Held <=7 days", "Exits via 52wk-DD rule"]
    ts.loc["Win rate"] = [f"{v:.1%}" for v in ts.loc["Win rate"]]
    ts.loc["Avg hold (days)"] = [f"{v:.0f}" for v in ts.loc["Avg hold (days)"]]
    print("\nTrading activity:")
    print(ts.to_string())

    yr = pd.DataFrame({n: yearly_returns(eqs[n]) for n in cols}) * 100
    print("\nCalendar-year returns (%):")
    print(yr.round(1).to_string())
    yr.round(2).to_csv(_BACKTEST_DIR / "momaccel_live_calendar_years.csv")

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    colors = {"BASELINE (live)": "#7f8c8d", "ACCEL": "#c0392b", "ACCEL+EXIT": "#27ae60", "Buy&Hold N500": "#2980b9"}
    for n in cols:
        s = eqs[n]
        ax.plot(s.index, s / s.iloc[0] * 100, label=n, color=colors[n], linewidth=1.4,
                alpha=0.8 if n == "Buy&Hold N500" else 1)
        dd = (s / s.cummax() - 1) * 100
        ax2.plot(dd.index, dd, color=colors[n], linewidth=0.9)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log)")
    ax.set_title("MOM_ACCEL screen on the live Sharpe+Clenow blended ranking")
    ax.legend()
    ax.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    out = _BACKTEST_DIR / "comparison_mom_accel_live.png"
    fig.savefig(out, dpi=140)
    print(f"\nChart -> {out}")


if __name__ == "__main__":
    main()
