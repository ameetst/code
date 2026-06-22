import yfinance as yf
import pandas as pd
from crossover import check_daily_signals

tickers = ["AEGISLOG.NS"]
signals = check_daily_signals(tickers, threshold=0.75, max_lookback=21)
print("Signals found:", signals)

df = yf.download(tickers, period='2y', progress=False)
df.dropna(subset=['Close'], inplace=True)
df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
df['Crossover'] = (df['EMA_50'] > df['EMA_200']) & (df['EMA_50'].shift(1) <= df['EMA_200'].shift(1))

crossovers = df[df['Crossover']]
print("\nRecent Crossovers for AEGISLOG.NS:")
print(crossovers[['Close', 'EMA_50', 'EMA_200']].tail())

if not df.empty:
    latest = df.iloc[-1]
    high_52w = df['Close'].tail(252).max()
    ratio = latest['Close'] / high_52w
    print(f"\nLatest Price: {latest['Close']}")
    print(f"52W High: {high_52w}")
    print(f"P/52H Ratio: {ratio}")
