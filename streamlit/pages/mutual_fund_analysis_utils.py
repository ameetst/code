import pandas as pd
import datetime
import calendar, sys
import yfinance as yf
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time

def get_fund_name(fund_ticker) -> str:
    ticker_name = str(yf.Ticker(fund_ticker).info.get('longName'))
    return ticker_name

def get_last_working_day_for_specific_date(date_obj):
    first_day_of_current_month = date_obj.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)

    while last_day_of_previous_month.weekday() > 4:
        last_day_of_previous_month -= timedelta(days=1)
    
    last_day_of_previous_month = last_day_of_previous_month
    return last_day_of_previous_month

def get_date_x_year_prior(current_date,years_prior):
    # Subtracting one month using relativedelta
    x_year_prior_date = current_date - relativedelta(years=years_prior)
    return x_year_prior_date

def get_x_previous_yearly_dates(dt: date, x: int) -> list[date]:
    if not isinstance(dt, date):
        raise TypeError("The 'dt' parameter must be a date object.")
    if not isinstance(x, int) or x < 0:
        raise ValueError("The 'x' parameter must be a non-negative integer.")

    date_list = []
    current_date = dt

    for _ in range(x):
        date_list.append(current_date)

        # 1. Get the month of the input date
        target_month = current_date.month

        # 2. Get the year of the previous year
        previous_year_date = current_date - relativedelta(years=1)
        target_year = previous_year_date.year

        # 3. Determine the last day of that month in the previous year
        # Go to the first day of the *next* month in the target year, then subtract one day.
        # This correctly handles months with 28, 29, 30, or 31 days.
        first_day_of_next_month = date(target_year, target_month, 1) + relativedelta(months=1)
        last_day_of_month = first_day_of_next_month - timedelta(days=1)

        # 4. Check if it's a working day (Monday=0 to Friday=4, Saturday=5, Sunday=6)
        # If it's Saturday (weekday() == 5), go back 1 day
        # If it's Sunday (weekday() == 6), go back 2 days

        current_date = last_day_of_month

        if current_date.weekday() == 5:  # Saturday
            current_date -= timedelta(days=1)
        elif current_date.weekday() == 6:  # Sunday
            current_date -= timedelta(days=2)
          
    return date_list

def get_nav_at_date(ticker_symbol, target_date):
    
    try:
        start_date = pd.to_datetime(target_date) - pd.Timedelta(days=5)
        end_date = pd.to_datetime(target_date) + pd.Timedelta(days=5)

        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'))

        if not hist.empty:
            # Find the exact date's data
            # Ensure the index is timezone-naive for direct comparison
            hist.index = hist.index.tz_localize(None) # type: ignore

            # Look for the exact target date
            if pd.to_datetime(target_date) in hist.index:
                nav = hist.loc[pd.to_datetime(target_date)]['Close']
                return nav
            else:
                # If exact date not found, try to find the closest previous trading day
                # Sort by date in descending order to easily find the most recent
                hist = hist.sort_index(ascending=False)
                for index, row in hist.iterrows():
                    if index <= pd.to_datetime(target_date):
                        print(f"Warning: Exact date {target_date} not found. Returning NAV for closest prior trading day: {index.strftime('%Y-%m-%d')}") # type: ignore
                        return row['Close']
                print(f"No trading data found for {ticker_symbol} on or before {target_date} within the fetched range.")
                return None
        else:
            print(f"No historical data found for {ticker_symbol} in the specified range.")
            return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    
def get_yearly_returns_yahoo(ticker: str, dates: list[date]) -> list[float]:
    """
    Calculates yearly returns for a given Yahoo Finance ticker based on a list of dates.
    The function expects the 'dates' list to be sorted in ascending order (earliest to latest).
    It will find the closest available trading day's price for each specified date.

    Args:
        ticker (str): The Yahoo Finance ticker symbol (e.g., '0P0000YWL1.BO').
        dates (list[datetime.date]): A list of datetime.date objects,
                                    sorted from earliest to latest. Ideally, 10 dates
                                    for 9 yearly returns, but handles fewer.

    Returns:
        list[float]: A list of percentage returns for each consecutive yearly period.
                     Returns for the period (dates[i] to dates[i+1]) will be calculated as
                     (Price_at_dates[i+1] - Price_at_dates[i]) / Price_at_dates[i].
                     Returns an empty list if data is insufficient or invalid.
    """
    if not ticker or not isinstance(ticker, str):
        raise ValueError("Ticker must be a non-empty string.")
    if not isinstance(dates, list) or len(dates) < 2:
        print("Warning: 'dates' list must contain at least two dates to calculate returns.")
        return []

    # Ensure dates are sorted and are datetime.date objects (in case datetime.datetime was passed)
    dates = sorted([d if isinstance(d, date) else d.date() for d in dates], reverse= True)
       
    # Determine the overall date range for data download, adding a buffer for weekend/holidays
    start_download = min(dates) 
    end_download = max(dates) + timedelta(days = 1)

    # Fetch historical data
    try:
        # progress=False suppresses the download progress bar
        data = yf.download(ticker, start=start_download, end=end_download, progress=False)
                
        if data.empty: # type: ignore
            print(f"Error: No historical data found for ticker '{ticker}' in the specified date range.")
            return []
        
        # Ensure 'Adj Close' column is present for adjusted prices (which are best for returns)
        if 'Close' not in data.columns: # type: ignore
            print(f"Error: 'Close' column not found for ticker '{ticker}'. Available columns: {data.columns.tolist()}") # type: ignore
            return []
        
        prices_series = data['Close'] # type: ignore
        # Convert DatetimeIndex to date objects for easier comparison with input dates
        prices_series.index = prices_series.index.date  # type: ignore

    except Exception as e:
        print(f"Error fetching data for '{ticker}': {e}")
        return []

    returns = []
    # Iterate through consecutive pairs of dates to calculate returns
    # The loop goes from the first date up to the second-to-last date
    # to form pairs (dates[i], dates[i+1])
    for i in range(len(dates) - 1):
        end_date_period = dates[i]
        start_date_period = dates[i+1]

        # Find the price for the start_date_period: last available price on or before start_date_period
        start_price_record = prices_series.loc[prices_series.index == start_date_period]
        end_price_record = prices_series.loc[prices_series.index == end_date_period]

        if start_price_record.empty:
            print(f"Warning: No valid start price found on or before {start_date_period} for {ticker}. Skipping period.")
            continue
        start_price = start_price_record.iloc[-1]
        
        # Find the price for the end_date_period: first available price on or after end_date_period
        end_price_candidate = prices_series.loc[prices_series.index >= end_date_period]
        if end_price_record.empty:
            print(f"Warning: No valid end price found on or after {end_date_period} for {ticker}. Skipping period.")
            continue
        end_price = end_price_record.iloc[0]
        
        if int(start_price) == 0:
            print(f"Warning: Start price for {ticker} on {start_price_record.index[-1]} is zero. Cannot calculate return for period ending {end_date_period}.")
            continue
        
        yearly_return = end_price/start_price - 1 

        returns.append(round(float(yearly_return*100),2))

    return returns