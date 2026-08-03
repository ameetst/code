"""
Early Mover Streak Backtest
============================
Entry : Composite score streak > 4 (5+ consecutive weeks of rising score)
Exit  : 10% Trailing Stop Loss (from peak price since entry)
        Also exits on 52H breach (PCT_FROM_52H < -25%)

Position limit : 20 stocks max
Position sizing: Inverse-volatility weighted, 5% cap
Re-entry       : Allowed after TSL exit
Benchmark      : Nifty 500 buy-and-hold
Data           : n500_bt.xlsx (Apr 2019 → Apr 2026)

Usage: python backtest_streak.py
"""

import sys
import os
import datetime
import shutil
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import momentum_lib as ml

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE            = "n500_bt.xlsx"
STARTING_EQUITY = 2_000_000
MAX_POSITIONS   = 20
MAX_WEIGHT      = 0.05          # 5% cap per position
STREAK_THRESHOLD = 4            # enter when streak > 4 (i.e. >= 5)
TSL_PCT         = 0.10          # 10% trailing stop loss
FRICTION        = 0.0020        # 20 bps round-trip slippage+brokerage
RFR_ANNUAL      = 0.07
TRADING_DAYS    = 252
WINDOWS         = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
LIQUID_YIELD    = 0.06          # 6% p.a. on idle cash

rfr_daily = RFR_ANNUAL / TRADING_DAYS

# ── HELPERS ───────────────────────────────────────────────────────────────────
@contextmanager
def suppress_stdout():
    import io
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old


def compute_streak(score_history):
    """Count consecutive rising entries from the tail of the list."""
    if len(score_history) < 2:
        return 0
    streak = 0
    for i in range(len(score_history) - 1, 0, -1):
        if score_history[i] > score_history[i - 1]:
            streak += 1
        else:
            break
    return streak


