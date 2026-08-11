"""
sandbox_residual_winsorise.py
==============================
Compare 3 residual winsorisation methods for RES_MOM calculation,
combined with RES_MOM > 0 as a new-entry confirmation filter.

Run 1 — NO_WINSOR   : Raw residuals (current production baseline)
Run 2 — MAD_ONLY    : MAD clipping at 3 MAD
Run 3 — MAD_ASYMM   : MAD clipping + asymmetric hard backstop (1st/99th pct)

All 3 runs use:
  - 4-window Sharpe (12M/9M/6M/3M)
  - Dynamic Regime Score engine (same as Sharpe.py v2)
  - RES_MOM > 0 as new-entry confirmation filter
  - 5% position cap, 6% liquid fund cash yield

Benchmark to beat (dynamic regime, no RES_MOM filter):
  CAGR 41.8%  /  MDD -17.2%
"""

import sys, os, datetime, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from contextlib import contextmanager
from scipy.stats import median_abs_deviation

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = "n500_bt.xlsx"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
FRICTION     = 0.002
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily      = RFR_ANNUAL / TRADING_DAYS

MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
EMA50_BAND          = 0.10
EMA_TREND_BAND      = 0.05
SIGNAL_WEIGHTS      = {"ema50": 0.35, "ema_trend": 0.25,
                       "breadth": 0.25, "momentum": 0.15}


# ── WINSORISATION FUNCTIONS ───────────────────────────────────────────────────
def winsorise_none(residuals: np.ndarray) -> np.ndarray:
    """No winsorisation — raw residuals (current production)."""
    return residuals


def winsorise_mad(residuals: np.ndarray, n: float = 3.0) -> np.ndarray:
    """MAD clipping at n MAD around the median (robust, symmetric)."""
    med = np.median(residuals)
    mad = median_abs_deviation(residuals, scale="normal")
    if mad < 1e-12:
        return residuals
    return np.clip(residuals, med - n * mad, med + n * mad)


def winsorise_mad_asymm(residuals: np.ndarray, n: float = 3.0,
                         pct_lo: float = 1.0, pct_hi: float = 99.0) -> np.ndarray:
    """MAD soft clip + asymmetric hard percentile backstop."""
    # Step 1: MAD clip
    r = winsorise_mad(residuals, n)
    # Step 2: Hard backstop — asymmetric (tight on downside, loose on upside)
    lo = np.percentile(r, pct_lo)
    hi = np.percentile(r, pct_hi)
    return np.clip(r, lo, hi)


# ── REGIME SCORE (mirrors Sharpe.py) ─────────────────────────────────────────
def compute_regime_score(nifty_s, eligible_mask, composite_series):
    px = nifty_s.dropna()
    if len(px) < 200:
        return 0.5, {"dynamic_n": 15, "allow_new": True}
    price  = px.iloc[-1]
    ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]
    ema50_score     = float(np.clip((price / ema50  - 1.0) / EMA50_BAND     + 0.5, 0.0, 1.0))
    ema_trend_score = float(np.clip((ema50  / ema200 - 1.0) / EMA_TREND_BAND + 0.5, 0.0, 1.0))
    total          = len(eligible_mask)
    elig           = int(eligible_mask.sum())
    breadth_score  = elig / total if total > 0 else 0.5
    pos_mom        = int((composite_series[eligible_mask] > 1.5).sum())
    momentum_score = pos_mom / max(1, elig)
    score = (ema50_score     * SIGNAL_WEIGHTS["ema50"]     +
             ema_trend_score * SIGNAL_WEIGHTS["ema_trend"] +
             breadth_score   * SIGNAL_WEIGHTS["breadth"]   +
             momentum_score  * SIGNAL_WEIGHTS["momentum"])
    dyn_n = int(MIN_N + score * (MAX_N - MIN_N))
    return score, {"dynamic_n": dyn_n, "allow_new": score >= NEW_ENTRY_THRESHOLD}


# ── RESIDUAL SHARPE WITH WINSORISATION ────────────────────────────────────────
def residual_sharpe_w(stock_series, mkt_rets, window, winsor_fn):
    """OLS residual Sharpe with pluggable winsorisation function."""
    px = stock_series.dropna()
    if len(px) < window * 0.90:
        return np.nan
    n      = min(len(px) - 1, window)
    s_rets = np.diff(np.log(px.iloc[-n-1:].values))
    m_rets = mkt_rets[-n:]
    if len(s_rets) != len(m_rets) or len(s_rets) < 10:
        return np.nan
    X = np.column_stack([np.ones(len(m_rets)), m_rets])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, s_rets, rcond=None)
    except np.linalg.LinAlgError:
        return np.nan
    residuals = s_rets - X @ coeffs
    residuals = winsor_fn(residuals)           # <-- winsorisation applied here
    sd = residuals.std(ddof=1)
    if sd < 1e-12:
        return np.nan
    return (residuals.mean() / sd) * np.sqrt(TRADING_DAYS)


