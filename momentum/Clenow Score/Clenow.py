"""
Clenow Momentum Ranking — NSE 500/750
======================================
A like-for-like implementation of Andreas Clenow's "Stocks on the Move"
trend-following momentum system, originally adapted to run on NSE universe
files that contain DAILY CLOSING PRICES ONLY (no daily Open/High/Low, no
volume). Updated Sep 2026 to also accept a tidy OHLC CSV (see INPUT FILE
FORMAT below) and use real Wilder True Range ATR when one is given.

    Momentum Score = Annualized Exponential Regression Slope × R²

WHERE THIS DIFFERS FROM THE BOOK, AND WHY (read this before trusting output)
-----------------------------------------------------------------------------
1. ATR / position sizing.
   The book sizes every position so that a 1-ATR(20) adverse move represents
   a fixed fraction ("risk factor") of account equity:

       shares = floor((Account Value × Risk Factor) / ATR_20)

   True ATR needs daily High/Low/Close (True Range). Updated Sep 2026: when
   the input file is an OHLC CSV (see INPUT FILE FORMAT below), real
   Wilder-smoothed True Range ATR is computed from actual High/Low/Close via
   `true_range_atr()` — this is the upgrade this docstring used to ask for
   ("swap atr_from_closes() for a real True Range calculation"). The
   close-only proxy, `atr_from_closes()` (a Wilder-smoothed 20-day average
   of the absolute close-to-close move, |close_t - close_t-1|), remains the
   fallback for the legacy xlsx layouts, which still carry close prices
   only — it stays a lower bound on real ATR (misses intraday range and
   overnight gap range), so position sizes computed from it run slightly
   LARGER than true-ATR sizing would. Every row's `atr_method` field in the
   output CSV records which one was actually used for that ticker (either
   could apply within the same run — see analyse_ticker()), so a run's
   output is self-documenting about which numbers are the real thing.

2. Chandelier trailing stop.
   Book: exit if close < (highest close since entry − 3 × ATR_20).
   For a ticker with a known entry_date (via --holdings), "highest close
   since entry" is computed from the actual price history in the loaded
   file. For a ticker with only an entry_price (no entry_date), it's
   approximated as max(entry_price, highest close in the loaded window).
   For a candidate not currently held, the field instead shows the
   stop level implied by entering TODAY at last_close — i.e. "if you buy
   this now, here's your initial stop."

3. Rebalance / turnover logic (the actual point of this rewrite).
   The book's system does NOT re-rank the whole universe and swap the
   portfolio to the new top-N every week — that would cause enormous
   turnover. Instead:
     - An existing holding is kept as long as it stays within the top
       REBALANCE_BAND_PCT (default 20%) of the ranked, qualified universe,
       stays above its 100-day MA, and hasn't hit its chandelier stop.
     - New BUYs are only opened to fill slots vacated by exits, drawn from
       the strict top `--top_n` of NOT-currently-held candidates, and only
       when the market (index) filter is passed.
   This requires telling the script what you currently hold — pass
   --holdings pointing at a small CSV (see below). Omit it and the script
   just proposes an initial portfolio from scratch (everything unheld).

4. Liquidity filter. The book requires a minimum average dollar-volume
   filter to exclude illiquid names. Implemented WHEN volume data is
   available — a second sheet named VOLUME for an xlsx input (same
   tickers, same layout as DATA, daily share volume instead of price), or
   the `volume` column for an OHLC CSV input. A stock is disqualified if
   its trailing 20-day average daily traded value (price × volume) falls
   below --min_liquidity (default ₹1,00,00,000 = ₹1 crore/day — a
   placeholder threshold, not a book-specified number; tune it for your
   universe). If no volume data is available (e.g. legacy n500.xlsx /
   n750.xlsx, which have no VOLUME sheet), this filter is simply skipped
   and every stock passes it by default.

5. Index-membership rebalancing (twice yearly in the book) — not modeled;
   this script only knows the tickers present in the file you feed it.

Everything else (90-day exponential regression momentum score, 100-day
stock MA filter, 200-day index MA regime filter, 15% single-day gap
disqualifier) is unchanged from the book's specification.

------------------------------------------------------------------------------
INPUT FILE FORMAT — auto-detected from the file extension (--file ...):

  .csv  ->  OHLC CSV (e.g. N750_OHLC.csv, from update_ohlc_dhan.py)
    Tidy/long format, one row per (ticker, date):
      columns: ticker, date, open, high, low, close, volume
    A row named NIFTY500 (in the `ticker` column) is the benchmark index.
    last_close comes from each ticker's most recent date; 52wk_high is the
    trailing min(252, available) days' max of the real `high` column (not
    close, since real intraday highs are available); ATR is real Wilder
    True Range from high/low/close (see true_range_atr()); the `volume`
    column feeds the liquidity filter directly. This is the only layout
    with genuine OHLC, so it's the only one that gets true ATR — the two
    xlsx layouts below always use the close-only proxy.

  .xlsx (two layouts, both auto-detected on the DATA sheet by whether the
  column headers stop being text and start being dates — nothing to
  configure):

    Legacy (n500.xlsx / n750.xlsx):
      Col A : TICKER  (index row named NIFTY500 embedded among the stocks)
      Col B : CLOSE (latest)
      Col C : 52WK HIGH
      Col D+: daily dates as column headers, close price on that date

    Updated (e.g. N750_updated.xlsx):
      Col A : TICKER
      Col B+: daily dates as column headers, close price on that date
      (no static CLOSE / 52WK HIGH columns — both are derived: last_close
      from the most recent date column, 52wk_high from the trailing
      min(252, available) days of CLOSES, since there's no real intraday
      high in this layout)

    In both xlsx layouts: 0 = non-trading / missing, treated as NaN.

    Optional second sheet VOLUME: same tickers in the same order, daily
    share volume instead of price, used for the liquidity filter above.

HOLDINGS FILE FORMAT (optional, --holdings path/to/holdings.csv)
  Columns (header row required): ticker, entry_date, entry_price
    - ticker      : required
    - entry_date  : optional, YYYY-MM-DD. Enables a true trailing stop
                    computed from actual price history since that date.
    - entry_price : optional. Used if entry_date is absent/out of range.
  A row needs at least `ticker`; entry_date/entry_price may be blank.

USAGE
  python Clenow.py --file N750_OHLC.csv \
      --top_n 20 --account_value 1000000 \
      --holdings holdings.csv --risk_factor 0.001 \
      --output clenow_ranked.csv

  (or --file n500.xlsx / N750_updated.xlsx for the legacy close-only layouts)

DEPENDENCIES
  pip install pandas numpy scipy openpyxl
  Python 3.10+
"""

