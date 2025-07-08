import requests
import pandas as pd
import json
import datetime
import calendar, sys
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time
from mftool import Mftool
from tqdm import tqdm # For a progress bar
import yfinance as yf

# Base URL for mfapi.in
BASE_URL = "https://api.mfapi.in/mf/"

# Benchmark ETF mapping for different fund types
BENCHMARK_MAPPING = {
    "FX": {"symbol": "152106", "name": "NIFTY 500", "description": "NIFTY 500 Index via Fund Code 152106"},
    "FC": {"symbol": "152106", "name": "NIFTY 500", "description": "NIFTY 500 Index via Fund Code 152106"},
    "MC": {"symbol": "146271", "name": "NIFTY MIDCAP 150", "description": "NIFTY MIDCAP 150 Index via Fund Code 146271"},
    "SC": {"symbol": "151375", "name": "NIFTY SMALLCAP 250", "description": "NIFTY SMALLCAP 250 Index via Fund Code 151375"}
}

def get_mf_data_direct(scheme_code: str) -> dict | None:
    """
    Fetches historical NAV data for a given mutual fund scheme code directly from mfapi.in.

    Args:
        scheme_code (str): The unique scheme code for the mutual fund.

    Returns:
        dict | None: A dictionary containing the scheme data if successful, otherwise None.
    """
    url = f"{BASE_URL}{scheme_code}"
    try:
        response = requests.get(url, timeout=10) # Add a timeout for robustness
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data for scheme code {scheme_code}: {e}")
        return None

def get_all_scheme_codes() -> dict | None:
    """
    Fetches a list of all available mutual fund scheme codes and names.

    Returns:
        dict | None: A dictionary mapping scheme codes to names if successful, otherwise None.
    """
    url = "https://api.mfapi.in/mf" # Endpoint for all schemes
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching all scheme codes: {e}")
        return None
        
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

# Initialize Mftool outside the functions to reuse the session
mf = Mftool()

def get_all_unique_categories() -> list[str]:
    """
    Fetches all available mutual fund scheme categories from mfapi.in.
    This can help in finding the exact category names to use for filtering.

    Returns:
        list[str]: A sorted list of unique scheme categories.
    """
    print("Fetching all scheme codes to identify unique categories. This might take a moment...")
    all_scheme_codes = mf.get_scheme_codes()
    if not isinstance(all_scheme_codes, dict):
        print("Failed to retrieve scheme codes or received an unexpected format.")
        return []

    if not all_scheme_codes:
        print("Failed to retrieve any scheme codes.")
        return []

    categories = set()
    print(f"Processing {len(all_scheme_codes)} schemes to find categories...")
    for scheme_code, _ in tqdm(all_scheme_codes.items(), desc="Discovering categories"):
        try:
            scheme_details = mf.get_scheme_details(scheme_code)
            if isinstance(scheme_details, dict) and 'scheme_category' in scheme_details:
                categories.add(scheme_details['scheme_category'])
        except Exception:
            # Silently skip schemes that fail to return details
            pass
    return sorted(list(categories))


