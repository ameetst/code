"""
backtest_turning_points.py
============================
PROTOTYPE — standalone, does NOT touch live code (momentum_lib.py / Sharpe.py /
backtest_wired.py unchanged). Lives only in prototype_turning_points/.

A near-verbatim copy of `backtest results/backtest_wired.py` with exactly ONE
change: the regime/entry-gate step. The Sharpe ranking logic (COMPOSITE
Z-score, 52H eligibility, rank) and everything downstream of the entry gate
(hold/exit rules, inverse-vol 5% cap sizing, friction, cash parking, output
format) is untouched.

REMOVED : ml.compute_regime_score (4-signal breadth score) -> dynamic_n (5-25)
ADDED   : turning_points_lib SLOW(12M)/FAST(1M) cycle classifier -> binary
          risk_on gate. Buy new positions only when risk_on (Bull or Rebound
          state); otherwise hold existing positions (still subject to normal
          52H/rank exit rules) and park freed capital in cash. Fixed TOP_N=20
          replaces the old dynamic 5-25 sizing.
"""

import sys
import os
import datetime
import warnings

import numpy as np
import pandas as pd
import shutil
import matplotlib.pyplot as plt
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import momentum_lib as ml
import turning_points_lib as tpl

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated.xlsx"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
TOP_N        = 20           # fixed position count (dynamic_n removed with the breadth regime score)
FRICTION     = 0.002        # 0.20% per trade

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily = RFR_ANNUAL / TRADING_DAYS


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


print(f"Loading {FILE} for historical simulation ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

dt_idx = pd.DatetimeIndex(dates)
eow_dates = []
for i in range(len(dt_idx) - 1):
    if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week:
        eow_dates.append(dates[i])
eow_dates.append(dates[-1])

# Need 252 days for 12M Sharpe warmup, PLUS 13 months for the SLOW/FAST classifier
# to produce its first valid state. The larger of the two buffers governs.
start_idx = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"Total trading days available: {len(dates)}")
print(f"Valid rebalance points (week-ends): {len(valid_dates)}")

if len(valid_dates) < 2:
    print("\n[!] ERROR: Insufficient data for backtesting.")
    sys.exit(0)

equity = 2000000.0
nifty_equity = 2000000.0
current_portfolio = {}
results_log = []

prices_df_ffill = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

print("\nStarting Point-in-Time Vectorised Backtest (Turning-Points regime gate):")
print("-" * 80)

n_unknown_state = 0

