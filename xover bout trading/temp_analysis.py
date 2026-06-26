import pandas as pd
import numpy as np

MARKET_DATA_FILE = "milt25_historical_data.pkl"
print(f"Loading data from {MARKET_DATA_FILE}...")
df = pd.read_pickle(MARKET_DATA_FILE)

tickers = df.columns.levels[0]

# Ensure timezone-naive
df.index = df.index.tz_localize(None)

returns = {}
for ticker in tickers:
    try:
        data = df[ticker].loc['2026-05-25':].dropna(subset=['Close'])
        if len(data) > 0:
            before_may_25 = df[ticker].loc[:'2026-05-24'].dropna(subset=['Close'])
            if len(before_may_25) > 0:
                base_price = before_may_25.iloc[-1]['Close']
            else:
                base_price = data.iloc[0]['Open']
            
            end_price = data.iloc[-1]['Close']
            ret = (end_price - base_price) / base_price * 100
            returns[ticker] = ret
    except Exception as e:
        pass

top_10 = sorted(returns.items(), key=lambda x: x[1], reverse=True)[:10]

print("Top 10 Gainers since May 25, 2026:")
for t, r in top_10:
    print(f"{t}: {r:.2f}%")

print("\n--- Analysis from May 15 to Present ---")
for t, r in top_10:
    data = df[t].loc['2026-05-15':].dropna(subset=['Close', 'Volume'])
    if len(data) == 0: continue
    
    pre_vol = data.loc[:'2026-05-24']['Volume'].mean()
    post_vol = data.loc['2026-05-25':]['Volume'].mean()
    vol_surge = post_vol / pre_vol if pre_vol > 0 else 1
    
    pre_prices = data.loc[:'2026-05-24']['Close']
    if len(pre_prices) > 0:
        consolidation = (pre_prices.max() - pre_prices.min()) / pre_prices.min() * 100
    else:
        consolidation = 0
        
    print(f"\n{t}: Return {r:.2f}%")
    print(f"  Volume Surge (Post-May25 vs Pre-May25): {vol_surge:.2f}x")
    print(f"  Pre-May25 Price Range (Volatility): {consolidation:.2f}%")
    
    full_data = df[t].dropna(subset=['Close'])
    ema10 = full_data['Close'].ewm(span=10, adjust=False).mean()
    ema20 = full_data['Close'].ewm(span=20, adjust=False).mean()
    ema50 = full_data['Close'].ewm(span=50, adjust=False).mean()
    
    try:
        may_25_idx = data.loc['2026-05-25':].index[0]
        p_may25 = full_data.loc[may_25_idx, 'Close']
        e10_may25 = ema10.loc[may_25_idx]
        e20_may25 = ema20.loc[may_25_idx]
        e50_may25 = ema50.loc[may_25_idx]
        
        # Determine trend alignment
        alignment = "Price > 10EMA > 20EMA > 50EMA" if (p_may25 > e10_may25 > e20_may25 > e50_may25) else "Not aligned"
        
        print(f"  On breakout date ({may_25_idx.date()}): Price={p_may25:.2f}, 20EMA={e20_may25:.2f}, 50EMA={e50_may25:.2f} -> {alignment}")
        
        # Analyze RSI
        delta = full_data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_may25 = rsi.loc[may_25_idx]
        print(f"  RSI on breakout date: {rsi_may25:.2f}")
        
    except:
        pass
