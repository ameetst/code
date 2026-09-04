"""
High Beta Rankings — Daily/Point-in-Time Stock Screener
=========================================================
Generates a ranked Top-N list of high-beta NSE stocks as of a given date
(default: the latest date available in the data file), using the same
selection logic validated in ./high_beta_backtest.py (same folder) and
documented in ./HighBeta.md.

INPUT FILE FORMAT — this script targets the N750_updated.xlsx-style file:
  - Two sheets, "DATA" and "VOLUME", each shaped: first column "TICKER", then
    one column per date (prices in DATA; SHARE COUNT, not INR value, in VOLUME).
  - A "NIFTY500" row is used as the benchmark.
  - The file is expected to be a short ROLLING window (N750_updated.xlsx itself
    covers roughly the trailing year), refreshed periodically — NOT the full
    multi-year History_updated.xlsx this script originally targeted. See
    "Rolling-window handling" below for how that's accommodated.
  - VOLUME is share count: this script converts it to INR turnover internally
    as turnover = shares * price before applying the liquidity filter. Pass
    --volume-units value if you ever point this at an older file (like
    History_updated.xlsx) whose VOLUME sheet is already INR turnover.

Selection logic (all defaults match the best-performing backtested variant):
  1. Universe    : all tickers in the DATA sheet, benchmark = NIFTY500.
  2. Liquidity   : median daily traded VALUE (shares x price, unless
                   --volume-units value) over the trailing lookback window
                   >= MIN_TURNOVER (default INR 1,00,00,000 / 1 crore).
  3. Beta        : rolling 6-month (126 trading day) beta vs NIFTY500 daily returns.
  4. Filter 1    : beta > MIN_BETA (default 1.0).
  5. Filter 2    : price within MAX_OFF_52W_HIGH of its trailing 52-week high, i.e.
                   (52w_high - price) / 52w_high < MAX_OFF_52W_HIGH (default 0.25 / 25%).
                   Screens out high-beta names that have already crashed far from
                   their highs ("falling knives").
  6. Filter 3    : the stock's own trailing 1-year daily Sharpe ratio (annualised,
                   using RISK_FREE_RATE) > MIN_SHARPE_1Y (default 1.0).
  7. Rank        : remaining names ranked by 6-month beta, descending. Top N selected
                   (default N=20), equal-weighted.

Rolling-window handling:
  The 52-week-high and 1-year-Sharpe filters, and the liquidity check, nominally
  want a 252-trading-day (~1 year) window. If the input file has fewer than 252
  rows of history available before the as-of date (expected, given
  N750_updated.xlsx-style files carry roughly a year with little to no spare
  buffer), the script automatically uses whatever history IS available for those
  three checks instead of hard-failing, scaling the minimum-coverage requirement
  down proportionally, and prints a warning saying how many days it actually used.
  Only the 6-month (126-day) beta window is non-negotiable — if there isn't even
  126 days of history before the as-of date, the script exits with an error,
  since beta can't be meaningfully estimated at all below that.

IMPORTANT — read before trusting the output:
  - This is a POINT-IN-TIME snapshot for research/monitoring, not a live trading
    signal: it only knows what's in the file you pass it, so if that file hasn't
    been refreshed today, "as of today" really means "as of the file's last date".
  - Fewer than N names can pass all three filters in a given period (this
    happened in 10 of 108 months in the historical monthly backtest, including
    one month with just 1 qualifying name). This script will warn loudly if
    that happens here — do NOT treat a short list as if it were a properly
    diversified 20-name book; consider holding cash or relaxing a filter for
    the shortfall.
  - A short rolling-window file means the 52-week-high and 1-year-Sharpe filters
    may effectively be computed over less than a true year — treat those two
    filters' outputs as more approximate the shorter the window actually used
    (the script tells you the actual window length it applied).

Usage:
    python high_beta_rankings.py --data-file N750_updated.xlsx
    python high_beta_rankings.py --data-file N750_updated.xlsx --top 15
    python high_beta_rankings.py --data-file N750_updated.xlsx --asof 2026-08-27
    python high_beta_rankings.py --data-file N750_updated.xlsx --min-beta 0.0 --max-off-high 1.0 --min-sharpe -999   # no filters
    python high_beta_rankings.py --data-file "C:\\...\\History_updated.xlsx" --volume-units value   # legacy file

Outputs:
    Prints the ranked table to the console, and writes both a timestamped CSV and
    a formatted XLSX into ./rankings_output/ next to this script.
"""

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

