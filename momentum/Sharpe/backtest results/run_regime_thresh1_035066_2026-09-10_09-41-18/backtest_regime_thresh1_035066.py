"""
SANDBOX backtest — does NOT touch any live code (Sharpe.py / momentum_lib.py
unchanged). Identical to backtest_wired.py in every respect EXCEPT the
regime score's Signal 4 ("momentum breadth") counts stocks with
COMPOSITE > 1.0 instead of the live hardcoded > 1.5
(see momentum_lib.py:1190, ml.compute_regime_score). Signal weights stay at
the live default (35/25/25/15). Ranking, eligibility, dynamic N mechanics,
hold buffer, vol-sized weighting, friction — all unchanged plain-Sharpe
strategy machinery.

Because momentum_lib.compute_regime_score() hardcodes the 1.5 threshold
in-line (it is not an exposed parameter), this script carries a LOCAL COPY
of that function (regime_score_custom_threshold below) with the threshold
pulled out as a parameter and everything else byte-for-byte identical to
momentum_lib.py's implementation — not a reinvented regime model, just the
one hardcoded literal made adjustable for this experiment.
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

MOMENTUM_THRESHOLD = 1.0  # live value is 1.5 (momentum_lib.py:1190)


def compute_regime_score_custom_threshold(
    nifty_s, pct52h_mask, composite_series, prices_df=None,
    signal_weights=None, min_n=None, max_n=None,
    new_entry_threshold=None, dyn_n_full_score=None,
    momentum_threshold=1.5,
):
    """Verbatim copy of ml.compute_regime_score with the Signal 4 momentum
    breadth threshold (hardcoded 1.5 in momentum_lib.py:1190) exposed as a
    parameter. See momentum_lib.py for the authoritative version."""
    if signal_weights is None:
        signal_weights = ml.DEFAULT_SIGNAL_WEIGHTS
    if min_n is None:
        min_n = ml.DEFAULT_MIN_N
    if max_n is None:
        max_n = ml.DEFAULT_MAX_N
    if new_entry_threshold is None:
        new_entry_threshold = ml.DEFAULT_NEW_ENTRY_THRESHOLD
    if dyn_n_full_score is None:
        dyn_n_full_score = ml.DEFAULT_DYN_N_FULL_SCORE

    px = nifty_s.dropna()
    if len(px) < 200:
        dyn_n = int((min_n + max_n) / 2)
        return 0.5, {
            "regime_score": 0.5, "dynamic_n": dyn_n, "allow_new": True,
            "ema50_score": 0.5, "ema_trend_score": 0.5,
            "breadth_score": 0.5, "momentum_score": 0.5,
            "eligible": 0, "note": "insufficient data",
        }

    if prices_df is not None and len(prices_df.columns) >= 200:
        ema50_all   = prices_df.T.ewm(span=50,  adjust=False).mean().T
        ema200_all  = prices_df.T.ewm(span=200, adjust=False).mean().T
        last_px     = prices_df.iloc[:, -1]
        last_ema50  = ema50_all.iloc[:, -1]
        last_ema200 = ema200_all.iloc[:, -1]
        valid       = last_px.notna() & last_ema200.notna()
        n_valid     = int(valid.sum())
        if n_valid > 0:
            ema50_score     = float((last_px[valid] > last_ema50[valid]).sum()) / n_valid
            ema_trend_score = float((last_ema50[valid] > last_ema200[valid]).sum()) / n_valid
        else:
            ema50_score = 0.5
            ema_trend_score = 0.5
    else:
        price  = px.iloc[-1]
        ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
        ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]
        ema50_score     = 1.0 if price > ema50 else 0.0
        ema_trend_score = 1.0 if ema50 > ema200 else 0.0

    total_stocks = len(pct52h_mask)
    elig_count   = int(pct52h_mask.sum())
    breadth_score = elig_count / total_stocks if total_stocks > 0 else 0.5

    # ── Only change vs momentum_lib.compute_regime_score: threshold is a
    #    parameter (momentum_threshold) instead of the hardcoded 1.5. ──────
    elig_comp      = composite_series[pct52h_mask]
    pos_mom        = int((elig_comp > momentum_threshold).sum())
    momentum_score = pos_mom / max(1, elig_count)

    score = (
        ema50_score     * signal_weights["ema50_breadth"]     +
        ema_trend_score * signal_weights["ema_trend_breadth"] +
        breadth_score   * signal_weights["breadth"]           +
        momentum_score  * signal_weights["momentum"]
    )
    dyn_n = int(min_n + min(score / dyn_n_full_score, 1.0) * (max_n - min_n))

    detail = {
        "ema50_score":     round(ema50_score, 3),
        "ema_trend_score": round(ema_trend_score, 3),
        "breadth_score":   round(breadth_score, 3),
        "momentum_score":  round(momentum_score, 3),
        "regime_score":    round(score, 3),
        "dynamic_n":       dyn_n,
        "eligible":        elig_count,
        "allow_new":       score >= new_entry_threshold,
    }
    return score, detail


# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated.xlsx"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
FRICTION     = 0.002        # 0.20% per trade

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily = RFR_ANNUAL / TRADING_DAYS

MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.35   # live default: 0.40
DYN_N_FULL_SCORE    = 0.66   # live default: 0.75
SIGNAL_WEIGHTS      = None   # live default (35/25/25/15)

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

print(f"\nStarting Point-in-Time Vectorised Backtest (threshold={MOMENTUM_THRESHOLD} + entry={NEW_ENTRY_THRESHOLD}/full={DYN_N_FULL_SCORE}):")
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
        regime_score, regime_detail = compute_regime_score_custom_threshold(
            sliced_nifty,
            eligible_mask,
            result["COMPOSITE"],
            prices_df=sliced_prices,
            signal_weights=SIGNAL_WEIGHTS,
            min_n=MIN_N,
            max_n=MAX_N,
            new_entry_threshold=NEW_ENTRY_THRESHOLD,
            dyn_n_full_score=DYN_N_FULL_SCORE,
            momentum_threshold=MOMENTUM_THRESHOLD,
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
run_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"run_regime_thresh1_035066_{timestamp}")
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

print(f"\n=== PERFORMANCE SUMMARY (threshold={MOMENTUM_THRESHOLD} + entry={NEW_ENTRY_THRESHOLD}/full={DYN_N_FULL_SCORE}) ===")
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

    plt.title(f'Momentum Breadth Threshold = {MOMENTUM_THRESHOLD} vs NIFTY500\n(Red shading = CASH Regime)')
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
