import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import os
import warnings

warnings.filterwarnings('ignore')

TRADE_LOG_FILE = "milt25_trade_log_live.csv"
UNIVERSE_FILE = "ind_niftytotalmarket_list (3).csv"

def load_trade_log():
    if os.path.exists(TRADE_LOG_FILE):
        df = pd.read_csv(TRADE_LOG_FILE)
        if 'Highest Close' not in df.columns:
            df['Highest Close'] = df['Entry Price']
        return df
    else:
        return pd.DataFrame(columns=["Ticker", "Entry Price", "Quantity", "Buy Date", "Highest Close", "Status", "Exit Price", "Exit Date"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG_FILE, index=False)

def get_tickers_from_csv(file_path):
    try:
        df = pd.read_csv(file_path)
        cols = [c.upper().strip() for c in df.columns]
        df.columns = cols
        
        symbol_col = 'SYMBOL' if 'SYMBOL' in cols else 'TICKER'
        if symbol_col in cols:
            tickers = df[symbol_col].dropna().astype(str).tolist()
            return [f"{t.strip()}.NS" for t in tickers if t.strip()]
        else:
            st.error(f"Error: Symbol/Ticker column not found in {file_path}")
            return []
    except Exception as e:
        st.error(f"Error reading {file_path}: {e}")
        return []

def wilders_smoothing(series, periods):
    res = np.zeros(len(series))
    res[0] = series.iloc[0]
    for i in range(1, len(series)):
        res[i] = res[i-1] + (series.iloc[i] - res[i-1]) / periods
    return pd.Series(res, index=series.index)

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_data(tickers, start_date, end_date):
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

def check_weekly_signals(tickers, open_positions, bb_std=3.7, sma_period=23, atr_mult=1.8, stop_loss_pct=0.20):
    if not tickers:
        return [], []

    end_date = datetime.date.today()
    # 2 years is enough for 52W ROC and all MAs
    start_date = end_date - datetime.timedelta(days=730) 

    with st.spinner(f"Downloading 2-year data for {len(tickers)} tickers... This might take a minute."):
        data = fetch_data(tickers, start_date, end_date)
    
    buy_signals = []
    exit_alerts = []
    
    progress_text = "Calculating weekly indicators & evaluating signals..."
    my_bar = st.progress(0, text=progress_text)
    
    open_tickers = {p['Ticker']: p for p in open_positions}
    
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
                
            df.dropna(subset=['Close', 'High', 'Low'], inplace=True)
            if len(df) < 252:
                continue

            # Resample daily to weekly (Friday)
            df_weekly = df.resample('W-FRI').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
            }).dropna(subset=['Close'])
            
            if len(df_weekly) < 52:
                continue

            # Indicators
            df_weekly['BB_Mid'] = df_weekly['Close'].rolling(window=20).mean()
            df_weekly['BB_Std'] = df_weekly['Close'].rolling(window=20).std()
            df_weekly['BB_Upper'] = df_weekly['BB_Mid'] + (bb_std * df_weekly['BB_Std'])
            df_weekly['Trend_MA'] = df_weekly['Close'].rolling(window=sma_period).mean()
            
            high_low = df_weekly['High'] - df_weekly['Low']
            high_close = np.abs(df_weekly['High'] - df_weekly['Close'].shift())
            low_close = np.abs(df_weekly['Low'] - df_weekly['Close'].shift())
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df_weekly['ATR'] = wilders_smoothing(tr, 14)
            df_weekly['ROC_52W'] = df_weekly['Close'] / df_weekly['Close'].shift(52) - 1
            
            latest_data = df_weekly.iloc[-1]
            latest_close = latest_data['Close']
            
            clean_ticker = str(ticker.replace('.NS', ''))
            
            # Check for BUY signal
            if latest_close > latest_data['BB_Upper'] and pd.notna(latest_data['ROC_52W']):
                buy_signals.append({
                    'Ticker': clean_ticker,
                    'Close Price': f"{latest_close:.2f}",
                    'BB Upper Band': f"{latest_data['BB_Upper']:.2f}",
                    '52W ROC (%)': f"{latest_data['ROC_52W'] * 100:.1f}",
                    '_roc_raw': latest_data['ROC_52W']
                })
                
            # Check for EXIT alert if owned
            if clean_ticker in open_tickers:
                pos = open_tickers[clean_ticker]
                highest_close = max(float(pos['Highest Close']), latest_close)
                
                trailing_stop = highest_close - (atr_mult * latest_data['ATR'])
                hard_stop = float(pos['Entry Price']) * (1 - stop_loss_pct)
                
                exit_reason = None
                if latest_close < hard_stop:
                    exit_reason = "Hard Stop (20%)"
                elif latest_close < latest_data['Trend_MA']:
                    exit_reason = "Trend MA Cross Down"
                elif latest_close < trailing_stop:
                    exit_reason = "ATR Trailing Stop"
                    
                if exit_reason:
                    exit_alerts.append({
                        'Ticker': clean_ticker,
                        'Current Close': f"{latest_close:.2f}",
                        'Exit Threshold': f"{max(hard_stop, trailing_stop, latest_data['Trend_MA']):.2f}",
                        'Reason': exit_reason
                    })
                    
        except Exception as e:
            continue
            
    my_bar.empty()
    
    # Sort buys by ROC
    buy_signals.sort(key=lambda x: x['_roc_raw'], reverse=True)
    # Remove raw key
    for b in buy_signals:
        del b['_roc_raw']
        
    return buy_signals, exit_alerts