def compute_resmom_w(prices_df, stock_tickers, nifty_log_rets, winsor_fn):
    """Compute RES_MOM composite with a given winsorisation function."""
    resmom_data = {}
    for label, window in SHARPE_WINDOWS.items():
        col = [residual_sharpe_w(prices_df.loc[t], nifty_log_rets,
                                 window, winsor_fn)
               for t in stock_tickers]
        resmom_data[f"RS_{label}"] = col
    resmom_df = pd.DataFrame(resmom_data, index=stock_tickers)

    rs_z_df = pd.DataFrame(index=stock_tickers)
    for label in SHARPE_WINDOWS:
        s = resmom_df[f"RS_{label}"]
        mu = s.mean(); sd = s.std(ddof=1)
        rs_z_df[f"RZ_{label}"] = (s - mu) / sd if sd > 0 else 0.0

    rz_cols = [f"RZ_{l}" for l in SHARPE_WINDOWS]
    rs_z_df["RES_MOM"] = rs_z_df[rz_cols].mean(axis=1)
    return rs_z_df


# ── SUPPRESS STDOUT ───────────────────────────────────────────────────────────
@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout; sys.stdout = devnull
        try: yield
        finally: sys.stdout = old


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print(f"[SANDBOX WINSORISE] Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

dt_idx    = pd.DatetimeIndex(dates)
eow_dates = [dates[i] for i in range(len(dt_idx)-1)
             if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week]
eow_dates.append(dates[-1])

start_idx   = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]
print(f"Rebalance points: {len(valid_dates)}  |  "
      f"Period: {valid_dates[0].strftime('%b %Y')} -> {valid_dates[-2].strftime('%b %Y')}")
print("-" * 70)

if len(valid_dates) < 2:
    print("[!] Insufficient data."); sys.exit(0)

prices_df_ff   = prices_df.ffill(axis=1)
nifty_ff       = nifty_series.ffill()


