"""
bt.py — N500 Sharpe Momentum Backtesting Engine
================================================
Weekly rebalance simulation over historical price data.

Reads price data from:
    ../momentum/Sharpe Score/n500_bt.xlsx

Uses momentum_lib from:
    ../momentum/Sharpe Score/momentum_lib.py

Run:
    python bt.py

Feature flags (edit CONFIG section below):
    USE_ADJUSTED_SHARPE   — Pezier-White skew/kurtosis adjustment (default: False)
    USE_WINSORIZE         — Winsorise raw Sharpes before Z-scoring   (default: False)
    USE_LIQUIDITY_FLOOR   — Filter out illiquid stocks               (default: False)
    USE_CLENOW_BLEND      — Blend Clenow composite into SHARPE_ALL   (default: False)
"""

import sys
import os
import datetime
import warnings
import shutil

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from contextlib import contextmanager
from scipy.stats import mstats

# ── PATH SETUP ────────────────────────────────────────────────────────────────
# bt.py lives in backtest\; momentum_lib.py lives one level up in Sharpe Score\
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR    = os.path.join(_THIS_DIR, "..", "momentum", "Sharpe Score")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, os.path.normpath(_LIB_DIR))

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
DATA_FILE    = os.path.join(_LIB_DIR, "n500_bt.xlsx")
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
INITIAL_CAP  = 2_000_000.0
FRICTION     = 0.002        # 0.20% per trade leg (one-way)
LIQUID_YIELD = 0.06         # 6% p.a. on idle cash

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily      = RFR_ANNUAL / TRADING_DAYS

# Dynamic regime parameters — mirrors Sharpe.py exactly
MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
HOLD_RANK_BUFFER    = 40
MIN_HOLD_DAYS       = 28
EMA50_BAND          = 0.10
EMA_TREND_BAND      = 0.05
SIGNAL_WEIGHTS      = {
    "ema50":     0.35,
    "ema_trend": 0.25,
    "breadth":   0.25,
    "momentum":  0.15,
}

# ── FEATURE FLAGS (Phase 2 improvements — all off for baseline) ───────────────
USE_ADJUSTED_SHARPE  = False   # Pezier-White Adjusted Sharpe (skew + kurtosis)
USE_WINSORIZE        = False   # Winsorise raw Sharpes at [2%, 98%] before Z-scoring
USE_LIQUIDITY_FLOOR  = False   # Require min avg daily volume (needs volume data)
USE_CLENOW_BLEND     = False   # Blend Clenow score into composite (weight below)
CLENOW_BLEND_WEIGHT  = 0.30    # 30% Clenow / 70% Sharpe when blending

# ── HELPERS ───────────────────────────────────────────────────────────────────

@contextmanager
def suppress_stdout():
    """Silence momentum_lib's per-stock progress prints inside the loop."""
    with open(os.devnull, "w") as devnull:
        old = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old


def compute_regime_score(nifty_s: pd.Series,
                         eligible_mask: pd.Series,
                         composite_series: pd.Series) -> tuple:
    """
    Continuous Regime Strength Score (0.0–1.0) from four signals.
    Mirrors compute_regime_score() in Sharpe.py exactly.

    Signals
    -------
    ema50     (35%) — how far NIFTY500 price is above/below its EMA50
    ema_trend (25%) — EMA50 vs EMA200 alignment
    breadth   (25%) — % stocks within -25% of 52-week high
    momentum  (15%) — % eligible stocks with COMPOSITE > 1.5

    Returns
    -------
    (regime_score: float, detail: dict)
    """
    px = nifty_s.dropna()
    if len(px) < 200:
        dyn_n = int(MIN_N + 0.5 * (MAX_N - MIN_N))
        return 0.5, {"regime_score": 0.5, "dynamic_n": dyn_n,
                     "allow_new": True, "note": "insufficient history"}

    price  = px.iloc[-1]
    ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]

    ema50_score     = float(np.clip((price / ema50  - 1.0) / EMA50_BAND      + 0.5, 0.0, 1.0))
    ema_trend_score = float(np.clip((ema50  / ema200 - 1.0) / EMA_TREND_BAND  + 0.5, 0.0, 1.0))

    total_stocks   = len(eligible_mask)
    elig_count     = int(eligible_mask.sum())
    breadth_score  = elig_count / total_stocks if total_stocks > 0 else 0.5

    elig_comp      = composite_series[eligible_mask]
    pos_mom        = int((elig_comp > 1.5).sum())
    momentum_score = pos_mom / max(1, elig_count)

    regime_score = (
        ema50_score     * SIGNAL_WEIGHTS["ema50"]     +
        ema_trend_score * SIGNAL_WEIGHTS["ema_trend"] +
        breadth_score   * SIGNAL_WEIGHTS["breadth"]   +
        momentum_score  * SIGNAL_WEIGHTS["momentum"]
    )
    dynamic_n = int(MIN_N + regime_score * (MAX_N - MIN_N))

    return regime_score, {
        "regime_score":    round(regime_score, 3),
        "dynamic_n":       dynamic_n,
        "allow_new":       regime_score >= NEW_ENTRY_THRESHOLD,
        "ema50_score":     round(ema50_score, 3),
        "ema_trend_score": round(ema_trend_score, 3),
        "breadth_score":   round(breadth_score, 3),
        "momentum_score":  round(momentum_score, 3),
        "eligible":        elig_count,
    }


