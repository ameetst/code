"""
Blitz, Huij & Martens (2011) residual momentum, adapted from
code/momentum/nse500_residual_momentum.py to run on dhan_datahq's
base files/History_updated.xlsx instead of a yfinance download.

Kept faithful to the original paper (unlike residual_momentum.py in this
same folder, which is a port of momentum_lib.py's practitioner variant -
see the comparison note at the bottom of this docstring):

  1. Monthly returns, 36-month ROLLING regression per stock:
       ExcRet_t = alpha + b1*Mkt_RF_t + b2*SMB_t + b3*HML_t + eps_t
     re-estimated fresh for every month (not one fit reused for the whole
     history) - the regression window and the momentum-formation window
     are two different things, per the paper.
  2. Residual momentum signal at month t:
       raw  = SUM(eps, t-12 .. t-2)          (11 months, SKIP t-1)
       std  = raw / STDEV(eps, t-12 .. t-2)  (information-ratio form)
     This is standard 12-1 price momentum applied to residuals instead of
     raw returns - the skip-month exists for the same reason it does in
     classic momentum: avoiding 1-month short-term reversal contamination.
  3. Only the LATEST cross-section is computed here (a snapshot ranking,
     not a backtest) - so only the ~11 signal months' regressions are run
     per stock, not the full 2016-2026 history.

Two deliberate deviations from nse500_residual_momentum.py, both because
better inputs are available in this workbook than a public yfinance pull:

  - Market factor (Mkt_RF): the ACTUAL NIFTY500 index (embedded as its own
    ticker row in the DATA sheet) instead of an equal-weight proxy of the
    downloaded universe. This is what that script's own docstring said it
    was doing, but its code never actually fetched Nifty500 - it silently
    substituted an equal-weight stand-in. Here we use the real thing.
  - Universe: every ticker in History_updated.xlsx with enough history,
    rather than a separately-fetched NSE 500 constituent list.

One deviation preserved AS-IS (not "fixed"), because this exercise is
about faithfully adapting the existing script, not improving on it:

  - HML is computed identically to SMB (both = low-past-return minus
    high-past-return stocks). The original script's own comment calls
    this a "collinear simplification; replace with actual B/M" - there's
    no book-value data in this price-only workbook to do better. The
    regression still runs fine (least-squares handles the collinearity),
    it just means the "3-factor" model is really closer to 2 independent
    factors (Market, and one momentum-sorted spread counted twice).

Output: blitz_residual_momentum.csv (STD_SIGNAL = the ranking signal,
matching the RES_MOM composite's role in residual_momentum.py).
"""

import argparse
import datetime
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
DHAN_DATAHQ = SCRIPT_DIR.parents[2] / "dhan_datahq"

DEFAULT_SOURCE_XLSX = DHAN_DATAHQ / "base files" / "History_updated.xlsx"
OUTPUT_CSV = SCRIPT_DIR / "blitz_residual_momentum.csv"

ROLL_MONTHS = 36        # regression window, per paper
MIN_OBS = 24            # min non-NaN months to fit a regression, per paper
FORMATION_MONTHS = 11   # t-12 .. t-2
RF_ANNUAL = 0.065        # matches nse500_residual_momentum.py's RF_ANNUAL_DEFAULT
MIN_HISTORY_MONTHS = ROLL_MONTHS + FORMATION_MONTHS + 2  # need this much before "now"


