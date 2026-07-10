import pandas as pd
import json
import momentum_lib as ml
import warnings
warnings.filterwarnings('ignore')

# 1. Load Prices
prices_df, nifty_series, stock_tickers, dates = ml.load_prices('N750_updated.xlsx')

# 2. Calculate Universe EMA50 and EMA200
print("Calculating EMAs for all stocks...")
# Axis 1 means calculate over columns (time)
ema50_df = prices_df.ewm(span=50, adjust=False, axis=1).mean()
ema200_df = prices_df.ewm(span=200, adjust=False, axis=1).mean()

last_price = prices_df.iloc[:, -1]
last_ema50 = ema50_df.iloc[:, -1]
last_ema200 = ema200_df.iloc[:, -1]

# Valid stocks (not na in price and ema200)
valid_mask = last_price.notna() & last_ema200.notna()
valid_count = valid_mask.sum()

# Option A1: % stocks > EMA50
above_ema50 = (last_price[valid_mask] > last_ema50[valid_mask]).sum()
a1_score = above_ema50 / valid_count if valid_count > 0 else 0

# Option A2: % stocks where EMA50 > EMA200
ema50_above_ema200 = (last_ema50[valid_mask] > last_ema200[valid_mask]).sum()
a2_score = ema50_above_ema200 / valid_count if valid_count > 0 else 0

# 3. Get current regime components
try:
    with open('N750_regime_history.json', 'r') as f:
        history = json.load(f)
        latest = history[-1]
        breadth_score = latest['Breadth']
        momentum_score = latest['Momentum']
except Exception:
    breadth_score = 0.5
    momentum_score = 0.5

# Calculate exact current Nifty 500 EMA scores
px = nifty_series.dropna()
price_idx = px.iloc[-1]
ema50_idx = px.ewm(span=50, adjust=False).mean().iloc[-1]
ema200_idx = px.ewm(span=200, adjust=False).mean().iloc[-1]

import numpy as np
EMA50_BAND = 0.05
EMA_TREND_BAND = 0.10
curr_ema50_score = float(np.clip((price_idx / ema50_idx  - 1.0) / EMA50_BAND + 0.5, 0.0, 1.0))
curr_ema_trend_score = float(np.clip((ema50_idx / ema200_idx - 1.0) / EMA_TREND_BAND + 0.5, 0.0, 1.0))

# 4. Calculate New Composite
new_composite = (a1_score * 0.35) + (a2_score * 0.25) + (breadth_score * 0.25) + (momentum_score * 0.15)
old_calc_composite = (curr_ema50_score * 0.35) + (curr_ema_trend_score * 0.25) + (breadth_score * 0.25) + (momentum_score * 0.15)

print("\n--- What-If Analysis: Universe Breadth vs NIFTY 500 ---")
print(f"Total Valid Stocks Evaluated: {valid_count}")
print(f"Option A1 (Stocks > EMA50): {above_ema50} ({a1_score*100:.1f}%)")
print(f"Option A2 (EMA50 > EMA200): {ema50_above_ema200} ({a2_score*100:.1f}%)")

print("\n--- Component Score Comparison ---")
print(f"Distance Score : {curr_ema50_score:.3f} (NIFTY)  vs  {a1_score:.3f} (Universe A1)")
print(f"Trend Score    : {curr_ema_trend_score:.3f} (NIFTY)  vs  {a2_score:.3f} (Universe A2)")

print("\n--- Impact on Overall Regime Score ---")
print(f"Current Composite Score: {old_calc_composite:.3f}")
print(f"New Composite Score: {new_composite:.3f}")
print(f"Net Impact: {(new_composite - old_calc_composite):+.3f}")
