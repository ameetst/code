import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import warnings
import os
import config_manager

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
def get_past_trading_date(lookback_days):
    if lookback_days == 0:
        return "the latest close"
    try:
        hist = yf.Ticker('^NSEI').history(period='2mo')
        if not hist.empty and len(hist) > lookback_days:
            target_date = hist.index[-(lookback_days + 1)].strftime("%b %d, %Y")
            return target_date
    except Exception:
        pass
    return "an unknown date"

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_data(tickers, start_date, end_date):
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

@st.cache_data(ttl=600, show_spinner=False)
def get_live_data(tickers):
    if not tickers:
        return pd.DataFrame()
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=1095)
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

@st.cache_data(ttl=3600, show_spinner=False)
def check_daily_signals(tickers, threshold, max_lookback, enable_p52h=True, enable_lookback=True, enable_liquidity=True):
    if not tickers:
        return []

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=1095) 

    with st.spinner(f"Downloading 3-year data for {len(tickers)} tickers... This might take 20-30 seconds."):
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

            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
            
            # Traded Value
            df['Traded_Value'] = df['Close'] * df['Volume']
            df['Median_TV_21'] = df['Traded_Value'].rolling(window=21).median()
            
            high_52w = df['High'].max()
            high_52w_in_last_63d = (high_52w == df['High'].tail(63).max())
            df['Momentum_63'] = df['Close'] / df['Close'].shift(63) - 1

            df['Signal'] = 0
            df.loc[df['EMA_50'] > df['EMA_200'], 'Signal'] = 1
            df['Position'] = df['Signal'].diff()

            latest_data = df.iloc[-1]
            latest_close = latest_data['Close']
            latest_ema_200 = latest_data['EMA_200']
            latest_momentum_63 = latest_data['Momentum_63']
            
            pos_1_indices = np.where(df['Position'] == 1)[0]
            if len(pos_1_indices) == 0:
                continue
                
            last_cross_iloc = pos_1_indices[-1]
            days_since_crossover = (len(df) - 1) - last_cross_iloc
            close_at_crossover = df.iloc[last_cross_iloc]['Close']
            
            p_52h = latest_close / high_52w if high_52w > 0 else 0
            
            cond_lookback = (0 <= days_since_crossover <= max_lookback) if enable_lookback else True
            cond_p52h = (p_52h >= threshold) if enable_p52h else True
            cond_liquidity = (latest_data['Median_TV_21'] > 10_000_000) if enable_liquidity else True
            
            if (cond_lookback and 
                latest_data['EMA_50'] > latest_data['EMA_200'] and
                cond_p52h and 
                latest_close > latest_ema_200 and
                cond_liquidity):
                
                returns_since_xover = ((latest_close - close_at_crossover) / close_at_crossover) * 100 if close_at_crossover > 0 else 0
                
                actionable_signals.append({
                    'Ticker': str(ticker.replace('.NS', '')), 
                    'Signal': 'BUY', 
                    'Days Since Cross': int(days_since_crossover),
                    'Close Price': float(latest_close),
                    'ADTV (Cr)': float(latest_data['Median_TV_21'] / 10_000_000),
                    '52W High': float(high_52w),
                    'P/52H (%)': float(p_52h * 100),
                    'Returns Since Xover (%)': float(returns_since_xover)
                })
                
        except Exception as e:
            continue
            
    my_bar.empty()
    return actionable_signals

