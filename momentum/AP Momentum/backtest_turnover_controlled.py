"""
Turnover-Controlled Backtest — Z-Score Strategies with Hold Lock + Dynamic N
==============================================================================
Extends the 3 AP Momentum z-score composite strategies (Z Equal Weight,
Z Weighted v1, Z Weighted v2) with the position-management layer Sharpe.py
actually trades with live, instead of a naive full top-N rebalance:

  1. DYNAMIC N          Portfolio size floats between MIN_N and MAX_N, driven
                         by a continuous 0-1 regime score built from 4 price
                         breadth signals (mirrors momentum_lib.compute_regime_score):
                           - EMA50 breadth      (35%): % of universe > own EMA50
                           - EMA trend breadth  (25%): % of universe with EMA50>EMA200
                           - 52W-high breadth   (25%): % of universe within -25% of 52W high
                           - Momentum breadth   (15%): % of the above group with composite
                                                 z-score > 0 (adapted from Sharpe.py's
                                                 ">1.5" threshold, which is calibrated to
                                                 its normalise_composite()-rescaled score,
                                                 not directly comparable to a raw z-sum)
                         dyn_n = MIN_N + min(score/0.75, 1) * (MAX_N - MIN_N)

  2. HOLD LOCK           A held name can only be rank-exited once it has been
                         held >= MIN_HOLD_DAYS (28 calendar days) AND its rank
                         (by composite, across the full scored universe) has
                         fallen beyond HOLD_RANK_BUFFER = round(2 * MAX_N).

  3. REGIME GATE         New entries only fire when regime score >=
                         NEW_ENTRY_THRESHOLD (0.40); otherwise existing
                         positions are held/exited but no new buys are made
                         and the freed-up capital sits in cash earning
                         LIQUID_YIELD_PA (6% p.a.), matching Sharpe.py.

Exit priority per held name, each rebalance (mirrors Sharpe.py's elif chain
exactly — first match wins):

  1. 52H_BREACH   PCT_FROM_52H < -25%  ->  exit immediately, OVERRIDES the
                  hold lock. This is the escape valve the lock needs: a name
                  crashing >25% off its high gets cut regardless of how
                  recently it was bought or how good its rank still looks.
  2. ADTV_FAIL    Median daily turnover (price x volume) over the last 12M
                  AND 6M both fall below MIN_TURNOVER_CR (Rs 1 Cr) -> exit
                  immediately, OVERRIDES the hold lock (a name that's gone
                  illiquid isn't something you wait out; matches Sharpe.py's
                  "rank_val is NaN" filter-exit branch, checked right after
                  the 52H test).
  3. REL_DD_BREACH  Rel_52H_DD = Stock_PCT_FROM_52H - Bench_PCT_FROM_52H
                  < REL_DD_BREACH_THRESHOLD (-20) -- i.e. the stock is
                  drawing down >20pp worse than NIFTY500 off its own high.
                  Respects the hold lock (checked only if held >= 28 days).
  4. RANK_EXIT    Composite rank -- computed only among names that pass BOTH
                  the 52H and ADTV eligibility gates -- has fallen beyond
                  HOLD_RANK_BUFFER. Respects the hold lock.
  5. else HOLD.

A name that no longer clears MIN_HISTORY to be scored at all is a "data
exit" -- a bookkeeping necessity, not a strategic trigger.

New entries (and RANK itself) are restricted to names passing BOTH the 52H
gate (PCT_FROM_52H >= -25) and the ADTV gate -- an already-disqualified name
can't be bought, and doesn't count toward any held name's rank buffer.

Rebalance is WEEKLY (Monday-anchored) rather than monthly — MIN_HOLD_DAYS=28
is only a meaningful lock relative to a rebalance cadence shorter than it;
Sharpe.py itself rebalances weekly for the same reason.

Scope note: this still omits Sharpe.py's EQ-series and circuit-hit
eligibility filters (Price_Band_List.csv-driven; no equivalent data source
here) -- those are liquidity/tradability filters, orthogonal to the
turnover-control and risk-exit mechanics this script is built to test.

Usage:
    python backtest_turnover_controlled.py
"""

import sys
import warnings
warnings.filterwarnings("ignore")

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

