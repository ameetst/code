import pandas as pd
import numpy as np

# Load trades
df = pd.read_csv('c:/Users/ameet/Documents/Github/code/Daily Trading/backtest_trades.csv')

wins = df[df['pnl'] > 0]
losses = df[df['pnl'] <= 0]

print("=== OVERALL METRICS ===")
print(f"Total Trades: {len(df)}")
print(f"Win Rate: {len(wins) / len(df) * 100:.2f}%")
print(f"Gross PnL: {df['pnl'].sum():.2f}")
print(f"Avg Win: {wins['pnl'].mean():.2f}")
print(f"Avg Loss: {losses['pnl'].mean():.2f}")
if len(losses) > 0:
    print(f"Reward/Risk Ratio: {wins['pnl'].mean() / abs(losses['pnl'].mean()):.2f}")

print("\n=== EXIT REASONS ===")
print(df.groupby('reason')['pnl'].agg(['count', 'sum', 'mean']))

if 'score' in df.columns:
    print("\n=== PERFORMANCE BY SCORE ===")
    print(df.groupby('score')['pnl'].agg(['count', 'sum', 'mean', lambda x: (x > 0).mean() * 100]).rename(columns={'<lambda_0>': 'Win %'}))
