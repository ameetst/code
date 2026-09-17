"""
ETF Momentum Backtest -- exit rank cutoff: fixed (40) vs percentile (20% of
investable universe), on top of the CURRENT live rules (binary risk-on/off
regime, weekly hold-and-replace, 6M/3M scoring).
=====================================================================================
EVALUATION ONLY -- backtest-only variant, NOT implemented in etf_momentum_ranking.py.

This is built on the same self-contained engine as
etf_backtest_simple_regime_weekly.py (NOT etf_backtest_current_vs_nifty500.py's
run_strategy/emr.build_allocation path -- that path calls the LIVE
emr.build_allocation(), which after the binary-regime rewrite no longer
understands a tiered "active_slots" cap, so re-using it with this script's
old tiered `regime` dict silently gives wrong results now. The simple-regime
engine reimplements Phase 1/2 locally against emr.should_exit's 3 rules, so
it stays correct and is what's actually validated as best-performing.)

Rank-cutoff modes:
  FIXED      : exit if RANK_INVESTABLE > 40 (current live CONFIG.EXIT_MAX_RANK)
  PERCENTILE : exit if RANK_INVESTABLE > round(0.20 * screen-pass count),
               recalculated every week as the investable pool size changes

Everything else (screen, scoring, binary regime, 52wk-DD and TSL exit
rules, sector cap, equal weight) is identical across both runs.

Usage:
  python etf_backtest_rank_cutoff_fixed_vs_pct.py
"""

import sys
import io
import contextlib
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_current_vs_nifty500 as bt              # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean  # noqa: E402
from etf_backtest_simple_regime_weekly import evaluate_regime_simple  # noqa: E402

emr = bt.emr
CONFIG = bt.CONFIG

SUBPERIOD_START = "2018-01-01"
SUBPERIOD_END = "2020-12-31"


def should_exit_with_cutoff(ticker, ranking_df, peak, current_price, rank_cutoff):
    """Same 3 rules as emr.should_exit(), but with the rank cutoff passed in
    explicitly instead of read from CONFIG.EXIT_MAX_RANK."""
    row = ranking_df[ranking_df["TICKER"] == ticker]
    if row.empty:
        return True, "Ticker no longer in ranking universe"
    row = row.iloc[0]
    reasons = []

    pct_from_high = row.get("PCT_FROM_HIGH", 0)
    if pd.notna(pct_from_high) and abs(pct_from_high) > CONFIG.EXIT_MAX_DD_FROM_HIGH * 100:
        reasons.append(f"52wk high DD {pct_from_high:.1f}% > {CONFIG.EXIT_MAX_DD_FROM_HIGH*100:.0f}%")

    inv_rank = row.get("RANK_INVESTABLE", float("inf"))
    if pd.notna(inv_rank) and inv_rank > rank_cutoff:
        reasons.append(f"Rank {int(inv_rank)} > {rank_cutoff}")

    if peak and peak > 0 and current_price and current_price > 0:
        dd_from_peak = (peak - current_price) / peak
        if dd_from_peak >= CONFIG.TSL_THRESHOLD:
            reasons.append(f"TSL {dd_from_peak*100:.1f}% >= {CONFIG.TSL_THRESHOLD*100:.0f}%")

    if reasons:
        return True, " | ".join(reasons)
    return False, ""


