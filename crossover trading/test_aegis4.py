import yfinance as yf
import pandas as pd
from crossover import fetch_data

# mimic crossover.py logic exactly
start_date = (pd.Timestamp.today() - pd.Timedelta(days=365*2)).strftime('%Y-%m-%d')
end_date = pd.Timestamp.today().strftime('%Y-%m-%d')
tickers = ["AEGISLOG.NS", "RELIANCE.NS"]
df_all = fetch_data(tickers, start_date, end_date)

df = df_all['AEGISLOG.NS'].copy()
df.dropna(subset=['Close', 'High'], inplace=True)

df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

# Traded Value
df['Traded_Value'] = df['Close'] * df['Volume']
df['Median_TV_21'] = df['Traded_Value'].rolling(window=21).median()

high_52w = df['High'].max()
high_52w_in_last_63d = (high_52w == df['High'].tail(63).max())
df['Momentum_63'] = df['Close'] / df['Close'].shift(63) - 1

df['Crossover'] = (df['EMA_50'] > df['EMA_200']) & (df['EMA_50'].shift(1) <= df['EMA_200'].shift(1))

crossovers = df[df['Crossover']]
latest_cross_idx = crossovers.index[-1]
days_since_crossover = len(df.loc[latest_cross_idx:]) - 1

latest_data = df.iloc[-1]
latest_close = latest_data['Close']
latest_ema_200 = latest_data['EMA_200']
latest_momentum_63 = latest_data['Momentum_63']
p_52h = latest_close / high_52w

print(f"Condition 1 (days_since_crossover <= 21): {days_since_crossover} <= 21 -> {0 <= days_since_crossover <= 21}")
print(f"Condition 2 (EMA_50 > EMA_200): {latest_data['EMA_50']:.2f} > {latest_ema_200:.2f} -> {latest_data['EMA_50'] > latest_ema_200}")
print(f"Condition 3 (P_52H >= 0.75): {p_52h:.4f} >= 0.75 -> {p_52h >= 0.75}")
print(f"Condition 4 (close > ema_200): {latest_close:.2f} > {latest_ema_200:.2f} -> {latest_close > latest_ema_200}")
print(f"Condition 5 (momentum_63 > 0): {latest_momentum_63:.4f} > 0 -> {latest_momentum_63 > 0}")
print(f"Condition 6 (high_52w_in_last_63d): {high_52w:.2f} == {df['High'].tail(63).max():.2f} -> {high_52w_in_last_63d}")
print(f"Condition 7 (Median_TV_21 > 10M): {latest_data['Median_TV_21']:,.0f} > 10M -> {latest_data['Median_TV_21'] > 10000000}")