def compute_inv_vol_weights(tickers, prices_slice, result_df, max_weight):
    """Inverse-volatility weighted allocation, capped at max_weight."""
    raw_w = {}
    for t in tickers:
        if t not in prices_slice.index:
            continue
        px = prices_slice.loc[t].dropna()
        comp = result_df.loc[t, "COMPOSITE"] if t in result_df.index else 1.0
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                pw = px.iloc[-w:] if len(px) >= w else px
                lr = np.diff(np.log(pw.values))
                if len(lr) > 5:
                    vols.append(np.std(lr, ddof=1) * np.sqrt(252))
            if vols and np.mean(vols) > 0:
                raw_w[t] = comp / np.mean(vols)
            else:
                raw_w[t] = comp
        else:
            raw_w[t] = comp

    total = sum(raw_w.values())
    weights = {}
    for t in raw_w:
        nw = raw_w[t] / total if total > 0 else 1.0 / len(raw_w)
        weights[t] = min(max_weight, nw)
    return weights


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print(f"Loading {FILE} for streak backtest ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
prices_df_ffill = prices_df.ffill(axis=1)
nifty_ffill = nifty_series.ffill()

print(f"Total trading days: {len(dates)}")
print(f"Date range: {dates[0].strftime('%Y-%m-%d')} → {dates[-1].strftime('%Y-%m-%d')}")
print(f"Tickers: {len(stock_tickers)}")

# ── DETECT WEEK-END DATES ────────────────────────────────────────────────────
dt_idx = pd.DatetimeIndex(dates)
eow_dates = []
for i in range(len(dt_idx) - 1):
    if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week:
        eow_dates.append(dates[i])
eow_dates.append(dates[-1])

# Need 252 days warm-up for 12M Sharpe + a few extra weeks for streak build-up
START_IDX = 252 + 10  # extra buffer for streak history
valid_dates = [d for d in eow_dates if dates.index(d) >= START_IDX]

print(f"Valid rebalance points: {len(valid_dates)}")
if len(valid_dates) < 10:
    print("[!] Not enough data for meaningful backtest.")
    sys.exit(1)

# ── BACKTEST LOOP ─────────────────────────────────────────────────────────────
equity = STARTING_EQUITY
nifty_equity = STARTING_EQUITY

# Portfolio: {ticker: {entry_date, weight, peak_price, entry_price}}
current_portfolio = {}

# Score history for streak computation: {ticker: [score_at_each_rebalance]}
score_history_all = {t: [] for t in stock_tickers}

results_log = []
trade_log = []  # individual trades

print("\nStarting Early Mover Streak Backtest:")
print("-" * 80)

for i in range(len(valid_dates) - 1):
    t_date = valid_dates[i]
    next_date = valid_dates[i + 1]

    idx = dates.index(t_date)
    next_idx = dates.index(next_date)

    # 1. ── SLICE DATA POINT-IN-TIME ──────────────────────────────────────
    sliced_prices = prices_df.iloc[:, :idx + 1]

    # 2. ── COMPUTE RANKINGS ──────────────────────────────────────────────
    with suppress_stdout():
        sharpe_df, z_df = ml.compute_sharpe(
            sliced_prices, stock_tickers, WINDOWS, rfr_daily, TRADING_DAYS)
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)

    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h

    core_labels = [l for l in WINDOWS if l != "1M"]
    z_cols = [f"Z_{l}" for l in core_labels]
    result["COMPOSITE"] = z_df[z_cols].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)

    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(
        ascending=False, method="first", na_option="bottom")

    # 3. ── UPDATE SCORE HISTORY & COMPUTE STREAKS ────────────────────────
    for t in stock_tickers:
        val = result["COMPOSITE"].get(t)
        if val is not None and pd.notna(val):
            score_history_all[t].append(float(val))
        else:
            score_history_all[t].append(np.nan)

    # Compute streaks for all eligible tickers
    streaks = {}
    for t in stock_tickers:
        clean = [v for v in score_history_all[t] if not np.isnan(v)]
        streaks[t] = compute_streak(clean)

    # 4. ── EXIT EVALUATION (TSL + 52H) ───────────────────────────────────
    exits_this_week = []
    for ticker, state in list(current_portfolio.items()):
        # Update peak price using daily data between last and current rebalance
        prev_idx = dates.index(valid_dates[max(0, i - 1)]) if i > 0 else idx
        daily_highs = prices_df_ffill.loc[ticker].iloc[prev_idx:idx + 1]
        max_daily = daily_highs.max()
        if pd.notna(max_daily) and max_daily > state["peak_price"]:
            state["peak_price"] = max_daily

        current_price = prices_df_ffill.loc[ticker].iloc[idx]
        if pd.isna(current_price):
            continue

        # TSL check: drop from peak
        drawdown_from_peak = (current_price - state["peak_price"]) / state["peak_price"]

        # 52H check
        pct52 = pct_52h.get(ticker, 0.0) if ticker in pct_52h.index else 0.0

        exit_reason = None
        if drawdown_from_peak <= -TSL_PCT:
            exit_reason = "TSL_EXIT"
        elif pd.notna(pct52) and pct52 < -25:
            exit_reason = "52H_BREACH"

        if exit_reason:
            pnl_pct = (current_price / state["entry_price"] - 1.0) * 100
            trade_log.append({
                "Date": t_date.strftime("%Y-%m-%d"),
                "Ticker": ticker,
                "Action": "SELL",
                "Reason": exit_reason,
                "Entry Price": state["entry_price"],
                "Exit Price": current_price,
                "Peak Price": state["peak_price"],
                "PnL %": round(pnl_pct, 2),
                "Days Held": (t_date - state["entry_date"]).days,
            })
            exits_this_week.append(ticker)

    for t in exits_this_week:
        del current_portfolio[t]

    # 5. ── ENTRY EVALUATION ──────────────────────────────────────────────
    slots_available = MAX_POSITIONS - len(current_portfolio)
    entries_this_week = []

    if slots_available > 0:
        # Find all eligible stocks with streak > STREAK_THRESHOLD, not already held
        candidates = []
        for t in elig_df.index:
            if t in current_portfolio:
                continue
            if streaks.get(t, 0) > STREAK_THRESHOLD:
                candidates.append((t, streaks[t], elig_df.loc[t, "COMPOSITE"]))

        # Sort by streak (desc), then composite (desc)
        candidates.sort(key=lambda x: (-x[1], -x[2]))
        candidates = candidates[:slots_available]

        if candidates:
            cand_tickers = [c[0] for c in candidates]
            cand_weights = compute_inv_vol_weights(
                cand_tickers, sliced_prices, result, MAX_WEIGHT)

            for t in cand_tickers:
                if t not in cand_weights:
                    continue
                entry_price = prices_df_ffill.loc[t].iloc[idx]
                if pd.isna(entry_price) or entry_price <= 0:
                    continue

                current_portfolio[t] = {
                    "entry_date": t_date,
                    "entry_price": entry_price,
                    "peak_price": entry_price,
                    "weight": cand_weights[t],
                }
                entries_this_week.append(t)

                trade_log.append({
                    "Date": t_date.strftime("%Y-%m-%d"),
                    "Ticker": t,
                    "Action": "BUY",
                    "Reason": f"STREAK_{streaks[t]}",
                    "Entry Price": entry_price,
                    "Exit Price": None,
                    "Peak Price": entry_price,
                    "PnL %": None,
                    "Days Held": None,
                })

    # 6. ── REBALANCE WEIGHTS for entire portfolio ────────────────────────
    if current_portfolio:
        all_held = list(current_portfolio.keys())
        new_weights = compute_inv_vol_weights(
            all_held, sliced_prices, result, MAX_WEIGHT)
        for t in all_held:
            if t in new_weights:
                current_portfolio[t]["weight"] = new_weights[t]

    # 7. ── CALCULATE RETURNS ─────────────────────────────────────────────
    if current_portfolio:
        port_tickers = list(current_portfolio.keys())
        total_equity_wt = sum(s["weight"] for s in current_portfolio.values())
        cash_wt = max(0.0, 1.0 - total_equity_wt)

        start_px = prices_df_ffill.loc[port_tickers].iloc[:, idx]
        end_px = prices_df_ffill.loc[port_tickers].iloc[:, next_idx]
        stock_rets = (end_px / start_px) - 1.0

        wt_series = pd.Series(
            {t: current_portfolio[t]["weight"] for t in port_tickers})
        gross_ret = (stock_rets * wt_series).sum() + \
                    cash_wt * ((1 + LIQUID_YIELD) ** (1 / 52) - 1.0)

        if pd.isna(gross_ret):
            gross_ret = 0.0
    else:
        gross_ret = (1 + LIQUID_YIELD) ** (1 / 52) - 1.0
        cash_wt = 1.0

    # Turnover / friction
    old_tickers = set(current_portfolio.keys()) - set(entries_this_week)
    abs_wt_chg = sum(current_portfolio[t]["weight"]
                     for t in entries_this_week if t in current_portfolio)
    abs_wt_chg += sum(0.05 for t in exits_this_week)  # approx old weight
    friction = abs_wt_chg * FRICTION
    net_ret = gross_ret - friction

    # Benchmark
    n_start = nifty_ffill.iloc[idx]
    n_end = nifty_ffill.iloc[next_idx]
    nifty_ret = (n_end / n_start) - 1.0

    equity *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)

    # Update peak prices for held positions using next period's daily data
    for ticker, state in current_portfolio.items():
        daily_data = prices_df_ffill.loc[ticker].iloc[idx:next_idx + 1]
        period_max = daily_data.max()
        if pd.notna(period_max) and period_max > state["peak_price"]:
            state["peak_price"] = period_max

    # Progress
    sys.stdout.write(
        f"\r  [{i + 1}/{len(valid_dates) - 1}] {t_date.strftime('%b %Y')} | "
        f"Eq: {equity:12,.0f} | Held: {len(current_portfolio):2d} | "
        f"Entries: {len(entries_this_week)} | Exits: {len(exits_this_week)}")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "Equity": equity,
        "Nifty_Equity": nifty_equity,
        "Held": len(current_portfolio),
        "Entries": len(entries_this_week),
        "Exits": len(exits_this_week),
        "Cash_Wt": round(cash_wt * 100, 1),
        "Gross_Return": gross_ret,
        "Net_Return": net_ret,
        "Nifty_Return": nifty_ret,
        "Tickers": ", ".join(sorted(current_portfolio.keys())) if current_portfolio else "CASH",
    })

