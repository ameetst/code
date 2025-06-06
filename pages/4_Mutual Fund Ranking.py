import pandas as pd
import datetime 
import calendar, sys
import yfinance as yf
from dateutil.relativedelta import relativedelta

def get_last_working_day_for_specific_date(date_obj):
    first_day_of_current_month = date_obj.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - datetime.timedelta(days=1)

    while last_day_of_previous_month.weekday() > 4:
        last_day_of_previous_month -= datetime.timedelta(days=1)
    return last_day_of_previous_month

def get_date_x_year_prior(current_date,years_prior):
    # Subtracting one month using relativedelta
    x_year_prior_date = current_date - relativedelta(years=years_prior)
    return x_year_prior_date

def get_nav_at_date(ticker_symbol, target_date):
    
    try:
        # yfinance needs a date range. We set start and end to the same day
        # to try and get data for just that day.
        # Sometimes, data for exactly the target_date might be missing if it's a weekend or holiday.
        # We fetch a small range to be safe.
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

if len(sys.argv) > 1:
    fund_type = sys.argv[1]
else:
    fund_type = "FC"

lwd_prev_month = get_last_working_day_for_specific_date(datetime.date.today())

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

    if datetime.date.weekday(one_year_prior_date_today) > 4:
        one_year_prior_date_today = one_year_prior_date_today - (datetime.date.weekday(one_year_prior_date_today) - 4)

    past_nav = get_nav_at_date(i,one_year_prior_date_today)
    