def run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed", percentile=0.20, ranking_fn=None):
    """mode='fixed' -> CONFIG.EXIT_MAX_RANK (40); mode='percentile' -> round(percentile * screen-pass count),
    recalculated every week. All other rules identical to run_strategy_simple_regime()."""
    if ranking_fn is None:
        ranking_fn = emr.build_ranking

    all_dates = prices.index
    week_starts = set(bt.get_week_starts(all_dates))
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}

    cash = bt.START_CAPITAL
    slots: dict[str, dict] = {}
    equity_rows = []
    trade_log = []
    regime_label = "RISK-ON"
    cutoff_log = []

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

            screen_pass_count = int(ranking_df["SCREEN_PASS"].sum()) if not ranking_df.empty else 0
            if mode == "fixed":
                rank_cutoff = CONFIG.EXIT_MAX_RANK
            else:
                rank_cutoff = max(1, round(percentile * screen_pass_count))
            cutoff_log.append({"date": d, "cutoff": rank_cutoff, "screen_pass": screen_pass_count})

            total_equity = cash + sum(
                slots[t]["shares"] * prices.loc[d, t] for t in slots
                if t in prices.columns and pd.notna(prices.loc[d, t])
            )
            slot_size = total_equity / CONFIG.TOP_N

            held_tickers = set()
            sector_count: dict[str, int] = {}
            for t in list(slots.keys()):
                s = slots[t]
                price_decision = (hist[t].dropna().iloc[-1]
                                  if t in hist.columns and hist[t].notna().any() else s["entry_price"])
                s["peak"] = max(s["peak"], price_decision)
                exit_flag, reason = should_exit_with_cutoff(t, ranking_df, s["peak"], price_decision, rank_cutoff)
                if exit_flag:
                    price_exec = (prices.loc[d, t]
                                  if t in prices.columns and pd.notna(prices.loc[d, t])
                                  else price_decision)
                    proceeds = s["shares"] * price_exec - bt.TRADE_COST_FIXED
                    pnl = proceeds - s["shares"] * s["entry_price"]
                    cash += proceeds
                    trade_log.append({
                        "TYPE": "SELL", "REASON": reason, "TICKER": t, "NAME": s["etf_name"],
                        "ENTRY_DATE": s["entry_date"], "EXIT_DATE": d,
                        "ENTRY_PRICE": round(s["entry_price"], 4), "EXIT_PRICE": round(price_exec, 4),
                        "SHARES": round(s["shares"], 4), "NET_PNL": round(pnl, 2), "REGIME": regime_label,
                    })
                    del slots[t]
                else:
                    held_tickers.add(t)
                    sector_count[s["sector"]] = sector_count.get(s["sector"], 0) + 1

            if risk_on:
                open_slots = CONFIG.TOP_N - len(held_tickers)
                if open_slots > 0 and not ranking_df.empty:
                    candidates = ranking_df[ranking_df["SCREEN_PASS"] & (ranking_df["RANK_INVESTABLE"] > 0)] \
                        .sort_values("RANK_INVESTABLE")
                    filled = 0
                    for _, row in candidates.iterrows():
                        if filled >= open_slots:
                            break
                        t = row["TICKER"]
                        if t in held_tickers:
                            continue
                        sector = row.get("SECTOR", "OTHER")
                        if sector_count.get(sector, 0) >= CONFIG.SECTOR_CAP:
                            continue
                        p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                        if pd.isna(p_entry) or p_entry <= 0:
                            continue
                        shares = (slot_size - bt.TRADE_COST_FIXED) / p_entry
                        cash -= slot_size
                        slots[t] = {"shares": shares, "entry_price": p_entry, "entry_date": d,
                                    "peak": p_entry, "sector": sector, "etf_name": row.get("ETF_NAME", t)}
                        trade_log.append({
                            "TYPE": "BUY", "REASON": f"NEW BUY (rank={int(row['RANK_INVESTABLE'])}, cutoff={rank_cutoff})",
                            "TICKER": t, "NAME": row.get("ETF_NAME", t), "ENTRY_DATE": d, "EXIT_DATE": None,
                            "ENTRY_PRICE": round(p_entry, 4), "EXIT_PRICE": None,
                            "SHARES": round(shares, 4), "NET_PNL": None, "REGIME": regime_label,
                        })
                        sector_count[sector] = sector_count.get(sector, 0) + 1
                        held_tickers.add(t)
                        filled += 1

        port_val = sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                        if t in prices.columns and pd.notna(prices.loc[d, t]))
        equity_rows.append({"date": d, "equity": cash + port_val,
                             "regime": regime_label, "n_holdings": len(slots)})

    eq = pd.DataFrame(equity_rows).set_index("date")
    return {"equity": eq, "trades": pd.DataFrame(trade_log), "cutoff_log": pd.DataFrame(cutoff_log)}