for i in range(len(valid_dates) - 1):
    t_date = valid_dates[i]
    next_date = valid_dates[i + 1]

    idx = dates.index(t_date)
    next_idx = dates.index(next_date)

    # 1. ── SLICE DATA POINT-IN-TIME ───────────────────────────────────
    sliced_prices = prices_df.iloc[:, :idx + 1]
    sliced_nifty = nifty_series_ffill.iloc[:idx + 1]
    sliced_dates = dates[:idx + 1]

    # 2. ── RUN MOMENTUM LOGIC (silently) — UNCHANGED from backtest_wired.py ──
    with suppress_stdout():
        sharpe_df, z_df = ml.compute_sharpe(sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)

    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h

    core_labels = [l for l in SHARPE_WINDOWS if l != "1M"]
    z_cols = [f"Z_{l}" for l in core_labels]
    result["COMPOSITE"] = z_df[z_cols].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)

    # 3. ── FILTER AND RANK — UNCHANGED ────────────────────────────────
    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(ascending=False, method="first", na_option="bottom")
    elig_df = elig_df.sort_values("RANK", ascending=True)
    top_candidates = elig_df.index.tolist()

    # 4. ── REGIME GATE — REPLACED: SLOW/FAST cycle state instead of the
    #        4-signal breadth regime score ─────────────────────────────
    with suppress_stdout():
        state = tpl.classify_state_asof(sliced_nifty, sliced_dates)
    if state == "Unknown":
        n_unknown_state += 1
    allow_new = tpl.is_risk_on(state)
    dynamic_n = TOP_N
    regime = f"{state} ({'BUY' if allow_new else 'CASH'})"
    is_cash = not allow_new

    # 5. ── PORTFOLIO CONSTRUCTION — UNCHANGED logic, dynamic_n now fixed ──
    next_portfolio_tickers = []

    # Pass 1: retain held stocks (exit evaluation always runs, regime-independent)
    for ticker, pstate in current_portfolio.items():
        entry_date = pstate['entry_date']
        days_held = (t_date - entry_date).days
        if ticker in top_candidates:
            rank = elig_df.loc[ticker, "RANK"]
            pct_from_52 = elig_df.loc[ticker, "PCT_FROM_52H"]
            if pct_from_52 < -25:
                continue  # 52H breach
            if rank <= 40 or days_held < 28:
                next_portfolio_tickers.append(ticker)

    # Pass 2: fill new slots up to TOP_N, gated by risk_on
    if allow_new:
        slots_to_fill = dynamic_n - len(next_portfolio_tickers)
        for ticker in top_candidates:
            if slots_to_fill <= 0:
                break
            if ticker not in next_portfolio_tickers:
                next_portfolio_tickers.append(ticker)
                slots_to_fill -= 1

    # Volatility-Adjusted Weights — UNCHANGED
    raw_weights = {}
    for ticker in next_portfolio_tickers:
        comp_score = result.loc[ticker, "COMPOSITE"]
        px = sliced_prices.loc[ticker].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                px_w = px.iloc[-w:] if len(px) >= w else px
                log_r = np.diff(np.log(px_w.values))
                if len(log_r) > 5:
                    vols.append(np.std(log_r, ddof=1) * np.sqrt(252))
            if vols and np.mean(vols) > 0:
                raw_weights[ticker] = comp_score / np.mean(vols)
            else:
                raw_weights[ticker] = comp_score
        else:
            raw_weights[ticker] = comp_score

    total_raw = sum(raw_weights.values())
    actual_portfolio = {}
    for ticker in next_portfolio_tickers:
        norm_w = raw_weights[ticker] / total_raw if total_raw > 0 else 1.0 / len(next_portfolio_tickers)
        capped_w = min(0.05, norm_w)
        if ticker in current_portfolio:
            actual_portfolio[ticker] = {'entry_date': current_portfolio[ticker]['entry_date'], 'weight': capped_w}
        else:
            actual_portfolio[ticker] = {'entry_date': t_date, 'weight': capped_w}

    # 6. ── RETURNS — UNCHANGED ─────────────────────────────────────────
    if is_cash:
        gross_ret = (1.06 ** (1 / 52)) - 1.0
    else:
        actual_port_list = list(actual_portfolio.keys())
        total_equity_weight = sum(s['weight'] for s in actual_portfolio.values())
        cash_weight = max(0.0, 1.0 - total_equity_weight)

        if actual_port_list:
            start_px = prices_df_ffill.loc[actual_port_list].iloc[:, idx]
            end_px = prices_df_ffill.loc[actual_port_list].iloc[:, next_idx]
            stock_returns = (end_px / start_px) - 1.0
            weights_series = pd.Series({t: actual_portfolio[t]['weight'] for t in actual_port_list})
            gross_ret = (stock_returns * weights_series).sum() + (cash_weight * ((1.06 ** (1 / 52)) - 1.0))
        else:
            gross_ret = (1.06 ** (1 / 52)) - 1.0

        if pd.isna(gross_ret):
            gross_ret = 0.0

    all_tickers = set(current_portfolio.keys()) | set(actual_portfolio.keys())
    abs_weight_change = 0.0
    for t in all_tickers:
        old_w = current_portfolio[t]['weight'] if t in current_portfolio else 0.0
        new_w = actual_portfolio[t]['weight'] if t in actual_portfolio else 0.0
        abs_weight_change += abs(new_w - old_w)

    friction_cost = abs_weight_change * FRICTION
    turnover = abs_weight_change / 2.0
    net_ret = gross_ret - friction_cost

    n_start = nifty_series_ffill.iloc[idx]
    n_end = nifty_series_ffill.iloc[next_idx]
    nifty_ret = (n_end / n_start) - 1.0

    equity *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)

    sys.stdout.write(f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
                      f"Eq: {equity:12,.0f} | State: {state:<10} | "
                      f"Held: {len(actual_portfolio):2d}")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "State": state,
        "Regime": regime,
        "Allow_New": allow_new,
        "Dynamic_N": dynamic_n,
        "Eligible_Count": len(elig_df),
        "Turnover_Pct": turnover * 100,
        "Gross_Return": gross_ret,
        "Net_Return": net_ret,
        "Nifty_Return": nifty_ret,
        "Equity": equity,
        "Nifty_Equity": nifty_equity,
        "Top20_Tickers": ", ".join(actual_portfolio) if actual_portfolio else "CASH",
    })

    current_portfolio = actual_portfolio

