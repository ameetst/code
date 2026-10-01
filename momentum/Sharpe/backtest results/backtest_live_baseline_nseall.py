"""
backtest_live_baseline_nseall.py
==================================
Point-in-time backtest that replicates the CURRENT LIVE Sharpe.py strategy
as closely as possible, run over the full NSEAll universe using the
10-year history file:

    C:\\Users\\ameet\\Documents\\Github\\dhan_datahq\\base files\\History_updated_NSEAll.xlsx

This is the BASELINE run — companion to backtest_live_resmom_gt1_nseall.py,
which is identical except for one added RES_MOM > 1.0 new-entry gate.

Fidelity notes (mirrors live Sharpe.py / dashboard_config.json exactly):
  - Uses momentum_lib.compute_universe_rankings() — the exact single
    source-of-truth ranking pipeline Sharpe.py calls live. This includes
    the MDTV turnover filter, 52H eligibility, and the real 4-signal
    whole-universe regime engine (not a simplified re-implementation).
  - MDTV filter: >= 1.0 Cr (12M or 6M median turnover) — ON (live default)
  - Series-EQ filter and circuit-hit filter — OFF (matches current live
    dashboard_config.json: eq_series_filter=false, circuit_filter_enabled=false)
  - Position cap: 1/MAX_N = 4% (live's actual cap, not the old backtest.py's
    hardcoded 5%)
  - All 4 live exit triggers, not backtest.py's old single rank<=40 rule:
      EXIT_52H     — PCT_FROM_52H < -25%, overrides hold lock
      EXIT_FILTER  — lost RANK for a non-52H reason (MDTV failure etc.)
      EXIT_REL_DD  — REL_52H_DD < -20%, respects 28-day hold lock
      EXIT_RANK    — RANK > 75 (hold_rank_buffer) AND held >= 28 days
  - RES_MOM is computed (it's part of the shared pipeline output) and
    displayed/logged, but NOT used as a filter — matches live.

Disclosed simplification vs. live Sharpe.py:
  Live Sharpe.py only recomputes position weights for that week's
  top-dynamic_n names; a held stock that drifts outside the top-N but
  stays inside the rank-75 buffer keeps its old static weight untouched.
  This backtest fully re-weights (vol-sized, capped) ALL held + new names
  every rebalance, matching how the existing backtest.py research engine
  works — cleaner/more standard for a research comparison, not a 1:1
  mechanical replica of that live weight-update quirk.

Does NOT modify Sharpe.py / momentum_lib.py / dashboard_config.json —
read-only imports only.
"""

import sys
import os
import datetime
import warnings
import pickle

import numpy as np
import pandas as pd
import shutil
import matplotlib.pyplot as plt
from contextlib import contextmanager

SHARPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SHARPE_ROOT not in sys.path:
    sys.path.insert(0, SHARPE_ROOT)

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG (mirrors dashboard_config.json + Sharpe.py live constants) ─────────
FILE = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated_NSEAll.xlsx"
BAND_CSV = os.path.join(SHARPE_ROOT, "Price_Band_List.csv")

RFR_ANNUAL      = 0.07
TRADING_DAYS    = 252
FRICTION        = 0.002        # 0.20% per trade (backtest research convention)
LIQUID_YIELD_PA = 0.06

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}

MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
SIGNAL_WEIGHTS      = {
    "ema50_breadth":     0.35,
    "ema_trend_breadth": 0.25,
    "breadth":           0.25,
    "momentum":          0.15,
}

MIN_TURNOVER_CR         = 1.0     # MDTV filter — ON
EQ_SERIES_FILTER        = False   # matches current live config
CIRCUIT_FILTER_ENABLED  = False   # matches current live config
CIRCUIT_HIT_THRESHOLD   = 20
REL_DD_BREACH_THRESHOLD = -20
HOLD_RANK_BUFFER        = 75
MIN_HOLD_DAYS           = 28

MAX_WT = 1.0 / MAX_N   # 4% position cap

USE_RESMOM_FILTER = False   # baseline run — RES_MOM computed but not used as a filter
RESMOM_THRESHOLD  = 1.0
RUN_LABEL = "LIVE_BASELINE"