def get_funds_by_category(category_name: str) -> pd.DataFrame:
    """
    Extracts all fund names and codes for a given mutual fund category.

    Args:
        category_name (str): The exact name of the mutual fund category
                             (e.g., "Equity - Mid Cap Fund", "Debt - Banking and PSU Fund").
                             Use get_all_unique_categories() to find exact names.

    Returns:
        pd.DataFrame: A DataFrame with 'scheme_code', 'scheme_name', 'fund_house',
                      and 'scheme_category' for funds in the specified category.
                      Returns an empty DataFrame if no funds are found or an error occurs.
    """
    print(f"Fetching all mutual fund schemes for category: '{category_name}'. This will take a while...")
    all_scheme_codes = mf.get_scheme_codes()

    if not isinstance(all_scheme_codes, dict):
        print("Failed to retrieve scheme codes or received an unexpected format.")
        return pd.DataFrame()

    if not all_scheme_codes:
        print("Failed to retrieve any scheme codes.")
        return pd.DataFrame()

    found_funds_data = []
    
    # Use tqdm for a clear progress bar in the console
    for scheme_code, initial_scheme_name in tqdm(all_scheme_codes.items(), desc=f"Filtering for '{category_name}'"):
        try:
            scheme_details = mf.get_scheme_details(scheme_code)
            if isinstance(scheme_details, dict):
                current_category = scheme_details.get('scheme_category')
                
                if current_category == category_name:
                    # Extract relevant information
                    fund_house = scheme_details.get('fund_house')
                    scheme_full_name = scheme_details.get('scheme_name')
                    
                    if scheme_full_name and isinstance(scheme_full_name, str):
                        # Clean up scheme name to remove plan/option suffixes for better grouping
                        cleaned_scheme_name = scheme_full_name.replace(' - Direct Plan', '')
                        cleaned_scheme_name = cleaned_scheme_name.replace(' - Regular Plan', '')
                        cleaned_scheme_name = cleaned_scheme_name.replace(' - Growth Option', '')
                        cleaned_scheme_name = cleaned_scheme_name.replace(' - Dividend Option', '')
                        cleaned_scheme_name = cleaned_scheme_name.replace(' - IDCW', '')
                        cleaned_scheme_name = cleaned_scheme_name.strip()

                        found_funds_data.append({
                            'scheme_code': scheme_code,
                            'scheme_name': cleaned_scheme_name,
                            'full_scheme_name': scheme_full_name, # Keep original for reference
                            'fund_house': fund_house,
                            'scheme_category': current_category
                        })
        except Exception:
            # Skip schemes where fetching details fails
            pass
            
    if found_funds_data:
        df = pd.DataFrame(found_funds_data)
        # Drop duplicates based on cleaned name and fund house to get unique funds
        # A fund might have multiple scheme codes for different plans/options (e.g., Direct Growth, Regular Dividend)
        # This keeps one entry for each unique fund name by fund house.
        df = df.drop_duplicates(subset=['scheme_name', 'fund_house']).reset_index(drop=True)
        return df
    else:
        print(f"No funds found for category: '{category_name}'.")
        return pd.DataFrame()

def get_mid_cap_funds() -> pd.DataFrame:
    """
    A specific function to get all unique Mid Cap mutual funds.

    This is a convenience wrapper around get_funds_by_category.

    Returns:
        pd.DataFrame: A DataFrame with details of Mid Cap funds.
    """
    mid_cap_category = "Equity - Mid Cap Fund"
    print(f"--- Getting all Mid Cap funds ---")
    mid_cap_funds_df = get_funds_by_category(mid_cap_category)
    return mid_cap_funds_df

def get_flexi_cap_funds() -> pd.DataFrame:
    """
    A specific function to get all unique Flexi Cap mutual funds.
    """
    flexi_cap_category = "Equity - Flexi Cap Fund"
    print(f"--- Getting all Flexi Cap funds ---")
    flexi_cap_funds_df = get_funds_by_category(flexi_cap_category)
    return flexi_cap_funds_df

def get_small_cap_funds() -> pd.DataFrame:
    """
    A specific function to get all unique Small Cap mutual funds.
    """
    small_cap_category = "Equity - Small Cap Fund"
    print(f"--- Getting all Small Cap funds ---")
    small_cap_funds_df = get_funds_by_category(small_cap_category)
    return small_cap_funds_df

def get_focused_funds() -> pd.DataFrame:
    """
    A specific function to get all unique Focused mutual funds.
    """
    focused_category = "Equity - Focused Fund"
    print(f"--- Getting all Focused funds ---")
    focused_funds_df = get_funds_by_category(focused_category)
    return focused_funds_df

