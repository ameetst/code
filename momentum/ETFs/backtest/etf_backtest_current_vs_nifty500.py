"""
ETF Momentum Backtest -- CURRENT live algo vs Buy & Hold Nifty 500
=====================================================================================
EVALUATION ONLY -- imports the live etf_momentum_ranking.py module for its
scoring/screening/regime/allocation functions (so this is a byte-faithful
backtest of the CURRENT live algo, not a re-implementation), but never calls
anything that writes to disk or hits the network with "today"-anchored state:

  - emr.fetch_nifty500_index is monkey-patched (in-process only, nothing
    written to etf_momentum_ranking.py) to always return None, so
    emr.regime_status() falls back to point-in-time ETF.xlsx-style regime
    data instead of trying to fetch "today's" live index for every
    simulated historical week.
  - Regime trend itself is evaluated locally each week from a full,
    once-fetched ^CRSLDX history (yfinance), sliced to "as of" each
    decision date -- this IS the live regime rule's own Priority-1 source,
    just made point-in-time-correct for backtesting.
  - No file under momentum/ETFs/ is read for writing, and no live state
    (holdings_log.json, strategy_config.json, nifty500_cache.csv, ETF.xlsx)
    is modified. Peak/entry tracking across weeks is kept in-memory here,
    not via emr.update_log()/holdings_log.json (which is keyed off the
    real calendar date, not the simulated one).

Data: C:\\Users\\ameet\\Documents\\Github\\dhan_datahq\\base files\\History_ETFs.xlsx
      (DATA sheet: row1 = TICKER + literal historical dates, no ETF_NAME column)

Benchmark: ^CRSLDX (Nifty 500 Index, price return) via yfinance, bought once
and held flat over the same period -- also used as the live regime's own
trend signal, so strategy and benchmark share one consistent index source.

Usage:
  python etf_backtest_current_vs_nifty500.py
"""

import sys
import io
import contextlib
from pathlib import Path
from datetime import timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import yfinance as yf

_BACKTEST_DIR = Path(__file__).resolve().parent
_ETFS_DIR = _BACKTEST_DIR.parent
sys.path.insert(0, str(_ETFS_DIR))

import etf_momentum_ranking as emr  # noqa: E402  (the LIVE module -- read only)

CONFIG = emr.CONFIG

# Never let the live module hit yfinance / its cache file for "today" while
# we are asking it about arbitrary historical dates.
emr.fetch_nifty500_index = lambda *a, **kw: None

HISTORY_FILE = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_ETFs.xlsx"
BENCHMARK_TICKER = "^CRSLDX"

START_CAPITAL = 1_000_000.0
TRADE_COST_FIXED = 20.0
CASH_INTEREST_PA = 0.02
MIN_HISTORY_DAYS = 260   # ~1 trading year warm-up before the first rebalance


