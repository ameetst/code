import pandas as pd
import datetime
import calendar, sys
import yfinance as yf
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time
import mutual_fund_analysis_utils

if len(sys.argv) > 1:
    fund_type = sys.argv[1]
else:
    fund_type = "FC"

lwd_prev_month = mutual_fund_analysis_utils.get_last_working_day_for_specific_date(date.today())

print("Fund Type - " + fund_type)
print("Last Day of Previous Month - " + str(lwd_prev_month))

mf_codes_file = fund_type + ".txt"

try:
    with open(mf_codes_file, 'r') as file:
        ticker_list = [line.strip() for line in file]
        #print(f"\nContent of '{mf_codes_file}':\n{content}")
except FileNotFoundError:
    print(f"Error: The file '{mf_codes_file}' was not found.")
except Exception as e:
    print(f"An error occurred: {e}")

# Example 1: Starting from today and getting 5 previous yearly dates
start_date = mutual_fund_analysis_utils.get_last_working_day_for_specific_date(date.today()) # Using a fixed date for consistent example output
num_dates = 10
result_dates = mutual_fund_analysis_utils.get_x_previous_yearly_dates(start_date, num_dates)
print(f"Starting from {start_date.strftime('%Y-%m-%d')}, {num_dates} previous yearly dates:")
for d in result_dates:
    print(d.strftime('%Y-%m-%d'))

# Reverse the list to be in chronological order (earliest to latest)
# This is required by the get_yearly_returns_yahoo function
# Result: [2016-06-17, 2017-06-17, ..., 2025-06-17]
dates_chronological = result_dates[::-1]

print(f"dates_chronological {dates_chronological}")

for ticker_fund in ticker_list:
    print(f"\n--- Calculating returns for {ticker_fund}---")
    fund_returns = mutual_fund_analysis_utils.get_yearly_returns_yahoo(ticker_fund, dates_chronological)

    if fund_returns:
        print(f"\nYearly Returns for {ticker_fund} (Period-wise):")
        for i, ret in enumerate(fund_returns):
            start_d_period = dates_chronological[i].strftime('%Y-%m-%d')
            end_d_period = dates_chronological[i+1].strftime('%Y-%m-%d')
            print(f"  {start_d_period} to {end_d_period}: {ret}") # Format as percentage with 2 decimal places
    else:
        print(f"Could not calculate yearly returns for {ticker_fund}. See warnings above.")