def _infer_dates_for_columns(date_indices: list) -> list:
    """
    Reconstruct trading dates when openpyxl can't read cached header values -
    e.g. N750_updated.xlsx's date header is a dynamic Excel array formula
    (SEQUENCE(365,1, WORKDAY(TODAY()-365)) filtered to Mon-Fri) whose cached
    values get wiped whenever openpyxl saves the file. Ported verbatim from
    momentum_lib.py's _infer_dates_for_columns.

    The array formula always starts at col B (0-based index 1), so
    date_indices[k] - 1 gives the offset into the formula's date sequence.
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=365)
    while start.weekday() >= 5:  # advance to first weekday (WORKDAY)
        start += datetime.timedelta(days=1)

    formula_dates = []
    d = start
    while len(formula_dates) < 365:
        if d.weekday() < 5:
            formula_dates.append(d)
        d += datetime.timedelta(days=1)

    result = []
    for i in date_indices:
        offset = i - 1  # col B = index 1 = formula_dates[0]
        if 0 <= offset < len(formula_dates):
            result.append(formula_dates[offset])
        else:
            extra = offset - len(formula_dates) + 1
            last = formula_dates[-1]
            ext = []
            while len(ext) < extra:
                last += datetime.timedelta(days=1)
                if last.weekday() < 5:
                    ext.append(last)
            formula_dates.extend(ext)
            result.append(formula_dates[offset])
    return result


def load_prices(filepath):
    wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    ws = wb["DATA"]
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header = all_rows[0]
    date_indices = [i for i, h in enumerate(header)
                    if isinstance(h, (datetime.datetime, datetime.date))]

    if date_indices:
        dates = [h.date() if isinstance(h, datetime.datetime) else h
                 for h in (header[i] for i in date_indices)]
    else:
        # Fallback: no cached date header (e.g. an unrefreshed array-formula
        # workbook) - infer dates from which columns actually hold numeric
        # price data, same heuristic momentum_lib.py uses.
        print("  Note: date headers not cached in file - inferring dates from "
              "price column positions and today's date.")
        candidate = set()
        for row in all_rows[1: min(11, len(all_rows))]:
            for i, v in enumerate(row):
                if i == 0:
                    continue
                try:
                    if v is not None and float(v) > 0:
                        candidate.add(i)
                except (TypeError, ValueError):
                    pass
        if not candidate:
            raise ValueError(
                "load_prices: no date columns found in header and no numeric "
                "price data detected."
            )
        date_indices = sorted(candidate)
        dates = _infer_dates_for_columns(date_indices)
        print(f"  Inferred {len(dates)} trading dates: "
              f"{dates[0]:%d-%b-%Y} -> {dates[-1]:%d-%b-%Y}")

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

    prices_df = pd.DataFrame(price_matrix, index=tickers, columns=pd.to_datetime(dates))
    prices_df = prices_df[~prices_df.index.duplicated(keep="first")].T  # rows=dates, cols=tickers

    nifty_series = prices_df["NIFTY500"].copy()
    stock_tickers = [t for t in prices_df.columns if t != "NIFTY500"]
    return prices_df[stock_tickers], nifty_series, stock_tickers


def to_monthly_returns(daily_prices: pd.Series) -> pd.Series:
    monthly = daily_prices.resample("ME").last()
    return monthly.pct_change().iloc[1:] * 100  # percent


def build_factors(returns: pd.DataFrame, mkt_rf: pd.Series, rf_monthly: float) -> pd.DataFrame:
    """SMB/HML proxies from trailing-12M cumulative return terciles (same
    logic as nse500_residual_momentum.py's build_factors), aligned to the
    stock universe's own return index."""
    cum12 = returns.rolling(12).sum()
    smb_series, hml_series = [], []

    for dt in returns.index:
        row, c12 = returns.loc[dt], cum12.loc[dt]
        valid = row.dropna().index.intersection(c12.dropna().index)
        if len(valid) < 30:
            smb_series.append(np.nan)
            hml_series.append(np.nan)
            continue
        r, c = row[valid], c12[valid]
        t30 = int(len(valid) * 0.30)
        sorted_c = c.sort_values()
        small = r[sorted_c.index[:t30]].mean()
        big = r[sorted_c.index[-t30:]].mean()
        smb_series.append(small - big)
        hml_series.append(small - big)  # preserved as-is; see module docstring

    return pd.DataFrame({
        "Mkt_RF": mkt_rf.reindex(returns.index) - rf_monthly,
        "SMB": smb_series,
        "HML": hml_series,
    }, index=returns.index)


def fit_residual(y_full: pd.Series, factors: pd.DataFrame, end_idx: int) -> float:
    """OLS over the ROLL_MONTHS window ending at end_idx; return the
    out-of-sample-within-window residual for the month AT end_idx (the
    last observation in that same fitting window - matches the original
    script's compute_residuals, which fits and evaluates on the same
    window rather than holding out the target month)."""
    start_idx = end_idx - ROLL_MONTHS + 1
    if start_idx < 0:
        return np.nan
    y = y_full.iloc[start_idx: end_idx + 1]
    X = factors.iloc[start_idx: end_idx + 1]
    mask = y.notna() & X.notna().all(axis=1)
    y_clean, X_clean = y[mask], X[mask]
    if len(y_clean) < MIN_OBS:
        return np.nan

    X_mat = np.column_stack([np.ones(len(X_clean)), X_clean.values])
    try:
        coef, _, _, _ = np.linalg.lstsq(X_mat, y_clean.values, rcond=None)
    except Exception:
        return np.nan

    t = y_full.index[end_idx]
    if factors.loc[t].notna().all() and pd.notna(y_full.loc[t]):
        pred = coef[0] + coef[1:] @ factors.loc[t].values
        return y_full.loc[t] - pred
    return np.nan


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_XLSX,
                         help="Path to an N750/NSEAll-format workbook with a DATA sheet "
                              "(default: dhan_datahq's History_updated.xlsx). "
                              "e.g. ../Sharpe/N750_updated.xlsx")
    return parser.parse_args()


