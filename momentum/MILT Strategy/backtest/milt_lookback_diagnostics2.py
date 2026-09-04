"""
milt_lookback_diagnostics2.py
===============================
Follow-up to milt_lookback_diagnostics.py's finding that
compute_bollinger_upper() computes mean/std over a window that INCLUDES the
current bar -- so a breakout week inflates its own band, which explains why
short windows produced zero signals and why longer windows caught more of
the named gainers than BB_WINDOW=30 does.

Test 4: LAGGED BAND -- redefine the band using only the PRIOR `window` weeks
(close.shift(1).rolling(window)), so this week's close is judged against a
band that doesn't already contain this week's own move. Does this restore
short-window viability? Re-run the signal-frequency sweep and gainer-lag
test with this definition, at the same WINDOWS as before.

Test 5: BB_STD SWEEP at fixed BB_WINDOW=30 -- since Test 3 suggested
BB_STD=3.7 (not window length) is what's gatekeeping QPOWER/NETWEB-style
movers, sweep std over a range and check signal counts / gainer coverage,
then run full walk-forward backtests (via milt_variant_backtest.py) for
the most interesting std values to see the CAGR/Sharpe/MaxDD/win-rate cost.

Usage
-----
    python milt_lookback_diagnostics2.py
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKTEST_DIR = Path(__file__).resolve().parent
MAIN_DIR = BACKTEST_DIR.parent
if str(MAIN_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_DIR))

import momentum_lib as ml
from milt_strategy import BB_STD as LIVE_BB_STD, BB_WINDOW as LIVE_BB_WINDOW
from milt_backtest import screen_split_artifacts, compute_metrics, trade_stats, DEFAULT_FILE

from milt_lookback_diagnostics import build_weekly_raw, WINDOWS, GAINERS

STD_VALUES = [2.0, 2.5, 3.0, 3.1, 3.3, 3.5, 3.7, 4.0]


def compute_bollinger_upper_lagged(close: pd.Series, window: int,
                                    num_std: float) -> pd.Series:
    """Same as ml.compute_bollinger_upper but computed on the PRIOR `window`
    bars only -- this week's close never contributes to its own band."""
    prior = close.shift(1)
    mid = prior.rolling(window).mean()
    std = prior.rolling(window).std(ddof=0)
    return mid + num_std * std


# ── TEST 4: LAGGED (NON-SELF-REFERENTIAL) BAND ───────────────────────────────

def lagged_band_coverage_sweep(weekly_raw: dict, bb_std: float = LIVE_BB_STD):
    print(f"\n{'='*84}\nTEST 4a: SIGNAL-FREQUENCY SWEEP -- LAGGED BAND  (BB_STD={bb_std} fixed)\n{'='*84}")
    print("Band computed on the PRIOR `window` weeks only (close.shift(1).rolling(w)).\n"
          "Compare directly against the self-inclusive Test 2 numbers.\n")

    print(f"{'BB_WINDOW':<10}{'Total signals':<16}{'Unique tickers':<16}{'Signals/yr':<12}")
    print("-" * 54)
    for w in WINDOWS:
        total_signals = 0
        unique_tickers = set()
        n_years = 0
        for t, wdf in weekly_raw.items():
            if len(wdf) < w + 2:  # need one extra bar since band is lagged by 1
                continue
            bb_upper = compute_bollinger_upper_lagged(wdf["close"], window=w, num_std=bb_std)
            sig = (wdf["close"] > bb_upper).fillna(False)
            n_sig = int(sig.sum())
            if n_sig:
                total_signals += n_sig
                unique_tickers.add(t)
            span_years = (wdf.index[-1] - wdf.index[0]).days / 365.25
            n_years = max(n_years, span_years)
        signals_per_yr = total_signals / n_years if n_years else 0.0
        print(f"{w:<10}{total_signals:<16}{len(unique_tickers):<16}{signals_per_yr:<12.1f}")


def lagged_band_gainer_test(weekly_raw: dict, bb_std: float = LIVE_BB_STD):
    print(f"\n{'='*84}\nTEST 4b: NAMED-GAINER COVERAGE -- LAGGED BAND  (BB_STD={bb_std} fixed)\n{'='*84}")
    present = [t for t in GAINERS if t in weekly_raw]
    for w in WINDOWS:
        hits = []
        for t in present:
            wdf = weekly_raw[t]
            if len(wdf) < w + 2:
                continue
            bb_upper = compute_bollinger_upper_lagged(wdf["close"], window=w, num_std=bb_std)
            sig = (wdf["close"] > bb_upper).fillna(False)
            if sig.any():
                hits.append(t)
        print(f"  BB_WINDOW={w:<3}: caught {len(hits)}/{len(present)}  -> {hits}")


