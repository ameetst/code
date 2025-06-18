import pandas as pd
import datetime
import calendar, sys
import yfinance as yf
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time

def get_last_working_day_for_specific_date(date_obj):
    first_day_of_current_month = date_obj.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)

    while last_day_of_previous_month.weekday() > 4:
        last_day_of_previous_month -= timedelta(days=1)
    
    last_day_of_previous_month = datetime.combine(last_day_of_previous_month, time.min)
    return last_day_of_previous_month

def get_date_x_year_prior(current_date,years_prior):
    # Subtracting one month using relativedelta
    x_year_prior_date = current_date - relativedelta(years=years_prior)
    return x_year_prior_date

def get_x_previous_yearly_dates(dt: datetime, x: int) -> list[datetime]:
    if not isinstance(dt, datetime):
        raise TypeError("The 'dt' parameter must be a datetime object.")
    if not isinstance(x, int) or x < 0:
        raise ValueError("The 'x' parameter must be a non-negative integer.")

    date_list = []
    current_date = dt

    for _ in range(x):
        date_list.append(current_date)
        current_date = current_date - timedelta(days=365)
        
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
            hist.index = hist.index.tz_localize(None)

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
                        print(f"Warning: Exact date {target_date} not found. Returning NAV for closest prior trading day: {index.strftime('%Y-%m-%d')}")
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
    dates = sorted([d if isinstance(d, date) else d.date() for d in dates])

    # Determine the overall date range for data download, adding a buffer for weekend/holidays
    start_download = min(dates) - timedelta(days=10) # Fetch a few extra days before the first date
    end_download = max(dates) + timedelta(days=10)   # Fetch a few extra days after the last date

    # Fetch historical data
    try:
        # progress=False suppresses the download progress bar
        data = yf.download(ticker, start=start_download, end=end_download, progress=False)
        
        if data.empty:
            print(f"Error: No historical data found for ticker '{ticker}' in the specified date range.")
            return []
        
        # Ensure 'Adj Close' column is present for adjusted prices (which are best for returns)
        if 'Adj Close' not in data.columns:
            print(f"Error: 'Adj Close' column not found for ticker '{ticker}'. Available columns: {data.columns.tolist()}")
            return []
        
        prices_series = data['Adj Close']
        # Convert DatetimeIndex to date objects for easier comparison with input dates
        prices_series.index = prices_series.index.date 

    except Exception as e:
        print(f"Error fetching data for '{ticker}': {e}")
        return []

    returns = []
    # Iterate through consecutive pairs of dates to calculate returns
    # The loop goes from the first date up to the second-to-last date
    # to form pairs (dates[i], dates[i+1])
    for i in range(len(dates) - 1):
        start_date_period = dates[i]
        end_date_period = dates[i+1]

        # Find the price for the start_date_period: last available price on or before start_date_period
        start_price_candidate = prices_series.loc[prices_series.index <= start_date_period]
        if start_price_candidate.empty:
            print(f"Warning: No valid start price found on or before {start_date_period} for {ticker}. Skipping period.")
            continue
        start_price = start_price_candidate.iloc[-1]
        
        # Find the price for the end_date_period: first available price on or after end_date_period
        end_price_candidate = prices_series.loc[prices_series.index >= end_date_period]
        if end_price_candidate.empty:
            print(f"Warning: No valid end price found on or after {end_date_period} for {ticker}. Skipping period.")
            continue
        end_price = end_price_candidate.iloc[0]

        if start_price == 0:
            print(f"Warning: Start price for {ticker} on {start_price_candidate.index[-1]} is zero. Cannot calculate return for period ending {end_date_period}.")
            continue
        
        yearly_return = (end_price - start_price) / start_price
        returns.append(yearly_return)

    return returns


if len(sys.argv) > 1:
    fund_type = sys.argv[1]
else:
    fund_type = "FC"

lwd_prev_month = get_last_working_day_for_specific_date(date.today())

print("fund type - " + fund_type)
print("last working day - " + str(lwd_prev_month))

mf_codes_file = fund_type + ".txt"

try:
    with open(mf_codes_file, 'r') as file:
        content = [line.strip() for line in file]
        #print(f"\nContent of '{mf_codes_file}':\n{content}")
except FileNotFoundError:
    print(f"Error: The file '{mf_codes_file}' was not found.")
except Exception as e:
    print(f"An error occurred: {e}")

for i in content:
    print(f"{yf.Ticker(i).info["shortName"].strip('"')} - {yf.Ticker(i).get_fast_info().last_price} \n")

    one_year_prior_date_today = get_date_x_year_prior(lwd_prev_month,1)

    if date.weekday(one_year_prior_date_today) > 4:
        one_year_prior_date_today = one_year_prior_date_today - (date.weekday(one_year_prior_date_today) - 4)

    past_nav = get_nav_at_date(i,one_year_prior_date_today)


# Example 1: Starting from today and getting 5 previous yearly dates
start_date = get_last_working_day_for_specific_date(date.today()) # Using a fixed date for consistent example output
num_dates = 10
result_dates = get_x_previous_yearly_dates(start_date, num_dates)
print(f"Starting from {start_date.strftime('%Y-%m-%d')}, {num_dates} previous yearly dates:")
for d in result_dates:
    print(d.strftime('%Y-%m-%d'))

# Define a recent end date for the series
end_date_for_series = start_date # Using a fixed date for reproducible example

# Generate dates going backward from the end_date_for_series
# This will give: [2025-06-17, 2024-06-17, ..., 2016-06-17]
dates_reversed_order = []
for i in range(10):
    # Using relativedelta for accurate year subtraction (handles leap years correctly)
    past_date = end_date_for_series - relativedelta(years=i)
    dates_reversed_order.append(past_date)
        
# Reverse the list to be in chronological order (earliest to latest)
# This is required by the get_yearly_returns_yahoo function
# Result: [2016-06-17, 2017-06-17, ..., 2025-06-17]
dates_chronological = dates_reversed_order[::-1]

print(f"Generated chronological dates for analysis:\n{[d.strftime('%Y-%m-%d') for d in dates_chronological]}")

# --- Step 2: Use the generated dates with the get_yearly_returns_yahoo function
# The ticker '0P0000YWL1.BO' corresponds to "ICICI Prudential Equity & Debt Fund - Growth - Direct Plan"
# on Yahoo Finance.

# You might need to install 'yfinance' and 'python-dateutil' if you haven't:
# pip install yfinance pandas python-dateutil

ticker_fund = '0P0000YWL1.BO' # Example: ICICI Prudential Equity & Debt Fund (on Yahoo Finance)

print(f"\n--- Calculating returns for {ticker_fund} ---")
fund_returns = get_yearly_returns_yahoo(ticker_fund, dates_chronological)

if fund_returns:
    print(f"\nYearly Returns for {ticker_fund} (Period-wise):")
    for i, ret in enumerate(fund_returns):
        start_d_period = dates_chronological[i].strftime('%Y-%m-%d')
        end_d_period = dates_chronological[i+1].strftime('%Y-%m-%d')
        print(f"  {start_d_period} to {end_d_period}: {ret:.2%}") # Format as percentage with 2 decimal places
else:
    print(f"Could not calculate yearly returns for {ticker_fund}. See warnings above.")

