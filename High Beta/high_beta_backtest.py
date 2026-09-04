"""
High Beta Strategy — Backtest
==============================
Long-only, monthly-rebalanced strategy that buys the 20 highest-beta liquid
NSE stocks (vs NIFTY500) each month and equal-weights them.

Data source : ../../dhan_datahq/base files/History_updated.xlsx  (DATA + VOLUME sheets)
Universe    : all tickers in the DATA sheet, benchmark = NIFTY500
Signal      : rolling 6-month (126 trading day) beta vs NIFTY500 daily returns
Selection   : Top 20 highest-beta names passing the liquidity filter, equal-weighted
Rebalance   : monthly, on the last trading day of each month
Liquidity   : median daily traded value (INR) over the lookback window >= INR 1,00,00,000
              (per user spec: >= INR 10,000,000)
Costs       : flat INR 20 per executed buy/sell leg, charged only on names
              entering or leaving the portfolio at each rebalance (and on any
              stop-loss exit, if STOP_LOSS_PCT is set below)
Capital     : starting capital assumed INR 10,00,000 (stated assumption — edit START_CAPITAL)
Risk-free   : 6.5% p.a. assumption, used only for Sharpe/Sortino
Stop-loss   : optional. Set STOP_LOSS_PCT (e.g. 0.20 for a 20% stop) to exit
              any position that closes 20% below its entry price (the price
              at the rebalance date it was bought), irrespective of its beta
              rank. The freed capital sits in cash (0% return) until the next
              monthly rebalance. Set STOP_LOSS_PCT = None to disable.

Outputs (written to ./high_beta_backtest_results/):
  - High_Beta_Strategy_Backtest.xlsx  (summary, charts, annual returns, holdings, daily NAV)
  - high_beta_equity_curve.png
  - high_beta_avg_beta.png
  - high_beta_daily_nav.csv
  - high_beta_holdings_history.csv
  - high_beta_summary.json
"""

import warnings
warnings.filterwarnings("ignore")

import json
import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.drawing.image import Image as XLImage

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
DATA_FILE        = os.path.join("..", "..", "dhan_datahq", "base files", "History_updated.xlsx")
OUT_DIR          = "high_beta_backtest_results"
BENCHMARK        = "NIFTY500"
LOOKBACK_DAYS    = 126          # ~6 trading months. NOTE: if you change this, rescale
                                 # MIN_HISTORY_PTS and MIN_BETA_PTS below proportionally
                                 # (they're expressed as a day-count, not a %, of LOOKBACK_DAYS).
                                 # e.g. for a 3-month (~63 day) lookback, use MIN_HISTORY_PTS=50,
                                 # MIN_BETA_PTS=40 to keep roughly the same coverage requirement.
TOP_N            = 20
LIQ_MIN_TURNOVER = 10_000_000   # INR, median daily turnover over the lookback window
COST_PER_ORDER   = 20.0         # INR, flat, per buy or sell leg
START_CAPITAL    = 1_000_000.0  # INR 10,00,000 (assumption)
RISK_FREE_RATE   = 0.065        # annualised (assumption, for Sharpe/Sortino only)
MIN_HISTORY_PTS  = 100          # min valid price obs required within lookback to be eligible
MIN_BETA_PTS     = 60           # min overlapping obs required to trust a beta estimate
STOP_LOSS_PCT    = None         # e.g. 0.20 for a 20% per-stock stop-loss from entry; None = off
REBALANCE_FREQ   = "M"          # "M" = monthly (last trading day of month), "W" = weekly (last trading day of week)
MIN_BETA_THRESHOLD    = None    # e.g. 1.0 to additionally require beta > this value to be selected
MAX_PCT_OFF_52W_HIGH  = None    # e.g. 0.25 to additionally require price within 25% of its
                                 # trailing 252-trading-day (52-week) high, i.e.
                                 # (52w_high - price) / 52w_high < MAX_PCT_OFF_52W_HIGH.
                                 # Filters out high-beta names that have already crashed far
                                 # from their highs ("falling knives"), keeping only those
                                 # still showing relative price strength.
