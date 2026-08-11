import pandas as pd
import warnings
warnings.filterwarnings('ignore')

try:
    df_n750 = pd.read_excel('N750_rankings.xlsx', sheet_name='CALCS', skiprows=1)
    df_n750 = df_n750[df_n750['RANK'].notna()]
    top50_n750 = df_n750.sort_values('RES_MOM', ascending=False).head(50)['TICKER'].tolist()

    df_all = pd.read_excel('NSEAll_rankings.xlsx', sheet_name='CALCS', skiprows=1)
    df_all = df_all[df_all['RANK'].notna()]
    top50_all = df_all.sort_values('RES_MOM', ascending=False).head(50)['TICKER'].tolist()

    set_n750 = set(top50_n750)
    set_all = set(top50_all)

    common = set_n750.intersection(set_all)
    only_n750 = set_n750 - set_all
    only_all = set_all - set_n750

    print(f"Top 50 Comparison: N750 vs NSEAll")
    print("-" * 50)
    print(f"Common Stocks in Top 50: {len(common)}")
    print(f"Different Stocks: {50 - len(common)}")
    print("-" * 50)
    
    print("\nStocks ONLY in N750 Top 50 (Pushed down by new additions):")
    # Sort them by their N750 rank
    only_n750_sorted = sorted(only_n750, key=lambda x: top50_n750.index(x))
    for ticker in only_n750_sorted:
        rank_n750 = top50_n750.index(ticker) + 1
        rank_all_idx = df_all.loc[df_all['TICKER'] == ticker, 'RANK']
        rank_all_str = str(int(rank_all_idx.values[0])) if not rank_all_idx.empty and not pd.isna(rank_all_idx.values[0]) else "N/A"
        print(f"  {ticker} (N750 Rank: {rank_n750} -> NSEAll Rank: {rank_all_str})")

    print("\nNew Stocks ONLY in NSEAll Top 50 (The new additions):")
    only_all_sorted = sorted(only_all, key=lambda x: top50_all.index(x))
    for ticker in only_all_sorted:
        rank_all = top50_all.index(ticker) + 1
        print(f"  {ticker} (New NSEAll Rank: {rank_all})")
        
except Exception as e:
    print(f"Error: {e}")
