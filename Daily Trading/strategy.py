import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import warnings
import os

# Suppress yfinance warnings about failed downloads
warnings.filterwarnings('ignore')

TRADE_LOG_FILE = "trade_log.csv"

def load_trade_log():
    if os.path.exists(TRADE_LOG_FILE):
        return pd.read_csv(TRADE_LOG_FILE)
    else:
        return pd.DataFrame(columns=["Ticker", "Entry Price", "Quantity", "Buy Date"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG_FILE, index=False)

def get_tickers_from_csv(file_path="tickers.csv"):
    """Reads tickers from a CSV file and appends .NS for Yahoo Finance India."""
    try:
        df = pd.read_csv(file_path)
        if 'TICKER' in df.columns:
            tickers = df['TICKER'].dropna().astype(str).tolist()
            return [f"{t.strip()}.NS" for t in tickers if t.strip()]
        else:
            st.error(f"Error: 'TICKER' column not found in {file_path}")
            return []
    except Exception as e:
        st.error(f"Error reading {file_path}: {e}")
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_data(tickers, start_date, end_date):
    """
    Fetches data using yfinance. 
    Decorated with st.cache_data so tweaking the slider doesn't trigger a re-download!
    Caches the result for 1 hour (3600 seconds).
    """
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

def check_daily_signals(tickers, threshold):
    """Evaluates the EMA crossover signal and 52-week high filter."""
    if not tickers:
        st.warning("No tickers to process.")
        return []

    # Calculate date range (need 1 year of history for the 52-week high)
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=365) 

    with st.spinner(f"Downloading 1-year data for {len(tickers)} tickers... This might take 20-30 seconds."):
        data = fetch_data(tickers, start_date, end_date)
    
    actionable_signals = []

    progress_text = "Processing signals & calculating 52-week highs..."
    my_bar = st.progress(0, text=progress_text)
    
    for idx, ticker in enumerate(tickers):
        # Update progress bar every 20 tickers
        if idx % 20 == 0:
            my_bar.progress(idx / len(tickers), text=progress_text)
            
        try:
            if len(tickers) == 1:
                df = data.copy()
            else:
                if ticker not in data.columns.levels[0]:
                    continue
                df = data[ticker].copy()
                
            df.dropna(subset=['Close', 'High'], inplace=True)
            
            # Need at least 200 days of data to calculate the 200 EMA
            if df.empty or len(df) < 200:
                continue

            # Calculate the EMAs
            df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
            df['EMA_63'] = df['Close'].ewm(span=63, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

            # Calculate 52-Week High (max high over the past 365 calendar days)
            high_52w = df['High'].max()
            
            # Check if 52-week high occurred in the last 63 trading days
            high_52w_in_last_63d = (high_52w == df['High'].tail(63).max())
            
            # Calculate 63-day Momentum
            df['Momentum_63'] = df['Close'] / df['Close'].shift(63) - 1

            # Generate Trading Signals
            df['Signal'] = 0
            df.loc[df['EMA_21'] > df['EMA_63'], 'Signal'] = 1
            df['Position'] = df['Signal'].diff()

            # Isolate today's data
            latest_data = df.iloc[-1]
            latest_position = latest_data['Position']
            latest_close = latest_data['Close']
            latest_ema_200 = latest_data['EMA_200']
            latest_momentum_63 = latest_data['Momentum_63']
            
            # Calculate P/52H
            p_52h = latest_close / high_52w if high_52w > 0 else 0
            
            # Record if a new Buy signal was triggered, it meets our 52W High threshold, AND price > 200 EMA AND Momentum > 0 AND 52W high in last 63 days
            if (latest_position == 1 and 
                p_52h >= threshold and 
                latest_close > latest_ema_200 and 
                latest_momentum_63 > 0 and 
                high_52w_in_last_63d):
                signal_type = 'BUY'
                actionable_signals.append({
                    'Ticker': ticker.replace('.NS', ''), 
                    'Signal': signal_type, 
                    'Close Price': round(latest_close, 2),
                    '52W High': round(high_52w, 2),
                    'P/52H': round(p_52h, 3)
                })
                
        except Exception as e:
            continue
            
    my_bar.empty() # Clear progress bar when done
    return actionable_signals

def main():
    st.set_page_config(page_title="Daily Market Screener & Trade Log", layout="wide")
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("📈 EMA Crossover System")
        st.markdown("This screener finds stocks with a **21/63 EMA Crossover** today, filtered by their proximity to the **52-week high**.")
    with col2:
        st.write("")
        st.write("")
        run_screener_btn = st.button("Run Screener", type="primary", use_container_width=True)
    
    tab1, tab2, tab3 = st.tabs(["Actionable Signals (Screener)", "Open Trade Log", "Configuration"])
    
    # --- CONFIGURATION TAB ---
    with tab3:
        st.header("Screener Configuration")
        threshold = st.slider(
            "Min P/52W High Ratio", 
            min_value=0.50, 
            max_value=1.00, 
            value=0.75, 
            step=0.01,
            help="Only show stocks where (Current Price / 52 Week High) is greater than this value. 0.75 means within 25% of the high."
        )
    
    # --- SCREENER TAB ---
    with tab1:
        if run_screener_btn:
            tickers_list = get_tickers_from_csv(file_path="tickers.csv")
            if tickers_list:
                signals = check_daily_signals(tickers_list, threshold)
                
                st.subheader(f"Actionable Signals Today (P/52H >= {threshold:.2f})")
                if signals:
                    results_df = pd.DataFrame(signals)
                    results_df = results_df.sort_values(by='P/52H', ascending=False).reset_index(drop=True)
                    results_df = results_df.astype(str)
                    
                    st.dataframe(results_df, use_container_width=True, hide_index=True)
                    st.success(f"Found {len(signals)} actionable BUY signals matching your criteria!")
                else:
                    st.info("No new BUY signals generated today that meet the filter criteria.")
        else:
            st.info("Click 'Run Screener' at the top to generate today's signals.")
                    
    # --- TRADE LOG TAB ---
    with tab2:
        st.header("Trade Log & Position Monitor")
        
        # Load trade log
        trade_log = load_trade_log()
        
        # Add new trade form
        with st.expander("➕ Add New Trade", expanded=False):
            with st.form("add_trade_form", clear_on_submit=True):
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    new_ticker = st.text_input("Ticker Symbol (e.g. RELIANCE)")
                with col2:
                    new_price = st.number_input("Entry Price (INR)", min_value=0.0, format="%.2f")
                with col3:
                    new_qty = st.number_input("Quantity", min_value=1, step=1)
                with col4:
                    new_date = st.date_input("Buy Date")
                    
                submit_btn = st.form_submit_button("Add to Log")
                if submit_btn and new_ticker:
                    ticker_str = new_ticker.upper().strip()
                    if not ticker_str.endswith(".NS"):
                        ticker_str += ".NS"
                        
                    new_trade = pd.DataFrame({
                        "Ticker": [ticker_str],
                        "Entry Price": [new_price],
                        "Quantity": [new_qty],
                        "Buy Date": [new_date.strftime("%Y-%m-%d")]
                    })
                    trade_log = pd.concat([trade_log, new_trade], ignore_index=True)
                    save_trade_log(trade_log)
                    st.success(f"Added {ticker_str} to Trade Log!")
                    st.rerun()
                    
        # Editable Dataframe to allow deletions or manual edits
        if not trade_log.empty:
            st.markdown("### Open Positions")
            
            # Button to refresh live data
            if st.button("🔄 Refresh Live Market Data & Check Exit Signals"):
                with st.spinner("Fetching latest data for open positions..."):
                    tickers_to_fetch = trade_log["Ticker"].tolist()
                    end_date = datetime.date.today()
                    start_date = end_date - datetime.timedelta(days=200) # Need enough for 63 EMA
                    
                    live_data = yf.download(tickers_to_fetch, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)
                    
                    status_list = []
                    
                    for idx, row in trade_log.iterrows():
                        t = row["Ticker"]
                        try:
                            if len(tickers_to_fetch) == 1:
                                df = live_data.copy()
                            else:
                                df = live_data[t].copy()
                                
                            df.dropna(subset=['Close'], inplace=True)
                            
                            if df.empty:
                                status_list.append({"Current Price": 0, "P&L (%)": 0, "21 EMA": 0, "63 EMA": 0, "Status": "No Data"})
                                continue
                                
                            df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
                            df['EMA_63'] = df['Close'].ewm(span=63, adjust=False).mean()
                            
                            latest = df.iloc[-1]
                            curr_price = latest['Close']
                            e21 = latest['EMA_21']
                            e63 = latest['EMA_63']
                            
                            pnl_pct = ((curr_price - row['Entry Price']) / row['Entry Price']) * 100
                            
                            status = "🟢 HOLD"
                            if e21 < e63:
                                status = "🔴 SELL (Trend Broken)"
                                
                            status_list.append({
                                "Current Price": round(curr_price, 2),
                                "P&L (%)": round(pnl_pct, 2),
                                "21 EMA": round(e21, 2),
                                "63 EMA": round(e63, 2),
                                "Status": status
                            })
                        except Exception as e:
                            status_list.append({"Current Price": 0, "P&L (%)": 0, "21 EMA": 0, "63 EMA": 0, "Status": "Error"})
                            
                    # Merge status into display dataframe
                    display_df = trade_log.copy()
                    status_df = pd.DataFrame(status_list)
                    for col in status_df.columns:
                        display_df[col] = status_df[col]
                        
                    # Reorder columns for display
                    display_df = display_df[['Ticker', 'Buy Date', 'Quantity', 'Entry Price', 'Current Price', 'P&L (%)', '21 EMA', '63 EMA', 'Status']]
                    display_df = display_df.astype(str)
                    
                    st.dataframe(display_df, use_container_width=True, hide_index=True)
            
            st.markdown("*(Use the table below to manually edit cells, or select a row on the far left and press Delete/Backspace to remove a closed trade. Changes save automatically!)*")
            edited_df = st.data_editor(trade_log, num_rows="dynamic", use_container_width=True)
            
            if not edited_df.equals(trade_log):
                save_trade_log(edited_df)
                st.success("Trade log updated!")
                st.rerun()
                
        else:
            st.info("Your trade log is empty. Add a new trade above!")

if __name__ == "__main__":
    main()