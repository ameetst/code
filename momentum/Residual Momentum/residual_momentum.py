"""
Residual Momentum screener over the full universe in dhan_datahq's
base files/History_updated.xlsx.

Methodology (matches the production implementation in
code/momentum/Sharpe/momentum_lib.py's compute_residual_momentum, reimplemented
here standalone so this doesn't depend on importing the Sharpe strategy):

- For each stock, date-align its daily log-returns with NIFTY500's daily
  log-returns (NIFTY500 is carried as its own ticker row inside the DATA
  sheet) and run OLS: stock_excess_return = alpha + beta * market_excess_return,
  over a trailing window of trading days.
- The stock's score for that window is its annualised residual Information
  Ratio: (alpha / std-dev of residuals) * sqrt(trading_days). This isolates
  the part of a stock's momentum that is NOT explained by market beta -
  "residual" as in regression residual, not raw price momentum.
- Repeated over four windows (12M/9M/6M/3M trading days), each Z-scored
  cross-sectionally across the universe, then averaged into one composite
  RES_MOM score per stock. A stock missing some windows (partial history)
  has those window Z-scores zero-filled before averaging (matches
  momentum_lib.py's compute_residual_momentum exactly).

No skip-month gap is applied (this measures alpha over the whole window,
not a raw cumulative return, so the short-term-reversal contamination that
skip-month exists to avoid in classic 12-1 momentum isn't the same concern).

Excludes NIFTY500 itself and any ticker with fewer than ~90 valid trading
days of price history (too little data for even the shortest 3M window).
"""

import datetime
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
DHAN_DATAHQ = SCRIPT_DIR.parents[2] / "dhan_datahq"

SOURCE_XLSX = DHAN_DATAHQ / "base files" / "History_updated.xlsx"
OUTPUT_CSV = SCRIPT_DIR / "residual_momentum.csv"

WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
TRADING_DAYS = 252
RFR_ANNUAL = 0.07
RFR_DAILY = RFR_ANNUAL / TRADING_DAYS
MIN_HISTORY_DAYS = 90


def load_prices(filepath: str):
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
        if row[0] is None:
            continue
        px = []
        for i in date_indices:
            v = row[i] if i < len(row) else None
            try:
                px.append(float(v) if v and float(v) > 0 else np.nan)
            except Exception:
                px.append(np.nan)
        ticker_name = str(row[0]).strip()
        if ticker_name.upper() in ("NIFTY 500", "NIFTY500"):
            ticker_name = "NIFTY500"
        tickers.append(ticker_name)
        price_matrix.append(px)

    prices_df = pd.DataFrame(price_matrix, index=tickers, columns=dates)
    prices_df = prices_df[~prices_df.index.duplicated(keep="first")]

    nifty_series = prices_df.loc["NIFTY500"].copy()
    stock_tickers = [t for t in prices_df.index if t != "NIFTY500"]
    prices_df = prices_df.loc[stock_tickers]

    return prices_df, nifty_series, stock_tickers


def _residual_information_ratio(stock_series: pd.Series, mkt_rets: pd.Series,
                                 window: int) -> float:
    if stock_series.notna().sum() < 2:
        return np.nan
    # Diff before dropping NaN so a stock-specific gap (halt / no-print day)
    # only invalidates the return(s) adjacent to it, instead of silently
    # splicing a multi-day price move into what looks like one daily return
    # and misaligning it against NIFTY500's genuine single-day return.
    s_rets = np.log(stock_series).diff().dropna()

    aligned = pd.concat([s_rets, mkt_rets], axis=1, join="inner").dropna()
    if len(aligned) < max(window * 0.90, 10):
        return np.nan
    aligned = aligned.iloc[-window:]

    s = aligned.iloc[:, 0].values - RFR_DAILY
    m = aligned.iloc[:, 1].values - RFR_DAILY

    X = np.column_stack([np.ones(len(m)), m])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, s, rcond=None)
    except np.linalg.LinAlgError:
        return np.nan
    residuals = s - X @ coeffs
    sd = residuals.std(ddof=2)  # 2 estimated params (alpha, beta)
    if sd < 1e-12:
        return np.nan
    alpha = coeffs[0]
    return (alpha / sd) * np.sqrt(TRADING_DAYS)


def _cross_section_z(series: pd.Series) -> pd.Series:
    mu, sd = series.mean(), series.std(ddof=1)
    return (series - mu) / sd if sd > 0 else series * 0.0


def compute_residual_momentum(prices_df, stock_tickers, nifty_series):
    mkt_rets = np.log(nifty_series).diff().dropna()

    resmom_data = {}
    for label, window in WINDOWS.items():
        col = [_residual_information_ratio(prices_df.loc[t], mkt_rets, window)
               for t in stock_tickers]
        valid = sum(1 for v in col if not np.isnan(v))
        resmom_data[f"RS_{label}"] = col
        print(f"  {label} ({window}d): {valid}/{len(stock_tickers)} valid")

    resmom_df = pd.DataFrame(resmom_data, index=stock_tickers)

    rz_cols = []
    for label in WINDOWS:
        col = f"RZ_{label}"
        resmom_df[col] = _cross_section_z(resmom_df[f"RS_{label}"])
        rz_cols.append(col)

    missing_mask = resmom_df[rz_cols].isna().any(axis=1)
    n_affected = missing_mask.sum()
    if n_affected > 0:
        print(f"  Note: {n_affected} stock(s) missing one or more window(s); "
              f"those window Z-scores set to 0 for the RES_MOM composite")
        resmom_df[rz_cols] = resmom_df[rz_cols].fillna(0.0)

    resmom_df["RES_MOM"] = resmom_df[rz_cols].mean(axis=1)
    return resmom_df


def main():
    print(f"Loading prices from {SOURCE_XLSX} ...")
    prices_df, nifty_series, stock_tickers = load_prices(SOURCE_XLSX)
    print(f"  {len(stock_tickers)} tickers, "
          f"{prices_df.columns.min()} -> {prices_df.columns.max()}")

    history_days = prices_df.notna().sum(axis=1)
    keep = history_days[history_days >= MIN_HISTORY_DAYS].index.tolist()
    dropped = len(stock_tickers) - len(keep)
    if dropped:
        print(f"  Excluding {dropped} ticker(s) with < {MIN_HISTORY_DAYS} days of price history")
    stock_tickers = keep

    print("\nComputing residual momentum scores ...")
    resmom_df = compute_residual_momentum(prices_df, stock_tickers, nifty_series)

    ranked = resmom_df.sort_values("RES_MOM", ascending=False)
    ranked.index.name = "TICKER"
    ranked.to_csv(OUTPUT_CSV)
    print(f"\nSaved {len(ranked)} ranked tickers to {OUTPUT_CSV}")

    valid = ranked.dropna(subset=["RES_MOM"])
    with pd.option_context("display.float_format", "{:.3f}".format):
        print(f"\n=== Top 20 by RES_MOM (n={len(valid)} scored) ===")
        print(valid.head(20)[["RS_12M", "RS_9M", "RS_6M", "RS_3M", "RES_MOM"]])
        print("\n=== Bottom 20 by RES_MOM ===")
        print(valid.tail(20)[["RS_12M", "RS_9M", "RS_6M", "RS_3M", "RES_MOM"]])


if __name__ == "__main__":
    main()