AP_DIR = Path(__file__).resolve().parent
SHARPE_DIR = AP_DIR.parent / "Sharpe"
sys.path.insert(0, str(AP_DIR))
sys.path.insert(0, str(SHARPE_DIR))

import momentum_lib as ml
import zscore_backtest as zb
from backtest_4_strategies import load_price_matrix, WEIGHTS_Z, DATA_FILE

RESULTS_DIR = AP_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

REBALANCE_FREQ = "W-MON"
START_DATE = "2017-11-01"

# -- Position-management config (mirrors Sharpe.py / momentum_lib defaults) --
MIN_N = 5
MAX_N = 25
HOLD_RANK_BUFFER = round(2 * MAX_N)      # 50
MIN_HOLD_DAYS = 28                        # calendar days
NEW_ENTRY_THRESHOLD = 0.40
LIQUID_YIELD_PA = 0.06
REL_DD_BREACH_THRESHOLD = -20             # Rel_52H_DD exit trigger (momentum_lib default)
MIN_TURNOVER_CR = 1.0                     # ADTV eligibility floor, Rs Cr (momentum_lib default)
ADTV_WINDOWS = {"12M": 252, "6M": 126}
SIGNAL_WEIGHTS = {
    "ema50_breadth": 0.35, "ema_trend_breadth": 0.25,
    "breadth": 0.25, "momentum": 0.15,
}


def precompute_regime_frames(prices, tickers):
    """EMA50/EMA200/52W-high are causal (recursive/rolling) filters, so they
    can be computed once over the full history instead of being recomputed
    from scratch at every rebalance -- identical result, far cheaper."""
    tick_px = prices[tickers]
    ema50 = tick_px.ewm(span=50, adjust=False).mean()
    ema200 = tick_px.ewm(span=200, adjust=False).mean()
    roll_max_252 = tick_px.rolling(window=252, min_periods=2).max()
    pct_from_52h = (tick_px / roll_max_252 - 1.0) * 100.0

    bench_roll_max = prices["NIFTY500"].rolling(window=252, min_periods=2).max()
    bench_pct_from_52h = (prices["NIFTY500"] / bench_roll_max - 1.0) * 100.0

    return ema50, ema200, pct_from_52h, bench_pct_from_52h


def precompute_adtv_eligible(prices, tickers):
    """ADTV eligibility (median daily turnover, price x volume, over the
    trailing 12M OR 6M >= MIN_TURNOVER_CR) is a rolling-median causal filter,
    so -- like the regime frames above -- it's computed once over the full
    history rather than recomputed at every rebalance."""
    print("  Loading VOLUME sheet for ADTV eligibility ...")
    vol_df = ml.load_volume(str(DATA_FILE))          # ticker x date
    vol_wide = vol_df.T
    vol_wide.index = pd.to_datetime(vol_wide.index)
    vol_wide = vol_wide.reindex(prices.index)          # align to the (holiday-row-dropped) trading calendar

    tick_px = prices[tickers]
    tick_vol = vol_wide[tickers]
    daily_turnover_cr = (tick_px * tick_vol) / 1e7     # Rs Cr

    eligible = None
    for label, window in ADTV_WINDOWS.items():
        med = daily_turnover_cr.rolling(window=window, min_periods=10).median()
        ok = med >= MIN_TURNOVER_CR
        eligible = ok if eligible is None else (eligible | ok)
    eligible = eligible.fillna(False)
    return eligible


