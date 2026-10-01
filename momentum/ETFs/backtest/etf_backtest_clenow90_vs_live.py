"""
ETF Momentum Backtest -- Clenow-90 scoring vs CURRENT live scoring (6M/3M Sharpe)
=====================================================================================
EVALUATION ONLY -- backtest-only, NOT wired into etf_momentum_ranking.py.

Both strategies share ONE engine and identical rules except the ranking:
  * binary regime: risk-on if Nifty 500 (^CRSLDX) > its 50-day EMA, else risk-off
    (risk-off = no new buys; holdings leave only via their own exit rule)
  * weekly hold-and-replace, 5 slots, equal weight, sector cap 1
  * screen: within 25% of 52-week high
  * exits (any one): >25% off 52wk high, investable rank > 40 (fixed), 5% TSL
  * held ETF that drops OUT of the ranking pool (any exclusion filter) is SOLD
  * stale-feed guard: an ETF with >= 10 identical consecutive closes inside its
    scoring window is excluded from ranking (window = 90 for Clenow, 126 for live)
  * same cleaned History_ETFs.xlsx data, Rs 20/leg, 2% cash yield, Rs 10L start

LIVE   : emr.build_ranking (0.5*Z(6M Sharpe) + 0.5*Z(3M Sharpe)) [+ stale guard]
CLENOW : exactly Clenow.py's maths -- log-linear regression on the last 90 closes,
         annualised slope = exp(slope*250)-1, score = annualised slope (%) * R^2;
         plus Clenow.py's two filters: no |1-day move| > 15% in the window, and
         close must not be below its 100-day simple MA. ETFs with < 90 closes,
         a score <= 0 (negative, or exactly 0 for a dead flat feed), or that fail
         the 52wk-high screen are excluded; ranks are among the survivors only.

Usage:  python etf_backtest_clenow90_vs_live.py
"""

import sys
import io
import contextlib
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_current_vs_nifty500 as bt                            # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean      # noqa: E402
from etf_backtest_simple_regime_weekly import evaluate_regime_simple     # noqa: E402
from etf_backtest_rank_cutoff_fixed_vs_pct import should_exit_with_cutoff, compute_metrics  # noqa: E402

emr = bt.emr
CONFIG = bt.CONFIG

CLENOW_WINDOW = 90
MA_STOCK_WINDOW = 100
GAP_THRESHOLD = 0.15
TRADING_DAYS_PER_YEAR = 250
STALE_RUN_CLOSES = 10
LIVE_WINDOW = 126
OUT = "clenow90_vs_live"

NAME_MAP: dict = {}
SECTOR_MAP: dict = {}


def init_maps(meta):
    for _, r in meta.iterrows():
        NAME_MAP[r["TICKER"]] = r["ETF_NAME"]
        SECTOR_MAP[r["TICKER"]] = emr.classify_sector(r["ETF_NAME"], r["TICKER"])


def max_identical_run(window_df: pd.DataFrame) -> pd.Series:
    """Longest run of consecutive identical closes (in closes) per column."""
    v = window_df.values
    same = np.zeros(v.shape, dtype=bool)
    same[1:] = (v[1:] == v[:-1]) & ~np.isnan(v[1:])
    run = np.ones(v.shape[1], dtype=int)
    best = np.ones(v.shape[1], dtype=int)
    for k in range(1, v.shape[0]):
        run = np.where(same[k], run + 1, 1)
        best = np.maximum(best, run)
    return pd.Series(best, index=window_df.columns)


def clenow_score_np(vals: np.ndarray):
    """Same maths as Clenow.py exp_regression()/clenow_score(), in numpy."""
    y = np.log(vals)
    n = len(y)
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    syy = ((y - ym) ** 2).sum()
    sxy = ((x - xm) * (y - ym)).sum()
    if syy == 0:
        return 0.0, 0.0, 0.0
    slope = sxy / sxx
    r2 = sxy ** 2 / (sxx * syy)
    ann = (np.exp(slope * TRADING_DAYS_PER_YEAR) - 1) * 100
    return ann * r2, ann, r2


