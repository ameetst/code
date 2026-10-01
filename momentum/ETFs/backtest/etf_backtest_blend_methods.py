"""
ETF Momentum Backtest -- blending the live (6M/3M Sharpe) and Clenow-90 rankings
=====================================================================================
EVALUATION ONLY -- backtest-only, NOT wired into any live file.

Reuses the engine, ranking functions and rules from etf_backtest_clenow90_vs_live.py
(binary EMA50 regime, weekly hold-and-replace, 5 slots, sector cap 1, 52wk screen,
exits: >25% off 52wk high / rank > 40 / 5% TSL, held ETF dropping out of the pool is
sold, stale-feed guard, same cleaned data and costs).

METHOD 1 -- capital sleeves: two independent 5-slot books (LIVE and CLENOW-90), each
  with 50% of the capital. Reported two ways: never rebalanced between sleeves, and
  rebalanced back to 50/50 on the first trading day of each calendar year.
  (Built from the two engine runs; the two books may hold the same ETF.)

METHOD 2 -- blended score, one 5-slot book, INTERSECTION pool:
  pool = ETFs eligible under BOTH rankings (all Clenow filters + 52wk screen +
         Clenow score > 0 AND present in the live pool with a real Sharpe score)
  each ETF's live rank and Clenow score are converted to a percentile rank within
  that pool; blended = 0.5 * live_pct + 0.5 * clenow_pct (lower = better);
  final RANK_INVESTABLE = rank of blended (ties -> better live rank). The fixed
  rank-40 exit uses this blended rank; an ETF that leaves the pool is sold.

Usage:  python etf_backtest_blend_methods.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_clenow90_vs_live as m   # noqa: E402

bt = m.bt
CONFIG = m.CONFIG
OUT = "blend_methods"

_CACHE = {"live": {}, "clenow": {}}


def cached(fn, key):
    def wrapper(meta, hist):
        k = hist.index[-1]
        if k not in _CACHE[key]:
            _CACHE[key][k] = fn(meta, hist)
        return _CACHE[key][k]
    return wrapper


ranking_live_c = cached(m.ranking_live, "live")
ranking_clenow_c = cached(m.ranking_clenow, "clenow")


def ranking_blend(meta, hist):
    l = ranking_live_c(meta, hist)
    c = ranking_clenow_c(meta, hist)
    df = c.copy()
    live_ok = l[l["ELIGIBLE"] & (l["SHARPE_6M"].notna() | l["SHARPE_3M"].notna())]
    df["LIVE_RANK"] = df["TICKER"].map(dict(zip(live_ok["TICKER"], live_ok["RANK_INVESTABLE"])))
    in_c = df["ELIGIBLE"].astype(bool)
    in_l = df["LIVE_RANK"].notna()
    pool = in_c & in_l
    not_live = in_c & ~in_l
    df.loc[not_live, "EXCL_REASON"] = "NOT_IN_LIVE_POOL"
    df["ELIGIBLE"] = pool
    df["RANK_INVESTABLE"] = np.nan
    if pool.sum() > 0:
        p = df[pool]
        n = len(p)
        denom = max(n - 1, 1)
        pl = (p["LIVE_RANK"].rank(ascending=True, method="first") - 1) / denom
        pc = (p["SCORE"].rank(ascending=False, method="first") - 1) / denom
        order = pd.DataFrame({"blend": 0.5 * pl + 0.5 * pc, "rl": pl}).sort_values(["blend", "rl"])
        df.loc[order.index, "RANK_INVESTABLE"] = np.arange(1, n + 1)
    fun = dict(c.attrs.get("funnel", {}))
    fun["NOT_IN_LIVE"] = int(not_live.sum())
    fun["n_pool"] = int(pool.sum())
    df.attrs["funnel"] = fun
    return df


def sleeve_curve(eq_a: pd.Series, eq_b: pd.Series, rebalance: str) -> pd.Series:
    ga = eq_a.pct_change().fillna(0.0)
    gb = eq_b.pct_change().fillna(0.0)
    va = vb = eq_a.iloc[0] / 2.0
    out, prev_year = [], eq_a.index[0].year
    for i, d in enumerate(eq_a.index):
        if i > 0:
            if rebalance == "annual" and d.year != prev_year:
                tot = va + vb
                va = vb = tot / 2.0
            va *= 1 + ga[d]
            vb *= 1 + gb[d]
        prev_year = d.year
        out.append(va + vb)
    return pd.Series(out, index=eq_a.index)


def holdings_sets(res, t0):
    h = res["equity"].loc[t0:, "holdings"].fillna("")
    return h.map(lambda x: set(x.split(",")) - {""})


def fmt_val(k, v):
    if isinstance(v, (int, np.integer)):
        return f"{v:,d}"
    if k.startswith("%") or k in ("Win rate", "Avg win", "Avg loss", "Best trade", "Worst trade"):
        return f"{v:.1%}"
    if "Rs" in k:
        return f"{v:,.0f}"
    return f"{v:.2f}"


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, audit = m.detect_and_clean(prices_raw)
    m.init_maps(meta)
    print(f"[clean] {len(audit)} bad-print fixes (same as earlier runs); EXIT_MAX_RANK={CONFIG.EXIT_MAX_RANK}")
    regime_s, ema50, _ = bt.fetch_benchmark_series(prices.index)

    print("=== LIVE ===");     res_live = m.run_engine(meta, prices, regime_s, ema50, ranking_live_c)
    print("=== CLENOW-90 ==="); res_cl = m.run_engine(meta, prices, regime_s, ema50, ranking_clenow_c)
    print("=== BLENDED (intersection, 50/50 percentile rank) ===")
    res_bl = m.run_engine(meta, prices, regime_s, ema50, ranking_blend)
    bh_full = bt.run_benchmark(regime_s)

    for tag, res in [("live", res_live), ("clenow90", res_cl), ("blended", res_bl)]:
        res["equity"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_equity.csv")
        res["trades"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_trades.csv", index=False)
        res["funnel"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_weekly_universe.csv", index=False)

    t0 = res_live["funnel"]["date"].iloc[0]
    eq = {"LIVE": res_live["equity"]["equity"].loc[t0:], "CLENOW-90": res_cl["equity"]["equity"].loc[t0:],
          "BLENDED score": res_bl["equity"]["equity"].loc[t0:]}
    eq["Sleeves 50/50 (no rebal)"] = sleeve_curve(eq["LIVE"], eq["CLENOW-90"], "none")
    eq["Sleeves 50/50 (annual rebal)"] = sleeve_curve(eq["LIVE"], eq["CLENOW-90"], "annual")
    eq["Buy&Hold N500"] = bh_full.loc[t0:]
    order = ["LIVE", "CLENOW-90", "Sleeves 50/50 (no rebal)", "Sleeves 50/50 (annual rebal)",
             "BLENDED score", "Buy&Hold N500"]
    pd.DataFrame({k: eq[k] for k in order}).to_csv(_BACKTEST_DIR / f"{OUT}_all_equity.csv")

    print(f"\nWindow: {t0.date()} -> {eq['LIVE'].index[-1].date()} (all series start same date/capital). "
          f"Regression check vs earlier run: LIVE CAGR {m.compute_metrics(eq['LIVE'])['cagr']:.2%} "
          f"(was 12.43%), CLENOW {m.compute_metrics(eq['CLENOW-90'])['cagr']:.2%} (was 12.20%)")

    def short(k):
        return {"Sleeves 50/50 (no rebal)": "Sleeves (no rebal)", "Sleeves 50/50 (annual rebal)": "Sleeves (annual)"}.get(k, k)

    mets = {k: m.compute_metrics(eq[k]) for k in order}
    print("\n### 1. HEADLINE METRICS (same window)")
    print(pd.DataFrame({short(k): m.fmt_metrics(v) for k, v in mets.items()}).to_string())

    w = slice("2018-01-01", "2020-12-31")
    print("\n### 2. 2018-2020 STRESS WINDOW")
    print(pd.DataFrame({short(k): m.fmt_metrics(m.compute_metrics(eq[k].loc[w])) for k in order}).to_string())

    cal = pd.DataFrame({short(k): m.calendar_returns(eq[k]) for k in order})
    print("\n### 3. CALENDAR-YEAR RETURNS (first year partial)")
    print((cal * 100).round(1).astype(str).add("%").to_string())

    # ---------- method 1 diagnostics ----------
    sl, sc = holdings_sets(res_live, t0), holdings_sets(res_cl, t0)
    inter = np.array([len(a & b) for a, b in zip(sl, sc)])
    union = np.array([len(a | b) for a, b in zip(sl, sc)])
    n_l = np.array([len(a) for a in sl]); n_c = np.array([len(b) for b in sc])
    r_l, r_c = eq["LIVE"].pct_change().dropna(), eq["CLENOW-90"].pct_change().dropna()
    wk_l, wk_c = eq["LIVE"].resample("W").last().pct_change().dropna(), eq["CLENOW-90"].resample("W").last().pct_change().dropna()
    print("\n### 4. METHOD 1 DIAGNOSTICS (sleeve diversification)")
    print(f"Correlation LIVE vs CLENOW-90 -- daily returns {r_l.corr(r_c):.2f}, weekly returns {wk_l.corr(wk_c):.2f}")
    print(f"Avg distinct ETFs held (of up to 10): {union.mean():.2f}; days with >=1 ETF in BOTH sleeves: "
          f"{(inter > 0).mean():.1%}; avg overlapping names: {inter.mean():.2f}")
    print(f"Avg combined cash (unfilled slots): {(1 - (n_l + n_c) / 10).mean():.1%}; "
          f"any ETF held in both sleeves carries 20% of the combined book at that time")

    # ---------- method 2 diagnostics ----------
    st = {}
    sells = {}
    for name, res in [("LIVE", res_live), ("CLENOW-90", res_cl), ("BLENDED score", res_bl)]:
        st[name], sells[name] = m.trade_stats(res)
    print("\n### 5. TRADE & PORTFOLIO STATISTICS (single-book strategies)")
    print(pd.DataFrame({n: {k: fmt_val(k, v) for k, v in s.items()} for n, s in st.items()}).to_string())

    pe = m.per_etf(sells["BLENDED score"])
    def show(df):
        d = df.copy()
        d["NetPnL"] = d["NetPnL"].map(lambda x: f"{x:,.0f}")
        d["WinRate"] = d["WinRate"].map(lambda x: f"{x:.0%}")
        d["AvgHold"] = d["AvgHold"].map(lambda x: f"{x:.0f}d")
        return d.to_string()
    print("\n### 6. ETF-LEVEL STATS -- BLENDED")
    print("Top 6 by net P&L (Rs):\n" + show(pe.sort_values("NetPnL", ascending=False).head(6)))
    print("Bottom 4 by net P&L (Rs):\n" + show(pe.sort_values("NetPnL").head(4)))
    sec = sells["BLENDED score"].groupby("SECTOR").agg(Trades=("NET_PNL", "size"), NetPnL=("NET_PNL", "sum")) \
        .sort_values("NetPnL", ascending=False)
    sec["NetPnL"] = sec["NetPnL"].map(lambda x: f"{x:,.0f}")
    print("Sector view (top 5):\n" + sec.head(5).to_string())

    sb = holdings_sets(res_bl, t0)
    for other_name, so in [("LIVE", sl), ("CLENOW-90", sc)]:
        both = [(a, b) for a, b in zip(sb, so) if a and b]
        print(f"Blended vs {other_name}: avg names in common {np.mean([len(a & b) for a, b in both]):.2f} "
              f"(identical portfolios {np.mean([a == b for a, b in both]):.1%} of days both invested)")
    tb, tl_, tc = (set(sells[k]["TICKER"]) for k in ["BLENDED score", "LIVE", "CLENOW-90"])
    print(f"Distinct ETFs traded: BLENDED {len(tb)}, of which also traded by LIVE {len(tb & tl_)}, "
          f"by CLENOW-90 {len(tb & tc)}")

    fn = res_bl["funnel"].copy()
    fn["year"] = pd.to_datetime(fn["date"]).dt.year
    yr = fn.groupby("year")[["n_data", "n_pool", "NOT_IN_LIVE", "n_held_after"]].mean().round(1)
    yr.columns = ["ETFs w/ data", "intersection pool", "Clenow-ok but not live-ok", "held"]
    fl, fc = res_live["funnel"].copy(), res_cl["funnel"].copy()
    for f_ in (fl, fc):
        f_["year"] = pd.to_datetime(f_["date"]).dt.year
    yr["live pool"] = fl.groupby("year")["n_pool"].mean().round(1)
    yr["clenow pool"] = fc.groupby("year")["n_pool"].mean().round(1)
    print("\n### 7. BLENDED UNIVERSE (avg per weekly decision, by year)\n" + yr.to_string())
    print(f"weeks with intersection pool < 5: {(fn['n_pool'] < 5).mean():.1%}; avg {fn['n_pool'].mean():.1f}")

    # ---------- chart ----------
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    style = {"LIVE": ("#7f8c8d", 1.5, "-"), "CLENOW-90": ("#c0392b", 1.5, "-"),
             "Sleeves 50/50 (annual rebal)": ("#27ae60", 1.9, "-"), "BLENDED score": ("#8e44ad", 1.9, "-"),
             "Buy&Hold N500": ("#2980b9", 1.1, "--")}
    for k, (color, lw, ls) in style.items():
        s = eq[k]
        ax.plot(s.index, s / s.iloc[0] * 100, label=k if k != "Buy&Hold N500" else "Buy & Hold Nifty 500 (reference)",
                color=color, linewidth=lw, linestyle=ls)
        dd = s / s.cummax() - 1
        ax2.plot(dd.index, dd * 100, color=color, linewidth=1.0, linestyle=ls)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_title(f"Blending Live + Clenow-90: capital sleeves vs blended score, {t0.date()} to {eq['LIVE'].index[-1].date()}")
    ax.legend()
    ax.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / f"{OUT}_equity_curve.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / (OUT + '_equity_curve.png')}")
    for k in ["BLENDED score", "Sleeves 50/50 (annual rebal)"]:
        r = eq[k].pct_change().dropna()
        print(f"[check] {k}: residual daily equity moves > 15%: {int((r.abs() > 0.15).sum())}")


if __name__ == "__main__":
    main()
