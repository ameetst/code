"""
Early Movers signal backtest.

Walks weekly through 10 years of N750 price history, re-running the live
scoring pipeline (momentum_lib.compute_universe_rankings) as of each date
using only data available up to that date, then replays the Early Movers
rising-candidate logic (streak/velocity/zone, ported from
sharpe_dashboard.compute_rising_candidates) against the rolling score
history built up during the walk. Forward returns of flagged tickers are
then measured against NIFTY500 to see whether the signal has real
predictive value, and whether adding a BETA <= 1.25 filter changes that.

Key assumptions (deviations from the live dashboard):
  - held_tickers is always empty. The live dashboard excludes stocks
    already in your portfolio from Early Movers; there is no simulated
    portfolio here, so this measures the raw quality of the signal
    itself, not "would this have added alpha to my actual holdings."
  - Universe is today's N750 constituent list applied across all 10 years
    (the backtest file doesn't carry point-in-time membership) -- this is
    a survivorship-bias caveat on the results, not a bug.
  - ADTV / EQ-series / circuit-hit filters are disabled: the backtest file
    has no VOLUME sheet and no historical Price_Band_List, so those
    eligibility gates can't be reconstructed historically. Only the
    PCT_FROM_52H >= -25 filter (and, in the beta-filtered config,
    BETA <= 1.25) determine eligibility here.
  - Weekly cadence (one evaluation per ISO week) rather than daily, by
    agreement -- Early Movers is a slow-moving signal (streak/velocity
    over several runs), so this trades a bit of resolution for ~5x less
    runtime without changing what the backtest can tell us.

Outputs (written next to this script):
  - signals_log.csv   one row per (eval_date, config, ticker) flagged instance
  - summary.csv        aggregated hit-rate / return stats per config x zone x horizon
"""
import os
import sys
import time
import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import openpyxl
import yfinance as yf

SCRIPT_DIR = Path(__file__).resolve().parent
SHARPE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SHARPE_DIR))
import momentum_lib as ml  # noqa: E402

# ── CONFIG ─────────────────────────────────────────────────────────────────
BACKTEST_FILE = SHARPE_DIR.parent / "ETFs" / "backtest results" / "N750 - Backtest.xlsx"

WINDOWS      = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
TRADING_DAYS = 252
RFR_ANNUAL   = 0.07

BURN_IN_TRADING_DAYS = 252          # need a full 12M window before first eval
FORWARD_HORIZONS     = {"1M": 21, "3M": 63, "6M": 126}   # trading days
BETA_CAP             = 1.25
FAST_MOVER_RANK_CAP  = 200
HISTORY_KEEP_RUNS    = 60           # weekly runs kept for streak/velocity (~1yr)

SIGNALS_LOG_PATH = SCRIPT_DIR / "signals_log.csv"
SUMMARY_PATH     = SCRIPT_DIR / "summary.csv"
NIFTY_CACHE_PATH = SCRIPT_DIR / "nifty500_benchmark_cache.csv"


