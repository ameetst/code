import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import os
import warnings

warnings.filterwarnings('ignore')

TRADE_LOG_FILE = "breakout_trade_log_live.csv"
UNIVERSE_FILE = "ind_niftytotalmarket_list (3).csv"

def load_trade_log():
    if os.path.exists(TRADE_LOG_FILE):
        df = pd.read_csv(TRADE_LOG_FILE)
        return df
    else:
        return pd.DataFrame(columns=["Ticker", "Entry Price", "Quantity", "Buy Date", "Status", "Exit Price", "Exit Date"])

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

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_data(tickers, start_date, end_date):
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

def check_daily_signals(tickers, open_positions, p52h_thresh=0.95, p6mh_thresh=0.95, rfr=6.0, sharpe_percentile=50, enable_p52h=True, enable_p6mh=True, enable_sharpe=True, enable_liquidity=True):
    if not tickers:
        return [], [], False, None

    # Fetch slightly more than a year to get 252 days and 63 days offset
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=400) 
    
    current_time = datetime.datetime.now()
    today = current_time.date()
    # Indian market closes at 15:30. We use 16:00 (4 PM) to safely assume the daily candle is completed.
    market_closed_for_today = current_time.hour >= 16

    with st.spinner("Downloading Nifty 500 (^CRSLDX) for regime filter..."):
        idx_data = fetch_data(["^CRSLDX"], start_date, end_date)
        if isinstance(idx_data.columns, pd.MultiIndex):
            idx_df = idx_data['^CRSLDX'].copy()
        else:
            idx_df = idx_data.copy()
            
        idx_df.dropna(subset=['Close'], inplace=True)
        # Drop today's live candle if running during market hours
        if len(idx_df) > 0 and idx_df.index[-1].date() == today and not market_closed_for_today:
            idx_df = idx_df.iloc[:-1]
            
        if len(idx_df) > 50:
            idx_df['EMA_50'] = idx_df['Close'].ewm(span=50, adjust=False).mean()
            idx_close = idx_df['Close'].iloc[-1]
            idx_ema = idx_df['EMA_50'].iloc[-1]
            regime_bullish = idx_close > idx_ema
            idx_date = idx_df.index[-1].date()
        else:
            regime_bullish = False
            idx_date = None

    with st.spinner(f"Downloading daily data for {len(tickers)} tickers... This might take a minute."):
        data = fetch_data(tickers, start_date, end_date)
    
    buy_signals = []
    exit_alerts = []
    processed_dfs = {}
    
    progress_text = "Calculating indicators..."
    my_bar = st.progress(0, text=progress_text)
    
    open_tickers = {p['Ticker']: p for p in open_positions}
    
    # Pre-calculate to find target percentile Sharpe_3M
    valid_sharpes = []
    
    for idx, ticker in enumerate(tickers):
        if idx % 20 == 0:
            my_bar.progress(0.5 * (idx / len(tickers)), text="Calculating basic indicators...")
            
        try:
            if len(tickers) == 1:
                df = data.copy()
            else:
                if ticker not in data.columns.levels[0]:
                    continue
                df = data[ticker].copy()
                
            df.dropna(subset=['Close', 'High'], inplace=True)
            
            # CRITICAL: Drop today's live candle if market is still open to use strictly T-1 close
            if len(df) > 0 and df.index[-1].date() == today and not market_closed_for_today:
                df = df.iloc[:-1]
                
            if len(df) < 200:
                continue

            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
            df['High_52W'] = df['High'].rolling(252, min_periods=100).max()
            df['High_6M'] = df['High'].rolling(126, min_periods=60).max()
            
            df['Traded_Value'] = df['Close'] * df['Volume']
            df['Median_TV_12M'] = df['Traded_Value'].rolling(window=252, min_periods=100).median()
            df['Median_TV_6M'] = df['Traded_Value'].rolling(window=126, min_periods=60).median()
            
            df['Daily_Return'] = df['Close'].pct_change()
            rolling_return = df['Daily_Return'].rolling(63)
            daily_rfr = rfr / 100.0 / 252.0
            # Annualized Sharpe over 63 trading days
            df['Sharpe_3M'] = ((rolling_return.mean() - daily_rfr) / rolling_return.std()) * np.sqrt(252)
            
            latest = df.iloc[-1]
            
            if pd.notna(latest['Sharpe_3M']):
                valid_sharpes.append(latest['Sharpe_3M'])
                
            processed_dfs[ticker] = df
            
        except Exception as e:
            continue
            
    sharpe_thresh = np.percentile(valid_sharpes, sharpe_percentile) if valid_sharpes else np.inf
    
    for idx, (ticker, df) in enumerate(processed_dfs.items()):
        if idx % 20 == 0:
            my_bar.progress(0.5 + 0.5 * (idx / len(processed_dfs)), text="Evaluating signals...")
            
        latest = df.iloc[-1]
        close = latest['Close']
        clean_ticker = str(ticker.replace('.NS', ''))
        
        # Check for EXIT alert if owned
        if clean_ticker in open_tickers:
            ema_50 = latest['EMA_50']
            if close < ema_50:
                exit_alerts.append({
                    'Ticker': clean_ticker,
                    'Current Close': f"{close:.2f}",
                    '50-Day EMA': f"{ema_50:.2f}",
                    'Reason': "Close Below 50 EMA"
                })
                
        # Check for BUY signal
        cond_trend = (close > latest['EMA_50']) and (latest['EMA_50'] > latest['EMA_200']) and (latest['EMA_200'] > 0)
        cond_52w = (close >= (latest['High_52W'] * p52h_thresh)) if enable_p52h else True
        cond_6m = (close >= (latest['High_6M'] * p6mh_thresh)) if enable_p6mh else True
        cond_sharpe = (pd.notna(latest['Sharpe_3M']) and (latest['Sharpe_3M'] >= sharpe_thresh)) if enable_sharpe else True
        
        cond_liquidity = (latest['Median_TV_12M'] > 10_000_000 and latest['Median_TV_6M'] > 10_000_000) if enable_liquidity else True
        
        if cond_trend and cond_52w and cond_6m and cond_sharpe and cond_liquidity:
            buy_signals.append({
                'Ticker': clean_ticker,
                'Close Price': f"{close:.2f}",
                '52W High Proximity': f"{close / latest['High_52W'] * 100:.1f}%",
                '6M High Proximity': f"{close / latest['High_6M'] * 100:.1f}%",
                '3M Sharpe': f"{latest['Sharpe_3M']:.2f}",
                '_sharpe_raw': latest['Sharpe_3M']
            })
            
    my_bar.empty()
    
    # Sort buys by Sharpe
    buy_signals.sort(key=lambda x: x['_sharpe_raw'], reverse=True)
    # Remove raw key
    for b in buy_signals:
        del b['_sharpe_raw']
        
    return buy_signals, exit_alerts, regime_bullish, idx_date

