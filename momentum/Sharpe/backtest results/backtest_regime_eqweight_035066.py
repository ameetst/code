"""
SANDBOX backtest — does NOT touch any live code (Sharpe.py / momentum_lib.py
unchanged). Paired-combination variant: equal-weighted regime signals
(25/25/25/25, instead of the live default 35/25/25/15) TOGETHER WITH
NEW_ENTRY_THRESHOLD=0.35 / DYN_N_FULL_SCORE=0.66 (instead of the live
0.40/0.75, matching the repo's backtest_wired_035_066.py precedent).
Momentum breadth threshold stays at the live hardcoded 1.5 (unchanged) —
this isolates "equal-weight + 0.35/0.66" from the third change (threshold)
tested separately in backtest_regime_thresh1_035066.py and all three
together in backtest_regime_combined.py.
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

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated.xlsx"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
TOP_N        = 20           # kept for reference; actual N is dynamic
FRICTION     = 0.002        # 0.20% per trade

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily = RFR_ANNUAL / TRADING_DAYS

# ── DYNAMIC REGIME PARAMETERS ───────────────────────────────────────────────
MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.35   # live default: 0.40
DYN_N_FULL_SCORE    = 0.66   # live default: 0.75
# Equal-weighted signals instead of ml.DEFAULT_SIGNAL_WEIGHTS (35/25/25/15).
SIGNAL_WEIGHTS = {
    "ema50_breadth":     0.25,
    "ema_trend_breadth": 0.25,
    "breadth":           0.25,
    "momentum":          0.25,
}

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
    if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week:
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

prices_df_ffill = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

print(f"\nStarting Point-in-Time Vectorised Backtest (Equal-Weighted Signals + entry={NEW_ENTRY_THRESHOLD}/full={DYN_N_FULL_SCORE}):")
print("-" * 80)

for i in range(len(valid_dates) - 1):
    t_date    = valid_dates[i]
    next_date = valid_dates[i+1]

    idx       = dates.index(t_date)
    next_idx  = dates.index(next_date)

    sliced_prices = prices_df.iloc[:, :idx+1]
    sliced_nifty  = nifty_series_ffill.iloc[:idx+1]

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

    with suppress_stdout():
        regime_score, regime_detail = ml.compute_regime_score(
            sliced_nifty,
            eligible_mask,
            result["COMPOSITE"],
            prices_df=sliced_prices,
            signal_weights=SIGNAL_WEIGHTS,
            min_n=MIN_N,
            max_n=MAX_N,
            new_entry_threshold=NEW_ENTRY_THRESHOLD,
            dyn_n_full_score=DYN_N_FULL_SCORE,
        )
    allow_new = regime_detail["allow_new"]
    dynamic_n = regime_detail["dynamic_n"]
    is_cash = not allow_new
    regime = f"BUY ({regime_score:.2f})" if allow_new else f"CASH ({regime_score:.2f})"

    next_portfolio_tickers = []

    for ticker, state in current_portfolio.items():
        entry_date  = state['entry_date']
        days_held   = (t_date - entry_date).days
        if ticker in top_candidates:
            rank        = elig_df.loc[ticker, "RANK"]
            pct_from_52 = elig_df.loc[ticker, "PCT_FROM_52H"]
            if pct_from_52 < -25:
                continue
            if rank <= 40 or days_held < 28:
                next_portfolio_tickers.append(ticker)

    if allow_new:
        slots_to_fill = dynamic_n - len(next_portfolio_tickers)
        for ticker in top_candidates:
            if slots_to_fill <= 0:
                break
            if ticker not in next_portfolio_tickers:
                next_portfolio_tickers.append(ticker)
                slots_to_fill -= 1

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
                    vols.append( np.std(log_r, ddof=1) * np.sqrt(252) )
            if vols and np.mean(vols) > 0:
                mean_vol = np.mean(vols)
                raw_weights[ticker] = comp_score / mean_vol
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

    if is_cash:
        gross_ret = (1.06 ** (1/52)) - 1.0
    else:
        actual_port_list = list(actual_portfolio.keys())
        total_equity_weight = sum([state['weight'] for state in actual_portfolio.values()])
        cash_weight = max(0.0, 1.0 - total_equity_weight)

        if actual_port_list:
            start_px = prices_df_ffill.loc[actual_port_list].iloc[:, idx]
            end_px   = prices_df_ffill.loc[actual_port_list].iloc[:, next_idx]
            stock_returns = (end_px / start_px) - 1.0

            weights_series = pd.Series({t: actual_portfolio[t]['weight'] for t in actual_port_list})
            gross_ret = (stock_returns * weights_series).sum() + (cash_weight * ((1.06 ** (1/52)) - 1.0))
        else:
            gross_ret = (1.06 ** (1/52)) - 1.0

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

    n_start   = nifty_series_ffill.iloc[idx]
    n_end     = nifty_series_ffill.iloc[next_idx]
    nifty_ret = (n_end / n_start) - 1.0

    equity *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)

    sys.stdout.write(f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
                     f"Eq: {equity:12,.0f} | RS: {regime_score:.2f} | N={dynamic_n:2d} | "
                     f"Held: {len(actual_portfolio):2d}")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "Regime": regime,
        "Dynamic_N": dynamic_n,
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

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"run_regime_eqweight_035066_{timestamp}")
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

print(f"\n=== PERFORMANCE SUMMARY (Equal-Weighted 25/25/25/25 + entry={NEW_ENTRY_THRESHOLD}/full={DYN_N_FULL_SCORE}) ===")
print(f"Period: {valid_dates[0].strftime('%b %Y')} to {valid_dates[-2].strftime('%b %Y')} ({years:.2f} years)")
print(f"Strategy CAGR:     {p_cagr:5.1f}%  |  Max Drawdown: {p_mdd:5.1f}%")
print(f"NIFTY500 CAGR:     {n_cagr:5.1f}%  |  Max Drawdown: {n_mdd:5.1f}%")
print("===========================\n")

try:
    df_res['Rebalance_Date'] = pd.to_datetime(df_res['Rebalance_Date'])

    plt.figure(figsize=(12, 6))
    plt.plot(df_res['Rebalance_Date'], df_res['Equity'], label=f"Strategy (CAGR {p_cagr:.1f}%)", color='#0055CC', linewidth=2)
    plt.plot(df_res['Rebalance_Date'], df_res['Nifty_Equity'], label=f"NIFTY500 (CAGR {n_cagr:.1f}%)", color='#555555', linewidth=2, linestyle='--')

    cash_dates = df_res[df_res['Regime'].str.contains("CASH", na=False)]['Rebalance_Date']
    for cd in cash_dates:
        plt.axvspan(cd, cd + pd.Timedelta(days=30), color='red', alpha=0.1, lw=0)

    plt.title('Equal-Weighted Regime Signals (25/25/25/25) vs NIFTY500\n(Red shading = CASH Regime)')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Equity (Starting 2M INR)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(output_png, dpi=300)
    plt.close()
    print(f"Equity curve saved to {output_png}")
except Exception as e:
    print(f"Could not generate equity curve: {e}")

try:
    shutil.copy2(__file__, os.path.join(run_dir, os.path.basename(__file__)))
except:
    pass