def winsorise_sharpe(sharpe_df: pd.DataFrame, limits=(0.02, 0.98)) -> pd.DataFrame:
    """
    [FEATURE FLAG: USE_WINSORIZE]
    Winsorise each window's raw Sharpe column at the given percentile limits
    before cross-sectional Z-scoring.  Clips extreme outliers that would
    otherwise dominate the Z-score distribution.
    """
    out = sharpe_df.copy()
    for col in out.columns:
        col_data = out[col].dropna().values
        lo = np.percentile(col_data, limits[0] * 100)
        hi = np.percentile(col_data, limits[1] * 100)
        out[col] = out[col].clip(lower=lo, upper=hi)
    return out


def compute_composite(sliced_prices: pd.DataFrame,
                      stock_tickers: list,
                      sliced_nifty: pd.Series) -> pd.DataFrame:
    """
    Run the full scoring pipeline on a point-in-time price slice.

    Respects all feature flags.  Returns a DataFrame indexed by ticker with:
        COMPOSITE    — primary ranking signal (normalised)
        PCT_FROM_52H — % distance from 52-week high
        RANK         — rank within eligible universe (NaN if ineligible)
    """
    # ── Step 1: Sharpe (raw or adjusted) ──────────────────────────────────────
    if USE_ADJUSTED_SHARPE:
        sharpe_df, z_df = ml.compute_adjusted_sharpe(
            sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
    else:
        sharpe_df, z_df = ml.compute_sharpe(
            sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)

    # ── Step 2: Optional winsorisation (applied to raw Sharpe before Z) ───────
    if USE_WINSORIZE:
        sharpe_df = winsorise_sharpe(sharpe_df)
        # Re-Z-score on winsorised values
        for label in SHARPE_WINDOWS:
            col = f"Z_{label}"
            mu  = sharpe_df[label].mean()
            sd  = sharpe_df[label].std(ddof=1)
            z_df[col] = (sharpe_df[label] - mu) / sd if sd > 0 else 0.0

        core = [l for l in SHARPE_WINDOWS if l != "1M"]
        z_df["COMPOSITE"] = z_df[[f"Z_{l}" for l in core]].mean(axis=1)
        z_df["SHARPE_3"]  = z_df[["Z_12M", "Z_6M", "Z_3M"]].mean(axis=1)

    # ── Step 3: Optional Clenow blend ─────────────────────────────────────────
    if USE_CLENOW_BLEND:
        _, _, _, cz_df = ml.compute_clenow(
            sliced_prices, stock_tickers, SHARPE_WINDOWS, TRADING_DAYS)
        sharpe_composite = z_df["COMPOSITE"]
        clenow_composite = cz_df["CLENOW_Z"]
        z_df["COMPOSITE"] = (
            (1.0 - CLENOW_BLEND_WEIGHT) * sharpe_composite +
            CLENOW_BLEND_WEIGHT         * clenow_composite
        )

    # ── Step 4: Normalise composite ───────────────────────────────────────────
    z_df["COMPOSITE"] = z_df["COMPOSITE"].map(ml.normalise_composite)

    # ── Step 5: 52H eligibility + rank ───────────────────────────────────────
    pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)
    result  = z_df[["COMPOSITE"]].copy()
    result["PCT_FROM_52H"] = pct_52h

    eligible_mask   = result["PCT_FROM_52H"] >= -25
    result["RANK"]  = np.nan
    result.loc[eligible_mask, "RANK"] = (
        result.loc[eligible_mask, "COMPOSITE"]
        .rank(ascending=False, method="first", na_option="bottom")
    )
    return result, eligible_mask


