import pandas as pd

market_data = pd.read_pickle('market_data.pkl')

print("Type:", type(market_data))
print("Shape:", market_data.shape)
print("Columns type:", type(market_data.columns))

if isinstance(market_data.columns, pd.MultiIndex):
    print("MultiIndex levels:", market_data.columns.names)
    print("Level 0 sample (first 5):", market_data.columns.get_level_values(0).unique()[:5].tolist())
    print("Level 1 sample (first 5):", market_data.columns.get_level_values(1).unique()[:5].tolist())
    
    # Try extracting one ticker both ways
    lvl1_tickers = market_data.columns.levels[1].tolist()
    lvl0_tickers = market_data.columns.levels[0].tolist()
    print(f"\nLevel 0 count: {len(lvl0_tickers)}")
    print(f"Level 1 count: {len(lvl1_tickers)}")
    
    # Try to get a stock using level 1 (current code)
    test_ticker = lvl1_tickers[0]
    print(f"\nTrying xs('{test_ticker}', level=1)...")
    try:
        df = market_data.xs(test_ticker, axis=1, level=1)
        print(f"  Success! Shape: {df.shape}, Columns: {df.columns.tolist()}")
    except Exception as e:
        print(f"  Failed: {e}")
    
    # Try to get a stock using level 0
    test_ticker0 = lvl0_tickers[0]
    print(f"\nTrying xs('{test_ticker0}', level=0)...")
    try:
        df = market_data.xs(test_ticker0, axis=1, level=0)
        print(f"  Success! Shape: {df.shape}, Columns: {df.columns.tolist()}")
    except Exception as e:
        print(f"  Failed: {e}")
    
    # Show raw column samples
    print("\nFirst 10 columns (raw):")
    for c in market_data.columns[:10]:
        print(f"  {c}")
else:
    print("Not a MultiIndex")
    print("Columns:", market_data.columns.tolist()[:10])
