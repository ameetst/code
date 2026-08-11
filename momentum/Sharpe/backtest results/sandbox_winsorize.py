"""
sandbox_winsorize.py
====================
Side-by-side backtest of 4 winsorization approaches:
  1. BASELINE       : No winsorization (current production)
  2. RETURNS_WINSOR : MAD-clip daily returns before Sharpe calc
  3. ZSCORE_WINSOR  : Clip Z-scores to [-3, +3] after Sharpe calc
  4. BOTH_WINSOR    : Both returns + Z-score winsorization combined

All runs use:
  - 5% Position Cap
  - No Sector Limits
  - Dynamic Regime Score (Macro)
"""

import sys, os, datetime, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from contextlib import contextmanager

import momentum_lib as ml
warnings.filterwarnings("ignore")

FILE         = "n500_bt.xlsx"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily      = RFR_ANNUAL / TRADING_DAYS
FRICTION       = 0.002

MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
EMA50_BAND          = 0.10
EMA_TREND_BAND      = 0.05
SIGNAL_WEIGHTS      = {"ema50": 0.35, "ema_trend": 0.25,
                        "breadth": 0.25, "momentum": 0.15}

@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout; sys.stdout = devnull
        try: yield
        finally: sys.stdout = old


# ── WINSORIZED SHARPE VARIANTS ────────────────────────────────────────────────

def _sharpe_ratio_winsor_returns(series, window, rfr_daily, trading_days):
    """Sharpe with MAD-clipped returns."""
    px = series.dropna()
    if len(px) < window * 0.90:
        return np.nan
    px_w     = px if len(px) < window + 1 else px.iloc[-(window + 1):]
    log_rets = np.diff(np.log(px_w.values))
    excess   = log_rets - rfr_daily
    # MAD winsorization
    median = np.median(excess)
    mad    = np.median(np.abs(excess - median))
    scale  = 1.4826 * mad
    if scale > 1e-12:
        lower = median - 3 * scale
        upper = median + 3 * scale
        excess = np.clip(excess, lower, upper)
    sd = excess.std(ddof=1)
    if sd < 1e-12:
        return np.nan
    return (excess.mean() / sd) * np.sqrt(trading_days)


def _cross_section_z_clipped(series):
    """Z-score with clipping to [-3, +3]."""
    mu, sd = series.mean(), series.std(ddof=1)
    z = (series - mu) / sd if sd > 0 else series * 0.0
    return z.clip(-3, 3)


def compute_sharpe_variant(prices_df, stock_tickers, windows, rfr_daily,
                           trading_days, winsor_returns=False, winsor_z=False):
    """Compute Sharpe with optional winsorization at two injection points."""
    sharpe_func = _sharpe_ratio_winsor_returns if winsor_returns else ml._sharpe_ratio
    z_func      = _cross_section_z_clipped     if winsor_z      else ml._cross_section_z

    sharpe_data = {}
    for label, window in windows.items():
        col = [sharpe_func(prices_df.loc[t], window, rfr_daily, trading_days)
               for t in stock_tickers]
        sharpe_data[label] = col

    sharpe_df = pd.DataFrame(sharpe_data, index=stock_tickers)

    z_df = pd.DataFrame(index=stock_tickers)
    for label in windows:
        z_df[f"Z_{label}"] = z_func(sharpe_df[label])

    z_label_cols = [f"Z_{l}" for l in windows]
    z_df[z_label_cols] = z_df[z_label_cols].fillna(0.0)

    core_labels = [l for l in windows if l != "1M"]
    z_cols             = [f"Z_{l}" for l in core_labels]
    z_df["COMPOSITE"]  = z_df[z_cols].mean(axis=1)
    z_df["SHARPE_3"]   = z_df[["Z_12M", "Z_6M", "Z_3M"]].mean(axis=1)

    return sharpe_df, z_df


