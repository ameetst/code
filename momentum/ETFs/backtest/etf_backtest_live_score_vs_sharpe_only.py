"""
ETF Momentum Backtest -- EXACT LIVE scoring (Sharpe + Clenow-90 blend) vs SHARPE-ONLY
=====================================================================================
EVALUATION ONLY -- backtest-only, no live file is modified.

The ranking is produced by the LIVE module's own emr.build_ranking() (imported, not
re-implemented), so "live" means exactly what etf_momentum_ranking.py does today:
  score = BLEND_W_SHARPE * [0.5*Z(6M Sharpe) + 0.5*Z(3M Sharpe)] + BLEND_W_CLENOW * Z(Clenow-90)
  Z-scores over the screened pool; Clenow = (exp(slope*250)-1)*100*R^2 on 90 closes, NaN if any
  1-day move > 15%; a NaN term is dropped (not rescaled); LIQUID sector excluded; 5% risk-free.
  NO Clenow 100-day-MA filter, NO positive-score requirement, NO stale-feed guard.
SHARPE-ONLY = the same live function with BLEND_W_CLENOW set to 0 (the documented way to get
the Sharpe-only ranking). The weights are changed only inside this process.

Everything else follows the live rules and uses live functions where they exist:
  regime: risk-on if Nifty 500 (^CRSLDX) > its 50-day EMA, else risk-off (no new buys; never sells)
  weekly hold-and-replace, 5 slots, equal weight, sector cap 1 (emr.CONFIG)
  buy pool = SCREEN_PASS & RANK_INVESTABLE > 0 (same as emr.build_allocation)
  exits = emr.should_exit() (52wk-high DD > 25%, rank > 40, 5% TSL, ticker missing from ranking)
Same cleaned History_ETFs.xlsx, Rs 20/leg, 2% cash yield, Rs 10L start, 2017-09-11 start.

Four runs: SHARPE-ONLY, BLEND(live), each also with a stale-feed guard (ETF with >= 10
identical consecutive closes in the last 126 closes is removed from the ranking input, so a
held one is sold) -- the guard variants match the treatment in the earlier Clenow tests.

Usage:  python etf_backtest_live_score_vs_sharpe_only.py
"""

import sys
import os
import io
import hashlib
import pickle
import contextlib
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

import etf_backtest_clenow90_vs_live as m      # noqa: E402

bt = m.bt
emr = bt.emr
CONFIG = emr.CONFIG
OUT = "live_score_vs_sharpe_only"
STALE_WINDOW = 126
STALE_RUN = 10
KEEP = ["TICKER", "ETF_NAME", "SECTOR", "PCT_FROM_HIGH", "RANK_INVESTABLE", "SCREEN_PASS",
        "SHARPE_6M", "SHARPE_3M", "CLENOW_90D"]

_G: dict = {}


def compute_ranking(meta, hist, wS, wC, guard):
    """Live emr.build_ranking() with the given blend weights (changed only in this process)."""
    stale_set = set()
    if guard:
        stale = m.max_identical_run(hist.tail(STALE_WINDOW)) >= STALE_RUN
        stale_set = set(stale[stale].index)
        meta = meta[~meta["TICKER"].isin(stale_set)]
    old = (CONFIG.BLEND_W_SHARPE, CONFIG.BLEND_W_CLENOW)
    CONFIG.BLEND_W_SHARPE, CONFIG.BLEND_W_CLENOW = wS, wC
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            df = emr.build_ranking(meta, hist)
    finally:
        CONFIG.BLEND_W_SHARPE, CONFIG.BLEND_W_CLENOW = old
    valid_s = df["SHARPE_6M"].notna() | df["SHARPE_3M"].notna()
    sp = df["SCREEN_PASS"].astype(bool)
    fun = {"n_pass": int(sp.sum()), "n_scored": int((sp & valid_s).sum()),
           "n_clenow_nan": int((sp & valid_s & df["CLENOW_90D"].isna()).sum()),
           "n_stale": int(sum(1 for t in stale_set if hist[t].notna().sum() >= STALE_WINDOW + 1))}
    return df[KEEP].copy(), fun


