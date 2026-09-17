"""
ETF Momentum Backtest -- Monthly hold-and-flush vs Weekly hold-and-replace vs Buy & Hold
=====================================================================================
EVALUATION ONLY -- backtest-only variant, NOT implemented in the live
etf_momentum_ranking.py. Adds a new allocation cadence on top of the same
screen/score/regime/exit rules already validated in
etf_backtest_current_vs_nifty500.py and etf_backtest_current_vs_nifty500_clean.py:

  MONTHLY (new, this script):
    - On the first trading day of each month: rank + screen the universe,
      pick the top-ranked ETFs up to the regime's active-slot count
      (sector cap enforced), equal weight.
    - Hold for the whole month. No new buys and no replacement of an
      exited slot until the next month's pick.
    - Any of the three exit rules (52wk-high DD, rank cutoff, 5% TSL)
      firing intra-month sells that one position -> cash, not replaced.
    - On the last trading day of the month: sell everything still held
      (full flush), then re-pick fresh next month.

  WEEKLY (existing live rules, reused via emr.build_allocation): re-run
  here on the SAME cleaned price data for a fair three-way comparison.

Data is cleaned with the same transient/permanent bad-print fix validated
in etf_backtest_current_vs_nifty500_clean.py before either engine runs.

Usage:
  python etf_backtest_monthly_vs_weekly.py
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


def get_month_starts(all_dates: pd.DatetimeIndex) -> list:
    out, prev_key = [], None
    for d in all_dates:
        key = (d.year, d.month)
        if key != prev_key:
            out.append(d)
            prev_key = key
    return out


def get_month_ends(all_dates: pd.DatetimeIndex, month_starts: list) -> set:
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}
    ends = []
    for i in range(1, len(month_starts)):
        ends.append(date_list[date_index[month_starts[i]] - 1])
    ends.append(date_list[-1])
    return set(ends)


def run_strategy_monthly(meta, prices, regime_s, ema50, ema100):
    all_dates = prices.index
    month_starts_list = get_month_starts(all_dates)
    month_starts = set(month_starts_list)
    month_ends = get_month_ends(all_dates, month_starts_list)
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}

    cash = bt.START_CAPITAL
    slots: dict[str, dict] = {}
    equity_rows = []
    trade_log = []
    regime_label = "BULL"

    for d in all_dates:
        idx = date_index[d]
        cash *= (1 + bt.CASH_INTEREST_PA / 365.0)

        ranking_df = None
        if idx >= bt.MIN_HISTORY_DAYS and (slots or d in month_starts):
            prev_day = date_list[idx - 1] if idx > 0 else d
            hist = prices.loc[:prev_day]
            with contextlib.redirect_stdout(io.StringIO()):
                ranking_df = emr.build_ranking(meta, hist)

        # ---- daily exit-rule monitoring on currently held slots ----
        if slots and ranking_df is not None:
            for t in list(slots.keys()):
                s = slots[t]
                price_now = (prices.loc[d, t]
                             if t in prices.columns and pd.notna(prices.loc[d, t])
                             else s["entry_price"])
                s["peak"] = max(s["peak"], price_now)
                exit_flag, reason = emr.should_exit(t, ranking_df, s["peak"], price_now)
                if exit_flag:
                    proceeds = s["shares"] * price_now - bt.TRADE_COST_FIXED
                    pnl = proceeds - s["shares"] * s["entry_price"]
                    cash += proceeds
                    trade_log.append({
                        "TYPE": "SELL", "REASON": f"INTRA-MONTH EXIT: {reason} (moved to cash)",
                        "TICKER": t, "NAME": s["etf_name"], "ENTRY_DATE": s["entry_date"], "EXIT_DATE": d,
                        "ENTRY_PRICE": round(s["entry_price"], 4), "EXIT_PRICE": round(price_now, 4),
                        "SHARES": round(s["shares"], 4), "NET_PNL": round(pnl, 2), "REGIME": regime_label,
                    })
                    del slots[t]

        # ---- month-end full flush of whatever survived the month ----
        if d in month_ends and slots:
            for t in list(slots.keys()):
                s = slots[t]
                price_now = (prices.loc[d, t]
                             if t in prices.columns and pd.notna(prices.loc[d, t])
                             else s["entry_price"])
                proceeds = s["shares"] * price_now - bt.TRADE_COST_FIXED
                pnl = proceeds - s["shares"] * s["entry_price"]
                cash += proceeds
                trade_log.append({
                    "TYPE": "SELL", "REASON": "MONTH-END FLUSH", "TICKER": t, "NAME": s["etf_name"],
                    "ENTRY_DATE": s["entry_date"], "EXIT_DATE": d,
                    "ENTRY_PRICE": round(s["entry_price"], 4), "EXIT_PRICE": round(price_now, 4),
                    "SHARES": round(s["shares"], 4), "NET_PNL": round(pnl, 2), "REGIME": regime_label,
                })
                del slots[t]

        # ---- month-start fresh pick (slots should be empty here) ----
        if d in month_starts and idx >= bt.MIN_HISTORY_DAYS and ranking_df is not None:
            prev_day = date_list[idx - 1] if idx > 0 else d
            price_now_r = regime_s.loc[prev_day] if prev_day in regime_s.index else np.nan
            ema50v = ema50.loc[prev_day] if prev_day in ema50.index else np.nan
            ema100v = ema100.loc[prev_day] if prev_day in ema100.index else np.nan
            regime_label, active_slots = bt.evaluate_regime(price_now_r, ema50v, ema100v)

            total_equity = cash + sum(
                slots[t]["shares"] * prices.loc[d, t] for t in slots
                if t in prices.columns and pd.notna(prices.loc[d, t])
            )
            slot_size = total_equity / CONFIG.TOP_N

            if active_slots > 0:
                candidates = ranking_df[ranking_df["SCREEN_PASS"] & (ranking_df["RANK_INVESTABLE"] > 0)] \
                    .sort_values("RANK_INVESTABLE")
                sector_count: dict[str, int] = {}
                picked = 0
                for _, row in candidates.iterrows():
                    if picked >= active_slots:
                        break
                    t = row["TICKER"]
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
                        "TYPE": "BUY", "REASON": f"MONTHLY PICK (rank={int(row['RANK_INVESTABLE'])})",
                        "TICKER": t, "NAME": row.get("ETF_NAME", t), "ENTRY_DATE": d, "EXIT_DATE": None,
                        "ENTRY_PRICE": round(p_entry, 4), "EXIT_PRICE": None,
                        "SHARES": round(shares, 4), "NET_PNL": None, "REGIME": regime_label,
                    })
                    sector_count[sector] = sector_count.get(sector, 0) + 1
                    picked += 1

        port_val = sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                        if t in prices.columns and pd.notna(prices.loc[d, t]))
        equity_rows.append({"date": d, "equity": cash + port_val,
                             "regime": regime_label, "n_holdings": len(slots)})

    eq = pd.DataFrame(equity_rows).set_index("date")
    return {"equity": eq, "trades": pd.DataFrame(trade_log)}


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    print("\n[clean] Applying the same transient/permanent bad-print fix as before ...")
    prices, audit = detect_and_clean(prices_raw)
    print(f"        {len(audit)} fixes applied ({(audit['TYPE']=='TRANSIENT_NULLED').sum()} transient, "
          f"{(audit['TYPE']!='TRANSIENT_NULLED').sum()} permanent rescale)")

    all_dates = prices.index
    regime_s, ema50, ema100 = bt.fetch_benchmark_series(all_dates)

    print(f"\n{'='*70}\n  Running WEEKLY (live rules, hold-and-replace)\n{'='*70}")
    res_weekly = bt.run_strategy(meta, prices, regime_s, ema50, ema100)
    res_weekly["equity"].to_csv(_BACKTEST_DIR / "strategy_weekly_equity.csv")
    res_weekly["trades"].to_csv(_BACKTEST_DIR / "strategy_weekly_trade_log.csv", index=False)
    m_weekly = bt.compute_metrics(res_weekly["equity"]["equity"])
    bt.print_metrics("WEEKLY (hold-and-replace)", m_weekly)

    print(f"\n{'='*70}\n  Running MONTHLY (hold-and-flush, no intra-month replacement)\n{'='*70}")
    res_monthly = run_strategy_monthly(meta, prices, regime_s, ema50, ema100)
    res_monthly["equity"].to_csv(_BACKTEST_DIR / "strategy_monthly_equity.csv")
    res_monthly["trades"].to_csv(_BACKTEST_DIR / "strategy_monthly_trade_log.csv", index=False)
    m_monthly = bt.compute_metrics(res_monthly["equity"]["equity"])
    bt.print_metrics("MONTHLY (hold-and-flush)", m_monthly)

    print(f"\n{'='*70}\n  Buy & Hold -- {bt.BENCHMARK_TICKER}\n{'='*70}")
    bh_eq = bt.run_benchmark(regime_s)
    m_bh = bt.compute_metrics(bh_eq)
    bt.print_metrics("BUY & HOLD NIFTY 500", m_bh)

    for name, series in [("weekly", res_weekly["equity"]["equity"]),
                          ("monthly", res_monthly["equity"]["equity"])]:
        r = series.pct_change().dropna()
        extreme = r[r.abs() > 0.15]
        if len(extreme):
            print(f"\n  [!] {name}: {len(extreme)} residual daily move(s) > 15%:")
            print(extreme.to_string())

    fig, ax = plt.subplots(figsize=(13, 7))
    for name, series, color in [
        ("WEEKLY (hold-and-replace)", res_weekly["equity"]["equity"], "#c0392b"),
        ("MONTHLY (hold-and-flush)", res_monthly["equity"]["equity"], "#27ae60"),
        (f"Buy & Hold {bt.BENCHMARK_TICKER}", bh_eq, "#2980b9"),
    ]:
        norm = series / series.iloc[0] * 100
        ax.plot(norm.index, norm, label=name, color=color, linewidth=1.3)
    ax.set_title("ETF Momentum: Weekly vs Monthly cadence vs Buy & Hold Nifty 500")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_monthly_vs_weekly.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / 'comparison_monthly_vs_weekly.png'}")

    print(f"\n{'='*70}\n  SUMMARY: {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*70}")
    print(f"  {'Metric':<20}{'WEEKLY':>18}{'MONTHLY':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("CAGR", "cagr", "{:.2%}"), ("Total Return", None, None),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"), ("Final Equity", "final", "INR {:,.0f}"),
    ]:
        if key is None:
            w = f"{(m_weekly['final']/m_weekly['initial']-1):.2%}"
            m = f"{(m_monthly['final']/m_monthly['initial']-1):.2%}"
            b = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            w = fmt.format(m_weekly[key]); m = fmt.format(m_monthly[key]); b = fmt.format(m_bh[key])
        print(f"  {label:<20}{w:>18}{m:>18}{b:>18}")

    n_buys_w = (res_weekly["trades"]["TYPE"] == "BUY").sum() if len(res_weekly["trades"]) else 0
    n_buys_m = (res_monthly["trades"]["TYPE"] == "BUY").sum() if len(res_monthly["trades"]) else 0
    print(f"\n  Total BUY trades -- WEEKLY: {n_buys_w}   MONTHLY: {n_buys_m}")


if __name__ == "__main__":
    main()
