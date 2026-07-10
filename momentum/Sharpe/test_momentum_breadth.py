import pandas as pd
import json

# Read current regime stats from Sharpe.py / dashboard's history
try:
    with open('N750_regime_history.json', 'r') as f:
        history = json.load(f)
        latest = history[-1]
        print("Current Regime Score:", latest['Composite'])
        print("Current Momentum Breadth:", latest['Momentum'])
        # Wait, the history only stores the final component scores, not the raw composite scores of all stocks.
except Exception as e:
    pass

# Let's read N750_rankings.xlsx to get the actual scores
df = pd.read_excel('N750_rankings.xlsx', sheet_name='CALCS', skiprows=1)

# In Sharpe.py, eligible_mask is usually PCT_FROM_52H >= -25 and ADTV_OK
# Since CALCS sheet has 'RANK' not na for eligible stocks, we can use that.
eligible = df[df['RANK'].notna()]
elig_comp = eligible['RES_MOM'] # Or COMPOSITE, whatever was used

current_pos_mom = (elig_comp > 1.5).sum()
elig_count = len(elig_comp)
current_mom_score = current_pos_mom / elig_count if elig_count > 0 else 0

median_score = elig_comp.median()
new_pos_mom = (elig_comp > median_score).sum()
new_mom_score = new_pos_mom / elig_count if elig_count > 0 else 0

print(f"\n--- Momentum Breadth Analysis ---")
print(f"Eligible Stocks count: {elig_count}")
print(f"Current threshold (1.5): {current_pos_mom} stocks passed -> Score: {current_mom_score:.3f}")
print(f"Median Score value: {median_score:.3f}")
print(f"New threshold (Median): {new_pos_mom} stocks passed -> Score: {new_mom_score:.3f}")

# Calculate impact on Regime Score
# Momentum weight is 15% (0.15)
diff_in_score = (new_mom_score - current_mom_score) * 0.15
print(f"\nImpact on Composite Regime Score: {(diff_in_score):+.3f}")
