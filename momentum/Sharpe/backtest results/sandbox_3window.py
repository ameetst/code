"""
sandbox_3window.py
==================
SANDBOX: 3-Window Sharpe (12M / 6M / 3M) vs 4-Window baseline (12M / 9M / 6M / 3M)

Removes the 9M window from COMPOSITE calculation to test whether
SHARPE_3 as the primary ranking signal improves or degrades performance.

Benchmark to beat:
  4-Window production  ->  CAGR: 38.3%  |  MDD: -16.3%
"""

import sys, os, datetime, warnings, shutil
import numpy as np
import pandas as pd
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

# KEY CHANGE: Drop 9M window
SHARPE_WINDOWS = {"12M": 252, "6M": 126, "3M": 63}

rfr_daily = RFR_ANNUAL / TRADING_DAYS

@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout; sys.stdout = devnull
        try: yield
        finally: sys.stdout = old

# ── LOAD ──────────────────────────────────────────────────────────────────────
print(f"[SANDBOX 3W] Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

dt_idx    = pd.DatetimeIndex(dates)
eow_dates = [dates[i] for i in range(len(dt_idx)-1)
             if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week]
eow_dates.append(dates[-1])

start_idx   = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"Trading days: {len(dates)}  |  Rebalance points: {len(valid_dates)}")
print(f"Windows: 12M / 6M / 3M  (9M removed)")
print("-" * 80)

if len(valid_dates) < 2:
    print("[!] Insufficient data."); sys.exit(0)

# ── STATE ─────────────────────────────────────────────────────────────────────
equity            = 2_000_000.0
nifty_equity      = 2_000_000.0
current_portfolio = {}
results_log       = []

prices_df_ffill    = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

