"""
ETF Momentum Backtest -- DE-DUPLICATED capital sleeves (LIVE + CLENOW-90, 50/50)
=====================================================================================
EVALUATION ONLY -- backtest-only, NOT wired into any live file.

Two 5-slot books share one ETF universe, each with 50% of the capital (Rs 5L each,
never rebalanced between sleeves). Each sleeve keeps its own rules from
etf_backtest_clenow90_vs_live.py (LIVE = 6M/3M Sharpe ranking, CLENOW = Clenow-90
ranking with its filters; binary EMA50 regime, weekly hold-and-replace, sector cap 1
PER SLEEVE, exits: >25% off 52wk high / rank > 40 / 5% TSL / dropped from pool).

DE-DUP RULE: a sleeve will not BUY an ETF the other sleeve currently holds; it
skips it and takes its next-ranked candidate. Weekly order of operations:
  1. both sleeves evaluate exits and sell (independently)
  2. buys, in a priority order: the first sleeve fills its open slots, then the second
     (which therefore sees the first sleeve's new buys and skips them).
Priority order variants (matters only when both want the same new ETF that week):
  alt          -- alternates each weekly decision (symmetric; headline variant)
  live_first   -- LIVE always fills first
  clenow_first -- CLENOW always fills first

Reference: the same two sleeves run independently (no de-dup, may hold the same ETF),
built from the two standalone engine runs.

Usage:  python etf_backtest_dedup_sleeves.py
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

import etf_backtest_blend_methods as b   # noqa: E402

m = b.m
bt = b.bt
CONFIG = b.CONFIG
OUT = "dedup_sleeves"
SLEEVES = ["LIVE", "CLENOW"]
FNS = {"LIVE": b.ranking_live_c, "CLENOW": b.ranking_clenow_c}


def priority_order(priority, n_dec):
    if priority == "live_first":
        return ["LIVE", "CLENOW"]
    if priority == "clenow_first":
        return ["CLENOW", "LIVE"]
    return ["LIVE", "CLENOW"] if n_dec % 2 == 0 else ["CLENOW", "LIVE"]


def run_joint(meta, prices, regime_s, ema50, priority):
    rank_cutoff = CONFIG.EXIT_MAX_RANK
    all_dates = prices.index
    week_starts = set(bt.get_week_starts(all_dates))
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}

    sv = {n: {"cash": bt.START_CAPITAL / 2.0, "slots": {}} for n in SLEEVES}
    trades, skips, funnel_rows, equity_rows = [], [], [], []
    regime_label, n_dec = "RISK-ON", 0

    def pos_val(slots, d):
        return sum(s["shares"] * prices.loc[d, t] for t, s in slots.items()
                   if t in prices.columns and pd.notna(prices.loc[d, t]))

    for d in all_dates:
        idx = date_index[d]
        for n in SLEEVES:
            sv[n]["cash"] *= (1 + bt.CASH_INTEREST_PA / 365.0)

        if d in week_starts and idx >= bt.MIN_HISTORY_DAYS:
            n_dec += 1
            prev_day = date_list[idx - 1] if idx > 0 else d
            hist = prices.loc[:prev_day]
            price_now = regime_s.loc[prev_day] if prev_day in regime_s.index else np.nan
            ema50v = ema50.loc[prev_day] if prev_day in ema50.index else np.nan
            regime_label, risk_on = m.evaluate_regime_simple(price_now, ema50v)
            frow = {"date": d, "regime": regime_label}
            info = {}

            # ---- phase 1: each sleeve ranks and processes its own exits ----
            for n in SLEEVES:
                s = sv[n]
                with contextlib.redirect_stdout(io.StringIO()):
                    rdf = FNS[n](meta, hist)
                excl_map = dict(zip(rdf["TICKER"], rdf["EXCL_REASON"]))
                excl_map.update(rdf.attrs.get("excluded", {}))
                elig = set(rdf.loc[rdf["ELIGIBLE"], "TICKER"])
                s["slot_size"] = (s["cash"] + pos_val(s["slots"], d)) / CONFIG.TOP_N
                info[n] = (rdf, elig)
                frow[f"pool_{n}"] = len(elig)
                for t in list(s["slots"].keys()):
                    sl = s["slots"][t]
                    price_dec = (hist[t].dropna().iloc[-1] if t in hist.columns and hist[t].notna().any()
                                 else sl["entry_price"])
                    sl["peak"] = max(sl["peak"], price_dec)
                    exit_flag, reason = m.should_exit_with_cutoff(t, rdf, sl["peak"], price_dec, rank_cutoff)
                    if exit_flag and reason.startswith("Ticker no longer"):
                        reason = "EXCLUDED: " + excl_map.get(t, "not in ranking")
                    if not exit_flag and t not in elig:
                        exit_flag, reason = True, "EXCLUDED: " + (excl_map.get(t) or "not in ranking pool")
                    if exit_flag:
                        px = prices.loc[d, t] if t in prices.columns and pd.notna(prices.loc[d, t]) else price_dec
                        proceeds = sl["shares"] * px - bt.TRADE_COST_FIXED
                        pnl = proceeds - sl["shares"] * sl["entry_price"]
                        s["cash"] += proceeds
                        trades.append({"TYPE": "SELL", "SLEEVE": n, "REASON": reason, "TICKER": t,
                                       "NAME": sl["etf_name"], "SECTOR": sl["sector"],
                                       "ENTRY_DATE": sl["entry_date"], "EXIT_DATE": d,
                                       "ENTRY_PRICE": sl["entry_price"], "EXIT_PRICE": px,
                                       "SHARES": sl["shares"], "NET_PNL": pnl, "REGIME": regime_label})
                        del s["slots"][t]

            # ---- phase 2: buys in priority order, skipping ETFs the other sleeve holds ----
            for n in priority_order(priority, n_dec):
                s = sv[n]
                other = sv["CLENOW" if n == "LIVE" else "LIVE"]
                rdf, _ = info[n]
                sector_count = {}
                for sl in s["slots"].values():
                    sector_count[sl["sector"]] = sector_count.get(sl["sector"], 0) + 1
                if risk_on:
                    open_slots = CONFIG.TOP_N - len(s["slots"])
                    if open_slots > 0 and not rdf.empty:
                        cand = rdf[rdf["ELIGIBLE"] & (rdf["RANK_INVESTABLE"] > 0)].sort_values("RANK_INVESTABLE")
                        filled = skipped = 0
                        for _, row in cand.iterrows():
                            if filled >= open_slots:
                                break
                            t = row["TICKER"]
                            if t in s["slots"]:
                                continue
                            sector = row.get("SECTOR", "OTHER")
                            if sector_count.get(sector, 0) >= CONFIG.SECTOR_CAP:
                                continue
                            p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                            if pd.isna(p_entry) or p_entry <= 0:
                                continue
                            if t in other["slots"]:
                                skips.append({"date": d, "sleeve": n, "ticker": t,
                                              "rank": int(row["RANK_INVESTABLE"])})
                                skipped += 1
                                continue
                            shares = (s["slot_size"] - bt.TRADE_COST_FIXED) / p_entry
                            s["cash"] -= s["slot_size"]
                            s["slots"][t] = {"shares": shares, "entry_price": p_entry, "entry_date": d,
                                             "peak": p_entry, "sector": sector, "etf_name": row.get("ETF_NAME", t)}
                            trades.append({"TYPE": "BUY", "SLEEVE": n, "REASON": f"NEW BUY (rank={int(row['RANK_INVESTABLE'])})",
                                           "TICKER": t, "NAME": row.get("ETF_NAME", t), "SECTOR": sector,
                                           "ENTRY_DATE": d, "EXIT_DATE": None, "ENTRY_PRICE": p_entry,
                                           "EXIT_PRICE": None, "SHARES": shares, "NET_PNL": None,
                                           "REGIME": regime_label, "DEDUP_SKIPS_BEFORE": skipped})
                            sector_count[sector] = sector_count.get(sector, 0) + 1
                            filled += 1
            for n in SLEEVES:
                frow[f"held_{n}"] = len(sv[n]["slots"])
            funnel_rows.append(frow)

        e = {n: sv[n]["cash"] + pos_val(sv[n]["slots"], d) for n in SLEEVES}
        equity_rows.append({"date": d, "equity": e["LIVE"] + e["CLENOW"], "eq_LIVE": e["LIVE"],
                            "eq_CLENOW": e["CLENOW"], "regime": regime_label,
                            "n_holdings": len(sv["LIVE"]["slots"]) + len(sv["CLENOW"]["slots"]),
                            "hold_LIVE": ",".join(sorted(sv["LIVE"]["slots"])),
                            "hold_CLENOW": ",".join(sorted(sv["CLENOW"]["slots"]))})

    last = all_dates[-1]
    unreal = 0.0
    for n in SLEEVES:
        for t, sl in sv[n]["slots"].items():
            unreal += sl["shares"] * (float(prices.loc[last, t]) - sl["entry_price"])
    return {"equity": pd.DataFrame(equity_rows).set_index("date"), "trades": pd.DataFrame(trades),
            "skips": pd.DataFrame(skips), "funnel": pd.DataFrame(funnel_rows), "open_unreal": unreal}


def agg_stats(trades, n_hold, open_unreal, t0):
    sells = trades[trades["TYPE"] == "SELL"].copy()
    sells["HOLD_DAYS"] = (pd.to_datetime(sells["EXIT_DATE"]) - pd.to_datetime(sells["ENTRY_DATE"])).dt.days
    sells["RET"] = sells["NET_PNL"] / (sells["SHARES"] * sells["ENTRY_PRICE"])
    wins, losses = sells[sells["NET_PNL"] > 0], sells[sells["NET_PNL"] <= 0]
    nh = n_hold.loc[t0:]
    out = {
        "Buys": int((trades["TYPE"] == "BUY").sum()), "Closed trades": len(sells),
        "Win rate": len(wins) / len(sells), "Avg win": wins["RET"].mean(), "Avg loss": losses["RET"].mean(),
        "Profit factor": wins["NET_PNL"].sum() / abs(losses["NET_PNL"].sum()),
        "Avg hold (days)": sells["HOLD_DAYS"].mean(), "Closed net P&L (Rs)": sells["NET_PNL"].sum(),
        "Open unrealized (Rs)": open_unreal, "Distinct ETFs traded": sells["TICKER"].nunique(),
        "Avg positions held (of 10)": nh.mean(), "% days with <5 positions": (nh < 5).mean(),
    }
    for k, key in [("TSL", "TSL"), ("Rank > 40", "Rank "), ("Dropped from ranking", "EXCLUDED")]:
        out[f"Exit: {k}"] = int(sells["REASON"].str.contains(key).sum())
    return out


def sets(equity, col, t0):
    return equity.loc[t0:, col].fillna("").map(lambda x: set(x.split(",")) - {""})


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, audit = m.detect_and_clean(prices_raw)
    m.init_maps(meta)
    print(f"[clean] {len(audit)} bad-print fixes (same as earlier runs); EXIT_MAX_RANK={CONFIG.EXIT_MAX_RANK}")
    regime_s, ema50, _ = bt.fetch_benchmark_series(prices.index)

    print("=== independent LIVE ===");   res_live = m.run_engine(meta, prices, regime_s, ema50, b.ranking_live_c)
    print("=== independent CLENOW ==="); res_cl = m.run_engine(meta, prices, regime_s, ema50, b.ranking_clenow_c)
    bh_full = bt.run_benchmark(regime_s)
    joint = {}
    for pr in ["alt", "live_first", "clenow_first"]:
        print(f"=== de-dup sleeves, priority = {pr} ===")
        joint[pr] = run_joint(meta, prices, regime_s, ema50, pr)
        joint[pr]["equity"].to_csv(_BACKTEST_DIR / f"{OUT}_{pr}_equity.csv")
        joint[pr]["trades"].to_csv(_BACKTEST_DIR / f"{OUT}_{pr}_trades.csv", index=False)
        joint[pr]["skips"].to_csv(_BACKTEST_DIR / f"{OUT}_{pr}_dedup_skips.csv", index=False)

    t0 = res_live["funnel"]["date"].iloc[0]
    eq_l = res_live["equity"]["equity"].loc[t0:]
    eq_c = res_cl["equity"]["equity"].loc[t0:]
    eq = {"LIVE": eq_l, "CLENOW-90": eq_c,
          "Sleeves independent": b.sleeve_curve(eq_l, eq_c, "none"),
          "De-dup (alternating)": joint["alt"]["equity"]["equity"].loc[t0:],
          "De-dup (LIVE first)": joint["live_first"]["equity"]["equity"].loc[t0:],
          "De-dup (CLENOW first)": joint["clenow_first"]["equity"]["equity"].loc[t0:],
          "Buy&Hold N500": bh_full.loc[t0:]}
    order = list(eq)
    pd.DataFrame(eq).to_csv(_BACKTEST_DIR / f"{OUT}_all_equity.csv")

    # integrity: sleeves never hold the same ETF on the same day
    for pr, res in joint.items():
        a, c = sets(res["equity"], "hold_LIVE", t0), sets(res["equity"], "hold_CLENOW", t0)
        assert all(len(x & y) == 0 for x, y in zip(a, c)), f"duplicate holding found in {pr}"
    print("[integrity] no ETF is ever held by both sleeves on the same day (all 3 orderings)")
    print(f"Window: {t0.date()} -> {eq_l.index[-1].date()}. Regression check: LIVE {m.compute_metrics(eq_l)['cagr']:.2%} "
          f"(was 12.43%), CLENOW {m.compute_metrics(eq_c)['cagr']:.2%} (was 12.20%), "
          f"independent sleeves {m.compute_metrics(eq['Sleeves independent'])['cagr']:.2%} (was 12.32%)")

    print("\n### 1. HEADLINE METRICS (same window)")
    print(pd.DataFrame({k: m.fmt_metrics(m.compute_metrics(eq[k])) for k in order}).to_string())
    w = slice("2018-01-01", "2020-12-31")
    print("\n### 2. 2018-2020 STRESS WINDOW")
    print(pd.DataFrame({k: m.fmt_metrics(m.compute_metrics(eq[k].loc[w])) for k in order}).to_string())
    cal_cols = ["LIVE", "CLENOW-90", "Sleeves independent", "De-dup (alternating)", "Buy&Hold N500"]
    cal = pd.DataFrame({k: m.calendar_returns(eq[k]) for k in cal_cols})
    print("\n### 3. CALENDAR-YEAR RETURNS (first year partial)")
    print((cal * 100).round(1).astype(str).add("%").to_string())

    # ---------- diagnostics ----------
    print("\n### 4. WHAT DE-DUP CHANGED")
    ind_sets_l, ind_sets_c = sets(res_live["equity"], "holdings", t0), sets(res_cl["equity"], "holdings", t0)
    inter = np.array([len(x & y) for x, y in zip(ind_sets_l, ind_sets_c)])
    n_ind = np.array([len(x) + len(y) for x, y in zip(ind_sets_l, ind_sets_c)])
    print(f"Independent sleeves: same ETF in both on {(inter > 0).mean():.1%} of days (avg {inter.mean():.2f} names); "
          f"avg positions {n_ind.mean():.2f} of 10")
    ret_ind = eq_l.pct_change().corr(eq_c.pct_change())
    for pr, res in joint.items():
        e = res["equity"].loc[t0:]
        sk = res["skips"]
        tr = res["trades"]
        buys = tr[tr["TYPE"] == "BUY"]
        subs = (buys["DEDUP_SKIPS_BEFORE"] > 0)
        by = sk.groupby("sleeve").size().to_dict() if len(sk) else {}
        sl_l, sl_c = e["eq_LIVE"], e["eq_CLENOW"]
        print(f"[{pr}] avg positions {e['n_holdings'].mean():.2f} of 10; de-dup skips {len(sk)} (by sleeve: {by}); "
              f"buys made after >=1 skip: {subs.sum()} of {len(buys)} ({subs.mean():.1%}); "
              f"daily-return corr between sleeves {sl_l.pct_change().corr(sl_c.pct_change()):.2f} "
              f"(independent {ret_ind:.2f})")
        print(f"        LIVE sleeve standalone-vs-dedup CAGR: {m.compute_metrics(eq_l)['cagr']:.2%} -> "
              f"{m.compute_metrics(sl_l)['cagr']:.2%}; CLENOW sleeve: {m.compute_metrics(eq_c)['cagr']:.2%} -> "
              f"{m.compute_metrics(sl_c)['cagr']:.2%}")

    print("\n### 5. TRADE & PORTFOLIO STATISTICS (combined book)")
    tr_ind = pd.concat([res_live["trades"], res_cl["trades"]], ignore_index=True)
    # the two standalone engines each ran with the full Rs 10L; a 50% sleeve is half that
    tr_ind[["NET_PNL", "SHARES"]] = tr_ind[["NET_PNL", "SHARES"]] * 0.5
    nh_ind = res_live["equity"]["n_holdings"] + res_cl["equity"]["n_holdings"]
    unreal_ind = 0.5 * float(res_live["open"]["UNREALIZED_PNL"].sum() + res_cl["open"]["UNREALIZED_PNL"].sum())
    stats = {"Sleeves independent": agg_stats(tr_ind, nh_ind, unreal_ind, t0)}
    for pr, label in [("alt", "De-dup (alt)"), ("live_first", "De-dup (LIVE 1st)"), ("clenow_first", "De-dup (CLENOW 1st)")]:
        stats[label] = agg_stats(joint[pr]["trades"], joint[pr]["equity"]["n_holdings"], joint[pr]["open_unreal"], t0)
    def fv(k, v):
        if isinstance(v, (int, np.integer)):
            return f"{v:,d}"
        if k in ("Win rate", "Avg win", "Avg loss") or k.startswith("%"):
            return f"{v:.1%}"
        if "Rs" in k:
            return f"{v:,.0f}"
        return f"{v:.2f}"
    print(pd.DataFrame({n: {k: fv(k, v) for k, v in s.items()} for n, s in stats.items()}).to_string())

    # ---------- chart ----------
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
    style = {"LIVE": ("#7f8c8d", 1.4, "-"), "CLENOW-90": ("#c0392b", 1.4, "-"),
             "Sleeves independent": ("#27ae60", 1.8, "-"), "De-dup (alternating)": ("#8e44ad", 1.8, "-"),
             "Buy&Hold N500": ("#2980b9", 1.1, "--")}
    for k, (color, lw, ls) in style.items():
        s = eq[k]
        ax.plot(s.index, s / s.iloc[0] * 100, label=k if k != "Buy&Hold N500" else "Buy & Hold Nifty 500 (reference)",
                color=color, linewidth=lw, linestyle=ls)
        dd = s / s.cummax() - 1
        ax2.plot(dd.index, dd * 100, color=color, linewidth=1.0, linestyle=ls)
    ax.set_yscale("log")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_title(f"De-duplicated vs independent 50/50 sleeves (Live + Clenow-90), {t0.date()} to {eq_l.index[-1].date()}")
    ax.legend()
    ax.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / f"{OUT}_equity_curve.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / (OUT + '_equity_curve.png')}")
    for k in ["De-dup (alternating)", "De-dup (LIVE first)", "De-dup (CLENOW first)"]:
        r = eq[k].pct_change().dropna()
        print(f"[check] {k}: residual daily equity moves > 15%: {int((r.abs() > 0.15).sum())}")


if __name__ == "__main__":
    main()
