import pandas as pd
import numpy as np

# Debug the regime filter
index_data = pd.read_pickle('index_data.pkl')
print("Index data columns:", index_data.columns.tolist())
print("Index data shape:", index_data.shape)
print("Last 5 rows:")
print(index_data.tail(5))

# Check if MultiIndex
if isinstance(index_data.columns, pd.MultiIndex):
    close_series = index_data['Close'].iloc[:, 0]
    print("\nMultiIndex detected, extracted Close column")
else:
    close_series = index_data['Close']
    print("\nFlat columns, using Close directly")

ema_series = close_series.ewm(span=50, adjust=False).mean()

print(f"\nLast Close: {close_series.iloc[-1]}")
print(f"Last 50EMA: {ema_series.iloc[-1]}")
print(f"Regime Bullish: {close_series.iloc[-1] > ema_series.iloc[-1]}")

# Show last 5 days comparison
print("\nLast 5 days Close vs 50EMA:")
for i in range(-5, 0):
    print(f"  {close_series.index[i].date()}: Close={close_series.iloc[i]:.2f}, 50EMA={ema_series.iloc[i]:.2f}, Bullish={close_series.iloc[i] > ema_series.iloc[i]}")