def _init_worker(prices, meta):
    _G["prices"], _G["meta"] = prices, meta


def _rank_task(task):
    prev_day, wS, wC, guard = task
    df, fun = compute_ranking(_G["meta"], _G["prices"].loc[:prev_day], wS, wC, guard)
    return task, (df, fun)


def decision_days(prices):
    all_dates = prices.index
    ws = set(bt.get_week_starts(all_dates))
    dl = list(all_dates)
    return [dl[i - 1] for i, d in enumerate(dl) if d in ws and i >= bt.MIN_HISTORY_DAYS]


def build_cache(meta, prices, variants):
    sig = hashlib.md5(open(emr.__file__, "rb").read() + open(emr._config_path(), "rb").read()
                      + str(os.path.getmtime(bt.HISTORY_FILE)).encode()).hexdigest()[:8]
    path = _BACKTEST_DIR / f"rankcache_{OUT}_{sig}.pkl"
    if path.exists():
        print(f"[cache] loading rankings from {path.name}")
        return pickle.load(open(path, "rb"))
    days = decision_days(prices)
    tasks = [(d, wS, wC, g) for (wS, wC, g) in variants.values() for d in days]
    workers = max(1, min((os.cpu_count() or 2) - 1, 8))
    print(f"[rank] computing {len(tasks)} weekly rankings ({len(days)} decisions x {len(variants)} variants) "
          f"on {workers} worker(s) ...")
    cache = {}
    try:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(prices, meta)) as ex:
            for task, res in ex.map(_rank_task, tasks, chunksize=8):
                cache[task] = res
    except Exception as e:
        print(f"[rank] parallel run failed ({e!r}); falling back to sequential")
        cache = {}
        for task in tasks:
            prev_day, wS, wC, g = task
            cache[task] = compute_ranking(meta, prices.loc[:prev_day], wS, wC, g)
    pickle.dump(cache, open(path, "wb"))
    print(f"[cache] saved {path.name}")
    return cache