# ── SINGLE BACKTEST FUNCTION ──────────────────────────────────────────────────
def run_backtest(label, winsor_fn):
    """Run a full backtest with the given winsorisation function."""
    print(f"\n  Running: {label} ...")
    equity            = 2_000_000.0
    nifty_equity      = 2_000_000.0
    current_portfolio = {}
    log               = []

    for i in range(len(valid_dates) - 1):
        t_date    = valid_dates[i]
        next_date = valid_dates[i + 1]
        idx       = dates.index(t_date)
        next_idx  = dates.index(next_date)

        sliced_prices = prices_df.iloc[:, :idx + 1]
        sliced_nifty  = nifty_ff.iloc[:idx + 1]

        # Sharpe rankings (silent)
        with suppress_stdout():
            sharpe_df, z_df = ml.compute_sharpe(
                sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
            pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)

        result = z_df.copy()
        result["PCT_FROM_52H"] = pct_52h

        z_cols = [f"Z_{l}" for l in SHARPE_WINDOWS]
        result["COMPOSITE"] = z_df[z_cols].mean(axis=1).map(ml.normalise_composite)

        # RES_MOM with winsorisation
        nifty_log_rets = np.diff(np.log(sliced_nifty.dropna().values))
        resmom_z = compute_resmom_w(
            sliced_prices, stock_tickers, nifty_log_rets, winsor_fn)
        result["RES_MOM"] = resmom_z["RES_MOM"]

        # Regime score
        eligible_mask = result["PCT_FROM_52H"] >= -25
        regime_score, reg = compute_regime_score(
            sliced_nifty, eligible_mask, result["COMPOSITE"])
        dynamic_n = reg["dynamic_n"]
        allow_new = reg["allow_new"]

        # Rank eligible stocks
        elig_df = result[eligible_mask].copy()
        elig_df["RANK"] = elig_df["COMPOSITE"].rank(
            ascending=False, method="first", na_option="bottom")
        elig_df = elig_df.sort_values("RANK")
        top_candidates = elig_df.index.tolist()

        # Portfolio construction
        next_tickers = []

        # Pass 1: retain held stocks (exit evaluation)
        for ticker, state in current_portfolio.items():
            days_held = (t_date - state["entry_date"]).days
            if ticker in elig_df.index:
                rank    = elig_df.loc[ticker, "RANK"]
                pct52   = elig_df.loc[ticker, "PCT_FROM_52H"]
                if pct52 < -25: continue
                if rank <= 40 or days_held < 28:
                    next_tickers.append(ticker)

        # Pass 2: fill new slots — RES_MOM > 0 confirmation gate
        if allow_new:
            slots = dynamic_n - len(next_tickers)
            for ticker in top_candidates:
                if slots <= 0: break
                if ticker in next_tickers: continue
                # NEW ENTRY FILTER: must have positive independent momentum
                res_mom_val = result.loc[ticker, "RES_MOM"]
                if pd.notna(res_mom_val) and res_mom_val > 0:
                    next_tickers.append(ticker)
                    slots -= 1

        # Volatility-weighted portfolio, 5% cap
        raw_w = {}
        for ticker in next_tickers:
            comp = result.loc[ticker, "COMPOSITE"]
            px   = sliced_prices.loc[ticker].dropna()
            if len(px) > 10:
                vols = []
                for w in [252, 189, 126, 63]:
                    pw = px.iloc[-w:] if len(px) >= w else px
                    lr = np.diff(np.log(pw.values))
                    if len(lr) > 5: vols.append(np.std(lr, ddof=1) * np.sqrt(252))
                raw_w[ticker] = comp / np.mean(vols) if vols and np.mean(vols) > 0 else comp
            else:
                raw_w[ticker] = comp

        actual = {}
        total_raw = sum(raw_w.values())
        for ticker in next_tickers:
            nw = raw_w[ticker] / total_raw if total_raw > 0 else 1.0 / len(next_tickers)
            actual[ticker] = {
                "entry_date": current_portfolio[ticker]["entry_date"]
                               if ticker in current_portfolio else t_date,
                "weight": min(0.05, nw)
            }

        # Returns
        eq_wt     = sum(s["weight"] for s in actual.values())
        cash_wt   = max(0.0, 1.0 - eq_wt)
        liq_ret   = (1.06 ** (1/52)) - 1.0

        if actual:
            port_list = list(actual.keys())
            s_px  = prices_df_ff.loc[port_list].iloc[:, idx]
            e_px  = prices_df_ff.loc[port_list].iloc[:, next_idx]
            stk_r = (e_px / s_px) - 1.0
            w_ser = pd.Series({t: actual[t]["weight"] for t in port_list})
            gross = (stk_r * w_ser).sum() + cash_wt * liq_ret
        else:
            gross = liq_ret

        if pd.isna(gross): gross = 0.0

        # Friction
        all_t = set(current_portfolio) | set(actual)
        tc    = sum(abs((actual[t]["weight"] if t in actual else 0.0)
                      - (current_portfolio[t]["weight"] if t in current_portfolio else 0.0))
                   for t in all_t) * FRICTION
        net   = gross - tc
        nifty_ret = (nifty_ff.iloc[next_idx] / nifty_ff.iloc[idx]) - 1.0

        equity       *= (1 + net)
        nifty_equity *= (1 + nifty_ret)

        log.append({"Date": t_date, "Equity": equity, "Nifty": nifty_equity,
                    "Net_Return": net, "Nifty_Return": nifty_ret,
                    "Holdings": len(actual), "Cash_Wt": cash_wt,
                    "Regime_Score": regime_score})
        current_portfolio = actual

        sys.stdout.write(f"\r    [{i+1}/{len(valid_dates)-1}] "
                         f"{t_date.strftime('%b %Y')} | "
                         f"Eq: {equity:12,.0f} | N={dynamic_n:2d} | "
                         f"Held: {len(actual):2d}")
        sys.stdout.flush()

    print()
    return pd.DataFrame(log)


# ── RUN ALL THREE VARIANTS ────────────────────────────────────────────────────
variants = [
    ("NO_WINSOR",  winsorise_none,     "#9AA5B4"),
    ("MAD_ONLY",   winsorise_mad,      "#1F4E79"),
    ("MAD_ASYMM",  winsorise_mad_asymm,"#E65C00"),
]

results = {}
for label, fn, _ in variants:
    results[label] = run_backtest(label, fn)

print("\n" + "=" * 70)

# ── METRICS ───────────────────────────────────────────────────────────────────
def calc_metrics(df):
    eq    = df["Equity"]
    years = (df["Date"].iloc[-1] - df["Date"].iloc[0]).days / 365.25
    cagr  = ((eq.iloc[-1] / 2_000_000.0) ** (1/years) - 1.0) * 100
    mdd   = ((eq / eq.cummax()) - 1.0).min() * 100
    ann_vol = df["Net_Return"].std() * np.sqrt(52) * 100
    calmar = cagr / abs(mdd) if mdd != 0 else 0
    avg_n = df["Holdings"].mean()
    avg_cash = df["Cash_Wt"].mean() * 100
    return {"CAGR %": round(cagr, 1), "MDD %": round(mdd, 1),
            "Ann Vol %": round(ann_vol, 1),
            "Calmar": round(calmar, 2),
            "Avg Holdings": round(avg_n, 1),
            "Avg Cash %": round(avg_cash, 1)}