# ── REGIME SCORE ──────────────────────────────────────────────────────────────
def compute_regime_score(nifty_s, eligible_mask, composite_series):
    px = nifty_s.dropna()
    if len(px) < 200:
        return 0.5, {"dynamic_n": 15, "allow_new": True}
    price  = px.iloc[-1]
    ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]
    ema50_score     = float(np.clip((price / ema50  - 1.0) / EMA50_BAND     + 0.5, 0.0, 1.0))
    ema_trend_score = float(np.clip((ema50  / ema200 - 1.0) / EMA_TREND_BAND + 0.5, 0.0, 1.0))
    total  = len(eligible_mask)
    elig   = int(eligible_mask.sum())
    breadth_score  = elig / total if total > 0 else 0.5
    pos_mom        = int((composite_series[eligible_mask] > 1.5).sum())
    momentum_score = pos_mom / max(1, elig)
    score = (ema50_score     * SIGNAL_WEIGHTS["ema50"]     +
             ema_trend_score * SIGNAL_WEIGHTS["ema_trend"] +
             breadth_score   * SIGNAL_WEIGHTS["breadth"]   +
             momentum_score  * SIGNAL_WEIGHTS["momentum"])
    dyn_n = int(MIN_N + score * (MAX_N - MIN_N))
    return score, {"dynamic_n": dyn_n, "allow_new": score >= NEW_ENTRY_THRESHOLD}


# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print(f"[SANDBOX] Loading {FILE} ...")
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

prices_df_ff = prices_df.ffill(axis=1)
nifty_ff     = nifty_series.ffill()


# ── SINGLE BACKTEST FUNCTION ──────────────────────────────────────────────────
def run_backtest(label, winsor_returns=False, winsor_z=False):
    tag = []
    if winsor_returns: tag.append("RetWinsor")
    if winsor_z:       tag.append("ZWinsor")
    tag_str = "+".join(tag) if tag else "None"
    print(f"\n  Running: {label} (Winsorization: {tag_str}) ...")

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

        with suppress_stdout():
            sharpe_df, z_df = compute_sharpe_variant(
                sliced_prices, stock_tickers, SHARPE_WINDOWS, rfr_daily,
                TRADING_DAYS, winsor_returns=winsor_returns, winsor_z=winsor_z)
            pct_52h = ml.compute_pct_from_52h(sliced_prices, stock_tickers)

        result = z_df.copy()
        result["PCT_FROM_52H"] = pct_52h
        z_cols = [f"Z_{l}" for l in SHARPE_WINDOWS if l != "1M"]
        result["COMPOSITE"] = z_df[z_cols].mean(axis=1).map(ml.normalise_composite)

        eligible_mask = result["PCT_FROM_52H"] >= -25
        regime_score, reg = compute_regime_score(
            sliced_nifty, eligible_mask, result["COMPOSITE"])
        dynamic_n = reg["dynamic_n"]
        allow_new = reg["allow_new"]

        elig_df = result[eligible_mask].copy()
        elig_df["RANK"] = elig_df["COMPOSITE"].rank(
            ascending=False, method="first", na_option="bottom")
        elig_df = elig_df.sort_values("RANK")
        top_candidates = elig_df.index.tolist()

        next_tickers = []
        for ticker, state in current_portfolio.items():
            days_held = (t_date - state["entry_date"]).days
            if ticker in elig_df.index:
                rank  = elig_df.loc[ticker, "RANK"]
                pct52 = elig_df.loc[ticker, "PCT_FROM_52H"]
                if pct52 < -25: continue
                if rank <= 40 or days_held < 28:
                    next_tickers.append(ticker)

        if allow_new:
            slots = dynamic_n - len(next_tickers)
            for ticker in top_candidates:
                if slots <= 0: break
                if ticker not in next_tickers:
                    next_tickers.append(ticker)
                    slots -= 1

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
            capped_w = min(0.05, nw)
            actual[ticker] = {
                "entry_date": current_portfolio[ticker]["entry_date"]
                               if ticker in current_portfolio else t_date,
                "weight": capped_w
            }

        eq_wt   = sum(s["weight"] for s in actual.values())
        cash_wt = max(0.0, 1.0 - eq_wt)
        liq_ret = (1.06 ** (1/52)) - 1.0

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

        all_t = set(current_portfolio) | set(actual)
        abs_change = sum(abs((actual[t]["weight"] if t in actual else 0.0)
                           - (current_portfolio[t]["weight"] if t in current_portfolio else 0.0))
                         for t in all_t)
        tc  = abs_change * FRICTION
        net = gross - tc

        nifty_ret = (nifty_ff.iloc[next_idx] / nifty_ff.iloc[idx]) - 1.0
        equity       *= (1 + net)
        nifty_equity *= (1 + nifty_ret)

        log.append({"Date": t_date, "Equity": equity, "Nifty": nifty_equity,
                    "Net_Return": net, "Holdings": len(actual), "Cash_Wt": cash_wt})
        current_portfolio = actual

        sys.stdout.write(f"\r    [{i+1}/{len(valid_dates)-1}] "
                         f"{t_date.strftime('%b %Y')} | "
                         f"Eq: {equity:12,.0f} | N={dynamic_n:2d} | "
                         f"Held: {len(actual):2d}")
        sys.stdout.flush()

    print()
    return pd.DataFrame(log)