MIN_SHARPE_1Y         = None    # e.g. 1.0 to additionally require the STOCK's own trailing
                                 # 252-trading-day daily Sharpe ratio (annualised, using
                                 # RISK_FREE_RATE) to exceed this value. A quality/consistency
                                 # filter: excludes high-beta names whose own risk-adjusted
                                 # return has been poor even if their raw beta is high.
BLEND_WINDOWS    = None         # None = single LOOKBACK_DAYS window (default behaviour).
                                 # Or a list of (lookback_days, min_beta_pts) tuples to rank by an
                                 # equal-weighted average beta across multiple windows instead, e.g.:
                                 #   BLEND_WINDOWS = [(63, 40), (126, 60), (252, 120)]   # 3m/6m/12m blend
                                 # A stock needs a valid beta in EVERY listed window to get a blended
                                 # score (missing any window drops it from selection that month).
                                 # Liquidity/history eligibility is always gated on the LONGEST window
                                 # in the blend (or LOOKBACK_DAYS itself when BLEND_WINDOWS is None).

os.makedirs(OUT_DIR, exist_ok=True)


def load_data():
    print("Loading data...")
    price_raw = pd.read_excel(DATA_FILE, sheet_name="DATA", header=0)
    vol_raw = pd.read_excel(DATA_FILE, sheet_name="VOLUME", header=0)

    price_raw = price_raw.drop_duplicates(subset="TICKER", keep="first").set_index("TICKER")
    vol_raw = vol_raw.drop_duplicates(subset="TICKER", keep="first").set_index("TICKER")

    price = price_raw.T
    vol = vol_raw.T
    price.index = pd.to_datetime(price.index)
    vol.index = pd.to_datetime(vol.index)
    price = price.sort_index()
    vol = vol.sort_index()

    common_cols = price.columns.intersection(vol.columns)
    price = price[common_cols]
    vol = vol[common_cols]

    price = price.mask(price <= 0)
    vol = vol.mask(vol < 0).fillna(0)

    assert BENCHMARK in price.columns, "Benchmark column missing from DATA sheet"

    # Drop holiday/blank rows: some "dates" in the source have no prices for
    # ANY ticker (market closed). Use the benchmark as the market-open proxy.
    valid_days = price[BENCHMARK].notna()
    price = price.loc[valid_days]
    vol = vol.loc[valid_days]

    return price, vol