# ── BACKTEST LOOP ─────────────────────────────────────────────────────────────
for i in range(len(valid_dates) - 1):
    t_date    = valid_dates[i]
    next_date = valid_dates[i + 1]
    idx       = dates.index(t_date)
    next_idx  = dates.index(next_date)

    sliced_prices = prices_df.iloc[:, :idx + 1]
    sliced_nifty  = nifty_series_ffill.iloc[:idx + 1]

    with suppress_stdout():
        sharpe_df, z_df = ml.compute_sharpe(
            sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)
        regime  = ml.compute_market_regime(sliced_nifty)

    is_cash = False  # NOT BUY = freeze, not liquidate

    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h

    # 3-window COMPOSITE = mean(Z_12M, Z_6M, Z_3M)
    z_cols = ["Z_12M", "Z_6M", "Z_3M"]
    result["COMPOSITE"] = z_df[z_cols].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)

    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(
        ascending=False, method="first", na_option="bottom")
    elig_df = elig_df.sort_values("RANK")
    top_candidates = elig_df.index.tolist()

    next_portfolio_tickers = []
    if not is_cash:
        # Pass 1: retain held stocks
        for ticker, state in current_portfolio.items():
            days_held = (t_date - state["entry_date"]).days
            if ticker in top_candidates:
                rank     = elig_df.loc[ticker, "RANK"]
                pct_52   = elig_df.loc[ticker, "PCT_FROM_52H"]
                if pct_52 < -25: continue
                if rank <= 40 or days_held < 28:
                    next_portfolio_tickers.append(ticker)

        # Pass 2: fill new slots in BUY regime only
        if regime.startswith("BUY"):
            slots = TOP_N - len(next_portfolio_tickers)
            for ticker in top_candidates:
                if slots <= 0: break
                if ticker not in next_portfolio_tickers:
                    next_portfolio_tickers.append(ticker)
                    slots -= 1

    # Volatility-adjusted weights, 5% cap
    raw_weights = {}
    for ticker in next_portfolio_tickers:
        comp  = result.loc[ticker, "COMPOSITE"]
        px    = sliced_prices.loc[ticker].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                pw   = px.iloc[-w:] if len(px) >= w else px
                lr   = np.diff(np.log(pw.values))
                if len(lr) > 5: vols.append(np.std(lr, ddof=1) * np.sqrt(252))
            raw_weights[ticker] = comp / np.mean(vols) if vols and np.mean(vols) > 0 else comp
        else:
            raw_weights[ticker] = comp

    total_raw = sum(raw_weights.values())
    actual_portfolio = {}
    for ticker in next_portfolio_tickers:
        nw = raw_weights[ticker] / total_raw if total_raw > 0 else 1.0 / len(next_portfolio_tickers)
        cw = min(0.05, nw)
        actual_portfolio[ticker] = {
            "entry_date": current_portfolio[ticker]["entry_date"] if ticker in current_portfolio else t_date,
            "weight": cw
        }

    # Returns
    if is_cash:
        gross_ret = (1.06 ** (1/52)) - 1.0
    else:
        port_list  = list(actual_portfolio.keys())
        eq_wt      = sum(s["weight"] for s in actual_portfolio.values())
        cash_wt    = max(0.0, 1.0 - eq_wt)
        if port_list:
            s_px       = prices_df_ffill.loc[port_list].iloc[:, idx]
            e_px       = prices_df_ffill.loc[port_list].iloc[:, next_idx]
            stk_ret    = (e_px / s_px) - 1.0
            w_series   = pd.Series({t: actual_portfolio[t]["weight"] for t in port_list})
            gross_ret  = (stk_ret * w_series).sum() + cash_wt * ((1.06 ** (1/52)) - 1.0)
        else:
            gross_ret  = (1.06 ** (1/52)) - 1.0
        if pd.isna(gross_ret): gross_ret = 0.0

    # Friction
    all_t = set(current_portfolio) | set(actual_portfolio)
    abs_wc = sum(
        abs((actual_portfolio[t]["weight"] if t in actual_portfolio else 0.0)
          - (current_portfolio[t]["weight"] if t in current_portfolio else 0.0))
        for t in all_t)
    net_ret  = gross_ret - abs_wc * FRICTION
    nifty_ret = (nifty_series_ffill.iloc[next_idx] / nifty_series_ffill.iloc[idx]) - 1.0

    equity       *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)

    sys.stdout.write(
        f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
        f"Eq: {equity:12,.0f} | NIFTY: {nifty_equity:12,.0f} | Regime: {regime.split(' ')[0]:<10}")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "Regime": regime,
        "Net_Return": net_ret,
        "Nifty_Return": nifty_ret,
        "Equity": equity,
        "Nifty_Equity": nifty_equity,
    })
    current_portfolio = actual_portfolio