def run_backtest(prices, tickers, rebalance_freq, start_date):
    rebal_dates = prices.resample(rebalance_freq).last().index
    trading_index = prices.index
    snapped = []
    for d in rebal_dates:
        idx = trading_index[trading_index <= d]
        if len(idx) > 0:
            snapped.append(idx[-1])
    snapped = sorted(set(snapped))
    snapped = [d for d in snapped if d >= pd.to_datetime(start_date)]

    print(f"  {len(snapped)} weekly rebalance dates from "
          f"{snapped[0].date()} to {snapped[-1].date()}")

    ema50_all, ema200_all, pct52h_all, bench_pct52h_all = precompute_regime_frames(prices, tickers)
    adtv_eligible_all = precompute_adtv_eligible(prices, tickers)

    held = {name: {} for name in WEIGHTS_Z}          # ticker -> entry_date
    period_returns = {name: [] for name in WEIGHTS_Z}
    diagnostics = {name: [] for name in WEIGHTS_Z}    # (date, dyn_n, regime, n_held, exits, entries)
    period_dates, period_start_dates = [], []

    for i in range(len(snapped) - 1):
        t0, t1 = snapped[i], snapped[i + 1]
        prices_up_to_t0 = prices.loc[:t0]
        fwd_rets = (prices.loc[t1] / prices.loc[t0]) - 1.0
        period_days = (t1 - t0).days
        idle_ret = (1 + LIQUID_YIELD_PA) ** (period_days / 365.25) - 1.0

        raw_z = zb.compute_raw_signals(prices_up_to_t0, tickers)
        if raw_z.empty:
            for name in WEIGHTS_Z:
                period_returns[name].append(0.0)
            period_dates.append(t1)
            period_start_dates.append(t0)
            continue
        z = zb.zscore_cross_section(raw_z)

        # -- Regime Signals 1-3: shared across all 3 strategies (price-only) --
        px_t0 = prices.loc[t0, tickers]
        valid_mask = px_t0.notna() & ema200_all.loc[t0].notna()
        n_valid = int(valid_mask.sum())
        if n_valid > 0:
            ema50_score = float((px_t0[valid_mask] > ema50_all.loc[t0][valid_mask]).sum()) / n_valid
            ema_trend_score = float((ema50_all.loc[t0][valid_mask] > ema200_all.loc[t0][valid_mask]).sum()) / n_valid
        else:
            ema50_score, ema_trend_score = 0.5, 0.5

        pct52h_t0 = pct52h_all.loc[t0]
        pct52h_mask = pct52h_t0 >= -25
        total_stocks = len(pct52h_mask)
        elig_count = int(pct52h_mask.sum())
        breadth_score = elig_count / total_stocks if total_stocks > 0 else 0.5
        eligible_52h_tickers = pct52h_mask[pct52h_mask].index   # regime Signal 3/4: 52H-only, whole universe

        # Rel_52H_DD = Stock_PCT52H - Bench_PCT52H, only defined for 52H-eligible names
        reldd_t0 = (pct52h_t0 - bench_pct52h_all.loc[t0]).where(pct52h_mask)

        # Ranking eligibility gate (separate from the regime signals above,
        # per momentum_lib's design: regime measures the whole universe,
        # ranking/entries/exits are gated by tradability): 52H AND ADTV.
        adtv_ok_t0 = adtv_eligible_all.loc[t0]
        rank_eligible_mask = pct52h_mask & adtv_ok_t0
        rank_eligible_tickers = rank_eligible_mask[rank_eligible_mask].index

        for name, w in WEIGHTS_Z.items():
            composite = sum(z[col].fillna(0) * wt for col, wt in w.items())
            composite = composite.reindex(raw_z.index)

            # -- Regime Signal 4: momentum breadth, adapted threshold (>0) --
            elig_comp = composite.reindex(eligible_52h_tickers)
            pos_mom = int((elig_comp > 0).sum())
            momentum_score = pos_mom / max(1, elig_count)

            regime_score = (
                ema50_score * SIGNAL_WEIGHTS["ema50_breadth"] +
                ema_trend_score * SIGNAL_WEIGHTS["ema_trend_breadth"] +
                breadth_score * SIGNAL_WEIGHTS["breadth"] +
                momentum_score * SIGNAL_WEIGHTS["momentum"]
            )
            dyn_n = int(MIN_N + min(regime_score / 0.75, 1.0) * (MAX_N - MIN_N))
            allow_new = regime_score >= NEW_ENTRY_THRESHOLD

            # Rank is computed only among names passing the 52H+ADTV gate --
            # an ineligible name never has a rank to fall back on.
            ranks = composite.reindex(rank_eligible_tickers).rank(ascending=False, method="first")
            cur_held = held[name]

            # -- Exit evaluation: 52H override -> ADTV override -> Rel-DD (locked) -> Rank (locked) --
            exits = []
            exit_trigger_counts = {"data": 0, "52h": 0, "adtv": 0, "reldd": 0, "rank": 0}
            for tkr, entry_date in list(cur_held.items()):
                comp_val = composite.get(tkr, np.nan)
                if pd.isna(comp_val):
                    exits.append(tkr)   # data exit: dropped out of scored universe
                    exit_trigger_counts["data"] += 1
                    continue

                pct52h_val = pct52h_t0.get(tkr, np.nan)
                days_held_ = (t0 - entry_date).days

                if pd.notna(pct52h_val) and pct52h_val < -25:
                    exits.append(tkr)   # Trigger 1: overrides hold lock
                    exit_trigger_counts["52h"] += 1
                    continue

                if not bool(adtv_ok_t0.get(tkr, False)):
                    exits.append(tkr)   # Trigger 2: ADTV fail, overrides hold lock
                    exit_trigger_counts["adtv"] += 1
                    continue

                reldd_val = reldd_t0.get(tkr, np.nan)
                if pd.notna(reldd_val) and reldd_val < REL_DD_BREACH_THRESHOLD:
                    if days_held_ >= MIN_HOLD_DAYS:
                        exits.append(tkr)   # Trigger 3: respects hold lock
                        exit_trigger_counts["reldd"] += 1
                    continue

                if ranks.get(tkr, np.nan) > HOLD_RANK_BUFFER and days_held_ >= MIN_HOLD_DAYS:
                    exits.append(tkr)   # Trigger 4: respects hold lock
                    exit_trigger_counts["rank"] += 1
            for tkr in exits:
                del cur_held[tkr]

            # -- New entries: only when regime allows, only 52H+ADTV-eligible
            #    names, fill up to dyn_n --
            entries = []
            if allow_new:
                open_slots = dyn_n - len(cur_held)
                if open_slots > 0:
                    ordered = composite.reindex(rank_eligible_tickers).dropna().sort_values(ascending=False).index
                    for tkr in ordered:
                        if len(entries) >= open_slots:
                            break
                        if tkr not in cur_held:
                            cur_held[tkr] = t0
                            entries.append(tkr)

            # -- Period return: invested (equal-weight) + idle cash yield --
            held_tickers = list(cur_held.keys())
            valid_held = [t for t in held_tickers if t in fwd_rets.index and not pd.isna(fwd_rets[t])]
            invested_w = min(len(held_tickers) / dyn_n, 1.0) if dyn_n > 0 else 0.0
            held_ret = fwd_rets[valid_held].mean() if valid_held else 0.0
            idle_w = 1.0 - invested_w
            port_ret = invested_w * held_ret + idle_w * idle_ret

            period_returns[name].append(port_ret)
            diagnostics[name].append({
                "date": t0, "regime_score": regime_score, "dyn_n": dyn_n,
                "n_held": len(held_tickers), "exits": len(exits), "entries": len(entries),
                "exits_52h": exit_trigger_counts["52h"],
                "exits_adtv": exit_trigger_counts["adtv"],
                "exits_reldd": exit_trigger_counts["reldd"],
                "exits_rank": exit_trigger_counts["rank"],
                "exits_data": exit_trigger_counts["data"],
            })

        period_dates.append(t1)
        period_start_dates.append(t0)
        if (i + 1) % 50 == 0 or i == len(snapped) - 2:
            print(f"  Rebalance {i+1}/{len(snapped)-1}  ({t0.date()} -> {t1.date()})")

    returns_df = pd.DataFrame(period_returns, index=period_dates)
    return returns_df, diagnostics, period_start_dates


