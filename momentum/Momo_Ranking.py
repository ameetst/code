import pandas as pd
import numpy as np
import re

def clean_price(val):
    if pd.isna(val) or val == '#N/A':
        return np.nan
    # Remove currency symbols, commas, spaces, and non-numeric chars
    val = re.sub(r'[^0-9.\-]', '', str(val))
    try:
        return float(val)
    except:
        return np.nan

def calculate_sharpe(prices):
    returns = prices.pct_change().dropna()
    if len(returns) < 2:
        return np.nan
    mean_return = returns.mean()
    std_return = returns.std()
    if std_return == 0 or np.isnan(std_return):
        return np.nan
    sharpe = (mean_return / std_return) * np.sqrt(252)
    return sharpe

def zscore(series):
    return (series - series.mean()) / series.std(ddof=0)

def main():
    df = pd.read_csv('momentum/nifty500.csv')
    df = df.replace('#N/A', np.nan)
    for col in df.columns[1:]:
        df[col] = df[col].apply(clean_price)
    df = df.dropna(axis=0, how='all', subset=list(df.columns[1:]))
    df = df.tail(365)
    results = []
    for symbol in df.columns[1:]:
        prices = df[symbol]
        sharpe_1y = calculate_sharpe(prices)
        sharpe_9m = calculate_sharpe(prices.tail(189))
        sharpe_6m = calculate_sharpe(prices.tail(126))
        sharpe_3m = calculate_sharpe(prices.tail(63))
        results.append({
            'Symbol': symbol,
            '1Y_SHARPE': sharpe_1y,
            '9M_SHARPE': sharpe_9m,
            '6M_SHARPE': sharpe_6m,
            '3M_SHARPE': sharpe_3m
        })
    out_df = pd.DataFrame(results)
    # Round Sharpe ratios
    for col in ['1Y_SHARPE', '9M_SHARPE', '6M_SHARPE', '3M_SHARPE']:
        out_df[col] = out_df[col].round(2)
    # Calculate z-scores for each Sharpe column
    out_df['1Y_SHARPE_Z'] = zscore(out_df['1Y_SHARPE'])
    out_df['9M_SHARPE_Z'] = zscore(out_df['9M_SHARPE'])
    out_df['6M_SHARPE_Z'] = zscore(out_df['6M_SHARPE'])
    out_df['3M_SHARPE_Z'] = zscore(out_df['3M_SHARPE'])
    # Round z-scores
    for col in ['1Y_SHARPE_Z', '9M_SHARPE_Z', '6M_SHARPE_Z', '3M_SHARPE_Z']:
        out_df[col] = out_df[col].round(2)
    # Calculate 52-week high and last close for each ticker
    highs = df[df.columns[1:]].max()
    lasts = df[df.columns[1:]].iloc[-1]
    # Calculate percent from 52wk high
    percent_from_high = (highs - lasts) / highs
    # Only keep tickers less than 25% from 52wk high
    keep_tickers = percent_from_high[percent_from_high < 0.25].index.tolist()
    out_df = out_df[out_df['Symbol'].isin(keep_tickers)].reset_index(drop=True)
    # Calculate weighted score (equal weights, ignore NaN in mean)
    out_df['WEIGHTED_SCORE'] = out_df[['1Y_SHARPE_Z', '9M_SHARPE_Z', '6M_SHARPE_Z', '3M_SHARPE_Z']].mean(axis=1, skipna=True).round(2)
    # Rank by weighted score descending
    out_df = out_df.sort_values('WEIGHTED_SCORE', ascending=False).reset_index(drop=True)
    out_df.to_csv('momentum/output.csv', index=False)

if __name__ == '__main__':
    main()