# ── RESULTS ───────────────────────────────────────────────────────────────────
print("\n" + "-" * 80)

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir   = os.path.join("backtest results", f"sandbox_3window_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

df_res = pd.DataFrame(results_log)
df_res.to_csv(os.path.join(run_dir, "results.csv"), index=False)

years  = (valid_dates[-1] - valid_dates[0]).days / 365.25 or 1.0
p_cagr = ((equity / 2_000_000.0) ** (1/years) - 1.0) * 100
n_cagr = ((nifty_equity / 2_000_000.0) ** (1/years) - 1.0) * 100

def mdd(eq): return ((eq / eq.cummax()) - 1.0).min() * 100

p_mdd = mdd(df_res["Equity"])
n_mdd = mdd(df_res["Nifty_Equity"])

print(f"\n{'':=<60}")
print(f"  [SANDBOX] 3-Window Sharpe  (12M / 6M / 3M)")
print(f"{'':=<60}")
print(f"  Period    : {valid_dates[0].strftime('%b %Y')} -> {valid_dates[-2].strftime('%b %Y')} ({years:.2f} yrs)")
print(f"  CAGR      : {p_cagr:5.1f}%    MDD: {p_mdd:5.1f}%")
print(f"  NIFTY500  : {n_cagr:5.1f}%    MDD: {n_mdd:5.1f}%")
print(f"{'':=<60}")
print(f"  BENCHMARK (4-Window 12M/9M/6M/3M):  CAGR 38.3%  /  MDD -16.3%")
print(f"{'':=<60}\n")

# ── COMPARISON EQUITY CURVE ───────────────────────────────────────────────────
try:
    # Load 4-window benchmark
    bench_csv = os.path.join("backtest results", "run_2026-04-21_09-47-06", "backtest_results.csv")
    bench_df  = pd.read_csv(bench_csv)
    bench_df["Rebalance_Date"] = pd.to_datetime(bench_df["Rebalance_Date"])
    bench_eq  = 2_000_000.0 * (1 + bench_df["Net_Return"]).cumprod()
    bench_ny  = 2_000_000.0 * (1 + bench_df["Nifty_Return"]).cumprod()

    bench_yrs  = (bench_df["Rebalance_Date"].iloc[-1] - bench_df["Rebalance_Date"].iloc[0]).days / 365.25
    bench_cagr = ((bench_eq.iloc[-1] / 2_000_000.0) ** (1/bench_yrs) - 1.0) * 100
    bench_mdd  = ((bench_eq / bench_eq.cummax()) - 1.0).min() * 100

    df_res["Rebalance_Date"] = pd.to_datetime(df_res["Rebalance_Date"])

    plt.style.use("default")
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1]})

    # ── Top panel: Equity curves ──
    ax = axes[0]
    ax.plot(df_res["Rebalance_Date"], df_res["Equity"],
            label=f"3-Window (12M/6M/3M)  CAGR {p_cagr:.1f}%  MDD {p_mdd:.1f}%",
            color="#E65C00", lw=2.5)
    ax.plot(bench_df["Rebalance_Date"], bench_eq,
            label=f"4-Window (12M/9M/6M/3M)  CAGR {bench_cagr:.1f}%  MDD {bench_mdd:.1f}%",
            color="#1F4E79", lw=2.5, linestyle="--")
    ax.plot(bench_df["Rebalance_Date"], bench_ny,
            label=f"NIFTY500  CAGR {n_cagr:.1f}%  MDD {n_mdd:.1f}%",
            color="#9AA5B4", lw=1.5, linestyle=":")
    ax.axhline(2_000_000, color="#CCCCCC", lw=1, linestyle=":")
    ax.set_title("Window Comparison: 3-Window vs 4-Window Sharpe Momentum\n"
                 "(Base: ₹ 2,000,000  |  Vol-Adjusted Sizing  |  5% Cap  |  6% Liquid Cash)",
                 fontsize=14, pad=12)
    ax.set_ylabel("Portfolio Equity (INR)", fontsize=12)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"₹{x/1e6:.1f}M"))
    ax.legend(fontsize=11, loc="upper left")
    ax.grid(True, alpha=0.25)

    # ── Bottom panel: Relative performance (3W / 4W ratio) ──
    ax2 = axes[1]
    # Align on common dates
    merged = pd.merge(
        df_res[["Rebalance_Date", "Equity"]].rename(columns={"Equity": "eq_3w"}),
        bench_df[["Rebalance_Date"]].assign(eq_4w=bench_eq.values),
        on="Rebalance_Date", how="inner"
    )
    if len(merged) > 0:
        ratio = (merged["eq_3w"] / merged["eq_4w"] - 1.0) * 100
        ax2.bar(merged["Rebalance_Date"], ratio,
                color=["#E65C00" if r > 0 else "#1F4E79" for r in ratio],
                alpha=0.6, width=5)
        ax2.axhline(0, color="#333333", lw=1)
        ax2.set_ylabel("3W vs 4W (%)", fontsize=11)
        ax2.set_xlabel("Date", fontsize=11)
        ax2.grid(True, alpha=0.2)

    plt.tight_layout()
    out_png = os.path.join(run_dir, "comparison_equity_curve.png")
    plt.savefig(out_png, dpi=200)
    plt.close()
    print(f"Comparison chart saved -> {out_png}")

except Exception as e:
    print(f"Could not generate comparison chart: {e}")

try:
    shutil.copy2(__file__, os.path.join(run_dir, "sandbox_3window.py"))
except Exception: pass
