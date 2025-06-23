import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import date

st.title("Dual Momentum Strategy")

st.markdown("""
This tool allows you to analyze the dual momentum strategy using two selected ETFs from the following:
- NIFTYBEES
- JUNIORBEES
- GOLDBEES
- SILVERBEES

Select two different ETFs to proceed.
""")

etf_options = {
    "NIFTYBEES": "NIFTYBEES.NS",
    "JUNIORBEES": "JUNIORBEES.NS",
    "GOLDBEES": "GOLDBEES.NS",
    "SILVERBEES": "SILVERBEES.NS"
}

# First dropdown
etf1 = st.selectbox("Select the first ETF:", list(etf_options.keys()), key="etf1")

# Second dropdown, enabled only after first is selected
etf2 = None
if etf1:
    remaining_etfs = [etf for etf in etf_options.keys() if etf != etf1]
    etf2 = st.selectbox("Select the second ETF:", remaining_etfs, key="etf2")

# Dual momentum logic and backtest
if etf1 and etf2:
    st.success(f"You selected {etf1} and {etf2}. Calculating dual momentum strategy using Yahoo Finance data...")
    tickers = [etf_options[etf1], etf_options[etf2]]
    start_date = "2015-01-01"
    end_date = pd.Timestamp.today().strftime("%Y-%m-%d")

    # Download monthly close data for both ETFs using yfinance
    raw_data = yf.download(tickers, start=start_date, end=end_date, interval="1mo")
    if raw_data is None or raw_data.empty:
        st.error("No data was downloaded. Please check ticker symbols or internet connection.")
        st.stop()
    if isinstance(raw_data.columns, pd.MultiIndex):
        data = raw_data['Close']
    else:
        data = raw_data[["Close"]]
        data.columns = [tickers[0]]
    data = data.dropna(how='all')

    # Calculate 12m and 6m returns
    returns_12m = data.pct_change(12)
    returns_6m = data.pct_change(6)
    # Weighted score: 50% 12m, 50% 6m
    score = 0.5 * returns_12m + 0.5 * returns_6m
    score = score.dropna()
    data = data.loc[score.index]
    # Each month, pick the ETF with the highest score
    best_etf = score.idxmax(axis=1)

    # Simulate strategy: each month, hold the ETF with the highest score
    strategy_returns = []
    nav_buy = []
    nav_sell = []
    held_etfs = []
    current_etf = None
    current_nav_buy = None
    for i in range(1, len(data)):
        chosen_etf = best_etf.iloc[i-1]  # Use previous month's signal
        # If same as previous, continue to hold, else switch
        if current_etf == chosen_etf:
            # Continue to hold, NAV at buy remains the same, NAV at sell is empty
            nav_buy.append(current_nav_buy)
            nav_sell.append(None)
        else:
            # Switch, update NAV at buy and NAV at sell for previous holding
            if current_etf is not None:
                nav_sell[-1] = data[current_etf].iloc[i-1]  # Set sell NAV for previous holding
            current_etf = chosen_etf
            current_nav_buy = data[chosen_etf].iloc[i-1]
            nav_buy.append(current_nav_buy)
            nav_sell.append(None)  # Will be filled on next switch or left None if held till end
        held_etfs.append(current_etf)
        monthly_return = data[current_etf].iloc[i] / data[current_etf].iloc[i-1] - 1
        strategy_returns.append(monthly_return)
    # After loop, set NAV at sell for last holding
    if nav_sell:
        nav_sell[-1] = data[current_etf].iloc[-1]
    strategy_returns = pd.Series(strategy_returns, index=data.index[1:])
    equity_curve = (1 + strategy_returns).cumprod()

    st.subheader("Backtest Results")
    st.write(f"CAGR: {100 * (equity_curve.iloc[-1] ** (12/len(equity_curve)) - 1):.2f}%")
    st.write(f"Max Drawdown: {100 * ((equity_curve.cummax() - equity_curve).max() / equity_curve.cummax().max()):.2f}%")

    st.line_chart(equity_curve, height=400, width=900)
    st.caption("Equity curve of the dual momentum strategy (monthly rebalancing, 50% 12m + 50% 6m lookback returns)")

    # Show which ETF was held each month, NAV at buy and sell
    returns_col = []
    for i in range(len(nav_sell)):
        if nav_sell[i] is not None and nav_buy[i] is not None:
            ret = (nav_sell[i] / nav_buy[i] - 1) * 100
            returns_col.append(f"{ret:.2f}%")
        else:
            returns_col.append("")
    results_df = pd.DataFrame({
        "Month": data.index[1:],
        "Held ETF": held_etfs,
        "NAV at Buy": nav_buy,
        "NAV at Sell": nav_sell,
        "Returns": returns_col
    })
    results_df.set_index("Month", inplace=True)
    # Left align all columns
    st.dataframe(results_df.style.set_properties(**{'text-align': 'left'}), height=400)