"""
Z-Score Composite Momentum Strategy Backtester
================================================
Backtests three signal-weighting configurations against historical price
data, using a composite of five momentum / trend signals:

    - 12-1 Momentum      (12mo return, skipping most recent month)
    - 6-1 Momentum       (6mo return, skipping most recent month)
    - 52-Week High       (proximity to 52-week high)
    - Clenow Score       (annualized regression slope * R^2 on log price)
    - Vol-Adj Momentum   (12-1 momentum / annualized volatility)

Three weight configs (per your reference table):
    Z Equal Weight            - 20/20/20/20/20
    Z Weighted v1 (PRIMARY)   - 30/20/20/20/10
    Z Weighted v2 (Trend+Vol) - 15/20/15/25/25

Each config's composite score is the weighted sum of the CROSS-SECTIONAL
z-scores of the five signals, computed independently at every rebalance
date. At each rebalance, the top N names by composite score are held
equal-weighted until the next rebalance.

Requirements:
    pip install yfinance pandas numpy matplotlib scipy

Usage:
    Edit the CONFIG block below (universe, dates, top N, etc.) and run:
    python zscore_backtest.py
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")  # headless-safe; remove this line if running interactively
import matplotlib.pyplot as plt


# ======================================================================
# CONFIGURATION — edit this block for your own universe / dates
# ======================================================================

# Universe of tickers to rank each rebalance. Keep this reasonably large
# (30-100+) since z-scores need a decent cross-section to be meaningful.
TICKERS = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "V",
    "UNH", "HD", "PG", "MA", "DIS", "PFE", "KO", "PEP", "MRK", "ABBV",
    "COST", "AVGO", "XOM", "CVX", "WMT", "BAC", "ADBE", "CRM", "NFLX",
    "CSCO", "ORCL", "ACN", "TMO", "LIN", "MCD", "ABT", "DHR", "NKE",
    "TXN", "NEE", "PM", "UNP", "LOW", "IBM", "QCOM", "HON", "AMD", "GE",
    "CAT", "SBUX",
]

BENCHMARK = "SPY"                 # buy & hold comparison
START_DATE = "2014-01-01"
END_DATE = "2024-12-31"

TOP_N = 10                        # holdings in the portfolio each period
REBALANCE_FREQ = "ME"             # 'ME' = calendar month-end rebalance

# Signal lookback windows (in trading days)
LOOKBACK_12M = 252
LOOKBACK_6M = 126
SKIP_RECENT = 21                  # skip most recent ~1 month (avoids reversal)
LOOKBACK_52W = 252
CLENOW_LOOKBACK = 90              # ~90 trading days (~4.5 months) trend window
VOL_LOOKBACK = 252

MIN_HISTORY = LOOKBACK_12M + SKIP_RECENT + 5   # min bars needed to score a name

RISK_FREE_RATE = 0.0              # annualized, for Sharpe ratio

WEIGHTS = {
    "Z Equal Weight": {
        "mom_12_1": 0.20, "mom_6_1": 0.20, "high_52w": 0.20,
        "clenow": 0.20, "vol_adj": 0.20,
    },
    "Z Weighted v1 (PRIMARY)": {
        "mom_12_1": 0.30, "mom_6_1": 0.20, "high_52w": 0.20,
        "clenow": 0.20, "vol_adj": 0.10,
    },
    "Z Weighted v2 (Trend+Vol)": {
        "mom_12_1": 0.15, "mom_6_1": 0.20, "high_52w": 0.15,
        "clenow": 0.25, "vol_adj": 0.25,
    },
}


# ======================================================================
# DATA LOADING
# ======================================================================

def load_prices(tickers, benchmark, start, end):
    """Download adjusted close prices via yfinance."""
    import yfinance as yf

    all_tickers = list(dict.fromkeys(tickers + [benchmark]))
    raw = yf.download(all_tickers, start=start, end=end, auto_adjust=True,
                       progress=False)["Close"]
    raw = raw.dropna(axis=1, how="all")
    raw = raw.ffill().dropna(how="all")
    return raw


def _parse_wide_frame(df, start=None, end=None):
    """Shared parser: given a raw wide DataFrame (Date + one col per ticker),
    clean it into the Date-indexed price matrix the backtester expects."""
    date_col = None
    for candidate in ["Date", "date", "DATE"]:
        if candidate in df.columns:
            date_col = candidate
            break
    if date_col is None:
        date_col = df.columns[0]  # assume first column is the date

    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    df = df.apply(pd.to_numeric, errors="coerce")

    if start:
        df = df[df.index >= pd.to_datetime(start)]
    if end:
        df = df[df.index <= pd.to_datetime(end)]

    df = df.ffill().dropna(axis=1, how="all").dropna(how="all")
    return df


def _parse_long_frame(df, date_col, ticker_col, price_col, start=None, end=None):
    """Shared parser: given a raw long DataFrame (Date, Ticker, Close rows),
    pivot into the Date x Ticker wide matrix the backtester expects."""
    df[date_col] = pd.to_datetime(df[date_col])
    wide = df.pivot(index=date_col, columns=ticker_col, values=price_col)
    wide = wide.sort_index()

    if start:
        wide = wide[wide.index >= pd.to_datetime(start)]
    if end:
        wide = wide[wide.index <= pd.to_datetime(end)]

    wide = wide.ffill().dropna(axis=1, how="all").dropna(how="all")
    return wide


def load_prices_from_csv(filepath, start=None, end=None):
    """
    Load historical prices from a local CSV file instead of downloading.

    Expected format — WIDE, one column per ticker, one row per date:

        Date,AAPL,MSFT,SPY,...
        2020-01-02,75.09,160.62,324.87,...
        2020-01-03,74.36,158.62,322.41,...
        ...

    - The date column can be named "Date", "date", or be the first column.
    - Any column not matching a ticker you care about is simply ignored
      (extra columns in the file are fine).
    - Values should be prices (adjusted close is recommended so dividends/
      splits don't distort the momentum signals).

    If your data is in LONG format instead (Date, Ticker, Close per row),
    see `load_prices_from_long_csv` below.
    """
    df = pd.read_csv(filepath)
    return _parse_wide_frame(df, start, end)


def load_prices_from_long_csv(filepath, date_col="Date", ticker_col="Ticker",
                               price_col="Close", start=None, end=None):
    """
    Load prices from a LONG-format CSV (one row per date x ticker):

        Date,Ticker,Close
        2020-01-02,AAPL,75.09
        2020-01-02,MSFT,160.62
        2020-01-03,AAPL,74.36
        ...

    Pivots it into the wide Date x Ticker matrix the backtester expects.
    """
    df = pd.read_csv(filepath)
    return _parse_long_frame(df, date_col, ticker_col, price_col, start, end)


def _parse_transposed_wide_frame(df, ticker_col="TICKER", start=None, end=None,
                                  zero_as_missing=True):
    """
    Shared parser for the 'transposed wide' layout: one row per TICKER,
    one column per date (common in Dhan / broker data exports):

        TICKER      2016-08-29  2016-08-30  ...
        AAPL        75.09       74.36       ...
        MSFT        160.62      158.62      ...

    Transposes into the standard Date-indexed, Ticker-columned price matrix.
    Zero values (common for pre-listing / not-yet-IPO'd names) are treated
    as missing rather than real prices of 0, and are NOT back-filled —
    only forward-filled from a ticker's first real observation onward.
    """
    df = df.set_index(ticker_col)
    df.columns = pd.to_datetime(df.columns)
    wide = df.T.sort_index()
    wide = wide.apply(pd.to_numeric, errors="coerce")

    if zero_as_missing:
        wide = wide.mask(wide == 0)

    if start:
        wide = wide[wide.index >= pd.to_datetime(start)]
    if end:
        wide = wide[wide.index <= pd.to_datetime(end)]

    wide = wide.ffill().dropna(axis=1, how="all").dropna(how="all")
    return wide


def load_prices_transposed_excel(filepath, sheet_name=0, ticker_col="TICKER",
                                  start=None, end=None, zero_as_missing=True):
    """
    Load prices from a transposed-wide .xlsx/.xls file: one row per ticker,
    one column per date. See `_parse_transposed_wide_frame` for the exact
    expected layout. Requires: pip install openpyxl
    """
    df = pd.read_excel(filepath, sheet_name=sheet_name)
    if isinstance(df, dict):
        return {name: _parse_transposed_wide_frame(d, ticker_col, start, end, zero_as_missing)
                for name, d in df.items()}
    return _parse_transposed_wide_frame(df, ticker_col, start, end, zero_as_missing)
    """
    Load historical prices from a local .xlsx/.xls file — WIDE format,
    one column per ticker, one row per date (mirrors load_prices_from_csv).

        Date,AAPL,MSFT,SPY,...
        2020-01-02,75.09,160.62,324.87,...

    `sheet_name` can be a sheet index (0 = first sheet, default), a sheet
    name string, or None to load ALL sheets (in which case this returns a
    dict of {sheet_name: DataFrame} instead — pick one before backtesting).

    Requires: pip install openpyxl
    """
    df = pd.read_excel(filepath, sheet_name=sheet_name)
    if isinstance(df, dict):
        return {name: _parse_wide_frame(d, start, end) for name, d in df.items()}
    return _parse_wide_frame(df, start, end)


def load_prices_from_long_excel(filepath, sheet_name=0, date_col="Date",
                                 ticker_col="Ticker", price_col="Close",
                                 start=None, end=None):
    """
    Load prices from a LONG-format .xlsx/.xls file (one row per date x ticker):

        Date,Ticker,Close
        2020-01-02,AAPL,75.09
        2020-01-02,MSFT,160.62

    Requires: pip install openpyxl
    """
    df = pd.read_excel(filepath, sheet_name=sheet_name)
    return _parse_long_frame(df, date_col, ticker_col, price_col, start, end)


# ======================================================================
# SIGNAL CALCULATIONS
# ======================================================================

def calc_momentum(price_series, lookback, skip_recent):
    """Total return over `lookback` days, skipping the most recent `skip_recent` days."""
    if len(price_series) < lookback + skip_recent:
        return np.nan
    p_end = price_series.iloc[-1 - skip_recent]
    p_start = price_series.iloc[-1 - skip_recent - lookback]
    if p_start <= 0 or pd.isna(p_start) or pd.isna(p_end):
        return np.nan
    return (p_end / p_start) - 1.0


def calc_52w_high(price_series, lookback):
    """Ratio of current price to trailing lookback-period high (closer to 1 = near high)."""
    if len(price_series) < lookback:
        return np.nan
    window = price_series.iloc[-lookback:]
    high = window.max()
    if high <= 0 or pd.isna(high):
        return np.nan
    return window.iloc[-1] / high


def calc_clenow(price_series, lookback):
    """Andreas Clenow-style momentum score: annualized exponential regression
    slope of log price, scaled by R^2 (rewards smooth, consistent trends)."""
    if len(price_series) < lookback:
        return np.nan
    window = price_series.iloc[-lookback:].values
    if np.any(window <= 0):
        return np.nan
    log_prices = np.log(window)
    x = np.arange(len(log_prices))
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, log_prices)
    annualized_return = np.exp(slope * 252) - 1.0
    return annualized_return * (r_value ** 2)


def calc_vol_adj_momentum(price_series, mom_lookback, skip_recent, vol_lookback):
    """12-1 momentum divided by annualized volatility of daily returns
    (risk-adjusted / 'efficiency' momentum)."""
    mom = calc_momentum(price_series, mom_lookback, skip_recent)
    if len(price_series) < vol_lookback + 1:
        return np.nan
    rets = price_series.iloc[-vol_lookback:].pct_change().dropna()
    vol = rets.std() * np.sqrt(252)
    if vol == 0 or pd.isna(vol) or pd.isna(mom):
        return np.nan
    return mom / vol


def compute_raw_signals(prices_up_to_t, tickers):
    """Compute the 5 raw signals for every ticker as of a given date."""
    rows = {}
    for tkr in tickers:
        if tkr not in prices_up_to_t.columns:
            continue
        s = prices_up_to_t[tkr].dropna()
        if len(s) < MIN_HISTORY:
            continue
        rows[tkr] = {
            "mom_12_1": calc_momentum(s, LOOKBACK_12M, SKIP_RECENT),
            "mom_6_1": calc_momentum(s, LOOKBACK_6M, SKIP_RECENT),
            "high_52w": calc_52w_high(s, LOOKBACK_52W),
            "clenow": calc_clenow(s, CLENOW_LOOKBACK),
            "vol_adj": calc_vol_adj_momentum(s, LOOKBACK_12M, SKIP_RECENT, VOL_LOOKBACK),
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def zscore_cross_section(df):
    """Cross-sectional z-score of each signal column at a single date."""
    z = (df - df.mean()) / df.std(ddof=0)
    return z.replace([np.inf, -np.inf], np.nan)


# ======================================================================
# BACKTEST ENGINE
# ======================================================================

def run_backtest(prices, tickers, weights_by_config, top_n, rebalance_freq):
    """
    For each rebalance date, score the universe under every config,
    pick the top N names, hold equal-weighted until next rebalance.
    Returns a DataFrame of period returns per config plus holdings log.
    """
    rebal_dates = prices.resample(rebalance_freq).last().index
    rebal_dates = [d for d in rebal_dates if d in prices.index or True]
    # snap each rebalance date to the nearest available trading day <= date
    trading_index = prices.index
    snapped = []
    for d in rebal_dates:
        idx = trading_index[trading_index <= d]
        if len(idx) > 0:
            snapped.append(idx[-1])
    snapped = sorted(set(snapped))

    period_returns = {name: [] for name in weights_by_config}
    period_dates = []
    holdings_log = {name: {} for name in weights_by_config}

    for i in range(len(snapped) - 1):
        t0 = snapped[i]
        t1 = snapped[i + 1]
        prices_up_to_t0 = prices.loc[:t0]

        raw = compute_raw_signals(prices_up_to_t0, tickers)
        if raw.empty or len(raw) < top_n:
            for name in weights_by_config:
                period_returns[name].append(0.0)
            period_dates.append(t1)
            continue

        z = zscore_cross_section(raw)

        fwd_rets = (prices.loc[t1] / prices.loc[t0]) - 1.0

        for name, w in weights_by_config.items():
            composite = sum(z[col].fillna(0) * wt for col, wt in w.items())
            composite = composite.reindex(raw.index)
            top_names = composite.sort_values(ascending=False).head(top_n).index.tolist()
            valid_names = [n for n in top_names if n in fwd_rets.index and not pd.isna(fwd_rets[n])]
            if valid_names:
                port_ret = fwd_rets[valid_names].mean()
            else:
                port_ret = 0.0
            period_returns[name].append(port_ret)
            holdings_log[name][t0] = top_names

        period_dates.append(t1)

    returns_df = pd.DataFrame(period_returns, index=period_dates)
    return returns_df, holdings_log


# ======================================================================
# PERFORMANCE METRICS
# ======================================================================

def equity_curve(period_returns):
    return (1 + period_returns).cumprod()


def performance_stats(period_returns, periods_per_year=12, rf=RISK_FREE_RATE):
    curve = equity_curve(period_returns)
    total_return = curve.iloc[-1] - 1
    n_periods = len(period_returns)
    years = n_periods / periods_per_year
    cagr = (curve.iloc[-1]) ** (1 / years) - 1 if years > 0 else np.nan

    ann_vol = period_returns.std() * np.sqrt(periods_per_year)
    excess = period_returns - (rf / periods_per_year)
    sharpe = (excess.mean() * periods_per_year) / ann_vol if ann_vol != 0 else np.nan

    running_max = curve.cummax()
    drawdown = curve / running_max - 1
    max_dd = drawdown.min()

    calmar = cagr / abs(max_dd) if max_dd != 0 else np.nan
    win_rate = (period_returns > 0).mean()

    return {
        "Total Return": total_return,
        "CAGR": cagr,
        "Ann. Volatility": ann_vol,
        "Sharpe Ratio": sharpe,
        "Max Drawdown": max_dd,
        "Calmar Ratio": calmar,
        "Win Rate": win_rate,
    }


# ======================================================================
# MAIN
# ======================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Z-Score Composite Momentum Backtester")
    parser.add_argument("--csv", type=str, default=None,
                         help="Path to a local CSV of historical prices, instead of "
                              "downloading via yfinance.")
    parser.add_argument("--csv-format", type=str, choices=["wide", "long"], default="wide",
                         help="'wide' = Date,Ticker1,Ticker2,... columns (default). "
                              "'long' = Date,Ticker,Close rows.")
    parser.add_argument("--excel", type=str, default=None,
                         help="Path to a local .xlsx/.xls file of historical prices, "
                              "instead of downloading via yfinance.")
    parser.add_argument("--excel-sheet", type=str, default="0",
                         help="Sheet name or index (0-based) to read from the Excel file. "
                              "Default is 0 (first sheet).")
    parser.add_argument("--excel-format", type=str, choices=["wide", "long", "transposed"], default="wide",
                         help="'wide' = Date,Ticker1,Ticker2,... columns (default). "
                              "'long' = Date,Ticker,Close rows. "
                              "'transposed' = TICKER row + one date column each (Dhan-style export).")
    parser.add_argument("--ticker-col", type=str, default="TICKER",
                         help="Name of the ticker-identifier column, used by --excel-format transposed.")
    parser.add_argument("--long-date-col", type=str, default="Date")
    parser.add_argument("--long-ticker-col", type=str, default="Ticker")
    parser.add_argument("--long-price-col", type=str, default="Close")
    args = parser.parse_args()

    if args.excel:
        # sheet arg may be an index (e.g. "0") or a sheet name (e.g. "Prices")
        sheet = int(args.excel_sheet) if args.excel_sheet.isdigit() else args.excel_sheet
        print(f"Loading price data from Excel: {args.excel} "
              f"(sheet={sheet}, format={args.excel_format})")
        if args.excel_format == "wide":
            prices = load_prices_from_excel(args.excel, sheet_name=sheet,
                                             start=START_DATE, end=END_DATE)
        elif args.excel_format == "transposed":
            prices = load_prices_transposed_excel(args.excel, sheet_name=sheet,
                                                   ticker_col=args.ticker_col,
                                                   start=START_DATE, end=END_DATE)
        else:
            prices = load_prices_from_long_excel(
                args.excel, sheet_name=sheet,
                date_col=args.long_date_col,
                ticker_col=args.long_ticker_col,
                price_col=args.long_price_col,
                start=START_DATE, end=END_DATE,
            )
        if isinstance(prices, dict):
            print(f"Multiple sheets found: {list(prices.keys())}. "
                  f"Pass --excel-sheet <name or index> to pick one.")
            return
        missing = [t for t in TICKERS + [BENCHMARK] if t not in prices.columns]
        if missing:
            print(f"WARNING: these tickers were not found in the Excel file and will be skipped: {missing}")
    elif args.csv:
        print(f"Loading price data from CSV: {args.csv} (format={args.csv_format})")
        if args.csv_format == "wide":
            prices = load_prices_from_csv(args.csv, start=START_DATE, end=END_DATE)
        else:
            prices = load_prices_from_long_csv(
                args.csv,
                date_col=args.long_date_col,
                ticker_col=args.long_ticker_col,
                price_col=args.long_price_col,
                start=START_DATE, end=END_DATE,
            )
        missing = [t for t in TICKERS + [BENCHMARK] if t not in prices.columns]
        if missing:
            print(f"WARNING: these tickers were not found in the CSV and will be skipped: {missing}")
    else:
        print(f"Downloading price data for {len(TICKERS)} tickers + benchmark...")
        prices = load_prices(TICKERS, BENCHMARK, START_DATE, END_DATE)

    universe_tickers = [t for t in TICKERS if t in prices.columns]
    print(f"Loaded {len(universe_tickers)} tickers with usable history.")

    print("Running backtest across all three configs...")
    returns_df, holdings_log = run_backtest(
        prices, universe_tickers, WEIGHTS, TOP_N, REBALANCE_FREQ
    )

    # Benchmark buy & hold, resampled to same rebalance dates
    bench_prices = prices[BENCHMARK].reindex(returns_df.index.union(prices.index)).ffill()
    bench_at_dates = bench_prices.reindex([returns_df.index[0]] + list(returns_df.index)).dropna()
    bench_period_rets = bench_at_dates.pct_change().dropna()
    bench_period_rets.index = returns_df.index[:len(bench_period_rets)]
    returns_df[f"{BENCHMARK} Buy & Hold"] = bench_period_rets

    curves = returns_df.apply(equity_curve)

    print("\n" + "=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)
    stats_table = {}
    for col in returns_df.columns:
        stats_table[col] = performance_stats(returns_df[col].dropna())
    stats_df = pd.DataFrame(stats_table).T
    pct_cols = ["Total Return", "CAGR", "Ann. Volatility", "Max Drawdown", "Win Rate"]
    display_df = stats_df.copy()
    for c in pct_cols:
        display_df[c] = display_df[c].apply(lambda x: f"{x:.1%}")
    display_df["Sharpe Ratio"] = stats_df["Sharpe Ratio"].apply(lambda x: f"{x:.2f}")
    display_df["Calmar Ratio"] = stats_df["Calmar Ratio"].apply(lambda x: f"{x:.2f}")
    print(display_df.to_string())

    # Save results
    stats_df.to_csv("backtest_performance_summary.csv")
    returns_df.to_csv("backtest_period_returns.csv")
    curves.to_csv("backtest_equity_curves.csv")

    # Plot equity curves
    plt.figure(figsize=(11, 6))
    for col in curves.columns:
        style = "--" if "Buy & Hold" in col else "-"
        plt.plot(curves.index, curves[col], style, label=col, linewidth=2)
    plt.title("Z-Score Composite Momentum: Equity Curves by Config")
    plt.ylabel("Growth of $1")
    plt.xlabel("Date")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig("backtest_equity_curves.png", dpi=150)
    print("\nSaved: backtest_performance_summary.csv, backtest_period_returns.csv,")
    print("       backtest_equity_curves.csv, backtest_equity_curves.png")


if __name__ == "__main__":
    main()
