import crossover
import pandas as pd

tickers = crossover.get_tickers_from_csv("tickers.csv")
print(f"Total tickers: {len(tickers)}")

signals = crossover.check_daily_signals(tickers, threshold=0.75, max_lookback=21, enable_p52h=False, enable_lookback=True, enable_liquidity=True)

print(f"Total signals found: {len(signals)}")
aegis = [s for s in signals if s['Ticker'] == 'AEGISLOG']
print(f"AEGISLOG in signals? {bool(aegis)}")
if aegis:
    print(aegis[0])