# ---------------------------------------------------------------
# Defaults (match the validated backtest configuration)
# ---------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "rankings_output"

BENCHMARK = "NIFTY500"
BETA_LOOKBACK_DAYS = 126     # 6 months
LIQ_LOOKBACK_DAYS = 252      # 12 months (gates on the longest window used by any filter)
LIQ_MIN_HIST_PTS = 200
MIN_BETA_PTS = 60
RISK_FREE_RATE = 0.065       # annualised, used in the 1y Sharpe filter

DEFAULT_TOP_N = 20
DEFAULT_MIN_TURNOVER = 10_000_000   # INR
DEFAULT_MIN_BETA = 1.0
DEFAULT_MAX_OFF_52W_HIGH = 0.25
DEFAULT_MIN_SHARPE_1Y = 1.0


def parse_args():
    p = argparse.ArgumentParser(description="High-beta stock ranking screener")
    p.add_argument("--data-file", type=str, required=True,
                    help="Path to the input workbook (DATA + VOLUME sheets), "
                         "e.g. N750_updated.xlsx")
    p.add_argument("--volume-units", type=str, choices=["shares", "value"], default="shares",
                    help="'shares' (default): VOLUME sheet is share count, converted to INR "
                         "turnover internally as shares*price (matches N750_updated.xlsx). "
                         "'value': VOLUME sheet is already INR turnover (matches the older "
                         "History_updated.xlsx format) — use this for that file.")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="Number of names to shortlist")
    p.add_argument("--asof", type=str, default=None,
                    help="Date (YYYY-MM-DD) to rank as of. Default: latest date in the file")
    p.add_argument("--min-turnover", type=float, default=DEFAULT_MIN_TURNOVER,
                    help="Minimum median daily traded value (INR) over the lookback window")
    p.add_argument("--min-beta", type=float, default=DEFAULT_MIN_BETA,
                    help="Minimum 6-month beta required (set very negative to disable)")
    p.add_argument("--max-off-high", type=float, default=DEFAULT_MAX_OFF_52W_HIGH,
                    help="Max fraction below the 52-week high, e.g. 0.25 = must be within 25%% of high"
                         " (set to 1.0 to disable)")
    p.add_argument("--min-sharpe", type=float, default=DEFAULT_MIN_SHARPE_1Y,
                    help="Minimum trailing 1-year stock Sharpe ratio (set very negative to disable)")
    p.add_argument("--no-save", action="store_true", help="Print only, don't write CSV/XLSX output files")
    return p.parse_args()


def load_data(data_file: Path, volume_units: str):
    if not data_file.exists():
        sys.exit(f"ERROR: data file not found at {data_file}\n"
                  f"Pass --data-file with the correct path to your input workbook")
    print(f"Loading data from {data_file} (volume units: {volume_units}) ...")
    price_raw = pd.read_excel(data_file, sheet_name="DATA", header=0).drop_duplicates(subset="TICKER").set_index("TICKER")
    vol_raw = pd.read_excel(data_file, sheet_name="VOLUME", header=0).drop_duplicates(subset="TICKER").set_index("TICKER")

    price = price_raw.T
    vol = vol_raw.T
    price.index = pd.to_datetime(price.index)
    vol.index = pd.to_datetime(vol.index)
    price = price.sort_index()
    vol = vol.sort_index()

    common = price.columns.intersection(vol.columns)
    price, vol = price[common], vol[common]
    price = price.mask(price <= 0)
    vol = vol.mask(vol < 0).fillna(0)

    if BENCHMARK not in price.columns:
        sys.exit(f"ERROR: benchmark column '{BENCHMARK}' not found in the DATA sheet")

    # drop holiday/blank rows (no price for ANY ticker that "date")
    valid_days = price[BENCHMARK].notna()
    price, vol = price.loc[valid_days], vol.loc[valid_days]

    # Convert VOLUME to INR turnover. "shares" (the N750_updated.xlsx format)
    # needs share-count * price; "value" (the older History_updated.xlsx format)
    # is already INR turnover and is used as-is.
    turnover = vol.mul(price) if volume_units == "shares" else vol

    return price, turnover