def get_rolling_returns(scheme_code: str, start_date_str: str, years: int) -> pd.DataFrame | None:
    """
    Calculates the N-year rolling returns for a given mutual fund scheme from a specified start date.

    Args:
        scheme_code (str): The unique scheme code for the mutual fund.
        start_date_str (str): The start date for the calculation in 'YYYY-MM-DD' format.
        years (int): The number of years for the rolling return calculation (e.g., 1, 3, 5).

    Returns:
        pd.DataFrame | None: A DataFrame with 'date' and '{years}y_rolling_return'
                             (as a percentage), or None if data cannot be fetched.
    """
    if not isinstance(years, int) or years <= 0:
        print("Error: The 'years' parameter must be a positive integer.")
        return None

    # Step 1: Fetch historical NAV data for the scheme
    scheme_data = get_mf_data_direct(scheme_code)
    if not scheme_data or 'data' not in scheme_data or not scheme_data['data']:
        print(f"Could not fetch or parse NAV data for scheme code {scheme_code}.")
        return None

    # Step 2: Convert to a Pandas DataFrame and process dates
    try:
        nav_history = scheme_data['data']
        df = pd.DataFrame(nav_history)
        df['date'] = pd.to_datetime(df['date'], format='%d-%m-%Y')
        df['nav'] = pd.to_numeric(df['nav'])
        df = df.set_index('date').sort_index()
    except (KeyError, TypeError, ValueError) as e:
        print(f"Error processing NAV data into a DataFrame: {e}")
        return None

    # Step 3: Resample to daily frequency to fill in missing weekend/holiday data
    # This ensures that pct_change(N*365) correctly looks back N calendar years.
    df = df.resample('D').ffill()

    # Step 4: Filter the DataFrame from the specified start date
    try:
        start_date = pd.to_datetime(start_date_str, format='%Y-%m-%d')
        df = df[df.index >= start_date]
    except ValueError:
        print(f"Invalid start_date_str format: '{start_date_str}'. Please use 'YYYY-MM-DD'.")
        return None

    if df.empty:
        print(f"No NAV data available on or after {start_date_str} for scheme {scheme_code}.")
        return None

    # Step 5: Calculate the N-year rolling return
    periods = years * 365
    column_name = f'{years}y_rolling_return'
    df[column_name] = df['nav'].pct_change(periods=periods) * 100

    # Step 6: Clean up the DataFrame for returning
    # Drop rows where rolling return is NaN (i.e., the first N years of data)
    returns_df = df[[column_name]].dropna().reset_index()
    returns_df.rename(columns={'index': 'date'}, inplace=True)
    
    return pd.DataFrame(returns_df)

def get_rolling_returns_for_scheme(scheme_code: str, start_date_str: str) -> pd.DataFrame | None:
    """
    Calculates the 1-year rolling returns for a given mutual fund scheme from a specified start date.
    This is a wrapper for get_rolling_returns(..., years=1) to maintain backward compatibility.

    Args:
        scheme_code (str): The unique scheme code for the mutual fund.
        start_date_str (str): The start date for the calculation in 'YYYY-MM-DD' format.

    Returns:
        pd.DataFrame | None: A DataFrame with 'date' and '1y_rolling_return'
                             (as a percentage), or None if data cannot be fetched.
    """
    return get_rolling_returns(scheme_code, start_date_str, years=1)

def get_3y_rolling_returns(scheme_code: str, start_date_str: str) -> pd.DataFrame | None:
    """
    Calculates the 3-year rolling returns for a given mutual fund scheme from a specified start date.

    Args:
        scheme_code (str): The unique scheme code for the mutual fund.
        start_date_str (str): The start date for the calculation in 'YYYY-MM-DD' format.

    Returns:
        pd.DataFrame | None: A DataFrame with 'date' and '3y_rolling_return'
                             (as a percentage), or None if data cannot be fetched.
    """
    return get_rolling_returns(scheme_code, start_date_str, years=3)

def get_5y_rolling_returns(scheme_code: str, start_date_str: str) -> pd.DataFrame | None:
    """
    Calculates the 5-year rolling returns for a given mutual fund scheme from a specified start date.

    Args:
        scheme_code (str): The unique scheme code for the mutual fund.
        start_date_str (str): The start date for the calculation in 'YYYY-MM-DD' format.

    Returns:
        pd.DataFrame | None: A DataFrame with 'date' and '5y_rolling_return'
                             (as a percentage), or None if data cannot be fetched.
    """
    return get_rolling_returns(scheme_code, start_date_str, years=5)