def vol_weighted_alloc(tickers: list,
                       composite_scores: pd.Series,
                       sliced_prices: pd.DataFrame,
                       cap: float = 0.05) -> dict:
    """
    Inverse-vol * composite_score weighting, capped at `cap` per stock.
    Returns {ticker: weight} normalised so total equity <= 1.0.
    """
    raw = {}
    for t in tickers:
        score = composite_scores.get(t, 1.0)
        px    = sliced_prices.loc[t].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                px_w  = px.iloc[-w:] if len(px) >= w else px
                log_r = np.diff(np.log(px_w.values))
                if len(log_r) > 5:
                    vols.append(np.std(log_r, ddof=1) * np.sqrt(TRADING_DAYS))
            mean_vol = np.mean(vols) if vols else None
            raw[t] = score / mean_vol if (mean_vol and mean_vol > 0) else score
        else:
            raw[t] = score

    total = sum(raw.values())
    if total <= 0:
        equal = 1.0 / len(tickers) if tickers else 0.0
        return {t: min(cap, equal) for t in tickers}

    return {t: min(cap, raw[t] / total) for t in tickers}


def compute_drawdown_series(equity_series: pd.Series) -> pd.Series:
    roll_max = equity_series.cummax()
    return (equity_series / roll_max) - 1.0


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print(f"Loading price data from:\n  {DATA_FILE}\n")
with suppress_stdout():
    prices_df, nifty_series, stock_tickers, dates = ml.load_prices(DATA_FILE)

prices_ffill = prices_df.ffill(axis=1)
nifty_ffill  = nifty_series.ffill()

valid_days = prices_df.shape[1]
print(f"  Universe : {len(stock_tickers)} stocks")
print(f"  History  : {valid_days} columns | "
      f"{dates[0].strftime('%d-%b-%Y')} → {dates[-1].strftime('%d-%b-%Y')}")

# ── IDENTIFY REBALANCE DATES (week-ends, Friday close) ───────────────────────
dt_idx = pd.DatetimeIndex(dates)
eow_dates = []
for i in range(len(dt_idx) - 1):
    if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week:
        eow_dates.append(dates[i])
eow_dates.append(dates[-1])

# Require 252-day warm-up for first 12M Sharpe window
WARMUP = 252
valid_rebals = [d for d in eow_dates if dates.index(d) >= WARMUP]

print(f"\n  Rebalance dates (week-ends after {WARMUP}d warm-up): {len(valid_rebals)}")
if len(valid_rebals) < 2:
    print("\n[ERROR] Not enough data for backtesting (need > 252 warm-up days + at least 2 rebalance points).")
    sys.exit(1)
print(f"  Simulation: {valid_rebals[0].strftime('%d-%b-%Y')} → {valid_rebals[-1].strftime('%d-%b-%Y')}\n")

# ── BACKTEST LOOP ─────────────────────────────────────────────────────────────
print("=" * 78)
print("  RUNNING POINT-IN-TIME WEEKLY BACKTEST")
flags_on = [k for k, v in {
    "AdjSharpe":  USE_ADJUSTED_SHARPE,
    "Winsorise":  USE_WINSORIZE,
    "Liquidity":  USE_LIQUIDITY_FLOOR,
    "ClenowBlend":USE_CLENOW_BLEND,
}.items() if v]
print(f"  Feature flags ON : {', '.join(flags_on) if flags_on else 'none (baseline)'}")
print("=" * 78)

equity         = INITIAL_CAP
nifty_equity   = INITIAL_CAP
portfolio      = {}   # {ticker: {"entry_date": date, "weight": float}}
results_log    = []

weekly_liquid_rate = (1.0 + LIQUID_YIELD) ** (1 / 52) - 1.0

