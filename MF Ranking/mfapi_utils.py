import requests
import pandas as pd
import json
import datetime
import calendar, sys
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time

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

from mftool import Mftool
import pandas as pd
from tqdm import tqdm # For a progress bar

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
    if not all_scheme_codes:
        print("Failed to retrieve any scheme codes.")
        return []

    categories = set()
    print(f"Processing {len(all_scheme_codes)} schemes to find categories...")
    for scheme_code, _ in tqdm(all_scheme_codes.items(), desc="Discovering categories"):
        try:
            scheme_details = mf.get_scheme_details(scheme_code)
            if scheme_details and 'scheme_category' in scheme_details:
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

    if not all_scheme_codes:
        print("Failed to retrieve any scheme codes.")
        return pd.DataFrame()

    found_funds_data = []
    
    # Use tqdm for a clear progress bar in the console
    for scheme_code, initial_scheme_name in tqdm(all_scheme_codes.items(), desc=f"Filtering for '{category_name}'"):
        try:
            scheme_details = mf.get_scheme_details(scheme_code)
            if scheme_details:
                current_category = scheme_details.get('scheme_category')
                
                if current_category == category_name:
                    # Extract relevant information
                    fund_house = scheme_details.get('fund_house')
                    scheme_full_name = scheme_details.get('scheme_name')
                    
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

# --- Example Usage ---
if __name__ == "__main__":
    print("--- Getting all scheme codes ---")
    all_schemes = get_all_scheme_codes()
    if all_schemes:
        print(f"Found {len(all_schemes)} schemes.")
        # Print a few examples
        for i,scheme_details in enumerate(all_schemes):
            print(f"Scheme Name - {scheme_details['schemeName']}, Scheme Code - {scheme_details['schemeCode']}")
    else:
        print("Failed to retrieve all scheme codes.")

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
    print("--- Discovering all unique mutual fund categories ---")
    categories = get_all_unique_categories()
    if categories:
        print("\nAvailable Categories:")
        for cat in categories:
            print(f"- {cat}")
    else:
        print("Could not retrieve categories.")

    print("\n" + "="*50 + "\n")

    # Step 2: Use a known category to filter funds
    # Choose one of the categories from the list printed above.
    # Common examples: "Equity - Large Cap Fund", "Equity - Mid Cap Fund", "Debt - Liquid Fund"
    
    target_category = "Equity - Mid Cap Fund" # Example category

    mid_cap_funds_df = get_funds_by_category(target_category)

    if not mid_cap_funds_df.empty:
        print(f"\n--- Funds in category: '{target_category}' ---")
        print(f"Total unique funds found: {len(mid_cap_funds_df)}")
        print(mid_cap_funds_df.head(15).to_string()) # Print first 15 for brevity and full string
        
        # You can save the results to a CSV file
        # mid_cap_funds_df.to_csv(f"{target_category.replace(' - ', '_').replace(' ', '_').lower()}_funds.csv", index=False)
        # print(f"\nSaved {len(mid_cap_funds_df)} funds to CSV.")
    else:
        print(f"No funds found for the category '{target_category}'. Check the category name or API connectivity.")

    print("\n" + "="*50 + "\n")

    target_category_2 = "Equity - Large Cap Fund" # Another example
    large_cap_funds_df = get_funds_by_category(target_category_2)

    if not large_cap_funds_df.empty:
        print(f"\n--- Funds in category: '{target_category_2}' ---")
        print(f"Total unique funds found: {len(large_cap_funds_df)}")
        print(large_cap_funds_df.head(15).to_string())
        