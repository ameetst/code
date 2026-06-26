import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import warnings
import os
import config_manager

warnings.filterwarnings('ignore')

TRADE_LOG_FILE = "vcp_trade_log.csv"
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
ATR_TRAIL_MULTIPLIER = 2.5
TREND_CONFIRM_DAYS = 20
RS_LOOKBACK = 63


def load_trade_log():
    if os.path.exists(TRADE_LOG_FILE):
        df = pd.read_csv(TRADE_LOG_FILE)
        if 'Status' not in df.columns:
            df['Status'] = 'Open'
        if 'Exit Price' not in df.columns:
            df['Exit Price'] = 0.0
        if 'Exit Date' not in df.columns:
            df['Exit Date'] = ""
        return df
    return pd.DataFrame(columns=["Ticker", "Entry Price", "Quantity", "Buy Date", "Status", "Exit Price", "Exit Date", "Stop Loss"])


def save_trade_log(df):
    df.to_csv(TRADE_LOG_FILE, index=False)


def get_tickers_from_csv(file_path="tickers.csv"):
    try:
        df = pd.read_csv(file_path)
        if 'TICKER' in df.columns:
            tickers = df['TICKER'].dropna().astype(str).tolist()
            return [f"{t.strip()}.NS" for t in tickers if t.strip()]
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
    start_date = end_date - datetime.timedelta(days=90)
    return yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)


def _atr(df, period=ATR_PERIOD):
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift(1)).abs()
    low_close = (df['Low'] - df['Close'].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def check_daily_signals(tickers, range_threshold, volume_multiplier, rsi_min, rsi_max, enable_dryup, dryup_pct):
    if not tickers:
        return [], None

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=730)

    with st.spinner(f"Downloading recent data for {len(tickers)} tickers..."):
        data = fetch_data(tickers, start_date, end_date)

    with st.spinner("Downloading benchmark data for trend and relative strength..."):
        benchmark_data = fetch_data(["^CRSLDX"], start_date, end_date)
        if isinstance(benchmark_data.columns, pd.MultiIndex):
            benchmark_df = benchmark_data["^CRSLDX"].copy() if "^CRSLDX" in benchmark_data.columns.levels[0] else pd.DataFrame()
        else:
            benchmark_df = benchmark_data.copy()
        if not benchmark_df.empty:
            benchmark_df.dropna(subset=['Close'], inplace=True)
            benchmark_df['EMA_50'] = benchmark_df['Close'].ewm(span=50, adjust=False).mean()
            benchmark_df['EMA_200'] = benchmark_df['Close'].ewm(span=200, adjust=False).mean()
            benchmark_df['EMA_200_Rising'] = benchmark_df['EMA_200'] > benchmark_df['EMA_200'].shift(TREND_CONFIRM_DAYS)
            benchmark_df['RS_63'] = benchmark_df['Close'].pct_change(RS_LOOKBACK)

    actionable_signals = []
    eval_date_str = ""
    progress_text = "Processing VCP breakout signals..."
    my_bar = st.progress(0, text=progress_text)

    benchmark_by_date = None
    if not benchmark_df.empty:
        benchmark_by_date = benchmark_df[['RS_63']].copy()

    for idx, ticker in enumerate(tickers):
        if idx % 20 == 0:
            my_bar.progress(idx / max(len(tickers), 1), text=progress_text)

        try:
            if len(tickers) == 1:
                df = data.copy()
            else:
                if ticker not in data.columns.levels[0]:
                    continue
                df = data[ticker].copy()

            df.dropna(subset=['Close', 'High', 'Low', 'Volume'], inplace=True)
            if df.empty or len(df) < 50:
                continue

            if not eval_date_str:
                eval_date_str = df.index[-1].strftime('%d-%b-%Y')

            df['10D_High'] = df['High'].rolling(10).max().shift(1)
            df['10D_Low'] = df['Low'].rolling(10).min().shift(1)
            df['Range_10D'] = (df['10D_High'] - df['10D_Low']) / (df['10D_Low'] + 1e-8)

            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / (loss + 1e-8)
            df['RSI'] = 100 - (100 / (1 + rs))

            df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
            df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
            df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
            df['EMA_200_Rising'] = df['EMA_200'] > df['EMA_200'].shift(TREND_CONFIRM_DAYS)
            df['ATR_14'] = _atr(df)

            df['Vol_20D_Avg'] = df['Volume'].rolling(20).mean().shift(1)
            df['Vol_5D_Avg'] = df['Volume'].rolling(5).mean().shift(1)
            df['Vol_Surge'] = df['Volume'] / (df['Vol_20D_Avg'] + 1e-8)

            df['Price_Up'] = df['Close'] > df['Close'].shift(1)
            df['RS_63'] = df['Close'].pct_change(RS_LOOKBACK)

            latest = df.iloc[-1]
            benchmark_latest = None
            if benchmark_by_date is not None:
                aligned_benchmark = benchmark_by_date.reindex(df.index, method='ffill')
                if not aligned_benchmark.empty:
                    benchmark_latest = aligned_benchmark.iloc[-1]

            cond_range = latest['Range_10D'] <= (range_threshold / 100.0)
            cond_rsi = (latest['RSI'] >= rsi_min) and (latest['RSI'] <= rsi_max)
            cond_vol = latest['Vol_Surge'] >= volume_multiplier
            cond_price = latest['Price_Up']
            cond_trend = (
                pd.notna(latest['EMA_50']) and pd.notna(latest['EMA_200']) and
                latest['Close'] > latest['EMA_50'] > latest['EMA_200'] > 0 and
                pd.notna(latest['EMA_200_Rising']) and bool(latest['EMA_200_Rising'])
            )
            cond_rs = (
                benchmark_latest is not None and
                pd.notna(latest['RS_63']) and
                pd.notna(benchmark_latest.get('RS_63', np.nan)) and
                latest['RS_63'] > benchmark_latest['RS_63']
            )

            if enable_dryup:
                cond_dryup = latest['Vol_5D_Avg'] < (latest['Vol_20D_Avg'] * (dryup_pct / 100.0))
            else:
                cond_dryup = True

            if cond_range and cond_rsi and cond_vol and cond_price and cond_dryup and cond_trend and cond_rs:
                atr_value = float(latest['ATR_14']) if pd.notna(latest['ATR_14']) and latest['ATR_14'] > 0 else np.nan
                stop_value = float(latest['Close'] - (ATR_STOP_MULTIPLIER * atr_value)) if pd.notna(atr_value) else np.nan
                actionable_signals.append({
                    'Ticker': str(ticker.replace('.NS', '')),
                    'Signal': 'BUY',
                    'Close Price': float(latest['Close']),
                    'Vol Surge (x)': float(latest['Vol_Surge']),
                    '10D Range (%)': float(latest['Range_10D'] * 100),
                    'RSI': float(latest['RSI']),
                    'ATR14': atr_value,
                    'Stop (2ATR)': stop_value,
                    'Risk/Share': float(ATR_STOP_MULTIPLIER * atr_value) if pd.notna(atr_value) else np.nan,
                    'Suggested Qty @2% Risk': np.nan,
                })

        except Exception:
            continue

    my_bar.empty()
    return actionable_signals, eval_date_str


