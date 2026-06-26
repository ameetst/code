import pandas as pd
import datetime

# Load data
print("Loading data...")
trades = pd.read_csv('backtest_trades.csv')
trades['Entry Date'] = pd.to_datetime(trades['Entry Date'])
trades['Exit Date'] = pd.to_datetime(trades['Exit Date'])
trades = trades.sort_values(by=['Ticker', 'Entry Date']).reset_index(drop=True)

market_data = pd.read_pickle('ema_historical_data.pkl')

total_reentries = 0
trade1_losses = 0
trade1_losses_with_peak_profit = 0
trade2_wins_after_trade1_loss = 0
trade2_wins_overall = 0

print("Analyzing trades...")
for i in range(len(trades) - 1):
    t1 = trades.iloc[i]
    t2 = trades.iloc[i+1]
    
    if t1['Ticker'] == t2['Ticker'] and t2['Entry Date'] > t1['Exit Date']:
        total_reentries += 1
        
        # Was trade 1 a loss?
        is_t1_loss = t1['Return (%)'] <= 0
        if is_t1_loss:
            trade1_losses += 1
            
            # Find peak price during t1
            ticker_data = market_data[t1['Ticker'] + '.NS'] if t1['Ticker'] + '.NS' in market_data.columns.levels[0] else None
            
            # If MultiIndex (which it is), ticker_data is already a DataFrame for that ticker
            if ticker_data is not None:
                # Slice data between entry and exit
                mask = (ticker_data.index >= t1['Entry Date']) & (ticker_data.index <= t1['Exit Date'])
                hold_data = ticker_data.loc[mask]
                
                if not hold_data.empty:
                    peak_price = hold_data['High'].max()
                    if peak_price > t1['Entry Price']:
                        trade1_losses_with_peak_profit += 1
            
            # Did trade 2 win after trade 1 loss?
            if t2['Return (%)'] > 0:
                trade2_wins_after_trade1_loss += 1
                
        # Did trade 2 win overall?
        if t2['Return (%)'] > 0:
            trade2_wins_overall += 1

print("\n--- DEEP DIVE ANALYTICS ---")
print(f"Total Re-entry Pairs Analyzed: {total_reentries}")

print(f"\n1. First Trade Losses: {trade1_losses} out of {total_reentries} first trades ended in a loss ({(trade1_losses/total_reentries)*100:.2f}%).")

if trade1_losses > 0:
    print(f"2. Fakeouts (Winners turned Losers): Of those {trade1_losses} losses, {trade1_losses_with_peak_profit} actually hit a peak price higher than the entry price before crashing to stop out ({(trade1_losses_with_peak_profit/trade1_losses)*100:.2f}%).")

print(f"3. Second Trade Win Rate (Overall): {trade2_wins_overall} of the {total_reentries} re-entries were winners ({(trade2_wins_overall/total_reentries)*100:.2f}%).")

if trade1_losses > 0:
    print(f"4. Second Trade Win Rate (After T1 Loss): When the first trade was a loss, the subsequent re-entry won {trade2_wins_after_trade1_loss} times out of {trade1_losses} ({(trade2_wins_after_trade1_loss/trade1_losses)*100:.2f}%).")