for i in range(len(valid_rebals) - 1):
    t_date    = valid_rebals[i]
    next_date = valid_rebals[i + 1]
    t_idx     = dates.index(t_date)
    next_idx  = dates.index(next_date)

    # ── 1. Slice data strictly point-in-time ──────────────────────────────────
    sliced_prices = prices_df.iloc[:, :t_idx + 1]
    sliced_nifty  = nifty_ffill.iloc[:t_idx + 1]

    # ── 2. Score universe silently ────────────────────────────────────────────
    with suppress_stdout():
        result, eligible_mask = compute_composite(
            sliced_prices, stock_tickers, sliced_nifty)

    # ── 3. Regime score ───────────────────────────────────────────────────────
    regime_score, regime_detail = compute_regime_score(
        sliced_nifty, eligible_mask, result["COMPOSITE"])
    dynamic_n = regime_detail["dynamic_n"]
    allow_new = regime_detail["allow_new"]

    # Ranked candidates (eligible only), sorted best first
    elig_result = result[eligible_mask].copy()
    elig_result = elig_result.dropna(subset=["RANK"])
    elig_result = elig_result.sort_values("RANK", ascending=True)
    top_candidates = elig_result.index.tolist()

    # ── 4. Exit evaluation ────────────────────────────────────────────────────
    #
    # Trigger 1 — 52H breach: stock no longer in eligible set (RANK = NaN)
    #             Overrides 28-day hold lock.
    # Trigger 2 — Rank decay: rank > HOLD_RANK_BUFFER AND held >= MIN_HOLD_DAYS
    #             Respects hold lock for recently entered positions.
    #
    retained = []
    for ticker, state in portfolio.items():
        days_held = (t_date - state["entry_date"]).days

        # Is the stock still in the eligible universe?
        if ticker not in elig_result.index:
            # EXIT — 52H breach (or dropped out entirely); lock overridden
            continue

        rank = elig_result.loc[ticker, "RANK"]

        # Trigger 2: rank decay exit (lock-aware)
        if rank > HOLD_RANK_BUFFER:
            if days_held >= MIN_HOLD_DAYS:
                continue   # EXIT — rank decayed, past lock
            # else: rank bad but still inside lock — HOLD regardless
        retained.append(ticker)

    # ── 5. New entries ────────────────────────────────────────────────────────
    next_tickers = list(retained)
    if allow_new:
        slots = dynamic_n - len(next_tickers)
        for ticker in top_candidates:
            if slots <= 0:
                break
            if ticker not in next_tickers:
                next_tickers.append(ticker)
                slots -= 1

    # ── 6. Volatility-weighted allocation ─────────────────────────────────────
    if next_tickers:
        composite_scores = result["COMPOSITE"]
        weights = vol_weighted_alloc(next_tickers, composite_scores, sliced_prices)
    else:
        weights = {}

    # Build next portfolio dict (preserve entry_date for held positions)
    next_portfolio = {}
    for ticker, w in weights.items():
        entry_date = (portfolio[ticker]["entry_date"]
                      if ticker in portfolio else t_date)
        next_portfolio[ticker] = {"entry_date": entry_date, "weight": w}

    # ── 7. Compute gross return for this week ─────────────────────────────────
    total_equity_wt = sum(s["weight"] for s in next_portfolio.values())
    cash_wt         = max(0.0, 1.0 - total_equity_wt)

    if next_portfolio:
        held_list  = list(next_portfolio.keys())
        start_px   = prices_ffill.loc[held_list].iloc[:, t_idx]
        end_px     = prices_ffill.loc[held_list].iloc[:, next_idx]
        stock_rets = (end_px / start_px) - 1.0
        wt_series  = pd.Series({t: next_portfolio[t]["weight"] for t in held_list})
        gross_ret  = float((stock_rets * wt_series).sum()) + cash_wt * weekly_liquid_rate
        if np.isnan(gross_ret):
            gross_ret = cash_wt * weekly_liquid_rate
    else:
        gross_ret = weekly_liquid_rate

    # ── 8. Friction: 0.20% × absolute weight change across all positions ──────
    all_tickers_touched = set(portfolio.keys()) | set(next_portfolio.keys())
    abs_wt_change = sum(
        abs(next_portfolio.get(t, {}).get("weight", 0.0) -
            portfolio.get(t, {}).get("weight", 0.0))
        for t in all_tickers_touched
    )
    friction_cost = abs_wt_change * FRICTION
    turnover_pct  = (abs_wt_change / 2.0) * 100   # round-trip display

    net_ret = gross_ret - friction_cost

    # ── 9. Benchmark ──────────────────────────────────────────────────────────
    n_start   = float(nifty_ffill.iloc[t_idx])
    n_end     = float(nifty_ffill.iloc[next_idx])
    nifty_ret = (n_end / n_start) - 1.0

    # ── 10. Compound ──────────────────────────────────────────────────────────
    equity       *= (1.0 + net_ret)
    nifty_equity *= (1.0 + nifty_ret)

    # ── 11. Progress line ─────────────────────────────────────────────────────
    sys.stdout.write(
        f"\r  [{i+1:>4}/{len(valid_rebals)-1}]  {t_date.strftime('%d-%b-%Y')}  |"
        f"  Eq: {equity:>14,.0f}  |  RS: {regime_score:.2f}  N={dynamic_n:2d}  "
        f"Hold: {len(next_portfolio):2d}  TO: {turnover_pct:4.0f}%"
    )
    sys.stdout.flush()

    # ── 12. Log ───────────────────────────────────────────────────────────────
    results_log.append({
        "Rebalance_Date":   t_date.strftime("%Y-%m-%d"),
        "Regime_Score":     round(regime_score, 3),
        "Dynamic_N":        dynamic_n,
        "Allow_New":        allow_new,
        "Eligible_Count":   int(eligible_mask.sum()),
        "Holdings_Count":   len(next_portfolio),
        "Turnover_Pct":     round(turnover_pct, 2),
        "Gross_Return_Pct": round(gross_ret * 100, 4),
        "Friction_Pct":     round(friction_cost * 100, 4),
        "Net_Return_Pct":   round(net_ret * 100, 4),
        "Nifty_Return_Pct": round(nifty_ret * 100, 4),
        "Equity":           round(equity, 2),
        "Nifty_Equity":     round(nifty_equity, 2),
        "Holdings":         ", ".join(next_portfolio.keys()) if next_portfolio else "CASH",
    })

    portfolio = next_portfolio