def calculate_yoy_consistency_rank(df_results: pd.DataFrame, ranking_methodology: str) -> pd.DataFrame:
    """
    Calculates the Year-On-Year Consistency Rank for a DataFrame of mutual fund returns, based on top 10 appearances.
    """
    headers = df_results.columns.tolist()
    
    # Separate benchmark rows from fund rows
    benchmark_mask = df_results.iloc[:, 0].str.contains('📊', na=False)
    fund_rows = df_results[~benchmark_mask].copy()
    benchmark_rows = df_results[benchmark_mask].copy()
    
    # Highlight top 10 for each period (only for fund rows)
    for i in range(2, len(headers)):
        returns_col = fund_rows.iloc[:, i].copy()
        numeric_returns = []
        valid_indices = {}
        for idx, val in enumerate(returns_col):
            if isinstance(val, str) and val.replace('🥇', '').replace('%', '').strip() not in ('', 'Error'):
                try:
                    numeric_val = float(val.replace('🥇', '').replace('%', '').strip())
                    valid_indices[idx] = len(numeric_returns)
                    numeric_returns.append(numeric_val)
                except (ValueError, TypeError):
                    pass
        if numeric_returns:
            # Get indices of top 10 values (descending)
            top_10_indices = pd.Series(numeric_returns).nlargest(10).index.tolist()
            for original_idx, numeric_idx in valid_indices.items():
                if numeric_idx in top_10_indices:
                    original_value = fund_rows.iloc[original_idx, i]
                    fund_rows.iloc[original_idx, i] = f"🥇 {str(original_value).replace('🥇', '').replace('%', '').strip()}"
    
    # Count the number of top-10 appearances for each fund
    top_10_counts = []
    for index, row in fund_rows.iterrows():
        count = sum(1 for i in range(2, len(row)) if str(row[i]).startswith('🥇'))
        top_10_counts.append(count)
    
    # Set the Rank column to the count
    fund_rows.iloc[:, 1] = top_10_counts
    
    # Remove '🥇' and '%' for sorting, keep as float or blank
    for i in range(2, len(headers)):
        fund_rows.iloc[:, i] = fund_rows.iloc[:, i].apply(lambda x: float(str(x).replace('🥇', '').replace('%', '').strip()) if str(x).replace('🥇', '').replace('%', '').strip() not in ('', 'Error') else '')
    
    # Set benchmark rank to "Benchmark"
    benchmark_rows.iloc[:, 1] = "Benchmark"
    
    # Combine fund rows and benchmark rows
    result_df = pd.concat([fund_rows, benchmark_rows], ignore_index=True)
    return pd.DataFrame(result_df)

def calculate_rolling_period_returns(df_results: pd.DataFrame, dates_chronological: list, ranking_methodology: str) -> pd.DataFrame:
    """
    Calculates rolling returns, highlights top 10 performers, and counts top 10 appearances for each fund.
    """
    headers = df_results.columns.tolist()
    
    # Separate benchmark rows from fund rows
    benchmark_mask = df_results.iloc[:, 0].str.contains('📊', na=False)
    fund_rows = df_results[~benchmark_mask].copy()
    benchmark_rows = df_results[benchmark_mask].copy()
    
    for i in range(2, len(headers)):
        returns_col = fund_rows.iloc[:, i].copy()
        numeric_returns = []
        valid_indices = {}
        for idx, val in enumerate(returns_col):
            if isinstance(val, str) and val.replace('🥇', '').replace('%', '').strip() not in ('', 'Error'):
                try:
                    numeric_val = float(val.replace('🥇', '').replace('%', '').strip())
                    valid_indices[idx] = len(numeric_returns)
                    numeric_returns.append(numeric_val)
                except (ValueError, TypeError):
                    pass
        if numeric_returns:
            top_10_indices = pd.Series(numeric_returns).nlargest(10).index.tolist()
            for original_idx, numeric_idx in valid_indices.items():
                if original_idx in top_10_indices:
                    original_value = fund_rows.iloc[original_idx, i]
                    fund_rows.iloc[original_idx, i] = f"🥇 {str(original_value).replace('🥇', '').replace('%', '').strip()}"
    
    top_10_counts = []
    for index, row in fund_rows.iterrows():
        count = sum(1 for i in range(2, len(row)) if str(row[i]).startswith('🥇'))
        top_10_counts.append(count)
    
    fund_rows.iloc[:, 1] = top_10_counts
    
    for i in range(2, len(headers)):
        fund_rows.iloc[:, i] = fund_rows.iloc[:, i].apply(lambda x: float(str(x).replace('🥇', '').replace('%', '').strip()) if str(x).replace('🥇', '').replace('%', '').strip() not in ('', 'Error') else '')
    
    # Set benchmark rank to "Benchmark"
    benchmark_rows.iloc[:, 1] = "Benchmark"
    
    # Combine fund rows and benchmark rows
    result_df = pd.concat([fund_rows, benchmark_rows], ignore_index=True)
    return pd.DataFrame(result_df)

