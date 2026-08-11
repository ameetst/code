"""
sandbox_skew.py
===============
SANDBOX EXPERIMENT: Adjusted Sharpe Ratio (Skewness + Kurtosis Penalty)

Replaces raw Sharpe scores with the Pezier-White Adjusted Sharpe:
    Adj_Sharpe = Sharpe * [1 + (Skew/6)*Sharpe - (ExcessKurt/24)*Sharpe^2]

Everything else (regime, vol-sizing, 5% cap, 28d hold lock, 52H filter)
is IDENTICAL to the production backtest.py for a clean apples-to-apples
comparison.

Benchmark to beat:
  Production backtest (Vol Sizing, 5% Cap) → CAGR: 38.3%  | MDD: -16.3%
"""

import sys
import os
import datetime
import warnings

import numpy as np
import pandas as pd
import scipy.stats as stats
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
FRICTION     = 0.002  # 0.20% per trade

WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily = RFR_ANNUAL / TRADING_DAYS

# Skew/Kurt blending coefficient — keep < 1 so Sharpe remains dominant
SKEW_WEIGHT = 0.5
KURT_WEIGHT = 0.5


# ── ADJUSTED SHARPE FUNCTION ─────────────────────────────────────────────────
def _adjusted_sharpe(series: pd.Series, window: int,
                     rfr_daily: float, trading_days: int) -> float:
    """
    Pezier-White Adjusted Sharpe Ratio.
    Rewards positive skewness (upside asymmetry).
    Penalizes excess kurtosis (fat-tailed jump risk).

    Formula:
        Adj_S = S * [1 + (Skew/6)*S - (ExcessKurt/24)*S^2]

    where S = raw annualised Sharpe.
    """
    px = series.dropna()
    if len(px) < window * 0.90:
        return np.nan
    px_w     = px if len(px) < window + 1 else px.iloc[-(window + 1):]
    log_rets = np.diff(np.log(px_w.values))
    excess   = log_rets - rfr_daily
    sd       = excess.std(ddof=1)
    if sd < 1e-12:
        return np.nan

    raw_sharpe = (excess.mean() / sd) * np.sqrt(trading_days)

    # Higher-order moments (scipy uses Fisher definition: kurtosis = excess kurtosis)
    skewness     = stats.skew(excess)
    excess_kurt  = stats.kurtosis(excess, fisher=True)  # already excess (kurt - 3)

    # Pezier-White adjustment
    adjustment = (
        1
        + (skewness / 6) * raw_sharpe
        - (excess_kurt / 24) * raw_sharpe ** 2
    )

    return raw_sharpe * adjustment


def _cross_section_z(series: pd.Series) -> pd.Series:
    mu, sd = series.mean(), series.std(ddof=1)
    return (series - mu) / sd if sd > 0 else series * 0.0


def compute_adjusted_sharpe_z(prices_df, stock_tickers, windows, rfr_daily, trading_days):
    """
    Compute Adjusted Sharpe per window, Z-score, and build COMPOSITE.
    Drop-in replacement for ml.compute_sharpe() in this sandbox.
    """
    adj_sharpe_data = {}
    for label, window in windows.items():
        col = [_adjusted_sharpe(prices_df.loc[t], window, rfr_daily, trading_days)
               for t in stock_tickers]
        adj_sharpe_data[label] = col

    adj_df = pd.DataFrame(adj_sharpe_data, index=stock_tickers)

    z_df = pd.DataFrame(index=stock_tickers)
    for label in windows:
        z_df[f"Z_{label}"] = _cross_section_z(adj_df[label])

    # Fill NaN Z-scores with 0 (universe mean) — same convention as momentum_lib
    z_label_cols = [f"Z_{l}" for l in windows]
    z_df[z_label_cols] = z_df[z_label_cols].fillna(0.0)

    # COMPOSITE = equal-weighted mean of all windows
    z_df["COMPOSITE"] = z_df[z_label_cols].mean(axis=1)

    return adj_df, z_df


# ── SUPPRESS STDOUT HELPER ────────────────────────────────────────────────────
@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print(f"[SANDBOX] Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

dt_idx    = pd.DatetimeIndex(dates)
eow_dates = []
for i in range(len(dt_idx) - 1):
    if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week:
        eow_dates.append(dates[i])
eow_dates.append(dates[-1])

start_idx   = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"Total trading days: {len(dates)}")
print(f"Valid rebalance points (week-ends): {len(valid_dates)}")
print(f"Adjusted Sharpe  |  Skew Weight={SKEW_WEIGHT}  |  Kurt Weight={KURT_WEIGHT}")
print("-" * 80)

if len(valid_dates) < 2:
    print("[!] ERROR: Insufficient data for backtesting.")
    sys.exit(0)

