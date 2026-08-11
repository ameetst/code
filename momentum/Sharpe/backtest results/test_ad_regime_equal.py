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
ad_score = adv / (adv + dec) if (adv + dec) > 0 else 0.5

# 2. Re-calculate the other 3 components exactly as Sharpe.py does
px = nifty_series.dropna()
ema50 = px.ewm(span=50, adjust=False).mean().iloc[-1]
ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]

EMA_TREND_BAND = 0.10
ema_trend_score = float(max(0.0, min(1.0, (ema50 / ema200 - 1.0) / EMA_TREND_BAND + 0.5)))

# For breadth and momentum, we can use the N750_rankings.xlsx to approximate
df = pd.read_excel('N750_rankings.xlsx', sheet_name='CALCS', skiprows=1)
eligible = df[df['RANK'].notna()]
total_stocks = len(df)
elig_count = len(eligible)

breadth_score = elig_count / total_stocks if total_stocks > 0 else 0.5

elig_comp = eligible['RES_MOM']
pos_mom = (elig_comp > 1.5).sum()
momentum_score = pos_mom / max(1, elig_count)

# Current weights: ema50 (0.35), ema_trend (0.25), breadth (0.25), momentum (0.15)
# Wait, let's verify if my approximation matches the actual current composite.
curr_ema50_score = 1.0 # From previous run
curr_composite_calc = (1.0 * 0.35) + (ema_trend_score * 0.25) + (breadth_score * 0.25) + (momentum_score * 0.15)

# New Weights: 25% each
new_composite = (ad_score * 0.25) + (ema_trend_score * 0.25) + (breadth_score * 0.25) + (momentum_score * 0.25)

print("\n--- What-If Analysis: Equal Weight (25%) + A/D Ratio ---")
print(f"1. A/D Ratio Score (replaces EMA50): {ad_score:.3f}")
print(f"2. EMA Trend (50v200): {ema_trend_score:.3f}")
print(f"3. 52H Breadth: {breadth_score:.3f}")
print(f"4. Momentum Breadth (>1.5): {momentum_score:.3f}")

print(f"\nCalculated Current Composite (approx): {curr_composite_calc:.3f}")
print(f"New Equal-Weighted Composite: {new_composite:.3f}")