import config_manager

def main():
    try:
        st.set_page_config(page_title="Daily Breakout Screener", layout="wide")
    except Exception:
        pass
        
    config = config_manager.load_config()
    
    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("📈 Momentum Breakout System")
        st.markdown("This screener finds stocks in strong uptrends that are breaking out, filtering for the **Top 10% Momentum Leaders**. It manages positions strictly using a **50-Day EMA structural stop**.")
    with col2:
        st.write("")
        st.write("")
        if st.button("Run Daily Screener", type="primary", use_container_width=True):
            st.session_state['breakout_has_run'] = True
            
    st.info("💡 **Best Practice:** You can run this screener every morning (e.g., at 9:30 AM). The engine safely ignores live intra-day ticks and strictly evaluates signals using **yesterday's completed closing prices**.")
    
    tab1, tab2, tab3 = st.tabs(["Actionable Signals (Screener)", "Trade Journal & Portfolio", "Configuration"])
    
    with tab3:
        st.header("Screener Configuration")
        enable_p52h = st.checkbox("Enable Min % of 52-Week High Filter", value=config.get("breakout_enable_p52h", True))
        p52h_thresh = st.slider("Min % of 52-Week High", min_value=0.50, max_value=1.00, value=float(config["breakout_p52h_thresh"]), step=0.01, disabled=not enable_p52h)
        
        enable_p6mh = st.checkbox("Enable Min % of 6-Month High Filter", value=config.get("breakout_enable_p6mh", True))
        p6mh_thresh = st.slider("Min % of 6-Month High", min_value=0.50, max_value=1.00, value=float(config["breakout_p6mh_thresh"]), step=0.01, disabled=not enable_p6mh)
        
        st.divider()
        st.subheader("Momentum (Sharpe) Configuration")
        enable_sharpe = st.checkbox("Enable Sharpe Momentum Filter", value=config.get("breakout_enable_sharpe", True))
        rfr = st.number_input("Risk-Free Rate (%)", min_value=0.0, max_value=20.0, value=float(config.get("breakout_rfr", 6.0)), step=0.5, disabled=not enable_sharpe)
        sharpe_percentile = st.slider("Minimum 3M Sharpe Percentile", min_value=0, max_value=99, value=int(config.get("breakout_sharpe_percentile", 50)), step=1, help="Set to 50 for the Median Sharpe.", disabled=not enable_sharpe)
        
        st.divider()
        st.subheader("Liquidity Configuration")
        enable_liquidity = st.checkbox("Enable Liquidity Filter (> 1 Cr ADTV for 6M & 12M)", value=config.get("breakout_enable_liquidity", True))
        
        st.write("")
        if st.button("Save as Default Configuration"):
            config_manager.save_config({
                "breakout_enable_p52h": enable_p52h,
                "breakout_p52h_thresh": p52h_thresh,
                "breakout_enable_p6mh": enable_p6mh,
                "breakout_p6mh_thresh": p6mh_thresh,
                "breakout_enable_sharpe": enable_sharpe,
                "breakout_rfr": rfr,
                "breakout_sharpe_percentile": sharpe_percentile,
                "breakout_enable_liquidity": enable_liquidity
            })
            st.success("Configuration saved! These settings will load automatically next time.")
        
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
            st.subheader(f"Open Positions ({len(open_trades)} / 10)")
            if not open_trades.empty:
                st.dataframe(open_trades[["Ticker", "Entry Price", "Quantity", "Buy Date"]], use_container_width=True, hide_index=True)
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
        mcol1, mcol2 = st.columns(2)
        
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
                        "Status": "Open",
                        "Exit Price": 0.0,
                        "Exit Date": ""
                    }
                    trade_log = pd.concat([trade_log, pd.DataFrame([new_row])], ignore_index=True)
                    save_trade_log(trade_log)
                    st.success(f"Added {new_ticker.upper()} to journal!")
                    st.rerun()
                    
        with mcol2:
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
        if st.session_state.get('breakout_has_run', False):
            tickers_list = get_tickers_from_csv(file_path=UNIVERSE_FILE)
            if tickers_list:
                trade_log = load_trade_log()
                open_positions = trade_log[trade_log['Status'] == 'Open'].to_dict('records')
                
                buy_signals, exit_alerts, regime_bullish, eval_date = check_daily_signals(
                    tickers_list, 
                    open_positions, 
                    p52h_thresh=p52h_thresh, 
                    p6mh_thresh=p6mh_thresh,
                    rfr=rfr,
                    sharpe_percentile=sharpe_percentile,
                    enable_p52h=enable_p52h,
                    enable_p6mh=enable_p6mh,
                    enable_sharpe=enable_sharpe,
                    enable_liquidity=enable_liquidity
                )
                
                st.markdown(f"**Data Evaluated On:** {eval_date.strftime('%d %b %Y') if eval_date else 'N/A'}")
                
                if not regime_bullish:
                    st.error("🛑 **MARKET REGIME: BEARISH.** Nifty 500 closed below its 50-Day EMA. No new entries should be taken.")
                else:
                    st.success("✅ **MARKET REGIME: BULLISH.** Nifty 500 closed above its 50-Day EMA.")
                
                st.divider()
                st.subheader("🚨 Exit Alerts (Open Positions)")
                if exit_alerts:
                    st.error(f"{len(exit_alerts)} of your open positions closed below their 50-Day EMA!")
                    st.dataframe(pd.DataFrame(exit_alerts), use_container_width=True, hide_index=True)
                else:
                    st.success("None of your open positions hit the 50 EMA exit criteria.")
                    
                st.divider()
                st.subheader("🛒 Actionable BUY Signals (Top 10% ROC)")
                
                if regime_bullish:
                    if buy_signals:
                        st.success(f"Found {len(buy_signals)} breakout signals!")
                        
                        df_signals = pd.DataFrame(buy_signals)
                        open_tickers = [p['Ticker'] for p in open_positions]
                        
                        def highlight_owned(row):
                            if row['Ticker'] in open_tickers:
                                return ['background-color: rgba(255, 255, 153, 0.4);'] * len(row)
                            return [''] * len(row)
                                
                        st.dataframe(df_signals.style.apply(highlight_owned, axis=1), use_container_width=True, hide_index=True)
                    else:  
                        st.info("No new BUY signals generated.")
                else:
                    st.warning("Buy signals are suppressed due to the Bearish Market Regime.")
        else:
            st.info("Click 'Run Daily Screener' at the top to generate signals.")

if __name__ == "__main__":
    main()
