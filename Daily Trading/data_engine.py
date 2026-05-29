import pandas as pd
import yfinance as yf
import os
import datetime

def get_nifty_universe(csv_path="ind_niftytotalmarket_list (3).csv"):
    """Reads the NSE total market CSV and returns a list of yfinance tickers."""
    try:
        df = pd.read_csv(csv_path)
        # Assuming the column is 'Symbol' based on earlier inspection
        symbols = df['Symbol'].dropna().unique().tolist()
        # Add .NS suffix for Yahoo Finance
        yf_tickers = [f"{sym}.NS" for sym in symbols]
        return yf_tickers, df
    except Exception as e:
        print(f"Error reading universe CSV: {e}")
        return [], pd.DataFrame()

def fetch_market_data(tickers, lookback_years=1):
    """
    Fetches historical daily data for a list of tickers.
    Uses yfinance batch download for speed.
    """
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365 * lookback_years)
    
    print(f"Downloading data for {len(tickers)} tickers...")
    # Grouped by Ticker (multi-index columns)
    data = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True)
    return data

def fetch_index_data(index_ticker="^CRSLDX", lookback_years=1):
    """Fetches historical data for a market index (default Nifty 500)."""
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=365 * lookback_years)
    data = yf.download(index_ticker, start=start_date, end=end_date)
    return data

def update_data(csv_path="ind_niftytotalmarket_list (3).csv", lookback_years=1, save_path="market_data.parquet"):
    """Main routine to fetch and save data locally."""
    yf_tickers, _ = get_nifty_universe(csv_path)
    if not yf_tickers:
        return None
        
    data = fetch_market_data(yf_tickers, lookback_years)
    # Save to parquet for fast loading
    # We must flatten the multi-index columns to save nicely or just use pickle/parquet
    # Parquet handles multi-index columns fine in newer pandas, but to be safe we can use pickle
    data.to_pickle(save_path.replace('.parquet', '.pkl'))
    
    # Also fetch index
    index_data = fetch_index_data("^CRSLDX", lookback_years)
    index_data.to_pickle("index_data.pkl")
    
    print("Data download and save complete.")
    return data

def load_local_data(data_path="market_data.pkl", index_path="index_data.pkl"):
    """Loads cached data from disk if it exists."""
    if os.path.exists(data_path) and os.path.exists(index_path):
        data = pd.read_pickle(data_path)
        index_data = pd.read_pickle(index_path)
        return data, index_data
    return None, None
