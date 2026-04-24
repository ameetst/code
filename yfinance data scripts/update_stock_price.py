"""
N750 Excel Updater — fetches historical daily close prices for all date columns
in the template. CLOSE and 52WK HIGH columns have been removed from the sheet.

Usage:
    pip install yfinance openpyxl pandas
    python update_n750.py

Input/Output:  N750.xlsx  (updated in place; a backup is saved first)
"""

import datetime
import time
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf
import openpyxl

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_FILE  = "NSEAll.xlsx"
OUTPUT_FILE = "NSEAll_updated.xlsx"
BATCH_SIZE  = 50          # tickers per yfinance batch download
SLEEP_SEC   = 2           # pause between batches (avoid rate-limiting)
PERIOD      = "1y"        # history period for date columns

# Tickers whose Yahoo Finance symbol differs from the value stored in the sheet.
# Key = value as it appears in column A  →  Value = correct Yahoo Finance symbol.
TICKER_OVERRIDES = {
    "NIFTY500": "^CRSLDX",   # Nifty 500 Total Return Index
}
# ─────────────────────────────────────────────────────────────────────────────
def load_template(path: str):
    """Return workbook, worksheet, tickers list, and date-column map.

    Uses two separate loads:
      - data_only=True  -> read cached formula results (dates in row 1, ticker names)
      - normal load     -> the writable workbook that preserves formulas on save
    """
    # Read-only load to extract cached values (dates, ticker names)
    wb_read = openpyxl.load_workbook(path, data_only=True)
    ws_read = wb_read["DATA"]

    tickers = []
    ticker_rows = {}          # ticker -> row number
    for row in ws_read.iter_rows(min_row=2, max_row=ws_read.max_row, min_col=1, max_col=1):
        val = row[0].value
        if val:
            tickers.append(str(val).strip())
            ticker_rows[str(val).strip()] = row[0].row

    # Map date -> column index for historical columns (col D onwards)
    date_cols = {}            # date -> column number
    for cell in ws_read[1]:
        if isinstance(cell.value, datetime.datetime):
            date_cols[cell.value.date()] = cell.column

    # Writable load — keeps all formulas intact (including the date header formula)
    wb_write = openpyxl.load_workbook(path)
    ws_write = wb_write["DATA"]

    return wb_write, ws_write, tickers, ticker_rows, date_cols
def ns(ticker: str) -> str:
    """Return the correct Yahoo Finance symbol for a ticker.

    Checks TICKER_OVERRIDES first (e.g. NIFTY500 -> ^CRSLDX), then appends
    .NS for plain NSE equity symbols that don't already carry a suffix.
    """
    if ticker in TICKER_OVERRIDES:
        return TICKER_OVERRIDES[ticker]
    t = ticker.upper()
    if not t.endswith(".NS") and not t.endswith(".BO") and not t.startswith("^"):
        return t + ".NS"
    return t
def batch_download(tickers_ns: list[str]) -> pd.DataFrame:
    """Download 1-year daily Close for a batch; return DataFrame indexed by date."""
    raw = yf.download(
        tickers_ns,
        period=PERIOD,
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    if raw.empty:
        return pd.DataFrame()

    # Normalise: extract Close, flatten multi-index if needed
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]]

    close.index = close.index.date   # datetime → date
    return close

def main():
    print(f"Loading template: {INPUT_FILE}")
    wb, ws, tickers, ticker_rows, date_cols = load_template(INPUT_FILE)

    total = len(tickers)
    print(f"  {total} tickers | {len(date_cols)} date columns "
          f"({min(date_cols)} → {max(date_cols)})\n")

    # ── Batch-download historical close prices ────────────────────────────────
    all_close: dict[str, dict] = {t: {} for t in tickers}  # ticker → {date: price}

    # Build symbol → original ticker map; overridden tickers use their Yahoo symbol as key
    ns_to_original = {ns(t): t for t in tickers}
    ns_tickers = list(ns_to_original.keys())
    # Separate out index / override tickers — they must NOT be batched with equity .NS tickers
    index_tickers  = [t for t in ns_tickers if t.startswith("^")]
    equity_tickers = [t for t in ns_tickers if not t.startswith("^")]

    # ── Download index tickers individually first ─────────────────────────────
    if index_tickers:
        print(f"Downloading {len(index_tickers)} index ticker(s): {index_tickers}")
        for sym in index_tickers:
            orig = ns_to_original[sym]
            print(f"  {sym} ({orig})", end="", flush=True)
            try:
                df = batch_download([sym])
                if not df.empty:
                    col_name = df.columns[0] if isinstance(df.columns[0], str) else sym
                    series = df.iloc[:, 0].dropna()
                    all_close[orig] = series.to_dict()
                    print(f"  ✓ ({len(series)} rows)")
                else:
                    print("  ✗ no data")
            except Exception as e:
                print(f"  ✗ ERROR: {e}")
            time.sleep(1)

    # ── Download equity tickers in batches ────────────────────────────────────
    batches = [equity_tickers[i:i + BATCH_SIZE] for i in range(0, len(equity_tickers), BATCH_SIZE)]
    print(f"\nDownloading equity prices in {len(batches)} batches of ≤{BATCH_SIZE}…")

    for idx, batch in enumerate(batches, 1):
        print(f"  Batch {idx}/{len(batches)}: {batch[0]} … {batch[-1]}", end="", flush=True)
        try:
            df = batch_download(batch)
            if not df.empty:
                for col in df.columns:
                    orig = ns_to_original.get(col, col.replace(".NS", ""))
                    if orig in all_close:
                        series = df[col].dropna()
                        all_close[orig] = series.to_dict()   # {date: float}
            print(f"  ✓ ({len(df)} rows)")
        except Exception as e:
            print(f"  ✗ ERROR: {e}")

        if idx < len(batches):
            time.sleep(SLEEP_SEC)

    # ── Write historical prices into date columns ─────────────────────────────
    print("\nWriting historical prices to workbook…")
    for ticker, price_map in all_close.items():
        row = ticker_rows.get(ticker)
        if row is None:
            continue
        for date, col in date_cols.items():
            price = price_map.get(date)
            if price is not None:
                ws.cell(row=row, column=col).value = round(float(price), 2)

    # ── Tickers with no data at all ──────────────────────────────────────────
    errors = [t for t in tickers if not all_close.get(t)]

    # ── Save ──────────────────────────────────────────────────────────────────
    print(f"\nSaving → {OUTPUT_FILE}")
    wb.save(OUTPUT_FILE)
    print("Done ✓")

    if errors:
        print(f"\n⚠  {len(errors)} tickers had missing data:")
        for t in errors:
            print(f"   {t}")
    else:
        print("All tickers fetched successfully.")
if __name__ == "__main__":
    if not Path(INPUT_FILE).exists():
        print(f"ERROR: {INPUT_FILE} not found. Place it in the same folder as this script.")
        sys.exit(1)
    main()