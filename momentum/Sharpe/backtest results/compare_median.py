import pandas as pd
import momentum_lib as ml
import momentum_lib_robust as mlr
import warnings
warnings.filterwarnings('ignore')

print('Loading prices and current rankings...')
prices_df, nifty_series, stock_tickers, dates = ml.load_prices('N750_updated.xlsx')

current_df = pd.read_excel('N750_rankings.xlsx', sheet_name='CALCS', skiprows=1)
current_df = current_df[current_df['RANK'].notna()].copy()
top_current = current_df.sort_values('RES_MOM', ascending=False).head(25)['TICKER'].tolist()

print('Computing Robust Rankings (Median Z-Score)...')
windows={"12M": 252, "9M": 189, "6M": 126, "3M": 63}
resmom_df, rs_z_df = mlr.compute_residual_momentum(prices_df, stock_tickers, nifty_series, windows)
# We need to filter eligible just like the current script
eligible = current_df['TICKER'].tolist()
rs_z_df = rs_z_df.loc[rs_z_df.index.isin(eligible)]

top_robust = rs_z_df.sort_values('RES_MOM', ascending=False).head(25).index.tolist()

print('\nTop 25 Comparison')
print(f'{"Rank":<5} | {"Average Z-Score (Current)":<25} | {"Median Z-Score (Alternative)":<25}')
print('-' * 62)
for i in range(25):
    c_tick = top_current[i] if i < len(top_current) else ''
    v_tick = top_robust[i] if i < len(top_robust) else ''
    marker = '*' if c_tick == v_tick else ' '
    print(f'{i+1:<5} | {c_tick:<25} | {v_tick:<25} {marker}')
