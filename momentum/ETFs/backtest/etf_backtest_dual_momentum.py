"""
ETF Backtest -- DUAL MOMENTUM (relative + absolute) on NSE ETFs
=====================================================================================
EVALUATION ONLY. Nothing here is wired into etf_momentum_ranking.py; the live module is
imported read-only for its scoring (emr.build_ranking), exactly like the other backtests.

Part A -- UPGRADE OF THE LIVE STRATEGY (weekly hold-and-replace, 5 slots, live blend score)
  The engine is a copy of etf_backtest_rank_cutoff_fixed_vs_pct.run_strategy_rank_cutoff
  (the engine behind the 12.21% "LIVE + 52wk filter (current)" result) with three switches:
    cash_pa    : interest on idle cash. 2% = what every earlier backtest assumed.
                 5.5% = idle slots parked in a liquid fund (ASSUMED constant yield: the only
                 liquid ETF in the data, CASHIETF, starts 2025-03 at ~5.2% p.a.).
    abs_entry  : (window, ...) absolute momentum on ENTRY -- an ETF is only bought if its
                 return over `window` days beats cash over the same window.
    abs_exit   : exit a held ETF if it fails that same test at a weekly rebalance.
    regime_gate: False removes the Nifty500 > EMA50 gate on new buys (does absolute momentum
                 make the regime filter redundant?).
  A0 is the live strategy as previously backtested (regression check: must equal 12.21%).
  A1 is A0 with idle cash earning 5.5% -- the pure yield effect. The effect of absolute
  momentum is read as A2/A3/A4 versus A1, not versus A0.

Part B -- CLASSIC (GEM-style) DUAL MOMENTUM on a small curated asset-class universe
  Monthly. Relative: rank by lookback return, hold the top N equal-weight. Absolute: each
  pick must beat cash over the lookback, otherwise its slot goes to cash (5.5%).
  Universe (one ETF per asset class, chosen from what is in the data): NIFTYBEES, JUNIORBEES,
  BANKBEES, MOM100, MID150BEES, CPSEETF, HNGSNGBEES, GOLDBEES, SILVERBEES. An ETF enters the
  candidate set once it has a full lookback of history.

Costs: Rs20/leg (engine convention); a 20 bps one-way ladder is reported for the headline rows.

Usage:  python etf_backtest_dual_momentum.py
"""

import sys
import io
import contextlib
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore", category=RuntimeWarning)
_BT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BT_DIR))

import etf_backtest_current_vs_nifty500 as bt                            # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean       # noqa: E402
from etf_backtest_simple_regime_weekly import evaluate_regime_simple     # noqa: E402
from etf_backtest_rank_cutoff_fixed_vs_pct import should_exit_with_cutoff  # noqa: E402

emr = bt.emr
CONFIG = bt.CONFIG
START = bt.START_CAPITAL
FEE = bt.TRADE_COST_FIXED
CASH_ASSUMED = 0.055
_RANK_CACHE: dict = {}


def cached_ranking(meta, hist):
    key = hist.index[-1]
    if key not in _RANK_CACHE:
        with contextlib.redirect_stdout(io.StringIO()):
            _RANK_CACHE[key] = emr.build_ranking(meta, hist)
    return _RANK_CACHE[key]


def window_return(series: pd.Series, w: int):
    s = series.dropna()
    if len(s) < w + 1:
        return np.nan
    return s.iloc[-1] / s.iloc[-1 - w] - 1.0


def hurdle(cash_pa: float, w: int) -> float:
    return (1.0 + cash_pa) ** (w / 252.0) - 1.0


