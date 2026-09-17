"""
Compare the Blitz-faithful residual momentum backtest (nse500_residual_momentum.py
--source-xlsx) against a backtest of the SAME long/short mechanics driven by the
practitioner IR signal (residual_momentum.py's RES_MOM composite) instead.

Both signals rank the same universe every month and are run through the exact
same backtest() engine (top-20%/bottom-20%, monthly rebalance, no transaction
costs) - so any performance gap reflects the signal, not the mechanics.

Since residual_momentum.py only computes RES_MOM as of "today", this script
adds a historical version: for each monthly formation date, it recomputes the
composite using only daily price data available up to that date (a proper
walk-forward signal, not a single snapshot) - four trailing-window (12M/9M/6M/3M
trading day) residual Information Ratios, cross-sectionally Z-scored and
zero-filled/averaged exactly as residual_momentum.py does it.

Two comparisons are reported:
  - "Full range"    : the practitioner signal's own natural backtest window
                       (starts ~12 months in, once the shortest window has
                       enough daily history - much earlier than Blitz's
                       36-month regression requirement).
  - "Matched period" : both backtests restricted to the exact months the
                       Blitz backtest covers (read from its saved Excel
                       Monthly_Returns sheet), for a true apples-to-apples
                       comparison over the same market conditions.
"""

from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

import residual_momentum as rm
import nse500_residual_momentum as nse

SCRIPT_DIR = Path(__file__).resolve().parent
BLITZ_RESULTS_XLSX = SCRIPT_DIR / "History_updated_residual_momentum_results.xlsx"

START, END = nse.DEFAULT_START, "2026-09"
TOP, BOT, LONG_ONLY = nse.DEFAULT_TOP, nse.DEFAULT_BOT, False