nifty_years = (results["NO_WINSOR"]["Date"].iloc[-1] - results["NO_WINSOR"]["Date"].iloc[0]).days / 365.25
nifty_eq    = results["NO_WINSOR"]["Nifty"]
nifty_cagr  = ((nifty_eq.iloc[-1] / 2_000_000.0) ** (1/nifty_years) - 1.0) * 100
nifty_mdd   = ((nifty_eq / nifty_eq.cummax()) - 1.0).min() * 100

print(f"\n{'Metric':<18} {'NO_WINSOR':>12} {'MAD_ONLY':>12} {'MAD_ASYMM':>12} {'NIFTY500':>12}")
print("-" * 68)
metrics_all = {lbl: calc_metrics(results[lbl]) for lbl, _, _ in variants}
for metric in ["CAGR %", "MDD %", "Ann Vol %", "Calmar", "Avg Holdings", "Avg Cash %"]:
    row = f"{metric:<18}"
    for lbl, _, _ in variants:
        row += f" {metrics_all[lbl][metric]:>12}"
    if metric == "CAGR %":  row += f" {nifty_cagr:>12.1f}"
    elif metric == "MDD %": row += f" {nifty_mdd:>12.1f}"
    else:                   row += f" {'—':>12}"
    print(row)

print("=" * 70)
print("  Benchmark (Dynamic Regime, no RES_MOM filter): CAGR 41.8% / MDD -17.2%")
print("=" * 70)

# ── CHART ─────────────────────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir   = os.path.join("backtest results", f"sandbox_residual_winsorise_{timestamp}")
os.makedirs(run_dir, exist_ok=True)

fig = plt.figure(figsize=(15, 13))
gs  = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.35)
ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1])
ax3 = fig.add_subplot(gs[2])

fig.suptitle(
    "Residual Momentum Winsorisation Comparison\n"
    "(Base: Rs 2,000,000 | 4-Window Sharpe | Dynamic Regime | "
    "RES_MOM > 0 Entry Filter | 5% Cap)",
    fontsize=12, y=0.99)

for label, fn, colour in variants:
    df   = results[label]
    m    = metrics_all[label]
    lbl  = (f"{label}  CAGR {m['CAGR %']:.1f}%  "
            f"MDD {m['MDD %']:.1f}%  "
            f"Calmar {m['Calmar']:.2f}")
    ax1.plot(df["Date"], df["Equity"], label=lbl, color=colour, lw=2.2)

# Nifty
df0 = results["NO_WINSOR"]
ax1.plot(df0["Date"], df0["Nifty"],
         label=f"NIFTY500  CAGR {nifty_cagr:.1f}%  MDD {nifty_mdd:.1f}%",
         color="#CCCCCC", lw=1.5, linestyle=":")
ax1.axhline(2_000_000, color="#DDDDDD", lw=1, linestyle=":")
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"Rs{x/1e6:.1f}M"))
ax1.set_ylabel("Portfolio Equity", fontsize=11)
ax1.legend(fontsize=9.5, loc="upper left")
ax1.set_title("Equity Curves", fontsize=11)
ax1.grid(True, alpha=0.2)

# Panel 2 — Holdings over time
for label, fn, colour in variants:
    ax2.plot(results[label]["Date"], results[label]["Holdings"],
             label=label, color=colour, lw=1.5, alpha=0.8)
ax2.set_ylabel("Holdings (N)", fontsize=10)
ax2.set_title("Dynamic Holdings", fontsize=10)
ax2.legend(fontsize=9, loc="upper right")
ax2.grid(True, alpha=0.2)

# Panel 3 — Cash weight divergence
for label, fn, colour in variants:
    ax3.plot(results[label]["Date"],
             results[label]["Cash_Wt"] * 100,
             label=label, color=colour, lw=1.5, alpha=0.8)
ax3.set_ylabel("Cash in Liquid Fund (%)", fontsize=10)
ax3.set_title("Cash Buffer", fontsize=10)
ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))
ax3.set_xlabel("Date", fontsize=11)
ax3.legend(fontsize=9, loc="upper right")
ax3.grid(True, alpha=0.2)

out_png = os.path.join(run_dir, "residual_winsorise_comparison.png")
plt.savefig(out_png, dpi=200, bbox_inches="tight")
plt.close()
print(f"\nChart saved -> {out_png}")

# Save CSVs
for label, _, _ in variants:
    results[label].to_csv(os.path.join(run_dir, f"{label}.csv"), index=False)

import shutil
try: shutil.copy2(__file__, os.path.join(run_dir, "sandbox_residual_winsorise.py"))
except Exception: pass
