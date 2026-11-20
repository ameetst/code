"""
ETF Momentum Backtest -- PLAIN CLENOW (100% Clenow-90) vs live blend (50/50) vs Sharpe-only (0%)
=====================================================================================
EVALUATION ONLY -- backtest-only, no live file is modified.

Same machinery as etf_backtest_live_score_vs_sharpe_only.py (live emr.build_ranking, live
emr.should_exit, live allocation rules, same cleaned data/costs/regime/start date). The only
thing varied is the blend weight inside this process:
    SHARPE-ONLY   wS=0.5, wC=0.0   (cached from the previous run)
    BLEND (live)  wS=0.5, wC=0.5   (cached from the previous run)
    PLAIN CLENOW  wS=0.0, wC=1.0   (new here: ranking = Z(Clenow-90) only)
each with and without the stale-feed guard.

Note on plain Clenow through the live function: an ETF whose Clenow term is blank (a >15% one-day
move inside its 90-day window, or < 90 closes) gets blend = 0*Sharpe-term = 0 (neutral), not
"excluded", and there is no 100-day-MA filter or positive-score requirement -- that is what
BLEND_W_SHARPE=0 does in the live code. The earlier "Clenow-90" run (clenow90_vs_live_*.csv) used the
Clenow.py rules instead (above 100-day MA, score > 0, gap/stale excluded); it is shown for reference.

Usage:  python etf_backtest_plain_clenow.py
"""

import sys
import os
import hashlib
import pickle
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_live_score_vs_sharpe_only as L   # noqa: E402

m, bt, emr, CONFIG = L.m, L.bt, L.emr, L.CONFIG
OUT = "plain_clenow"


def signature():
    return hashlib.md5(open(emr.__file__, "rb").read() + open(emr._config_path(), "rb").read()
                       + str(os.path.getmtime(bt.HISTORY_FILE)).encode()).hexdigest()[:8]


