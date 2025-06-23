import warnings
import pandas as pd
import datetime
import calendar, sys
import yfinance as yf
from dateutil.relativedelta import relativedelta
from datetime import datetime, date, timedelta, time
from mftool import Mftool
import utils._mfapi_utils as mfapi_utils
import streamlit as st 

warnings.filterwarnings('ignore')

# Streamlit page configuration
st.set_page_config(
    page_title="Mutual Fund Ranking",
    page_icon="📊",
    layout="wide"
)

st.title("Mutual Fund Ranking Analysis")

# Dropdown for fund type selection
fund_type_options = {
    "Flexicap": "FX",
    "Focussed": "FC", 
    "Midcap": "MC",
    "Smallcap": "SC"
}

selected_fund_type = st.selectbox(
    "Select Fund Type:",
    options=list(fund_type_options.keys()),
    index=1  # Default to "Focussed" (FC)
)

# Dropdown for ranking methodology selection
ranking_methodology = st.selectbox(
    "Select Ranking Methodology:",
    options=["Year-On-Year Consistency Rank", "# of Years Consistency Rank"],
    index=0
)

# Get the corresponding value for the selected fund type
fund_type = fund_type_options[selected_fund_type]

lwd_prev_month = mfapi_utils.get_last_working_day_for_specific_date(date.today())

st.write(f"**Fund Type:** {selected_fund_type} ({fund_type})")
st.write(f"**Start Date For Computation:** {lwd_prev_month}")

# Calculate dates before reading the file
start_date = mfapi_utils.get_last_working_day_for_specific_date(date.today())
num_dates = 10
result_dates = mfapi_utils.get_x_previous_yearly_dates(start_date, num_dates)
dates_chronological = result_dates

# Use the correct path to the .txt files in the pages directory
mf_codes_file = f"pages/{fund_type}.txt"

try:
    with open(mf_codes_file, 'r') as file:
        ticker_list = [line.strip() for line in file]
        st.success(f"Successfully loaded {len(ticker_list)} fund codes from {mf_codes_file}")
        
        # Calculate returns based on the selected methodology
        if ticker_list and len(dates_chronological) >= 2:
            results_data = []
            headers = ["MF Name", ranking_methodology]

            if ranking_methodology == "Year-On-Year Consistency Rank":
                st.write("**Calculating Year-on-Year Returns...**")
                for i in range(len(dates_chronological) - 1):
                    start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                    headers.append(f"{end_date.strftime('%Y-%m-%d')} to {start_date.strftime('%Y-%m-%d')}")

                for mf_code in filter(str.strip, ticker_list):
                    try:
                        scheme_details = mfapi_utils.mf.get_scheme_details(mf_code)
                        mf_name = scheme_details['scheme_name'] if isinstance(scheme_details, dict) and 'scheme_name' in scheme_details else mf_code
                    except Exception:
                        mf_name = mf_code
                    
                    row_data = [mf_name]
                    scheme_data = mfapi_utils.get_mf_data_direct(mf_code)
                    if not scheme_data or 'data' not in scheme_data:
                        row_data.extend(["N/A"] * (len(dates_chronological) - 1))
                        results_data.append(row_data)
                        continue
                    
                    nav_data = pd.DataFrame(scheme_data['data'])
                    nav_data['date'] = pd.to_datetime(nav_data['date'])
                    nav_data['nav'] = pd.to_numeric(nav_data['nav'])
                    nav_data = nav_data.sort_values('date')

                    for i in range(len(dates_chronological) - 1):
                        start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                        start_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(start_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(start_date)]) > 0 else None
                        end_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(end_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(end_date)]) > 0 else None
                        
                        if start_nav_row is not None and end_nav_row is not None:
                            yoy_return = ((end_nav_row['nav'] - start_nav_row['nav']) / start_nav_row['nav']) * 100
                            row_data.append(f"{yoy_return:.2f}%")
                        else:
                            row_data.append("N/A")
                    results_data.append(row_data)

            elif ranking_methodology == "# of Years Consistency Rank":
                st.write("**Calculating Rolling Returns...**")
                for i in range(1, len(dates_chronological)):
                    headers.append(f"{i}-Year Return")
                
                end_date = dates_chronological[0]
                for mf_code in filter(str.strip, ticker_list):
                    try:
                        scheme_details = mfapi_utils.mf.get_scheme_details(mf_code)
                        mf_name = scheme_details['scheme_name'] if isinstance(scheme_details, dict) and 'scheme_name' in scheme_details else mf_code
                    except Exception:
                        mf_name = mf_code

                    row_data = [mf_name]
                    scheme_data = mfapi_utils.get_mf_data_direct(mf_code)
                    if not scheme_data or 'data' not in scheme_data:
                        row_data.extend(["N/A"] * (len(dates_chronological) - 1))
                        results_data.append(row_data)
                        continue

                    nav_data = pd.DataFrame(scheme_data['data'])
                    nav_data['date'] = pd.to_datetime(nav_data['date'])
                    nav_data['nav'] = pd.to_numeric(nav_data['nav'])
                    nav_data = nav_data.sort_values('date')
                    
                    for i in range(1, len(dates_chronological)):
                        start_date = dates_chronological[i]
                        start_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(start_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(start_date)]) > 0 else None
                        end_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(end_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(end_date)]) > 0 else None
                        
                        if start_nav_row is not None and end_nav_row is not None:
                            start_nav, end_nav = start_nav_row['nav'], end_nav_row['nav']
                            if start_nav > 0:
                                years = (end_nav_row['date'] - start_nav_row['date']).days / 365.25
                                cagr = ((end_nav / start_nav) ** (1 / years) - 1) * 100 if years > 0 else 0
                                row_data.append(f"{cagr:.2f}%")
                            else:
                                row_data.append("N/A")
                        else:
                            row_data.append("N/A")
                    results_data.append(row_data)
            
            # Add RANK column data to each row (placeholder values)
            for row in results_data:
                # Insert placeholder for RANK at the second position
                row.insert(1, "N/A")
            
            # Create DataFrame
            if results_data:
                # Use the selected methodology name for the RANK column header
                df_results = pd.DataFrame(results_data, columns=pd.Index(headers))
                
                # Calculate and display the results based on the selected methodology
                if ranking_methodology == "Year-On-Year Consistency Rank":
                    df_processed = mfapi_utils.calculate_yoy_consistency_rank(df_results.copy(), ranking_methodology)
                    st.write(f"**{ranking_methodology} Table:**")
                    st.write("*🟢 indicates top quartile performance for that period*")
                    st.dataframe(df_processed, use_container_width=True)
                elif ranking_methodology == "# of Years Consistency Rank":
                    df_processed = mfapi_utils.calculate_rolling_period_returns(df_results.copy(), dates_chronological, ranking_methodology)
                    st.write(f"**{ranking_methodology} Table:**")
                    st.write("*🟢 indicates top quartile performance for that period*")
                    st.dataframe(df_processed, use_container_width=True)
                # Future "elif" blocks can go here for other methodologies
                
            else:
                st.warning("No valid mutual fund codes found for calculation.")
except FileNotFoundError:
    st.error(f"Error: The file '{mf_codes_file}' was not found.")
except Exception as e:
    st.error(f"An error occurred: {e}")