# =========================================================
# DATA LOADING (History_ETFs.xlsx has literal date headers,
# no ETF_NAME column -- different shape from the live ETF.xlsx)
# =========================================================
def load_history_etfs(filepath: str):
    from openpyxl import load_workbook
    print(f"[load] {filepath}")
    wb = load_workbook(filepath, data_only=True, read_only=True)
    ws = wb["DATA"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = rows[0]
    dates = pd.to_datetime(list(header[1:]))

    tickers, price_rows = [], []
    for row in rows[1:]:
        t = str(row[0]).strip().upper() if row[0] is not None else ""
        if not t or t == "NONE":
            continue
        tickers.append(t)
        price_rows.append(row[1:])

    price_raw = pd.DataFrame(price_rows, index=tickers, columns=dates)
    price_raw = price_raw.apply(pd.to_numeric, errors="coerce").replace(0, np.nan)
    prices = price_raw.T.sort_index()
    prices = prices.loc[:, prices.notna().sum() > 0]   # drop fully-empty tickers
    prices = prices.ffill()

    meta = pd.DataFrame({"TICKER": prices.columns, "ETF_NAME": prices.columns})
    print(f"       {len(meta)} tickers (non-empty) | {len(prices)} trading days "
          f"({prices.index[0].date()} -> {prices.index[-1].date()})")
    return meta.reset_index(drop=True), prices


def get_week_starts(all_dates: pd.DatetimeIndex) -> list:
    out, prev_key = [], None
    for d in all_dates:
        key = (d.isocalendar()[0], d.isocalendar()[1])
        if key != prev_key:
            out.append(d)
            prev_key = key
    return out


def evaluate_regime(price, ema50, ema100):
    """Exact replica of emr.regime_status()'s tiered BULL/PARTIAL/BEAR branch."""
    if pd.isna(price) or pd.isna(ema50) or pd.isna(ema100):
        return "BULL", CONFIG.TOP_N
    if ema50 > ema100 and price > ema50:
        return "BULL", CONFIG.TOP_N
    elif price > ema100:
        return "PARTIAL", CONFIG.TOP_N_PARTIAL
    else:
        return "BEAR", 0


def fetch_benchmark_series(all_dates: pd.DatetimeIndex):
    print(f"[regime/benchmark] Fetching {BENCHMARK_TICKER} (Nifty 500 Index) via yfinance ...")
    raw = yf.download(BENCHMARK_TICKER,
                       start=all_dates[0].strftime("%Y-%m-%d"),
                       end=(all_dates[-1] + timedelta(days=5)).strftime("%Y-%m-%d"),
                       auto_adjust=True, progress=False)
    close = raw["Close"].squeeze().dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    print(f"       {len(close)} rows ({close.index[0].date()} -> {close.index[-1].date()})")
    regime_s = close.reindex(all_dates, method="ffill")
    ema50 = regime_s.ewm(span=CONFIG.TREND_FAST_EMA_WINDOW, adjust=False).mean()
    ema100 = regime_s.ewm(span=CONFIG.TREND_EMA_WINDOW, adjust=False).mean()
    return regime_s, ema50, ema100


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
            "max_dd": max_dd, "vol": vol, "sharpe": sharpe}


def print_metrics(name: str, m: dict):
    print(f"\n  {name}")
    print(f"    CAGR            : {m['cagr']:>9.2%}")
    print(f"    Total Return    : {(m['final']/m['initial']-1):>9.2%}")
    print(f"    Max Drawdown    : {m['max_dd']:>9.2%}")
    print(f"    Volatility      : {m['vol']:>9.2%}")
    print(f"    Sharpe (simple) : {m['sharpe']:>9.2f}")
    print(f"    Final Equity    : INR {m['final']:>14,.0f}")


