import warnings
import pandas as pd
import datetime
import calendar, sys
import yfinance as yf
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time
from mftool import Mftool
import mfapi_utils

warnings.filterwarnings('ignore')

if len(sys.argv) > 1:
    fund_type = sys.argv[1]
else:
    fund_type = "FC"

lwd_prev_month = mfapi_utils.get_last_working_day_for_specific_date(date.today())

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
start_date = mfapi_utils.get_last_working_day_for_specific_date(date.today()) # Using a fixed date for consistent example output
num_dates = 10
result_dates = mfapi_utils.get_x_previous_yearly_dates(start_date, num_dates)
print(f"Starting from {start_date.strftime('%Y-%m-%d')}, {num_dates} previous yearly dates:")
for d in result_dates:
    print(d.strftime('%Y-%m-%d'))

dates_chronological = result_dates

# Example of using get_rolling_returns_for_scheme
sbi_blue_chip_code = "119598"  # Example scheme code
start_date_for_returns = "2020-01-01"
print(f"\n--- Calculating 1-year rolling returns for scheme code: {sbi_blue_chip_code} ---")
rolling_returns_df = mfapi_utils.get_rolling_returns_for_scheme(sbi_blue_chip_code, start_date_for_returns)

if rolling_returns_df is not None and not rolling_returns_df.empty:
    print(f"Successfully calculated rolling returns from {start_date_for_returns}.")
    print("Latest 5 rolling return entries:")
    print(rolling_returns_df.tail())
    print("\nFirst 5 rolling return entries:")
    print(rolling_returns_df.head())
else:
    print(f"Could not calculate rolling returns for scheme {sbi_blue_chip_code}.")