print("\n" + "=" * 78)
print("  BACKTEST COMPLETE")
print("=" * 78)

# ── PERFORMANCE METRICS ───────────────────────────────────────────────────────
df = pd.DataFrame(results_log)
df["Rebalance_Date"] = pd.to_datetime(df["Rebalance_Date"])

years = (valid_rebals[-1] - valid_rebals[0]).days / 365.25
years = max(years, 0.01)

p_cagr = ((equity        / INITIAL_CAP) ** (1.0 / years) - 1.0) * 100
n_cagr = ((nifty_equity  / INITIAL_CAP) ** (1.0 / years) - 1.0) * 100

dd_series   = compute_drawdown_series(df["Equity"])
ndd_series  = compute_drawdown_series(df["Nifty_Equity"])
p_mdd       = dd_series.min() * 100
n_mdd       = ndd_series.min() * 100

weekly_rets = df["Net_Return_Pct"] / 100.0
ann_vol     = weekly_rets.std(ddof=1) * np.sqrt(52) * 100
ann_ret_dec = (equity / INITIAL_CAP) ** (1.0 / years) - 1.0
sharpe_strat = (ann_ret_dec - RFR_ANNUAL) / (ann_vol / 100) if ann_vol > 0 else 0.0

calmar = p_cagr / abs(p_mdd) if p_mdd != 0 else float("inf")

print(f"\n  Period   : {valid_rebals[0].strftime('%b %Y')} → "
      f"{valid_rebals[-1].strftime('%b %Y')}  ({years:.2f} years)")