# =========================================================
# MAIN BACKTEST LOOP -- strategy engine
# =========================================================
def run_strategy(meta, prices, regime_s, ema50, ema100, ranking_fn=None):
    """ranking_fn(meta, hist) -> ranking_df; defaults to the live emr.build_ranking
    (same 6M/3M weighted-Sharpe composite). Pass a different callable to test an
    alternate scoring rule while keeping the weekly hold-and-replace cadence
    (emr.build_allocation) and all other live rules unchanged."""
    if ranking_fn is None:
        ranking_fn = emr.build_ranking
    all_dates = prices.index
    week_starts = set(get_week_starts(all_dates))
    date_list = list(all_dates)
    date_index = {d: i for i, d in enumerate(date_list)}

    cash = START_CAPITAL
    slots: dict[str, dict] = {}   # ticker -> {shares, entry_price, entry_date, peak, sector, etf_name}
    equity_rows = []
    trade_log = []
    regime_label = "BULL"

    for d in all_dates:
        idx = date_index[d]
        cash *= (1 + CASH_INTEREST_PA / 365.0)

        if d in week_starts and idx >= MIN_HISTORY_DAYS:
            prev_day = date_list[idx - 1] if idx > 0 else d
            hist = prices.loc[:prev_day]

            price_now = regime_s.loc[prev_day] if prev_day in regime_s.index else np.nan
            ema50v = ema50.loc[prev_day] if prev_day in ema50.index else np.nan
            ema100v = ema100.loc[prev_day] if prev_day in ema100.index else np.nan
            regime_label, active_slots = evaluate_regime(price_now, ema50v, ema100v)
            regime = {"label": regime_label, "active_slots": active_slots}

            with contextlib.redirect_stdout(io.StringIO()):
                ranking_df = ranking_fn(meta, hist)

            prev_allocation = [
                {"ticker": t, "etf_name": s["etf_name"], "sector": s["sector"], "peak": s["peak"]}
                for t, s in slots.items()
            ]
            allocation_df = emr.build_allocation(ranking_df, regime,
                                                  prev_allocation=prev_allocation, prices=hist)

            target = {row["TICKER"]: row for _, row in allocation_df.iterrows()
                      if row["TICKER"] != "CASH"}
            held_before = set(slots.keys())
            held_after = set(target.keys())

            total_equity = cash + sum(
                slots[t]["shares"] * prices.loc[d, t]
                for t in slots if t in prices.columns and pd.notna(prices.loc[d, t])
            )
            slot_size = total_equity / CONFIG.TOP_N

            # ---- SELLS (rule exit or regime/sector-cap slot cut) ----
            for t in held_before - held_after:
                s = slots.pop(t)
                price_now_t = (prices.loc[d, t]
                               if t in prices.columns and pd.notna(prices.loc[d, t])
                               else s["entry_price"])
                exit_flag, reason = emr.should_exit(t, ranking_df, s["peak"], price_now_t)
                reason = reason if exit_flag else "Regime slot reduction / sector cap / rank cut"
                proceeds = s["shares"] * price_now_t - TRADE_COST_FIXED
                pnl = proceeds - s["shares"] * s["entry_price"]
                cash += proceeds
                trade_log.append({
                    "TYPE": "SELL", "REASON": reason, "TICKER": t, "NAME": s["etf_name"],
                    "ENTRY_DATE": s["entry_date"], "EXIT_DATE": d,
                    "ENTRY_PRICE": round(s["entry_price"], 4), "EXIT_PRICE": round(price_now_t, 4),
                    "SHARES": round(s["shares"], 4), "NET_PNL": round(pnl, 2), "REGIME": regime_label,
                })

            # ---- HOLDS: refresh peak using the decision-date (prev_day) price ----
            for t in held_before & held_after:
                s = slots[t]
                px_prev = (hist[t].dropna().iloc[-1]
                           if t in hist.columns and hist[t].notna().any() else s["peak"])
                s["peak"] = max(s["peak"], px_prev)
                s["sector"] = target[t].get("SECTOR", s["sector"])

            # ---- BUYS ----
            for t in held_after - held_before:
                row = target[t]
                p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                if pd.isna(p_entry) or p_entry <= 0:
                    continue
                shares = (slot_size - TRADE_COST_FIXED) / p_entry
                cash -= slot_size
                slots[t] = {"shares": shares, "entry_price": p_entry, "entry_date": d,
                            "peak": p_entry, "sector": row.get("SECTOR", "OTHER"),
                            "etf_name": row.get("ETF_NAME", t)}
                trade_log.append({
                    "TYPE": "BUY", "REASON": f"NEW BUY (rank={row.get('INV_RANK','-')})",
                    "TICKER": t, "NAME": row.get("ETF_NAME", t), "ENTRY_DATE": d, "EXIT_DATE": None,
                    "ENTRY_PRICE": round(p_entry, 4), "EXIT_PRICE": None,
                    "SHARES": round(shares, 4), "NET_PNL": None, "REGIME": regime_label,
                })

        port_val = sum(slots[t]["shares"] * prices.loc[d, t] for t in slots
                        if t in prices.columns and pd.notna(prices.loc[d, t]))
        equity_rows.append({"date": d, "equity": cash + port_val,
                             "regime": regime_label, "n_holdings": len(slots)})

    eq = pd.DataFrame(equity_rows).set_index("date")
    return {"equity": eq, "trades": pd.DataFrame(trade_log)}