def main():
    args = parse_args()
    source_xlsx = args.source

    print(f"Loading prices from {source_xlsx} ...")
    daily_prices, nifty_daily, stock_tickers = load_prices(source_xlsx)
    print(f"  {len(stock_tickers)} tickers, "
          f"{daily_prices.index.min().date()} -> {daily_prices.index.max().date()}")

    print("\nResampling to monthly returns ...")
    returns = pd.DataFrame({t: to_monthly_returns(daily_prices[t]) for t in stock_tickers})
    nifty_ret = to_monthly_returns(nifty_daily)
    print(f"  {returns.shape[0]} months, {returns.index.min().date()} -> {returns.index.max().date()}")

    rf_monthly = ((1 + RF_ANNUAL) ** (1 / 12) - 1) * 100
    factors = build_factors(returns, nifty_ret, rf_monthly)
    exc_returns = returns.sub(rf_monthly)

    n_months = len(returns.index)
    if n_months < MIN_HISTORY_MONTHS:
        raise ValueError(
            f"Need >= {MIN_HISTORY_MONTHS} months of history (36-month regression "
            f"window + 11-month formation window + buffer), but {source_xlsx.name} "
            f"only has {n_months}. This method needs a long-history workbook like "
            f"dhan_datahq's History_updated.xlsx - a short rolling window like "
            f"N750_updated.xlsx (which only carries ~1 year of daily history) "
            f"can't support a 36-month regression."
        )

    # Signal at "now" = latest available month, using residuals for the
    # 11 months t-12 .. t-2 (skip t-1), each residual from its own
    # 36-month rolling regression ending at that month.
    last_idx = n_months - 1
    formation_idxs = list(range(last_idx - 12, last_idx - 1))  # t-12 .. t-2 inclusive
    print(f"\nFormation window: {returns.index[formation_idxs[0]].date()} "
          f"-> {returns.index[formation_idxs[-1]].date()} "
          f"(signal date: {returns.index[last_idx].date()}, skipping {returns.index[last_idx-1].date()})")

    print(f"Running {ROLL_MONTHS}-month rolling regressions for {len(stock_tickers)} tickers "
          f"x {len(formation_idxs)} formation months ...")

    raw_signal, std_signal, n_valid_resid = {}, {}, {}
    for ticker in stock_tickers:
        y_full = exc_returns[ticker]
        resids = [fit_residual(y_full, factors, i) for i in formation_idxs]
        resids = pd.Series(resids)
        valid = resids.notna().sum()
        n_valid_resid[ticker] = valid
        if valid < 6:  # matches original script's compute_signal threshold
            raw_signal[ticker] = np.nan
            std_signal[ticker] = np.nan
            continue
        raw = resids.sum()
        std = resids.std()
        raw_signal[ticker] = raw
        std_signal[ticker] = raw / std if std > 0 else np.nan

    result = pd.DataFrame({
        "RAW_SIGNAL": raw_signal,
        "STD_SIGNAL": std_signal,
        "N_VALID_MONTHS": n_valid_resid,
    })
    result.index.name = "TICKER"
    result = result.sort_values("STD_SIGNAL", ascending=False)

    scored = result["STD_SIGNAL"].notna().sum()
    print(f"\n{scored}/{len(stock_tickers)} tickers scored "
          f"(rest had < 6 valid residual months in the formation window)")

    result.to_csv(OUTPUT_CSV)
    print(f"Saved {len(result)} ranked tickers to {OUTPUT_CSV}")

    valid = result.dropna(subset=["STD_SIGNAL"])
    with pd.option_context("display.float_format", "{:.3f}".format):
        print(f"\n=== Top 10 by STD_SIGNAL ===")
        print(valid.head(10))
        print(f"\n=== Bottom 10 by STD_SIGNAL ===")
        print(valid.tail(10))


if __name__ == "__main__":
    main()