def get_nifty500_series(target_dates):
    """
    The NIFTY500 row in the backtest file is entirely empty (0/2557 non-null --
    a cached-formula gap, same issue documented in momentum_lib.load_prices).
    Substitute Nifty 500 index history fetched via yfinance (^CRSLDX), cached
    locally, aligned to the backtest file's own trading-date columns.
    """
    target_dates = pd.DatetimeIndex(target_dates)

    if NIFTY_CACHE_PATH.exists():
        cached = pd.read_csv(NIFTY_CACHE_PATH, index_col=0, parse_dates=True)["Close"]
        if cached.index.min() <= target_dates.min() and cached.index.max() >= target_dates.max():
            return cached.reindex(target_dates).ffill()

    print("Fetching NIFTY500 (^CRSLDX) benchmark history from yfinance ...")
    hist = yf.Ticker("^CRSLDX").history(
        start=(target_dates.min() - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
        end=(target_dates.max() + pd.Timedelta(days=5)).strftime("%Y-%m-%d"))
    close = hist["Close"]
    close.index = pd.DatetimeIndex(close.index.date)
    close.to_frame(name="Close").to_csv(NIFTY_CACHE_PATH)
    print(f"  fetched {len(close)} rows ({close.index[0].date()} -> {close.index[-1].date()}), "
          f"cached -> {NIFTY_CACHE_PATH.name}")

    aligned = close.reindex(target_dates).ffill()
    n_missing = aligned.isna().sum()
    if n_missing:
        print(f"  Warning: {n_missing} target date(s) still unfilled after ffill "
              f"(before first available fetch date).")
    return aligned


# ── DATA LOADING (backtest file has 2 leading meta cols: ETF Name | Ticker) ──
def load_backtest_prices(filepath):
    print(f"Loading {filepath.name} ...")
    wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    ws = wb["DATA"]
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = all_rows[0]
    date_indices = [i for i, h in enumerate(header)
                    if isinstance(h, (datetime.datetime, datetime.date))]
    dates = [h.date() if isinstance(h, datetime.datetime) else h
             for h in (header[i] for i in date_indices)]

    tickers, price_matrix = [], []
    for row in all_rows[1:]:
        if row[1] is None:
            continue
        px = []
        for i in date_indices:
            v = row[i] if i < len(row) else None
            try:
                px.append(float(v) if v and float(v) > 0 else np.nan)
            except Exception:
                px.append(np.nan)
        tickers.append(str(row[1]).strip())
        price_matrix.append(px)

    prices_df = pd.DataFrame(price_matrix, index=tickers, columns=pd.to_datetime(dates))
    prices_df = prices_df[~prices_df.index.duplicated(keep="first")]

    nifty_raw = prices_df.loc["NIFTY500"] if "NIFTY500" in prices_df.index else None
    stock_tickers = [t for t in prices_df.index if t != "NIFTY500"]
    prices_df     = prices_df.loc[stock_tickers]

    if nifty_raw is None or nifty_raw.notna().sum() == 0:
        nifty_series = get_nifty500_series(prices_df.columns)
    else:
        nifty_series = nifty_raw.copy()

    print(f"  {len(stock_tickers)} tickers | {len(dates)} dates "
          f"({dates[0]} -> {dates[-1]})")
    return prices_df, nifty_series, stock_tickers, prices_df.columns


def get_weekly_eval_dates(all_dates, start_idx):
    """One evaluation date per ISO week (the last trading day seen that week),
    starting from all_dates[start_idx] onward."""
    buckets = {}
    for d in all_dates[start_idx:]:
        key = (d.isocalendar()[0], d.isocalendar()[1])
        buckets[key] = d  # overwritten each day -> ends up as last trading day of week
    return sorted(buckets.values())


# ── EARLY MOVERS LOGIC (ported from sharpe_dashboard.compute_rising_candidates) ──
def compute_rising_candidates(history, result_df, held_tickers):
    n_runs = len(history)
    if n_runs < 3:
        return pd.DataFrame(), n_runs

    all_tickers = set()
    for entry in history:
        all_tickers.update(entry["scores"].keys())

    score_series = {t: [] for t in all_tickers}
    z3m_series   = {t: [] for t in all_tickers}
    for entry in history:
        z3m_data = entry.get("z3m_scores", {})
        for t in all_tickers:
            score_series[t].append(entry["scores"].get(t, np.nan))
            z3m_series[t].append(z3m_data.get(t, np.nan))

    candidates = []
    for ticker in all_tickers:
        if ticker in held_tickers:
            continue
        if ticker not in result_df.index:
            continue
        rank_val = result_df.loc[ticker, "RANK"]
        if pd.isna(rank_val):
            continue
        rank = int(rank_val)

        scores_arr = np.array(score_series[ticker], dtype=float)
        valid      = ~np.isnan(scores_arr)
        if valid.sum() < 3:
            continue
        clean = scores_arr[valid]

        window = clean[-5:]
        slope  = float(np.polyfit(np.arange(len(window)), window, 1)[0]) if len(window) >= 2 else 0.0

        streak = 0
        for i in range(len(clean) - 1, 0, -1):
            if clean[i] > clean[i - 1]:
                streak += 1
            else:
                break

        z3m_arr   = np.array(z3m_series[ticker], dtype=float)
        z3m_valid = ~np.isnan(z3m_arr)
        z3m_slope  = None
        z3m_streak = None
        if z3m_valid.sum() >= 2:
            z3m_clean = z3m_arr[z3m_valid]
            z3m_win   = z3m_clean[-5:]
            z3m_slope = float(np.polyfit(np.arange(len(z3m_win)), z3m_win, 1)[0]) if len(z3m_win) >= 2 else 0.0
            z3m_streak = 0
            for i in range(len(z3m_clean) - 1, 0, -1):
                if z3m_clean[i] > z3m_clean[i - 1]:
                    z3m_streak += 1
                else:
                    break

        zone = "Near-Term" if rank <= 50 else ("Building" if rank <= 100 else None)
        s_now = float(clean[-1])
        candidates.append({
            "Ticker": ticker, "Zone": zone,
            "_zo": 0 if rank <= 50 else (1 if rank <= 100 else 2),
            "Rank": rank, "Score Now": round(s_now, 3),
            "Velocity": round(slope, 4), "Streak": streak,
            "Z3M Vel": round(z3m_slope, 4) if z3m_slope is not None else None,
            "Z3M Streak": z3m_streak,
        })

    if not candidates:
        return pd.DataFrame(), n_runs

    df = pd.DataFrame(candidates)
    none_zone    = df[df["Zone"].isna() & (df["Rank"] <= FAST_MOVER_RANK_CAP)]
    fast_tickers = none_zone.nlargest(10, "Velocity")["Ticker"].tolist()
    df.loc[df["Ticker"].isin(fast_tickers) & df["Zone"].isna(), "Zone"] = "Fast Mover"

    df = df[df["Zone"].notna() & (df["Velocity"] > 0)].copy()
    df = df.sort_values(["_zo", "Velocity"], ascending=[True, False]).drop(columns=["_zo"])
    return df, n_runs


# ── FORWARD RETURNS ───────────────────────────────────────────────────────────
def forward_return(price_row, date_pos, horizon):
    """price_row: full (unsliced) price Series indexed by date, sorted ascending.
    date_pos: integer position of the evaluation date. Returns pct return to
    date_pos + horizon, or NaN if unavailable."""
    if date_pos + horizon >= len(price_row):
        return np.nan
    p0 = price_row.iloc[date_pos]
    p1 = price_row.iloc[date_pos + horizon]
    if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
        return np.nan
    return (p1 / p0 - 1.0) * 100.0


# ── MAIN WALK-FORWARD LOOP ─────────────────────────────────────────────────
def main():
    t_start = time.time()
    prices_df, nifty_series, stock_tickers, all_dates = load_backtest_prices(BACKTEST_FILE)
    all_dates = list(all_dates)

    if len(all_dates) <= BURN_IN_TRADING_DAYS:
        raise SystemExit("Not enough history for the burn-in window.")

    eval_dates = get_weekly_eval_dates(all_dates, BURN_IN_TRADING_DAYS)

    max_dates = int(os.environ.get("EM_BACKTEST_MAX_DATES", "0"))
    if max_dates > 0:
        eval_dates = eval_dates[:max_dates]
        print(f"[smoke test] limiting to first {max_dates} eval dates")

    print(f"Evaluating {len(eval_dates)} weekly dates from {eval_dates[0]} to {eval_dates[-1]}")

    date_pos = {d: i for i, d in enumerate(all_dates)}
    nifty_row = nifty_series  # full series, position-aligned with all_dates

    history_base = []   # rolling score history for compute_rising_candidates
    history_beta = []
    prev_flagged_base = set()
    prev_flagged_beta = set()

    rows = []
    n_done = 0
    for eval_date in eval_dates:
        pos = date_pos[eval_date]
        prices_hist = prices_df.loc[:, prices_df.columns <= pd.Timestamp(eval_date)]
        nifty_hist  = nifty_series.loc[nifty_series.index <= pd.Timestamp(eval_date)]

        try:
            result, regime_score, regime_detail = ml.compute_universe_rankings(
                prices_hist, nifty_hist, stock_tickers,
                volume_df=None, min_turnover_cr=0,
                eq_series_filter=False, circuit_filter_enabled=False,
                band_csv_path=None, windows=WINDOWS,
                trading_days=TRADING_DAYS, rfr_annual=RFR_ANNUAL)
        except Exception as e:
            print(f"  [{eval_date}] scoring failed: {e}")
            continue

        # ── two eligibility variants sharing one scoring pass ──
        eligible_base = result["PCT_FROM_52H"] >= -25
        eligible_beta = eligible_base & (result["BETA"] <= BETA_CAP)

        rank_base = pd.Series(np.nan, index=result.index)
        rank_base.loc[eligible_base] = result.loc[eligible_base, "COMPOSITE"].rank(
            ascending=False, method="first")
        rank_beta = pd.Series(np.nan, index=result.index)
        rank_beta.loc[eligible_beta] = result.loc[eligible_beta, "COMPOSITE"].rank(
            ascending=False, method="first")

        result_base = result.copy(); result_base["RANK"] = rank_base
        result_beta = result.copy(); result_beta["RANK"] = rank_beta

        entry = {
            "run_date": str(eval_date),
            "scores": result["COMPOSITE"].round(4).dropna().to_dict(),
            "z3m_scores": result["SHARPE_3"].round(4).dropna().to_dict(),
        }
        history_base.append(entry); history_base[:] = history_base[-HISTORY_KEEP_RUNS:]
        history_beta.append(entry); history_beta[:] = history_beta[-HISTORY_KEEP_RUNS:]

        rising_base, _ = compute_rising_candidates(history_base, result_base, held_tickers=set())
        rising_beta, _ = compute_rising_candidates(history_beta, result_beta, held_tickers=set())

        for cfg_name, rising_df, prev_flagged in [
            ("baseline", rising_base, prev_flagged_base),
            ("beta_filtered", rising_beta, prev_flagged_beta),
        ]:
            cur_flagged = set()
            for _, r in rising_df.iterrows():
                ticker = r["Ticker"]
                cur_flagged.add(ticker)
                if ticker not in prices_df.index:
                    continue
                price_row = prices_df.loc[ticker]
                row = {
                    "eval_date": eval_date, "config": cfg_name, "ticker": ticker,
                    "zone": r["Zone"], "rank": r["Rank"], "score_now": r["Score Now"],
                    "velocity": r["Velocity"], "streak": r["Streak"],
                    "first_flag": ticker not in prev_flagged,
                }
                for h_label, h_days in FORWARD_HORIZONS.items():
                    row[f"fwd_{h_label}"]       = forward_return(price_row, pos, h_days)
                    row[f"fwd_{h_label}_nifty"] = forward_return(nifty_row, pos, h_days)
                rows.append(row)
            if cfg_name == "baseline":
                prev_flagged_base = cur_flagged
            else:
                prev_flagged_beta = cur_flagged

        n_done += 1
        if n_done % 25 == 0:
            elapsed = time.time() - t_start
            rate = elapsed / n_done
            remaining = rate * (len(eval_dates) - n_done)
            print(f"  [{n_done}/{len(eval_dates)}] {eval_date}  "
                  f"elapsed={elapsed/60:.1f}m  eta={remaining/60:.1f}m  "
                  f"signals so far: base={len(prev_flagged_base)} beta={len(prev_flagged_beta)}")

    signals_df = pd.DataFrame(rows)
    signals_df.to_csv(SIGNALS_LOG_PATH, index=False)
    print(f"\nWrote {len(signals_df)} signal instances -> {SIGNALS_LOG_PATH}")

    summarize(signals_df)
    print(f"\nTotal runtime: {(time.time() - t_start)/60:.1f} minutes")


def summarize(signals_df):
    if signals_df.empty:
        print("No signals generated -- nothing to summarize.")
        return

    summary_rows = []
    for cfg in signals_df["config"].unique():
        for first_only in [False, True]:
            sub = signals_df[signals_df["config"] == cfg]
            if first_only:
                sub = sub[sub["first_flag"]]
            for zone in list(sub["zone"].dropna().unique()) + ["ALL"]:
                zsub = sub if zone == "ALL" else sub[sub["zone"] == zone]
                if zsub.empty:
                    continue
                for h_label in FORWARD_HORIZONS:
                    col = f"fwd_{h_label}"
                    nifty_col = f"fwd_{h_label}_nifty"
                    valid = zsub[[col, nifty_col]].dropna()
                    if valid.empty:
                        continue
                    excess = valid[col] - valid[nifty_col]
                    summary_rows.append({
                        "config": cfg, "first_flag_only": first_only, "zone": zone,
                        "horizon": h_label, "n": len(valid),
                        "hit_rate_pct": round((valid[col] > 0).mean() * 100, 1),
                        "beat_nifty_pct": round((excess > 0).mean() * 100, 1),
                        "mean_return_pct": round(valid[col].mean(), 2),
                        "median_return_pct": round(valid[col].median(), 2),
                        "mean_excess_vs_nifty_pct": round(excess.mean(), 2),
                    })

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(SUMMARY_PATH, index=False)
    print(f"Wrote summary -> {SUMMARY_PATH}\n")
    with pd.option_context("display.max_rows", None, "display.width", 160):
        print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