def calculate_benchmark_outperformance_rank(df_results: pd.DataFrame, dates_chronological: list, ranking_methodology: str) -> pd.DataFrame:
    """
    Calculates benchmark outperformance ranking based on year-by-year comparison.
    Only includes funds with >5 years history and >2000 crores AUM.
    """
    headers = df_results.columns.tolist()
    
    # Separate benchmark rows from fund rows
    benchmark_mask = df_results.iloc[:, 0].astype(str).str.contains('📊', na=False)
    fund_rows = df_results[~benchmark_mask].copy()
    benchmark_rows = df_results[benchmark_mask].copy()
    
    # Get benchmark returns for comparison
    benchmark_returns = []
    if not benchmark_rows.empty:
        for i in range(2, len(headers)):
            benchmark_val = benchmark_rows.iloc[0, i]
            if isinstance(benchmark_val, str) and benchmark_val.replace('%', '').strip() not in ('', 'Error'):
                try:
                    benchmark_returns.append(float(benchmark_val.replace('%', '').strip()))
                except (ValueError, TypeError):
                    benchmark_returns.append(0.0)
            else:
                benchmark_returns.append(0.0)
    
    # Calculate outperformance for each fund
    outperformance_data = []
    for idx, row in fund_rows.iterrows():
        fund_name = row.iloc[0]
        outperformance_periods = 0
        total_outperformance = 0.0
        
        for i in range(2, len(headers)):
            fund_return_str = str(row.iloc[i])
            if fund_return_str.replace('%', '').strip() not in ('', 'Error', 'nan'):
                try:
                    fund_return = float(fund_return_str.replace('%', '').strip())
                    if i-2 < len(benchmark_returns) and benchmark_returns[i-2] != 0.0:
                        outperformance = fund_return - benchmark_returns[i-2]
                        if outperformance > 0:
                            outperformance_periods += 1
                        total_outperformance += outperformance
                except (ValueError, TypeError):
                    pass
        
        outperformance_data.append({
            'fund_name': fund_name,
            'outperformance_periods': outperformance_periods,
            'total_outperformance': total_outperformance,
            'avg_outperformance': total_outperformance / len(headers[2:]) if len(headers[2:]) > 0 else 0
        })
    
    # Create outperformance DataFrame
    outperformance_df = pd.DataFrame(outperformance_data)
    
    # Sort by outperformance periods (descending), then by average outperformance (descending)
    outperformance_df = outperformance_df.sort_values(
        ['outperformance_periods', 'avg_outperformance'], 
        ascending=[False, False]
    )
    
    # Add rank column
    outperformance_df['rank'] = range(1, len(outperformance_df) + 1)
    
    # Highlight top performers
    for idx, row in outperformance_df.iterrows():
        if row['outperformance_periods'] >= len(headers[2:]) * 0.7:  # 70% of periods
            fund_name = row['fund_name']
            # Find the corresponding row in fund_rows and add highlight
            fund_idx = list(fund_rows.index[fund_rows.iloc[:, 0] == fund_name])
            if fund_idx:
                fund_rows.iloc[fund_idx[0], 1] = f"🥇 {row['rank']}"
    
    # Set benchmark rank to "Benchmark"
    benchmark_rows.iloc[:, 1] = "Benchmark"
    
    # Combine fund rows and benchmark rows
    result_df = pd.concat([fund_rows, benchmark_rows], ignore_index=True)
    return pd.DataFrame(result_df)

