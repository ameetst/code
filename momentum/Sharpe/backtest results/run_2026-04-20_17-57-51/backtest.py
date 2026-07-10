import sys
import os
import datetime
import warnings

import numpy as np
import pandas as pd
import shutil
import matplotlib.pyplot as plt
from contextlib import contextmanager

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = "n500_bt.xlsx"
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

# Map dates to pandas DatetimeIndex for week-end detection
dt_idx = pd.DatetimeIndex(dates)
eow_dates = []

# Detect week-ends (where the week changes compared to the next date)
for i in range(len(dt_idx) - 1):
    if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week:
        eow_dates.append(dates[i])
# Always evaluate the absolute final day available in the dataset too
eow_dates.append(dates[-1])

# We need 252 days buffer for the first 12M calculation
start_idx = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"Total trading days available: {len(dates)}")
print(f"Valid rebalance points (week-ends): {len(valid_dates)}")

if len(valid_dates) < 2:
    print("\n[!] ERROR: Insufficient data for backtesting.")
    print("    You need at least 252 days of 'warm-up' data to compute the first 12M Sharpe, ")
    print("    PLUS enough remaining months to actually simulate the portfolio moving forward.")
    print("    Your current n500.xlsx file seems to only contain ~1 year of data.")
    sys.exit(0)

equity = 100.0
nifty_equity = 100.0
current_portfolio = {} # dict of ticker: entry_date
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
        regime, is_cash = ml.compute_market_regime(sliced_nifty)
        
    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h
    
    # COMPOSITE = SHARPE_ALL (mean of 12M, 9M, 6M, 3M)
    core_labels = [l for l in SHARPE_WINDOWS if l != "1M"]
    z_cols = [f"Z_{l}" for l in core_labels]
    result["COMPOSITE"] = z_df[z_cols].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)
    
    # 3. ── FILTER AND RANK ────────────────────────────────────────────
    # Compute base ranks for everyone eligible (52H >= -25)
    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(ascending=False, method="first", na_option="bottom")
    elig_df = elig_df.sort_values("RANK", ascending=True)
    
    top_candidates = elig_df.index.tolist()
    
    # Apply Hysteresis Buffer and Regime constraints
    next_portfolio_tickers = []
    
    if not is_cash:
        # Pass 1: Keep existing stocks if they are still OK
        for ticker, entry_date in current_portfolio.items():
            days_held = (t_date - entry_date).days
            if ticker in top_candidates:
                rank = elig_df.loc[ticker, "RANK"]
                pct_from_52 = elig_df.loc[ticker, "PCT_FROM_52H"]
                
                # Check emergency stop loss (Bypasses hold time lock)
                if pct_from_52 < -25:
                    continue # Discard
                
                # Check hold period vs Rank
                if rank <= 40:
                    next_portfolio_tickers.append(ticker)
                elif days_held < 28:
                    next_portfolio_tickers.append(ticker) # Locked in due to 1-month rule
        
        # Pass 2: Fill empty slots up to TOP_N, ONLY if in a strong BUY regime
        if regime.startswith("BUY"):
            slots_to_fill = TOP_N - len(next_portfolio_tickers)
            for ticker in top_candidates:
                if slots_to_fill <= 0:
                    break
                if ticker not in next_portfolio_tickers:
                    next_portfolio_tickers.append(ticker)
                    slots_to_fill -= 1
                    
    # Build updated portfolio state dictionary
    actual_portfolio = {}
    for ticker in next_portfolio_tickers:
        if ticker in current_portfolio:
            actual_portfolio[ticker] = current_portfolio[ticker]
        else:
            actual_portfolio[ticker] = t_date
    
    # 4. ── CALCULATE RETURNS ──────────────────────────────────────────
    if is_cash:
        # Cash/Liquid Funds regime (~2% p.a. mapped to weekly return)
        gross_ret = (1.02 ** (1/52)) - 1.0
    else:
        # Look ahead to next rebalance date
        actual_port_list = list(actual_portfolio.keys())
        if actual_port_list:
            start_px = prices_df_ffill.loc[actual_port_list].iloc[:, idx]
            end_px   = prices_df_ffill.loc[actual_port_list].iloc[:, next_idx]
            stock_returns = (end_px / start_px) - 1.0
            gross_ret = stock_returns.mean()
        else:
            gross_ret = 0.0
            
        if pd.isna(gross_ret):
            gross_ret = 0.0
    
    # Turnover and Slippage calculation (based on set of keys)
    prev_set = set(current_portfolio.keys())
    new_set  = set(actual_portfolio.keys())
    
    if prev_set and not new_set:
        turnover = 1.0 # Sold everything to go to cash
        friction_cost = turnover * FRICTION # Only selling
    elif not prev_set and new_set:
        turnover = 1.0 # Bought entirely new portfolio from cash
        friction_cost = turnover * FRICTION # Only buying
    else:
        new_stocks = new_set - prev_set
        turnover = len(new_stocks) / TOP_N if prev_set else 1.0
        friction_cost = turnover * FRICTION * 2 # Standard swap cost (sell + buy)
        
    net_ret = gross_ret - friction_cost
    
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
        "Top20_Tickers": ", ".join(actual_portfolio) if actual_portfolio else "CASH"
    })
    
    current_portfolio = actual_portfolio

print("\n" + "-" * 80)
print("Backtest complete!")

# ── SETUP RUN FOLDER ──────────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = os.path.join("backtest results", f"run_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

output_csv = os.path.join(run_dir, "backtest_results.csv")
output_png = os.path.join(run_dir, "equity_curve.png")

# ── PERFORMANCE METRICS ───────────────────────────────────────────────────────
df_res = pd.DataFrame(results_log)
df_res.to_csv(output_csv, index=False)
print(f"Results saved to {output_csv}")

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

# ── PLOT EQUITY CURVE ─────────────────────────────────────────────────────────
try:
    df_res['Rebalance_Date'] = pd.to_datetime(df_res['Rebalance_Date'])
    
    plt.figure(figsize=(12, 6))
    plt.plot(df_res['Rebalance_Date'], df_res['Equity'], label=f"Strategy (CAGR {p_cagr:.1f}%)", color='#0055CC', linewidth=2)
    plt.plot(df_res['Rebalance_Date'], df_res['Nifty_Equity'], label=f"NIFTY500 (CAGR {n_cagr:.1f}%)", color='#555555', linewidth=2, linestyle='--')
    
    # Highlight CASH regime periods
    cash_dates = df_res[df_res['Regime'].str.contains("CASH", na=False)]['Rebalance_Date']
    for cd in cash_dates:
        plt.axvspan(cd, cd + pd.Timedelta(days=30), color='red', alpha=0.1, lw=0)
        
    plt.title('Sharpe Momentum Strategy vs NIFTY500\n(Red shading = CASH Regime)')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Equity (Base 100)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_png, dpi=300)
    plt.close()
    print(f"Equity curve saved to {output_png}")
except Exception as e:
    print(f"Could not generate equity curve: {e}")

# Copy the script itself into the run folder for archiving
try:
    shutil.copy2(__file__, os.path.join(run_dir, "backtest.py"))
except:
    pass

