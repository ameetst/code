"""
sandbox_adj_sharpe.py
=====================
Compares Raw Sharpe (baseline/production) vs Pezier-White Adjusted Sharpe
(skewness bonus + kurtosis penalty) using the current Dynamic Regime engine.

Both runs are identical except for the scoring function.
"""

import sys, os, datetime, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from contextlib import contextmanager

import momentum_lib as ml
warnings.filterwarnings("ignore")

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE           = "n500_bt.xlsx"
RFR_ANNUAL     = 0.07
TRADING_DAYS   = 252
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily      = RFR_ANNUAL / TRADING_DAYS
FRICTION       = 0.002
INITIAL_EQUITY = 2_000_000.0

MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
EMA50_BAND          = 0.10
EMA_TREND_BAND      = 0.05
SIGNAL_WEIGHTS      = {"ema50": 0.35, "ema_trend": 0.25,
                        "breadth": 0.25, "momentum": 0.15}

RUNS = [
    ("BASELINE",   "Raw Sharpe (No Skew/Kurt)",          False),
    ("ADJ_SHARPE", "Adjusted Sharpe (Skew + Kurt Penalty)", True),
]

# ── HELPERS ───────────────────────────────────────────────────────────────────
@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout; sys.stdout = devnull
        try: yield
        finally: sys.stdout = old

def compute_regime_score(nifty_s, eligible_mask, composite_series):
    px = nifty_s.dropna()
    if len(px) < 200:
        return 0.5, {"dynamic_n": 15, "allow_new": True}
    price  = px.iloc[-1]
    ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]
    ema50_score     = float(np.clip((price/ema50  - 1.0)/EMA50_BAND     + 0.5, 0.0, 1.0))
    ema_trend_score = float(np.clip((ema50 /ema200 - 1.0)/EMA_TREND_BAND + 0.5, 0.0, 1.0))
    total = len(eligible_mask)
    elig  = int(eligible_mask.sum())
    breadth_score  = elig / total if total > 0 else 0.5
    pos_mom        = int((composite_series[eligible_mask] > 1.5).sum())
    momentum_score = pos_mom / max(1, elig)
    score = (ema50_score     * SIGNAL_WEIGHTS["ema50"]     +
             ema_trend_score * SIGNAL_WEIGHTS["ema_trend"] +
             breadth_score   * SIGNAL_WEIGHTS["breadth"]   +
             momentum_score  * SIGNAL_WEIGHTS["momentum"])
    return score, {"dynamic_n": int(MIN_N + score*(MAX_N - MIN_N)),
                   "allow_new": score >= NEW_ENTRY_THRESHOLD}

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
print(f"[SANDBOX] Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

dt_idx    = pd.DatetimeIndex(dates)
eow_dates = [dates[i] for i in range(len(dt_idx)-1)
             if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week]
eow_dates.append(dates[-1])
start_idx   = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"  Rebalance points : {len(valid_dates)}")
print(f"  Period           : {valid_dates[0].strftime('%b %Y')} -> {valid_dates[-2].strftime('%b %Y')}")
print("-" * 70)

prices_ff = prices_df.ffill(axis=1)
nifty_ff  = nifty_series.ffill()