# =========================================================
# RANKING FUNCTIONS (each returns df with ELIGIBLE / EXCL_REASON / RANK_INVESTABLE,
# plus attrs["funnel"] weekly universe stats)
# =========================================================
def ranking_live(meta, hist):
    tail = hist.tail(LIVE_WINDOW)
    stale = max_identical_run(tail) >= STALE_RUN_CLOSES
    stale_set = set(stale[stale].index)
    meta_f = meta[~meta["TICKER"].isin(stale_set)]
    df = emr.build_ranking(meta_f, hist)
    df["ELIGIBLE"] = df["SCREEN_PASS"].astype(bool)
    df["EXCL_REASON"] = np.where(df["ELIGIBLE"], "", "SCREEN_52WK")
    n_valid_hist = (hist.notna().sum() >= LIVE_WINDOW + 1)
    has_data = df["SHARPE_6M"].notna()
    df.attrs["excluded"] = {t: "STALE_FEED" for t in stale_set}
    df.attrs["funnel"] = {
        "n_data": int(has_data.sum() + sum(1 for t in stale_set if n_valid_hist.get(t, False))),
        "SCREEN_52WK": int((~df["SCREEN_PASS"].astype(bool) & has_data).sum()),
        "STALE_FEED": int(sum(1 for t in stale_set if n_valid_hist.get(t, False))),
        "GAP_15PCT": 0, "BELOW_MA100": 0, "SCORE<=0": 0,
        "n_pool": int((df["ELIGIBLE"] & has_data).sum()),
    }
    return df


def ranking_clenow(meta, hist):
    stale = max_identical_run(hist.tail(CLENOW_WINDOW)) >= STALE_RUN_CLOSES
    recs = []
    for t in hist.columns:
        col = hist[t]
        s = col.dropna()
        s = s[s > 0]
        n = len(s)
        rec = {"TICKER": t, "ETF_NAME": NAME_MAP.get(t, t), "SECTOR": SECTOR_MAP.get(t, "OTHER"),
               "CLOSE": np.nan, "PCT_FROM_HIGH": np.nan, "SCORE": np.nan, "ANN_SLOPE": np.nan, "R2": np.nan,
               "HAS_DATA": n >= CLENOW_WINDOW, "ELIGIBLE": False, "EXCL_REASON": ""}
        if n < CLENOW_WINDOW:
            rec["EXCL_REASON"] = "HISTORY<90"
            recs.append(rec)
            continue
        close = float(s.iloc[-1])
        high52 = float(col.tail(252).max())
        pct = (high52 - close) / high52 if high52 > 0 else np.nan
        rec["CLOSE"] = close
        rec["PCT_FROM_HIGH"] = pct * 100 if pd.notna(pct) else np.nan
        recent = s.iloc[-CLENOW_WINDOW:].values
        if pd.notna(pct) and pct > CONFIG.MAX_DRAWDOWN_FROM_HIGH:
            rec["EXCL_REASON"] = "SCREEN_52WK"
        elif bool(stale.get(t, False)):
            rec["EXCL_REASON"] = "STALE_FEED"
        elif (np.abs(np.diff(recent) / recent[:-1]) > GAP_THRESHOLD).any():
            rec["EXCL_REASON"] = "GAP_15PCT"
        elif n >= MA_STOCK_WINDOW and close < s.iloc[-MA_STOCK_WINDOW:].mean():
            rec["EXCL_REASON"] = "BELOW_MA100"
        else:
            score, ann, r2 = clenow_score_np(recent)
            rec["SCORE"], rec["ANN_SLOPE"], rec["R2"] = score, ann, r2
            if not (score > 0):
                rec["EXCL_REASON"] = "SCORE<=0"
            else:
                rec["ELIGIBLE"] = True
        recs.append(rec)
    df = pd.DataFrame(recs)
    df["RANK_INVESTABLE"] = np.nan
    pool = df["ELIGIBLE"]
    df.loc[pool, "RANK_INVESTABLE"] = df.loc[pool, "SCORE"].rank(ascending=False, method="first")
    vc = df.loc[df["HAS_DATA"], "EXCL_REASON"].value_counts()
    df.attrs["funnel"] = {
        "n_data": int(df["HAS_DATA"].sum()),
        "SCREEN_52WK": int(vc.get("SCREEN_52WK", 0)), "STALE_FEED": int(vc.get("STALE_FEED", 0)),
        "GAP_15PCT": int(vc.get("GAP_15PCT", 0)), "BELOW_MA100": int(vc.get("BELOW_MA100", 0)),
        "SCORE<=0": int(vc.get("SCORE<=0", 0)), "n_pool": int(pool.sum()),
    }
    return df


