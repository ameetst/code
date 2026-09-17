"""
Verify the skip_days=21 change to momentum_lib.py's compute_residual_momentum
by running the FULL production ranking pipeline (compute_universe_rankings -
the same function Sharpe.py and sharpe_dashboard.py call) twice against the
real live data file, with skip_days=0 (the old behaviour) vs skip_days=21
(what's now the default on disk) - then diff every column of the output.

Read-only: only calls ml.load_prices/load_volume/compute_universe_rankings.
Never touches dashboard_config.json, the positions ledger, equity history,
or places any order - Sharpe.py's trading/execution logic is not invoked.

The "before" run is produced by monkeypatching compute_residual_momentum's
default in-process (via functools.partial) for the duration of one call,
then restoring the real function - momentum_lib.py itself is never edited
by this script.
"""

import functools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SHARPE_DIR = Path(__file__).resolve().parents[1] / "Sharpe"
sys.path.insert(0, str(SHARPE_DIR))
import momentum_lib as ml  # noqa: E402

FILE = str(SHARPE_DIR / "N750_updated.xlsx")
BAND_CSV = str(SHARPE_DIR / "Price_Band_List.csv")

# Same config Sharpe.py actually runs with (dashboard_config.json + its own
# hardcoded constants) - see Sharpe.py lines 100-146.
CFG = dict(
    min_turnover_cr=1.0,
    eq_series_filter=True,
    circuit_filter_enabled=False,
    circuit_threshold=20,
    band_csv_path=BAND_CSV,
    windows={"12M": 252, "9M": 189, "6M": 126, "3M": 63},
    trading_days=252,
    rfr_annual=0.07,
    signal_weights={
        "ema50_breadth": 0.35,
        "ema_trend_breadth": 0.25,
        "breadth": 0.25,
        "momentum": 0.15,
    },
    min_n=5,
    max_n=30,
    new_entry_threshold=0.40,
)


def run_pipeline(prices_df, nifty_series, stock_tickers, volume_df, skip_days):
    orig = ml.compute_residual_momentum
    ml.compute_residual_momentum = functools.partial(orig, skip_days=skip_days)
    try:
        result, regime_score, regime_detail = ml.compute_universe_rankings(
            prices_df, nifty_series, stock_tickers, volume_df=volume_df, **CFG)
    finally:
        ml.compute_residual_momentum = orig
    return result, regime_score, regime_detail


def main():
    print(f"Loading {FILE} ...")
    prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
    print(f"  {len(stock_tickers)} tickers, {dates[0]} -> {dates[-1]}")
    volume_df = ml.load_volume(FILE)

    print("\n--- BEFORE: skip_days=0 (original behaviour) ---")
    before, regime_before, detail_before = run_pipeline(
        prices_df, nifty_series, stock_tickers, volume_df, skip_days=0)

    print("\n--- AFTER: skip_days=21 (current momentum_lib.py default) ---")
    after, regime_after, detail_after = run_pipeline(
        prices_df, nifty_series, stock_tickers, volume_df, skip_days=21)

    print("\n" + "=" * 70)
    print("REGIME SCORE / DYNAMIC N (depends only on breadth/EMA signals,")
    print("not on residual momentum - should be identical)")
    print("=" * 70)
    print(f"  Regime score : before={regime_before:.4f}  after={regime_after:.4f}  "
          f"{'MATCH' if regime_before == regime_after else 'DIFFERS'}")
    print(f"  Dynamic N    : before={detail_before['dynamic_n']}  "
          f"after={detail_after['dynamic_n']}  "
          f"{'MATCH' if detail_before['dynamic_n'] == detail_after['dynamic_n'] else 'DIFFERS'}")

    print("\n" + "=" * 70)
    print("RES_MOM / RZ_* — the columns skip_days actually touches")
    print("=" * 70)
    resmom_cols = ["RS_12M", "RS_9M", "RS_6M", "RS_3M", "RZ_12M", "RZ_9M",
                   "RZ_6M", "RZ_3M", "RES_MOM"]
    resmom_diff = (before[resmom_cols] - after[resmom_cols]).abs()
    n_changed = (resmom_diff.max(axis=1) > 1e-9).sum()
    print(f"  {n_changed}/{len(before)} stocks have a different RES_MOM/RS_*/RZ_* "
          f"value before vs after (expected: most/all of them)")
    print(f"  Mean |RES_MOM before - after| = {resmom_diff['RES_MOM'].mean():.4f}")
    print(f"  Max  |RES_MOM before - after| = {resmom_diff['RES_MOM'].max():.4f}")

    print("\n  Sample (5 tickers), RES_MOM before vs after:")
    sample = before.index[:5]
    print(pd.DataFrame({
        "RES_MOM_before": before.loc[sample, "RES_MOM"],
        "RES_MOM_after":  after.loc[sample, "RES_MOM"],
    }).to_string())

    print("\n" + "=" * 70)
    print("RANK / COMPOSITE / ELIGIBILITY — what actually drives stock selection")
    print("(RES_MOM is joined in AFTER ranking - Sharpe.py itself documents it")
    print(" as 'display only' - so these should be UNCHANGED)")
    print("=" * 70)
    composite_diff = (before["COMPOSITE"] - after["COMPOSITE"]).abs().max()
    rank_diff = (before["RANK"].fillna(-1) != after["RANK"].fillna(-1)).sum()
    elig_before = set(before.index[before["RANK"].notna()])
    elig_after = set(after.index[after["RANK"].notna()])
    print(f"  Max |COMPOSITE before - after| = {composite_diff:.10f}")
    print(f"  Tickers with a different RANK  = {rank_diff}/{len(before)}")
    print(f"  Eligible set identical         = {elig_before == elig_after}")

    top_before = before.sort_values(["RANK", "COMPOSITE"], ascending=[True, False]) \
        .head(detail_before["dynamic_n"]).index.tolist()
    top_after = after.sort_values(["RANK", "COMPOSITE"], ascending=[True, False]) \
        .head(detail_after["dynamic_n"]).index.tolist()
    print(f"  Top-{detail_before['dynamic_n']} selection identical = {top_before == top_after}")

    verdict = (composite_diff < 1e-9 and rank_diff == 0 and
               elig_before == elig_after and top_before == top_after)
    print("\n" + "=" * 70)
    if verdict:
        print("VERDICT: skip_days=21 changes RES_MOM/RZ_* values as intended, and")
        print("has ZERO effect on RANK, COMPOSITE, eligibility, or the actual")
        print("stock selection - confirming RES_MOM is display-only in the live")
        print("pipeline (Sharpe.py's own comment: 'RES_MOM = ... (display only)').")
    else:
        print("VERDICT: UNEXPECTED - RANK/COMPOSITE/selection changed. RES_MOM may")
        print("be wired into selection somewhere this script didn't account for -")
        print("investigate before trusting the 'display only' assumption.")
    print("=" * 70)

    out = before[resmom_cols].add_suffix("_before").join(
        after[resmom_cols].add_suffix("_after")).join(
        before[["RANK", "COMPOSITE"]].add_suffix("_before")).join(
        after[["RANK", "COMPOSITE"]].add_suffix("_after"))
    out_path = Path(__file__).resolve().parent / "skip_days_pipeline_verification.csv"
    out.to_csv(out_path)
    print(f"\nSaved full comparison to {out_path}")


if __name__ == "__main__":
    main()