print("\n" + "-" * 80)
print("Backtest complete!")

# ── SAVE RESULTS ──────────────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = os.path.join("backtest results", f"streak_run_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

df_res = pd.DataFrame(results_log)
df_res.to_csv(os.path.join(run_dir, "backtest_results.csv"), index=False)

df_trades = pd.DataFrame(trade_log)
df_trades.to_csv(os.path.join(run_dir, "trade_log.csv"), index=False)

# ── PERFORMANCE METRICS ──────────────────────────────────────────────────────
def compute_drawdown(eq_series):
    roll_max = eq_series.cummax()
    dd = (eq_series / roll_max) - 1.0
    return dd.min()

years = (valid_dates[-1] - valid_dates[0]).days / 365.25
if years <= 0:
    years = 1.0

p_cagr = ((equity / STARTING_EQUITY) ** (1 / years) - 1.0) * 100
n_cagr = ((nifty_equity / STARTING_EQUITY) ** (1 / years) - 1.0) * 100

p_mdd = compute_drawdown(df_res["Equity"]) * 100
n_mdd = compute_drawdown(df_res["Nifty_Equity"]) * 100

# Win rate from trade log
sells = df_trades[df_trades["Action"] == "SELL"]
n_trades = len(sells)
n_wins = len(sells[sells["PnL %"] > 0]) if n_trades > 0 else 0
win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0
avg_pnl = sells["PnL %"].mean() if n_trades > 0 else 0
avg_win = sells[sells["PnL %"] > 0]["PnL %"].mean() if n_wins > 0 else 0
avg_loss = sells[sells["PnL %"] <= 0]["PnL %"].mean() if (n_trades - n_wins) > 0 else 0
avg_hold = sells["Days Held"].mean() if n_trades > 0 else 0

