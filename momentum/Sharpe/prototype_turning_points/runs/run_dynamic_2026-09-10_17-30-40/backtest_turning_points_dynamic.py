"""
backtest_turning_points_dynamic.py
=====================================
PROTOTYPE — standalone, does NOT touch live code. Lives only in
prototype_turning_points/.

"Level C" variant: same as backtest_turning_points.py (same Sharpe ranking,
same exit rules, same friction/cash mechanics), but the binary
allow-new/cash gate is replaced with a continuous EXPOSURE DIAL:

  Bull       -> 100% of normal position sizing
  Bear       -> BEAR_EXPOSURE_FLOOR (15%) — floor, not zero (long-only judgment call)
  Correction -> a_correction  (paper Proposition 9, estimated from history strictly
  Rebound    -> a_rebound      before the current date, shrunk toward 50% by sample size)

New positions are always considered (never hard-gated), but the WHOLE
portfolio's weights are scaled by the state's exposure dial — so a thin
Correction dial means small new positions, not zero.
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
TOP_N        = 20
FRICTION     = 0.002

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
dial_log = []

prices_df_ffill = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

print("\nStarting Point-in-Time Vectorised Backtest (Turning-Points DYNAMIC exposure dial):")
print("-" * 80)

n_unknown_state = 0

for i in range(len(valid_dates) - 1):
    t_date = valid_dates[i]
    next_date = valid_dates[i + 1]

    idx = dates.index(t_date)
    next_idx = dates.index(next_date)

    sliced_prices = prices_df.iloc[:, :idx + 1]
    sliced_nifty = nifty_series_ffill.iloc[:idx + 1]
    sliced_dates = dates[:idx + 1]

    # 2. ── RANKING — UNCHANGED ─────────────────────────────────────────
    with suppress_stdout():
        sharpe_df, z_df = ml.compute_sharpe(sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)

    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h
    core_labels = [l for l in SHARPE_WINDOWS if l != "1M"]
    z_cols = [f"Z_{l}" for l in core_labels]
    result["COMPOSITE"] = z_df[z_cols].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)

    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(ascending=False, method="first", na_option="bottom")
    elig_df = elig_df.sort_values("RANK", ascending=True)
    top_candidates = elig_df.index.tolist()

    # 4. ── REGIME: state + dynamic exposure dial ──────────────────────
    with suppress_stdout():
        state = tpl.classify_state_asof(sliced_nifty, sliced_dates)
        dial = tpl.dynamic_blend_a_asof(sliced_nifty, sliced_dates)
    if state == "Unknown":
        n_unknown_state += 1
    exposure = tpl.state_exposure_dynamic(state, dial)
    regime = f"{state} (exp={exposure:.2f})"

    dial_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"), "State": state, "Exposure": exposure,
        "K": dial["diagnostics"].get("K"), "n_bull": dial["diagnostics"].get("n_bull"),
        "n_bear": dial["diagnostics"].get("n_bear"),
        **{f"Co_{k}": v for k, v in dial["diagnostics"].get("Correction", {}).items()},
        **{f"Re_{k}": v for k, v in dial["diagnostics"].get("Rebound", {}).items()},
    })

    # 5. ── PORTFOLIO CONSTRUCTION — always fill to TOP_N, THEN scale by exposure ──
    next_portfolio_tickers = []

    # Pass 1: retain held stocks — exit evaluation always runs, regime-independent
    for ticker, pstate in current_portfolio.items():
        entry_date = pstate['entry_date']
        days_held = (t_date - entry_date).days
        if ticker in top_candidates:
            rank = elig_df.loc[ticker, "RANK"]
            pct_from_52 = elig_df.loc[ticker, "PCT_FROM_52H"]
            if pct_from_52 < -25:
                continue
            if rank <= 40 or days_held < 28:
                next_portfolio_tickers.append(ticker)

    # Pass 2: always fill to TOP_N (no hard gate — exposure scaling happens after sizing)
    slots_to_fill = TOP_N - len(next_portfolio_tickers)
    for ticker in top_candidates:
        if slots_to_fill <= 0:
            break
        if ticker not in next_portfolio_tickers:
            next_portfolio_tickers.append(ticker)
            slots_to_fill -= 1

    # Volatility-Adjusted Weights — UNCHANGED base sizing
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
        scaled_w = capped_w * exposure   # <-- the Level C exposure dial applied here
        if ticker in current_portfolio:
            actual_portfolio[ticker] = {'entry_date': current_portfolio[ticker]['entry_date'], 'weight': scaled_w}
        else:
            actual_portfolio[ticker] = {'entry_date': t_date, 'weight': scaled_w}

    # 6. ── RETURNS — UNCHANGED mechanics, just always the "invested" branch
    #        since exposure scaling already handles the cash fraction ──────
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
                      f"Eq: {equity:12,.0f} | State: {state:<10} | Exp: {exposure:.2f} | "
                      f"Held: {len(actual_portfolio):2d}")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "State": state,
        "Regime": regime,
        "Exposure": exposure,
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
    print(f"[note] {n_unknown_state} rebalance date(s) had 'Unknown' state — treated as defensive floor.")

# ── SETUP RUN FOLDER ──────────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", f"run_dynamic_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

output_csv = os.path.join(run_dir, "backtest_results.csv")
output_png = os.path.join(run_dir, "equity_curve.png")
dial_csv = os.path.join(run_dir, "dial_diagnostics.csv")

df_res = pd.DataFrame(results_log)
df_res.to_csv(output_csv, index=False)
pd.DataFrame(dial_log).to_csv(dial_csv, index=False)
print(f"Results saved to {output_csv}")
print(f"Dial diagnostics saved to {dial_csv}")


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

avg_exposure = df_res["Exposure"].mean()

print("\n=== PERFORMANCE SUMMARY (Turning-Points DYNAMIC exposure dial) ===")
print(f"Period: {valid_dates[0].strftime('%b %Y')} to {valid_dates[-2].strftime('%b %Y')} ({years:.2f} years)")
print(f"Strategy CAGR:     {p_cagr:5.1f}%  |  Max Drawdown: {p_mdd:5.1f}%")
print(f"NIFTY500 CAGR:     {n_cagr:5.1f}%  |  Max Drawdown: {n_mdd:5.1f}%")
print(f"Average exposure across all rebalance points: {avg_exposure:.1%}")
print("State distribution:")
print(df_res["State"].value_counts())
print("=====================================================================\n")

try:
    df_res['Rebalance_Date'] = pd.to_datetime(df_res['Rebalance_Date'])

    fig, axes = plt.subplots(2, 1, figsize=(13, 8), gridspec_kw={"height_ratios": [3, 1]})
    ax = axes[0]
    ax.plot(df_res['Rebalance_Date'], df_res['Equity'],
            label=f"Strategy (CAGR {p_cagr:.1f}%, MDD {p_mdd:.1f}%)", color='#8E24AA', linewidth=2)
    ax.plot(df_res['Rebalance_Date'], df_res['Nifty_Equity'],
            label=f"NIFTY500 (CAGR {n_cagr:.1f}%, MDD {n_mdd:.1f}%)", color='#555555', linewidth=2, linestyle='--')
    ax.set_title('Sharpe Vol-Sized Strategy vs NIFTY500 — Turning-Points DYNAMIC exposure dial (Level C)')
    ax.set_ylabel('Portfolio Equity (Starting 2M INR)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.fill_between(df_res['Rebalance_Date'], df_res['Exposure'], alpha=0.4, color='#8E24AA')
    ax2.set_ylabel('Exposure dial')
    ax2.set_xlabel('Date')
    ax2.set_ylim(0, 1.05)
    ax2.grid(True, alpha=0.2)

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
