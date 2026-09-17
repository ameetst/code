"""
ETF Momentum Backtest -- Simple binary regime filter (risk-on/risk-off)
=====================================================================================
EVALUATION ONLY -- backtest-only variant, NOT implemented in etf_momentum_ranking.py.

New regime rule (replaces the live tiered BULL/PARTIAL/BEAR EMA50/EMA100 filter):
    RISK-ON  : Nifty 500 (^CRSLDX) price > its 50-day EMA
    RISK-OFF : price <= its 50-day EMA
No EMA100, no PARTIAL tier, no forced regime-driven slot cuts.

Behaviour:
  - Exit rules (52wk-high DD, rank cutoff, TSL) are checked every week on
    every held position REGARDLESS of regime -- unchanged from the live
    rules.
  - RISK-ON: normal weekly hold-and-replace. Any slot vacated (by an exit
    rule firing) is refilled the same week with the next best-ranked ETF
    (subject to sector cap), same as the live engine.
  - RISK-OFF: no NEW buys are made. Existing holdings keep running and are
    only removed if they individually trip an exit rule -- a vacated slot
    during risk-off is simply left in cash and is NOT backfilled until
    risk-on resumes. (This is the interpretation of "exit ETFs only once
    they pass any of the exit rules" + "sold positions are replaced with
    the next higher one" -- replacement is the risk-on behaviour; risk-off
    is deliberately not replacing, since the point of the filter is to
    stop deploying new capital while price is below trend.)

Compares three curves on the same cleaned History_ETFs.xlsx data:
  1. LIVE tiered regime (reused from the earlier strategy_weekly_baseline_equity.csv run)
  2. NEW simple risk-on/off regime (this script)
  3. Buy & Hold Nifty 500

Reports both the full 2016-2026 period and the 2018-2020 real-drawdown
sub-period (2018 IL&FS/mid-cap crash + COVID crash), since that's exactly
the scenario this change is meant to help with.

Usage:
  python etf_backtest_simple_regime_weekly.py
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

emr = bt.emr
CONFIG = bt.CONFIG

SUBPERIOD_START = "2018-01-01"
SUBPERIOD_END = "2020-12-31"


def evaluate_regime_simple(price, ema50):
    """RISK-ON if price > 50-EMA, else RISK-OFF. No data -> default RISK-ON."""
    if pd.isna(price) or pd.isna(ema50):
        return "RISK-ON", True
    return ("RISK-ON", True) if price > ema50 else ("RISK-OFF", False)


def run_strategy_simple_regime(meta, prices, regime_s, ema50, ranking_fn=None):
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

            total_equity = cash + sum(
                slots[t]["shares"] * prices.loc[d, t] for t in slots
                if t in prices.columns and pd.notna(prices.loc[d, t])
            )
            slot_size = total_equity / CONFIG.TOP_N

            # ---- exit-rule checks on every held position (regardless of regime) ----
            held_tickers = set()
            sector_count: dict[str, int] = {}
            for t in list(slots.keys()):
                s = slots[t]
                price_decision = (hist[t].dropna().iloc[-1]
                                  if t in hist.columns and hist[t].notna().any() else s["entry_price"])
                s["peak"] = max(s["peak"], price_decision)
                exit_flag, reason = emr.should_exit(t, ranking_df, s["peak"], price_decision)
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

            # ---- RISK-ON only: refill vacated slots with the next best-ranked ETF ----
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
                            "TYPE": "BUY", "REASON": f"NEW BUY (rank={int(row['RANK_INVESTABLE'])})",
                            "TICKER": t, "NAME": row.get("ETF_NAME", t), "ENTRY_DATE": d, "EXIT_DATE": None,
                            "ENTRY_PRICE": round(p_entry, 4), "EXIT_PRICE": None,
                            "SHARES": round(shares, 4), "NET_PNL": None, "REGIME": regime_label,
                        })
                        sector_count[sector] = sector_count.get(sector, 0) + 1
                        held_tickers.add(t)
                        filled += 1
            # RISK-OFF: vacated slots stay in cash, not backfilled.

        port_val = sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                        if t in prices.columns and pd.notna(prices.loc[d, t]))
        equity_rows.append({"date": d, "equity": cash + port_val,
                             "regime": regime_label, "n_holdings": len(slots)})

    eq = pd.DataFrame(equity_rows).set_index("date")
    return {"equity": eq, "trades": pd.DataFrame(trade_log)}


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
    regime_s, ema50, ema100 = bt.fetch_benchmark_series(all_dates)  # ema100 unused here

    print(f"\n{'='*70}\n  NEW -- WEEKLY, simple risk-on/off regime (price vs 50-EMA)\n{'='*70}")
    res_simple = run_strategy_simple_regime(meta, prices, regime_s, ema50)
    res_simple["equity"].to_csv(_BACKTEST_DIR / "strategy_simple_regime_equity.csv")
    res_simple["trades"].to_csv(_BACKTEST_DIR / "strategy_simple_regime_trade_log.csv", index=False)
    m_simple = compute_metrics(res_simple["equity"]["equity"])
    print_metrics("WEEKLY -- simple risk-on/off regime", m_simple)

    r = res_simple["equity"]["equity"].pct_change().dropna()
    extreme = r[r.abs() > 0.15]
    if len(extreme):
        print(f"\n  [!] {len(extreme)} residual daily move(s) > 15%:")
        print(extreme.to_string())

    # ---- reuse the already-computed live-tiered-regime and buy&hold curves ----
    live_eq = pd.read_csv(_BACKTEST_DIR / "strategy_weekly_baseline_equity.csv",
                           index_col=0, parse_dates=True)["equity"]
    bh_eq = pd.read_csv(_BACKTEST_DIR / "benchmark_equity_clean.csv",
                         index_col=0, parse_dates=True)["equity"]
    m_live = compute_metrics(live_eq)
    m_bh = compute_metrics(bh_eq)

    # ---- comparison chart, full period ----
    fig, ax = plt.subplots(figsize=(13, 7))
    for name, series, color in [
        ("LIVE tiered regime (EMA50/EMA100, 3-state)", live_eq, "#7f8c8d"),
        ("NEW simple risk-on/off (price vs EMA50)", res_simple["equity"]["equity"], "#c0392b"),
        (f"Buy & Hold {bt.BENCHMARK_TICKER}", bh_eq, "#2980b9"),
    ]:
        norm = series / series.iloc[0] * 100
        ax.plot(norm.index, norm, label=name, color=color, linewidth=1.3)
    ax.set_title("ETF Momentum: Tiered regime vs Simple risk-on/off regime vs Buy & Hold (full period)")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_simple_regime_full.png", dpi=140)
    print(f"\nChart (full period) -> {_BACKTEST_DIR / 'comparison_simple_regime_full.png'}")

    print(f"\n{'='*70}\n  FULL PERIOD SUMMARY: {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*70}")
    print(f"  {'Metric':<20}{'LIVE tiered':>18}{'Simple on/off':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("CAGR", "cagr", "{:.2%}"), ("Total Return", None, None),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"), ("Final Equity", "final", "INR {:,.0f}"),
    ]:
        if key is None:
            a = f"{(m_live['final']/m_live['initial']-1):.2%}"
            b = f"{(m_simple['final']/m_simple['initial']-1):.2%}"
            c = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            a = fmt.format(m_live[key]); b = fmt.format(m_simple[key]); c = fmt.format(m_bh[key])
        print(f"  {label:<20}{a:>18}{b:>18}{c:>18}")

    # ---- 2018-2020 sub-period comparison ----
    live_w = live_eq.loc[SUBPERIOD_START:SUBPERIOD_END]
    simple_w = res_simple["equity"]["equity"].loc[SUBPERIOD_START:SUBPERIOD_END]
    bh_w = bh_eq.loc[SUBPERIOD_START:SUBPERIOD_END]
    m_live_w = compute_metrics(live_w)
    m_simple_w = compute_metrics(simple_w)
    m_bh_w = compute_metrics(bh_w)

    fig2, ax2 = plt.subplots(figsize=(13, 7))
    for name, series, color in [
        ("LIVE tiered regime", live_w, "#7f8c8d"),
        ("NEW simple risk-on/off", simple_w, "#c0392b"),
        ("Buy & Hold Nifty 500", bh_w, "#2980b9"),
    ]:
        norm = series / series.iloc[0] * 100
        ax2.plot(norm.index, norm, label=name, color=color, linewidth=1.5)
    ax2.axvspan(pd.Timestamp("2018-08-01"), pd.Timestamp("2019-03-01"), color="grey", alpha=0.12, label="IL&FS / mid-cap crash")
    ax2.axvspan(pd.Timestamp("2020-02-15"), pd.Timestamp("2020-04-15"), color="orange", alpha=0.15, label="COVID crash")
    ax2.set_title("2018-2020 real drawdown period: Tiered vs Simple regime vs Buy & Hold")
    ax2.set_ylabel("Growth of 100")
    ax2.legend()
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.setp(ax2.get_xticklabels(), rotation=45)
    fig2.tight_layout()
    fig2.savefig(_BACKTEST_DIR / "comparison_simple_regime_2018_2020.png", dpi=140)
    print(f"\nChart (2018-2020) -> {_BACKTEST_DIR / 'comparison_simple_regime_2018_2020.png'}")

    print(f"\n{'='*70}\n  2018-2020 SUB-PERIOD SUMMARY\n{'='*70}")
    print(f"  {'Metric':<20}{'LIVE tiered':>18}{'Simple on/off':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("Total Return", None, None), ("CAGR", "cagr", "{:.2%}"),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"),
    ]:
        if key is None:
            a = f"{(m_live_w['final']/m_live_w['initial']-1):.2%}"
            b = f"{(m_simple_w['final']/m_simple_w['initial']-1):.2%}"
            c = f"{(m_bh_w['final']/m_bh_w['initial']-1):.2%}"
        else:
            a = fmt.format(m_live_w[key]); b = fmt.format(m_simple_w[key]); c = fmt.format(m_bh_w[key])
        print(f"  {label:<20}{a:>18}{b:>18}{c:>18}")

    n_buys = (res_simple["trades"]["TYPE"] == "BUY").sum() if len(res_simple["trades"]) else 0
    n_sells = (res_simple["trades"]["TYPE"] == "SELL").sum() if len(res_simple["trades"]) else 0
    print(f"\n  Simple-regime trades -- BUY: {n_buys}   SELL: {n_sells}")


if __name__ == "__main__":
    main()
