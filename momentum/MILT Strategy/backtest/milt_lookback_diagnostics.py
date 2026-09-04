"""
milt_lookback_diagnostics.py
=============================
Three cheap diagnostics to evaluate whether BB_WINDOW=30 (weeks) is the
right lookback, before running an expensive full walk-forward sweep.

1. Warmup-exclusion audit -- how many universe tickers are mechanically
   excluded at any given date purely because they don't yet have
   BB_WINDOW+1 weeks of trading history (build_weekly_indicators requires
   len(weekly) >= bb_window + 1 before bb_upper is even defined).

2. Signal-frequency/coverage sweep -- holding BB_STD=3.7 fixed, how many
   entry signals (close > bb_upper) fire per year and how many *unique*
   tickers ever signal, for BB_WINDOW in {10,15,20,25,30,40,50}.

3. Entry-lag on the known FY25 big-gainer list -- for each window, find
   the first signal date for each named ticker and how much of its
   subsequent 12M gain was already gone by the time the signal fired.

Usage
-----
    python milt_lookback_diagnostics.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import BB_STD as LIVE_BB_STD, MA_PERIOD, ATR_PERIOD
from milt_backtest import screen_split_artifacts, DEFAULT_FILE

WINDOWS = [10, 15, 20, 25, 30, 40, 50]
GAINERS = ["CUPID", "STLTECH", "MTARTECH", "QPOWER", "DIACABS",
           "TDPOWERSYS", "LUMAXTECH", "SMLMAH", "NETWEB", "SANSERA"]


def build_weekly_raw(ohlc: dict, exclude: set) -> dict:
    """Resample every ticker to weekly OHLC once (window-independent)."""
    weekly = {}
    for t in ohlc["tickers"]:
        if t in exclude:
            continue
        for field in ("open", "high", "low", "close"):
            df = ohlc[field]
            if df is None or t not in df.index:
                break
        else:
            wdf = ml.resample_weekly_ohlc(
                ohlc["open"].loc[t], ohlc["high"].loc[t],
                ohlc["low"].loc[t], ohlc["close"].loc[t],
            )
            if not wdf.empty:
                weekly[t] = wdf
    return weekly


# ── TEST 1: WARMUP-EXCLUSION AUDIT ───────────────────────────────────────────

def warmup_audit(weekly_raw: dict, master_dates: list):
    print(f"\n{'='*78}\nTEST 1: WARMUP-EXCLUSION AUDIT\n{'='*78}")
    print("At each snapshot date, how many universe tickers are mechanically\n"
          "ineligible (< window+1 weeks of own trading history) purely due to\n"
          "the BB_WINDOW warmup gate -- independent of the breakout condition.\n")

    snap_dates = master_dates[::26][1:]  # roughly every 6 months
    if master_dates[-1] not in snap_dates:
        snap_dates.append(master_dates[-1])

    header = f"{'Date':<12}{'Total':<8}" + "".join(f"w={w:<7}" for w in WINDOWS)
    print(header)
    print("-" * len(header))
    for d in snap_dates:
        n_total = sum(1 for wdf in weekly_raw.values() if (wdf.index <= d).sum() > 0)
        row = f"{d.date().isoformat():<12}{n_total:<8}"
        for w in WINDOWS:
            n_elig = sum(1 for wdf in weekly_raw.values()
                         if (wdf.index <= d).sum() >= w + 1)
            pct = 100.0 * n_elig / n_total if n_total else 0.0
            row += f"{pct:>6.1f}% "
        print(row)

    # Most direct number: as of the latest date, count tickers whose FIRST
    # bar is within the last `w` weeks -- i.e. currently mid-warmup and
    # therefore invisible to the strategy regardless of momentum.
    latest = master_dates[-1]
    print(f"\nAs of {latest.date().isoformat()} -- tickers currently INSIDE their "
          f"warmup window (listed too recently to ever have fired a signal yet):")
    for w in WINDOWS:
        n_warming_up = sum(
            1 for wdf in weekly_raw.values()
            if 0 < len(wdf) < w + 1
        )
        print(f"  BB_WINDOW={w:<3}: {n_warming_up} tickers still mid-warmup "
              f"(need {w+1} weekly bars, i.e. ~{(w+1)/4.33:.1f} months of listed history)")


# ── TEST 2: SIGNAL-FREQUENCY / COVERAGE SWEEP ────────────────────────────────

def coverage_sweep(weekly_raw: dict, bb_std: float = LIVE_BB_STD):
    print(f"\n{'='*78}\nTEST 2: SIGNAL-FREQUENCY / COVERAGE SWEEP  (BB_STD={bb_std} fixed)\n{'='*78}")
    print("Total entry-signal instances (close > bb_upper) and unique tickers\n"
          "ever signaled, per BB_WINDOW. Signals are counted independent of the\n"
          "portfolio engine (no MAX_POSITIONS cap) -- pure indicator coverage.\n")

    print(f"{'BB_WINDOW':<10}{'Total signals':<16}{'Unique tickers':<16}"
          f"{'Signals/yr':<12}{'Avg pre-signal history (wks)':<30}")
    print("-" * 84)

    results = []
    for w in WINDOWS:
        total_signals = 0
        unique_tickers = set()
        pre_hist_weeks = []
        n_years = 0
        for t, wdf in weekly_raw.items():
            if len(wdf) < w + 1:
                continue
            bb_upper = ml.compute_bollinger_upper(wdf["close"], window=w, num_std=bb_std)
            sig = wdf["close"] > bb_upper
            sig = sig.fillna(False)
            n_sig = int(sig.sum())
            if n_sig:
                total_signals += n_sig
                unique_tickers.add(t)
                first_idx = sig.idxmax()
                pos = wdf.index.get_loc(first_idx)
                pre_hist_weeks.append(pos)
            span_years = (wdf.index[-1] - wdf.index[0]).days / 365.25
            n_years = max(n_years, span_years)

        signals_per_yr = total_signals / n_years if n_years else 0.0
        avg_pre_hist = np.mean(pre_hist_weeks) if pre_hist_weeks else float("nan")
        print(f"{w:<10}{total_signals:<16}{len(unique_tickers):<16}"
              f"{signals_per_yr:<12.1f}{avg_pre_hist:<30.1f}")
        results.append({
            "bb_window": w, "total_signals": total_signals,
            "unique_tickers": len(unique_tickers), "signals_per_yr": signals_per_yr,
        })
    return pd.DataFrame(results)


# ── TEST 3: ENTRY-LAG ON KNOWN BIG GAINERS ───────────────────────────────────

def gainer_lag_test(weekly_raw: dict, bb_std: float = LIVE_BB_STD):
    print(f"\n{'='*78}\nTEST 3: ENTRY-LAG ON KNOWN FY25 BIG GAINERS  (BB_STD={bb_std} fixed)\n{'='*78}")
    print("For each window, first signal date/price for each named gainer, and\n"
          "how much of the run from its 52-week low to its current price had\n"
          "already happened by the time the signal fired.\n"
          "'--' = never signaled at this window (either no breakout, or ticker\n"
          "still inside its warmup period for the whole available history).\n")

    present = [t for t in GAINERS if t in weekly_raw]
    missing = [t for t in GAINERS if t not in weekly_raw]
    if missing:
        print(f"Not found in universe / no OHLC: {missing}\n")

    for w in WINDOWS:
        print(f"-- BB_WINDOW = {w} --")
        print(f"  {'Ticker':<12}{'Signal date':<14}{'Signal px':<12}"
              f"{'52w low':<12}{'Current':<12}{'% of move captured':<20}")
        for t in present:
            wdf = weekly_raw[t]
            if len(wdf) < w + 1:
                print(f"  {t:<12}{'-- (in warmup, only ' + str(len(wdf)) + ' wks history) --'}")
                continue
            bb_upper = ml.compute_bollinger_upper(wdf["close"], window=w, num_std=bb_std)
            sig = (wdf["close"] > bb_upper).fillna(False)
            if not sig.any():
                print(f"  {t:<12}{'-- never signaled --'}")
                continue
            first_date = sig.idxmax()
            sig_px = wdf.loc[first_date, "close"]
            low_52w = wdf["close"].min()
            cur_px = wdf["close"].iloc[-1]
            total_move = cur_px - low_52w
            captured_before_signal = sig_px - low_52w
            pct_missed = 100.0 * captured_before_signal / total_move if total_move > 0 else float("nan")
            print(f"  {t:<12}{first_date.date().isoformat():<14}{sig_px:<12.2f}"
                  f"{low_52w:<12.2f}{cur_px:<12.2f}{pct_missed:<20.1f}")
        print()


def main():
    if not Path(DEFAULT_FILE).exists():
        print(f"ERROR: {DEFAULT_FILE} not found.")
        return

    print(f"Loading {DEFAULT_FILE} ...")
    ohlc = ml.load_ohlc(DEFAULT_FILE)
    print(f"  {len(ohlc['tickers'])} tickers | {len(ohlc['dates'])} daily bars")

    flagged = screen_split_artifacts(ohlc)
    print(f"Excluded {len(flagged)} split-artifact ticker(s)")

    weekly_raw = build_weekly_raw(ohlc, exclude=flagged)
    print(f"{len(weekly_raw)} tickers with usable weekly OHLC")

    master_dates = sorted(set().union(*[set(wdf.index) for wdf in weekly_raw.values()]))

    warmup_audit(weekly_raw, master_dates)
    coverage_df = coverage_sweep(weekly_raw)
    gainer_lag_test(weekly_raw)

    coverage_df.to_csv(BACKTEST_DIR / "milt_lookback_coverage_sweep.csv", index=False)
    print(f"\nSaved milt_lookback_coverage_sweep.csv")


if __name__ == "__main__":
    main()