# ── RUN ALL 4 VARIANTS ───────────────────────────────────────────────────────
runs = [
    ("BASELINE",       False, False),
    ("RETURNS_WINSOR", True,  False),
    ("ZSCORE_WINSOR",  False, True),
    ("BOTH_WINSOR",    True,  True),
]

results = {}
for label, wr, wz in runs:
    results[label] = run_backtest(label, winsor_returns=wr, winsor_z=wz)

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
            "Ann Vol %": round(ann_vol, 1), "Calmar": round(calmar, 2),
            "Avg Holdings": round(avg_n, 1), "Avg Cash %": round(avg_cash, 1)}

n_eq    = results["BASELINE"]["Nifty"]
n_years = (results["BASELINE"]["Date"].iloc[-1] - results["BASELINE"]["Date"].iloc[0]).days / 365.25
n_cagr  = ((n_eq.iloc[-1] / 2_000_000.0) ** (1/n_years) - 1.0) * 100
n_mdd   = ((n_eq / n_eq.cummax()) - 1.0).min() * 100

metrics_all = {lbl: calc_metrics(results[lbl]) for lbl, _, _ in runs}

print(f"\n{'Metric':<18} {'BASELINE':>12} {'RET_WINSOR':>12} {'Z_WINSOR':>12} {'BOTH':>12} {'NIFTY500':>12}")
print("-" * 78)
for metric in ["CAGR %", "MDD %", "Ann Vol %", "Calmar", "Avg Holdings", "Avg Cash %"]:
    row = f"{metric:<18}"
    for lbl, _, _ in runs:
        row += f" {metrics_all[lbl][metric]:>12}"
    if metric == "CAGR %":  row += f" {n_cagr:>12.1f}"
    elif metric == "MDD %": row += f" {n_mdd:>12.1f}"
    else:                   row += f" {'—':>12}"
    print(row)
print("=" * 78)

# ── SAVE RESULTS TO SEPARATE FOLDERS ─────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
base_dir  = os.path.join("backtest results", f"winsorization_{timestamp}")

for label, _, _ in runs:
    run_dir = os.path.join(base_dir, label)
    os.makedirs(run_dir, exist_ok=True)
    results[label].to_csv(os.path.join(run_dir, f"{label}.csv"), index=False)
    print(f"  Saved -> {run_dir}/{label}.csv")

# ── EQUITY CURVE CHART ───────────────────────────────────────────────────────
colors = {
    "BASELINE":       "#9AA5B4",
    "RETURNS_WINSOR": "#2E86C1",
    "ZSCORE_WINSOR":  "#E67E22",
    "BOTH_WINSOR":    "#1F4E79",
}

plt.figure(figsize=(14, 7))
plt.title("Winsorization Impact on Sharpe Momentum Strategy\n"
          "(Base: Rs 2M | Dynamic Regime | 5% Cap | No Sector Limit)",
          fontsize=13, fontweight="bold")

for label, _, _ in runs:
    df = results[label]
    m  = metrics_all[label]
    lw = 2.0 if label != "BASELINE" else 1.5
    plt.plot(df["Date"], df["Equity"], color=colors[label], lw=lw,
             label=f"{label} | CAGR {m['CAGR %']}%, MDD {m['MDD %']}%, Calmar {m['Calmar']}")

df_b = results["BASELINE"]
plt.plot(df_b["Date"], df_b["Nifty"], color="#CCCCCC", lw=1.5, linestyle=":",
         label=f"NIFTY500 | CAGR {n_cagr:.1f}%, MDD {n_mdd:.1f}%")

plt.axhline(2_000_000, color="#DDDDDD", lw=1, linestyle=":")
plt.ylabel("Portfolio Equity (Rs)")
plt.legend(loc="upper left", fontsize=9)
plt.grid(True, alpha=0.2)
plt.tight_layout()

out_png = os.path.join(base_dir, "winsorization_comparison.png")
plt.savefig(out_png, dpi=200)
plt.close()

print(f"\nChart saved -> {out_png}")
