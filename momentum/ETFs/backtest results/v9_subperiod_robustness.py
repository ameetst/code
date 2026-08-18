"""
Sub-period robustness check on the v9 backtest results.

Question: is 3W-STRICT's edge over CURRENT stable across time, or is it
concentrated in the recent (2023+) era when a wave of new thematic/sector
ETFs launched in India (which STRICT's 12M-history requirement filters out)?

Reads the equity curves already saved by etf_backtest_v9_multiwindow_sharpe.py
(v9_<CONFIG>_backtest_equity.csv) -- does not re-run the backtest.

Splits:
  Calendar-year buckets (2020 partial -> 2026 partial): total return per year.
  Two-period split: PRE-2023 (2020-04-01 -> 2022-12-31) vs 2023+ (2023-01-01 -> 2026-07-13),
  each period's CAGR / max-drawdown computed from its own equity path (rebased,
  i.e. as if each period were its own mini-backtest starting at 100).

Usage:
  python v9_subperiod_robustness.py
"""

import pandas as pd
import numpy as np
from pathlib import Path

_BASE = Path(__file__).resolve().parent

CONFIGS = ["CURRENT", "NEW_4W_AVG", "NEW_4W_STRICT", "NEW_3W_AVG", "NEW_3W_STRICT"]
SPLIT_DATE = pd.Timestamp("2023-01-01")


def load_eq(name: str) -> pd.DataFrame:
    df = pd.read_csv(_BASE / f"v9_{name}_backtest_equity.csv", index_col=0, parse_dates=True)
    return df


def period_metrics(eq: pd.DataFrame) -> dict:
    if len(eq) < 2:
        return {"years": np.nan, "total_return": np.nan, "cagr": np.nan, "max_dd": np.nan}
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    start_val = eq["equity"].iloc[0]
    end_val = eq["equity"].iloc[-1]
    total_return = end_val / start_val - 1
    cagr = (end_val / start_val) ** (1 / years) - 1 if years > 0 else np.nan
    peak = eq["equity"].cummax()
    dd = (eq["equity"] - peak) / peak
    max_dd = dd.min()
    return {"years": years, "total_return": total_return, "cagr": cagr, "max_dd": max_dd}


def main():
    eqs = {name: load_eq(name) for name in CONFIGS}

    # ---- Calendar-year total return table ----
    all_years = sorted(set(eqs["CURRENT"].index.year))
    print(f"\n{'='*100}\n  CALENDAR-YEAR TOTAL RETURN (rebased to start of each year's first available date)\n{'='*100}")
    header = f"  {'Year':<8}" + "".join(f"{name:>16}" for name in CONFIGS)
    print(header)
    for yr in all_years:
        row_vals = []
        for name in CONFIGS:
            eq = eqs[name]
            yr_eq = eq[eq.index.year == yr]
            if len(yr_eq) < 2:
                row_vals.append("n/a")
                continue
            ret = yr_eq["equity"].iloc[-1] / yr_eq["equity"].iloc[0] - 1
            row_vals.append(f"{ret:.2%}")
        print(f"  {yr:<8}" + "".join(f"{v:>16}" for v in row_vals))

    # ---- Two-period split ----
    print(f"\n{'='*100}\n  TWO-PERIOD SPLIT: PRE-2023 (2020-04 -> 2022-12) vs 2023+ (2023-01 -> 2026-07)\n{'='*100}")
    for period_label, mask_fn in [
        ("PRE-2023", lambda eq: eq[eq.index < SPLIT_DATE]),
        ("2023-ONWARD", lambda eq: eq[eq.index >= SPLIT_DATE]),
    ]:
        print(f"\n  -- {period_label} --")
        print(f"  {'Config':<16}{'Years':>8}{'TotalRet':>12}{'CAGR':>10}{'MaxDD':>10}")
        for name in CONFIGS:
            sub = mask_fn(eqs[name])
            m = period_metrics(sub)
            print(f"  {name:<16}{m['years']:>8.2f}{m['total_return']:>12.2%}{m['cagr']:>10.2%}{m['max_dd']:>10.2%}")

    # ---- Delta table: 3W-STRICT vs CURRENT, per period ----
    print(f"\n{'='*100}\n  3W-STRICT minus CURRENT (percentage points)\n{'='*100}")
    print(f"  {'Period':<16}{'CAGR delta':>14}{'MaxDD delta':>14}")
    for period_label, mask_fn in [
        ("PRE-2023", lambda eq: eq[eq.index < SPLIT_DATE]),
        ("2023-ONWARD", lambda eq: eq[eq.index >= SPLIT_DATE]),
        ("FULL PERIOD", lambda eq: eq),
    ]:
        m_cur = period_metrics(mask_fn(eqs["CURRENT"]))
        m_3ws = period_metrics(mask_fn(eqs["NEW_3W_STRICT"]))
        cagr_delta = (m_3ws["cagr"] - m_cur["cagr"]) * 100
        dd_delta = (m_3ws["max_dd"] - m_cur["max_dd"]) * 100
        print(f"  {period_label:<16}{cagr_delta:>13.2f}p{dd_delta:>13.2f}p")


if __name__ == "__main__":
    main()