def get_benchmark_data(benchmark_symbol: str, start_date: date, end_date: date) -> pd.DataFrame | None:
    """
    Fetches historical data for a benchmark index/ETF using yfinance.
    
    Args:
        benchmark_symbol (str): The benchmark symbol (e.g., "^NSEI" for NIFTY 50)
        start_date (date): Start date for data fetching
        end_date (date): End date for data fetching
        
    Returns:
        pd.DataFrame | None: DataFrame with 'date' and 'nav' columns, or None if failed
    """
    try:
        # Convert dates to string format for yfinance
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Fetch data using yfinance
        ticker = yf.Ticker(benchmark_symbol)
        hist_data = ticker.history(start=start_str, end=end_str)
        
        if hist_data.empty:
            print(f"No data found for benchmark {benchmark_symbol}")
            return None
            
        # Convert to the same format as mutual fund data
        benchmark_df = pd.DataFrame({
            'date': hist_data.index,
            'nav': hist_data['Close']
        })
        
        # Reset index to make date a column
        benchmark_df = benchmark_df.reset_index(drop=True)
        benchmark_df['date'] = pd.to_datetime(benchmark_df['date'])
        benchmark_df['nav'] = pd.to_numeric(benchmark_df['nav'])
        
        return benchmark_df.sort_values('date')
        
    except Exception as e:
        print(f"Error fetching benchmark data for {benchmark_symbol}: {e}")
        return None

def get_fund_aum(scheme_code: str) -> float:
    """
    Gets the AUM (Assets Under Management) for a mutual fund scheme.
    
    Args:
        scheme_code (str): The scheme code of the mutual fund
        
    Returns:
        float: AUM in crores, or 0 if not available
    """
    try:
        scheme_details = mf.get_scheme_details(scheme_code)
        if isinstance(scheme_details, dict) and 'aum' in scheme_details:
            aum_str = str(scheme_details['aum'])
            # Remove any non-numeric characters except decimal point
            aum_clean = ''.join(c for c in aum_str if c.isdigit() or c == '.')
            if aum_clean:
                return float(aum_clean)
        return 0.0
    except Exception:
        return 0.0

def calculate_benchmark_returns(benchmark_symbol: str, dates_chronological: list, ranking_methodology: str) -> list:
    """
    Calculates benchmark returns for the given dates and methodology.
    
    Args:
        benchmark_symbol (str): The benchmark symbol (yfinance symbol) or mutual fund code
        dates_chronological (list): List of dates for calculation
        ranking_methodology (str): The ranking methodology ("Year-On-Year Consistency Rank", "xYears Performance Rank", or "Benchmark Outperformance Rank")
        
    Returns:
        list: List of benchmark returns for each period
    """
    benchmark_returns = []
    
    try:
        # Get the earliest and latest dates for data fetching
        start_date = min(dates_chronological)
        end_date = max(dates_chronological)
        
        # Check if benchmark_symbol is a mutual fund code (numeric) or yfinance symbol
        if benchmark_symbol.isdigit():
            # It's a mutual fund code, use mutual fund API
            benchmark_data = get_mf_data_direct(benchmark_symbol)
            if benchmark_data and 'data' in benchmark_data:
                nav_data = pd.DataFrame(benchmark_data['data'])
                nav_data['date'] = pd.to_datetime(nav_data['date'], format='%d-%m-%Y', dayfirst=True)
                nav_data['nav'] = pd.to_numeric(nav_data['nav'])
                nav_data = nav_data.sort_values('date')
                benchmark_data = nav_data
            else:
                print(f"No NAV data for benchmark code {benchmark_symbol}")
                return [""] * (len(dates_chronological) - 1)
        else:
            # It's a yfinance symbol, use yfinance
            benchmark_data = get_benchmark_data(benchmark_symbol, start_date, end_date)
        
        if benchmark_data is None or benchmark_data.empty:
            print(f"Benchmark data is empty for {benchmark_symbol}")
            return [""] * (len(dates_chronological) - 1)
        print(f"Benchmark data head for {benchmark_symbol}:\n", benchmark_data.head())
        
        if ranking_methodology == "Year-On-Year Consistency Rank" or ranking_methodology == "Benchmark Outperformance Rank":
            for i in range(len(dates_chronological) - 1):
                start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                # Find NAV values closest to the dates
                start_nav_row = benchmark_data[benchmark_data['date'] <= pd.to_datetime(start_date)]
                end_nav_row = benchmark_data[benchmark_data['date'] <= pd.to_datetime(end_date)]
                print(f"Period {start_date} to {end_date}: start_nav_row={start_nav_row.tail(1)}, end_nav_row={end_nav_row.tail(1)}")
                if len(start_nav_row) > 0 and len(end_nav_row) > 0:
                    start_nav = start_nav_row.iloc[-1]['nav']
                    end_nav = end_nav_row.iloc[-1]['nav']
                    if start_nav > 0:
                        yoy_return = ((end_nav - start_nav) / start_nav) * 100
                        benchmark_returns.append(f"{yoy_return:.2f}%")
                    else:
                        benchmark_returns.append("")
                else:
                    benchmark_returns.append("")
        elif ranking_methodology == "xYears Performance Rank":
            end_date = dates_chronological[0]
            for i in range(1, len(dates_chronological)):
                start_date = dates_chronological[i]
                start_nav_row = benchmark_data[benchmark_data['date'] <= pd.to_datetime(start_date)]
                end_nav_row = benchmark_data[benchmark_data['date'] <= pd.to_datetime(end_date)]
                print(f"Period {start_date} to {end_date}: start_nav_row={start_nav_row.tail(1)}, end_nav_row={end_nav_row.tail(1)}")
                if len(start_nav_row) > 0 and len(end_nav_row) > 0:
                    start_nav = start_nav_row.iloc[-1]['nav']
                    end_nav = end_nav_row.iloc[-1]['nav']
                    if start_nav > 0:
                        years = (end_nav_row.iloc[-1]['date'] - start_nav_row.iloc[-1]['date']).days / 365.25
                        cagr = ((end_nav / start_nav) ** (1 / years) - 1) * 100 if years > 0 else 0
                        benchmark_returns.append(f"{cagr:.2f}%")
                    else:
                        benchmark_returns.append("")
                else:
                    benchmark_returns.append("")
        return benchmark_returns
    except Exception as e:
        print(f"Error calculating benchmark returns: {e}")
        return [""] * (len(dates_chronological) - 1)

