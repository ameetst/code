import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import warnings
import os

warnings.filterwarnings('ignore')

TRADE_LOG_FILE = "trade_log.csv"

def load_trade_log():
    if os.path.exists(TRADE_LOG_FILE):
        df = pd.read_csv(TRADE_LOG_FILE)
        # Migration: ensure new columns exist for older trade logs
        if 'Status' not in df.columns:
            df['Status'] = 'Open'
        if 'Exit Price' not in df.columns:
            df['Exit Price'] = 0.0
        if 'Exit Date' not in df.columns:
            df['Exit Date'] = ""
        return df
    else:
        return pd.DataFrame(columns=["Ticker", "Entry Price", "Quantity", "Buy Date", "Status", "Exit Price", "Exit Date"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG_FILE, index=False)

def get_tickers_from_csv(file_path="tickers.csv"):
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
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

@st.cache_data(ttl=600, show_spinner=False)
def get_live_data(tickers):
    if not tickers:
        return pd.DataFrame()
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=200)
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

def check_daily_signals(tickers, threshold):
    if not tickers:
        st.warning("No tickers to process.")
        return []

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=365) 

    with st.spinner(f"Downloading 1-year data for {len(tickers)} tickers... This might take 20-30 seconds."):
        data = fetch_data(tickers, start_date, end_date)
    
    actionable_signals = []
    progress_text = "Processing signals & calculating 52-week highs..."
    my_bar = st.progress(0, text=progress_text)
    
    for idx, ticker in enumerate(tickers):
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
            
            if df.empty or len(df) < 200:
                continue

            df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
            df['EMA_63'] = df['Close'].ewm(span=63, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
            
            # Traded Value
            df['Traded_Value'] = df['Close'] * df['Volume']
            df['Median_TV_21'] = df['Traded_Value'].rolling(window=21).median()
            
            high_52w = df['High'].max()
            high_52w_in_last_63d = (high_52w == df['High'].tail(63).max())
            df['Momentum_63'] = df['Close'] / df['Close'].shift(63) - 1

            df['Signal'] = 0
            df.loc[df['EMA_21'] > df['EMA_63'], 'Signal'] = 1
            df['Position'] = df['Signal'].diff()

            latest_data = df.iloc[-1]
            latest_position = latest_data['Position']
            latest_close = latest_data['Close']
            latest_ema_200 = latest_data['EMA_200']
            latest_momentum_63 = latest_data['Momentum_63']
            
            p_52h = latest_close / high_52w if high_52w > 0 else 0
            
            if (latest_position == 1 and 
                p_52h >= threshold and 
                latest_close > latest_ema_200 and 
                latest_momentum_63 > 0 and 
                high_52w_in_last_63d and
                latest_data['Median_TV_21'] > 10_000_000):
                actionable_signals.append({
                    'Ticker': ticker.replace('.NS', ''), 
                    'Signal': 'BUY', 
                    'Close Price': round(latest_close, 2),
                    'ADTV (Cr)': round(latest_data['Median_TV_21'] / 10_000_000, 2),
                    '52W High': round(high_52w, 2),
                    'P/52H': round(p_52h, 3)
                })
                
        except Exception as e:
            continue
            
    my_bar.empty()
    return actionable_signals

def main():
    st.set_page_config(page_title="Daily Market Screener & Portfolio", layout="wide")
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("📈 EMA Crossover System")
        st.markdown("This screener finds stocks with a **21/63 EMA Crossover** today, filtered by their proximity to the **52-week high**.")
    with col2:
        st.write("")
        st.write("")
        run_screener_btn = st.button("Run Screener", type="primary", use_container_width=True)
    
    tab1, tab2, tab3 = st.tabs(["Actionable Signals (Screener)", "Trade Journal & Portfolio", "Configuration"])
    
    with tab3:
        st.header("Screener Configuration")
        threshold = st.slider(
            "Min P/52W High Ratio", 
            min_value=0.50, 
            max_value=1.00, 
            value=0.75, 
            step=0.01
        )
    
    with tab1:
        if run_screener_btn:
            tickers_list = get_tickers_from_csv(file_path="tickers.csv")
            if tickers_list:
                signals = check_daily_signals(tickers_list, threshold)
                
                st.subheader(f"Actionable Signals Today (P/52H >= {threshold:.2f})")
                if signals:
                    results_df = pd.DataFrame(signals)
                    results_df = results_df.sort_values(by='P/52H', ascending=False).reset_index(drop=True)
                    st.dataframe(results_df.astype(str), use_container_width=True, hide_index=True)
                    st.success(f"Found {len(signals)} actionable BUY signals matching your criteria!")
                else:
                    st.info("No new BUY signals generated today that meet the filter criteria.")
        else:
            st.info("Click 'Run Screener' at the top to generate today's signals.")
                    
    with tab2:
        st.header("Trade Journal & Portfolio")
        trade_log = load_trade_log()
        open_trades = trade_log[trade_log['Status'] == 'Open'].copy()
        closed_trades = trade_log[trade_log['Status'] == 'Closed'].copy()
        
        # --- 1. PORTFOLIO STATS ---
        st.markdown("### Portfolio Statistics")
        stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
        
        total_realized_pnl = 0.0
        win_rate = 0.0
        avg_win = 0.0
        max_drawdown = 0.0
        
        if not closed_trades.empty:
            closed_trades['Realized INR'] = (pd.to_numeric(closed_trades['Exit Price']) - pd.to_numeric(closed_trades['Entry Price'])) * pd.to_numeric(closed_trades['Quantity'])
            closed_trades['Realized %'] = ((pd.to_numeric(closed_trades['Exit Price']) - pd.to_numeric(closed_trades['Entry Price'])) / pd.to_numeric(closed_trades['Entry Price'])) * 100
            
            total_realized_pnl = closed_trades['Realized INR'].sum()
            winning_trades = closed_trades[closed_trades['Realized INR'] > 0]
            if len(closed_trades) > 0:
                win_rate = (len(winning_trades) / len(closed_trades)) * 100
            if len(winning_trades) > 0:
                avg_win = winning_trades['Realized %'].mean()
                
            # Calculate Max Drawdown from Cumulative Realized PnL Peak
            closed_trades_sorted = closed_trades.sort_values(by="Exit Date")
            cumulative_pnl = closed_trades_sorted['Realized INR'].cumsum()
            peak = cumulative_pnl.cummax()
            drawdown = peak - cumulative_pnl
            if len(drawdown) > 0:
                max_drawdown = drawdown.max()
                
        stat_col1.metric("Total Realized P&L", f"₹{total_realized_pnl:,.2f}")
        stat_col2.metric("Win Rate (Closed Trades)", f"{win_rate:.1f}%")
        stat_col3.metric("Avg Win (%)", f"{avg_win:.1f}%")
        stat_col4.metric("Max Drawdown (Realized)", f"₹{max_drawdown:,.2f}")
        
        st.divider()
        
        # --- 2. ADD / CLOSE TRADES ---
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            with st.expander("➕ Open New Trade", expanded=False):
                with st.form("add_trade_form", clear_on_submit=True):
                    new_ticker = st.text_input("Ticker Symbol")
                    new_price = st.number_input("Entry Price (INR)", min_value=0.0, format="%.2f")
                    new_qty = st.number_input("Quantity", min_value=1, step=1)
                    new_date = st.date_input("Buy Date")
                    
                    if st.form_submit_button("Add to Journal"):
                        if new_ticker:
                            t_str = new_ticker.upper().strip()
                            if not t_str.endswith(".NS"): t_str += ".NS"
                            new_trade = pd.DataFrame({
                                "Ticker": [t_str], "Entry Price": [new_price], "Quantity": [new_qty],
                                "Buy Date": [new_date.strftime("%Y-%m-%d")], "Status": ["Open"],
                                "Exit Price": [0.0], "Exit Date": [""]
                            })
                            trade_log = pd.concat([trade_log, new_trade], ignore_index=True)
                            save_trade_log(trade_log)
                            st.success(f"Added {t_str}!")
                            st.rerun()
                            
        with action_col2:
            with st.expander("➖ Close Open Trade", expanded=False):
                if not open_trades.empty:
                    with st.form("close_trade_form", clear_on_submit=True):
                        # Use index to uniquely identify trades in case of multiple entries for same ticker
                        options = [f"Idx {idx}: {row['Ticker']} (Qty: {row['Quantity']} @ {row['Entry Price']})" for idx, row in open_trades.iterrows()]
                        selected_option = st.selectbox("Select Trade to Close", options)
                        exit_price = st.number_input("Exit Price (INR)", min_value=0.0, format="%.2f")
                        exit_date = st.date_input("Exit Date")
                        
                        if st.form_submit_button("Close Trade"):
                            if selected_option and exit_price > 0:
                                idx_to_close = int(selected_option.split(":")[0].replace("Idx ", ""))
                                trade_log.at[idx_to_close, 'Status'] = 'Closed'
                                trade_log.at[idx_to_close, 'Exit Price'] = float(exit_price)
                                trade_log.at[idx_to_close, 'Exit Date'] = exit_date.strftime("%Y-%m-%d")
                                save_trade_log(trade_log)
                                st.success("Trade closed successfully!")
                                st.rerun()
                else:
                    st.info("No open trades available to close.")
        
        # --- 3. OPEN POSITIONS ---
        st.markdown("### Open Positions (Live Monitor)")
        if not open_trades.empty:
            if st.button("🔄 Refresh Live Market Data & Check Exit Signals"):
                with st.spinner("Fetching latest data..."):
                    tickers_to_fetch = open_trades["Ticker"].unique().tolist()
                    live_data = get_live_data(tickers_to_fetch)
                    
                    status_list = []
                    for idx, row in open_trades.iterrows():
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
                            
                            pnl_pct = ((curr_price - row['Entry Price']) / row['Entry Price']) * 100
                            
                            status = "🟢 HOLD"
                            if latest['EMA_21'] < latest['EMA_63']:
                                status = "🔴 SELL (Trend Broken)"
                                
                            status_list.append({
                                "Current Price": round(curr_price, 2), "P&L (%)": round(pnl_pct, 2),
                                "21 EMA": round(latest['EMA_21'], 2), "63 EMA": round(latest['EMA_63'], 2),
                                "Status": status
                            })
                        except Exception:
                            status_list.append({"Current Price": 0, "P&L (%)": 0, "21 EMA": 0, "63 EMA": 0, "Status": "Error"})
                            
                    display_df = open_trades[['Ticker', 'Buy Date', 'Quantity', 'Entry Price']].copy()
                    status_df = pd.DataFrame(status_list, index=display_df.index)
                    for col in status_df.columns:
                        display_df[col] = status_df[col]
                    
                    st.dataframe(display_df.astype(str), use_container_width=True, hide_index=True)
            else:
                st.dataframe(open_trades[['Ticker', 'Buy Date', 'Quantity', 'Entry Price']].astype(str), use_container_width=True, hide_index=True)
        else:
            st.info("No open positions.")
            
        st.divider()
        
        # --- 4. CLOSED TRADES HISTORY ---
        st.markdown("### Closed Trades History")
        if not closed_trades.empty:
            display_closed = closed_trades[['Ticker', 'Buy Date', 'Entry Price', 'Quantity', 'Exit Date', 'Exit Price', 'Realized %', 'Realized INR']].copy()
            # Format nicely
            display_closed['Entry Price'] = display_closed['Entry Price'].apply(lambda x: f"₹{x:,.2f}")
            display_closed['Exit Price'] = display_closed['Exit Price'].apply(lambda x: f"₹{x:,.2f}")
            display_closed['Realized %'] = display_closed['Realized %'].apply(lambda x: f"{x:.2f}%")
            display_closed['Realized INR'] = display_closed['Realized INR'].apply(lambda x: f"₹{x:,.2f}")
            
            st.dataframe(display_closed.astype(str), use_container_width=True, hide_index=True)
            
            # Simple row deletion/editing for mistakes in history
            with st.expander("🛠️ Advanced: Edit Raw Journal Data"):
                st.markdown("*(To manually edit mistakes or delete rows, use the raw data editor below. Check the box on the far left of a row and press Delete to remove it completely!)*")
                edited_df = st.data_editor(trade_log.astype(str), num_rows="dynamic", use_container_width=True)
                if not edited_df.equals(trade_log.astype(str)):
                    try:
                        valid_edits = edited_df[edited_df['Ticker'].str.strip() != ""]
                        parsed_df = valid_edits.copy()
                        parsed_df['Entry Price'] = pd.to_numeric(parsed_df['Entry Price'], errors='coerce').fillna(0.0)
                        parsed_df['Quantity'] = pd.to_numeric(parsed_df['Quantity'], errors='coerce').fillna(0).astype(int)
                        parsed_df['Exit Price'] = pd.to_numeric(parsed_df['Exit Price'], errors='coerce').fillna(0.0)
                        save_trade_log(parsed_df)
                        st.success("Log manually updated!")
                        st.rerun()
                    except Exception:
                        st.error("Error saving edits.")
        else:
            st.info("No closed trades yet. Check back when you secure some profits!")

if __name__ == "__main__":
    main()