# ── BACKTEST RUNNER ───────────────────────────────────────────────────────────
def run_backtest(label, description, use_adjusted):
    print(f"\n  [{label}]  {description}")
    equity            = INITIAL_EQUITY
    nifty_equity      = INITIAL_EQUITY
    current_portfolio = {}
    log               = []

    for i in range(len(valid_dates) - 1):
        t_date    = valid_dates[i]
        next_date = valid_dates[i + 1]
        idx       = dates.index(t_date)
        next_idx  = dates.index(next_date)

        sliced = prices_df.iloc[:, :idx + 1]
        nifty_sl = nifty_ff.iloc[:idx + 1]

        with suppress_stdout():
            if use_adjusted:
                sharpe_df, z_df = ml.compute_adjusted_sharpe(
                    sliced, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
            else:
                sharpe_df, z_df = ml.compute_sharpe(
                    sliced, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
            pct_52h = ml.compute_pct_from_52h(sliced, stock_tickers)

        result = z_df.copy()
        result["PCT_FROM_52H"] = pct_52h
        z_cols = [f"Z_{l}" for l in SHARPE_WINDOWS]
        result["COMPOSITE"] = z_df[z_cols].mean(axis=1).map(ml.normalise_composite)

        eligible_mask = result["PCT_FROM_52H"] >= -25
        regime_score, reg = compute_regime_score(nifty_sl, eligible_mask, result["COMPOSITE"])
        dynamic_n = reg["dynamic_n"]
        allow_new = reg["allow_new"]

        elig_df = result[eligible_mask].copy()
        elig_df["RANK"] = elig_df["COMPOSITE"].rank(ascending=False, method="first", na_option="bottom")
        top_candidates = elig_df.sort_values("RANK").index.tolist()

        # Retain held positions
        next_tickers = []
        for ticker, state in current_portfolio.items():
            days_held = (t_date - state["entry_date"]).days
            if ticker in elig_df.index:
                rank  = elig_df.loc[ticker, "RANK"]
                pct52 = elig_df.loc[ticker, "PCT_FROM_52H"]
                if pct52 < -25: continue
                if rank <= 40 or days_held < 28:
                    next_tickers.append(ticker)

        # Fill new slots
        if allow_new:
            slots = dynamic_n - len(next_tickers)
            for t in top_candidates:
                if slots <= 0: break
                if t not in next_tickers:
                    next_tickers.append(t)
                    slots -= 1

        # Volatility-weighted sizing
        raw_w = {}
        for t in next_tickers:
            comp = result.loc[t, "COMPOSITE"]
            px   = sliced.loc[t].dropna()
            if len(px) > 10:
                vols = []
                for w in [252, 189, 126, 63]:
                    pw = px.iloc[-w:] if len(px) >= w else px
                    lr = np.diff(np.log(pw.values))
                    if len(lr) > 5: vols.append(np.std(lr, ddof=1) * np.sqrt(252))
                raw_w[t] = comp / np.mean(vols) if vols and np.mean(vols) > 0 else comp
            else:
                raw_w[t] = comp

        actual = {}
        total_raw = sum(raw_w.values())
        for t in next_tickers:
            nw = raw_w[t] / total_raw if total_raw > 0 else 1.0 / len(next_tickers)
            actual[t] = {
                "entry_date": current_portfolio[t]["entry_date"] if t in current_portfolio else t_date,
                "weight": min(0.05, nw)
            }

        eq_wt   = sum(s["weight"] for s in actual.values())
        cash_wt = max(0.0, 1.0 - eq_wt)
        liq_ret = (1.06 ** (1/52)) - 1.0

        if actual:
            port_list = list(actual.keys())
            s_px  = prices_ff.loc[port_list].iloc[:, idx]
            e_px  = prices_ff.loc[port_list].iloc[:, next_idx]
            stk_r = (e_px / s_px) - 1.0
            w_ser = pd.Series({t: actual[t]["weight"] for t in port_list})
            gross = (stk_r * w_ser).sum() + cash_wt * liq_ret
        else:
            gross = liq_ret

        if pd.isna(gross): gross = 0.0

        all_t = set(current_portfolio) | set(actual)
        tc = sum(abs((actual[t]["weight"] if t in actual else 0.0) -
                     (current_portfolio[t]["weight"] if t in current_portfolio else 0.0))
                 for t in all_t) * FRICTION
        net = gross - tc

        nifty_ret = (nifty_ff.iloc[next_idx] / nifty_ff.iloc[idx]) - 1.0
        equity       *= (1 + net)
        nifty_equity *= (1 + nifty_ret)

        log.append({"Date": t_date, "Equity": equity, "Nifty": nifty_equity,
                    "Net_Return": net, "Holdings": len(actual), "Cash_Wt": cash_wt,
                    "Regime_Score": regime_score})
        current_portfolio = actual

        sys.stdout.write(f"\r    [{i+1}/{len(valid_dates)-1}] "
                         f"{t_date.strftime('%b %Y')} | "
                         f"Eq: {equity:12,.0f} | N={dynamic_n:2d} | RS={regime_score:.2f}")
        sys.stdout.flush()

    print()
    return pd.DataFrame(log)

# ── RUN ALL VARIANTS ──────────────────────────────────────────────────────────
results = {}
for label, desc, use_adj in RUNS:
    results[label] = run_backtest(label, desc, use_adj)

print("\n" + "=" * 75)

# ── METRICS ───────────────────────────────────────────────────────────────────
def calc_metrics(df):
    eq    = df["Equity"]
    years = (df["Date"].iloc[-1] - df["Date"].iloc[0]).days / 365.25
    cagr  = ((eq.iloc[-1] / INITIAL_EQUITY) ** (1/years) - 1.0) * 100
    weekly_ret = df["Net_Return"]
    mdd   = ((eq / eq.cummax()) - 1.0).min() * 100
    ann_vol = weekly_ret.std() * np.sqrt(52) * 100
    sharpe  = (weekly_ret.mean() * 52) / (weekly_ret.std() * np.sqrt(52)) if weekly_ret.std() > 0 else 0
    calmar  = cagr / abs(mdd) if mdd != 0 else 0
    best_wk = weekly_ret.max() * 100
    worst_wk= weekly_ret.min() * 100
    win_rate= (weekly_ret > 0).mean() * 100
    avg_n   = df["Holdings"].mean()
    avg_cash= df["Cash_Wt"].mean() * 100
    return {
        "CAGR %":        round(cagr, 1),
        "MDD %":         round(mdd, 1),
        "Ann Vol %":     round(ann_vol, 1),
        "Sharpe Ratio":  round(sharpe, 2),
        "Calmar Ratio":  round(calmar, 2),
        "Best Week %":   round(best_wk, 1),
        "Worst Week %":  round(worst_wk, 1),
        "Win Rate %":    round(win_rate, 1),
        "Avg Holdings":  round(avg_n, 1),
        "Avg Cash %":    round(avg_cash, 1),
    }

# Nifty metrics
n_eq    = results["BASELINE"]["Nifty"]
n_years = (results["BASELINE"]["Date"].iloc[-1] - results["BASELINE"]["Date"].iloc[0]).days / 365.25
n_cagr  = ((n_eq.iloc[-1] / INITIAL_EQUITY) ** (1/n_years) - 1.0) * 100
n_mdd   = ((n_eq / n_eq.cummax()) - 1.0).min() * 100
n_rets  = n_eq.pct_change().dropna()

metrics = {lbl: calc_metrics(results[lbl]) for lbl, _, _ in RUNS}

print(f"\n{'Metric':<18} {'BASELINE':>14} {'ADJ_SHARPE':>14} {'NIFTY500':>14}")
print("-" * 64)
for m in ["CAGR %", "MDD %", "Ann Vol %", "Sharpe Ratio", "Calmar Ratio",
          "Best Week %", "Worst Week %", "Win Rate %", "Avg Holdings", "Avg Cash %"]:
    row = f"{m:<18} {metrics['BASELINE'][m]:>14} {metrics['ADJ_SHARPE'][m]:>14}"
    if m == "CAGR %":  row += f" {n_cagr:>14.1f}"
    elif m == "MDD %": row += f" {n_mdd:>14.1f}"
    else:              row += f" {'—':>14}"
    print(row)
print("=" * 64)

# ── SAVE SEPARATE FOLDERS ─────────────────────────────────────────────────────
timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
base_dir  = os.path.join("backtest results", f"adj_sharpe_{timestamp}")

for label, _, _ in RUNS:
    run_dir = os.path.join(base_dir, label)
    os.makedirs(run_dir, exist_ok=True)
    results[label].to_csv(os.path.join(run_dir, f"{label}.csv"), index=False)
    print(f"  Saved -> {run_dir}")

# ── CHART ─────────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(15, 10))
gs  = gridspec.GridSpec(2, 2, figure=fig, hspace=0.38, wspace=0.3)

COLORS = {
    "BASELINE":   "#9AA5B4",
    "ADJ_SHARPE": "#1F4E79",
    "NIFTY":      "#CCCCCC",
}

ax1 = fig.add_subplot(gs[0, :])   # equity curve (full width)
ax2 = fig.add_subplot(gs[1, 0])   # drawdown
ax3 = fig.add_subplot(gs[1, 1])   # rolling 52W return

# — Equity Curve —
for label, desc, _ in RUNS:
    df = results[label]; m = metrics[label]
    lw = 2.2 if label == "ADJ_SHARPE" else 1.5
    ax1.plot(df["Date"], df["Equity"], color=COLORS[label], lw=lw,
             label=f"{label}  |  CAGR {m['CAGR %']}%  MDD {m['MDD %']}%  Calmar {m['Calmar Ratio']}")

ax1.plot(results["BASELINE"]["Date"], results["BASELINE"]["Nifty"],
         color=COLORS["NIFTY"], lw=1.3, linestyle=":",
         label=f"NIFTY500  |  CAGR {n_cagr:.1f}%  MDD {n_mdd:.1f}%")
ax1.axhline(INITIAL_EQUITY, color="#DDDDDD", lw=0.8, linestyle="--")
ax1.set_title("Equity Curve — Raw Sharpe vs Adjusted Sharpe (Skew+Kurt)",
              fontweight="bold", fontsize=12)
ax1.set_ylabel("Portfolio Value (₹)")
ax1.legend(fontsize=9, loc="upper left")
ax1.grid(True, alpha=0.18)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"₹{x/1e6:.1f}M"))