# ------------------------------------------------------------------ Part A engine
def run_live_variant(meta, prices, ema50, regime_s, *, cash_pa=0.02, abs_w=None, abs_hurdle_pa=None,
                     abs_entry=False, abs_exit=False, regime_gate=True, bps=0.0):
    abs_hurdle_pa = cash_pa if abs_hurdle_pa is None else abs_hurdle_pa
    all_dates = prices.index
    week_starts = set(bt.get_week_starts(all_dates))
    date_list = list(all_dates)
    cash = START
    slots, trade_log, rows = {}, [], []
    regime_label = "RISK-ON"
    cost_rate = bps / 1e4

    for idx, d in enumerate(date_list):
        cash *= (1 + cash_pa / 365.0)
        if d in week_starts and idx >= bt.MIN_HISTORY_DAYS:
            prev_day = date_list[idx - 1]
            hist = prices.loc[:prev_day]
            price_now = regime_s.loc[prev_day] if prev_day in regime_s.index else np.nan
            ema50v = ema50.loc[prev_day] if prev_day in ema50.index else np.nan
            regime_label, risk_on = evaluate_regime_simple(price_now, ema50v)
            if not regime_gate:
                risk_on = True
            ranking_df = cached_ranking(meta, hist)

            total_equity = cash + sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                                      if pd.notna(prices.loc[d, t]))
            slot_size = total_equity / CONFIG.TOP_N
            rank_cutoff = CONFIG.EXIT_MAX_RANK
            hur = hurdle(abs_hurdle_pa, abs_w) if abs_w else None

            held, sector_count = set(), {}
            for t in list(slots.keys()):
                s = slots[t]
                price_dec = hist[t].dropna().iloc[-1] if hist[t].notna().any() else s["entry_price"]
                s["peak"] = max(s["peak"], price_dec)
                exit_flag, reason = should_exit_with_cutoff(t, ranking_df, s["peak"], price_dec, rank_cutoff)
                if not exit_flag and abs_exit and abs_w:
                    wr = window_return(hist[t], abs_w)
                    if pd.notna(wr) and wr <= hur:
                        exit_flag, reason = True, f"ABS-MOM {abs_w}d ret {wr*100:.1f}% <= cash {hur*100:.1f}%"
                if exit_flag:
                    price_exec = prices.loc[d, t] if pd.notna(prices.loc[d, t]) else price_dec
                    proceeds = s["shares"] * price_exec * (1 - cost_rate) - FEE
                    cash += proceeds
                    trade_log.append({"TYPE": "SELL", "REASON": reason, "TICKER": t, "EXIT_DATE": d,
                                      "NET_PNL": proceeds - s["shares"] * s["entry_price"]})
                    del slots[t]
                else:
                    held.add(t)
                    sector_count[s["sector"]] = sector_count.get(s["sector"], 0) + 1

            if risk_on:
                open_slots = CONFIG.TOP_N - len(held)
                if open_slots > 0 and not ranking_df.empty:
                    cands = ranking_df[ranking_df["SCREEN_PASS"] & (ranking_df["RANK_INVESTABLE"] > 0)] \
                        .sort_values("RANK_INVESTABLE")
                    filled = 0
                    for _, row in cands.iterrows():
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
                        if abs_entry and abs_w:
                            wr = window_return(hist[t], abs_w)
                            if pd.isna(wr) or wr <= hur:
                                continue
                        shares = (slot_size * (1 - cost_rate) - FEE) / p_entry
                        cash -= slot_size
                        slots[t] = {"shares": shares, "entry_price": p_entry, "entry_date": d,
                                    "peak": p_entry, "sector": sector}
                        trade_log.append({"TYPE": "BUY", "REASON": "NEW BUY", "TICKER": t, "EXIT_DATE": None,
                                          "NET_PNL": None})
                        sector_count[sector] = sector_count.get(sector, 0) + 1
                        held.add(t)
                        filled += 1

        port = sum(slots[t]["shares"] * prices.loc[d, t] for t in slots if pd.notna(prices.loc[d, t]))
        rows.append({"date": d, "equity": cash + port, "n": len(slots),
                     "cash_share": cash / (cash + port) if cash + port > 0 else 1.0})
    eq = pd.DataFrame(rows).set_index("date")
    return eq, pd.DataFrame(trade_log)


