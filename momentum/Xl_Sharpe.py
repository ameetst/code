import pandas as pd
import numpy as np
from scipy.stats import skew, kurtosis

# ─────────────────────────────────────────────────────────────────────────────
# 1. LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
# Adjust range A1:IE503 as needed to cover all ticker rows and date columns
df = pd.DataFrame(xl("DATA!A1:IE503", headers=True)).copy()

# ─────────────────────────────────────────────────────────────────────────────
# 2. CLEANING: Remove Global Trading Holidays
# ─────────────────────────────────────────────────────────────────────────────
# If every ticker has 0 for a specific date, it's a global holiday — drop it.
# Force all columns to numeric: xl() can return raw Python bools/strings which
# break the (== 0).all(axis=0) comparison if pandas sees non-numeric dtypes.
price_history = df.iloc[:, 3:].apply(pd.to_numeric, errors='coerce')
is_holiday = (price_history == 0).all(axis=0)
clean_history = price_history.loc[:, ~is_holiday]

# Rebuild dataframe with cleaned history columns
df_clean = pd.concat([df.iloc[:, :3], clean_history], axis=1)

# ─────────────────────────────────────────────────────────────────────────────
# 3. INITIAL FILTER: Within 25% of 52-week High
# ─────────────────────────────────────────────────────────────────────────────
# Column B (Index 1) = Latest Price, Column C (Index 2) = 52wk High
price_col = pd.to_numeric(df_clean.iloc[:, 1], errors='coerce')
high_col  = pd.to_numeric(df_clean.iloc[:, 2], errors='coerce')
mask      = (price_col / high_col).fillna(0) >= 0.75
filtered_df = df_clean[mask].copy()

# ─────────────────────────────────────────────────────────────────────────────
# 4. CALCULATION LOGIC
# ─────────────────────────────────────────────────────────────────────────────
def get_metrics(row):
    # ── Pre-listing zero handling ────────────────────────────────────────────
    # FIX 1: Replace zeros with NaN but keep them IN POSITION so that
    # pct_change() across a gap also produces NaN (no phantom returns).
    # Do NOT call dropna() on prices before computing returns.
    prices = pd.to_numeric(row.iloc[3:], errors='coerce')

    # Replace leading zeros (pre-listing) with NaN, preserving position.
    arr = prices.values.astype(float)
    first_nz = next((i for i, v in enumerate(arr) if v != 0), None)
    if first_nz is None:
        return pd.Series([np.nan] * 5)
    arr[:first_nz] = np.nan        # pre-listing → NaN in-place
    arr[arr == 0]  = np.nan        # any remaining interior zeros → NaN
    prices = pd.Series(arr, index=prices.index)

    # Need at least 63 prices (≈ 3 months) to proceed at all
    if prices.notna().sum() < 63:
        return pd.Series([np.nan] * 5)

    # ── Returns: compute BEFORE dropping NaN so gaps produce NaN returns ─────
    rets = prices.pct_change()     # NaN prices → NaN returns across the gap
    rets = rets.dropna()           # drop NaN returns (gaps and first obs)

    # ── Sharpe Ratio Helper (Annualised) ─────────────────────────────────────
    # FIX 2: Return np.nan (not 0.0) for degenerate cases so the ticker is
    #         excluded from Z-score calculations rather than pulling the mean.
    # FIX 4: Adaptive minimum sample per period (matching Sharpe_momentum.py).
    def shp(r, days):
        sub     = r.tail(days)
        min_obs = max(10, days // 4)   # adaptive floor: 63 / 31 / 15
        if len(sub) < min_obs:
            return np.nan
        std = sub.std(ddof=1)
        if std == 0 or np.isnan(std):
            return np.nan
        return (sub.mean() / std) * np.sqrt(252)

    # ── FIX 3: Skew & Kurtosis — use bias-corrected estimators ───────────────
    skewness = skew(rets, bias=False)
    excess_k = kurtosis(rets, fisher=True, bias=False)   # excess kurtosis

    return pd.Series([
        shp(rets, 252),   # S12  — 12-month Sharpe
        shp(rets, 126),   # S6   — 6-month Sharpe
        shp(rets, 63),    # S3   — 3-month Sharpe
        skewness,
        excess_k,
    ])

# ─────────────────────────────────────────────────────────────────────────────
# 5. COMPUTE METRICS & APPLY QUALITY FLOOR
# ─────────────────────────────────────────────────────────────────────────────
metric_names = ['S12', 'S6', 'S3', 'Skew', 'Kurt']

if not filtered_df.empty:
    filtered_df[metric_names] = filtered_df.apply(get_metrics, axis=1)

    # --- QUALITY FLOOR ---
    # Remove stocks with a 3m Sharpe below 1.5 to ensure trend heat.
    # (Intentional — ensures only strongly trending stocks are ranked.)
    filtered_df = filtered_df[filtered_df['S3'] >= 1.5].dropna(subset=['S12'])

    if not filtered_df.empty:
        # ─────────────────────────────────────────────────────────────────────
        # 6. Z-SCORE CALCULATION (cross-sectional)
        # ─────────────────────────────────────────────────────────────────────
        def z_score(series):
            mu  = series.mean()
            sig = series.std(ddof=1)
            if sig == 0 or np.isnan(sig):
                return pd.Series(np.nan, index=series.index)
            return (series - mu) / sig

        z_cols = ['Z_S12', 'Z_S6', 'Z_S3', 'Z_Skew', 'Z_Kurt']
        for col, z_col in zip(metric_names, z_cols):
            filtered_df[z_col] = z_score(filtered_df[col])

        # ─────────────────────────────────────────────────────────────────────
        # 7. FINAL SCORING
        # Weights: Sharpe (0.33 each) | Skew (+0.10 tilt) | Kurt (−0.05 tilt)
        # ─────────────────────────────────────────────────────────────────────
        filtered_df['Final_Score'] = (
            (filtered_df['Z_S12']  * 0.33) +
            (filtered_df['Z_S6']   * 0.33) +
            (filtered_df['Z_S3']   * 0.33) +
            (filtered_df['Z_Skew'] * 0.10) -
            (filtered_df['Z_Kurt'] * 0.05)
        )

        # ─────────────────────────────────────────────────────────────────────
        # 8. OUTPUT — sorted descending by Final_Score
        # ─────────────────────────────────────────────────────────────────────
        output_cols = (
            [filtered_df.columns[0], 'Final_Score'] +
            metric_names +
            z_cols
        )
        result = (
            filtered_df[output_cols]
            .sort_values(by='Final_Score', ascending=False)
            .reset_index(drop=True)
        )
    else:
        result = "No stocks passed the S3 >= 1.5 quality floor."
else:
    result = "No stocks passed the 52-week High proximity filter."

result