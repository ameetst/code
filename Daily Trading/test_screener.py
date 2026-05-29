import pandas as pd
from strategy import run_screener

try:
    market_data = pd.read_pickle('market_data.pkl')
    index_data = pd.read_pickle('index_data.pkl')
    print("Data loaded. Testing run_screener...")
    res, regime = run_screener(market_data, index_data)
    print("Success. Regime:", regime)
    print("Found", len(res), "results")
except Exception as e:
    import traceback
    traceback.print_exc()
