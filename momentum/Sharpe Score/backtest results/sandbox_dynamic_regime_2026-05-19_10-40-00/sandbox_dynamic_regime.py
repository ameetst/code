"""
sandbox_dynamic_regime.py
==========================
SANDBOX: Dynamic Regime Score vs Binary EMA50 Regime

Replaces the binary BUY / NOT BUY switch with a continuous Regime Strength
Score (0.0 -> 1.0) built from 4 signals:

  Signal 1 (35%): EMA50 distance  — how far NIFTY500 is above/below EMA50
  Signal 2 (25%): EMA trend       — EMA50 vs EMA200 alignment
  Signal 3 (25%): 52H Breadth     — % of stocks within -25% of 52-week high
  Signal 4 (15%): Momentum Breadth— % of eligible stocks with COMPOSITE > 1.5

Regime Score -> Dynamic TOP_N (5 to 25 positions)
Below NEW_ENTRY_THRESHOLD -> exit evaluation only, no new buys

Benchmark to beat:
  Binary EMA50 (4-Window, 5% cap)  ->  CAGR 38.3%  |  MDD -16.3%
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
FRICTION     = 0.002

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily      = RFR_ANNUAL / TRADING_DAYS

# Dynamic Regime parameters
MIN_N               = 5     # minimum holdings at lowest regime score
MAX_N               = 25    # maximum holdings at highest regime score
NEW_ENTRY_THRESHOLD = 0.40  # below this score, no new buys
HOLD_RANK_BUFFER    = 40
MIN_HOLD_DAYS       = 28

# EMA distance normalisation bounds (±10% around EMA50 maps to 0-1)
EMA50_BAND  = 0.10
# EMA trend normalisation bound (±5% between EMA50 and EMA200 maps to 0-1)
EMA_TREND_BAND = 0.05

# Signal weights (must sum to 1.0)
SIGNAL_WEIGHTS = {
    "ema50":     0.35,
    "ema_trend": 0.25,
    "breadth":   0.25,
    "momentum":  0.15,
}


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout; sys.stdout = devnull
        try: yield
        finally: sys.stdout = old


def compute_regime_score(nifty_series, eligible_mask, composite_series):
    """
    Compute a continuous Regime Strength Score (0.0 -> 1.0).

    Parameters
    ----------
    nifty_series    : pd.Series  — NIFTY500 price history (point-in-time slice)
    eligible_mask   : pd.Series  — boolean mask of 52H-eligible stocks
    composite_series: pd.Series  — COMPOSITE scores for all stocks

    Returns
    -------
    regime_score : float (0.0 to 1.0)
    score_detail : dict  — individual signal scores for logging
    """
    px = nifty_series.dropna()
    if len(px) < 200:
        return 0.5, {}  # insufficient data — neutral

    price  = px.iloc[-1]
    ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]

    # Signal 1: EMA50 distance
    ema50_pct   = (price / ema50 - 1.0)
    ema50_score = float(np.clip(ema50_pct / EMA50_BAND + 0.5, 0.0, 1.0))

    # Signal 2: EMA trend (EMA50 vs EMA200)
    ema_trend_pct   = (ema50 / ema200 - 1.0)
    ema_trend_score = float(np.clip(ema_trend_pct / EMA_TREND_BAND + 0.5, 0.0, 1.0))

    # Signal 3: 52H Breadth
    total_stocks  = len(eligible_mask)
    elig_count    = eligible_mask.sum()
    breadth_score = float(elig_count / total_stocks) if total_stocks > 0 else 0.5

    # Signal 4: Momentum breadth — eligible stocks with positive momentum
    elig_comp = composite_series[eligible_mask]
    pos_mom   = (elig_comp > 1.5).sum()
    momentum_score = float(pos_mom / max(1, elig_count))

    # Weighted composite
    regime_score = (
        ema50_score     * SIGNAL_WEIGHTS["ema50"]     +
        ema_trend_score * SIGNAL_WEIGHTS["ema_trend"] +
        breadth_score   * SIGNAL_WEIGHTS["breadth"]   +
        momentum_score  * SIGNAL_WEIGHTS["momentum"]
    )

    detail = {
        "ema50_score":     round(ema50_score, 3),
        "ema_trend_score": round(ema_trend_score, 3),
        "breadth_score":   round(breadth_score, 3),
        "momentum_score":  round(momentum_score, 3),
        "regime_score":    round(regime_score, 3),
        "dynamic_n":       int(MIN_N + regime_score * (MAX_N - MIN_N)),
        "eligible":        int(elig_count),
    }

    return regime_score, detail


# ── LOAD ──────────────────────────────────────────────────────────────────────
print(f"[SANDBOX DYNAMIC] Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

dt_idx    = pd.DatetimeIndex(dates)
eow_dates = [dates[i] for i in range(len(dt_idx)-1)
             if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week]
eow_dates.append(dates[-1])

start_idx   = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"Trading days: {len(dates)}  |  Rebalance points: {len(valid_dates)}")
print(f"Windows: 12M / 9M / 6M / 3M  |  Dynamic N: {MIN_N}-{MAX_N}  |  Entry threshold: {NEW_ENTRY_THRESHOLD}")
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

    # 1. Compute Sharpe rankings (silent)
    with suppress_stdout():
        sharpe_df, z_df = ml.compute_sharpe(
            sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)

    result = z_df.copy()
    result["PCT_FROM_52H"] = pct_52h

    core_z = ["Z_12M", "Z_9M", "Z_6M", "Z_3M"]
    result["COMPOSITE"] = z_df[core_z].mean(axis=1)
    result["COMPOSITE"] = result["COMPOSITE"].map(ml.normalise_composite)

    eligible_mask = result["PCT_FROM_52H"] >= -25
    elig_df = result[eligible_mask].copy()
    elig_df["RANK"] = elig_df["COMPOSITE"].rank(
        ascending=False, method="first", na_option="bottom")
    elig_df = elig_df.sort_values("RANK")
    top_candidates = elig_df.index.tolist()

    # 2. Compute Dynamic Regime Score
    regime_score, detail = compute_regime_score(
        sliced_nifty,
        eligible_mask,
        result["COMPOSITE"]
    )
    dynamic_n  = detail.get("dynamic_n", 20)
    allow_new  = regime_score >= NEW_ENTRY_THRESHOLD

    # 3. Portfolio construction
    next_portfolio_tickers = []

    # Pass 1: retain held stocks (evaluate exits regardless of regime)
    for ticker, state in current_portfolio.items():
        days_held = (t_date - state["entry_date"]).days
        if ticker in elig_df.index:
            rank   = elig_df.loc[ticker, "RANK"]
            pct52  = elig_df.loc[ticker, "PCT_FROM_52H"]
            if pct52 < -25: continue          # 52H breach — exit
            if rank <= HOLD_RANK_BUFFER or days_held < MIN_HOLD_DAYS:
                next_portfolio_tickers.append(ticker)
        # If ticker not eligible at all — drop it

    # Pass 2: fill new slots only if regime score allows new entries
    if allow_new:
        slots = dynamic_n - len(next_portfolio_tickers)
        for ticker in top_candidates:
            if slots <= 0: break
            if ticker not in next_portfolio_tickers:
                next_portfolio_tickers.append(ticker)
                slots -= 1

    # 4. Volatility-adjusted weights, 5% cap
    raw_weights = {}
    for ticker in next_portfolio_tickers:
        comp = result.loc[ticker, "COMPOSITE"]
        px   = sliced_prices.loc[ticker].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                pw = px.iloc[-w:] if len(px) >= w else px
                lr = np.diff(np.log(pw.values))
                if len(lr) > 5: vols.append(np.std(lr, ddof=1) * np.sqrt(252))
            raw_weights[ticker] = comp / np.mean(vols) if vols and np.mean(vols) > 0 else comp
        else:
            raw_weights[ticker] = comp

    total_raw = sum(raw_weights.values())
    actual_portfolio = {}
    for ticker in next_portfolio_tickers:
        nw = raw_weights[ticker] / total_raw if total_raw > 0 else 1.0 / len(next_portfolio_tickers)
        cw = min(0.05, nw)  # 5% cap
        actual_portfolio[ticker] = {
            "entry_date": current_portfolio[ticker]["entry_date"] if ticker in current_portfolio else t_date,
            "weight": cw
        }

    # 5. Returns
    port_list  = list(actual_portfolio.keys())
    eq_wt      = sum(s["weight"] for s in actual_portfolio.values())
    cash_wt    = max(0.0, 1.0 - eq_wt)
    liquid_ret = (1.06 ** (1/52)) - 1.0

    if port_list:
        s_px      = prices_df_ffill.loc[port_list].iloc[:, idx]
        e_px      = prices_df_ffill.loc[port_list].iloc[:, next_idx]
        stk_ret   = (e_px / s_px) - 1.0
        w_series  = pd.Series({t: actual_portfolio[t]["weight"] for t in port_list})
        gross_ret = (stk_ret * w_series).sum() + cash_wt * liquid_ret
    else:
        gross_ret = liquid_ret

    if pd.isna(gross_ret): gross_ret = 0.0

    # 6. Friction
    all_t  = set(current_portfolio) | set(actual_portfolio)
    abs_wc = sum(
        abs((actual_portfolio[t]["weight"] if t in actual_portfolio else 0.0)
          - (current_portfolio[t]["weight"] if t in current_portfolio else 0.0))
        for t in all_t)
    net_ret   = gross_ret - abs_wc * FRICTION
    nifty_ret = (nifty_series_ffill.iloc[next_idx] / nifty_series_ffill.iloc[idx]) - 1.0

    equity       *= (1 + net_ret)
    nifty_equity *= (1 + nifty_ret)

    sys.stdout.write(
        f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
        f"Eq: {equity:12,.0f} | RS: {regime_score:.2f} | N={dynamic_n:2d} | "
        f"Held: {len(actual_portfolio):2d} | Cash: {cash_wt:.0%}")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date":  t_date.strftime("%Y-%m-%d"),
        "Regime_Score":    round(regime_score, 3),
        "Dynamic_N":       dynamic_n,
        "Holdings":        len(actual_portfolio),
        "Cash_Weight":     round(cash_wt, 3),
        "Allow_New":       allow_new,
        "EMA50_Score":     detail.get("ema50_score"),
        "EMATrend_Score":  detail.get("ema_trend_score"),
        "Breadth_Score":   detail.get("breadth_score"),
        "Momentum_Score":  detail.get("momentum_score"),
        "Net_Return":      net_ret,
        "Nifty_Return":    nifty_ret,
        "Equity":          equity,
        "Nifty_Equity":    nifty_equity,
    })
    current_portfolio = actual_portfolio

# ── RESULTS ───────────────────────────────────────────────────────────────────
print("\n" + "-" * 80)

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir   = os.path.join("backtest results", f"sandbox_dynamic_regime_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

df_res = pd.DataFrame(results_log)
df_res.to_csv(os.path.join(run_dir, "results.csv"), index=False)

years  = (valid_dates[-1] - valid_dates[0]).days / 365.25 or 1.0
p_cagr = ((equity / 2_000_000.0) ** (1/years) - 1.0) * 100
n_cagr = ((nifty_equity / 2_000_000.0) ** (1/years) - 1.0) * 100

def mdd(eq): return ((eq / eq.cummax()) - 1.0).min() * 100

p_mdd = mdd(df_res["Equity"])
n_mdd = mdd(df_res["Nifty_Equity"])

avg_n      = df_res["Holdings"].mean()
avg_cash   = df_res["Cash_Weight"].mean()
avg_rs     = df_res["Regime_Score"].mean()

print(f"\n{'':=<65}")
print(f"  [SANDBOX] Dynamic Regime Score  (4-Window Sharpe)")
print(f"{'':=<65}")
print(f"  Period    : {valid_dates[0].strftime('%b %Y')} -> {valid_dates[-2].strftime('%b %Y')} ({years:.2f} yrs)")
print(f"  CAGR      : {p_cagr:5.1f}%    MDD: {p_mdd:5.1f}%")
print(f"  NIFTY500  : {n_cagr:5.1f}%    MDD: {n_mdd:5.1f}%")
print(f"  Avg Holdings : {avg_n:.1f}  |  Avg Cash: {avg_cash:.1%}  |  Avg Regime Score: {avg_rs:.2f}")
print(f"{'':=<65}")
print(f"  BENCHMARK (Binary EMA50, N=20):  CAGR 38.3%  /  MDD -16.3%")
print(f"{'':=<65}\n")

# ── COMPARISON CHART ──────────────────────────────────────────────────────────
try:
    bench_csv = os.path.join("backtest results", "run_2026-04-21_09-47-06", "backtest_results.csv")
    bench_df  = pd.read_csv(bench_csv)
    bench_df["Rebalance_Date"] = pd.to_datetime(bench_df["Rebalance_Date"])
    bench_eq  = 2_000_000.0 * (1 + bench_df["Net_Return"]).cumprod()
    bench_ny  = 2_000_000.0 * (1 + bench_df["Nifty_Return"]).cumprod()
    bench_yrs = (bench_df["Rebalance_Date"].iloc[-1] - bench_df["Rebalance_Date"].iloc[0]).days / 365.25
    bench_cagr= ((bench_eq.iloc[-1] / 2_000_000.0) ** (1/bench_yrs) - 1.0) * 100
    bench_mdd = ((bench_eq / bench_eq.cummax()) - 1.0).min() * 100

    df_res["Rebalance_Date"] = pd.to_datetime(df_res["Rebalance_Date"])

    fig, axes = plt.subplots(3, 1, figsize=(14, 13),
                             gridspec_kw={"height_ratios": [3, 1, 1]})
    fig.suptitle("Dynamic Regime Score vs Binary EMA50\n"
                 "(Base: Rs 2,000,000 | 4-Window Sharpe | 5% Cap | 6% Liquid Cash)",
                 fontsize=13, y=0.98)

    # Panel 1 — Equity curves
    ax = axes[0]
    ax.plot(df_res["Rebalance_Date"], df_res["Equity"],
            label=f"Dynamic Regime  CAGR {p_cagr:.1f}%  MDD {p_mdd:.1f}%",
            color="#E65C00", lw=2.5)
    ax.plot(bench_df["Rebalance_Date"], bench_eq,
            label=f"Binary EMA50  CAGR {bench_cagr:.1f}%  MDD {bench_mdd:.1f}%",
            color="#1F4E79", lw=2.5, linestyle="--")
    ax.plot(bench_df["Rebalance_Date"], bench_ny,
            label=f"NIFTY500  CAGR {n_cagr:.1f}%  MDD {n_mdd:.1f}%",
            color="#9AA5B4", lw=1.5, linestyle=":")
    ax.axhline(2_000_000, color="#CCCCCC", lw=1, linestyle=":")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"Rs{x/1e6:.1f}M"))
    ax.set_ylabel("Portfolio Equity", fontsize=11)
    ax.legend(fontsize=10, loc="upper left")
    ax.grid(True, alpha=0.25)

    # Panel 2 — Dynamic N and Regime Score over time
    ax2 = axes[1]
    ax2_twin = ax2.twinx()
    ax2.fill_between(df_res["Rebalance_Date"], df_res["Holdings"],
                     alpha=0.35, color="#E65C00", label="Holdings (N)")
    ax2.axhline(20, color="#1F4E79", lw=1, linestyle="--", alpha=0.5, label="Fixed N=20 baseline")
    ax2_twin.plot(df_res["Rebalance_Date"], df_res["Regime_Score"],
                  color="#2E7D32", lw=1.5, label="Regime Score")
    ax2_twin.axhline(NEW_ENTRY_THRESHOLD, color="#C62828", lw=1, linestyle=":",
                     label=f"Entry threshold ({NEW_ENTRY_THRESHOLD})")
    ax2_twin.set_ylim(0, 1.05)
    ax2.set_ylabel("Holdings (N)", fontsize=10)
    ax2_twin.set_ylabel("Regime Score", fontsize=10)
    lines1, labels1 = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2_twin.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, fontsize=9, loc="upper right")
    ax2.grid(True, alpha=0.2)

    # Panel 3 — Cash weight over time
    ax3 = axes[2]
    ax3.fill_between(df_res["Rebalance_Date"], df_res["Cash_Weight"] * 100,
                     alpha=0.4, color="#1565C0")
    ax3.set_ylabel("Cash in Liquid Fund (%)", fontsize=10)
    ax3.set_xlabel("Date", fontsize=11)
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax3.grid(True, alpha=0.2)

    plt.tight_layout()
    out_png = os.path.join(run_dir, "dynamic_regime_comparison.png")
    plt.savefig(out_png, dpi=200)
    plt.close()
    print(f"Chart saved -> {out_png}")

except Exception as e:
    print(f"Could not generate chart: {e}")

try:
    shutil.copy2(__file__, os.path.join(run_dir, "sandbox_dynamic_regime.py"))
except Exception: pass