# ── CHECKPOINTING — resume after a kill/crash without losing progress ─────────
CHECKPOINT_PATH = os.path.join(SHARPE_ROOT, "backtest results", f".checkpoint_{RUN_LABEL}.pkl")


def save_checkpoint(i, current_portfolio, equity, nifty_equity, results_log):
    """Atomic write: build the full file elsewhere, then rename over the old one."""
    tmp_path = CHECKPOINT_PATH + ".tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump({
            "i": i,
            "current_portfolio": current_portfolio,
            "equity": equity,
            "nifty_equity": nifty_equity,
            "results_log": results_log,
        }, f)
    os.replace(tmp_path, CHECKPOINT_PATH)


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
volume_df = ml.load_volume(FILE)
print(f"Volume data: {'loaded' if volume_df is not None else 'NOT FOUND — MDTV filter will pass everything'}")

dt_idx = pd.DatetimeIndex(dates)
eow_dates = []
for i in range(len(dt_idx) - 1):
    if dt_idx[i].isocalendar().week != dt_idx[i+1].isocalendar().week:
        eow_dates.append(dates[i])
eow_dates.append(dates[-1])

start_idx = 252
valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]

print(f"Total trading days available: {len(dates)}")
print(f"Universe size: {len(stock_tickers)} tickers")
print(f"Valid rebalance points (week-ends): {len(valid_dates)}")
print(f"Run: {RUN_LABEL}  |  RES_MOM filter: "
      f"{'ON (>' + str(RESMOM_THRESHOLD) + ')' if USE_RESMOM_FILTER else 'OFF (display only, matches live)'}")

if len(valid_dates) < 2:
    print("\n[!] ERROR: Insufficient data for backtesting.")
    sys.exit(0)

equity = 2000000.0
nifty_equity = 2000000.0
current_portfolio = {}   # ticker: {'entry_date': date, 'weight': w}
results_log = []
start_i = 0

if os.path.exists(CHECKPOINT_PATH):
    try:
        with open(CHECKPOINT_PATH, "rb") as f:
            ckpt = pickle.load(f)
        start_i            = ckpt["i"] + 1
        current_portfolio  = ckpt["current_portfolio"]
        equity              = ckpt["equity"]
        nifty_equity         = ckpt["nifty_equity"]
        results_log          = ckpt["results_log"]
        print(f"\n[CHECKPOINT FOUND] Resuming from rebalance {start_i}/{len(valid_dates)-1} "
              f"(equity={equity:,.0f}, {len(current_portfolio)} held)")
    except Exception as e:
        print(f"\n[CHECKPOINT] Could not load ({e}) — starting fresh.")
        start_i = 0

prices_df_ffill = prices_df.ffill(axis=1)
nifty_series_ffill = nifty_series.ffill()

print("\nStarting Point-in-Time Vectorised Backtest (live-faithful engine):")
print("-" * 80)

t_run_start = datetime.datetime.now()