import argparse
import datetime
import math
import os
import sys
import warnings
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ── Strategy Parameters (book-specified unless noted) ───────────────────────

MOMENTUM_WINDOW        = 90     # trading days for the regression
MA_STOCK_WINDOW        = 100    # stock trend filter
MA_INDEX_WINDOW        = 200    # index regime filter
GAP_THRESHOLD          = 0.15   # disqualify if any 1-day |move| > 15% in window
ATR_WINDOW             = 20     # ATR / trailing-stop lookback
TRADING_DAYS_PER_YEAR  = 250    # book's annualization constant (not 252)
INDEX_TICKER           = "NIFTY500"

# User-confirmed defaults (see conversation — not hardcoded book gospel,
# these are named constants specifically so they're easy to find and tune):
DEFAULT_TOP_N          = 20     # target portfolio size (book's worked example)
DEFAULT_RISK_FACTOR    = 0.001  # 0.1% of equity risked per position per ATR
TRAILING_STOP_ATR_MULT = 3.0    # chandelier stop = high_since_entry - 3*ATR
REBALANCE_BAND_PCT     = 0.20   # keep existing holdings while inside top 20%
                                 # of the ranked, qualified universe

LIQUIDITY_WINDOW            = 20          # trading days for avg daily value
DEFAULT_MIN_AVG_DAILY_VALUE = 10_000_000  # ₹1 crore/day — placeholder, tune via --min_liquidity


# ── Core Math ────────────────────────────────────────────────────────────────

def exp_regression(prices: np.ndarray) -> tuple[float, float]:
    """Fit log-linear regression on prices. Returns (annualized_slope_pct, r_squared)."""
    if len(prices) < 2 or np.any(prices <= 0):
        return np.nan, np.nan

    y = np.log(prices)
    x = np.arange(len(y))
    slope, _, r_value, _, _ = stats.linregress(x, y)

    annualized_slope = (np.exp(slope * TRADING_DAYS_PER_YEAR) - 1) * 100
    r_squared        = r_value ** 2
    return annualized_slope, r_squared


def clenow_score(prices: np.ndarray) -> tuple[float, float, float]:
    """Returns (momentum_score, annualized_slope, r_squared)."""
    slope, r2 = exp_regression(prices)
    if np.isnan(slope):
        return np.nan, np.nan, np.nan
    return slope * r2, slope, r2


def atr_from_closes(prices: pd.Series, window: int = ATR_WINDOW) -> pd.Series:
    """
    Wilder-smoothed ATR approximated from close-to-close moves only
    (no High/Low available — see module docstring, point 1).
    `prices` must be a clean (no-NaN, positive) Series ordered by date.
    Returns a Series of the same length (first value is NaN).
    """
    true_range_proxy = prices.diff().abs()
    atr = true_range_proxy.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    return atr