# =========================================================
# ENGINE (identical for both rankings)
# =========================================================
def run_engine(meta, prices, regime_s, ema50, ranking_fn):
    rank_cutoff = CONFIG.EXIT_MAX_RANK
    all_dates = prices.index
    week_starts = set(bt.get_week_starts(all_dates))
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}

    cash = bt.START_CAPITAL
    slots: dict[str, dict] = {}
    equity_rows, trade_log, funnel_rows = [], [], []
    regime_label = "RISK-ON"

    for d in all_dates:
        idx = date_index[d]
        cash *= (1 + bt.CASH_INTEREST_PA / 365.0)

        if d in week_starts and idx >= bt.MIN_HISTORY_DAYS:
            prev_day = date_list[idx - 1] if idx > 0 else d
            hist = prices.loc[:prev_day]
            price_now = regime_s.loc[prev_day] if prev_day in regime_s.index else np.nan
            ema50v = ema50.loc[prev_day] if prev_day in ema50.index else np.nan
            regime_label, risk_on = evaluate_regime_simple(price_now, ema50v)

            with contextlib.redirect_stdout(io.StringIO()):
                ranking_df = ranking_fn(meta, hist)
            frow = dict(ranking_df.attrs.get("funnel", {}))
            frow.update(date=d, regime=regime_label)
            excl_map = dict(zip(ranking_df["TICKER"], ranking_df["EXCL_REASON"]))
            excl_map.update(ranking_df.attrs.get("excluded", {}))
            elig = set(ranking_df.loc[ranking_df["ELIGIBLE"], "TICKER"])

            total_equity = cash + sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                                      if t in prices.columns and pd.notna(prices.loc[d, t]))
            slot_size = total_equity / CONFIG.TOP_N

            held, sector_count = set(), {}
            for t in list(slots.keys()):
                s = slots[t]
                price_dec = (hist[t].dropna().iloc[-1] if t in hist.columns and hist[t].notna().any()
                             else s["entry_price"])
                s["peak"] = max(s["peak"], price_dec)
                exit_flag, reason = should_exit_with_cutoff(t, ranking_df, s["peak"], price_dec, rank_cutoff)
                if exit_flag and reason.startswith("Ticker no longer"):
                    reason = "EXCLUDED: " + excl_map.get(t, "not in ranking")
                if not exit_flag and t not in elig:
                    exit_flag, reason = True, "EXCLUDED: " + (excl_map.get(t) or "not in ranking pool")
                if exit_flag:
                    px = prices.loc[d, t] if t in prices.columns and pd.notna(prices.loc[d, t]) else price_dec
                    proceeds = s["shares"] * px - bt.TRADE_COST_FIXED
                    pnl = proceeds - s["shares"] * s["entry_price"]
                    cash += proceeds
                    trade_log.append({"TYPE": "SELL", "REASON": reason, "TICKER": t, "NAME": s["etf_name"],
                                      "SECTOR": s["sector"], "ENTRY_DATE": s["entry_date"], "EXIT_DATE": d,
                                      "ENTRY_PRICE": s["entry_price"], "EXIT_PRICE": px, "SHARES": s["shares"],
                                      "NET_PNL": pnl, "REGIME": regime_label})
                    del slots[t]
                else:
                    held.add(t)
                    sector_count[s["sector"]] = sector_count.get(s["sector"], 0) + 1

            if risk_on:
                open_slots = CONFIG.TOP_N - len(held)
                if open_slots > 0 and not ranking_df.empty:
                    cand = ranking_df[ranking_df["ELIGIBLE"] & (ranking_df["RANK_INVESTABLE"] > 0)] \
                        .sort_values("RANK_INVESTABLE")
                    filled = 0
                    for _, row in cand.iterrows():
                        if filled >= open_slots:
                            break
                        t = row["TICKER"]
                        if t in held:
                            continue
                        sector = row.get("SECTOR", "OTHER")
                        if sector_count.get(sector, 0) >= CONFIG.SECTOR_CAP:
                            continue
                        p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                        if pd.isna(p_entry) or p_entry <= 0:
                            continue
                        shares = (slot_size - bt.TRADE_COST_FIXED) / p_entry
                        cash -= slot_size
                        slots[t] = {"shares": shares, "entry_price": p_entry, "entry_date": d, "peak": p_entry,
                                    "sector": sector, "etf_name": row.get("ETF_NAME", t)}
                        trade_log.append({"TYPE": "BUY", "REASON": f"NEW BUY (rank={int(row['RANK_INVESTABLE'])})",
                                          "TICKER": t, "NAME": row.get("ETF_NAME", t), "SECTOR": sector,
                                          "ENTRY_DATE": d, "EXIT_DATE": None, "ENTRY_PRICE": p_entry,
                                          "EXIT_PRICE": None, "SHARES": shares, "NET_PNL": None,
                                          "REGIME": regime_label})
                        sector_count[sector] = sector_count.get(sector, 0) + 1
                        held.add(t)
                        filled += 1
            frow["n_held_after"] = len(slots)
            funnel_rows.append(frow)

        port_val = sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                       if t in prices.columns and pd.notna(prices.loc[d, t]))
        equity_rows.append({"date": d, "equity": cash + port_val, "regime": regime_label,
                            "n_holdings": len(slots), "holdings": ",".join(sorted(slots))})

    last = all_dates[-1]
    open_pos = []
    for t, s in slots.items():
        px = float(prices.loc[last, t])
        open_pos.append({"TICKER": t, "SECTOR": s["sector"], "ENTRY_DATE": s["entry_date"],
                         "ENTRY_PRICE": s["entry_price"], "LAST_PRICE": px,
                         "UNREALIZED_PNL": s["shares"] * (px - s["entry_price"])})
    return {"equity": pd.DataFrame(equity_rows).set_index("date"), "trades": pd.DataFrame(trade_log),
            "funnel": pd.DataFrame(funnel_rows), "open": pd.DataFrame(open_pos)}