print(f"\n  {'Metric':<24}  {'Strategy':>12}  {'NIFTY500':>12}")
print(f"  {'-'*50}")
print(f"  {'CAGR':<24}  {p_cagr:>11.1f}%  {n_cagr:>11.1f}%")
print(f"  {'Max Drawdown':<24}  {p_mdd:>11.1f}%  {n_mdd:>11.1f}%")
print(f"  {'Ann. Volatility':<24}  {ann_vol:>11.1f}%  {'—':>12}")
print(f"  {'Sharpe (ex RFR 7%)':<24}  {sharpe_strat:>12.2f}  {'—':>12}")
print(f"  {'Calmar Ratio':<24}  {calmar:>12.2f}  {'—':>12}")
print(f"  {'Final Equity (INR)':<24}  {equity:>12,.0f}  {nifty_equity:>12,.0f}")
print(f"  {'-'*50}")
avg_to = df["Turnover_Pct"].mean()
avg_n  = df["Holdings_Count"].mean()
print(f"\n  Avg Holdings / week  : {avg_n:.1f}")
print(f"  Avg Turnover / week  : {avg_to:.1f}%")
print(f"  Total rebalances     : {len(df)}")

# ── OUTPUT FOLDER ─────────────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir   = os.path.join(_THIS_DIR, "backtest results", f"run_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

csv_path    = os.path.join(run_dir, "backtest_results.csv")
png_path    = os.path.join(run_dir, "equity_curve.png")
script_path = os.path.join(run_dir, "bt.py")

df.to_csv(csv_path, index=False)
print(f"\n  Results CSV  → {csv_path}")

# ── EQUITY CURVE PLOT ─────────────────────────────────────────────────────────
try:
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 8),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True
    )
    fig.patch.set_facecolor("#F8F9FA")
    for ax in (ax1, ax2):
        ax.set_facecolor("#FFFFFF")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # ── Top panel: equity curves ───────────────────────────────────────────
    ax1.plot(df["Rebalance_Date"], df["Equity"],
             label=f"Strategy  CAGR {p_cagr:.1f}%  MDD {p_mdd:.1f}%",
             color="#0055CC", linewidth=2.0)
    ax1.plot(df["Rebalance_Date"], df["Nifty_Equity"],
             label=f"NIFTY500  CAGR {n_cagr:.1f}%  MDD {n_mdd:.1f}%",
             color="#888888", linewidth=1.5, linestyle="--")

    # Shade NOT-BUY regime periods (regime_score < NEW_ENTRY_THRESHOLD)
    no_buy_dates = df[df["Regime_Score"] < NEW_ENTRY_THRESHOLD]["Rebalance_Date"]
    for nd in no_buy_dates:
        ax1.axvspan(nd, nd + pd.Timedelta(days=7),
                    color="#FF6B6B", alpha=0.15, lw=0)

    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: f"₹{x/1e6:.1f}M"))
    ax1.set_ylabel("Portfolio Value (INR)")
    ax1.set_title(
        f"N500 Sharpe Momentum  vs  NIFTY500\n"
        f"Friction 0.20%/trade · Liquid 6% p.a. · Vol-sized · 5% cap  "
        f"({'Baseline' if not flags_on else ' + '.join(flags_on)})",
        fontsize=11, pad=10
    )
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.25)

    # ── Bottom panel: regime score ─────────────────────────────────────────
    ax2.fill_between(df["Rebalance_Date"], df["Regime_Score"],
                     color="#0055CC", alpha=0.25, linewidth=0)
    ax2.plot(df["Rebalance_Date"], df["Regime_Score"],
             color="#0055CC", linewidth=1.0)
    ax2.axhline(NEW_ENTRY_THRESHOLD, color="#CC3300", linewidth=1.0,
                linestyle="--", alpha=0.7, label=f"Entry gate ({NEW_ENTRY_THRESHOLD})")
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("Regime Score")
    ax2.set_xlabel("Date")
    ax2.legend(loc="lower left", fontsize=8)
    ax2.grid(True, alpha=0.25)

    plt.tight_layout()
    plt.savefig(png_path, dpi=200, bbox_inches="tight")
    plt.close()
    print(f"  Equity curve → {png_path}")
except Exception as exc:
    print(f"  [Warning] Could not generate plot: {exc}")

# ── ARCHIVE SCRIPT ────────────────────────────────────────────────────────────
try:
    shutil.copy2(os.path.abspath(__file__), script_path)
    print(f"  Script copy  → {script_path}")
except Exception:
    pass

print(f"\n  Run folder: {run_dir}\n")