print("\n" + "=" * 60)
print("  EARLY MOVER STREAK BACKTEST — PERFORMANCE SUMMARY")
print("=" * 60)
print(f"  Period         : {valid_dates[0].strftime('%b %Y')} → "
      f"{valid_dates[-1].strftime('%b %Y')} ({years:.1f} years)")
print(f"  Data           : {FILE} ({len(stock_tickers)} stocks)")
print(f"  Entry Rule     : Composite streak > {STREAK_THRESHOLD}")
print(f"  Exit Rule      : {TSL_PCT:.0%} Trailing Stop Loss + 52H breach")
print(f"  Max Positions  : {MAX_POSITIONS}")
print("-" * 60)
print(f"  Strategy CAGR  : {p_cagr:6.1f}%    Max Drawdown: {p_mdd:6.1f}%")
print(f"  Nifty500 CAGR  : {n_cagr:6.1f}%    Max Drawdown: {n_mdd:6.1f}%")
print(f"  Alpha          : {p_cagr - n_cagr:+6.1f}%")
print("-" * 60)
print(f"  Total Trades   : {n_trades}")
print(f"  Win Rate       : {win_rate:5.1f}%  ({n_wins}/{n_trades})")
print(f"  Avg PnL/Trade  : {avg_pnl:+5.1f}%")
print(f"  Avg Winner     : {avg_win:+5.1f}%")
print(f"  Avg Loser      : {avg_loss:+5.1f}%")
print(f"  Avg Hold Period: {avg_hold:.0f} days")
print(f"  Final Equity   : Rs {equity:,.0f}")
print("=" * 60)

# ── EQUITY CURVE ──────────────────────────────────────────────────────────────
try:
    df_res["Rebalance_Date"] = pd.to_datetime(df_res["Rebalance_Date"])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9),
                                    gridspec_kw={"height_ratios": [3, 1]})

    # Equity curve
    ax1.plot(df_res["Rebalance_Date"], df_res["Equity"],
             label=f"Streak Strategy (CAGR {p_cagr:.1f}%, MDD {p_mdd:.1f}%)",
             color="#0055CC", linewidth=2)
    ax1.plot(df_res["Rebalance_Date"], df_res["Nifty_Equity"],
             label=f"Nifty 500 (CAGR {n_cagr:.1f}%, MDD {n_mdd:.1f}%)",
             color="#888888", linewidth=2, linestyle="--")
    ax1.set_title("Early Mover Streak Strategy vs Nifty 500\n"
                   f"Entry: Streak > {STREAK_THRESHOLD}  |  "
                   f"Exit: {TSL_PCT:.0%} TSL  |  Max {MAX_POSITIONS} positions",
                   fontsize=13)
    ax1.set_ylabel("Portfolio Equity (₹)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, p: f"₹{x / 1e6:.1f}M" if x >= 1e6 else f"₹{x:,.0f}"))

    # Holdings count
    ax2.fill_between(df_res["Rebalance_Date"], df_res["Held"],
                      color="#0055CC", alpha=0.3, label="# Held")
    ax2.set_ylabel("Positions Held")
    ax2.set_xlabel("Date")
    ax2.set_ylim(0, MAX_POSITIONS + 2)
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    chart_path = os.path.join(run_dir, "equity_curve.png")
    plt.savefig(chart_path, dpi=300)
    plt.close()
    print(f"\nEquity curve saved to {chart_path}")
except Exception as e:
    print(f"\nCould not generate chart: {e}")

# Archive script
try:
    shutil.copy2(__file__, os.path.join(run_dir, "backtest_streak.py"))
except:
    pass

print(f"All results saved to: {run_dir}/")