def calculate_benchmark_outperformance_table(fund_returns: list, benchmark_returns: list, fund_names: list, headers: list) -> pd.DataFrame:
    """
    For each fund, for each year, show fund return, benchmark return, and outperformance.
    Rank by number of years outperformed, then by average outperformance.
    """
    results = []
    for idx, row in enumerate(fund_returns):
        mf_name = fund_names[idx]
        outperformance_count = 0
        outperformance_sum = 0
        row_result = [mf_name]
        for j in range(len(benchmark_returns)):
            try:
                fund_ret = float(row[j]) if row[j] not in ('', 'Error', None) else None
            except Exception:
                fund_ret = None
            try:
                bench_ret = float(benchmark_returns[j]) if benchmark_returns[j] not in ('', 'Error', None) else None
            except Exception:
                bench_ret = None
            if fund_ret is not None and bench_ret is not None:
                outperf = fund_ret - bench_ret
                row_result.extend([f"{fund_ret:.2f}%", f"{bench_ret:.2f}%", f"{outperf:.2f}%"])
                if outperf > 0:
                    outperformance_count += 1
                outperformance_sum += outperf
            else:
                row_result.extend(["", "", ""])
        row_result.append(outperformance_count)
        row_result.append(outperformance_sum / len(benchmark_returns) if benchmark_returns else 0)
        results.append(row_result)
    # Sort by outperformance count only
    results.sort(key=lambda x: x[-2], reverse=True)
    # Add rank
    for i, row in enumerate(results):
        row.insert(1, i+1)
    # Build headers
    new_headers = ["MF Name", "Rank"]
    for h in headers:
        new_headers.extend([
            f"{h}",
            f"{h} Benchmark",
            f"{h} Outperformance (%)"
        ])
    new_headers.extend(["Years Outperf", "Avg Outperf"])
    return pd.DataFrame(results, columns=pd.Index(new_headers))

if __name__ == "__main__":
    from datetime import date
    fund_code = "146271"
    start_date = get_last_working_day_for_specific_date(date.today())
    num_dates = 10
    result_dates = get_x_previous_yearly_dates(start_date, num_dates)
    returns = calculate_benchmark_returns(fund_code, result_dates, "Benchmark Outperformance Rank")
    print(f"Year-on-year returns for Nifty Midcap 150 (code {fund_code}): {returns}")
    # Print year-by-year comparison (fund vs benchmark, here both are the same)
    for i, period in enumerate([f"{result_dates[i+1]} to {result_dates[i]}" for i in range(len(result_dates)-1)]):
        print(f"Period {period}: Fund={returns[i]}, Benchmark={returns[i]}")