def main():
    args = parse_args()
    data_file = Path(args.data_file).resolve()
    price, turnover = load_data(data_file, args.volume_units)

    stock_cols = [c for c in price.columns if c != BENCHMARK]
    trading_days = price.index
    returns = price.pct_change()

    if args.asof:
        asof = pd.Timestamp(args.asof)
        if asof not in trading_days:
            candidates_dates = trading_days[trading_days <= asof]
            if candidates_dates.empty:
                sys.exit(f"ERROR: no trading data on or before {args.asof}")
            asof = candidates_dates[-1]
            print(f"Note: {args.asof} not a trading day in the data; using {asof.date()} instead")
    else:
        asof = trading_days[-1]

    loc = trading_days.get_loc(asof)

    # The 6-month beta window is non-negotiable — beta can't be meaningfully
    # estimated with much less history than that.
    if loc < BETA_LOOKBACK_DAYS + 5:
        sys.exit(f"ERROR: only {loc + 1} trading days available before {asof.date()} — "
                  f"need at least {BETA_LOOKBACK_DAYS + 5} for the 6-month beta window.")

    # The 52w-high / 1y-Sharpe / liquidity windows nominally want 252 days, but
    # a rolling-window file (e.g. N750_updated.xlsx) may carry less. Use
    # whatever's actually available and scale the coverage requirement to match,
    # rather than hard-failing.
    year_window = min(LIQ_LOOKBACK_DAYS, loc + 1)
    year_min_hist = max(1, round(LIQ_MIN_HIST_PTS * year_window / LIQ_LOOKBACK_DAYS))
    if year_window < LIQ_LOOKBACK_DAYS:
        print(f"Note: only {year_window} trading days of history available (full year = "
              f"{LIQ_LOOKBACK_DAYS}) — the 52-week-high, 1-year-Sharpe and liquidity checks "
              f"will use this shorter window; treat them as approximate.")

    print(f"Ranking as of: {asof.date()}  (latest date in file: {trading_days[-1].date()})")

    win_start = loc - year_window + 1

    # ---- liquidity + history eligibility ----
    pwin_liq = price[stock_cols].iloc[win_start: loc + 1]
    twin_liq = turnover[stock_cols].iloc[win_start: loc + 1]
    price_cov = pwin_liq.notna().sum()
    med_turnover = twin_liq.median()
    cur_price_ok = price[stock_cols].loc[asof].notna()
    eligible_mask = (price_cov >= year_min_hist) & (med_turnover >= args.min_turnover) & cur_price_ok
    eligible = set(eligible_mask[eligible_mask].index)
    print(f"Liquid/eligible universe: {len(eligible)} of {len(stock_cols)} stocks "
          f"(median {year_window}d turnover >= INR {args.min_turnover:,.0f})")

    # ---- 6-month beta ----
    beta_start = loc - BETA_LOOKBACK_DAYS + 1
    bwin = returns[BENCHMARK].iloc[beta_start: loc + 1]
    bvar = bwin.var()
    stock_win = returns[stock_cols].iloc[beta_start: loc + 1]
    valid_counts = stock_win.notna().sum()
    cov = stock_win.apply(lambda col: col.cov(bwin))
    beta = (cov / bvar)[valid_counts >= MIN_BETA_PTS]

    candidates = beta[beta.index.isin(eligible)]
    n_liquid = len(candidates)

    # ---- filter 1: beta > MIN_BETA ----
    candidates = candidates[candidates > args.min_beta]
    n_after_beta = len(candidates)

    # ---- filter 2: within X% of 52-week (or shorter-window) high ----
    high_52w = pwin_liq.max()
    cur_price = price[stock_cols].loc[asof]
    off_high = (high_52w - cur_price) / high_52w
    candidates = candidates[candidates.index.isin(off_high[off_high < args.max_off_high].index)]
    n_after_52w = len(candidates)

    # ---- filter 3: trailing 1-year (or shorter-window) stock Sharpe > MIN_SHARPE ----
    rwin_1y = returns[stock_cols].iloc[win_start: loc + 1]
    valid_1y = rwin_1y.notna().sum()
    ann_ret = rwin_1y.mean() * 252
    ann_vol = rwin_1y.std() * np.sqrt(252)
    sharpe_1y = (ann_ret - RISK_FREE_RATE) / ann_vol
    sharpe_1y = sharpe_1y[valid_1y >= year_min_hist]
    candidates = candidates[candidates.index.isin(sharpe_1y[sharpe_1y > args.min_sharpe].index)]
    n_after_sharpe = len(candidates)

    print(f"\nFilter funnel:")
    print(f"  Liquid & has valid beta          : {n_liquid}")
    print(f"  + beta > {args.min_beta:<20}    : {n_after_beta}")
    print(f"  + within {args.max_off_high*100:.0f}% of {year_window}d high{'':<5}: {n_after_52w}")
    print(f"  + {year_window}d Sharpe > {args.min_sharpe:<15}: {n_after_sharpe}")

    ranked = candidates.sort_values(ascending=False)
    top = ranked.head(args.top)

    if len(top) < args.top:
        print(f"\n*** WARNING: only {len(top)} names passed every filter — fewer than the "
              f"requested top {args.top}. ***")
        print("*** Equal-weighting the shortfall means MORE capital per name than intended — "
              "*** consider holding cash for the difference or relaxing a filter rather than "
              "*** over-concentrating into the names that happened to qualify. ***")

    weight_pct = 100.0 / max(len(top), 1)
    result = pd.DataFrame({
        "Rank": range(1, len(top) + 1),
        "Ticker": top.index,
        "Beta (6m)": top.values.round(3),
        f"{year_window}d Sharpe": sharpe_1y.loc[top.index].round(2).values,
        f"% off {year_window}d High": (off_high.loc[top.index] * 100).round(1).values,
        "Last Close (INR)": cur_price.loc[top.index].round(2).values,
        f"Median {year_window}d Turnover (INR)": med_turnover.loc[top.index].round(0).values,
        "Weight %": round(weight_pct, 2),
    }).set_index("Rank")

    print(f"\n=== Top {len(top)} High-Beta Holdings as of {asof.date()} ===\n")
    print(result.to_string())

    if not args.no_save:
        OUTPUT_DIR.mkdir(exist_ok=True)
        stamp = asof.strftime("%Y-%m-%d")
        csv_path = OUTPUT_DIR / f"high_beta_rankings_{stamp}.csv"
        xlsx_path = OUTPUT_DIR / f"high_beta_rankings_{stamp}.xlsx"
        result.to_csv(csv_path)
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
            wb = Workbook()
            ws = wb.active
            ws.title = "Rankings"
            ws["A1"] = f"High Beta Rankings — as of {asof.date()}"
            ws["A1"].font = Font(bold=True, size=13)
            ws["A2"] = (f"beta>{args.min_beta}, within {args.max_off_high*100:.0f}% of 52w high, "
                        f"1y Sharpe>{args.min_sharpe}, min turnover INR {args.min_turnover:,.0f}")
            hdr_font = Font(bold=True, color="FFFFFF")
            hdr_fill = PatternFill("solid", fgColor="1F2937")
            start_row = 4
            ws.cell(row=start_row, column=1, value="Rank").font = hdr_font
            ws.cell(row=start_row, column=1).fill = hdr_fill
            for j, col in enumerate(result.columns, start=2):
                c = ws.cell(row=start_row, column=j, value=col)
                c.font = hdr_font
                c.fill = hdr_fill
            for i, (rank, row) in enumerate(result.iterrows(), start=start_row + 1):
                ws.cell(row=i, column=1, value=rank)
                for j, col in enumerate(result.columns, start=2):
                    ws.cell(row=i, column=j, value=row[col])
            widths = [14, 12, 12, 16, 16, 22, 10]
            for col_letter, w in zip("ABCDEFGH", widths):
                ws.column_dimensions[col_letter].width = w
            wb.save(xlsx_path)
        except ImportError:
            print("(openpyxl not available — skipped .xlsx output, CSV still written)")

        print(f"\nSaved: {csv_path}")
        print(f"Saved: {xlsx_path}")


if __name__ == "__main__":
    main()
