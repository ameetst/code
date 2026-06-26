import pandas as pd
import datetime

print("Loading data...")
trades = pd.read_csv('backtest_trades.csv')
trades['Entry Date'] = pd.to_datetime(trades['Entry Date'])
market_data = pd.read_pickle('ema_historical_data.pkl')

results = []

for idx, row in trades.iterrows():
    ticker = row['Ticker'] + '.NS'
    entry_date = row['Entry Date']
    
    if ticker in market_data.columns.levels[0]:
        df = market_data[ticker].copy()
        
        # Get data strictly prior to entry date
        prior_data = df[df.index < entry_date]
        if len(prior_data) >= 126:
            vol_21d = prior_data['Volume'].tail(21).mean()
            vol_126d = prior_data['Volume'].tail(126).mean()
            
            if vol_126d > 0:
                rv = vol_21d / vol_126d
                
                results.append({
                    'Ticker': row['Ticker'],
                    'Entry Date': entry_date.date(),
                    'Return (%)': row['Return (%)'],
                    'RV': rv
                })

results_df = pd.DataFrame(results)

if not results_df.empty:
    high_vol = results_df[results_df['RV'] > 1.25]
    normal_vol = results_df[results_df['RV'] <= 1.25]
    
    print("\n--- VOLUME SURGE ANALYTICS ---")
    print(f"Total Trades Analyzed: {len(results_df)}")
    
    print(f"\nBucket 1: High Volume Prior to Breakout (21-Day Avg Vol is >25% higher than 6-Month Avg Vol)")
    print(f"Count: {len(high_vol)}")
    if not high_vol.empty:
        print(f"Win Rate: {(len(high_vol[high_vol['Return (%)'] > 0]) / len(high_vol)) * 100:.2f}%")
        print(f"Avg Return: {high_vol['Return (%)'].mean():.2f}%")
        
        # Save to CSV for manual verification
        high_vol[['Ticker', 'Entry Date', 'RV', 'Return (%)']].to_csv('high_volume_surge_trades.csv', index=False)
        print("\nSaved 49 trades to high_volume_surge_trades.csv")
        
    print(f"\nBucket 2: Normal/Low Volume Prior to Breakout")
    print(f"Count: {len(normal_vol)}")
    if not normal_vol.empty:
        print(f"Win Rate: {(len(normal_vol[normal_vol['Return (%)'] > 0]) / len(normal_vol)) * 100:.2f}%")
        print(f"Avg Return: {normal_vol['Return (%)'].mean():.2f}%")