# =========================================================
# STATS
# =========================================================
def trade_stats(res) -> dict:
    tr = res["trades"]
    sells = tr[tr["TYPE"] == "SELL"].copy()
    sells["HOLD_DAYS"] = (pd.to_datetime(sells["EXIT_DATE"]) - pd.to_datetime(sells["ENTRY_DATE"])).dt.days
    sells["RET"] = sells["NET_PNL"] / (sells["SHARES"] * sells["ENTRY_PRICE"])
    wins, losses = sells[sells["NET_PNL"] > 0], sells[sells["NET_PNL"] <= 0]
    eq = res["equity"]
    fn = res["funnel"]
    out = {
        "Buys": int((tr["TYPE"] == "BUY").sum()),
        "Closed trades": len(sells),
        "Win rate": len(wins) / len(sells) if len(sells) else np.nan,
        "Avg win": wins["RET"].mean(), "Avg loss": losses["RET"].mean(),
        "Profit factor": wins["NET_PNL"].sum() / abs(losses["NET_PNL"].sum()) if len(losses) else np.nan,
        "Avg hold (days)": sells["HOLD_DAYS"].mean(), "Median hold (days)": sells["HOLD_DAYS"].median(),
        "Best trade": sells["RET"].max(), "Worst trade": sells["RET"].min(),
        "Closed net P&L (Rs)": sells["NET_PNL"].sum(),
        "Open at end": len(res["open"]),
        "Open unrealized (Rs)": res["open"]["UNREALIZED_PNL"].sum() if len(res["open"]) else 0.0,
        "Distinct ETFs traded": sells["TICKER"].nunique(),
        "Avg holdings": eq.loc[eq.index >= fn["date"].iloc[0], "n_holdings"].mean(),
        "% weeks risk-off": (fn["regime"] == "RISK-OFF").mean(),
        "% weeks < 5 held": (fn["n_held_after"] < CONFIG.TOP_N).mean(),
    }
    comp = {"TSL": sells["REASON"].str.contains("TSL"), "52wk-high DD": sells["REASON"].str.contains("52wk"),
            "Rank > 40": sells["REASON"].str.contains("Rank "), "Dropped from ranking": sells["REASON"].str.contains("EXCLUDED")}
    for k, m in comp.items():
        out[f"Exit: {k}"] = int(m.sum())
    return out, sells