for i in range(start_i, len(valid_dates) - 1):
    t_date    = valid_dates[i]
    next_date = valid_dates[i+1]

    idx       = dates.index(t_date)
    next_idx  = dates.index(next_date)

    sliced_prices = prices_df.iloc[:, :idx+1]
    sliced_nifty  = nifty_series.iloc[:idx+1]
    sliced_volume = volume_df.iloc[:, :idx+1] if volume_df is not None else None

    with suppress_stdout():
        result, regime_score, regime_detail = ml.compute_universe_rankings(
            sliced_prices, sliced_nifty, stock_tickers,
            volume_df=sliced_volume,
            min_turnover_cr=MIN_TURNOVER_CR,
            eq_series_filter=EQ_SERIES_FILTER,
            circuit_filter_enabled=CIRCUIT_FILTER_ENABLED,
            circuit_threshold=CIRCUIT_HIT_THRESHOLD,
            band_csv_path=BAND_CSV,
            windows=SHARPE_WINDOWS,
            trading_days=TRADING_DAYS,
            rfr_annual=RFR_ANNUAL,
            signal_weights=SIGNAL_WEIGHTS,
            min_n=MIN_N,
            max_n=MAX_N,
            new_entry_threshold=NEW_ENTRY_THRESHOLD,
        )

    dynamic_n = regime_detail["dynamic_n"]
    allow_new = regime_detail["allow_new"]
    is_cash   = not allow_new
    regime = f"BUY ({regime_score:.2f})" if allow_new else f"CASH ({regime_score:.2f})"

    # ── EXIT EVALUATION — mirrors Sharpe.py's 4 exit triggers exactly ─────────
    next_portfolio_tickers = []
    exit_counts = {"52H": 0, "FILTER": 0, "REL_DD": 0, "RANK": 0}

    for ticker, state in current_portfolio.items():
        entry_date = state['entry_date']
        held_days  = (t_date - entry_date).days

        rank_val   = result.loc[ticker, "RANK"] if ticker in result.index else np.nan
        pct52h_val = result.loc[ticker, "PCT_FROM_52H"] if ticker in result.index else np.nan
        reldd_val  = result.loc[ticker, "REL_52H_DD"] if ticker in result.index else np.nan
        adtv_val   = result.loc[ticker, "ADTV_ELIGIBLE"] if (
            ticker in result.index and "ADTV_ELIGIBLE" in result.columns
        ) else True

        if pd.notna(pct52h_val) and pct52h_val < -25:
            exit_counts["52H"] += 1
            continue
        elif pd.isna(rank_val):
            exit_counts["FILTER"] += 1
            continue
        elif pd.notna(reldd_val) and reldd_val < REL_DD_BREACH_THRESHOLD:
            if held_days >= MIN_HOLD_DAYS:
                exit_counts["REL_DD"] += 1
                continue
            else:
                next_portfolio_tickers.append(ticker)   # locked, held despite rel-dd breach
        elif rank_val > HOLD_RANK_BUFFER:
            if held_days >= MIN_HOLD_DAYS:
                exit_counts["RANK"] += 1
                continue
            else:
                next_portfolio_tickers.append(ticker)   # locked, held despite rank breach
        else:
            next_portfolio_tickers.append(ticker)       # healthy hold

    # ── ENTRY — top dynamic_n by RANK, live's exact mechanism (no backfill) ──
    top_n_tickers = result.head(dynamic_n).index.tolist()
    entry_candidates = []
    if allow_new:
        held_set = set(next_portfolio_tickers)
        raw_candidates = [t for t in top_n_tickers if t not in held_set]
        if USE_RESMOM_FILTER:
            for t in raw_candidates:
                rm = result.loc[t, "RES_MOM"] if "RES_MOM" in result.columns else np.nan
                if pd.notna(rm) and rm > RESMOM_THRESHOLD:
                    entry_candidates.append(t)
        else:
            entry_candidates = raw_candidates

    next_portfolio_tickers.extend(entry_candidates)

    # ── POSITION SIZING — vol-weighted, capped at MAX_WT (full reweight) ─────
    raw_weights = {}
    for ticker in next_portfolio_tickers:
        comp_score = result.loc[ticker, "COMPOSITE"] if ticker in result.index else 0.0
        px = sliced_prices.loc[ticker].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                px_w = px.iloc[-w:] if len(px) >= w else px
                log_r = np.diff(np.log(px_w.values))
                if len(log_r) > 5:
                    vols.append(np.std(log_r, ddof=1) * np.sqrt(252))
            if vols and np.mean(vols) > 0:
                raw_weights[ticker] = comp_score / np.mean(vols)
            else:
                raw_weights[ticker] = comp_score
        else:
            raw_weights[ticker] = comp_score

    total_raw = sum(raw_weights.values())
    actual_portfolio = {}
    for ticker in next_portfolio_tickers:
        norm_w = raw_weights[ticker] / total_raw if total_raw > 0 else 1.0 / len(next_portfolio_tickers)
        capped_w = min(MAX_WT, norm_w)
        if ticker in current_portfolio:
            actual_portfolio[ticker] = {'entry_date': current_portfolio[ticker]['entry_date'], 'weight': capped_w}
        else:
            actual_portfolio[ticker] = {'entry_date': t_date, 'weight': capped_w}

    # ── RETURNS ────────────────────────────────────────────────────────────
    if is_cash and not actual_portfolio:
        gross_ret = (1.0 + LIQUID_YIELD_PA) ** (1/52) - 1.0
    else:
        actual_port_list = list(actual_portfolio.keys())
        total_equity_weight = sum(s['weight'] for s in actual_portfolio.values())
        cash_weight = max(0.0, 1.0 - total_equity_weight)

        if actual_port_list:
            start_px = prices_df_ffill.loc[actual_port_list].iloc[:, idx]
            end_px   = prices_df_ffill.loc[actual_port_list].iloc[:, next_idx]
            stock_returns = (end_px / start_px) - 1.0
            weights_series = pd.Series({t: actual_portfolio[t]['weight'] for t in actual_port_list})
            gross_ret = (stock_returns * weights_series).sum() + (cash_weight * ((1.0 + LIQUID_YIELD_PA) ** (1/52) - 1.0))
        else:
            gross_ret = (1.0 + LIQUID_YIELD_PA) ** (1/52) - 1.0

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

    elapsed = (datetime.datetime.now() - t_run_start).total_seconds()
    sys.stdout.write(f"\r  [{i+1}/{len(valid_dates)-1}] {t_date.strftime('%b %Y')} | "
                     f"Eq: {equity:12,.0f} | RS: {regime_score:.2f} | N={dynamic_n:2d} | "
                     f"Held: {len(actual_portfolio):2d} | Exits(52H/F/DD/RK): "
                     f"{exit_counts['52H']}/{exit_counts['FILTER']}/{exit_counts['REL_DD']}/{exit_counts['RANK']} | "
                     f"elapsed: {elapsed:6.0f}s")
    sys.stdout.flush()

    results_log.append({
        "Rebalance_Date": t_date.strftime("%Y-%m-%d"),
        "Regime": regime,
        "Eligible_Count": int(result["RANK"].notna().sum()),
        "Dynamic_N": dynamic_n,
        "Turnover_Pct": turnover * 100,
        "Exit_52H": exit_counts["52H"],
        "Exit_Filter": exit_counts["FILTER"],
        "Exit_RelDD": exit_counts["REL_DD"],
        "Exit_Rank": exit_counts["RANK"],
        "Gross_Return": gross_ret,
        "Net_Return": net_ret,
        "Nifty_Return": nifty_ret,
        "Equity": equity,
        "Nifty_Equity": nifty_equity,
        "Holdings": ", ".join(actual_portfolio) if actual_portfolio else "CASH"
    })

    current_portfolio = actual_portfolio
    save_checkpoint(i, current_portfolio, equity, nifty_equity, results_log)