def true_range_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                    window: int = ATR_WINDOW) -> pd.Series:
    """
    Real Wilder-smoothed ATR from actual daily True Range:

        TR_t = max(high_t - low_t, |high_t - close_(t-1)|, |low_t - close_(t-1)|)
        ATR  = Wilder EWM of TR, alpha = 1/window (same smoothing as
               atr_from_closes(), just fed the real True Range instead of
               the close-to-close proxy)

    `high`, `low`, `close` must be aligned (same date index, no NaN) and
    ordered by date. Returns a Series of the same length (first value is
    NaN, since TR needs a previous close).
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    return atr


# ── File Loading ───────────────────────────────────────────────────────────

def _find_date_column_start(columns) -> Optional[int]:
    """
    Returns the index of the first column (after column 0, the ticker
    column) whose header is an actual date. Everything before that index
    (and after index 0) is treated as legacy static metadata columns
    (e.g. CLOSE, 52WK HIGH); everything from that index on is a price/
    volume date column. Returns None if no date-like header is found.
    """
    for i, c in enumerate(columns):
        if i == 0:
            continue
        if isinstance(c, (pd.Timestamp, datetime.datetime, datetime.date)):
            return i
    return None


def _is_ohlc_csv(filepath: str) -> bool:
    """File-extension check driving the auto-detect in load_data()/
    load_volume() — see module docstring, INPUT FILE FORMAT."""
    return str(filepath).strip().lower().endswith(".csv")


# In-process cache for the tidy OHLC CSV, keyed by (filepath, mtime): both
# load_data() and load_ohlc_hl() need the same parsed frame (close for the
# former; high/low for the latter; both need the real `high` column for a
# proper 52wk_high too) — reading and re-parsing a many-MB CSV twice per
# run would be pure waste. Cleared automatically whenever the file's mtime
# changes, so re-running after a fresh update_ohlc_dhan.py pull picks up
# the new data rather than a stale in-memory copy.
_OHLC_CSV_CACHE: dict = {}


def _load_ohlc_csv_cached(filepath: str) -> pd.DataFrame:
    mtime = os.path.getmtime(filepath)
    key = (str(filepath), mtime)
    if _OHLC_CSV_CACHE.get("key") == key:
        return _OHLC_CSV_CACHE["df"]

    raw = pd.read_csv(filepath)
    missing = {"ticker", "date", "open", "high", "low", "close"} - set(raw.columns)
    if missing:
        raise ValueError(
            f"'{filepath}' is missing expected OHLC CSV column(s): {sorted(missing)} — "
            f"expected ticker, date, open, high, low, close[, volume]."
        )
    raw["ticker"] = raw["ticker"].astype(str).str.strip()
    raw["date"] = pd.to_datetime(raw["date"])

    _OHLC_CSV_CACHE.clear()
    _OHLC_CSV_CACHE["key"] = key
    _OHLC_CSV_CACHE["df"] = raw
    return raw


def _pivot_ohlc_field(raw: pd.DataFrame, field: str) -> pd.DataFrame:
    """ticker x date wide frame for one OHLC CSV column, sorted by date."""
    wide = raw.pivot(index="ticker", columns="date", values=field)
    return wide.reindex(sorted(wide.columns), axis=1)


def load_ohlc_hl(filepath: str) -> Optional[tuple[pd.DataFrame, pd.DataFrame]]:
    """
    For an OHLC CSV input, returns (high [ticker x date], low [ticker x
    date]) -- the extra data load_data() doesn't return (its signature is
    unchanged so existing callers, e.g. clenow_runner.py's
    `_, index_series, _ = Clenow.load_data(filepath)`, keep working
    as-is). Returns None for an xlsx input (no real intraday high/low
    exists in either xlsx layout), so callers fall back to the close-only
    ATR proxy — see analyse_ticker().
    """
    if not _is_ohlc_csv(filepath):
        return None
    raw = _load_ohlc_csv_cached(filepath)
    high = _pivot_ohlc_field(raw, "high").drop(index=INDEX_TICKER, errors="ignore")
    low = _pivot_ohlc_field(raw, "low").drop(index=INDEX_TICKER, errors="ignore")
    return high, low


def load_data(filepath: str) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Auto-detects the input format from the file extension (.csv -> tidy
    OHLC; .xlsx -> legacy or updated close-only layout, itself
    auto-detected as before) — see module docstring, INPUT FILE FORMAT.

    Returns (stock_prices [ticker x date], index_series [date],
             high_52wk [ticker -> 52-week-high price]) in all cases, so
    every existing caller (rank(), clenow_runner.py) keeps working
    unchanged regardless of which input format was actually loaded.
    """
    print(f"Loading {filepath} ...")

    if _is_ohlc_csv(filepath):
        raw = _load_ohlc_csv_cached(filepath)
        close = _pivot_ohlc_field(raw, "close")
        high = _pivot_ohlc_field(raw, "high")
        print(f"  Layout detected : OHLC CSV (tidy, {len(raw)} rows) — "
              f"52wk_high derived from the real 'high' column")

        if INDEX_TICKER not in close.index:
            raise ValueError(f"'{INDEX_TICKER}' not found in the ticker column.")

        index_series = close.loc[INDEX_TICKER].dropna().astype(float)
        stock_prices = close.drop(index=INDEX_TICKER).astype(float)
        high_no_idx = high.drop(index=INDEX_TICKER, errors="ignore").astype(float)

        def _trailing_high_from_highs(row: pd.Series) -> float:
            clean = row.dropna()
            if clean.empty:
                return np.nan
            return clean.iloc[-min(len(clean), 252):].max()
        high_52wk = high_no_idx.apply(_trailing_high_from_highs, axis=1)

        date_cols = list(close.columns)
        print(f"  Tickers loaded  : {len(stock_prices)}")
        print(f"  Date columns    : {len(date_cols)}  ({date_cols[0].date()} → {date_cols[-1].date()})")
        print(f"  Index ticker    : {INDEX_TICKER} ({index_series.notna().sum()} trading days)")
        return stock_prices, index_series, high_52wk

    raw = pd.read_excel(filepath, sheet_name="DATA", header=0)
    raw = raw.rename(columns={raw.columns[0]: "ticker"})

    date_start = _find_date_column_start(raw.columns)
    if date_start is None:
        raise ValueError(
            "Could not find any date-headers on the DATA sheet — "
            "expected either 'TICKER, CLOSE, 52WK HIGH, <dates>' or 'TICKER, <dates>'."
        )

    date_cols = list(raw.columns[date_start:])
    meta_cols = list(raw.columns[1:date_start])   # legacy static columns, if any

    static_52wk = None
    if len(meta_cols) >= 2:
        raw_meta = raw[["ticker"] + meta_cols].set_index("ticker")
        wk_candidates = [c for c in meta_cols if isinstance(c, str) and "52" in c.upper()]
        col_52wk = wk_candidates[0] if wk_candidates else meta_cols[-1]
        static_52wk = raw_meta[col_52wk].astype(float)
        print(f"  Layout detected : legacy (static columns: {meta_cols}) — using '{col_52wk}' for 52wk_high")
    else:
        print("  Layout detected : ticker + dates only — 52wk_high will be derived from price history")

    price_data = raw[["ticker"] + date_cols].copy()
    price_data[date_cols] = price_data[date_cols].replace(0, np.nan)
    price_data = price_data.set_index("ticker")

    if INDEX_TICKER not in price_data.index:
        raise ValueError(f"'{INDEX_TICKER}' not found in the TICKER column.")

    index_series = price_data.loc[INDEX_TICKER].dropna().astype(float)
    stock_prices = price_data.drop(index=INDEX_TICKER).astype(float)

    if static_52wk is not None:
        high_52wk = static_52wk.drop(index=INDEX_TICKER, errors="ignore")
    else:
        def _trailing_high(row: pd.Series) -> float:
            clean = row.dropna()
            if clean.empty:
                return np.nan
            return clean.iloc[-min(len(clean), 252):].max()
        high_52wk = stock_prices.apply(_trailing_high, axis=1)

    print(f"  Tickers loaded  : {len(stock_prices)}")
    print(f"  Date columns    : {len(date_cols)}  ({date_cols[0].date()} → {date_cols[-1].date()})")
    print(f"  Index ticker    : {INDEX_TICKER} ({index_series.notna().sum()} trading days)")

    return stock_prices, index_series, high_52wk


