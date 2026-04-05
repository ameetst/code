import sys
import os
import datetime
import warnings

import numpy as np
import pandas as pd
from contextlib import contextmanager

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = "n500.xlsx"
OUTPUT_FILE  = "backtest_results.csv"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
TOP_N        = 20
FRICTION     = 0.002  # 0.20% per trade (0.4% round trip on turnover)

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63, "1M": 21}
rfr_daily = RFR_ANNUAL / TRADING_DAYS

# Temporarily swallows stdout from momentum_lib functions
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

# Map dates to pandas DatetimeIndex for month-end detection
dt_idx = pd.DatetimeIndex(dates)
eom_dates = []

# Detect month-ends (where the month changes compared to the next date)
for i in range(len(dt_idx) - 1):
    if dt_idx[i].month != dt_idx[i+1].month:
        eom_dates.append(dates[i])
# Always evaluate the absolute final day available in the dataset too
eom_dates.append(dates[-1])

# We need 252 days buffer for the first 12M calculation
start_idx = 252
valid_dates = [d for d in eom_dates if dates.index(d) >= start_idx]

print(f"Total trading days available: {len(dates)}")
print(f"Valid rebalance points (month-ends): {len(valid_dates)}")

if len(valid_dates) < 2:
    print("\n[!] ERROR: Insufficient data for backtesting.")
    print("    You need at least 252 days of 'warm-up' data to compute the first 12M Sharpe, ")
    print("    PLUS enough remaining months to actually simulate the portfolio moving forward.")
    print("    Your current n500.xlsx file seems to only contain ~1 year of data.")
    sys.exit(0)

equity = 100.0
nifty_equity = 100.0
current_portfolio = []
results_log = []

# Forward fill prices across the whole sheet to handle missing days/halts
prices_df_ffill = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

print("\nStarting Point-in-Time Vectorised Backtest:")
print("-" * 80)

for i in range(len(valid_dates) - 1):
    t_date    = valid_dates[i]
    next_date = valid_dates[i+1]
    
    idx       = dates.index(t_date)
    next_idx  = dates.index(next_date)
    
    # 1. ── SLICE DATA POINT-IN-TIME ───────────────────────────────────
    sliced_prices = prices_df.iloc[:, :idx+1]
    sliced_nifty  = nifty_series_ffill.iloc[:idx+1]
    
    # 2. ── RUN MOMENTUM LOGIC (silently) ──────────────────────────────
    with suppress_stdout():
        sharpe_df, z_df = ml.compute_sharpe(sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)
        regime = ml.compute_market_regime(sliced_nifty)
        
    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h
    
    # COMPOSITE = SHARPE_ALL (mean of 12M, 9M, 6M, 3M)
    core_labels = [l for l in SHARPE_WINDOWS if l != "1M"]
    z_cols = [f"Z_{l}" for l in core_labels]
    result["COMPOSITE"] = z_df[z_cols].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)
    
    # 3. ── FILTER AND RANK ────────────────────────────────────────────
    eligible = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible].copy()
    
    # In case there are fewer eligible stocks than TOP_N, we take what we can
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(ascending=False, method="first", na_option="bottom")
    elig_df = elig_df.sort_values("RANK", ascending=True)
    
    top_stocks = elig_df.head(TOP_N).index.tolist()
    
    # 4. ── CALCULATE RETURNS ──────────────────────────────────────────
    # Look ahead to next rebalance date
    start_px = prices_df_ffill.loc[top_stocks].iloc[:, idx]
    end_px   = prices_df_ffill.loc[top_stocks].iloc[:, next_idx]
    
    stock_returns = (end_px / start_px) - 1.0
    gross_ret = stock_returns.mean()
    
    # Turnover calculation (new additions as a fraction of the portfolio size)
    new_stocks = set(top_stocks) - set(current_portfolio)
    turnover = len(new_stocks) / TOP_N if current_portfolio else 1.0
    
    # Friction applied to both buys and sells (turnover * 2 * friction_bps)
    net_ret = gross_ret - (turnover * FRICTION * 2) if not pd.isna(gross_ret) else 0.0
    
    # Benchmark return
    n_start   = nifty_series_ffill.iloc[idx]
    n_end     = nifty_series_ffill.iloc[next_idx]
    nifty_ret = (n_end / n_start) - 1.0
    
    # Compounding
    equity *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)
    
    # Print status
    sys.stdout.write(f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
                     f"Eq: {equity:6.1f} | NIFTY: {nifty_equity:6.1f} | "
                     f"Turnover: {turnover*100:3.0f}% | Regime: {regime.split(' ')[0]:<10}")
    sys.stdout.flush()
    
    # Log loop variables
    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "Regime": regime,
        "Eligible_Count": len(elig_df),
        "Turnover_Pct": turnover * 100,
        "Gross_Return": gross_ret,
        "Net_Return": net_ret,
        "Nifty_Return": nifty_ret,
        "Equity": equity,
        "Nifty_Equity": nifty_equity,
        "Top20_Tickers": ", ".join(top_stocks)
    })
    
    current_portfolio = top_stocks

print("\n" + "-" * 80)
print("Backtest complete!")

# ── PERFORMANCE METRICS ───────────────────────────────────────────────────────
df_res = pd.DataFrame(results_log)
df_res.to_csv(OUTPUT_FILE, index=False)
print(f"Results saved to {OUTPUT_FILE}")

def compute_drawdown(equity_series):
    roll_max = equity_series.cummax()
    drawdown = (equity_series / roll_max) - 1.0
    return drawdown.min()

years = (valid_dates[-1] - valid_dates[0]).days / 365.25
if years <= 0:
    years = 1.0  # fallback to avoid div by zero if dates identical

p_cagr = ((equity / 100.0) ** (1 / years) - 1.0) * 100
n_cagr = ((nifty_equity / 100.0) ** (1 / years) - 1.0) * 100

p_mdd = compute_drawdown(df_res["Equity"]) * 100
n_mdd = compute_drawdown(df_res["Nifty_Equity"]) * 100

print("\n=== PERFORMANCE SUMMARY ===")
print(f"Period: {valid_dates[0].strftime('%b %Y')} to {valid_dates[-2].strftime('%b %Y')} ({years:.2f} years)")
print(f"Strategy CAGR:     {p_cagr:5.1f}%  |  Max Drawdown: {p_mdd:5.1f}%")
print(f"NIFTY500 CAGR:     {n_cagr:5.1f}%  |  Max Drawdown: {n_mdd:5.1f}%")
print("===========================\n")