def get_cache(meta, prices, wS_live):
    sig = signature()
    old_path = _BACKTEST_DIR / f"rankcache_live_score_vs_sharpe_only_{sig}.pkl"
    if not old_path.exists():
        raise SystemExit(f"previous ranking cache not found ({old_path.name}); rerun the live-score script first")
    cache = pickle.load(open(old_path, "rb"))
    new_path = _BACKTEST_DIR / f"rankcache_{OUT}_{sig}.pkl"
    if new_path.exists():
        print(f"[cache] loading plain-Clenow rankings from {new_path.name}")
        cache.update(pickle.load(open(new_path, "rb")))
        return cache
    days = L.decision_days(prices)
    tasks = [(d, 0.0, 1.0, g) for g in (False, True) for d in days]
    workers = max(1, min((os.cpu_count() or 2) - 1, 8))
    print(f"[rank] computing {len(tasks)} plain-Clenow weekly rankings on {workers} worker(s) ...")
    new = {}
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=L._init_worker, initargs=(prices, meta)) as ex:
            for task, res in ex.map(L._rank_task, tasks, chunksize=8):
                new[task] = res
    except Exception as e:
        print(f"[rank] parallel run failed ({e!r}); falling back to sequential")
        new = {t: L.compute_ranking(meta, prices.loc[:t[0]], *t[1:]) for t in tasks}
    pickle.dump(new, open(new_path, "wb"))
    print(f"[cache] saved {new_path.name}")
    cache.update(new)
    return cache


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, audit = m.detect_and_clean(prices_raw)
    m.init_maps(meta)
    wS, wC = CONFIG.BLEND_W_SHARPE, CONFIG.BLEND_W_CLENOW
    print(f"[config] live blend weights Sharpe/Clenow = {wS}/{wC}; EXIT_MAX_RANK={CONFIG.EXIT_MAX_RANK}; "
          f"TOP_N={CONFIG.TOP_N}; SECTOR_CAP={CONFIG.SECTOR_CAP}; {len(audit)} bad-print fixes (same cleaning)")
    regime_s, ema50, _ = bt.fetch_benchmark_series(prices.index)
    cache = get_cache(meta, prices, wS)

    variants = {"SHARPE-ONLY": (wS, 0.0, False), "BLEND 50/50 (live)": (wS, wC, False),
                "PLAIN CLENOW": (0.0, 1.0, False),
                "SHARPE-ONLY +guard": (wS, 0.0, True), "BLEND 50/50 +guard": (wS, wC, True),
                "PLAIN CLENOW +guard": (0.0, 1.0, True)}
    res = {}
    for name, (a, b_, g) in variants.items():
        print(f"=== running {name} ===")
        res[name] = L.run_live_engine(meta, prices, regime_s, ema50, lambda d, a=a, b_=b_, g=g: cache[(d, a, b_, g)])
        tag = name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("+", "").replace("/", "")
        res[name]["equity"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_equity.csv")
        res[name]["trades"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_trades.csv", index=False)
    bh_full = bt.run_benchmark(regime_s)

    names = list(variants)
    t0 = res[names[0]]["funnel"]["date"].iloc[0]
    eq = {n: res[n]["equity"]["equity"].loc[t0:] for n in names}
    # regression check against the previous run's saved curves
    prev = pd.read_csv(_BACKTEST_DIR / "live_score_vs_sharpe_only_all_equity.csv", index_col=0, parse_dates=True)
    for n, col in [("SHARPE-ONLY", "SHARPE-ONLY"), ("BLEND 50/50 (live)", "BLEND (live)"),
                   ("SHARPE-ONLY +guard", "SHARPE-ONLY +guard"), ("BLEND 50/50 +guard", "BLEND (live) +guard")]:
        ok = np.isclose(eq[n].iloc[-1], prev[col].iloc[-1])
        print(f"[regression] {n}: final equity {eq[n].iloc[-1]:,.0f} vs previous run {prev[col].iloc[-1]:,.0f} -> {'match' if ok else 'MISMATCH'}")
    clenow_py = pd.read_csv(_BACKTEST_DIR / "clenow90_vs_live_clenow90_equity.csv", index_col=0, parse_dates=True)["equity"].loc[t0:]
    eq["Clenow.py rules (earlier)"] = clenow_py
    eq["Buy&Hold N500"] = bh_full.loc[t0:]
    order = ["SHARPE-ONLY", "BLEND 50/50 (live)", "PLAIN CLENOW", "SHARPE-ONLY +guard", "BLEND 50/50 +guard",
             "PLAIN CLENOW +guard", "Clenow.py rules (earlier)", "Buy&Hold N500"]
    pd.DataFrame({k: eq[k] for k in order}).to_csv(_BACKTEST_DIR / f"{OUT}_all_equity.csv")
    print(f"\nWindow: {t0.date()} -> {eq[names[0]].index[-1].date()}; same start date and capital for every series.")

    print("\n### 1. HEADLINE METRICS (same window)")
    print(pd.DataFrame({k: m.fmt_metrics(m.compute_metrics(eq[k])) for k in order}).to_string())
    w = slice("2018-01-01", "2020-12-31")
    print("\n### 2. 2018-2020 STRESS WINDOW")
    print(pd.DataFrame({k: m.fmt_metrics(m.compute_metrics(eq[k].loc[w])) for k in order}).to_string())
    cal = pd.DataFrame({k: m.calendar_returns(eq[k]) for k in order})
    print("\n### 3. CALENDAR-YEAR RETURNS (first year partial)")
    print((cal * 100).round(1).astype(str).add("%").to_string())

    st, sells = {}, {}
    for n in names:
        st[n], sells[n] = m.trade_stats(res[n])
    print("\n### 4. TRADE & PORTFOLIO STATISTICS")
    print(pd.DataFrame({n: {k: L.fv(k, v) for k, v in s.items()} for n, s in st.items()}).to_string())

    # ---- plain-Clenow specifics ----
    def blank_buys(name, key):
        t = res[name]["trades"]
        b = t[t["TYPE"] == "BUY"]
        cnt = 0
        for _, r in b.iterrows():
            prev_day = prices.index[prices.index.get_loc(r["ENTRY_DATE"]) - 1]
            rdf = cache[(prev_day,) + key][0]
            v = rdf.loc[rdf["TICKER"] == r["TICKER"], "CLENOW_90D"]
            cnt += int(len(v) > 0 and pd.isna(v.iloc[0]))
        return cnt, len(b)
    nb, tb = blank_buys("PLAIN CLENOW", (0.0, 1.0, False))
    print(f"\n### 5. PLAIN-CLENOW DETAILS\nBuys of an ETF whose Clenow term was blank (ranked as neutral, score 0): {nb} of {tb}")
    pe = m.per_etf(sells["PLAIN CLENOW"])
    def show(df):
        d = df.copy()
        d["NetPnL"] = d["NetPnL"].map(lambda x: f"{x:,.0f}")
        d["WinRate"] = d["WinRate"].map(lambda x: f"{x:.0%}")
        d["AvgHold"] = d["AvgHold"].map(lambda x: f"{x:.0f}d")
        return d.to_string()
    print("Top 6 ETFs by net P&L (Rs):\n" + show(pe.sort_values("NetPnL", ascending=False).head(6)))
    print("Bottom 4 ETFs by net P&L (Rs):\n" + show(pe.sort_values("NetPnL").head(4)))
    sec = sells["PLAIN CLENOW"].groupby("SECTOR").agg(Trades=("NET_PNL", "size"), NetPnL=("NET_PNL", "sum")) \
        .sort_values("NetPnL", ascending=False)
    sec["NetPnL"] = sec["NetPnL"].map(lambda x: f"{x:,.0f}")
    print("Sector view (top 5):\n" + sec.head(5).to_string())

    days = L.decision_days(prices)
    def compare(keyA, keyB):
        rows = []
        for d in days:
            a_df, b_df = cache[(d,) + keyA][0], cache[(d,) + keyB][0]
            pa, pb = L.fresh_picks(a_df), L.fresh_picks(b_df)
            j = pd.merge(a_df[["TICKER", "RANK_INVESTABLE", "SHARPE_6M", "SHARPE_3M"]],
                         b_df[["TICKER", "RANK_INVESTABLE"]], on="TICKER", suffixes=("_a", "_b"))
            j = j[j["SHARPE_6M"].notna() | j["SHARPE_3M"].notna()].dropna(subset=["RANK_INVESTABLE_a", "RANK_INVESTABLE_b"])
            rows.append((len(set(pa) & set(pb)), pa[:1] == pb[:1],
                         j["RANK_INVESTABLE_a"].corr(j["RANK_INVESTABLE_b"], method="spearman") if len(j) > 3 else np.nan))
        r = pd.DataFrame(rows, columns=["common", "same1", "corr"])
        return r["common"].mean(), (r["common"] == 5).mean(), r["same1"].mean(), r["corr"].mean()
    print("\n### 6. HOW DIFFERENT IS THE PLAIN-CLENOW RANKING? (weekly, fresh top-5 with sector cap; no-guard)")
    for lab, kb in [("Sharpe-only", (wS, 0.0, False)), ("live 50/50 blend", (wS, wC, False))]:
        c, same5, s1, rc = compare((0.0, 1.0, False), kb)
        print(f"vs {lab:<17}: {c:.2f} of 5 picks in common; identical top-5 {same5:.1%} of weeks; same #1 {s1:.1%}; rank corr {rc:.2f}")
    ha = res["PLAIN CLENOW"]["equity"].loc[t0:, "holdings"].fillna("").map(lambda x: set(x.split(",")) - {""})
    for lab, other in [("Sharpe-only", "SHARPE-ONLY"), ("live blend", "BLEND 50/50 (live)")]:
        hb = res[other]["equity"].loc[t0:, "holdings"].fillna("").map(lambda x: set(x.split(",")) - {""})
        both = [(x, y) for x, y in zip(ha, hb) if x and y]
        print(f"Actual portfolios vs {lab}: avg {np.mean([len(x & y) for x, y in both]):.2f} names in common; identical on "
              f"{np.mean([x == y for x, y in both]):.1%} of days both invested")

    # ---------- chart ----------
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    style = {"SHARPE-ONLY": ("#7f8c8d", 1.6, "-", "Sharpe-only (0% Clenow)"),
             "BLEND 50/50 (live)": ("#8e44ad", 1.6, "-", "Live blend (50% Clenow)"),
             "PLAIN CLENOW": ("#c0392b", 1.6, "-", "Plain Clenow (100%)"),
             "Buy&Hold N500": ("#2980b9", 1.1, "--", "Buy & Hold Nifty 500 (reference)")}
    for k, (color, lw, ls, lab) in style.items():
        s = eq[k]
        ax.plot(s.index, s / s.iloc[0] * 100, color=color, linewidth=lw, linestyle=ls, label=lab)
        dd = s / s.cummax() - 1
        ax2.plot(dd.index, dd * 100, color=color, linewidth=1.0, linestyle=ls)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_title(f"Plain Clenow vs live blend vs Sharpe-only (live pipeline, no stale guard), "
                 f"{t0.date()} to {eq[names[0]].index[-1].date()}")
    ax.legend()
    ax.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / f"{OUT}_equity_curve.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / (OUT + '_equity_curve.png')}")
    for n in names:
        r = eq[n].pct_change().dropna()
        print(f"[check] {n}: residual daily equity moves > 15%: {int((r.abs() > 0.15).sum())}")


if __name__ == "__main__":
    main()