def run_benchmark(regime_s: pd.Series) -> pd.Series:
    shares = START_CAPITAL / regime_s.iloc[0]
    return shares * regime_s


def main():
    print(f"{'='*70}\n  ETF Momentum -- CURRENT algo vs Buy & Hold Nifty 500 (^CRSLDX)\n{'='*70}")
    print("\n[config] Live strategy_config.json / CONFIG values in effect:")
    for k, v in emr.get_config_as_dict().items():
        print(f"    {k:<24}: {v}")

    meta, prices = load_history_etfs(HISTORY_FILE)
    all_dates = prices.index

    regime_s, ema50, ema100 = fetch_benchmark_series(all_dates)

    print(f"\n{'='*70}\n  Running CURRENT algo (weekly hold-and-replace, live rules)\n{'='*70}")
    res = run_strategy(meta, prices, regime_s, ema50, ema100)
    res["equity"].to_csv(_BACKTEST_DIR / "strategy_equity.csv")
    res["trades"].to_csv(_BACKTEST_DIR / "strategy_trade_log.csv", index=False)
    m_strat = compute_metrics(res["equity"]["equity"])
    print_metrics("CURRENT ALGO", m_strat)

    print(f"\n{'='*70}\n  Buy & Hold -- {BENCHMARK_TICKER} (Nifty 500 Index)\n{'='*70}")
    bh_eq = run_benchmark(regime_s)
    bh_eq.to_frame("equity").to_csv(_BACKTEST_DIR / "benchmark_equity.csv")
    m_bh = compute_metrics(bh_eq)
    print_metrics("BUY & HOLD NIFTY 500", m_bh)

    # ---- sanity check for outlier daily moves (data-quality flag) ----
    for name, series in [("strategy", res["equity"]["equity"]), ("benchmark", bh_eq)]:
        r = series.pct_change().dropna()
        extreme = r[r.abs() > 0.15]
        if len(extreme):
            print(f"\n  [!] {name}: {len(extreme)} daily move(s) > 15% -- possible bad print, inspect trade log:")
            print(extreme.to_string())

    # ---- comparison chart ----
    fig, ax = plt.subplots(figsize=(13, 7))
    strat_norm = res["equity"]["equity"] / res["equity"]["equity"].iloc[0] * 100
    bh_norm = bh_eq / bh_eq.iloc[0] * 100
    ax.plot(strat_norm.index, strat_norm, label="CURRENT algo (weekly hold-and-replace)", color="#c0392b", linewidth=1.4)
    ax.plot(bh_norm.index, bh_norm, label=f"Buy & Hold {BENCHMARK_TICKER} (Nifty 500)", color="#2980b9", linewidth=1.4)
    ax.set_title("ETF Momentum: CURRENT algo vs Buy & Hold Nifty 500")
    ax.set_ylabel("Growth of 100 (log scale)")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(alpha=0.3)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "comparison_equity_curve.png", dpi=140)
    print(f"\nChart -> {_BACKTEST_DIR / 'comparison_equity_curve.png'}")

    # ---- summary table ----
    print(f"\n{'='*70}\n  SUMMARY: {all_dates[0].date()} -> {all_dates[-1].date()}\n{'='*70}")
    print(f"  {'Metric':<20}{'CURRENT algo':>18}{'Buy&Hold N500':>18}")
    for label, key, fmt in [
        ("CAGR", "cagr", "{:.2%}"), ("Total Return", None, None),
        ("Max Drawdown", "max_dd", "{:.2%}"), ("Volatility", "vol", "{:.2%}"),
        ("Sharpe (simple)", "sharpe", "{:.2f}"), ("Final Equity", "final", "INR {:,.0f}"),
    ]:
        if key is None:
            a = f"{(m_strat['final']/m_strat['initial']-1):.2%}"
            b = f"{(m_bh['final']/m_bh['initial']-1):.2%}"
        else:
            a = fmt.format(m_strat[key]); b = fmt.format(m_bh[key])
        print(f"  {label:<20}{a:>18}{b:>18}")


if __name__ == "__main__":
    main()