print("\n" + "-" * 80)
print("Backtest complete!")

try:
    os.remove(CHECKPOINT_PATH)
except OSError:
    pass

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
run_dir = os.path.join(SHARPE_ROOT, "backtest results", f"run_{timestamp}_live_baseline_nseall")
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

print("\n=== PERFORMANCE SUMMARY ===")
print(f"Run: {RUN_LABEL}  |  Universe: NSEAll ({len(stock_tickers)} tickers)")
print(f"Period: {valid_dates[0].strftime('%b %Y')} to {valid_dates[-2].strftime('%b %Y')} ({years:.2f} years)")
print(f"Strategy CAGR:     {p_cagr:5.1f}%  |  Max Drawdown: {p_mdd:5.1f}%")
print(f"NIFTY500 CAGR:     {n_cagr:5.1f}%  |  Max Drawdown: {n_mdd:5.1f}%")
print("===========================\n")

try:
    df_res['Rebalance_Date'] = pd.to_datetime(df_res['Rebalance_Date'])
    plt.figure(figsize=(12, 6))
    plt.plot(df_res['Rebalance_Date'], df_res['Equity'], label=f"{RUN_LABEL} (CAGR {p_cagr:.1f}%)", color='#0055CC', linewidth=2)
    plt.plot(df_res['Rebalance_Date'], df_res['Nifty_Equity'], label=f"NIFTY500 (CAGR {n_cagr:.1f}%)", color='#555555', linewidth=2, linestyle='--')
    cash_dates = df_res[df_res['Regime'].str.contains("CASH", na=False)]['Rebalance_Date']
    for cd in cash_dates:
        plt.axvspan(cd, cd + pd.Timedelta(days=30), color='red', alpha=0.1, lw=0)
    plt.title(f'{RUN_LABEL} (Live-faithful engine, NSEAll) vs NIFTY500\n(Red shading = CASH Regime)')
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
except Exception:
    pass
