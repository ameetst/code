import requests
import pandas as pd
import json
import datetime
import calendar, sys
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time
from mftool import Mftool
from tqdm import tqdm # For a progress bar

# Base URL for mfapi.in
BASE_URL = "https://api.mfapi.in/mf/"

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
    
    return returns_df

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

# --- Example Usage ---
if __name__ == "__main__":
    # print("--- Getting all scheme codes ---")
    # all_schemes = get_all_scheme_codes()
    # if all_schemes:
    #     print(f"Found {len(all_schemes)} schemes.")
    #     # Print a few examples
    #     for i,scheme_details in enumerate(all_schemes):
    #         print(f"Scheme Name - {scheme_details['schemeName']}, Scheme Code - {scheme_details['schemeCode']}")
    #     df = pd.DataFrame(all_schemes)
    #     df.to_csv("all_schemes.csv", index=False)
    #     print("Saved all_schemes to all_schemes.csv")
    # else:
    #     print("Failed to retrieve all scheme codes.")

    
    print("\n" + "="*50 + "\n")
   
    # Example: Get NAV for a specific scheme
    # You'll need a valid scheme code. You can find them from the 'get_all_scheme_codes()' output
    # or by searching on mfapi.in or AMFI India website.
    # Let's use a common one, e.g., SBI Blue Chip Fund - Direct Plan - Growth (example code from searches)
    sbi_blue_chip_code = "119598"
    print(f"--- Getting NAV data for scheme code: {sbi_blue_chip_code} ---")
    scheme_data = get_mf_data_direct(sbi_blue_chip_code)

    if scheme_data:
        scheme_name = scheme_data.get('meta', {}).get('scheme_name', 'N/A')
        print(f"Scheme Name: {scheme_name}")

        # The 'data' key contains a list of dictionaries with 'date' and 'nav'
        nav_history = scheme_data.get('data', [])

        if nav_history:
            print(f"Found {len(nav_history)} NAV entries.")
            # Convert to Pandas DataFrame for easier analysis
            df = pd.DataFrame(nav_history)
            df['date'] = pd.to_datetime(df['date'], format='%d-%m-%Y')
            df['nav'] = pd.to_numeric(df['nav'])
            df = df.set_index('date').sort_index()

            print("\nLatest 5 NAV entries (DataFrame):")
            print(df.tail())
            print("\nFirst 5 NAV entries (DataFrame):")
            print(df.head())
        else:
            print("No NAV data found for this scheme.")
    else:
        print(f"Failed to retrieve data for scheme code {sbi_blue_chip_code}.")

    # Step 1: Discover available categories (optional, but highly recommended first time)
    # print("--- Discovering all unique mutual fund categories ---")
    # categories = get_all_unique_categories()
    # if categories:
    #     print("\nAvailable Categories:")
    #     for cat in categories:
    #         print(f"- {cat}")
    # else:
    #     print("Could not retrieve categories.")

    # print("\n" + "="*50 + "\n")

    print("\n" + "="*50 + "\n")

    target_category_2 = "Equity - Large Cap Fund" # Another example
    large_cap_funds_df = get_funds_by_category(target_category_2)

    if not large_cap_funds_df.empty:
        print(f"\n--- Funds in category: '{target_category_2}' ---")
        print(f"Total unique funds found: {len(large_cap_funds_df)}")
        print(large_cap_funds_df.head(15).to_string())

    print("\n" + "="*50 + "\n")

    # Step 3: Use the specific function for Mid Cap funds
    target_category = "Equity - Mid Cap Fund"
    all_mid_cap_funds = get_mid_cap_funds()
    if not all_mid_cap_funds.empty:
        print(f"Total unique mid cap funds found: {len(all_mid_cap_funds)}")
        print("--- Top 15 Mid Cap Funds ---")
        print(all_mid_cap_funds.head(15).to_string())
        all_mid_cap_funds.to_csv(f"{target_category.replace(' - ', '_').replace(' ', '_').lower()}_funds.csv", index=False)
        print(f"\nSaved {len(all_mid_cap_funds)} funds to CSV.")
        
        # Example of getting just the scheme codes
        mid_cap_scheme_codes = all_mid_cap_funds['scheme_code'].tolist()
        print("\n--- Example Scheme Codes for Mid Cap Funds ---")
        print(mid_cap_scheme_codes[:5]) # Print first 5 codes
        
    print("\n" + "="*50 + "\n")

    # Step 4: Use the specific function for Flexi Cap funds
    target_category = "Equity - Flexi Cap Fund"
    all_flexi_cap_funds_df = get_flexi_cap_funds()
    if not all_flexi_cap_funds_df.empty:
        all_flexi_cap_funds_df.to_csv(f"{target_category.replace(' - ', '_').replace(' ', '_').lower()}_funds.csv", index=False)
        print(f"\nSaved {len(all_flexi_cap_funds_df)} funds to CSV.")
        print(f"Total unique flexi cap funds found: {len(all_flexi_cap_funds_df)}")
        print("--- Top 15 Flexi Cap Funds ---")
        print(all_flexi_cap_funds_df.head(15).to_string())

    print("\n" + "="*50 + "\n")

    # Step 5: Use the specific function for Small Cap funds
    target_category = "Equity - Small Cap Fund"
    all_small_cap_funds = get_small_cap_funds()
    if not all_small_cap_funds.empty:
        all_small_cap_funds.to_csv(f"{target_category.replace(' - ', '_').replace(' ', '_').lower()}_funds.csv", index=False)
        print(f"\nSaved {len(all_small_cap_funds)} funds to CSV.")
        print(f"Total unique small cap funds found: {len(all_small_cap_funds)}")
        print("--- Top 15 Small Cap Funds ---")
        print(all_small_cap_funds.head(15).to_string())

    print("\n" + "="*50 + "\n")
    
    # Step 6: Use the specific function for Focused funds
    all_focused_funds = get_focused_funds()
    if not all_focused_funds.empty:
        print(f"Total unique focused funds found: {len(all_focused_funds)}")
        print("--- Top 15 Focused Funds ---")
        print(all_focused_funds.head(15).to_string())
        
    print("\n" + "="*50 + "\n")
