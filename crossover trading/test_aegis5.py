import pandas as pd
from crossover import check_daily_signals

tickers = ["AEGISLOG.NS", "RELIANCE.NS"]
signals = check_daily_signals(tickers, threshold=0.75, max_lookback=21, enable_p52h=False, enable_lookback=True, enable_liquidity=True)
print("Signals returned:", signals)