def main():
    try:
        st.set_page_config(page_title="Daily Market Screener & Portfolio", layout="wide")
    except Exception:
        pass
        
    config = config_manager.load_config()
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("🚀 EMA Crossover System")
        st.markdown("This screener finds stocks with a **50/200 EMA Crossover** today, filtered by their proximity to the **52-week high**.")
    with col2:
        st.write("")
        st.write("")
        if st.button("Run Screener", type="primary", use_container_width=True):
            st.session_state['crossover_has_run'] = True
    
    tab1, tab2, tab3 = st.tabs(["Actionable Signals (Screener)", "Trade Journal & Portfolio", "Configuration"])
    
    with tab3:
        st.header("Screener Configuration")
        
        enable_p52h = st.checkbox("Enable Min P/52W High Filter", value=config["crossover_enable_p52h"])
        threshold = st.slider(
            "Min P/52W High Ratio", 
            min_value=0.50, 
            max_value=0.99, 
            value=float(config["crossover_threshold"]), 
            step=0.01,
            help="For example, 0.75 means the stock's current price is at least 75% of its 52-week high.",
            disabled=not enable_p52h
        )
        
        enable_lookback = st.checkbox("Enable Lookback Window Filter", value=config["crossover_enable_lookback"])
        max_lookback = st.slider(
            "Lookback Window (Days Since Crossover)",
            min_value=0,
            max_value=21,
            value=int(config["crossover_max_lookback"]),
            step=1,
            help="Find stocks that crossed over within this many trading days. Set to 0 to only find exact crossovers from yesterday.",
            disabled=not enable_lookback
        )
        
        enable_liquidity = st.checkbox("Enable Liquidity Filter (> 1 Cr Daily Traded Value)", value=config["crossover_enable_liquidity"])
        
        st.write("")
        if st.button("Save as Default Configuration"):
            config_manager.save_config({
                "crossover_enable_p52h": enable_p52h,
                "crossover_threshold": threshold,
                "crossover_enable_lookback": enable_lookback,
                "crossover_max_lookback": max_lookback,
                "crossover_enable_liquidity": enable_liquidity
            })
            st.success("Configuration saved! These settings will load automatically next time.")
        
        if enable_lookback:
            past_date = get_past_trading_date(max_lookback)
            if max_lookback == 0:
                st.caption(f"💡 Searching for exact crossovers based on **{past_date}**.")
            else:
                st.caption(f"💡 Searching for crossovers from today all the way back to **{past_date}**.")
        else:
            st.caption("💡 Searching for ANY active crossover (50 > 200 EMA) regardless of when it happened.")
            
        with st.expander("Update Universe File"):
            with st.form("universe_upload"):
                uploaded_file = st.file_uploader("Upload CSV", type=['csv'])
                if st.form_submit_button("Save"):
                    try:
                        with open("tickers.csv", "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        st.success("Successfully updated `tickers.csv`! You can now run the screener with the new universe.")
                    except Exception as e:
                        st.error(f"Error saving file: {e}")
    
    with tab1:
        if st.session_state.get('crossover_has_run', False):
            tickers_list = get_tickers_from_csv(file_path="tickers.csv")
            if tickers_list:
                signals = check_daily_signals(tickers_list, threshold, max_lookback, enable_p52h, enable_lookback, enable_liquidity)
                st.session_state['latest_crossover_signals'] = signals
                
                if signals:
                    st.success(f"Found {len(signals)} actionable signals today!")
                    
                    # Sort signals by Days Since Cross then by P/52H (%)
                    signals = sorted(signals, key=lambda x: (x['Days Since Cross'], -x['P/52H (%)']))
                    
                    df_signals = pd.DataFrame(signals)
                    
                    trade_log = load_trade_log()
                    open_tickers = [t.replace('.NS', '') for t in trade_log[trade_log['Status'] == 'Open']['Ticker'].tolist()] if not trade_log.empty else []
                    
                    def highlight_owned(row):
                        if row['Ticker'] in open_tickers:
                            # Light yellow background for light/dark mode compatibility
                            return ['background-color: rgba(255, 255, 153, 0.4);'] * len(row)
                        else:
                            return [''] * len(row)
                            
                    styled_df = df_signals.style.apply(highlight_owned, axis=1).format({
                        'Close Price': '{:.2f}',
                        'ADTV (Cr)': '{:.2f}',
                        '52W High': '{:.2f}',
                        'P/52H (%)': '{:.1f}%',
                        'Returns Since Xover (%)': '{:.1f}%'
                    })
                    
                    st.dataframe(
                        styled_df,
                        use_container_width=True,
                        hide_index=True
                    )
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
        stat_col1, stat_col2, stat_col3, stat_col4, stat_col5 = st.columns(5)
        
        active_trades_count = len(open_trades)
        total_invested = 0.0
        if not open_trades.empty:
            total_invested = (pd.to_numeric(open_trades['Entry Price']) * pd.to_numeric(open_trades['Quantity'])).sum()
        
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
                
        stat_col1.metric("Active Trades", f"{active_trades_count}")
        stat_col2.metric("Capital Deployed", f"₹{total_invested:,.2f}")
        stat_col3.metric("Realized P&L", f"₹{total_realized_pnl:,.2f}")
        stat_col4.metric("Win Rate", f"{win_rate:.1f}%")
        stat_col5.metric("Avg Win", f"{avg_win:.1f}%")
        
        st.divider()
        
        # --- 2. OPEN POSITIONS ---
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
                                status_list.append({"Current Price": 0, "P&L (%)": 0, "50 EMA": 0, "200 EMA": 0, "Status": "No Data"})
                                continue
                                
                            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
                            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
                            latest = df.iloc[-1]
                            curr_price = latest['Close']
                            
                            pnl_pct = ((curr_price - row['Entry Price']) / row['Entry Price']) * 100
                            
                            status = "🟢 HOLD"
                            if latest['EMA_50'] < latest['EMA_200']:
                                status = "🔴 SELL (Trend Broken)"
                                
                            status_list.append({
                                "Current Price": round(curr_price, 2), "P&L (%)": round(pnl_pct, 2),
                                "50 EMA": round(latest['EMA_50'], 2), "200 EMA": round(latest['EMA_200'], 2),
                                "Status": status
                            })
                        except Exception:
                            status_list.append({"Current Price": 0, "P&L (%)": 0, "50 EMA": 0, "200 EMA": 0, "Status": "Error"})
                            
                    display_df = open_trades[['Ticker', 'Buy Date', 'Quantity', 'Entry Price']].copy()
                    status_df = pd.DataFrame(status_list, index=display_df.index)
                    for col in status_df.columns:
                        display_df[col] = status_df[col]
                    
                    st.dataframe(display_df.astype(str), use_container_width=True, hide_index=True)
            else:
                display_df = open_trades[['Ticker', 'Buy Date', 'Quantity', 'Entry Price']].copy()
                display_df['Current Price'] = "-"
                display_df['P&L (%)'] = "-"
                display_df['50 EMA'] = "-"
                display_df['200 EMA'] = "-"
                display_df['Status'] = "Pending Refresh"
                st.dataframe(display_df.astype(str), use_container_width=True, hide_index=True)
                
        else:
            st.info("No open positions.")
            
        st.divider()

        # --- 3. ADD / CLOSE TRADES ---
        action_col1, action_col2 = st.columns(2)
        
        with action_col1:
            with st.expander("➕ Open New Trade", expanded=False):
                # Load tickers for autocomplete
                all_t = get_tickers_from_csv("tickers.csv")
                all_tickers_clean = sorted([t.replace('.NS', '') for t in all_t]) if all_t else []
                
                new_ticker = st.selectbox("Ticker Symbol", options=[""] + all_tickers_clean, help="Start typing to search available tickers", key="new_trade_ticker")
                
                default_price = 0.0
                if new_ticker:
                    signals = st.session_state.get('latest_crossover_signals', [])
                    for s in signals:
                        if s['Ticker'] == new_ticker:
                            default_price = float(s['Close Price'])
                            break
                            
                new_price = st.number_input("Entry Price (INR)", min_value=0.0, value=default_price, format="%.2f", key="new_trade_price")
                new_qty = st.number_input("Quantity", min_value=1, step=1, key="new_trade_qty")
                new_date = st.date_input("Buy Date", key="new_trade_date")
                
                if st.button("Add to Journal", type="primary"):
                        if not new_ticker or str(new_ticker).strip() == "":
                            st.error("Please select a Ticker Symbol.")
                        elif new_price <= 0:
                            st.error("Please enter a valid Entry Price greater than 0.")
                        elif new_qty <= 0:
                            st.error("Please enter a valid Quantity greater than 0.")
                        elif not new_date:
                            st.error("Please select a Buy Date.")
                        else:
                            t_str = str(new_ticker).upper().strip()
                            if not t_str.endswith(".NS"): t_str += ".NS"
                            new_trade = pd.DataFrame({
                                "Ticker": [t_str], "Entry Price": [new_price], "Quantity": [new_qty],
                                "Buy Date": [new_date.strftime("%Y-%m-%d")], "Status": ["Open"],
                                "Exit Price": [0.0], "Exit Date": [""]
                            })
                            trade_log = pd.concat([trade_log, new_trade], ignore_index=True)
                            save_trade_log(trade_log)
                            st.success(f"Added {t_str}!")
                            
                            # Clear form fields
                            for k in ["new_trade_ticker", "new_trade_price", "new_trade_qty", "new_trade_date"]:
                                if k in st.session_state:
                                    del st.session_state[k]
                                    
                            st.rerun()
                            
        with action_col2:
            with st.expander("➖ Close Open Trade", expanded=False):
                if not open_trades.empty:
                    with st.form("close_trade_form", clear_on_submit=True):
                        options = open_trades.index.tolist()
                        def format_trade(idx):
                            row = open_trades.loc[idx]
                            return f"{row['Ticker']} (Qty: {row['Quantity']} @ {row['Entry Price']})"
                            
                        selected_option = st.selectbox("Select Trade to Close", options, format_func=format_trade)
                        exit_price = st.number_input("Exit Price (INR)", min_value=0.0, format="%.2f")
                        exit_date = st.date_input("Exit Date")
                        
                        if st.form_submit_button("Close Trade"):
                            if selected_option is not None and exit_price > 0:
                                idx_to_close = selected_option
                                trade_log.at[idx_to_close, 'Status'] = 'Closed'
                                trade_log.at[idx_to_close, 'Exit Price'] = float(exit_price)
                                trade_log.at[idx_to_close, 'Exit Date'] = exit_date.strftime("%Y-%m-%d")
                                save_trade_log(trade_log)
                                st.success("Trade closed successfully!")
                                st.rerun()
                else:
                    st.info("No open trades available to close.")
            
        st.write("")
        if not open_trades.empty:
            with st.expander("❌ Delete an Open Trade", expanded=False):
                with st.form("delete_trade_form", clear_on_submit=True):
                    options = open_trades.index.tolist()
                    def format_trade(idx):
                        row = open_trades.loc[idx]
                        return f"{row['Ticker']} (Qty: {row['Quantity']} @ {row['Entry Price']})"
                        
                    selected_del_option = st.selectbox("Select Open Trade to Delete (This permanently removes it from the journal)", options, format_func=format_trade)
                    if st.form_submit_button("Delete Trade"):
                        if selected_del_option is not None:
                            idx_to_delete = selected_del_option
                            trade_log = trade_log.drop(index=idx_to_delete).reset_index(drop=True)
                            save_trade_log(trade_log)
                            st.success("Trade deleted successfully!")
                            st.rerun()

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