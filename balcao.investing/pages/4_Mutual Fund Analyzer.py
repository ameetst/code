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
from tqdm import tqdm
import os

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
    "Select a Fund Family": None,
    "Flexicap": "FX",
    "Focussed": "FC", 
    "Midcap": "MC",
    "Smallcap": "SC"
}

selected_fund_type = st.selectbox(
    "Select Fund Family:",
    options=list(fund_type_options.keys()),
    index=0  # Default to 'Select an Option'
)

# Dropdown for ranking methodology selection
ranking_methodology_options = ["Select a Ranking Methodology", "Year-On-Year Consistency Rank", "xYears Performance Rank", "Benchmark Outperformance Rank"]
ranking_methodology = st.selectbox(
    "Select Ranking Methodology:",
    options=ranking_methodology_options,
    index=0
)



# Only proceed if both dropdowns have a valid selection
if selected_fund_type != "Select a Fund Family" and ranking_methodology != "Select a Ranking Methodology":
    fund_type = fund_type_options[selected_fund_type]
    
    # Additional check to ensure fund_type is not None
    if fund_type is None:
        st.error("Please select a valid fund family.")
    else:
        lwd_prev_month = mfapi_utils.get_last_working_day_for_specific_date(date.today())
        st.write(f"**Fund Type:** {selected_fund_type} ({fund_type})")
        st.write(f"**Start Date For Computation:** {lwd_prev_month}")

        # Calculate dates before reading the file
        start_date = mfapi_utils.get_last_working_day_for_specific_date(date.today())
        num_dates = 10
        result_dates = mfapi_utils.get_x_previous_yearly_dates(start_date, num_dates)
        dates_chronological = result_dates

        # Get the correct path to the .txt files
        # Get the directory where the current script is located
        current_dir = os.path.dirname(os.path.abspath(__file__))
        mf_codes_file = os.path.join(current_dir, f"{fund_type}.txt")

        try:
            with open(mf_codes_file, 'r') as file:
                ticker_list = [line.strip() for line in file]
                st.success(f"Successfully loaded {len(ticker_list)} fund codes from {mf_codes_file}")
                
                # Calculate returns based on the selected methodology
                if ticker_list and len(dates_chronological) >= 2:
                    results_data = []
                    headers = ["MF Name", "Rank"]

                    if ranking_methodology == "Year-On-Year Consistency Rank":
                        st.write("**Calculating Year-on-Year Returns...**")
                        for i in range(len(dates_chronological) - 1):
                            start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                            headers.append(f"{end_date.strftime('%Y-%m-%d')} to {start_date.strftime('%Y-%m-%d')}")

                        # Create progress bar for mutual fund processing
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        for idx, mf_code in enumerate(tqdm(filter(str.strip, ticker_list), desc="Processing Mutual Funds")):
                            # Update progress bar
                            progress = (idx + 1) / len([x for x in ticker_list if x.strip()])
                            progress_bar.progress(progress)
                            status_text.text(f"Processing {idx + 1}/{len([x for x in ticker_list if x.strip()])}: {mf_code}")
                            
                            try:
                                scheme_details = mfapi_utils.mf.get_scheme_details(mf_code)
                                mf_name = scheme_details['scheme_name'] if isinstance(scheme_details, dict) and 'scheme_name' in scheme_details else mf_code
                            except Exception:
                                mf_name = mf_code
                            
                            row_data = [mf_name]
                            scheme_data = mfapi_utils.get_mf_data_direct(mf_code)
                            if not scheme_data or 'data' not in scheme_data:
                                row_data.extend([""] * (len(dates_chronological) - 1))
                                results_data.append(row_data)
                                continue
                            
                            nav_data = pd.DataFrame(scheme_data['data'])
                            nav_data['date'] = pd.to_datetime(nav_data['date'], format='%d-%m-%Y', dayfirst=True)
                            nav_data['nav'] = pd.to_numeric(nav_data['nav'])
                            nav_data = nav_data.sort_values('date')

                            # Exclude funds with less than 5 years of history
                            if (nav_data['date'].max() - nav_data['date'].min()).days < 1825:
                                continue

                            for i in range(len(dates_chronological) - 1):
                                start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                                start_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(start_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(start_date)]) > 0 else None
                                end_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(end_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(end_date)]) > 0 else None
                                
                                if start_nav_row is not None and end_nav_row is not None:
                                    yoy_return = ((end_nav_row['nav'] - start_nav_row['nav']) / start_nav_row['nav']) * 100
                                    row_data.append(f"{yoy_return:.2f}%")
                                else:
                                    row_data.append("")
                            results_data.append(row_data)
                        
                        # Clear progress indicators
                        progress_bar.empty()
                        status_text.empty()

                    elif ranking_methodology == "xYears Performance Rank":
                        st.write("**Calculating Rolling Returns...**")
                        for i in range(1, len(dates_chronological)):
                            headers.append(f"{i}-Year Return")
                        
                        end_date = dates_chronological[0]
                        
                        # Create progress bar for mutual fund processing
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        for idx, mf_code in enumerate(tqdm(filter(str.strip, ticker_list), desc="Processing Mutual Funds")):
                            # Update progress bar
                            progress = (idx + 1) / len([x for x in ticker_list if x.strip()])
                            progress_bar.progress(progress)
                            status_text.text(f"Processing {idx + 1}/{len([x for x in ticker_list if x.strip()])}: {mf_code}")
                            
                            try:
                                scheme_details = mfapi_utils.mf.get_scheme_details(mf_code)
                                mf_name = scheme_details['scheme_name'] if isinstance(scheme_details, dict) and 'scheme_name' in scheme_details else mf_code
                            except Exception:
                                mf_name = mf_code

                            row_data = [mf_name]
                            scheme_data = mfapi_utils.get_mf_data_direct(mf_code)
                            if not scheme_data or 'data' not in scheme_data:
                                row_data.extend([""] * (len(dates_chronological) - 1))
                                results_data.append(row_data)
                                continue

                            nav_data = pd.DataFrame(scheme_data['data'])
                            nav_data['date'] = pd.to_datetime(nav_data['date'], format='%d-%m-%Y', dayfirst=True)
                            nav_data['nav'] = pd.to_numeric(nav_data['nav'])
                            nav_data = nav_data.sort_values('date')

                            # Exclude funds with less than 5 years of history
                            if (nav_data['date'].max() - nav_data['date'].min()).days < 1825:
                                continue

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
                                        row_data.append("")
                                else:
                                    row_data.append("")
                            results_data.append(row_data)
                        
                        # Clear progress indicators
                        progress_bar.empty()
                        status_text.empty()

                    elif ranking_methodology == "Benchmark Outperformance Rank":
                        st.write("**Calculating Benchmark Outperformance (Year-on-Year)...**")
                        st.info("🔍 **Filtering Criteria:** Funds with >5 years history and >2000 crores AUM")
                        
                        period_headers = []
                        for i in range(len(dates_chronological) - 1):
                            start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                            period_headers.append(f"{end_date.strftime('%Y-%m-%d')} to {start_date.strftime('%Y-%m-%d')}")
                        
                        # Get benchmark returns for all periods
                        benchmark_info = mfapi_utils.BENCHMARK_MAPPING[fund_type]
                        st.info(f"📊 **Benchmark:** {benchmark_info['name']} (Code: {benchmark_info['symbol']})")
                        benchmark_returns = mfapi_utils.calculate_benchmark_returns(
                            benchmark_info["symbol"], 
                            dates_chronological, 
                            ranking_methodology
                        )
                        st.info(f"📊 **Benchmark Returns:** {benchmark_returns}")
                        
                        # After benchmark_returns is calculated in Benchmark Outperformance Rank section
                        print(f"Benchmark returns for {benchmark_info['symbol']}: {benchmark_returns}")
                        
                        # Create progress bar for mutual fund processing
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        fund_returns = []
                        fund_names = []
                        processed_count = 0
                        filtered_count = 0
                        for idx, mf_code in enumerate(tqdm(filter(str.strip, ticker_list), desc="Processing Mutual Funds")):
                            # Update progress bar
                            progress = (idx + 1) / len([x for x in ticker_list if x.strip()])
                            progress_bar.progress(progress)
                            status_text.text(f"Processing {idx + 1}/{len([x for x in ticker_list if x.strip()])}: {mf_code}")
                            
                            try:
                                scheme_details = mfapi_utils.mf.get_scheme_details(mf_code)
                                mf_name = scheme_details['scheme_name'] if isinstance(scheme_details, dict) and 'scheme_name' in scheme_details else mf_code
                            except Exception:
                                mf_name = mf_code
                            
                            scheme_data = mfapi_utils.get_mf_data_direct(mf_code)
                            if not scheme_data or 'data' not in scheme_data:
                                continue
                            
                            nav_data = pd.DataFrame(scheme_data['data'])
                            nav_data['date'] = pd.to_datetime(nav_data['date'], format='%d-%m-%Y', dayfirst=True)
                            nav_data['nav'] = pd.to_numeric(nav_data['nav'])
                            nav_data = nav_data.sort_values('date')

                            processed_count += 1

                            row_returns = []
                            for i in range(len(dates_chronological) - 1):
                                start_date, end_date = dates_chronological[i + 1], dates_chronological[i]
                                start_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(start_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(start_date)]) > 0 else None
                                end_nav_row = nav_data[nav_data['date'] <= pd.to_datetime(end_date)].iloc[-1] if len(nav_data[nav_data['date'] <= pd.to_datetime(end_date)]) > 0 else None
                                
                                if start_nav_row is not None and end_nav_row is not None:
                                    yoy_return = ((end_nav_row['nav'] - start_nav_row['nav']) / start_nav_row['nav']) * 100
                                    row_returns.append(f"{yoy_return:.2f}")
                                else:
                                    row_returns.append("")
                            fund_returns.append(row_returns)
                            fund_names.append(mf_name)
                            # Print fund name and year-by-year comparison with benchmark in Streamlit
                            st.write(f"**{mf_name} vs Benchmark**")
                            for i, period in enumerate(period_headers):
                                fund_ret = row_returns[i] if i < len(row_returns) else ""
                                bench_ret = benchmark_returns[i] if i < len(benchmark_returns) else ""
                                st.write(f"{period}: Fund = {fund_ret}, Benchmark = {bench_ret}")
                        # Clear progress indicators
                        progress_bar.empty()
                        status_text.empty()
                        
                        # Show processing summary
                        st.info(f"📊 **Processing Summary:** {processed_count} funds processed, {filtered_count} funds filtered out")
                        
                        # Build the outperformance table
                        df_processed = mfapi_utils.calculate_benchmark_outperformance_table(
                            fund_returns, benchmark_returns, fund_names, period_headers
                        )
                        st.write(f"**{ranking_methodology} Table:**")
                        st.write("🏆 **Funds are ranked purely by number of years outperformed**")
                        st.dataframe(df_processed, use_container_width=True, hide_index=True)
                    

                    
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
                            st.dataframe(df_processed, use_container_width=True, hide_index=True)
                        elif ranking_methodology == "xYears Performance Rank":
                            df_processed = mfapi_utils.calculate_rolling_period_returns(df_results.copy(), dates_chronological, ranking_methodology)
                            st.write(f"**{ranking_methodology} Table:**")
                            st.dataframe(df_processed, use_container_width=True, hide_index=True)
                            # Future "elif" blocks can go here for other methodologies
                        
                    else:
                        st.warning("No valid mutual fund codes found for calculation.")
        except FileNotFoundError:
            st.error(f"Error: The file '{mf_codes_file}' was not found.")
            st.error(f"Current working directory: {os.getcwd()}")
            st.error(f"Script directory: {current_dir}")
            st.error(f"Attempted file path: {mf_codes_file}")
        except Exception as e:
            st.error(f"An error occurred: {e}")

