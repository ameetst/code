# VCP Volume Breakout Strategy

## Overview
This strategy aims to capture explosive price moves by identifying stocks that have undergone a period of intense volatility contraction (VCP) and are suddenly experiencing massive institutional accumulation (volume surges) from a "rested" state (neutral RSI). 

## Rules

### 1. The Setup (Volatility Contraction)
The stock must be demonstrating extreme price tightness. 
- **Rule:** The High-to-Low range over the last 10 trading days must be **less than 8%**.
- **Why:** This indicates a complete dry-up of selling pressure and overhead supply. The stock is coiled like a spring. (A/B tested: 8% was the optimal threshold — 5% was too restrictive causing cash drag, 12% let in too much noise.)

### 2. The Condition (Rested RSI)
The stock must not be overextended prior to the breakout.
- **Rule:** The 14-period RSI must be between **40 and 60**.
- **Why:** Breakouts that start from a neutral state have far more "fuel" to run. If the RSI is already > 70, the move is often exhausted and prone to failure.

### 3. The Trigger (Institutional Volume Surge)
The breakout must be accompanied by an undeniable institutional footprint.
- **Rule:** Today's volume must be at least **3x (300%) greater** than the 20-day moving average of volume.
- **Rule:** The closing price must be higher than yesterday's close (an up day).
- **Why:** Retail traders cannot move a stock with 3x average volume. This is the footprint of large funds accumulating positions.

### 4. Position Sizing
We implement a strictly risk-managed approach to capital allocation.
- **Risk Per Trade:** 2% of total account equity.
- **Stop Loss:** -8% from the entry price.
- **Position Size Calculation:** `(Total Equity * 0.02) / (Entry Price * 0.08)`. This essentially allocates 25% of the account to each trade to risk 2%. We hold a maximum of 20 positions at any time.

### 5. Exit Strategy (Trailing Stop)
We use an asymmetric risk-to-reward system designed to cut losers quickly and let winners run.
- **Stop Loss:** Exit immediately if the price falls **8%** below the entry price.
- **Trailing Stop:** Exit the position at the next open if the daily closing price drops below the **21-day Exponential Moving Average (EMA)**.
- **Why:** By removing a fixed profit target, we allow the stock to trend for months if it catches a massive tailwind. The 21-EMA acts as a standard institutional "trend support" line. As long as it stays above this line, we hold.
