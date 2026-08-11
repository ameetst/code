import pandas as pd
import json
import momentum_lib as ml
import warnings
warnings.filterwarnings('ignore')

# 1. Calculate A/D Ratio
prices_df, nifty_series, stock_tickers, dates = ml.load_prices('N750_updated.xlsx')
last_two = prices_df.iloc[:, -2:]
rets = last_two.iloc[:, 1] - last_two.iloc[:, 0]
adv = (rets > 0).sum()
dec = (rets < 0).sum()

ad_ratio = adv / dec if dec > 0 else float('inf')
# Map to a 0.0 - 1.0 score (Advance / Total)
ad_score = adv / (adv + dec) if (adv + dec) > 0 else 0.5

# 2. Get the NIFTY500 and EMA50 Score
px = nifty_series.dropna()
price = px.iloc[-1]
ema50 = px.ewm(span=50, adjust=False).mean().iloc[-1]
import numpy as np
EMA50_BAND = 0.05
ema50_score = float(np.clip((price / ema50  - 1.0) / EMA50_BAND + 0.5, 0.0, 1.0))

# 3. Read current regime composite
try:
    with open('N750_regime_history.json', 'r') as f:
        history = json.load(f)
        latest = history[-1]
        curr_composite = latest['Composite']
except Exception:
    curr_composite = 0.5

# Calculate new composite by replacing the EMA50 component (35% weight)
# New = Old - (Old EMA50 * 0.35) + (AD Score * 0.35)
new_composite = curr_composite - (ema50_score * 0.35) + (ad_score * 0.35)

print("\n--- What-If Analysis: Replacing EMA50 with A/D Ratio ---")
print(f"1-Day Advancers: {adv} | Decliners: {dec}")
print(f"A/D Ratio: {ad_ratio:.2f}")
print(f"\nCurrent EMA50 Distance Score: {ema50_score:.3f}")
print(f"New A/D Breadth Score: {ad_score:.3f}")

print(f"\nCurrent Composite Regime Score: {curr_composite:.3f}")
print(f"New Composite Regime Score: {new_composite:.3f}")
print(f"Net Impact on Regime Score: {(new_composite - curr_composite):+.3f}")
