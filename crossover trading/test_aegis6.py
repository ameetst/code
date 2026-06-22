import pandas as pd
from crossover import fetch_data
import datetime

tickers = ["AEGISLOG.NS"]
end_date = datetime.date.today()
start_date = end_date - datetime.timedelta(days=1095) 

data = fetch_data(tickers, start_date, end_date)
df = data.copy()
df.dropna(subset=['Close', 'High'], inplace=True)

df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

df['Traded_Value'] = df['Close'] * df['Volume']
df['Median_TV_21'] = df['Traded_Value'].rolling(window=21).median()

high_52w = df['High'].max()

df['Signal'] = 0
df.loc[df['EMA_50'] > df['EMA_200'], 'Signal'] = 1
df['Position'] = df['Signal'].diff()

latest_data = df.iloc[-1]
latest_close = latest_data['Close']
latest_ema_200 = latest_data['EMA_200']

pos_1_indices = df[df['Position'] == 1].index
print("pos_1_indices empty?", pos_1_indices.empty)

if not pos_1_indices.empty:
    last_cross_iloc = df.index.get_loc(pos_1_indices[-1])
    days_since_crossover = (len(df) - 1) - last_cross_iloc
    print("Days since crossover:", days_since_crossover)
    
    cond_lookback = (0 <= days_since_crossover <= 21)
    cond_p52h = True # disabled
    cond_liquidity = (latest_data['Median_TV_21'] > 10_000_000)
    
    print("cond_lookback:", cond_lookback)
    print("EMA_50 > EMA_200:", latest_data['EMA_50'] > latest_data['EMA_200'])
    print("cond_p52h:", cond_p52h)
    print("latest_close > latest_ema_200:", latest_close > latest_ema_200)
    print("cond_liquidity:", cond_liquidity)