# ------------------------------------------------------------------ Part B engine
GEM_UNIVERSE = ["NIFTYBEES", "JUNIORBEES", "BANKBEES", "MOM100", "MID150BEES", "CPSEETF",
                "HNGSNGBEES", "GOLDBEES", "SILVERBEES"]


def month_end_signal_idx(dates):
    return [i for i in range(len(dates) - 1) if dates[i].month != dates[i + 1].month]


def gem_schedule(prices, universe, lookbacks, topn, absolute, cash_pa):
    """exec row index -> weight vector over `universe` columns (cash is the remainder).
    Score = mean return over the given lookbacks (one lookback = classic)."""
    px = prices[universe].values
    n = len(universe)
    sched = {}
    for s_idx in month_end_signal_idx(prices.index):
        e_idx = s_idx + 1
        if e_idx < bt.MIN_HISTORY_DAYS:
            continue
        maxlb = max(lookbacks)
        if s_idx < maxlb:
            continue
        scores = np.full(n, np.nan)
        abs_ok = np.zeros(n, dtype=bool)
        for j in range(n):
            col = px[:, j]
            win = col[s_idx - maxlb: s_idx + 1]
            if np.isnan(win).any():
                continue
            rets = [col[s_idx] / col[s_idx - lb] - 1.0 for lb in lookbacks]
            scores[j] = np.mean(rets)
            abs_ok[j] = all(r > hurdle(cash_pa, lb) for r, lb in zip(rets, lookbacks)) if len(lookbacks) == 1 \
                else (np.mean([r - hurdle(cash_pa, lb) for r, lb in zip(rets, lookbacks)]) > 0)
        valid = np.where(~np.isnan(scores))[0]
        w = np.zeros(n)
        if len(valid):
            top = valid[np.argsort(-scores[valid])][:topn]
            for j in top:
                if (not absolute) or abs_ok[j]:
                    w[j] = 1.0 / topn
        sched[e_idx] = w
    return sched


def simulate_weights(prices, universe, sched, cash_pa, bps):
    rets = prices[universe].pct_change().fillna(0.0).values
    T, N = rets.shape
    hold = np.zeros(N)
    cash = START
    eq = np.empty(T)
    turn = []
    for i in range(T):
        hold *= (1.0 + rets[i])
        cash *= (1.0 + cash_pa / 365.0)
        if i in sched:
            w = sched[i]
            equity = hold.sum() + cash
            target = w * equity
            delta = target - hold
            notional = np.abs(delta).sum()
            legs = int((np.abs(delta) > 1.0).sum())
            cost = notional * bps / 1e4 + legs * FEE
            scale = (equity - cost) / equity
            hold = target * scale
            cash = (1.0 - w.sum()) * equity * scale
            turn.append(notional / equity)
        eq[i] = hold.sum() + cash
    return pd.Series(eq, index=prices.index), float(np.sum(turn))


# ------------------------------------------------------------------ reporting
def stats(eq: pd.Series) -> dict:
    m = bt.compute_metrics(eq)
    return {"CAGR": m["cagr"], "Vol": m["vol"], "MaxDD": m["max_dd"], "CAGR/vol": m["sharpe"],
            "(CAGR-5.5%)/vol": (m["cagr"] - CASH_ASSUMED) / m["vol"] if m["vol"] else 0.0}


def rebase(s, start):
    s = s[s.index >= start]
    return s / s.iloc[0] * START