def load_volume(filepath: str) -> Optional[pd.DataFrame]:
    """
    Loads daily share volume as a ticker x date frame, used by the
    liquidity filter. For an OHLC CSV, this is the `volume` column
    (pivoted, same as close/high/low). For an xlsx, this is the optional
    VOLUME sheet (same TICKER + dates layout as DATA). Returns None if no
    volume data is available so the liquidity filter is simply skipped
    (e.g. the original n500.xlsx / n750.xlsx, which have no VOLUME sheet).
    """
    if _is_ohlc_csv(filepath):
        raw = _load_ohlc_csv_cached(filepath)
        if "volume" not in raw.columns:
            print("  No 'volume' column in the OHLC CSV — liquidity filter will be skipped.")
            return None
        print(f"Loading volume data from {filepath} ('volume' column) ...")
        vol = _pivot_ohlc_field(raw, "volume").drop(index=INDEX_TICKER, errors="ignore").astype(float)
        date_cols = list(vol.columns)
        print(f"  Volume tickers  : {len(vol)}  |  Date columns: {len(date_cols)} "
              f"({date_cols[0].date()} → {date_cols[-1].date()})")
        return vol

    try:
        sheet_names = pd.ExcelFile(filepath).sheet_names
    except Exception:
        return None
    if "VOLUME" not in sheet_names:
        print("  No VOLUME sheet found — liquidity filter will be skipped.")
        return None

    print(f"Loading volume data from {filepath} (sheet VOLUME) ...")
    raw = pd.read_excel(filepath, sheet_name="VOLUME", header=0)
    raw = raw.rename(columns={raw.columns[0]: "ticker"})

    date_start = _find_date_column_start(raw.columns)
    if date_start is None:
        print("  ⚠ VOLUME sheet has no recognizable date columns — liquidity filter skipped.")
        return None

    date_cols = list(raw.columns[date_start:])
    vol = raw[["ticker"] + date_cols].copy()
    vol[date_cols] = vol[date_cols].replace(0, np.nan)
    vol = vol.set_index("ticker").astype(float)

    print(f"  Volume tickers  : {len(vol)}  |  Date columns: {len(date_cols)} "
          f"({date_cols[0].date()} → {date_cols[-1].date()})")
    return vol