# — Drawdown —
for label, _, _ in RUNS:
    eq = results[label]["Equity"]
    dd = (eq / eq.cummax() - 1) * 100
    ax2.fill_between(results[label]["Date"], dd, 0,
                     color=COLORS[label], alpha=0.35, label=label)
    ax2.plot(results[label]["Date"], dd, color=COLORS[label], lw=1.0)
ax2.set_title("Drawdown (%)", fontweight="bold")
ax2.set_ylabel("%")
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.18)

# — Rolling 52W Return —
for label, _, _ in RUNS:
    eq = results[label]["Equity"]
    roll_ret = eq.pct_change(52).dropna() * 100
    dates_roll = results[label]["Date"].iloc[52:]
    ax3.plot(dates_roll, roll_ret.values, color=COLORS[label], lw=1.4, label=label)
ax3.axhline(0, color="#999999", lw=0.8, linestyle="--")
ax3.set_title("Rolling 52-Week Return (%)", fontweight="bold")
ax3.set_ylabel("%")
ax3.legend(fontsize=8)
ax3.grid(True, alpha=0.18)

fig.suptitle(
    f"Raw Sharpe vs Adjusted Sharpe Backtest  |  N500 Universe  |  "
    f"{valid_dates[0].strftime('%b %Y')} – {valid_dates[-2].strftime('%b %Y')}  |  "
    f"Dynamic Regime  |  5% Cap  |  0.20% Friction",
    fontsize=10, color="#444444"
)

out_png = os.path.join(base_dir, "adj_sharpe_comparison.png")
plt.savefig(out_png, dpi=200, bbox_inches="tight")
plt.close()
print(f"\n  Chart  -> {out_png}")
print(f"\n[DONE] All results saved under: {base_dir}")
