import argparse
from pathlib import Path

import pandas as pd
import yfinance as yf


TRADING_DAYS_3M = 63
TRADING_DAYS_6M = 126
TRADING_DAYS_52W = 252


def to_yahoo_symbol(ticker: str, exchange_suffix: str = ".NS") -> str:
    ticker = ticker.strip().upper()
    if "." in ticker:
        return ticker
    return f"{ticker}{exchange_suffix}"


def load_tickers(excel_path: Path, sheet_name: str = "DATA", column_name: str = "TICKER") -> list[str]:
    df = pd.read_excel(excel_path, sheet_name=sheet_name)
    if column_name not in df.columns:
        raise ValueError(f"Column '{column_name}' not found in {excel_path}")

    tickers = (
        df[column_name]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .unique()
        .tolist()
    )
    return sorted(tickers)


def download_data(
    tickers: list[str],
    period: str = "18mo",
    interval: str = "1d",
    exchange_suffix: str = ".NS",
) -> pd.DataFrame:
    yahoo_tickers = [to_yahoo_symbol(ticker, exchange_suffix) for ticker in tickers]
    cache_dir = Path(".yfinance_cache")
    cache_dir.mkdir(exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir.resolve()))

    data = yf.download(
        tickers=yahoo_tickers,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=True,
    )

    if data.empty:
        raise RuntimeError("No data returned from yfinance.")

    return data


def extract_symbol_frame(raw_data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    yahoo_symbol = to_yahoo_symbol(ticker)
    if isinstance(raw_data.columns, pd.MultiIndex):
        if yahoo_symbol not in raw_data.columns.get_level_values(0):
            return pd.DataFrame()
        df = raw_data[yahoo_symbol].copy()
    else:
        df = raw_data.copy()

    df = df.dropna(subset=["Close"]).copy()
    if df.empty:
        return df

    df = df[["Close"]].copy()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    df["Prev3MHigh"] = df["Close"].shift(1).rolling(TRADING_DAYS_3M).max()
    df["Prev6MHigh"] = df["Close"].shift(1).rolling(TRADING_DAYS_6M).max()
    df["High52W"] = df["Close"].rolling(TRADING_DAYS_52W).max()
    return df.dropna().copy()


def evaluate_rules(df: pd.DataFrame) -> dict | None:
    if df.empty:
        return None

    latest = df.iloc[-1]
    close = float(latest["Close"])
    ema50 = float(latest["EMA50"])
    ema200 = float(latest["EMA200"])
    prev_3m_high = float(latest["Prev3MHigh"])
    prev_6m_high = float(latest["Prev6MHigh"])
    high_52w = float(latest["High52W"])

    conditions = {
        "close_gt_50ema": close > ema50,
        "close_gt_200ema": close > ema200,
        "within_15pct_52w_high": close >= high_52w * 0.85,
        "ema50_gt_ema200": ema50 > ema200,
        "new_3m_high": close > prev_3m_high,
        "within_10pct_prev_3m_high": close <= prev_3m_high * 1.10,
        "not_new_6m_high": close < prev_6m_high,
    }

    return {
        "signal_date": df.index[-1].date().isoformat(),
        "close": round(close, 2),
        "ema50": round(ema50, 2),
        "ema200": round(ema200, 2),
        "prev_3m_high": round(prev_3m_high, 2),
        "prev_6m_high": round(prev_6m_high, 2),
        "high_52w": round(high_52w, 2),
        "all_conditions_met": all(conditions.values()),
        **conditions,
    }


def scan_breakouts(tickers: list[str]) -> pd.DataFrame:
    raw_data = download_data(tickers)
    results = []

    for ticker in tickers:
        try:
            df = extract_symbol_frame(raw_data, ticker)
            metrics = evaluate_rules(df)
            if metrics is None:
                results.append({"ticker": ticker, "error": "Insufficient data"})
                continue

            results.append({"ticker": ticker, "error": "", **metrics})
        except Exception as exc:
            results.append({"ticker": ticker, "error": str(exc)})

    results_df = pd.DataFrame(results)
    if "all_conditions_met" in results_df.columns:
        results_df = results_df.sort_values(
            by=["all_conditions_met", "ticker"],
            ascending=[False, True],
            na_position="last",
        )
    return results_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tickers for a 3-month breakout setup.")
    parser.add_argument(
        "--input",
        default="n500.xlsx",
        help="Path to Excel file containing tickers in a TICKER column.",
    )
    parser.add_argument(
        "--output",
        default="three_month_breakout_results.csv",
        help="Path to save the CSV output.",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    tickers = load_tickers(input_path)
    results_df = scan_breakouts(tickers)
    results_df.to_csv(output_path, index=False)

    matches = results_df[results_df.get("all_conditions_met", False) == True].copy()

    print(f"Scanned {len(tickers)} tickers from {input_path}")
    print(f"Saved full results to {output_path}")
    print()

    if matches.empty:
        print("No tickers matched all breakout conditions today.")
        return

    columns_to_show = [
        "ticker",
        "signal_date",
        "close",
        "ema50",
        "ema200",
        "prev_3m_high",
        "prev_6m_high",
        "high_52w",
    ]
    print("Tickers matching all rules:")
    print(matches[columns_to_show].to_string(index=False))


if __name__ == "__main__":
    main()
