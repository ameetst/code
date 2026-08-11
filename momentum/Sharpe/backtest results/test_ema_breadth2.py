import pandas as pd
import json
import momentum_lib as ml
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# 1. Load Prices
prices_df, nifty_series, stock_tickers, dates = ml.load_prices('N750_updated.xlsx')

# 2. Calculate per-stock EMAs (across columns = time axis)
print("Calculating per-stock EMAs...")
ema50_df = prices_df.ewm(span=50, adjust=False, axis=1).mean()
ema200_df = prices_df.ewm(span=200, adjust=False, axis=1).mean()

last_price = prices_df.iloc[:, -1]
last_ema50 = ema50_df.iloc[:, -1]
last_ema200 = ema200_df.iloc[:, -1]

valid_mask = last_price.notna() & last_ema200.notna()
valid_count = valid_mask.sum()

# Signal 1: % stocks > their own EMA50
above_ema50 = (last_price[valid_mask] > last_ema50[valid_mask]).sum()
s1_score = above_ema50 / valid_count if valid_count > 0 else 0

# Signal 2: % stocks > their own EMA200
above_ema200 = (last_price[valid_mask] > last_ema200[valid_mask]).sum()
s2_score = above_ema200 / valid_count if valid_count > 0 else 0

# 3. Get current regime components from history
try:
    with open('N750_regime_history.json', 'r') as f:
        history = json.load(f)
        latest = history[-1]
        breadth_score = latest['Breadth']
        momentum_score = latest['Momentum']
except Exception:
    breadth_score = 0.5
    momentum_score = 0.5

# Current Nifty500 EMA scores
px = nifty_series.dropna()
price_idx = px.iloc[-1]
ema50_idx = px.ewm(span=50, adjust=False).mean().iloc[-1]
ema200_idx = px.ewm(span=200, adjust=False).mean().iloc[-1]

EMA50_BAND = 0.05
EMA_TREND_BAND = 0.10
curr_ema50_score = float(np.clip((price_idx / ema50_idx - 1.0) / EMA50_BAND + 0.5, 0.0, 1.0))
curr_ema_trend_score = float(np.clip((ema50_idx / ema200_idx - 1.0) / EMA_TREND_BAND + 0.5, 0.0, 1.0))

# Current composite
old_composite = (curr_ema50_score * 0.35) + (curr_ema_trend_score * 0.25) + (breadth_score * 0.25) + (momentum_score * 0.15)

# New composite: replace signal 1 with % > EMA50, signal 2 with % > EMA200
new_composite = (s1_score * 0.35) + (s2_score * 0.25) + (breadth_score * 0.25) + (momentum_score * 0.15)

print(f"\n--- What-If: Replace with Universe EMA Breadth ---")
print(f"Valid Stocks: {valid_count}")
print(f"Signal 1 (Stocks > own EMA50):  {above_ema50}/{valid_count} = {s1_score*100:.1f}%")
print(f"Signal 2 (Stocks > own EMA200): {above_ema200}/{valid_count} = {s2_score*100:.1f}%")

print(f"\n--- Component Comparison ---")
print(f"Signal 1: {curr_ema50_score:.3f} (NIFTY EMA50 Dist)  vs  {s1_score:.3f} (Universe > EMA50)")
print(f"Signal 2: {curr_ema_trend_score:.3f} (NIFTY EMA Trend)  vs  {s2_score:.3f} (Universe > EMA200)")
print(f"Signal 3: {breadth_score:.3f} (52H Breadth - unchanged)")
print(f"Signal 4: {momentum_score:.3f} (Momentum Breadth - unchanged)")

print(f"\n--- Impact ---")
print(f"Current Composite: {old_composite:.3f}")
print(f"New Composite:     {new_composite:.3f}")
print(f"Net Impact:        {(new_composite - old_composite):+.3f}")
