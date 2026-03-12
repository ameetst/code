"""
Clenow Momentum Ranking — NSE 500/750
======================================
Based on Andreas Clenow's algorithm from "Stocks on the Move"

    Momentum Score = Annualized Exponential Regression Slope × R²

Input file format (e.g. n500.xlsx):
  - Sheet: DATA
  - Col A : TICKER
  - Col B : CLOSE (latest)
  - Col C : 52WK HIGH
  - Col D+ : Weekly dates as headers, price on that date (0 = no data)
  - Index ticker (NIFTY500) is one of the rows

Usage:
  python clenow_nse500.py --file n500.xlsx [--top_n 50] [--account_value 1000000] [--output ranked_output.csv]
"""

import argparse
from typing import Any
import sys
import warnings
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ── Strategy Parameters ────────────────────────────────────────────────────────

MOMENTUM_WINDOW       = 90    # trading days for regression
MA_STOCK_WINDOW       = 100   # stock 100-day MA filter
MA_INDEX_WINDOW       = 200   # index 200-day MA market filter
GAP_THRESHOLD         = 0.15  # disqualify if any 1-day gap > 15%
VOLATILITY_WINDOW     = 20    # trading days for inverse-vol position sizing
TRADING_DAYS_PER_YEAR = 252
INDEX_TICKER          = "NIFTY500"


# ── Core Math ──────────────────────────────────────────────────────────────────

def exp_regression(prices: np.ndarray) -> tuple[float, float]:
    """
    Fit log-linear regression on prices.
    Returns (annualized_slope_pct, r_squared).
    """
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


# ── File Loading ───────────────────────────────────────────────────────────────

def load_data(filepath: str) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """
    Load n500.xlsx and reshape into:
      - stock_prices : DataFrame  [ticker × date], non-zero prices only (NaN elsewhere)
      - index_prices : Series     [date] for NIFTY500
      - meta         : DataFrame  [ticker, close, 52wk_high]

    Returns (stock_prices, index_series, meta_df)
    """
    print(f"Loading {filepath} ...")
    raw = pd.read_excel(filepath, sheet_name="DATA", header=0)

    # Rename first 3 cols
    raw.columns = ["ticker", "close", "52wk_high"] + list(raw.columns[3:])

    # Date columns start at index 3
    date_cols = raw.columns[3:]

    # Replace 0s with NaN (non-trading / missing)
    price_data = raw[["ticker"] + list(date_cols)].copy()
    price_data[date_cols] = price_data[date_cols].replace(0, np.nan)

    # Set ticker as index
    price_data = price_data.set_index("ticker")

    # Separate index from stocks
    if INDEX_TICKER not in price_data.index:
        raise ValueError(f"'{INDEX_TICKER}' not found in the TICKER column.")

    index_series  = price_data.loc[INDEX_TICKER].dropna().astype(float)
    stock_prices  = price_data.drop(index=INDEX_TICKER).astype(float)

    meta = raw[["ticker", "close", "52wk_high"]].set_index("ticker")
    meta = meta.drop(index=INDEX_TICKER)

    print(f"  Tickers loaded  : {len(stock_prices)}")
    print(f"  Date columns    : {len(date_cols)}  ({date_cols[0].date()} → {date_cols[-1].date()})")
    print(f"  Index ticker    : {INDEX_TICKER} ({index_series.notna().sum()} trading days)")

    return stock_prices, index_series, meta


# ── Market Filter ──────────────────────────────────────────────────────────────

def check_market_filter(index_series: pd.Series) -> tuple[bool, float, float]:
    """
    Returns (above_ma200, last_index_close, ma200_value).
    Uses the most recent MA_INDEX_WINDOW non-NaN index prices.
    """
    valid = index_series.dropna()
    if len(valid) < MA_INDEX_WINDOW:
        print(f"  ⚠ Only {len(valid)} index data points — market filter skipped (needs {MA_INDEX_WINDOW}).")
        return True, valid.iloc[-1], np.nan

    last_close = valid.iloc[-1]
    ma200      = valid.iloc[-MA_INDEX_WINDOW:].mean()
    above      = last_close > ma200
    return above, last_close, ma200


# ── Per-Ticker Analysis ────────────────────────────────────────────────────────

