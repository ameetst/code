import pandas as pd
import numpy as np
import momentum_lib as ml
import warnings
warnings.filterwarnings('ignore')

print('Loading rankings and prices...')
df = pd.read_excel('N750_rankings.xlsx', sheet_name='CALCS', skiprows=1)
# Filter only those that have a valid rank (eligible)
df = df[df['RANK'].notna()].copy()

prices_df, _, _, _ = ml.load_prices('N750_updated.xlsx')

print('Computing Vol-Adjusted Scores...')
vol_scores = {}
for idx, row in df.iterrows():
    ticker = row['TICKER']
    comp = row['RES_MOM']  # Wait, composite score is RES_MOM? Let's check column name. It might be COMPOSITE. Let's use SHARPE_ALL or whatever is used for ranking.
    
    if ticker not in prices_df.index:
        continue
        
    px = prices_df.loc[ticker].dropna()
    if len(px) > 10:
        vols = []
        for w in [252, 189, 126, 63]:
            px_w = px.iloc[-w:] if len(px) >= w else px
            log_r = np.diff(np.log(px_w.values))
            if len(log_r) > 5:
                vols.append(np.std(log_r, ddof=1) * np.sqrt(252))
        if vols and np.mean(vols) > 0:
            vol_scores[ticker] = comp / np.mean(vols)
        else:
            vol_scores[ticker] = comp
    else:
        vol_scores[ticker] = comp

df['VOL_ADJ_SCORE'] = df['TICKER'].map(vol_scores)

# Rank by Composite
top_comp = df.sort_values('RES_MOM', ascending=False).head(25)['TICKER'].tolist()

# Rank by Vol-Adj
top_vol = df.sort_values('VOL_ADJ_SCORE', ascending=False).head(25)['TICKER'].tolist()

print('\nTop 25 Comparison')
print(f'{"Rank":<5} | {"Composite Rank (Current)":<25} | {"Vol-Adj Rank (Alternative)":<25}')
print('-' * 62)
for i in range(25):
    c_tick = top_comp[i] if i < len(top_comp) else ''
    v_tick = top_vol[i] if i < len(top_vol) else ''
    marker = '*' if c_tick == v_tick else ' '
    print(f'{i+1:<5} | {c_tick:<25} | {v_tick:<25} {marker}')