def main():
    st.set_page_config(page_title="MILT 25 Screener & Portfolio", layout="wide")
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("🚀 MILT 25 Momentum System")
        st.markdown("This system identifies massive outliers using a **20-week 3.7 StdDev Bollinger Band** breakout and manages them using momentum-based trailing stops.")
    with col2:
        st.write("")
        st.write("")
        if st.button("Run Weekly Screener", type="primary", use_container_width=True):
            st.session_state['run_screener'] = True
            
    st.info("💡 **Best Practice:** Run this screener after the Friday close (over the weekend). The signals are meant to be executed on Monday morning.")
    
    tab1, tab2, tab3 = st.tabs(["Actionable Signals (Screener)", "Trade Journal & Portfolio", "Configuration"])
    
    with tab3:
        st.header("Screener Configuration")
        bb_std = st.slider("Bollinger Band Std Dev", min_value=2.0, max_value=5.0, value=3.7, step=0.1)
        sma_period = st.slider("Trend SMA Period (Weeks)", min_value=10, max_value=50, value=23, step=1)
        atr_mult = st.slider("ATR Trailing Stop Multiplier", min_value=1.0, max_value=3.0, value=1.8, step=0.1)
        stop_loss_pct = st.slider("Initial Hard Stop (%)", min_value=5, max_value=50, value=20, step=1) / 100.0
        
        st.divider()
        st.subheader("Update Tickers List")
        uploaded_file = st.file_uploader("Upload new universe CSV (must have 'SYMBOL' or 'TICKER' column)", type=["csv"])
        if uploaded_file is not None:
            if st.button("Save New Tickers File", type="primary"):
                try:
                    with open(UNIVERSE_FILE, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    st.success(f"Successfully updated `{UNIVERSE_FILE}`!")
                except Exception as e:
                    st.error(f"Error saving file: {e}")
    
    with tab2:
        st.header("Trade Journal & Portfolio")
        trade_log = load_trade_log()
        open_trades = trade_log[trade_log['Status'] == 'Open'].copy()
        closed_trades = trade_log[trade_log['Status'] == 'Closed'].copy()
        
        col_open, col_closed = st.columns(2)
        with col_open:
            st.subheader(f"Open Positions ({len(open_trades)} / 25)")
            if not open_trades.empty:
                st.dataframe(open_trades[["Ticker", "Entry Price", "Quantity", "Buy Date", "Highest Close"]], use_container_width=True, hide_index=True)
            else:
                st.write("No open positions.")
                
        with col_closed:
            st.subheader("Closed Positions")
            if not closed_trades.empty:
                st.dataframe(closed_trades[["Ticker", "Entry Price", "Exit Price", "Quantity", "Buy Date", "Exit Date"]], use_container_width=True, hide_index=True)
            else:
                st.write("No closed positions.")
                
        st.divider()
        st.subheader("Manage Trades")
        mcol1, mcol2, mcol3 = st.columns(3)
        
        with mcol1:
            st.markdown("### 🛒 Add New Trade")
            with st.form("add_trade_form"):
                new_ticker = st.text_input("Ticker (e.g., RELIANCE)")
                new_entry = st.number_input("Entry Price", min_value=0.0, format="%.2f")
                new_qty = st.number_input("Quantity", min_value=1, step=1)
                new_date = st.date_input("Buy Date")
                submitted_add = st.form_submit_button("Add Trade")
                
                if submitted_add and new_ticker:
                    new_row = {
                        "Ticker": new_ticker.upper(),
                        "Entry Price": new_entry,
                        "Quantity": new_qty,
                        "Buy Date": new_date.strftime("%Y-%m-%d"),
                        "Highest Close": new_entry,
                        "Status": "Open",
                        "Exit Price": 0.0,
                        "Exit Date": ""
                    }
                    trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                    save_trade_log(trade_log)
                    st.success(f"Added {new_ticker.upper()} to journal!")
                    st.rerun()
                    
        with mcol2:
            st.markdown("### 📈 Update Highest Close")
            st.caption("Update the highest weekly close to track the ATR trailing stop correctly.")
            if not open_trades.empty:
                with st.form("update_highest_form"):
                    upd_ticker = st.selectbox("Select Ticker", open_trades['Ticker'].tolist())
                    new_highest = st.number_input("New Highest Close", min_value=0.0, format="%.2f")
                    submitted_upd = st.form_submit_button("Update Highest Close")
                    
                    if submitted_upd:
                        idx = trade_log[(trade_log['Ticker'] == upd_ticker) & (trade_log['Status'] == 'Open')].index
                        trade_log.loc[idx, 'Highest Close'] = new_highest
                        save_trade_log(trade_log)
                        st.success(f"Updated {upd_ticker} Highest Close!")
                        st.rerun()
            else:
                st.write("No open trades to update.")
                    
        with mcol3:
            st.markdown("### 🔒 Close Trade")
            if not open_trades.empty:
                with st.form("close_trade_form"):
                    close_ticker = st.selectbox("Select Ticker to Close", open_trades['Ticker'].tolist())
                    exit_price = st.number_input("Exit Price", min_value=0.0, format="%.2f")
                    exit_date = st.date_input("Exit Date")
                    submitted_close = st.form_submit_button("Close Trade")
                    
                    if submitted_close:
                        idx = trade_log[(trade_log['Ticker'] == close_ticker) & (trade_log['Status'] == 'Open')].index
                        trade_log.loc[idx, 'Status'] = 'Closed'
                        trade_log.loc[idx, 'Exit Price'] = exit_price
                        trade_log.loc[idx, 'Exit Date'] = exit_date.strftime("%Y-%m-%d")
                        save_trade_log(trade_log)
                        st.success(f"Closed {close_ticker}!")
                        st.rerun()
            else:
                st.write("No open trades to close.")
    
    with tab1:
        if st.session_state.get('run_screener', False):
            tickers_list = get_tickers_from_csv(file_path=UNIVERSE_FILE)
            if tickers_list:
                trade_log = load_trade_log()
                open_positions = trade_log[trade_log['Status'] == 'Open'].to_dict('records')
                
                buy_signals, exit_alerts = check_weekly_signals(
                    tickers_list, 
                    open_positions, 
                    bb_std=bb_std, 
                    sma_period=sma_period, 
                    atr_mult=atr_mult, 
                    stop_loss_pct=stop_loss_pct
                )
                
                if exit_alerts:
                    st.error(f"🚨 ALERT: {len(exit_alerts)} of your open positions hit their exit criteria this week!")
                    st.dataframe(pd.DataFrame(exit_alerts), use_container_width=True, hide_index=True)
                else:
                    st.success("✅ None of your open positions hit an exit criteria this week.")
                    
                st.divider()
                st.subheader("🛒 Actionable BUY Signals (Ranked by 52W ROC)")
                
                if buy_signals:
                    st.success(f"Found {len(buy_signals)} breakout signals this week!")
                    
                    df_signals = pd.DataFrame(buy_signals)
                    open_tickers = [p['Ticker'] for p in open_positions]
                    
                    def highlight_owned(row):
                        if row['Ticker'] in open_tickers:
                            return ['background-color: rgba(255, 255, 153, 0.4);'] * len(row)
                        return [''] * len(row)
                            
                    st.dataframe(df_signals.style.apply(highlight_owned, axis=1), use_container_width=True, hide_index=True)
                else:  
                    st.info("No new BUY signals generated this week.")
        else:
            st.info("Click 'Run Weekly Screener' at the top to generate this week's signals.")

if __name__ == "__main__":
    main()
