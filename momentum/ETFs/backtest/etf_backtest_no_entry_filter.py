"""
ETF Momentum Backtest -- remove the 52-week-high ENTRY filter.
=====================================================================================
EVALUATION ONLY -- backtest-only, NOT implemented in etf_momentum_ranking.py.

Same engine as etf_backtest_rank_cutoff_fixed_vs_pct.py: binary Nifty500>50EMA regime,
weekly hold-and-replace, top 5, sector cap 1, exits = 25%-off-52wk-high / rank>EXIT_MAX_RANK
/ 5% TSL. LIQUID-sector funds (CONFIG.EXCLUDE_SECTORS) are excluded everywhere.

Two ranking families, each run with and without the 52wk-high ENTRY filter:
  LIVE (current)  : 0.5*Sharpe(6M/3M) composite + 0.5*Clenow-90D z-score (Oct-2026 live algo)
  SHARPE-ONLY     : previous algo (BLEND_W_CLENOW = 0)
  "no filter"     : every non-excluded ETF is ranked and buyable regardless of distance from
                    the 52wk high; z-scores use that full pool. The 25%-off-high EXIT rule is
                    KEPT, so a deep-drawdown ETF bought here can be sold again next week.

Usage:
  python etf_backtest_no_entry_filter.py
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

bt, emr, CONFIG = rc.bt, rc.emr, rc.CONFIG
_CACHE: dict = {}


def _base(meta, hist):
    key = hist.index[-1]
    if key not in _CACHE:
        _CACHE[key] = emr.build_ranking(meta, hist)
    return _CACHE[key].copy()


def _zscore(s: pd.Series) -> pd.Series:
    mu, sig = s.mean(), s.std()
    if sig == 0 or np.isnan(sig):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sig


def rerank(df: pd.DataFrame, gate: bool, blend: bool) -> pd.DataFrame:
    """Recompute pool, z-scores and rank with the same maths as emr.build_ranking.
    gate=True -> pool = live SCREEN_PASS (52wk-high AND sector not excluded);
    gate=False -> pool = every non-excluded-sector ETF.
    blend=True -> live Sharpe/Clenow blend; False -> Sharpe-only (previous algo)."""
    if df.empty:
        return df
    if gate:
        pool = df["SCREEN_PASS"].astype(bool)
    else:
        pool = ~df["SECTOR"].isin(CONFIG.EXCLUDE_SECTORS)

    z6 = pd.Series(np.nan, index=df.index)
    z3 = pd.Series(np.nan, index=df.index)
    zc = pd.Series(np.nan, index=df.index)
    if pool.sum() > 0:
        z6[pool] = _zscore(df.loc[pool, "SHARPE_6M"])
        z3[pool] = _zscore(df.loc[pool, "SHARPE_3M"])
        zc[pool] = _zscore(df.loc[pool, "CLENOW_90D"])
    both, only6, only3 = z6.notna() & z3.notna(), z6.notna() & z3.isna(), z6.isna() & z3.notna()
    sc = pd.Series(np.nan, index=df.index)
    sc[both] = CONFIG.SHARPE_W6M * z6[both] + CONFIG.SHARPE_W3M * z3[both]
    sc[only6] = CONFIG.SHARPE_W6M * z6[only6]
    sc[only3] = CONFIG.SHARPE_W3M * z3[only3]

    if blend:
        wS, wC = CONFIG.BLEND_W_SHARPE, CONFIG.BLEND_W_CLENOW
        score = pd.Series(np.nan, index=df.index)
        b, os_, oc = sc.notna() & zc.notna(), sc.notna() & zc.isna(), sc.isna() & zc.notna()
        score[b] = wS * sc[b] + wC * zc[b]
        score[os_] = wS * sc[os_]
        score[oc] = wC * zc[oc]
    else:
        score = sc

    out = df.copy()
    out["RANK_INVESTABLE"] = np.nan
    if pool.sum() > 0:
        out.loc[pool, "RANK_INVESTABLE"] = score[pool].rank(ascending=False, na_option="bottom").astype(int)
    out["SCREEN_PASS"] = pool
    return out


def make_fn(gate, blend):
    return lambda meta, hist: rerank(_base(meta, hist), gate, blend)


def yearly_returns(eq: pd.Series) -> pd.Series:
    ye = eq.groupby(eq.index.year).last()
    prev = ye.shift(1)
    prev.iloc[0] = eq.iloc[0]
    return ye / prev - 1


def trade_stats(tr: pd.DataFrame) -> dict:
    if tr is None or not len(tr):
        return {}
    buys = tr[tr["TYPE"] == "BUY"]
    sells = tr[tr["TYPE"] == "SELL"].copy()
    hold = (pd.to_datetime(sells["EXIT_DATE"]) - pd.to_datetime(sells["ENTRY_DATE"])).dt.days
    return {
        "buys": len(buys),
        "win": float((sells["NET_PNL"] > 0).mean()) if len(sells) else np.nan,
        "avg_hold": float(hold.mean()) if len(sells) else np.nan,
        "quick": int((hold <= 7).sum()),
        "dd_exits": int(sells["REASON"].str.contains("52wk high DD").sum()),
    }


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, _ = detect_and_clean(prices_raw)
    all_dates = prices.index
    regime_s, ema50, _ = bt.fetch_benchmark_series(all_dates)

    # sanity: re-ranker with gate on must reproduce the live ranking exactly
    ok = 0
    test_dates = [all_dates[k] for k in (400, 900, 1400, 1900, 2400)]
    for d in test_dates:
        ref = _base(meta, prices.loc[:d])
        mine = rerank(ref, gate=True, blend=True)
        ra = ref.dropna(subset=["RANK_INVESTABLE"]).set_index("TICKER")["RANK_INVESTABLE"].astype(int)
        rb = mine.dropna(subset=["RANK_INVESTABLE"]).set_index("TICKER")["RANK_INVESTABLE"].astype(int)
        ok += int(ra.sort_index().equals(rb.sort_index()))
    print(f"[sanity] re-ranker == live ranking on {ok}/{len(test_dates)} test dates")

    variants = [
        ("LIVE + 52wk filter (current)", _base),
        ("LIVE, NO entry filter", make_fn(False, True)),
        ("SHARPE-ONLY + 52wk filter", make_fn(True, False)),
        ("SHARPE-ONLY, NO entry filter", make_fn(False, False)),
    ]
    runs = {}
    for name, fn in variants:
        print(f"\n[run] {name}", flush=True)
        res = rc.run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed", ranking_fn=fn)
        tag = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
        res["equity"].to_csv(_BACKTEST_DIR / f"noentry_{tag}_equity.csv")
        res["trades"].to_csv(_BACKTEST_DIR / f"noentry_{tag}_trade_log.csv", index=False)
        runs[name] = res
        m = rc.compute_metrics(res["equity"]["equity"])
        print(f"      CAGR={m['cagr']:.2%} MaxDD={m['max_dd']:.1%} Sharpe={m['sharpe']:.2f}", flush=True)

    bh = bt.run_benchmark(regime_s)
    names = list(runs)
    cols = names + ["Buy&Hold N500"]
    eqs = {n: runs[n]["equity"]["equity"] for n in names}
    eqs["Buy&Hold N500"] = bh

    def metrics_table(slicer):
        rows = {}
        for n in cols:
            e = slicer(eqs[n])
            m = rc.compute_metrics(e)
            rows[n] = {"CAGR": f"{m['cagr']:.2%}", "Total return": f"{m['final']/m['initial']-1:.1%}",
                       "Max drawdown": f"{m['max_dd']:.1%}", "Volatility": f"{m['vol']:.1%}",
                       "Sharpe (CAGR/vol)": f"{m['sharpe']:.2f}", "Final equity (INR)": f"{m['final']:,.0f}"}
        return pd.DataFrame(rows)

    pd.set_option("display.width", 200)
    print(f"\n{'='*100}\nFULL PERIOD {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*100}")
    print(metrics_table(lambda e: e).to_string())
    print(f"\n{'='*100}\nSUB-PERIOD {rc.SUBPERIOD_START} -> {rc.SUBPERIOD_END}\n{'='*100}")
    print(metrics_table(lambda e: e.loc[rc.SUBPERIOD_START:rc.SUBPERIOD_END]).to_string())

    ts = pd.DataFrame({n: trade_stats(runs[n]["trades"]) for n in names}).astype(object)
    ts.index = ["Buys", "Win rate", "Avg hold (days)", "Held <=7 days", "Exits via 52wk-DD rule"]
    ts.loc["Win rate"] = [f"{v:.1%}" for v in ts.loc["Win rate"]]
    ts.loc["Avg hold (days)"] = [f"{v:.0f}" for v in ts.loc["Avg hold (days)"]]
    print("\nTrading activity:")
    print(ts.to_string())

    yr = pd.DataFrame({n: yearly_returns(eqs[n]) for n in cols}) * 100
    print("\nCalendar-year returns (%):")
    print(yr.round(1).to_string())
    yr.round(2).to_csv(_BACKTEST_DIR / "noentry_calendar_years.csv")

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    colors = {"LIVE + 52wk filter (current)": "#2c3e50", "LIVE, NO entry filter": "#c0392b",
              "SHARPE-ONLY + 52wk filter": "#95a5a6", "SHARPE-ONLY, NO entry filter": "#e59866",
              "Buy&Hold N500": "#2980b9"}
    for n in cols:
        s = eqs[n]
        ax.plot(s.index, s / s.iloc[0] * 100, label=n, color=colors[n], linewidth=1.4,
                alpha=0.8 if n == "Buy&Hold N500" else 1,
                linestyle="--" if n.startswith("SHARPE") else "-")
        dd = (s / s.cummax() - 1) * 100
        ax2.plot(dd.index, dd, color=colors[n], linewidth=0.9)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log)")
    ax.set_title("ETF momentum: removing the 52-week-high entry filter (25% off-high exit kept)")
    ax.legend()
    ax.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    out = _BACKTEST_DIR / "comparison_no_entry_filter.png"
    fig.savefig(out, dpi=140)
    print(f"\nChart -> {out}")


if __name__ == "__main__":
    main()