def run_backtest(price, vol):
    stock_cols = [c for c in price.columns if c != BENCHMARK]
    trading_days = price.index
    returns = price.pct_change()
    bench_ret = returns[BENCHMARK]

    print(f"Universe: {len(stock_cols)} stocks, {len(price)} trading days "
          f"({trading_days.min().date()} -> {trading_days.max().date()})")

    period_code = "W" if REBALANCE_FREQ.upper().startswith("W") else "M"
    period_key = trading_days.to_period(period_code)
    last_day_per_period = pd.Series(trading_days, index=period_key).groupby(level=0).last()
    rebal_dates = last_day_per_period.tolist()

    windows = BLEND_WINDOWS if BLEND_WINDOWS else [(LOOKBACK_DAYS, MIN_BETA_PTS)]
    gating_lookback = max(lb for lb, _ in windows)
    gating_min_hist = MIN_HISTORY_PTS if not BLEND_WINDOWS else int(
        MIN_HISTORY_PTS * gating_lookback / LOOKBACK_DAYS)

    # the 52w-high and 1y-Sharpe filters need a full 252-day window to be
    # meaningful, so extend the warm-up period (not the liquidity gate itself)
    # when either is active.
    if MAX_PCT_OFF_52W_HIGH is not None or MIN_SHARPE_1Y is not None:
        gating_lookback = max(gating_lookback, 252)

    first_valid_idx = gating_lookback + 5
    rebal_dates = [d for d in rebal_dates if trading_days.get_loc(d) >= first_valid_idx]
    mode_desc = f"blend{[w[0] for w in windows]}" if BLEND_WINDOWS else f"single{LOOKBACK_DAYS}"
    print(f"Rebalance freq: {REBALANCE_FREQ} | Beta mode: {mode_desc} | Rebalance dates: {len(rebal_dates)} "
          f"({rebal_dates[0].date()} -> {rebal_dates[-1].date()})")

    def beta_for_window(asof_date, lookback, min_pts):
        loc = trading_days.get_loc(asof_date)
        window = returns.iloc[loc - lookback + 1: loc + 1]
        bwin = window[BENCHMARK]
        bvar = bwin.var()
        if bvar == 0 or np.isnan(bvar):
            return pd.Series(dtype=float)
        stock_win = window[stock_cols]
        valid_counts = stock_win.notna().sum()
        cov = stock_win.apply(lambda col: col.cov(bwin))
        return (cov / bvar)[valid_counts >= min_pts]

    def compute_betas(asof_date):
        """Returns the ranking score: a single beta, or an equal-weighted blend
        across BLEND_WINDOWS (a stock must have a valid beta in every listed
        window to receive a blended score)."""
        per_window = [beta_for_window(asof_date, lb, minpts) for lb, minpts in windows]
        combined = pd.concat(per_window, axis=1).dropna()
        return combined.mean(axis=1) if not combined.empty else pd.Series(dtype=float)

    def liquidity_ok(asof_date):
        loc = trading_days.get_loc(asof_date)
        pwin = price[stock_cols].iloc[loc - gating_lookback + 1: loc + 1]
        vwin = vol[stock_cols].iloc[loc - gating_lookback + 1: loc + 1]
        price_cov = pwin.notna().sum()
        med_turnover = vwin.median()
        cur_price_ok = price[stock_cols].loc[asof_date].notna()
        ok = (price_cov >= gating_min_hist) & (med_turnover >= LIQ_MIN_TURNOVER) & cur_price_ok
        return ok[ok].index.tolist()

    def pct_off_52w_high(asof_date):
        """(52w_high - price) / 52w_high, using a trailing 252-trading-day window."""
        loc = trading_days.get_loc(asof_date)
        lb52 = min(252, loc + 1)
        pwin = price[stock_cols].iloc[loc - lb52 + 1: loc + 1]
        high_52w = pwin.max()
        cur = price[stock_cols].loc[asof_date]
        return (high_52w - cur) / high_52w

    def sharpe_1y(asof_date):
        """Each stock's own trailing 252-trading-day annualised daily Sharpe."""
        loc = trading_days.get_loc(asof_date)
        lb1y = min(252, loc + 1)
        rwin = returns[stock_cols].iloc[loc - lb1y + 1: loc + 1]
        valid_counts = rwin.notna().sum()
        ann_ret = rwin.mean() * 252
        ann_vol = rwin.std() * np.sqrt(252)
        sh = (ann_ret - RISK_FREE_RATE) / ann_vol
        return sh[valid_counts >= 200]

    holdings_history = []
    prev_holdings = set()
    for d in rebal_dates:
        eligible = set(liquidity_ok(d))
        betas = compute_betas(d)
        betas = betas[betas.index.isin(eligible)]

        if MIN_BETA_THRESHOLD is not None:
            betas = betas[betas > MIN_BETA_THRESHOLD]

        if MAX_PCT_OFF_52W_HIGH is not None:
            off_high = pct_off_52w_high(d)
            passes_52w = off_high[off_high < MAX_PCT_OFF_52W_HIGH].index
            betas = betas[betas.index.isin(passes_52w)]

        if MIN_SHARPE_1Y is not None:
            sh1y = sharpe_1y(d)
            passes_sharpe = sh1y[sh1y > MIN_SHARPE_1Y].index
            betas = betas[betas.index.isin(passes_sharpe)]

        n_after_filters = len(betas)
        betas = betas.sort_values(ascending=False)
        top = betas.head(TOP_N)
        tickers = list(top.index)

        entries = [t for t in tickers if t not in prev_holdings]
        exits = [t for t in prev_holdings if t not in tickers]
        n_trades = len(entries) + len(exits)

        holdings_history.append({
            "rebal_date": d, "tickers": tickers, "betas": top.to_dict(),
            "n_eligible": len(eligible), "n_after_filters": n_after_filters,
            "entries": entries, "exits": exits,
            "n_trades": n_trades, "cost": n_trades * COST_PER_ORDER,
        })
        prev_holdings = set(tickers)

    # ---- Day-by-day NAV simulation ----
    # Capital is split 1/N across the N selected names at each rebalance
    # ("slots"). Each slot's value tracks price/entry_price * per_stock_capital.
    # If STOP_LOSS_PCT is set, a slot that closes STOP_LOSS_PCT below its entry
    # price is frozen at that floor (moved to cash, 0% return) for the rest of
    # the holding period, and one extra sell-order cost is charged.
    nav = START_CAPITAL
    daily_rows = []
    n_stop_events = 0
    for i, h in enumerate(holdings_history):
        start = h["rebal_date"]
        end = holdings_history[i + 1]["rebal_date"] if i + 1 < len(holdings_history) else trading_days[-1]
        nav -= h["cost"]
        period_days = trading_days[(trading_days > start) & (trading_days <= end)]
        tickers = h["tickers"]
        if not tickers or len(period_days) == 0:
            for d in period_days:
                daily_rows.append({"date": d, "ret": 0.0, "nav": nav, "n_holdings": 0, "n_stopped": 0})
            continue

        n = len(tickers)
        per_stock_capital = nav / n
        entry_price = price.loc[start, tickers]
        path = price.loc[period_days, tickers].ffill()   # carry last valid print through brief gaps
        ratio = path.div(entry_price, axis=1)
        n_active = path.notna().sum(axis=1)

        n_stopped_daily = pd.Series(0, index=period_days)
        if STOP_LOSS_PCT is not None:
            floor_ratio = 1 - STOP_LOSS_PCT
            below = ratio <= floor_ratio
            stopped_mask = below.cummax()          # once stopped, stays stopped for the period
            trigger_days = below.idxmax()
            for t in tickers:
                if below[t].any():
                    n_stop_events += 1
                    daily_rows_cost_day = trigger_days[t]
            eff_ratio = ratio.mask(stopped_mask, floor_ratio)
            n_stopped_daily = stopped_mask.sum(axis=1)
        else:
            eff_ratio = ratio

        slot_values = eff_ratio.mul(per_stock_capital)
        port_nav_path = slot_values.sum(axis=1, skipna=True)

        # apply stop-loss exit costs on the trigger day (persists forward)
        if STOP_LOSS_PCT is not None:
            for t in tickers:
                if below[t].any():
                    port_nav_path.loc[trigger_days[t]:] -= COST_PER_ORDER

        prev_nav = nav
        for d in period_days:
            new_nav = port_nav_path.loc[d]
            ret = new_nav / prev_nav - 1
            daily_rows.append({"date": d, "ret": ret, "nav": new_nav,
                                "n_holdings": int(n_active.loc[d]),
                                "n_stopped": int(n_stopped_daily.loc[d])})
            prev_nav = new_nav
        nav = port_nav_path.iloc[-1]

    daily_df = pd.DataFrame(daily_rows).set_index("date")
    bench_window = bench_ret.loc[daily_df.index]
    bench_nav = START_CAPITAL * (1 + bench_window.fillna(0)).cumprod()

    return holdings_history, daily_df, bench_window, bench_nav, n_stop_events