def compute_metrics(eq: pd.Series) -> dict:
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    initial, final = eq.iloc[0], eq.iloc[-1]
    cagr = (final / initial) ** (1 / years) - 1
    peak = eq.cummax()
    dd = (eq - peak) / peak
    max_dd = dd.min()
    daily_ret = eq.pct_change().dropna()
    vol = daily_ret.std() * np.sqrt(252)
    sharpe = cagr / vol if vol > 0 else 0.0
    return {"years": years, "initial": initial, "final": final, "cagr": cagr,
            "max_dd": max_dd, "max_dd_date": dd.idxmin(), "vol": vol, "sharpe": sharpe}


def print_metrics(name: str, m: dict):
    print(f"\n  {name}")
    print(f"    Total Return    : {(m['final']/m['initial']-1):>9.2%}")
    print(f"    CAGR            : {m['cagr']:>9.2%}")
    print(f"    Max Drawdown    : {m['max_dd']:>9.2%}  (trough: {m['max_dd_date'].date()})")
    print(f"    Volatility      : {m['vol']:>9.2%}")
    print(f"    Sharpe (simple) : {m['sharpe']:>9.2f}")


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    print("\n[clean] Applying the same transient/permanent bad-print fix as before ...")
    prices, audit = detect_and_clean(prices_raw)
    print(f"        {len(audit)} fixes applied ({(audit['TYPE']=='TRANSIENT_NULLED').sum()} transient, "
          f"{(audit['TYPE']!='TRANSIENT_NULLED').sum()} permanent rescale)")

    all_dates = prices.index
    regime_s, ema50, ema100 = bt.fetch_benchmark_series(all_dates)

    print(f"\n{'='*70}\n  FIXED cutoff -- EXIT_MAX_RANK = {CONFIG.EXIT_MAX_RANK}\n{'='*70}")
    res_fixed = run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed")
    res_fixed["equity"].to_csv(_BACKTEST_DIR / "rankcutoff_FIXED_equity.csv")
    res_fixed["trades"].to_csv(_BACKTEST_DIR / "rankcutoff_FIXED_trade_log.csv", index=False)
    m_fixed = compute_metrics(res_fixed["equity"]["equity"])
    print_metrics(f"FIXED (rank > {CONFIG.EXIT_MAX_RANK})", m_fixed)

    print(f"\n{'='*70}\n  PERCENTILE cutoff -- 20% of screen-pass universe\n{'='*70}")
    res_pct = run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="percentile", percentile=0.20)
    res_pct["equity"].to_csv(_BACKTEST_DIR / "rankcutoff_PCT20_equity.csv")
    res_pct["trades"].to_csv(_BACKTEST_DIR / "rankcutoff_PCT20_trade_log.csv", index=False)
    res_pct["cutoff_log"].to_csv(_BACKTEST_DIR / "rankcutoff_PCT20_cutoff_log.csv", index=False)
    m_pct = compute_metrics(res_pct["equity"]["equity"])
    print_metrics("PERCENTILE (20% of investable)", m_pct)

    cl = res_pct["cutoff_log"]
    print(f"\n  Percentile-cutoff stats over the backtest: "
          f"min={cl['cutoff'].min()}  max={cl['cutoff'].max()}  "
          f"mean={cl['cutoff'].mean():.1f}  "
          f"(screen-pass count ranged {cl['screen_pass'].min()}-{cl['screen_pass'].max()})")

    bh_eq = bt.run_benchmark(regime_s)
    m_bh = compute_metrics(bh_eq)
    print_metrics("BUY & HOLD NIFTY 500", m_bh)

    for name, series in [("FIXED", res_fixed["equity"]["equity"]), ("PERCENTILE", res_pct["equity"]["equity"])]:
        r = series.pct_change().dropna()
        extreme = r[r.abs() > 0.15]
        if len(extreme):
            print(f"\n  [!] {name}: {len(extreme)} residual daily move(s) > 15%:")
            print(extreme.to_string())

    fig, ax = plt.subplots(figsize=(13, 7))
    for name, series, color in [
        (f"FIXED (rank > {CONFIG.EXIT_MAX_RANK})", res_fixed["equity"]["equity"], "#7f8c8d"),
        ("PERCENTILE (20% of investable)", res_pct["equity"]["equity"], "#c0392b"),
        (f"Buy & Hold {bt.BENCHMARK_TICKER}", bh_eq, "#2980b9"),
    ]:
        norm = series / series.iloc[0] * 100
        ax.plot(norm.index, norm, label=name, color=color, linewidth=1.3)
    ax.set_title("ETF Momentum: Fixed vs Percentile exit-rank cutoff (binary regime, weekly)")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_rankcutoff_full.png", dpi=140)
    print(f"\nChart (full period) -> {_BACKTEST_DIR / 'comparison_rankcutoff_full.png'}")

    print(f"\n{'='*70}\n  FULL PERIOD SUMMARY: {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*70}")
    print(f"  {'Metric':<20}{'FIXED (40)':>18}{'PERCENTILE (20%)':>20}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("CAGR", "cagr", "{:.2%}"), ("Total Return", None, None),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"), ("Final Equity", "final", "INR {:,.0f}"),
    ]:
        if key is None:
            a = f"{(m_fixed['final']/m_fixed['initial']-1):.2%}"
            b = f"{(m_pct['final']/m_pct['initial']-1):.2%}"
            c = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            a = fmt.format(m_fixed[key]); b = fmt.format(m_pct[key]); c = fmt.format(m_bh[key])
        print(f"  {label:<20}{a:>18}{b:>20}{c:>18}")

    # ---- 2018-2020 sub-period ----
    fixed_w = res_fixed["equity"]["equity"].loc[SUBPERIOD_START:SUBPERIOD_END]
    pct_w = res_pct["equity"]["equity"].loc[SUBPERIOD_START:SUBPERIOD_END]
    bh_w = bh_eq.loc[SUBPERIOD_START:SUBPERIOD_END]
    m_fixed_w = compute_metrics(fixed_w)
    m_pct_w = compute_metrics(pct_w)
    m_bh_w = compute_metrics(bh_w)

    print(f"\n{'='*70}\n  2018-2020 SUB-PERIOD SUMMARY\n{'='*70}")
    print(f"  {'Metric':<20}{'FIXED (40)':>18}{'PERCENTILE (20%)':>20}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("Total Return", None, None), ("CAGR", "cagr", "{:.2%}"),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"),
    ]:
        if key is None:
            a = f"{(m_fixed_w['final']/m_fixed_w['initial']-1):.2%}"
            b = f"{(m_pct_w['final']/m_pct_w['initial']-1):.2%}"
            c = f"{(m_bh_w['final']/m_bh_w['initial']-1):.2%}"
        else:
            a = fmt.format(m_fixed_w[key]); b = fmt.format(m_pct_w[key]); c = fmt.format(m_bh_w[key])
        print(f"  {label:<20}{a:>18}{b:>20}{c:>18}")

    n_buys_f = (res_fixed["trades"]["TYPE"] == "BUY").sum() if len(res_fixed["trades"]) else 0
    n_buys_p = (res_pct["trades"]["TYPE"] == "BUY").sum() if len(res_pct["trades"]) else 0
    print(f"\n  Total BUY trades -- FIXED: {n_buys_f}   PERCENTILE: {n_buys_p}")


if __name__ == "__main__":
    main()