print("\n" + "-" * 80)
print("Backtest complete!")
if n_unknown_state:
    print(f"[note] {n_unknown_state} rebalance date(s) had 'Unknown' state (insufficient "
          f"monthly history for SLOW/FAST classification) — treated as risk-off/cash.")

# ── SETUP RUN FOLDER ──────────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", f"run_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

output_csv = os.path.join(run_dir, "backtest_results.csv")
output_png = os.path.join(run_dir, "equity_curve.png")

df_res = pd.DataFrame(results_log)
df_res.to_csv(output_csv, index=False)
print(f"Results saved to {output_csv}")


def compute_drawdown(equity_series):
    roll_max = equity_series.cummax()
    drawdown = (equity_series / roll_max) - 1.0
    return drawdown.min()


years = (valid_dates[-1] - valid_dates[0]).days / 365.25
if years <= 0:
    years = 1.0

p_cagr = ((equity / 2000000.0) ** (1 / years) - 1.0) * 100
n_cagr = ((nifty_equity / 2000000.0) ** (1 / years) - 1.0) * 100

p_mdd = compute_drawdown(df_res["Equity"]) * 100
n_mdd = compute_drawdown(df_res["Nifty_Equity"]) * 100

state_dist = df_res["State"].value_counts()
cash_pct = (df_res["Allow_New"] == False).mean() * 100

print("\n=== PERFORMANCE SUMMARY (Turning-Points regime gate) ===")
print(f"Period: {valid_dates[0].strftime('%b %Y')} to {valid_dates[-2].strftime('%b %Y')} ({years:.2f} years)")
print(f"Strategy CAGR:     {p_cagr:5.1f}%  |  Max Drawdown: {p_mdd:5.1f}%")
print(f"NIFTY500 CAGR:     {n_cagr:5.1f}%  |  Max Drawdown: {n_mdd:5.1f}%")
print(f"Rebalance points in CASH (risk-off): {cash_pct:.1f}%")
print("State distribution across rebalance points:")
print(state_dist)
print("==========================================================\n")

# ── PLOT EQUITY CURVE (existing style + state coloring) ────────────────────
try:
    df_res['Rebalance_Date'] = pd.to_datetime(df_res['Rebalance_Date'])

    plt.figure(figsize=(13, 6))
    plt.plot(df_res['Rebalance_Date'], df_res['Equity'],
              label=f"Strategy (CAGR {p_cagr:.1f}%, MDD {p_mdd:.1f}%)", color='#0055CC', linewidth=2)
    plt.plot(df_res['Rebalance_Date'], df_res['Nifty_Equity'],
              label=f"NIFTY500 (CAGR {n_cagr:.1f}%, MDD {n_mdd:.1f}%)", color='#555555', linewidth=2, linestyle='--')

    state_colors = {"Bull": None, "Bear": "#C62828", "Correction": "#EF6C00", "Rebound": "#66BB6A", "Unknown": "#999999"}
    for state_name, color in state_colors.items():
        if color is None:
            continue
        mask = df_res['State'] == state_name
        for d in df_res.loc[mask, 'Rebalance_Date']:
            plt.axvspan(d, d + pd.Timedelta(days=7), color=color, alpha=0.15, lw=0)

    plt.title('Sharpe Vol-Sized Strategy vs NIFTY500 — Turning-Points regime gate\n'
              '(Red=Bear, Orange=Correction, Green=Rebound background shading; Bull=unshaded)')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Equity (Starting 2M INR)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_png, dpi=200)
    plt.close()
    print(f"Equity curve saved to {output_png}")
except Exception as e:
    print(f"Could not generate equity curve: {e}")

try:
    shutil.copy2(__file__, os.path.join(run_dir, os.path.basename(__file__)))
except Exception:
    pass