# ── TEST 5: BB_STD SWEEP AT FIXED WINDOW=30 ──────────────────────────────────

def std_coverage_sweep(weekly_raw: dict, window: int = LIVE_BB_WINDOW):
    print(f"\n{'='*84}\nTEST 5a: SIGNAL-FREQUENCY / GAINER COVERAGE vs BB_STD  (BB_WINDOW={window} fixed)\n{'='*84}")
    present = [t for t in GAINERS if t in weekly_raw]

    print(f"{'BB_STD':<10}{'Total signals':<16}{'Unique tickers':<16}{'Gainers caught':<16}{'Which'}")
    print("-" * 90)
    for std in STD_VALUES:
        total_signals = 0
        unique_tickers = set()
        hits = []
        for t, wdf in weekly_raw.items():
            if len(wdf) < window + 1:
                continue
            bb_upper = ml.compute_bollinger_upper(wdf["close"], window=window, num_std=std)
            sig = (wdf["close"] > bb_upper).fillna(False)
            if int(sig.sum()):
                total_signals += int(sig.sum())
                unique_tickers.add(t)
                if t in present:
                    hits.append(t)
        print(f"{std:<10}{total_signals:<16}{len(unique_tickers):<16}{len(hits)}/{len(present):<13} {hits}")


def std_full_backtest_sweep(std_candidates: list):
    print(f"\n{'='*84}\nTEST 5b: FULL WALK-FORWARD BACKTEST vs BB_STD  (BB_WINDOW={LIVE_BB_WINDOW} fixed)\n{'='*84}")
    print(f"Running milt_variant_backtest.py --bb-window {LIVE_BB_WINDOW} --bb-std <std> "
          f"for std in {std_candidates} ...\n")

    print(f"{'BB_STD':<8}{'CAGR%':<10}{'Vol%':<9}{'Sharpe':<9}{'MaxDD%':<10}"
          f"{'Trades':<9}{'WinRate%':<10}")
    print("-" * 65)
    for std in std_candidates:
        result = subprocess.run(
            [sys.executable, "milt_variant_backtest.py",
             "--bb-window", str(LIVE_BB_WINDOW), "--bb-std", str(std)],
            cwd=str(BACKTEST_DIR), capture_output=True, text=True, timeout=600,
        )
        out = result.stdout
        std_tag = str(std).replace(".", "p")
        equity_csv = BACKTEST_DIR / f"milt_variant_bbw{LIVE_BB_WINDOW}_std{std_tag}_equity.csv"
        trades_csv = BACKTEST_DIR / f"milt_variant_bbw{LIVE_BB_WINDOW}_std{std_tag}_trades.csv"
        if not equity_csv.exists():
            print(f"{std:<8} FAILED -- {out[-400:]}")
            continue
        equity_df = pd.read_csv(equity_csv)
        trades_df = pd.read_csv(trades_csv)
        from milt_backtest import DEFAULT_CAPITAL
        metrics = compute_metrics(equity_df, DEFAULT_CAPITAL)
        tstats = trade_stats(trades_df)
        print(f"{std:<8}{metrics['cagr_pct']:<10.2f}{metrics['ann_vol_pct']:<9.2f}"
              f"{metrics['sharpe']:<9}{metrics['max_drawdown_pct']:<10.2f}"
              f"{tstats['n_trades']:<9}{tstats.get('win_rate_pct', float('nan')):<10}")


def main():
    if not Path(DEFAULT_FILE).exists():
        print(f"ERROR: {DEFAULT_FILE} not found.")
        return

    print(f"Loading {DEFAULT_FILE} ...")
    ohlc = ml.load_ohlc(DEFAULT_FILE)
    flagged = screen_split_artifacts(ohlc)
    weekly_raw = build_weekly_raw(ohlc, exclude=flagged)
    print(f"{len(weekly_raw)} tickers with usable weekly OHLC\n")

    lagged_band_coverage_sweep(weekly_raw)
    lagged_band_gainer_test(weekly_raw)

    std_coverage_sweep(weekly_raw)
    std_full_backtest_sweep([2.5, 3.0, 3.1, 3.5, 3.7])


if __name__ == "__main__":
    main()