# ── PORTFOLIO STATE ───────────────────────────────────────────────────────────
equity           = 2_000_000.0
nifty_equity     = 2_000_000.0
current_portfolio = {}  # {ticker: {'entry_date': date, 'weight': float}}
results_log       = []

prices_df_ffill    = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

# ── MAIN BACKTEST LOOP ────────────────────────────────────────────────────────
for i in range(len(valid_dates) - 1):
    t_date    = valid_dates[i]
    next_date = valid_dates[i + 1]
    idx       = dates.index(t_date)
    next_idx  = dates.index(next_date)

    # 1. Slice point-in-time data
    sliced_prices = prices_df.iloc[:, :idx + 1]
    sliced_nifty  = nifty_series_ffill.iloc[:idx + 1]

    # 2. Compute ADJUSTED Sharpe scores (replaces ml.compute_sharpe)
    adj_df, z_df = compute_adjusted_sharpe_z(
        sliced_prices, stock_tickers, WINDOWS, rfr_daily, TRADING_DAYS
    )

    # 3. Other metrics via momentum_lib (unchanged)
    with suppress_stdout():
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)
        regime  = ml.compute_market_regime(sliced_nifty)

    is_cash = False  # CASH regime deprecated; NOT BUY = freeze, not liquidate

    result                = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h
    result["COMPOSITE"]   = result["COMPOSITE"].map(ml.normalise_composite)

    # 4. Filter & Rank
    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df       = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(
        ascending=False, method="first", na_option="bottom"
    )
    elig_df = elig_df.sort_values("RANK", ascending=True)
    top_candidates = elig_df.index.tolist()

    next_portfolio_tickers = []

    if not is_cash:
        # Pass 1: Keep existing holdings if still valid
        for ticker, state in current_portfolio.items():
            entry_date = state["entry_date"]
            days_held  = (t_date - entry_date).days
            if ticker in top_candidates:
                rank       = elig_df.loc[ticker, "RANK"]
                pct_from_52 = elig_df.loc[ticker, "PCT_FROM_52H"]
                if pct_from_52 < -25:
                    continue
                if rank <= 40:
                    next_portfolio_tickers.append(ticker)
                elif days_held < 28:
                    next_portfolio_tickers.append(ticker)

        # Pass 2: Fill slots from top candidates in BUY regime only
        if regime.startswith("BUY"):
            slots_to_fill = TOP_N - len(next_portfolio_tickers)
            for ticker in top_candidates:
                if slots_to_fill <= 0:
                    break
                if ticker not in next_portfolio_tickers:
                    next_portfolio_tickers.append(ticker)
                    slots_to_fill -= 1

    # 5. Volatility-Adjusted Weights (5% cap) — identical to production
    raw_weights = {}
    for ticker in next_portfolio_tickers:
        comp_score = result.loc[ticker, "COMPOSITE"]
        px         = sliced_prices.loc[ticker].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                px_w  = px.iloc[-w:] if len(px) >= w else px
                log_r = np.diff(np.log(px_w.values))
                if len(log_r) > 5:
                    vols.append(np.std(log_r, ddof=1) * np.sqrt(252))
            if vols and np.mean(vols) > 0:
                raw_weights[ticker] = comp_score / np.mean(vols)
            else:
                raw_weights[ticker] = comp_score
        else:
            raw_weights[ticker] = comp_score

    total_raw    = sum(raw_weights.values())
    actual_portfolio = {}
    for ticker in next_portfolio_tickers:
        norm_w   = raw_weights[ticker] / total_raw if total_raw > 0 else 1.0 / len(next_portfolio_tickers)
        capped_w = min(0.05, norm_w)  # strict 5% cap
        if ticker in current_portfolio:
            actual_portfolio[ticker] = {
                "entry_date": current_portfolio[ticker]["entry_date"],
                "weight": capped_w
            }
        else:
            actual_portfolio[ticker] = {"entry_date": t_date, "weight": capped_w}

    # 6. Returns
    if is_cash:
        gross_ret = (1.06 ** (1 / 52)) - 1.0
    else:
        actual_port_list   = list(actual_portfolio.keys())
        total_equity_weight = sum(s["weight"] for s in actual_portfolio.values())
        cash_weight         = max(0.0, 1.0 - total_equity_weight)

        if actual_port_list:
            start_px      = prices_df_ffill.loc[actual_port_list].iloc[:, idx]
            end_px        = prices_df_ffill.loc[actual_port_list].iloc[:, next_idx]
            stock_returns = (end_px / start_px) - 1.0
            weights_series = pd.Series({t: actual_portfolio[t]["weight"] for t in actual_port_list})
            gross_ret = (
                (stock_returns * weights_series).sum()
                + cash_weight * ((1.06 ** (1 / 52)) - 1.0)
            )
        else:
            gross_ret = (1.06 ** (1 / 52)) - 1.0

        if pd.isna(gross_ret):
            gross_ret = 0.0

    # 7. Friction (absolute weight displacement)
    all_tickers      = set(current_portfolio.keys()) | set(actual_portfolio.keys())
    abs_weight_change = sum(
        abs((actual_portfolio[t]["weight"] if t in actual_portfolio else 0.0)
            - (current_portfolio[t]["weight"] if t in current_portfolio else 0.0))
        for t in all_tickers
    )
    friction_cost = abs_weight_change * FRICTION
    turnover      = abs_weight_change / 2.0

    net_ret = gross_ret - friction_cost

    # 8. Benchmark
    nifty_ret    = (nifty_series_ffill.iloc[next_idx] / nifty_series_ffill.iloc[idx]) - 1.0
    equity      *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)

    sys.stdout.write(
        f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
        f"Eq: {equity:12,.0f} | NIFTY: {nifty_equity:12,.0f} | "
        f"Regime: {regime.split(' ')[0]:<10}"
    )
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "Regime":         regime,
        "Gross_Return":   gross_ret,
        "Net_Return":     net_ret,
        "Nifty_Return":   nifty_ret,
        "Equity":         equity,
        "Nifty_Equity":   nifty_equity,
        "Top20_Tickers":  ", ".join(actual_portfolio) if actual_portfolio else "CASH"
    })

    current_portfolio = actual_portfolio