def main():
    try:
        st.set_page_config(page_title="VCP Breakout Screener", layout="wide")
    except Exception:
        pass

    config = config_manager.load_config()

    col1, col2 = st.columns([4, 1])
    with col1:
        st.title("VCP Breakout System")
        st.markdown("Hunts for volatility contraction with trend and relative-strength confirmation.")
    with col2:
        st.write("")
        st.write("")
        if st.button("Run Screener", type="primary", use_container_width=True):
            st.session_state['vcp_has_run'] = True

    tab1, tab2, tab3 = st.tabs(["Actionable Signals (Screener)", "Trade Journal & Portfolio", "Configuration"])

    with tab3:
        st.header("Screener Configuration")

        range_threshold = st.slider(
            "Max 10-Day Price Range (%)",
            min_value=1.0,
            max_value=15.0,
            value=float(config.get("vcp_range_threshold", 8.0)),
            step=0.5,
            help="Maximum acceptable percentage difference between the highest high and lowest low over the last 10 days."
        )

        volume_multiplier = st.slider(
            "Min Volume Surge (x 20-Day Avg)",
            min_value=1.0,
            max_value=10.0,
            value=float(config.get("vcp_volume_multiplier", 3.0)),
            step=0.5,
            help="Today's volume must be at least this many times greater than the 20-day average volume."
        )

        enable_dryup = st.checkbox("Enable Volume Dry-Up Filter (Conservative Mode)", value=config.get("vcp_enable_dryup", False))
        dryup_pct = st.slider(
            "Max Pre-Breakout Volume Dry-Up (%)",
            min_value=10,
            max_value=150,
            value=int(config.get("vcp_dryup_pct", 70)),
            step=5,
            help="Average volume over the 5 days prior to breakout must be less than this percentage of the 20-day average.",
            disabled=not enable_dryup
        )

        col_rsi1, col_rsi2 = st.columns(2)
        with col_rsi1:
            rsi_min = st.number_input("Min RSI (14)", value=int(config.get("vcp_rsi_min", 40)))
        with col_rsi2:
            rsi_max = st.number_input("Max RSI (14)", value=int(config.get("vcp_rsi_max", 60)))

        st.write("")
        if st.button("Save as Default Configuration"):
            config["vcp_range_threshold"] = range_threshold
            config["vcp_volume_multiplier"] = volume_multiplier
            config["vcp_rsi_min"] = rsi_min
            config["vcp_rsi_max"] = rsi_max
            config["vcp_enable_dryup"] = enable_dryup
            config["vcp_dryup_pct"] = dryup_pct
            config_manager.save_config(config)
            st.success("Configuration saved! These settings will load automatically next time.")

        with st.expander("Update Universe File"):
            with st.form("universe_upload_vcp"):
                uploaded_file = st.file_uploader("Upload CSV", type=['csv'])
                if st.form_submit_button("Save"):
                    try:
                        with open("tickers.csv", "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        st.success("Successfully updated `tickers.csv`! You can now run the screener with the new universe.")
                    except Exception as e:
                        st.error(f"Error saving file: {e}")

    with tab1:
        if st.session_state.get('vcp_has_run', False):
            tickers_list = get_tickers_from_csv(file_path="tickers.csv")
            if tickers_list:
                signals, eval_date_str = check_daily_signals(tickers_list, range_threshold, volume_multiplier, rsi_min, rsi_max, enable_dryup, dryup_pct)
                st.session_state['latest_vcp_signals'] = signals

                if signals:
                    st.success(f"Found {len(signals)} actionable signals today! (Data as of {eval_date_str})")
                    signals = sorted(signals, key=lambda x: (-x['Vol Surge (x)'], x['Ticker']))
                    df_signals = pd.DataFrame(signals)

                    if 'ATR14' in df_signals.columns:
                        df_signals['ATR14'] = df_signals['ATR14'].round(2)
                    if 'Stop (2ATR)' in df_signals.columns:
                        df_signals['Stop (2ATR)'] = df_signals['Stop (2ATR)'].round(2)
                    if 'Risk/Share' in df_signals.columns:
                        df_signals['Risk/Share'] = df_signals['Risk/Share'].round(2)

                    trade_log = load_trade_log()
                    open_tickers = [t.replace('.NS', '') for t in trade_log[trade_log['Status'] == 'Open']['Ticker'].tolist()] if not trade_log.empty else []

                    def highlight_owned(row):
                        if row['Ticker'] in open_tickers:
                            return ['background-color: rgba(255, 255, 153, 0.4);'] * len(row)
                        return [''] * len(row)

                    styled_df = df_signals.style.apply(highlight_owned, axis=1).format({
                        'Close Price': '{:.2f}',
                        'Vol Surge (x)': '{:.1f}x',
                        '10D Range (%)': '{:.1f}%',
                        'RSI': '{:.1f}',
                        'ATR14': '{:.2f}',
                        'Stop (2ATR)': '{:.2f}',
                        'Risk/Share': '{:.2f}',
                    })
                    st.dataframe(styled_df, use_container_width=True, hide_index=True)

                    st.write("---")
                    st.subheader("Add to Portfolio")
                    col_add1, col_add2, col_add3, col_add4, col_add5 = st.columns(5)
                    with col_add1:
                        sel_ticker = st.selectbox("Select Ticker", [s['Ticker'] for s in signals])
                    with col_add2:
                        sel_price = st.number_input("Entry Price", value=float(next((s['Close Price'] for s in signals if s['Ticker'] == sel_ticker), 0.0)))
                    with col_add3:
                        sel_qty = st.number_input("Quantity", min_value=1, value=100)
                    with col_add4:
                        default_stop = float(next((s['Stop (2ATR)'] for s in signals if s['Ticker'] == sel_ticker and pd.notna(s.get('Stop (2ATR)'))), sel_price * 0.90))
                        sel_sl = st.number_input("ATR Stop", value=default_stop)
                    with col_add5:
                        st.write("")
                        st.write("")
                        if st.button("Add Trade"):
                            if sel_ticker in open_tickers:
                                st.warning(f"{sel_ticker} is already in your open portfolio!")
                            else:
                                new_trade = pd.DataFrame([{
                                    "Ticker": sel_ticker,
                                    "Entry Price": sel_price,
                                    "Quantity": sel_qty,
                                    "Buy Date": eval_date_str,
                                    "Status": "Open",
                                    "Exit Price": 0.0,
                                    "Exit Date": "",
                                    "Stop Loss": sel_sl,
                                    "ATR Trail": ATR_TRAIL_MULTIPLIER,
                                }])
                                trade_log = pd.concat([trade_log, new_trade], ignore_index=True)
                                save_trade_log(trade_log)
                                st.success(f"Added {sel_ticker} to portfolio!")
                                st.rerun()
                else:
                    st.info(f"No new BUY signals generated today that meet the filter criteria. (Data as of {eval_date_str})")
        else:
            st.info("Click 'Run Screener' at the top to generate today's signals.")

    with tab2:
        st.header("Trade Journal & Portfolio")
        trade_log = load_trade_log()
        open_trades = trade_log[trade_log['Status'] == 'Open'].copy()
        closed_trades = trade_log[trade_log['Status'] == 'Closed'].copy()

        st.markdown("### Portfolio Statistics")
        stat_col1, stat_col2, stat_col3, stat_col4, stat_col5, stat_col6 = st.columns(6)

        active_trades_count = len(open_trades)
        total_invested = 0.0
        total_unrealized_pnl = 0.0

        if not open_trades.empty:
            total_invested = (pd.to_numeric(open_trades['Entry Price']) * pd.to_numeric(open_trades['Quantity'])).sum()
            tickers_to_fetch = [t + ".NS" for t in open_trades["Ticker"].unique().tolist()]
            live_data = get_live_data(tickers_to_fetch)
            for _, row in open_trades.iterrows():
                t = row["Ticker"] + ".NS"
                try:
                    df = live_data.copy() if len(tickers_to_fetch) == 1 else live_data[t].copy()
                    df.dropna(subset=['Close'], inplace=True)
                    if not df.empty:
                        curr_price = df.iloc[-1]['Close']
                        total_unrealized_pnl += (curr_price - row['Entry Price']) * row['Quantity']
                except Exception:
                    pass

        total_realized_pnl = 0.0
        win_rate = 0.0
        avg_win = 0.0

        if not closed_trades.empty:
            closed_trades['Realized INR'] = (pd.to_numeric(closed_trades['Exit Price']) - pd.to_numeric(closed_trades['Entry Price'])) * pd.to_numeric(closed_trades['Quantity'])
            closed_trades['Realized %'] = ((pd.to_numeric(closed_trades['Exit Price']) - pd.to_numeric(closed_trades['Entry Price'])) / pd.to_numeric(closed_trades['Entry Price'])) * 100
            total_realized_pnl = closed_trades['Realized INR'].sum()
            winning_trades = closed_trades[closed_trades['Realized INR'] > 0]
            if len(closed_trades) > 0:
                win_rate = (len(winning_trades) / len(closed_trades)) * 100
            if len(winning_trades) > 0:
                avg_win = winning_trades['Realized %'].mean()

        stat_col1.metric("Active Trades", f"{active_trades_count}")
        stat_col2.metric("Capital Deployed", f"Rs {total_invested:,.2f}")
        stat_col3.metric("Realized P&L", f"Rs {total_realized_pnl:,.2f}")
        stat_col4.metric("Unrealized P&L", f"Rs {total_unrealized_pnl:,.2f}")
        stat_col5.metric("Win Rate", f"{win_rate:.1f}%")
        stat_col6.metric("Avg Win", f"{avg_win:.1f}%")

        st.divider()
        st.markdown("### Open Positions (Live Monitor)")

        col_refresh, _ = st.columns([1, 4])
        with col_refresh:
            if st.button("Refresh Prices", use_container_width=True):
                get_live_data.clear()
                st.rerun()

        if not open_trades.empty:
            with st.spinner("Fetching latest data for ATR trailing stop calculation..."):
                tickers_to_fetch = [t + ".NS" for t in open_trades["Ticker"].unique().tolist()]
                live_data = get_live_data(tickers_to_fetch)

                status_list = []
                for _, row in open_trades.iterrows():
                    t = row["Ticker"] + ".NS"
                    try:
                        df = live_data.copy() if len(tickers_to_fetch) == 1 else live_data[t].copy()
                        df.dropna(subset=['Close', 'High', 'Low'], inplace=True)
                        if df.empty:
                            status_list.append({"Current Price": 0, "P&L (%)": 0, "ATR14": 0, "Trail Stop": 0, "Hard Stop": row.get('Stop Loss', 0), "Status": "No Data"})
                            continue

                        df['ATR14'] = _atr(df)
                        latest = df.iloc[-1]
                        curr_price = latest['Close']
                        atr14 = float(latest['ATR14']) if pd.notna(latest['ATR14']) else np.nan
                        hard_stop = float(row.get('Stop Loss', row['Entry Price'] * 0.90))
                        trail_stop = max(hard_stop, curr_price - (ATR_TRAIL_MULTIPLIER * atr14)) if pd.notna(atr14) else hard_stop
                        pnl_pct = ((curr_price - row['Entry Price']) / row['Entry Price']) * 100

                        status = "HOLD"
                        if curr_price <= hard_stop:
                            status = "SELL (Hard Stop Hit)"
                        elif curr_price <= trail_stop:
                            status = "SELL (ATR Trail Broken)"

                        status_list.append({
                            "Current Price": round(curr_price, 2),
                            "P&L (%)": round(pnl_pct, 2),
                            "ATR14": round(atr14, 2) if pd.notna(atr14) else 0,
                            "Trail Stop": round(trail_stop, 2),
                            "Hard Stop": round(hard_stop, 2),
                            "Status": status,
                        })
                    except Exception:
                        status_list.append({"Current Price": 0, "P&L (%)": 0, "ATR14": 0, "Trail Stop": 0, "Hard Stop": row.get('Stop Loss', 0), "Status": "Error"})

                display_df = open_trades[['Ticker', 'Buy Date', 'Quantity', 'Entry Price']].copy()
                status_df = pd.DataFrame(status_list, index=display_df.index)
                final_df = pd.concat([display_df, status_df], axis=1)

                def color_status(val):
                    color = 'red' if 'SELL' in str(val) else 'green' if 'HOLD' in str(val) else 'white'
                    return f'color: {color}'

                st.dataframe(final_df.style.map(color_status, subset=['Status']), use_container_width=True, hide_index=True)

                st.write("---")
                st.markdown("#### Close Position")
                c_col1, c_col2, c_col3, c_col4 = st.columns(4)
                with c_col1:
                    close_ticker = st.selectbox("Select Ticker to Close", open_trades['Ticker'].tolist())
                with c_col2:
                    current_prc = final_df[final_df['Ticker'] == close_ticker]['Current Price'].values[0] if not final_df.empty else 0.0
                    close_price = st.number_input("Exit Price", value=float(current_prc))
                with c_col3:
                    close_date = st.date_input("Exit Date")
                with c_col4:
                    st.write("")
                    st.write("")
                    if st.button("Confirm Exit", type="primary"):
                        idx_to_close = trade_log[(trade_log['Ticker'] == close_ticker) & (trade_log['Status'] == 'Open')].index[0]
                        trade_log.at[idx_to_close, 'Status'] = 'Closed'
                        trade_log.at[idx_to_close, 'Exit Price'] = close_price
                        trade_log.at[idx_to_close, 'Exit Date'] = close_date.strftime("%d-%b-%Y")
                        save_trade_log(trade_log)
                        st.success(f"Closed {close_ticker}!")
                        st.rerun()
        else:
            st.info("No open trades in the VCP portfolio.")

        if not closed_trades.empty:
            st.divider()
            st.markdown("### Trade History")
            display_closed = closed_trades[['Ticker', 'Buy Date', 'Entry Price', 'Exit Date', 'Exit Price', 'Quantity', 'Realized INR', 'Realized %']]

            def color_pnl(val):
                color = 'green' if val > 0 else 'red' if val < 0 else 'white'
                return f'color: {color}'

            st.dataframe(
                display_closed.style.map(color_pnl, subset=['Realized INR', 'Realized %']).format({'Realized INR': '{:.2f}', 'Realized %': '{:.2f}%'}),
                use_container_width=True,
                hide_index=True,
            )


if __name__ == "__main__":
    main()