def analyse_ticker(ticker: str, price_row: pd.Series) -> dict:
    """
    Analyse one ticker row and return a result dict.
    price_row is a Series indexed by date, with NaN for missing.
    """
    # Drop NaN/zero entries to get actual trading day prices in order
    prices = price_row.dropna().astype(float)
    prices = prices[prices > 0]

    base: dict[str, Any] = dict(
        ticker            = ticker,
        last_close        = np.nan,
        ma_100            = np.nan,
        below_ma100       = np.nan,
        annualized_slope  = np.nan,
        r_squared         = np.nan,
        momentum_score    = np.nan,
        volatility_20d    = np.nan,
        rank              = np.nan,
        signal            = "DISQUALIFIED",
        disqualify_reason = "",
    )

    if len(prices) < MOMENTUM_WINDOW:
        base["disqualify_reason"] = f"Insufficient data ({len(prices)} pts, need {MOMENTUM_WINDOW})"
        return base

    base["last_close"] = round(prices.iloc[-1], 4)

    # ── Gap filter ─────────────────────────────────────────────────────────────
    recent_prices = prices.iloc[-MOMENTUM_WINDOW:]
    daily_returns = recent_prices.pct_change().abs()
    if (daily_returns > GAP_THRESHOLD).any():
        base["disqualify_reason"] = f"Gap > {GAP_THRESHOLD*100:.0f}% in last {MOMENTUM_WINDOW} days"
        return base

    # ── 100-day MA filter ──────────────────────────────────────────────────────
    if len(prices) >= MA_STOCK_WINDOW:
        ma100           = prices.iloc[-MA_STOCK_WINDOW:].mean()
        base["ma_100"]  = round(ma100, 4)
        base["below_ma100"] = bool(prices.iloc[-1] < ma100)
    else:
        base["below_ma100"] = False   # not enough history — don't penalize

    # ── Momentum score ─────────────────────────────────────────────────────────
    score, slope, r2 = clenow_score(recent_prices.values)
    base["annualized_slope"] = round(slope, 4) if not np.isnan(slope) else np.nan
    base["r_squared"]        = round(r2,    4) if not np.isnan(r2)    else np.nan
    base["momentum_score"]   = round(score, 4) if not np.isnan(score) else np.nan

    # ── 20-day volatility (annualised std dev of daily returns) ────────────────
    vol_prices = prices.iloc[-VOLATILITY_WINDOW:]
    if len(vol_prices) >= 2:
        daily_rets = vol_prices.pct_change().dropna()
        vol = daily_rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        base["volatility_20d"] = round(vol, 6) if vol > 0 else np.nan

    base["signal"] = "PENDING"   # will be assigned after ranking
    return base


# ── Main Ranking ───────────────────────────────────────────────────────────────