def per_etf(sells) -> pd.DataFrame:
    g = sells.groupby("TICKER").agg(Trades=("NET_PNL", "size"), NetPnL=("NET_PNL", "sum"),
                                    WinRate=("NET_PNL", lambda x: (x > 0).mean()),
                                    AvgHold=("HOLD_DAYS", "mean"), Sector=("SECTOR", "first"))
    return g


def calendar_returns(s: pd.Series) -> pd.Series:
    ye = s.groupby(s.index.year).last()
    prev = ye.shift(1)
    prev.iloc[0] = s.iloc[0]
    return ye / prev - 1


def fmt_metrics(m):
    return {"CAGR": f"{m['cagr']:.2%}", "Total return": f"{m['final']/m['initial']-1:.1%}",
            "Max drawdown": f"{m['max_dd']:.2%}", "Volatility": f"{m['vol']:.2%}",
            "Sharpe (CAGR/vol)": f"{m['sharpe']:.2f}", "Final equity (Rs)": f"{m['final']:,.0f}"}


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, audit = detect_and_clean(prices_raw)
    init_maps(meta)
    print(f"[clean] {len(audit)} bad-print fixes applied (same as earlier runs); "
          f"EXIT_MAX_RANK={CONFIG.EXIT_MAX_RANK}, TOP_N={CONFIG.TOP_N}, SECTOR_CAP={CONFIG.SECTOR_CAP}")
    regime_s, ema50, _ = bt.fetch_benchmark_series(prices.index)

    # ---- self-test: numpy Clenow == Clenow.py's scipy maths ----
    try:
        from scipy import stats
        worst = 0.0
        cnt = 0
        for t in prices.columns[::12]:
            v = prices[t].dropna().values[-CLENOW_WINDOW:]
            if len(v) < CLENOW_WINDOW:
                continue
            sl, _, rv, _, _ = stats.linregress(np.arange(len(v)), np.log(v))
            ref = (np.exp(sl * TRADING_DAYS_PER_YEAR) - 1) * 100 * rv ** 2
            mine = clenow_score_np(v)[0]
            worst = max(worst, abs(ref - mine))
            cnt += 1
        print(f"[self-test] numpy Clenow vs scipy (Clenow.py maths) on {cnt} ETFs: max abs diff = {worst:.2e}")
    except ImportError:
        print("[self-test] scipy not available; skipped")

    print("\n=== running LIVE (6M/3M Sharpe) ===")
    res_live = run_engine(meta, prices, regime_s, ema50, ranking_live)
    print("=== running CLENOW-90 ===")
    res_cl = run_engine(meta, prices, regime_s, ema50, ranking_clenow)
    bh_full = bt.run_benchmark(regime_s)

    for tag, res in [("live", res_live), ("clenow90", res_cl)]:
        res["equity"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_equity.csv")
        res["trades"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_trades.csv", index=False)
        res["funnel"].to_csv(_BACKTEST_DIR / f"{OUT}_{tag}_weekly_universe.csv", index=False)

    t0 = res_live["funnel"]["date"].iloc[0]
    eq_live = res_live["equity"]["equity"].loc[t0:]
    eq_cl = res_cl["equity"]["equity"].loc[t0:]
    eq_bh = bh_full.loc[t0:]
    print(f"\nComparison window (first decision date -> end): {t0.date()} -> {eq_live.index[-1].date()}  "
          f"({(eq_live.index[-1]-t0).days/365.25:.1f} yrs). All three series start at the same date/capital.")

    # ---------- 1. headline metrics ----------
    ms = {"LIVE (6M/3M Sharpe)": compute_metrics(eq_live), "CLENOW-90": compute_metrics(eq_cl),
          "Buy&Hold Nifty500": compute_metrics(eq_bh)}
    tbl = pd.DataFrame({k: fmt_metrics(v) for k, v in ms.items()})
    print("\n### 1. HEADLINE METRICS (same window)\n" + tbl.to_string())

    # ---------- 2. 2018-2020 stress window ----------
    w = slice("2018-01-01", "2020-12-31")
    mw = {"LIVE (6M/3M Sharpe)": compute_metrics(eq_live.loc[w]), "CLENOW-90": compute_metrics(eq_cl.loc[w]),
          "Buy&Hold Nifty500": compute_metrics(eq_bh.loc[w])}
    print("\n### 2. 2018-2020 STRESS WINDOW\n" + pd.DataFrame({k: fmt_metrics(v) for k, v in mw.items()}).to_string())

    # ---------- 3. calendar years ----------
    cal = pd.DataFrame({"LIVE": calendar_returns(eq_live), "CLENOW-90": calendar_returns(eq_cl),
                        "Buy&Hold": calendar_returns(eq_bh)})
    print("\n### 3. CALENDAR-YEAR RETURNS (first year is partial, from the start date)\n"
          + (cal * 100).round(1).astype(str).add("%").to_string())

    # ---------- 4. trade / portfolio statistics ----------
    st_live, sells_live = trade_stats(res_live)
    st_cl, sells_cl = trade_stats(res_cl)
    def f(k, v):
        if isinstance(v, (int, np.integer)):
            return f"{v:,d}"
        if k.startswith("%") or k in ("Win rate", "Avg win", "Avg loss", "Best trade", "Worst trade"):
            return f"{v:.1%}"
        if "Rs" in k:
            return f"{v:,.0f}"
        return f"{v:.2f}"
    stat_tbl = pd.DataFrame({"LIVE": {k: f(k, v) for k, v in st_live.items()},
                             "CLENOW-90": {k: f(k, v) for k, v in st_cl.items()}})
    print("\n### 4. TRADE & PORTFOLIO STATISTICS\n" + stat_tbl.to_string())

    # ---------- 5. ETF-level statistics ----------
    for name, sells, res in [("LIVE", sells_live, res_live), ("CLENOW-90", sells_cl, res_cl)]:
        pe = per_etf(sells)
        top = pe.sort_values("NetPnL", ascending=False).head(6)
        bot = pe.sort_values("NetPnL").head(4)
        freq = pe.sort_values("Trades", ascending=False).head(5)
        def show(df):
            d = df.copy()
            d["NetPnL"] = d["NetPnL"].map(lambda x: f"{x:,.0f}")
            d["WinRate"] = d["WinRate"].map(lambda x: f"{x:.0%}")
            d["AvgHold"] = d["AvgHold"].map(lambda x: f"{x:.0f}d")
            return d.to_string()
        print(f"\n### 5. ETF-LEVEL STATS -- {name}")
        print("Top 6 ETFs by net P&L (Rs):\n" + show(top))
        print("Bottom 4 ETFs by net P&L (Rs):\n" + show(bot))
        print("Most-traded 5 ETFs:\n" + show(freq))
        sec = sells.groupby("SECTOR").agg(Trades=("NET_PNL", "size"), NetPnL=("NET_PNL", "sum")) \
            .sort_values("NetPnL", ascending=False)
        sec["NetPnL"] = sec["NetPnL"].map(lambda x: f"{x:,.0f}")
        print("Sector view (top 6 by net P&L):\n" + sec.head(6).to_string())

    both = set(sells_live["TICKER"]) & set(sells_cl["TICKER"])
    union = set(sells_live["TICKER"]) | set(sells_cl["TICKER"])
    eqj = res_live["equity"].join(res_cl["equity"], lsuffix="_l", rsuffix="_c").loc[t0:]
    sl = eqj["holdings_l"].fillna("").map(lambda x: set(x.split(",")) - {""})
    sc = eqj["holdings_c"].fillna("").map(lambda x: set(x.split(",")) - {""})
    both_inv = [(a, b) for a, b in zip(sl, sc) if a and b]
    print("\n### 6. OVERLAP BETWEEN THE TWO STRATEGIES")
    print(f"Distinct ETFs traded -- LIVE {sells_live['TICKER'].nunique()}, CLENOW-90 {sells_cl['TICKER'].nunique()}, "
          f"in both {len(both)} (Jaccard {len(both)/len(union):.0%})")
    print(f"Days both hold >=1 ETF: {len(both_inv)}; avg names held in common on those days: "
          f"{np.mean([len(a & b) for a, b in both_inv]):.2f}; identical portfolios on "
          f"{np.mean([a == b for a, b in both_inv]):.1%} of those days")

    # ---------- 7. universe / eligibility ----------
    print("\n### 7. UNIVERSE & ELIGIBILITY (avg per weekly decision, by year)")
    for name, res in [("CLENOW-90", res_cl), ("LIVE", res_live)]:
        fn = res["funnel"].copy()
        fn["year"] = pd.to_datetime(fn["date"]).dt.year
        cols = ["n_data", "SCREEN_52WK", "STALE_FEED", "GAP_15PCT", "BELOW_MA100", "SCORE<=0", "n_pool", "n_held_after"]
        yr = fn.groupby("year")[cols].mean().round(1)
        yr = yr.rename(columns={"n_data": "ETFs w/ data", "SCREEN_52WK": "fail 52wk", "STALE_FEED": "stale feed",
                                "GAP_15PCT": "gap>15%", "BELOW_MA100": "<MA100", "SCORE<=0": "score<=0",
                                "n_pool": "ranked pool", "n_held_after": "held"})
        print(f"\n{name}:\n" + yr.to_string())
        print(f"weeks with ranked pool < 5: {(fn['n_pool'] < 5).mean():.1%}; avg pool {fn['n_pool'].mean():.1f}")

    # ---------- chart ----------
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    for name, s, color, lw, ls in [("LIVE (6M/3M Sharpe)", eq_live, "#7f8c8d", 1.7, "-"),
                                   ("CLENOW-90", eq_cl, "#c0392b", 1.7, "-"),
                                   ("Buy & Hold Nifty 500 (reference)", eq_bh, "#2980b9", 1.1, "--")]:
        ax.plot(s.index, s / s.iloc[0] * 100, label=name, color=color, linewidth=lw, linestyle=ls)
        dd = s / s.cummax() - 1
        ax2.plot(dd.index, dd * 100, color=color, linewidth=1.0, linestyle=ls)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_title(f"Clenow-90 vs current live scoring -- binary regime, weekly hold-and-replace, "
                 f"{t0.date()} to {eq_live.index[-1].date()}")
    ax.legend()
    ax.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / f"{OUT}_equity_curve.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / (OUT + '_equity_curve.png')}")

    r_all = {"LIVE": eq_live, "CLENOW-90": eq_cl}
    for n, s in r_all.items():
        r = s.pct_change().dropna()
        ex = r[r.abs() > 0.15]
        print(f"[check] {n}: residual daily equity moves > 15%: {len(ex)}")


if __name__ == "__main__":
    main()