def main():
    prices, tickers = load_price_matrix()

    print(f"\nRunning turnover-controlled backtest: {len(WEIGHTS_Z)} strategies, "
          f"{REBALANCE_FREQ} rebalance, hold-lock {MIN_HOLD_DAYS}d, "
          f"rank buffer {HOLD_RANK_BUFFER}, dyn N [{MIN_N},{MAX_N}] ...")
    returns_df, diagnostics, period_start_dates = run_backtest(
        prices, tickers, REBALANCE_FREQ, START_DATE)

    bench_series = prices["NIFTY500"]
    bench_period_rets = pd.Series(
        [(bench_series[t1] / bench_series[t0]) - 1.0
         for t0, t1 in zip(period_start_dates, returns_df.index)],
        index=returns_df.index)
    returns_df["NIFTY500 Buy & Hold"] = bench_period_rets

    curves = returns_df.apply(zb.equity_curve)

    print("\n" + "=" * 90)
    print("PERFORMANCE SUMMARY — Turnover-Controlled (Hold Lock + Dynamic N + 52H/ADTV/RelDD Exits), Weekly Rebalance")
    print("=" * 90)
    stats_table = {col: zb.performance_stats(returns_df[col].dropna(), periods_per_year=52)
                   for col in returns_df.columns}
    stats_df = pd.DataFrame(stats_table).T

    # -- Turnover / sizing diagnostics --
    n_years = len(returns_df) / 52
    slugs = {"Z Equal Weight": "equal_weight",
             "Z Weighted v1 (PRIMARY)": "weighted_v1",
             "Z Weighted v2 (Trend+Vol)": "weighted_v2"}
    for name in WEIGHTS_Z:
        diag = pd.DataFrame(diagnostics[name])
        stats_df.loc[name, "Avg Positions Held"] = diag["n_held"].mean()
        stats_df.loc[name, "Avg Regime Score"] = diag["regime_score"].mean()
        stats_df.loc[name, "Exits / Year"] = diag["exits"].sum() / n_years
        stats_df.loc[name, "Entries / Year"] = diag["entries"].sum() / n_years
        stats_df.loc[name, "  - 52H Exits/Yr"] = diag["exits_52h"].sum() / n_years
        stats_df.loc[name, "  - ADTV Exits/Yr"] = diag["exits_adtv"].sum() / n_years
        stats_df.loc[name, "  - RelDD Exits/Yr"] = diag["exits_reldd"].sum() / n_years
        stats_df.loc[name, "  - Rank Exits/Yr"] = diag["exits_rank"].sum() / n_years
        diag.to_csv(RESULTS_DIR / f"diagnostics_{slugs[name]}_turnover_controlled.csv", index=False)

    pct_cols = ["Total Return", "CAGR", "Ann. Volatility", "Max Drawdown", "Win Rate"]
    display_df = stats_df.copy()
    for c in pct_cols:
        display_df[c] = display_df[c].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "--")
    display_df["Sharpe Ratio"] = stats_df["Sharpe Ratio"].apply(lambda x: f"{x:.2f}")
    display_df["Calmar Ratio"] = stats_df["Calmar Ratio"].apply(lambda x: f"{x:.2f}")
    count_cols = ["Avg Positions Held", "Exits / Year", "Entries / Year",
                  "  - 52H Exits/Yr", "  - ADTV Exits/Yr", "  - RelDD Exits/Yr", "  - Rank Exits/Yr"]
    for c in count_cols:
        if c in display_df.columns:
            display_df[c] = stats_df[c].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "--")
    if "Avg Regime Score" in display_df.columns:
        display_df["Avg Regime Score"] = stats_df["Avg Regime Score"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "--")
    print(display_df.to_string())

    stats_df.to_csv(RESULTS_DIR / "performance_summary_turnover_controlled.csv")
    returns_df.to_csv(RESULTS_DIR / "period_returns_turnover_controlled.csv")
    curves.to_csv(RESULTS_DIR / "equity_curves_turnover_controlled.csv")

    plt.figure(figsize=(12, 6.5))
    colors = {"Z Equal Weight": "#1f77b4", "Z Weighted v1 (PRIMARY)": "#ff7f0e",
              "Z Weighted v2 (Trend+Vol)": "#2ca02c", "NIFTY500 Buy & Hold": "#7f7f7f"}
    for col in curves.columns:
        style = "--" if "Buy & Hold" in col else "-"
        lw = 1.6 if "Buy & Hold" in col else 2.2
        plt.plot(curves.index, curves[col], style, label=col, linewidth=lw, color=colors.get(col))
    plt.yscale("log")
    plt.title("Turnover-Controlled Z-Score Strategies: Hold Lock + Dynamic N + 52H/ADTV/RelDD Exits (Weekly)")
    plt.ylabel("Growth of ₹1 (log scale)")
    plt.xlabel("Date")
    plt.legend()
    plt.grid(alpha=0.3, which="both")
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / "equity_curves_turnover_controlled.png", dpi=150)

    print(f"\nSaved outputs to {RESULTS_DIR}:")
    print("  performance_summary_turnover_controlled.csv, period_returns_turnover_controlled.csv,")
    print("  equity_curves_turnover_controlled.csv, equity_curves_turnover_controlled.png,")
    print("  diagnostics_<strategy>_turnover_controlled.csv")


if __name__ == "__main__":
    main()
