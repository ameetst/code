import pandas as pd
import numpy as np
from scipy.stats import skew, kurtosis

# 1. LOAD DATA
# Adjust range A1:IE503 as needed to cover all ticker rows and date columns
df = xl("DATA!A1:IE503", headers=True)

# 2. CLEANING: Remove Global Trading Holidays
# If every ticker has a 0 for a specific date, it's a holiday.
price_history = df.iloc[:, 3:]
is_holiday = (price_history == 0).all(axis=0)
clean_history = price_history.loc[:, ~is_holiday]

# Rebuild dataframe with cleaned history columns
df_clean = pd.concat([df.iloc[:, :3], clean_history], axis=1)

# 3. INITIAL FILTER: Within 25% of 52-week High
# Column B (Index 1) = Price, Column C (Index 2) = 52wk High
price_col = pd.to_numeric(df_clean.iloc[:, 1], errors='coerce')
high_col = pd.to_numeric(df_clean.iloc[:, 2], errors='coerce')
mask = (price_col / high_col).fillna(0) >= 0.75
filtered_df = df_clean[mask].copy()

# 4. CALCULATION LOGIC
def get_metrics(row):
    # History starts from Column D (Index 3). Handle pre-listing 0s as NaN.
    prices = pd.to_numeric(row.iloc[3:], errors='coerce').replace(0, np.nan).dropna()
    
    # Need at least 63 trading days (approx 3m) to compute metrics
    if len(prices) < 63:
        return pd.Series([np.nan]*5)
    
    rets = prices.pct_change().dropna()
    
    # Sharpe Ratio Helper (Annualized)
    def shp(r, days):
        sub = r.tail(days)
        if len(sub) < 5 or sub.std() == 0: return 0.0
        return (sub.mean() / sub.std()) * np.sqrt(252)

    return pd.Series([
        shp(rets, 252), # S12
        shp(rets, 126), # S6
        shp(rets, 63),  # S3
        skew(rets), 
        kurtosis(rets)
    ])

# 5. COMPUTE & APPLY SECONDARY FILTER
metric_names = ['S12', 'S6', 'S3', 'Skew', 'Kurt']
if not filtered_df.empty:
    filtered_df[metric_names] = filtered_df.apply(get_metrics, axis=1)
    
    # --- QUALITY FLOOR ---
    # Removes stocks with a 3m Sharpe below 1.5 to ensure trend heat
    filtered_df = filtered_df[filtered_df['S3'] >= 1.5].dropna(subset=['S12'])

    if not filtered_df.empty:
        # 6. Z-SCORE CALCULATION
        def z_score(series):
            return (series - series.mean()) / series.std()

        z_cols = ['Z_S12', 'Z_S6', 'Z_S3', 'Z_Skew', 'Z_Kurt']
        for i, col in enumerate(metric_names):
            filtered_df[z_cols[i]] = z_score(filtered_df[col])

        # 7. FINAL SCORING
        # Weights: Sharpe (0.33 each) | Skew (+0.10) | Kurtosis (-0.05)
        filtered_df['Final_Score'] = (
            (filtered_df['Z_S12'] * 0.33) + 
            (filtered_df['Z_S6'] * 0.33) + 
            (filtered_df['Z_S3'] * 0.33) + 
            (filtered_df['Z_Skew'] * 0.10) - 
            (filtered_df['Z_Kurt'] * 0.05)
        )

        # 8. OUTPUT
        output_cols = [filtered_df.columns[0], 'Final_Score'] + metric_names + z_cols
        result = filtered_df[output_cols].sort_values(by='Final_Score', ascending=False)
    else:
        result = "No Stocks Passed S3 Floor"
else:
    result = "No Stocks Passed 52wk High Filter"

result.reset_index(drop=True)