# ── RESULTS ───────────────────────────────────────────────────────────────────
print("\n" + "-" * 80)
print("[SANDBOX] Backtest complete!")

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir   = os.path.join("backtest results", f"sandbox_skew_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

df_res = pd.DataFrame(results_log)
df_res.to_csv(os.path.join(run_dir, "results.csv"), index=False)

def compute_drawdown(eq):
    return ((eq / eq.cummax()) - 1.0).min()

years  = (valid_dates[-1] - valid_dates[0]).days / 365.25 or 1.0
p_cagr = ((equity / 2_000_000.0) ** (1 / years) - 1.0) * 100
n_cagr = ((nifty_equity / 2_000_000.0) ** (1 / years) - 1.0) * 100
p_mdd  = compute_drawdown(df_res["Equity"]) * 100
n_mdd  = compute_drawdown(df_res["Nifty_Equity"]) * 100

print(f"\n{'':=<60}")
print(f"  [SANDBOX] Adjusted Sharpe (Skew+Kurt) Strategy")
print(f"{'':=<60}")
print(f"Period       : {valid_dates[0].strftime('%b %Y')} to {valid_dates[-2].strftime('%b %Y')} ({years:.2f} years)")
print(f"Strategy CAGR:  {p_cagr:5.1f}%  |  Max Drawdown: {p_mdd:5.1f}%")
print(f"NIFTY500 CAGR:  {n_cagr:5.1f}%  |  Max Drawdown: {n_mdd:5.1f}%")
print(f"{'':=<60}")
print(f"\n  [BENCHMARK]  Production backtest (Vol Sizing, 5% Cap)")
print(f"  Strategy CAGR:  38.3%  |  Max Drawdown: -16.3%")
print(f"{'':=<60}\n")

# ── PLOT ──────────────────────────────────────────────────────────────────────
try:
    df_res["Rebalance_Date"] = pd.to_datetime(df_res["Rebalance_Date"])
    plt.figure(figsize=(13, 7))
    plt.plot(df_res["Rebalance_Date"], df_res["Equity"],
             label=f"Adj Sharpe Sandbox (CAGR {p_cagr:.1f}%)", color="#E65C00", lw=2)
    plt.plot(df_res["Rebalance_Date"], df_res["Nifty_Equity"],
             label=f"NIFTY500 (CAGR {n_cagr:.1f}%)", color="#555555", lw=2, linestyle="--")
    plt.axhline(y=2_000_000, color="gray", linestyle=":", lw=1, alpha=0.5)
    plt.title("SANDBOX: Adjusted Sharpe (Skew + Kurtosis) vs NIFTY500\n"
              f"Benchmark: 38.3% CAGR / -16.3% MDD  →  Sandbox: {p_cagr:.1f}% CAGR / {p_mdd:.1f}% MDD",
              fontsize=13)
    plt.xlabel("Date")
    plt.ylabel("Portfolio Equity (Starting ₹ 2,000,000)")
    plt.legend(fontsize=11)
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(os.path.join(run_dir, "equity_curve.png"), dpi=300)
    plt.close()
    print(f"Equity curve saved to {run_dir}")
except Exception as e:
    print(f"Could not generate plot: {e}")

try:
    shutil.copy2(__file__, os.path.join(run_dir, "sandbox_skew.py"))
except Exception:
    pass