def run_live_engine(meta, prices, regime_s, ema50, rank_get):
    all_dates = prices.index
    week_starts = set(bt.get_week_starts(all_dates))
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}
    cash = bt.START_CAPITAL
    slots: dict[str, dict] = {}
    equity_rows, trades, funnel_rows = [], [], []
    regime_label = "RISK-ON"

    for d in all_dates:
        idx = date_index[d]
        cash *= (1 + bt.CASH_INTEREST_PA / 365.0)
        if d in week_starts and idx >= bt.MIN_HISTORY_DAYS:
            prev_day = date_list[idx - 1]
            hist = prices.loc[:prev_day]
            price_now = regime_s.loc[prev_day] if prev_day in regime_s.index else np.nan
            ema50v = ema50.loc[prev_day] if prev_day in ema50.index else np.nan
            regime_label, risk_on = m.evaluate_regime_simple(price_now, ema50v)
            rdf, fun = rank_get(prev_day)

            total_equity = cash + sum(s["shares"] * prices.loc[d, t] for t, s in slots.items()
                                      if t in prices.columns and pd.notna(prices.loc[d, t]))
            slot_size = total_equity / CONFIG.TOP_N

            held, sector_count = set(), {}
            for t in list(slots.keys()):
                s = slots[t]
                price_dec = (hist[t].dropna().iloc[-1] if t in hist.columns and hist[t].notna().any()
                             else s["entry_price"])
                s["peak"] = max(s["peak"], price_dec)
                exit_flag, reason = emr.should_exit(t, rdf, s["peak"], price_dec)
                if exit_flag:
                    if reason.startswith("Ticker no longer"):
                        reason = "EXCLUDED: dropped from ranking input (stale-feed guard)"
                    px = prices.loc[d, t] if t in prices.columns and pd.notna(prices.loc[d, t]) else price_dec
                    proceeds = s["shares"] * px - bt.TRADE_COST_FIXED
                    cash += proceeds
                    trades.append({"TYPE": "SELL", "REASON": reason, "TICKER": t, "NAME": s["etf_name"],
                                   "SECTOR": s["sector"], "ENTRY_DATE": s["entry_date"], "EXIT_DATE": d,
                                   "ENTRY_PRICE": s["entry_price"], "EXIT_PRICE": px, "SHARES": s["shares"],
                                   "NET_PNL": proceeds - s["shares"] * s["entry_price"], "REGIME": regime_label})
                    del slots[t]
                else:
                    held.add(t)
                    sector_count[s["sector"]] = sector_count.get(s["sector"], 0) + 1

            if risk_on:
                open_slots = CONFIG.TOP_N - len(held)
                if open_slots > 0 and not rdf.empty:
                    cand = rdf[rdf["SCREEN_PASS"].astype(bool) & (rdf["RANK_INVESTABLE"] > 0)] \
                        .sort_values("RANK_INVESTABLE")
                    filled = 0
                    for _, row in cand.iterrows():
                        if filled >= open_slots:
                            break
                        t = row["TICKER"]
                        if t in held:
                            continue
                        sector = row["SECTOR"]
                        if sector_count.get(sector, 0) >= CONFIG.SECTOR_CAP:
                            continue
                        p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                        if pd.isna(p_entry) or p_entry <= 0:
                            continue
                        shares = (slot_size - bt.TRADE_COST_FIXED) / p_entry
                        cash -= slot_size
                        slots[t] = {"shares": shares, "entry_price": p_entry, "entry_date": d, "peak": p_entry,
                                    "sector": sector, "etf_name": row["ETF_NAME"]}
                        trades.append({"TYPE": "BUY", "REASON": f"NEW BUY (rank={int(row['RANK_INVESTABLE'])})",
                                       "TICKER": t, "NAME": row["ETF_NAME"], "SECTOR": sector, "ENTRY_DATE": d,
                                       "EXIT_DATE": None, "ENTRY_PRICE": p_entry, "EXIT_PRICE": None,
                                       "SHARES": shares, "NET_PNL": None, "REGIME": regime_label})
                        sector_count[sector] = sector_count.get(sector, 0) + 1
                        held.add(t)
                        filled += 1
            funnel_rows.append({"date": d, "regime": regime_label, "n_held_after": len(slots), **fun})

        pv = sum(s["shares"] * prices.loc[d, t] for t, s in slots.items()
                 if t in prices.columns and pd.notna(prices.loc[d, t]))
        equity_rows.append({"date": d, "equity": cash + pv, "regime": regime_label, "n_holdings": len(slots),
                            "holdings": ",".join(sorted(slots))})

    last = all_dates[-1]
    open_pos = [{"TICKER": t, "SECTOR": s["sector"], "ENTRY_DATE": s["entry_date"], "ENTRY_PRICE": s["entry_price"],
                 "LAST_PRICE": float(prices.loc[last, t]),
                 "UNREALIZED_PNL": s["shares"] * (float(prices.loc[last, t]) - s["entry_price"])}
                for t, s in slots.items()]
    return {"equity": pd.DataFrame(equity_rows).set_index("date"), "trades": pd.DataFrame(trades),
            "funnel": pd.DataFrame(funnel_rows), "open": pd.DataFrame(open_pos)}


def fresh_picks(rdf, n=5):
    c = rdf[rdf["SCREEN_PASS"].astype(bool) & (rdf["RANK_INVESTABLE"] > 0)].sort_values("RANK_INVESTABLE")
    out, seen = [], set()
    for _, r in c.iterrows():
        if r["SECTOR"] in seen:
            continue
        out.append(r["TICKER"])
        seen.add(r["SECTOR"])
        if len(out) == n:
            break
    return out