def rank(filepath: str, top_n: int, output_path: str, account_value: float = 0):

    # 1. Load
    stock_prices, index_series, meta = load_data(filepath)

    # 2. Market filter
    market_ok, idx_close, idx_ma200 = check_market_filter(index_series)
    status = "ABOVE ✓" if market_ok else "BELOW ✗"
    print(f"\n  NIFTY500 last close : {idx_close:,.2f}")
    if not np.isnan(idx_ma200):
        print(f"  NIFTY500 200-day MA : {idx_ma200:,.2f}  [{status}]")
    if not market_ok:
        print("  ⚠  Market filter FAILED — no new BUY signals.")

    # 3. Score every ticker
    print(f"\nScoring {len(stock_prices)} tickers ...")
    results = []
    for ticker, row in stock_prices.iterrows():
        results.append(analyse_ticker(ticker, row))

    df = pd.DataFrame(results)

    # 4. Rank qualified tickers
    qualified_mask = (df["signal"] == "PENDING") & df["momentum_score"].notna()
    qualified      = df[qualified_mask].copy()
    disqualified   = df[~qualified_mask].copy()

    qualified = qualified.sort_values("momentum_score", ascending=False).reset_index(drop=True)
    qualified["rank"] = qualified.index + 1

    n_qualified  = len(qualified)
    top_n        = min(top_n, n_qualified)   # cap at available qualified count
    cutoff_score = qualified["momentum_score"].iloc[top_n - 1] if n_qualified > 0 else np.nan

    # 5. Assign signals
    def assign_signal(row):
        if row["rank"] > top_n:
            return "HOLD / SELL"
        if row["below_ma100"]:
            return "HOLD / SELL"   # in top 20% but price < 100-day MA
        if market_ok:
            return "BUY"
        else:
            return "HOLD"          # top 20% but market filter blocks new buys

    qualified["signal"] = qualified.apply(assign_signal, axis=1)
    disqualified["signal"] = disqualified["signal"].replace("PENDING", "DISQUALIFIED")

    # ── Inverse volatility weighting (top N stocks only) ──────────────────────
    top_mask   = qualified["rank"] <= top_n
    top_stocks = qualified[top_mask].copy()

    inv_vol = pd.Series(
        1.0 / top_stocks["volatility_20d"].replace(0, np.nan),
        index=top_stocks.index,
    )
    total_inv_vol = inv_vol.sum()

    if total_inv_vol > 0:
        weight_pct: pd.Series = pd.Series((inv_vol / total_inv_vol) * 100)
        qualified.loc[top_mask, "inv_vol_weight_pct"] = np.round(weight_pct, 2)
        if account_value > 0:
            alloc: pd.Series = pd.Series((inv_vol / total_inv_vol) * account_value)
            qualified.loc[top_mask, "allocated_amount"] = np.round(alloc, 2)
            qualified.loc[top_mask, "approx_shares"] = (
                qualified.loc[top_mask, "allocated_amount"] /
                qualified.loc[top_mask, "last_close"]
            ).apply(lambda x: int(x) if not np.isnan(x) else np.nan)
    else:
        qualified["inv_vol_weight_pct"] = np.nan

    # 6. Merge and add meta
    final = pd.concat([qualified, disqualified], ignore_index=True)
    final = final.merge(meta[["close", "52wk_high"]].rename(
        columns={"close": "last_close_raw", "52wk_high": "52wk_high"}
    ).reset_index(), on="ticker", how="left")

    # 7. Clean up output columns
    output_cols = [
        "rank", "ticker", "signal",
        "momentum_score", "annualized_slope", "r_squared",
        "last_close", "ma_100", "below_ma100",
        "volatility_20d", "inv_vol_weight_pct",
        "allocated_amount", "approx_shares",
        "52wk_high", "disqualify_reason"
    ]
    final = final[[c for c in output_cols if c in final.columns]]
    final = final.sort_values(["rank"], na_position="last")

    # 8. Print summary
    print(f"\n{'═'*55}")
    print(f"  CLENOW MOMENTUM RANKING RESULTS")
    print(f"{'═'*55}")
    print(f"  Universe            : {len(stock_prices)} stocks")
    print(f"  Qualified           : {n_qualified}")
    print(f"  Disqualified        : {len(disqualified)}")
    print(f"  Top N threshold     : top {top_n} stocks")
    if not np.isnan(cutoff_score):
        print(f"  Min BUY score       : {cutoff_score:.2f}")
    print(f"\n  Signal breakdown:")
    for sig, cnt in final["signal"].value_counts().items():
        print(f"    {sig:<20} {cnt}")

    print(f"\n  Top 20 HOLD/BUY candidates (by momentum rank):")
    top_df = final[final["signal"].isin(["BUY", "HOLD"])].head(20)
    if len(top_df) == 0:
        print("    (none)")
    else:
        display_cols = ["rank", "ticker", "signal", "momentum_score",
                        "annualized_slope", "r_squared", "volatility_20d",
                        "inv_vol_weight_pct", "last_close"]
        if account_value > 0:
            display_cols += ["allocated_amount", "approx_shares"]
        print(top_df[display_cols].to_string(index=False))

    # 9. Save
    final.to_csv(output_path, index=False)
    print(f"\n  ✓ Full results saved to: {output_path}")
    print(f"{'═'*55}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Clenow Momentum Ranking for NSE 500/750"
    )
    parser.add_argument(
        "--file", required=True,
        help="Path to input Excel file (e.g. n500.xlsx)"
    )
    parser.add_argument(
        "--top_n", type=int, default=50,
        help="Number of top momentum stocks to flag as BUY/HOLD (default: 50)"
    )
    parser.add_argument(
        "--output", default="clenow_ranked.csv",
        help="Output CSV filename (default: clenow_ranked.csv)"
    )
    parser.add_argument(
        "--account_value", type=float, default=0,
        help="Total portfolio value in INR for position sizing (optional, e.g. 1000000)"
    )
    args = parser.parse_args()
    rank(args.file, args.top_n, args.output, account_value=args.account_value)


if __name__ == "__main__":
    main()