import yfinance as yf
import pandas as pd
from crossover import fetch_data

# mimic crossover.py logic exactly
start_date = (pd.Timestamp.today() - pd.Timedelta(days=365*2)).strftime('%Y-%m-%d')
end_date = pd.Timestamp.today().strftime('%Y-%m-%d')
tickers = ["AEGISLOG.NS", "RELIANCE.NS"]
df_all = fetch_data(tickers, start_date, end_date)

if df_all.empty:
    print("DataFrame is empty!")
else:
    df = df_all['AEGISLOG.NS'].copy()
    print(df.tail())
    df.dropna(subset=['Close', 'High'], inplace=True)
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    df['Crossover'] = (df['EMA_50'] > df['EMA_200']) & (df['EMA_50'].shift(1) <= df['EMA_200'].shift(1))
    
    print("\nCrossovers found:")
    crossovers = df[df['Crossover']]
    print(crossovers[['Close', 'EMA_50', 'EMA_200']])
    
    if not crossovers.empty:
        latest_cross_idx = crossovers.index[-1]
        days_since_cross = len(df.loc[latest_cross_idx:]) - 1
        print(f"\nDays since cross: {days_since_cross}")
        
        # Calculate 52-week high
        high_52w = df['High'].tail(252).max()
        latest_price = df.iloc[-1]['Close']
        ratio = latest_price / high_52w
        print(f"P/52H: {ratio:.4f}")
        print(f"Meets condition (<= 21 days)? {days_since_cross <= 21}")
        print(f"Meets threshold (>= 0.75)? {ratio >= 0.75}")