def fv(k, v):
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
    wS, wC = CONFIG.BLEND_W_SHARPE, CONFIG.BLEND_W_CLENOW
    print(f"[config] LIVE blend weights Sharpe/Clenow = {wS}/{wC}; Sharpe risk-free = {CONFIG.DAILY_RF*252:.2%}; "
          f"EXIT_MAX_RANK={CONFIG.EXIT_MAX_RANK}; TOP_N={CONFIG.TOP_N}; SECTOR_CAP={CONFIG.SECTOR_CAP}; "
          f"EXCLUDE_SECTORS={CONFIG.EXCLUDE_SECTORS}; variant={CONFIG.STRATEGY_VARIANT}; "
          f"{len(audit)} bad-print fixes (same cleaning as earlier runs)")
    regime_s, ema50, _ = bt.fetch_benchmark_series(prices.index)

    variants = {"SHARPE-ONLY": (wS, 0.0, False), "BLEND (live)": (wS, wC, False),
                "SHARPE-ONLY +guard": (wS, 0.0, True), "BLEND (live) +guard": (wS, wC, True)}
    cache = build_cache(meta, prices, variants)

    res = {}
    for name, (a, b_, g) in variants.items():
        print(f"=== running {name} ===")
        res[name] = run_live_engine(meta, prices, regime_s, ema50, lambda d, a=a, b_=b_, g=g: cache[(d, a, b_, g)])
        tag = name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("+", "")
        res[name]["equity"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_equity.csv")
        res[name]["trades"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_trades.csv", index=False)
        res[name]["funnel"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_weekly_universe.csv", index=False)
    bh_full = bt.run_benchmark(regime_s)

    names = list(variants)
    t0 = res[names[0]]["funnel"]["date"].iloc[0]
    eq = {n: res[n]["equity"]["equity"].loc[t0:] for n in names}
    eq["Buy&Hold N500"] = bh_full.loc[t0:]
    order = list(eq)
    pd.DataFrame(eq).to_csv(_BACKTEST_DIR / f"{OUT}_all_equity.csv")
    print(f"\nWindow: {t0.date()} -> {eq[names[0]].index[-1].date()} "
          f"({(eq[names[0]].index[-1] - t0).days / 365.25:.1f} yrs); same start date and capital for every series.")

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
    print(pd.DataFrame({n: {k: fv(k, v) for k, v in s.items()} for n, s in st.items()}).to_string())

    for n in ["SHARPE-ONLY", "BLEND (live)"]:
        pe = m.per_etf(sells[n])
        def show(df):
            d = df.copy()
            d["NetPnL"] = d["NetPnL"].map(lambda x: f"{x:,.0f}")
            d["WinRate"] = d["WinRate"].map(lambda x: f"{x:.0%}")
            d["AvgHold"] = d["AvgHold"].map(lambda x: f"{x:.0f}d")
            return d.to_string()
        print(f"\n### 5. ETF-LEVEL STATS -- {n}")
        print("Top 6 by net P&L (Rs):\n" + show(pe.sort_values("NetPnL", ascending=False).head(6)))
        print("Bottom 4 by net P&L (Rs):\n" + show(pe.sort_values("NetPnL").head(4)))
        sec = sells[n].groupby("SECTOR").agg(Trades=("NET_PNL", "size"), NetPnL=("NET_PNL", "sum")) \
            .sort_values("NetPnL", ascending=False)
        sec["NetPnL"] = sec["NetPnL"].map(lambda x: f"{x:,.0f}")
        print("Sector view (top 5):\n" + sec.head(5).to_string())

    # ---------- how different are the two rankings? (no-guard variants) ----------
    days = decision_days(prices)
    rows = []
    for d in days:
        sh, bl = cache[(d, wS, 0.0, False)][0], cache[(d, wS, wC, False)][0]
        a, b_ = fresh_picks(sh), fresh_picks(bl)
        pool = sh[sh["SCREEN_PASS"].astype(bool) & (sh["SHARPE_6M"].notna() | sh["SHARPE_3M"].notna())]["TICKER"]
        j = pd.merge(sh[["TICKER", "RANK_INVESTABLE"]], bl[["TICKER", "RANK_INVESTABLE"]], on="TICKER", suffixes=("_s", "_b"))
        j = j[j["TICKER"].isin(pool)].dropna()
        fun = cache[(d, wS, wC, False)][1]
        rows.append({"date": d, "top5_overlap": len(set(a) & set(b_)), "same_no1": bool(a and b_ and a[0] == b_[0]),
                     "rank_corr": j["RANK_INVESTABLE_s"].corr(j["RANK_INVESTABLE_b"], method="spearman") if len(j) > 3 else np.nan,
                     "n_pool": fun["n_pass"], "n_scored": fun["n_scored"], "n_clenow_nan": fun["n_clenow_nan"]})
    dg = pd.DataFrame(rows)
    dg["year"] = pd.to_datetime(dg["date"]).dt.year
    print("\n### 6. HOW DIFFERENT ARE THE TWO RANKINGS? (weekly decisions, fresh top-5 with sector cap)")
    yr = dg.groupby("year").agg(top5_overlap=("top5_overlap", "mean"), same_no1=("same_no1", "mean"),
                                rank_corr=("rank_corr", "mean"), ETFs_scored=("n_scored", "mean"),
                                clenow_blank=("n_clenow_nan", "mean")).round(2)
    yr.columns = ["avg names in common (of 5)", "same #1 pick", "rank corr (Spearman)", "ETFs scored", "of which Clenow blank (gap)"]
    print(yr.to_string())
    print(f"All weeks: avg {dg['top5_overlap'].mean():.2f} of 5 picks in common; identical top-5 set in "
          f"{(dg['top5_overlap'] == 5).mean():.1%} of weeks; same #1 in {dg['same_no1'].mean():.1%}; "
          f"avg rank correlation {dg['rank_corr'].mean():.2f}")

    hb = res["BLEND (live)"]["equity"].loc[t0:, "holdings"].fillna("").map(lambda x: set(x.split(",")) - {""})
    hs = res["SHARPE-ONLY"]["equity"].loc[t0:, "holdings"].fillna("").map(lambda x: set(x.split(",")) - {""})
    both = [(x, y) for x, y in zip(hb, hs) if x and y]
    tb, ts = set(sells["BLEND (live)"]["TICKER"]), set(sells["SHARPE-ONLY"]["TICKER"])
    print(f"Actual portfolios: avg {np.mean([len(x & y) for x, y in both]):.2f} names in common on days both invested; "
          f"identical on {np.mean([x == y for x, y in both]):.1%} of those days; distinct ETFs traded "
          f"BLEND {len(tb)} / SHARPE-ONLY {len(ts)} / both {len(tb & ts)}")

    # ---------- chart ----------
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    style = {"SHARPE-ONLY": ("#7f8c8d", 1.7, "-"), "BLEND (live)": ("#8e44ad", 1.7, "-"),
             "Buy&Hold N500": ("#2980b9", 1.1, "--")}
    for k, (color, lw, ls) in style.items():
        s = eq[k]
        ax.plot(s.index, s / s.iloc[0] * 100, color=color, linewidth=lw, linestyle=ls,
                label={"SHARPE-ONLY": "Sharpe-only (6M/3M)", "BLEND (live)": "Live blend: Sharpe + Clenow-90 (50/50 Z)",
                       "Buy&Hold N500": "Buy & Hold Nifty 500 (reference)"}[k])
        dd = s / s.cummax() - 1
        ax2.plot(dd.index, dd * 100, color=color, linewidth=1.0, linestyle=ls)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_title(f"Exact live scoring (Sharpe+Clenow blend) vs Sharpe-only, {t0.date()} to {eq[names[0]].index[-1].date()}")
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
