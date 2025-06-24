import pandas as pd
import numpy as np
import os
import re


def calculate_sharpe(returns: pd.Series, window: int) -> float:
    """
    Calculate annualized Sharpe ratio for a given return series and window (in trading days).
    Assumes risk-free rate is zero.
    """
    mean = returns[-window:].mean()
    std = returns[-window:].std()
    if std == 0 or np.isnan(std):
        return np.nan
    sharpe = (mean / std) * np.sqrt(252)  # annualized
    return sharpe


def momentum_score(df: pd.DataFrame, top_n: int = 5, w_3m: float = 0.3, w_6m: float = 0.4, w_1m: float = 0.3):
    """
    df: DataFrame of daily NAVs/prices (index: dates, columns: tickers)
    Returns: List of top_n tickers by composite momentum score
    """
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame")
    returns = df.pct_change().dropna()
    tickers = df.columns
    metrics = { '3m_sharpe': [], '6m_sharpe': [], '1m_return': [] }
    for ticker in tickers:
        r = returns[ticker]
        if not isinstance(r, pd.Series):
            r = pd.Series(r)
        # 3m = 63 trading days, 6m = 126, 1m = 21
        sharpe_3m = calculate_sharpe(r, 63)
        sharpe_6m = calculate_sharpe(r, 126)
        tseries = df[ticker]
        if not isinstance(tseries, pd.Series):
            tseries = pd.Series(tseries)
        ret_1m = (tseries.iloc[-1] / tseries.iloc[-21]) - 1 if len(tseries) > 21 else np.nan
        metrics['3m_sharpe'].append(sharpe_3m)
        metrics['6m_sharpe'].append(sharpe_6m)
        metrics['1m_return'].append(ret_1m)
    metrics_df = pd.DataFrame(metrics, index=tickers)
    # Rank (higher is better)
    metrics_df['rank_3m'] = metrics_df['3m_sharpe'].rank(ascending=False)
    metrics_df['rank_6m'] = metrics_df['6m_sharpe'].rank(ascending=False)
    metrics_df['rank_1m'] = metrics_df['1m_return'].rank(ascending=False)
    # Composite score
    metrics_df['score'] = (
        w_3m * metrics_df['rank_3m'] +
        w_6m * metrics_df['rank_6m'] +
        w_1m * metrics_df['rank_1m']
    )
    metrics_df = metrics_df.sort_values('score', ascending=False)
    return metrics_df.head(top_n)


def clean_price(val):
    if pd.isna(val) or val == '#N/A':
        return np.nan
    # Remove currency symbols, commas, spaces, and non-numeric chars
    val = re.sub(r'[^0-9.\-]', '', str(val))
    try:
        return float(val)
    except:
        return np.nan


def calculate_sharpe(prices, window):
    # Calculate daily returns
    returns = prices.pct_change().dropna()
    if len(returns) < 2:
        return np.nan
    mean_return = returns.mean()
    std_return = returns.std()
    if std_return == 0 or np.isnan(std_return):
        return np.nan
    # Annualize Sharpe ratio (assuming 252 trading days)
    sharpe = (mean_return / std_return) * np.sqrt(252)
    return sharpe


def main():
    df = pd.read_csv('momentum/nifty500.csv')
    df = df.replace('#N/A', np.nan)
    # Clean all price columns
    for col in df.columns[1:]:
        df[col] = df[col].apply(clean_price)
    # Drop rows with all NaNs except date
    df = df.dropna(axis=0, how='all', subset=df.columns[1:])
    # Use the most recent 365 rows (1Y)
    df = df.tail(365)
    results = []
    for symbol in df.columns[1:]:
        prices = df[symbol]
        sharpe_1y = calculate_sharpe(prices, 252)
        sharpe_9m = calculate_sharpe(prices.tail(189), 189)
        sharpe_6m = calculate_sharpe(prices.tail(126), 126)
        sharpe_3m = calculate_sharpe(prices.tail(63), 63)
        results.append({
            'Symbol': symbol,
            '1Y_SHARPE': sharpe_1y,
            '9M_SHARPE': sharpe_9m,
            '6M_SHARPE': sharpe_6m,
            '3M_SHARPE': sharpe_3m
        })
    out_df = pd.DataFrame(results)
    out_df.to_csv('momentum/output.csv', index=False)


if __name__ == "__main__":
    main()