def main():
    meta, raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, _ = detect_and_clean(raw)
    regime_s, ema50, _ = bt.fetch_benchmark_series(prices.index)
    bench = pd.read_csv(_BT_DIR / "benchmark_equity_clean.csv", index_col=0, parse_dates=True).iloc[:, 0]
    # one common start for every row (first monthly execution after the standard 260-row warm-up)
    common_start = pd.Timestamp("2017-10-02")
    print(f"[window] {common_start.date()} -> {prices.index[-1].date()}")

    # ---------------- Part A
    print("\nPart A: live-strategy variants (this is the slow part: first run builds the ranking cache) ...")
    A = {
        "A0 live (cash 2%)":                      dict(cash_pa=0.02),
        "A1 live + idle cash at 5.5%":            dict(cash_pa=CASH_ASSUMED),
        "A2 A1 + abs-mom ENTRY (6M)":             dict(cash_pa=CASH_ASSUMED, abs_w=126, abs_entry=True),
        "A3 A1 + abs-mom ENTRY+EXIT (6M)":        dict(cash_pa=CASH_ASSUMED, abs_w=126, abs_entry=True, abs_exit=True),
        "A4 A3, no regime gate":                  dict(cash_pa=CASH_ASSUMED, abs_w=126, abs_entry=True, abs_exit=True,
                                                       regime_gate=False),
        "A5 A1 + abs-mom ENTRY+EXIT (3M)":        dict(cash_pa=CASH_ASSUMED, abs_w=63, abs_entry=True, abs_exit=True),
        "A6 A1 + abs-mom ENTRY+EXIT (12M)":       dict(cash_pa=CASH_ASSUMED, abs_w=252, abs_entry=True, abs_exit=True),
        "A7 A3 with hurdle 0% (time-series mom)": dict(cash_pa=CASH_ASSUMED, abs_w=126, abs_entry=True,
                                                       abs_exit=True, abs_hurdle_pa=0.0),
    }
    curves, trades = {}, {}
    for name, kw in A.items():
        eq, tl = run_live_variant(meta, prices, ema50, regime_s, **kw)
        curves[name] = rebase(eq["equity"], common_start)
        trades[name] = (tl, eq)
        print(f"  done {name}")

    # regression check against the earlier published run (full period from 2016-09)
    full0 = bt.compute_metrics(run_live_variant(meta, prices, ema50, regime_s, cash_pa=0.02)[0]["equity"])
    print(f"\n[regression] A0 full period 2016-09 -> 2026-09: CAGR {full0['cagr']*100:.2f}% "
          f"MaxDD {full0['max_dd']*100:.1f}%  (earlier run: 12.21% / -21.7%)")

    # ---------------- Part B
    print("\nPart B: classic dual momentum (monthly, curated universe) ...")
    uni = [t for t in GEM_UNIVERSE if t in prices.columns]
    print("  universe:", uni)
    B_defs = {
        "B1 GEM top1, 12M, abs":            dict(lookbacks=[252], topn=1, absolute=True),
        "B2 top3, 12M, abs":                dict(lookbacks=[252], topn=3, absolute=True),
        "B3 top3, 12M, relative only":      dict(lookbacks=[252], topn=3, absolute=False),
        "B4 top3, 6M, abs":                 dict(lookbacks=[126], topn=3, absolute=True),
        "B5 top3, avg(3/6/12M), abs":       dict(lookbacks=[63, 126, 252], topn=3, absolute=True),
        "B6 top5, 12M, abs":                dict(lookbacks=[252], topn=5, absolute=True),
    }
    turn_b = {}
    for name, kw in B_defs.items():
        sched = gem_schedule(prices, uni, kw["lookbacks"], kw["topn"], kw["absolute"], CASH_ASSUMED)
        eq, t = simulate_weights(prices, uni, sched, CASH_ASSUMED, bps=0)
        curves[name] = rebase(eq, common_start)
        yrs = (eq.index[-1] - common_start).days / 365.25
        turn_b[name] = t / yrs
        eq20, _ = simulate_weights(prices, uni, sched, CASH_ASSUMED, bps=20)
        trades[name + "|20bps"] = rebase(eq20, common_start)
    # equal-weight buy&hold-ish references
    ew_sched = {i: np.full(len(uni), 1.0 / len(uni)) for i in gem_schedule(prices, uni, [252], 1, False, 0.02)}
    eq_ew, _ = simulate_weights(prices, uni, ew_sched, CASH_ASSUMED, 0)
    curves["EW all curated ETFs (monthly)"] = rebase(eq_ew, common_start)

    curves["Nifty 500 buy&hold"] = rebase(bench, common_start)

    # ---------------- tables
    rows = []
    for name, eq in curves.items():
        d = stats(eq)
        if name.startswith("A"):
            tl, full_eq = trades[name]
            fe = full_eq[full_eq.index >= common_start]
            d["Buys"] = int((tl["TYPE"] == "BUY").sum())
            d["IdleCash%"] = fe["cash_share"].mean() * 100
        if name in turn_b:
            d["AnnTurnover"] = turn_b[name]
        rows.append(pd.Series(d, name=name))
    tbl = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    print("\n" + "=" * 110)
    print("HEADLINE (Rs20/leg costs; idle cash 5.5% except A0; window 2017-10-02 -> 2026-09-10)")
    print("=" * 110)
    print(tbl.to_string(float_format=lambda x: f"{x:,.3f}"))
    tbl.to_csv(_BT_DIR / "dual_momentum_headline.csv")

    print("\n" + "=" * 110)
    print("COST CHECK: Part B at 20 bps one-way (CAGR / MaxDD)")
    print("=" * 110)
    for name in B_defs:
        s = stats(trades[name + "|20bps"])
        print(f"  {name:<32} {s['CAGR']*100:5.1f}% / {s['MaxDD']*100:6.1f}%")

    print("\nCost check: Part A, A1 vs A3 at 20 bps one-way")
    for nm in ["A1 live + idle cash at 5.5%", "A3 A1 + abs-mom ENTRY+EXIT (6M)"]:
        kw = A[nm]
        eq, _ = run_live_variant(meta, prices, ema50, regime_s, bps=20, **kw)
        s = stats(rebase(eq["equity"], common_start))
        print(f"  {nm:<36} {s['CAGR']*100:5.1f}% / {s['MaxDD']*100:6.1f}%")

    print("\n" + "=" * 110)
    print("CALENDAR-YEAR RETURNS (%)")
    print("=" * 110)
    keep = ["A0 live (cash 2%)", "A1 live + idle cash at 5.5%", "A3 A1 + abs-mom ENTRY+EXIT (6M)",
            "A4 A3, no regime gate", "B1 GEM top1, 12M, abs", "B2 top3, 12M, abs", "Nifty 500 buy&hold"]
    yr = pd.DataFrame({k: curves[k].resample("YE").last().pct_change() * 100 for k in keep})
    for k in keep:
        y0 = curves[k].index[0].year
        yr.loc[pd.Timestamp(f"{y0}-12-31"), k] = (curves[k][curves[k].index.year == y0].iloc[-1] / curves[k].iloc[0] - 1) * 100
    yr.columns = [c[:16] for c in yr.columns]
    print(yr.round(1).to_string())

    fig, ax = plt.subplots(figsize=(12, 6.5))
    for k in ["A0 live (cash 2%)", "A1 live + idle cash at 5.5%", "A3 A1 + abs-mom ENTRY+EXIT (6M)",
              "B2 top3, 12M, abs", "B1 GEM top1, 12M, abs", "Nifty 500 buy&hold"]:
        ax.plot(curves[k].index, curves[k].values, label=k, lw=1.5)
    ax.set_yscale("log"); ax.grid(alpha=0.3); ax.legend(fontsize=8)
    ax.set_title("Dual momentum on NSE ETFs: live-strategy upgrade (A) and classic GEM-style (B)")
    fig.tight_layout()
    fig.savefig(_BT_DIR / "dual_momentum_equity_curve.png", dpi=110)
    pd.DataFrame(curves).to_csv(_BT_DIR / "dual_momentum_equity.csv")
    print(f"\nChart -> {_BT_DIR / 'dual_momentum_equity_curve.png'}")


if __name__ == "__main__":
    main()