def perf_stats(ret_series, nav_series_):
    ret_series = ret_series.dropna()
    n_years = len(ret_series) / 252.0
    total_return = nav_series_.iloc[-1] / nav_series_.iloc[0] - 1
    cagr = (nav_series_.iloc[-1] / nav_series_.iloc[0]) ** (1 / n_years) - 1
    ann_vol = ret_series.std() * np.sqrt(252)
    sharpe = (cagr - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else np.nan
    roll_max = nav_series_.cummax()
    dd = nav_series_ / roll_max - 1
    max_dd = dd.min()
    calmar = cagr / abs(max_dd) if max_dd != 0 else np.nan
    downside = ret_series[ret_series < 0].std() * np.sqrt(252)
    sortino = (cagr - RISK_FREE_RATE) / downside if downside and downside > 0 else np.nan
    return {
        "Total Return %": round(total_return * 100, 2), "CAGR %": round(cagr * 100, 2),
        "Annualised Vol %": round(ann_vol * 100, 2), "Sharpe": round(sharpe, 3),
        "Sortino": round(sortino, 3), "Max Drawdown %": round(max_dd * 100, 2),
        "Calmar": round(calmar, 3), "Years": round(n_years, 2),
    }


def build_outputs(holdings_history, daily_df, bench_window, bench_nav, n_stop_events):
    port_stats = perf_stats(daily_df["ret"], daily_df["nav"])
    bench_stats = perf_stats(bench_window.fillna(0), bench_nav)

    common = pd.concat([daily_df["ret"], bench_window], axis=1, join="inner").dropna()
    cov_mat = np.cov(common.iloc[:, 0], common.iloc[:, 1])
    port_beta_full = cov_mat[0, 1] / cov_mat[1, 1]
    port_alpha_ann = (common.iloc[:, 0].mean() - port_beta_full * common.iloc[:, 1].mean()) * 252

    total_costs = sum(h["cost"] for h in holdings_history) + n_stop_events * COST_PER_ORDER
    avg_trades = float(np.mean([h["n_trades"] for h in holdings_history]))
    avg_eligible = float(np.mean([h["n_eligible"] for h in holdings_history]))

    summary = {
        "rebalance_freq": REBALANCE_FREQ,
        "stop_loss_pct": STOP_LOSS_PCT,
        "start_date": str(daily_df.index.min().date()), "end_date": str(daily_df.index.max().date()),
        "n_rebalances": len(holdings_history), "avg_eligible_universe": round(avg_eligible, 1),
        "avg_trades_per_rebalance": round(avg_trades, 2), "total_transaction_cost_inr": total_costs,
        "n_stop_loss_events": n_stop_events,
        "start_capital_inr": START_CAPITAL, "final_nav_inr": round(daily_df["nav"].iloc[-1], 2),
        "portfolio_beta_full_period": round(port_beta_full, 3),
        "portfolio_alpha_annualised_%": round(port_alpha_ann * 100, 2),
        "portfolio": port_stats, "benchmark_NIFTY500": bench_stats,
    }
    print(json.dumps(summary, indent=2, default=str))

    freq_tag = "_weekly" if REBALANCE_FREQ.upper().startswith("W") else ""
    blend_tag = "_blend" + "".join(f"{lb}" for lb, _ in BLEND_WINDOWS) if BLEND_WINDOWS else ""
    filt_tag = ("_betagt1" if MIN_BETA_THRESHOLD is not None else "") + \
               ("_off52w25" if MAX_PCT_OFF_52W_HIGH is not None else "") + \
               ("_sharpe1y" if MIN_SHARPE_1Y is not None else "")
    tag = freq_tag + blend_tag + filt_tag + (f"_sl{int(STOP_LOSS_PCT*100)}" if STOP_LOSS_PCT else "")
    daily_df.to_csv(os.path.join(OUT_DIR, f"high_beta_daily_nav{tag}.csv"))
    hold_rows = [{"rebal_date": h["rebal_date"], "ticker": t, "beta": h["betas"][t]}
                 for h in holdings_history for t in h["tickers"]]
    hold_df = pd.DataFrame(hold_rows)
    hold_df.to_csv(os.path.join(OUT_DIR, f"high_beta_holdings_history{tag}.csv"), index=False)
    with open(os.path.join(OUT_DIR, f"high_beta_summary{tag}.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # ---- charts ----
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})
    ax = axes[0]
    ax.plot(daily_df.index, daily_df["nav"], label="High-Beta Top-20 Strategy", color="#c0392b", lw=1.4)
    ax.plot(bench_nav.index, bench_nav, label="NIFTY500 (Buy & Hold)", color="#2c3e50", lw=1.2)
    ax.set_yscale("log")
    ax.set_ylabel("NAV (INR, log scale)")
    sl_label = f" (with {int(STOP_LOSS_PCT*100)}% per-stock stop-loss)" if STOP_LOSS_PCT else " (no stop-loss)"
    ax.set_title(f"High-Beta Strategy vs NIFTY500 — Equity Curve{sl_label}")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3, which="both")

    ax2 = axes[1]
    port_dd = daily_df["nav"] / daily_df["nav"].cummax() - 1
    bench_dd = bench_nav / bench_nav.cummax() - 1
    ax2.fill_between(daily_df.index, port_dd * 100, 0, color="#c0392b", alpha=0.4, label="Strategy Drawdown")
    ax2.fill_between(bench_nav.index, bench_dd * 100, 0, color="#2c3e50", alpha=0.25, label="NIFTY500 Drawdown")
    ax2.set_ylabel("Drawdown %")
    ax2.legend(loc="lower left")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    plt.tight_layout()
    eq_path = os.path.join(OUT_DIR, f"high_beta_equity_curve{tag}.png")
    plt.savefig(eq_path, dpi=140)
    plt.close(fig)

    avg_beta_series = hold_df.groupby("rebal_date")["beta"].mean()
    fig2, ax3 = plt.subplots(figsize=(11, 4.5))
    ax3.plot(avg_beta_series.index, avg_beta_series.values, color="#8e44ad", lw=1.3, marker="o", ms=3)
    ax3.axhline(1.0, color="gray", ls="--", lw=1)
    ax3.set_title("Average Beta of Selected Top-20 Portfolio at Each Monthly Rebalance")
    ax3.set_ylabel("Average Beta (6m trailing vs NIFTY500)")
    ax3.grid(alpha=0.3)
    ax3.xaxis.set_major_locator(mdates.YearLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    plt.tight_layout()
    beta_path = os.path.join(OUT_DIR, f"high_beta_avg_beta{tag}.png")
    plt.savefig(beta_path, dpi=140)
    plt.close(fig2)

    # ---- annual returns ----
    port_annual = daily_df["nav"].resample("YE").last().pct_change()
    port_annual.iloc[0] = daily_df["nav"].resample("YE").last().iloc[0] / START_CAPITAL - 1
    bnav = bench_nav.resample("YE").last()
    bench_annual = bnav.pct_change()
    bench_annual.iloc[0] = bnav.iloc[0] / START_CAPITAL - 1
    annual_tbl = pd.DataFrame({"Strategy %": (port_annual * 100).round(2),
                                "NIFTY500 %": (bench_annual * 100).round(2)})
    annual_tbl.index = annual_tbl.index.year

    # ---- workbook ----
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    title_font = Font(bold=True, size=14)
    bold = Font(bold=True, size=12)
    hdr_fill = PatternFill("solid", fgColor="1F2937")
    hdr_font = Font(bold=True, color="FFFFFF")

    ws["A1"] = "High Beta Strategy — Backtest Summary"
    ws["A1"].font = title_font
    ws["A2"] = "Universe: NSE stocks in History_updated.xlsx | Benchmark: NIFTY500"
    ws["A3"] = f"Period: {summary['start_date']} to {summary['end_date']} ({summary['portfolio']['Years']} years)"

    rules = [
        "Signal: rolling 6-month (126 trading day) beta vs NIFTY500",
        "Selection: Top 20 highest-beta stocks, equal-weighted",
        "Rebalance: monthly (month-end reconstitution)",
        "Liquidity filter: median daily traded value over lookback >= INR 10,000,000 (1 crore)",
        "Transaction cost: flat INR 20 per executed buy/sell leg on names entering/leaving the portfolio",
        "Starting capital (assumption): INR 10,00,000",
        "Risk-free rate (assumption, for Sharpe/Sortino): 6.5% p.a.",
    ]
    r = 5
    ws[f"A{r}"] = "Methodology"; ws[f"A{r}"].font = bold; r += 1
    for rule in rules:
        ws[f"A{r}"] = f"- {rule}"; r += 1

    r += 1
    ws[f"A{r}"] = "Performance Comparison"; ws[f"A{r}"].font = bold; r += 1
    metrics_tbl = pd.DataFrame({"Strategy (Top-20 High Beta)": summary["portfolio"],
                                 "NIFTY500 (Buy & Hold)": summary["benchmark_NIFTY500"]})
    ws.cell(row=r, column=1, value="Metric").font = hdr_font
    ws.cell(row=r, column=1).fill = hdr_fill
    for j, col in enumerate(metrics_tbl.columns, start=2):
        c = ws.cell(row=r, column=j, value=col); c.font = hdr_font; c.fill = hdr_fill
    r += 1
    for idx, row in metrics_tbl.iterrows():
        ws.cell(row=r, column=1, value=idx)
        for j, col in enumerate(metrics_tbl.columns, start=2):
            ws.cell(row=r, column=j, value=row[col])
        r += 1

    r += 1
    ws[f"A{r}"] = "Portfolio Risk Characteristics"; ws[f"A{r}"].font = bold; r += 1
    extra = [
        ("Realised portfolio beta (full period, daily regression)", summary["portfolio_beta_full_period"]),
        ("Realised annualised alpha vs NIFTY500", f"{summary['portfolio_alpha_annualised_%']}%"),
        ("Average eligible (liquid) universe per rebalance", summary["avg_eligible_universe"]),
        ("Number of monthly rebalances", summary["n_rebalances"]),
        ("Average trades (entries+exits) per rebalance", summary["avg_trades_per_rebalance"]),
        ("Stop-loss setting", f"{int(STOP_LOSS_PCT*100)}% per stock" if STOP_LOSS_PCT else "Off"),
        ("Total stop-loss events over backtest", summary["n_stop_loss_events"]),
        ("Total transaction costs paid over backtest", f"INR {summary['total_transaction_cost_inr']:,.0f}"),
        ("Starting capital", f"INR {summary['start_capital_inr']:,.0f}"),
        ("Final NAV", f"INR {summary['final_nav_inr']:,.0f}"),
    ]
    for label, val in extra:
        ws.cell(row=r, column=1, value=label); ws.cell(row=r, column=2, value=val); r += 1

    for col, width in zip("ABCDE", [55, 26, 26, 18, 18]):
        ws.column_dimensions[col].width = width

    ws2 = wb.create_sheet("Charts")
    img1 = XLImage(eq_path); img1.width, img1.height = 780, 560
    ws2.add_image(img1, "A1")
    img2 = XLImage(beta_path); img2.width, img2.height = 780, 320
    ws2.add_image(img2, "A32")

    ws3 = wb.create_sheet("Annual Returns")
    ws3.append(["Year", "Strategy %", "NIFTY500 %"])
    for c in ws3[1]:
        c.font = hdr_font; c.fill = hdr_fill
    for yr, row in annual_tbl.iterrows():
        ws3.append([int(yr), row["Strategy %"], row["NIFTY500 %"]])
    ws3.column_dimensions["A"].width = 10
    ws3.column_dimensions["B"].width = 14
    ws3.column_dimensions["C"].width = 14

    ws4 = wb.create_sheet("Holdings History")
    ws4.append(["Rebalance Date", "Ticker", "Beta (6m trailing)"])
    for c in ws4[1]:
        c.font = hdr_font; c.fill = hdr_fill
    for _, row in hold_df.iterrows():
        ws4.append([row["rebal_date"].strftime("%Y-%m-%d"), row["ticker"], round(row["beta"], 3)])
    ws4.column_dimensions["A"].width = 16
    ws4.column_dimensions["B"].width = 16
    ws4.column_dimensions["C"].width = 16

    ws5 = wb.create_sheet("Daily NAV")
    ws5.append(["Date", "Daily Return %", "NAV (INR)", "N Holdings"])
    for c in ws5[1]:
        c.font = hdr_font; c.fill = hdr_fill
    for dt, row in daily_df.iterrows():
        ws5.append([dt.strftime("%Y-%m-%d"), round(row["ret"] * 100, 4), round(row["nav"], 2), int(row["n_holdings"])])
    ws5.column_dimensions["A"].width = 14

    wb.save(os.path.join(OUT_DIR, f"High_Beta_Strategy_Backtest{tag}.xlsx"))
    print(f"\nAll outputs written to ./{OUT_DIR}/")
    return summary


if __name__ == "__main__":
    price, vol = load_data()
    holdings_history, daily_df, bench_window, bench_nav, n_stop_events = run_backtest(price, vol)
    build_outputs(holdings_history, daily_df, bench_window, bench_nav, n_stop_events)