def load_blitz_perf(path: Path) -> pd.DataFrame:
    """Re-load the Blitz backtest's monthly returns from its saved Excel
    report, rather than re-running the ~5-minute 36-month rolling regression."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["Monthly_Returns"]
    rows = list(ws.iter_rows(min_row=3, values_only=True))
    wb.close()
    df = pd.DataFrame(rows, columns=["date", "long_ret", "short_ret", "ls_ret", "n_long", "n_short"])
    df["date"] = pd.to_datetime(df["date"], format="%b-%Y") + pd.offsets.MonthEnd(0)
    return df.set_index("date")


def build_matched_universe():
    """Same universe/returns nse500_residual_momentum.py --source-xlsx built
    (655 stocks, liquidity filter off) - so both signals rank an identical
    monthly returns panel."""
    monthly_prices, avg_tv = nse.monthly_from_xlsx(rm.SOURCE_XLSX, START, END)
    returns = nse.compute_returns(monthly_prices)
    liquid = nse.liquidity_filter_from_workbook(monthly_prices, avg_tv, 0.0)
    return returns[liquid]


SKIP_DAYS = 21  # ~1 trading month


def historical_practitioner_signal(daily_prices: pd.DataFrame, nifty_daily: pd.Series,
                                    tickers: list, formation_dates,
                                    skip_days: int = 0) -> pd.DataFrame:
    """RES_MOM composite as of each formation date, using only data <= that
    date. Log-returns and stock/market alignment don't depend on the
    truncation point, so both are computed once and just sliced per date -
    avoids redoing diff()/dropna() for every (date, ticker, window).

    skip_days > 0 shifts each window's regression fit AND score back by
    that many trading days - i.e. the window covers [t-skip-window, t-skip)
    instead of [t-window, t) - dropping the most recent ~month from the
    calculation entirely. Same reversal-avoidance rationale as classic
    12-1 skip-month momentum, adapted to an IR-style signal that has no
    separate formation sub-period to apply the skip to on its own."""
    stock_log = np.log(daily_prices[tickers]).diff()
    mkt_log = np.log(nifty_daily).diff()

    aligned = {}
    for t in tickers:
        a = pd.concat([stock_log[t], mkt_log], axis=1, join="inner").dropna()
        a.columns = ["s", "m"]
        aligned[t] = a

    signal = pd.DataFrame(index=formation_dates, columns=tickers, dtype=float)

    for d in formation_dates:
        rs = {}
        for label, window in rm.WINDOWS.items():
            needed = window + skip_days
            col = np.full(len(tickers), np.nan)
            for j, t in enumerate(tickers):
                a = aligned[t]
                sub = a.loc[:d]
                if len(sub) < max(needed * 0.90, 10):
                    continue
                sub = sub.iloc[-needed:-skip_days] if skip_days > 0 else sub.iloc[-window:]
                s = sub["s"].values - rm.RFR_DAILY
                m = sub["m"].values - rm.RFR_DAILY
                X = np.column_stack([np.ones(len(m)), m])
                try:
                    coeffs, _, _, _ = np.linalg.lstsq(X, s, rcond=None)
                except np.linalg.LinAlgError:
                    continue
                resid = s - X @ coeffs
                sd = resid.std(ddof=2)
                if sd < 1e-12:
                    continue
                col[j] = (coeffs[0] / sd) * np.sqrt(rm.TRADING_DAYS)
            rs[label] = pd.Series(col, index=tickers)

        rz = pd.DataFrame({f"RZ_{l}": rm._cross_section_z(rs[l]) for l in rm.WINDOWS})
        rz = rz.fillna(0.0)  # matches momentum_lib.py's zero-fill convention
        signal.loc[d] = rz.mean(axis=1)

    return signal.astype(float)


def compare_metrics(perf_a: pd.DataFrame, perf_b: pd.DataFrame, label_a: str, label_b: str):
    ma = nse.performance_metrics(perf_a, nse.RF_ANNUAL_DEFAULT)
    mb = nse.performance_metrics(perf_b, nse.RF_ANNUAL_DEFAULT)
    rows = []
    for leg in ["Long Leg", "Short Leg", "L/S Portfolio"]:
        for stat in ["Ann. Return (%)", "Ann. Volatility (%)", "Sharpe Ratio",
                     "Max Drawdown (%)", "Win Rate (%)", "t-stat", "p-value"]:
            key = f"{leg} {stat}"
            rows.append({"Leg": leg, "Stat": stat, label_a: ma.get(key), label_b: mb.get(key)})
    return pd.DataFrame(rows)


def main():
    print("Loading Blitz backtest results (from saved Excel report) ...")
    blitz_perf = load_blitz_perf(BLITZ_RESULTS_XLSX)
    print(f"  {len(blitz_perf)} months: {blitz_perf.index.min():%Y-%m} -> {blitz_perf.index.max():%Y-%m}")

    print("\nRebuilding matched universe/returns (same as the Blitz run) ...")
    returns = build_matched_universe()
    print(f"  {returns.shape[1]} stocks, {returns.shape[0]} months")

    print("\nLoading daily prices for the practitioner signal ...")
    daily_prices, nifty_daily, _ = rm.load_prices(rm.SOURCE_XLSX)
    daily_prices = daily_prices.T  # -> index=date, columns=ticker
    # rm.load_prices builds its date columns as plain datetime.date objects,
    # not Timestamps - normalize so slicing against returns.index (Timestamps
    # from nse.monthly_from_xlsx) compares correctly.
    daily_prices.index = pd.to_datetime(daily_prices.index)
    nifty_daily.index = pd.to_datetime(nifty_daily.index)
    tickers = [t for t in returns.columns if t in daily_prices.columns]
    print(f"  {len(tickers)} tickers usable for the practitioner signal")

    print(f"\nComputing historical practitioner (RES_MOM) signal with a {SKIP_DAYS}-trading-day "
          f"skip gap for {len(returns.index)} months x {len(tickers)} tickers ...")
    signal = historical_practitioner_signal(daily_prices, nifty_daily, tickers, returns.index,
                                             skip_days=SKIP_DAYS)
    scored_per_month = signal.notna().sum(axis=1)
    print(f"  Median tickers scored/month: {scored_per_month.median():.0f}")

    print("\nBacktesting skip-month practitioner signal with identical mechanics "
          "(top/bot, monthly rebalance) ...")
    practitioner_perf = nse.backtest(returns, signal, TOP, BOT, LONG_ONLY)
    print(f"  {len(practitioner_perf)} monthly observations (full range)")

    matched_idx = practitioner_perf.index.intersection(blitz_perf.index)
    practitioner_matched = practitioner_perf.loc[matched_idx]
    blitz_matched = blitz_perf.loc[matched_idx]
    print(f"  {len(matched_idx)} months overlap with the Blitz backtest's period "
          f"({matched_idx.min():%Y-%m} -> {matched_idx.max():%Y-%m})")

    print(f"\n=== FULL RANGE: skip-month practitioner IR's own natural backtest window ===")
    full_metrics = nse.performance_metrics(practitioner_perf, nse.RF_ANNUAL_DEFAULT)
    for k, v in full_metrics.items():
        print(f"  {k:<35} {v:>10}")

    print("\n=== MATCHED PERIOD: Blitz vs Practitioner IR (skip-month), same months ===")
    cmp_df = compare_metrics(blitz_matched, practitioner_matched, "Blitz", "Practitioner_IR_SkipMonth")

    # Fold in the original (no-skip) numbers from the prior run, if saved,
    # so the before/after effect of adding the skip gap is visible directly.
    baseline_path = SCRIPT_DIR / "backtest_comparison_matched.csv"
    if baseline_path.exists():
        baseline = pd.read_csv(baseline_path)[["Leg", "Stat", "Practitioner_IR"]]
        cmp_df = cmp_df.merge(baseline, on=["Leg", "Stat"], how="left")
        cmp_df = cmp_df[["Leg", "Stat", "Blitz", "Practitioner_IR", "Practitioner_IR_SkipMonth"]]

    with pd.option_context("display.float_format", "{:.3f}".format, "display.width", 140):
        print(cmp_df.to_string(index=False))

    corr = blitz_matched["ls_ret"].corr(practitioner_matched["ls_ret"])
    print(f"\nCorrelation of monthly L/S returns (Blitz vs skip-month Practitioner IR) "
          f"over matched period: {corr:.3f}")

    cmp_df.to_csv(SCRIPT_DIR / "backtest_comparison_matched_skipmonth.csv", index=False)
    practitioner_perf.to_csv(SCRIPT_DIR / "practitioner_ir_backtest_full_skipmonth.csv")
    print(f"\nSaved backtest_comparison_matched_skipmonth.csv and "
          f"practitioner_ir_backtest_full_skipmonth.csv")


if __name__ == "__main__":
    main()