def load_holdings(filepath: Optional[str]) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by ticker with columns entry_date (Timestamp
    or NaT) and entry_price (float or NaN). Empty (0-row) frame if no path given.
    """
    cols = ["entry_date", "entry_price"]
    if not filepath:
        return pd.DataFrame(columns=cols).rename_axis("ticker")

    print(f"Loading current holdings from {filepath} ...")
    df = pd.read_csv(filepath)
    df.columns = [c.strip().lower() for c in df.columns]
    if "ticker" not in df.columns:
        raise ValueError("Holdings file must have a 'ticker' column.")

    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    if "entry_date" in df.columns:
        df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    else:
        df["entry_date"] = pd.NaT
    if "entry_price" in df.columns:
        df["entry_price"] = pd.to_numeric(df["entry_price"], errors="coerce")
    else:
        df["entry_price"] = np.nan

    df = df.set_index("ticker")[cols]
    print(f"  Holdings loaded : {len(df)}")
    return df


# ── Market Filter ────────────────────────────────────────────────────────────

def check_market_filter(index_series: pd.Series) -> tuple[bool, float, float]:
    """Returns (above_ma200, last_index_close, ma200_value)."""
    valid = index_series.dropna()
    if len(valid) < MA_INDEX_WINDOW:
        print(f"  ⚠ Only {len(valid)} index data points — market filter skipped (needs {MA_INDEX_WINDOW}).")
        return True, valid.iloc[-1], np.nan

    last_close = valid.iloc[-1]
    ma200      = valid.iloc[-MA_INDEX_WINDOW:].mean()
    above      = last_close > ma200
    return above, last_close, ma200


# ── Per-Ticker Analysis ──────────────────────────────────────────────────────

def analyse_ticker(
    ticker: str,
    price_row: pd.Series,
    holding: Optional[pd.Series],
    volume_row: Optional[pd.Series] = None,
    min_liquidity: float = DEFAULT_MIN_AVG_DAILY_VALUE,
    high_row: Optional[pd.Series] = None,
    low_row: Optional[pd.Series] = None,
) -> dict:
    """
    Analyse one ticker. `holding` is the row from the holdings frame for this
    ticker if currently held, else None. `volume_row` is the ticker's daily
    share-volume series if volume data was loaded, else None (liquidity
    filter is skipped in that case). `high_row`/`low_row` are the ticker's
    daily high/low series when the input was an OHLC CSV (see
    load_ohlc_hl()); both None for an xlsx input. Does NOT assign the final
    portfolio signal (BUY/HOLD/SELL/WATCHLIST) — that happens after
    ranking, in build_portfolio(), because it depends on rank relative to
    the rest of the universe and on open-slot availability.
    """
    prices = price_row.dropna().astype(float)
    prices = prices[prices > 0]

    base: dict[str, Any] = dict(
        ticker              = ticker,
        is_held             = holding is not None,
        last_close          = np.nan,
        ma_100              = np.nan,
        below_ma100         = np.nan,
        annualized_slope    = np.nan,
        r_squared           = np.nan,
        momentum_score      = np.nan,
        atr_20              = np.nan,
        atr_method          = "",
        highest_close_ref   = np.nan,
        stop_loss_level     = np.nan,
        distance_to_stop_pct= np.nan,
        stopped_out         = False,
        avg_daily_value_20d = np.nan,
        entry_date          = holding["entry_date"] if holding is not None else pd.NaT,
        entry_price         = holding["entry_price"] if holding is not None else np.nan,
        rank                = np.nan,
        disqualify_reason   = "",
        qualifies           = False,   # passed data/gap checks
    )

    if len(prices) < MOMENTUM_WINDOW:
        base["disqualify_reason"] = f"Insufficient data ({len(prices)} pts, need {MOMENTUM_WINDOW})"
        return base

    base["last_close"] = round(prices.iloc[-1], 4)

    # ── Liquidity filter (only if a VOLUME sheet was provided) ───────────
    if volume_row is not None:
        vol_clean = volume_row.dropna()
        common_dates = prices.index.intersection(vol_clean.index)
        recent_common = sorted(common_dates)[-LIQUIDITY_WINDOW:]
        if len(recent_common) >= max(5, LIQUIDITY_WINDOW // 2):
            daily_value = prices.loc[recent_common] * vol_clean.loc[recent_common]
            avg_value = daily_value.mean()
            base["avg_daily_value_20d"] = round(avg_value, 2)
            if avg_value < min_liquidity:
                base["disqualify_reason"] = (
                    f"Illiquid: {LIQUIDITY_WINDOW}d avg traded value "
                    f"₹{avg_value:,.0f} < min ₹{min_liquidity:,.0f}"
                )
                return base

    # ── Gap filter ───────────────────────────────────────────────────────
    recent_prices = prices.iloc[-MOMENTUM_WINDOW:]
    daily_returns = recent_prices.pct_change().abs()
    if (daily_returns > GAP_THRESHOLD).any():
        base["disqualify_reason"] = f"Gap > {GAP_THRESHOLD*100:.0f}% in last {MOMENTUM_WINDOW} days"
        return base

    # ── 100-day MA filter ───────────────────────────────────────────────
    if len(prices) >= MA_STOCK_WINDOW:
        ma100 = prices.iloc[-MA_STOCK_WINDOW:].mean()
        base["ma_100"] = round(ma100, 4)
        base["below_ma100"] = bool(prices.iloc[-1] < ma100)
    else:
        base["below_ma100"] = False  # not enough history — don't penalise

    # ── Momentum score ───────────────────────────────────────────────────
    score, slope, r2 = clenow_score(recent_prices.values)
    base["annualized_slope"] = round(slope, 4) if not np.isnan(slope) else np.nan
    base["r_squared"]        = round(r2,    4) if not np.isnan(r2)    else np.nan
    base["momentum_score"]   = round(score, 4) if not np.isnan(score) else np.nan

    # ── ATR: real True Range when high/low are available (OHLC CSV input),
    #    else the close-to-close proxy (xlsx input) — see module docstring
    #    point 1 and true_range_atr()'s docstring.
    atr_method = "close_proxy"
    atr_series = None
    if high_row is not None and low_row is not None:
        h = high_row.reindex(prices.index).astype(float)
        l = low_row.reindex(prices.index).astype(float)
        aligned = h.notna() & l.notna()
        if aligned.sum() >= ATR_WINDOW:
            atr_series = true_range_atr(h[aligned], l[aligned], prices[aligned], ATR_WINDOW)
            atr_method = "true_range"
        # else: not enough aligned high/low history for this ticker (e.g.
        # a recent listing) -- fall through to the close proxy below so it
        # still gets *a* usable ATR rather than none at all.
    if atr_series is None:
        atr_series = atr_from_closes(prices, ATR_WINDOW)

    atr_now = atr_series.iloc[-1]
    base["atr_20"] = round(atr_now, 6) if pd.notna(atr_now) else np.nan
    base["atr_method"] = atr_method if pd.notna(atr_now) else ""

    # ── Chandelier stop reference ────────────────────────────────────────
    if pd.notna(atr_now):
        if holding is not None:
            entry_date  = holding["entry_date"]
            entry_price = holding["entry_price"]
            if pd.notna(entry_date) and entry_date in prices.index:
                since_entry = prices[prices.index >= entry_date]
                highest_ref = since_entry.max() if len(since_entry) else prices.iloc[-1]
            elif pd.notna(entry_date):
                # entry predates the loaded window — best available proxy
                highest_ref = prices.max()
            elif pd.notna(entry_price):
                highest_ref = max(entry_price, prices.max())
            else:
                # held but no entry info supplied — fresh stop off current price
                highest_ref = prices.iloc[-1]
        else:
            # not held: "if I entered today" reference stop
            highest_ref = prices.iloc[-1]

        base["highest_close_ref"] = round(highest_ref, 4)
        stop_level = highest_ref - TRAILING_STOP_ATR_MULT * atr_now
        base["stop_loss_level"] = round(stop_level, 4)
        base["distance_to_stop_pct"] = round(
            (base["last_close"] - stop_level) / base["last_close"] * 100, 2
        )
        if holding is not None and base["last_close"] < stop_level:
            base["stopped_out"] = True

    base["qualifies"] = True
    return base


# ── Portfolio Construction (rank -> band-hold / new-buy / exit logic) ──────

def build_portfolio(
    df: pd.DataFrame,
    holdings: pd.DataFrame,
    market_ok: bool,
    top_n: int,
) -> tuple[pd.DataFrame, int]:
    """
    df must contain all analysed tickers (qualified + disqualified), already
    ranked (a 'rank' column on qualified rows, NaN on disqualified).
    Returns (df with 'signal' and 'signal_reason' columns filled, band_cutoff_rank).
    """
    qualified = df[df["qualifies"]].copy()
    n_qualified = len(qualified)
    band_cutoff_rank = max(1, math.ceil(REBALANCE_BAND_PCT * n_qualified)) if n_qualified else 0

    held_tickers = set(holdings.index)
    df = df.set_index("ticker", drop=False)

    signal = pd.Series("NO SIGNAL", index=df.index)
    reason = pd.Series("", index=df.index)

    # Disqualified rows keep that status outright (overrides everything, incl. held)
    disq_mask = ~df["qualifies"]
    signal[disq_mask] = "DISQUALIFIED"
    reason[disq_mask] = df.loc[disq_mask, "disqualify_reason"]

    # ── Step 1: decide exits among current holdings ─────────────────────
    held_continuing = set()
    for t in held_tickers:
        if t not in df.index or not df.loc[t, "qualifies"]:
            signal[t] = "SELL"
            reason[t] = "No longer in universe or data-disqualified"
            continue
        row = df.loc[t]
        if bool(row["stopped_out"]):
            signal[t] = "SELL"
            reason[t] = f"Stopped out: close {row['last_close']} < chandelier stop {row['stop_loss_level']}"
        elif bool(row["below_ma100"]):
            signal[t] = "SELL"
            reason[t] = "Closed below 100-day MA"
        elif pd.isna(row["rank"]) or row["rank"] > band_cutoff_rank:
            signal[t] = "SELL"
            reason[t] = f"Rank {row['rank']:.0f} fell outside top {REBALANCE_BAND_PCT*100:.0f}% band (cutoff {band_cutoff_rank})"
        else:
            held_continuing.add(t)
            signal[t] = "HOLD"
            reason[t] = f"Held, rank {row['rank']:.0f} still within band (cutoff {band_cutoff_rank})"

    # ── Step 2: fill open slots from best-ranked non-held candidates ────
    open_slots = max(0, top_n - len(held_continuing))
    candidates = qualified[
        (~qualified["ticker"].isin(held_tickers)) & (qualified["below_ma100"] == False)  # noqa: E712
    ].sort_values("rank")
    fill = candidates.head(open_slots)

    new_buys = set()
    for t in fill["ticker"]:
        if market_ok:
            signal[t] = "BUY"
            reason[t] = f"New entry, rank {int(df.loc[t, 'rank'])} of {n_qualified}"
            new_buys.add(t)
        else:
            signal[t] = "WATCHLIST"
            reason[t] = "Would BUY (top-ranked, slot open) but market regime filter blocked (index below 200-day MA)"

    df["signal"] = signal
    df["signal_reason"] = reason
    df["in_portfolio"] = df["ticker"].isin(held_continuing | new_buys)

    return df.reset_index(drop=True), band_cutoff_rank


# ── Main Ranking ─────────────────────────────────────────────────────────

def rank(
    filepath: str,
    top_n: int,
    output_path: str,
    account_value: float = 0,
    holdings_path: Optional[str] = None,
    risk_factor: float = DEFAULT_RISK_FACTOR,
    min_liquidity: float = DEFAULT_MIN_AVG_DAILY_VALUE,
):
    # 1. Load
    stock_prices, index_series, high_52wk = load_data(filepath)
    volume = load_volume(filepath)
    holdings = load_holdings(holdings_path)
    hl = load_ohlc_hl(filepath)
    high_df, low_df = hl if hl is not None else (None, None)
    print(f"  ATR method      : {'real True Range (OHLC CSV)' if hl is not None else 'close-to-close proxy (xlsx input, no High/Low)'}")

    # 2. Market filter
    market_ok, idx_close, idx_ma200 = check_market_filter(index_series)
    status = "ABOVE ✓" if market_ok else "BELOW ✗"
    print(f"\n  NIFTY500 last close : {idx_close:,.2f}")
    if not np.isnan(idx_ma200):
        print(f"  NIFTY500 200-day MA : {idx_ma200:,.2f}  [{status}]")
    if not market_ok:
        print("  ⚠  Market filter FAILED — no new BUY signals (existing holdings still managed).")

    # 3. Score every ticker
    print(f"\nScoring {len(stock_prices)} tickers ...")
    results = []
    for ticker, row in stock_prices.iterrows():
        holding = holdings.loc[ticker] if ticker in holdings.index else None
        vol_row = volume.loc[ticker] if (volume is not None and ticker in volume.index) else None
        high_row = high_df.loc[ticker] if (high_df is not None and ticker in high_df.index) else None
        low_row = low_df.loc[ticker] if (low_df is not None and ticker in low_df.index) else None
        results.append(analyse_ticker(ticker, row, holding, vol_row, min_liquidity, high_row, low_row))

    df = pd.DataFrame(results)

    # 4. Rank qualified tickers by momentum score
    qualified_mask = df["qualifies"] & df["momentum_score"].notna()
    df.loc[qualified_mask, "rank"] = (
        df.loc[qualified_mask, "momentum_score"]
        .rank(ascending=False, method="first")
        .astype(int)
    )
    # anything qualifying but without a usable momentum score (shouldn't
    # normally happen) is treated as disqualified for ranking purposes
    df.loc[df["qualifies"] & df["momentum_score"].isna(), "qualifies"] = False
    df.loc[df["qualifies"] & df["momentum_score"].isna(), "disqualify_reason"] = "Momentum score could not be computed"

    # 5. Build portfolio (band-hold / new-buy / exit / watchlist logic)
    df, band_cutoff_rank = build_portfolio(df, holdings, market_ok, top_n)

    # 6. Position sizing (ATR risk-parity) for the resulting portfolio only
    port_mask = df["in_portfolio"]
    port = df[port_mask].copy()
    if len(port) and port["atr_20"].notna().any():
        size_metric = port["last_close"] / port["atr_20"]
        total_metric = size_metric.replace([np.inf, -np.inf], np.nan).sum()
        weight_pct = (size_metric / total_metric) * 100
        df.loc[port_mask, "position_weight_pct"] = np.round(weight_pct, 2)

        if account_value > 0:
            shares = np.floor((account_value * risk_factor) / port["atr_20"])
            allocated = shares * port["last_close"]
            risk_rupees = shares * port["atr_20"]
            df.loc[port_mask, "approx_shares"] = shares
            df.loc[port_mask, "allocated_amount"] = np.round(allocated, 2)
            df.loc[port_mask, "risk_amount_per_atr"] = np.round(risk_rupees, 2)

    # 7. Attach 52-week high (from static column or derived — see load_data)
    df["52wk_high"] = df["ticker"].map(high_52wk)

    # 8. Output columns
    output_cols = [
        "rank", "ticker", "signal", "signal_reason", "is_held",
        "momentum_score", "annualized_slope", "r_squared",
        "last_close", "ma_100", "below_ma100",
        "atr_20", "atr_method", "highest_close_ref", "stop_loss_level", "distance_to_stop_pct",
        "avg_daily_value_20d",
        "position_weight_pct", "allocated_amount", "approx_shares", "risk_amount_per_atr",
        "entry_date", "entry_price",
        "52wk_high", "disqualify_reason",
    ]
    final = df[[c for c in output_cols if c in df.columns]]
    final = final.sort_values(["rank"], na_position="last")

    # 9. Print summary
    print(f"\n{'═'*60}")
    print(f"  CLENOW MOMENTUM PORTFOLIO — REBALANCE RESULTS")
    print(f"{'═'*60}")
    print(f"  Universe            : {len(stock_prices)} stocks")
    print(f"  Qualified           : {int(qualified_mask.sum())}")
    print(f"  Currently held (in) : {len(holdings)}")
    print(f"  Target portfolio N  : {top_n}")
    print(f"  Band cutoff rank    : top {band_cutoff_rank} (keep-existing threshold, {REBALANCE_BAND_PCT*100:.0f}% band)")
    if volume is not None:
        print(f"  Liquidity filter    : {LIQUIDITY_WINDOW}d avg traded value >= ₹{min_liquidity:,.0f}")
    print(f"\n  Signal breakdown:")
    for sig, cnt in final["signal"].value_counts().items():
        print(f"    {sig:<12} {cnt}")

    print(f"\n  Resulting portfolio (HOLD + BUY), ranked:")
    port_df = final[final["signal"].isin(["BUY", "HOLD"])].sort_values("rank")
    if len(port_df) == 0:
        print("    (none)")
    else:
        display_cols = ["rank", "ticker", "signal", "momentum_score",
                         "atr_20", "stop_loss_level", "position_weight_pct"]
        if account_value > 0:
            display_cols += ["allocated_amount", "approx_shares"]
        print(port_df[display_cols].to_string(index=False))

    sells = final[final["signal"] == "SELL"]
    if len(sells):
        print(f"\n  Exits this rebalance (SELL):")
        print(sells[["ticker", "signal_reason"]].to_string(index=False))

    watch = final[final["signal"] == "WATCHLIST"]
    if len(watch):
        print(f"\n  On watchlist (blocked only by market regime filter):")
        print(watch[["rank", "ticker", "momentum_score"]].to_string(index=False))

    # 10. Save
    final.to_csv(output_path, index=False)
    print(f"\n  ✓ Full results saved to: {output_path}")
    print(f"{'═'*60}\n")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Clenow 'Stocks on the Move' Momentum Ranking for NSE 500/750"
    )
    parser.add_argument("--file", required=True,
                         help="Path to input file: an OHLC CSV (e.g. N750_OHLC.csv, for real ATR) "
                              "or a legacy close-only Excel file (e.g. n500.xlsx, N750_updated.xlsx)")
    parser.add_argument("--top_n", type=int, default=DEFAULT_TOP_N,
                         help=f"Target portfolio size (default: {DEFAULT_TOP_N})")
    parser.add_argument("--output", default="clenow_ranked.csv", help="Output CSV filename")
    parser.add_argument("--account_value", type=float, default=0,
                         help="Total portfolio value in INR for ATR-based position sizing (optional)")
    parser.add_argument("--holdings", default=None,
                         help="Path to CSV of current holdings (ticker[,entry_date][,entry_price])")
    parser.add_argument("--risk_factor", type=float, default=DEFAULT_RISK_FACTOR,
                         help=f"Fraction of account equity risked per position per ATR (default: {DEFAULT_RISK_FACTOR})")
    parser.add_argument("--min_liquidity", type=float, default=DEFAULT_MIN_AVG_DAILY_VALUE,
                         help=f"Minimum {LIQUIDITY_WINDOW}-day avg daily traded value in INR to qualify "
                              f"(default: {DEFAULT_MIN_AVG_DAILY_VALUE:,.0f}); only applied if the file has "
                              f"volume data (a VOLUME sheet for xlsx, or a 'volume' column for an OHLC CSV)")
    args = parser.parse_args()
    rank(
        args.file, args.top_n, args.output,
        account_value=args.account_value,
        holdings_path=args.holdings,
        risk_factor=args.risk_factor,
        min_liquidity=args.min_liquidity,
    )


if __name__ == "__main__":
    main()
