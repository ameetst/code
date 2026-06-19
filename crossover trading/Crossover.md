# 50/200 EMA Crossover System

This document outlines the core rules and algorithm for the interactive EMA Crossover Screener and Trade Journal.

## 1. Strategy Overview
This is a medium-to-long-term trend-following system designed to catch major multi-month or multi-year trends. It systematically identifies stocks where the medium-term trend is crossing above the long-term trend, commonly known as a "Golden Cross."

## 2. Core Indicators
The strategy relies on the following daily calculations:
- **Fast EMA (50-day):** Represents the medium-term price trend.
- **Slow EMA (200-day):** Represents the long-term price baseline.
- **52-Week High (`High_52W`):** The highest closing price over the past year.
- **P/52H (Proximity Ratio):** The ratio of the current price to the 52-week high (`Close / High_52W`).

## 3. Trading Rules

### Entry Rules
To trigger a **BUY** signal, a stock must meet the following conditions:
1. **The Golden Cross:** The 50-day EMA must have crossed *above* the 200-day EMA.
2. **Lookback Window:** The exact crossover event must have occurred within the user-defined lookback window (e.g., within the last 0 to 21 trading days). 
3. **Proximity to Highs Filter:** The stock's current price must be within a user-defined threshold of its 52-week high (Default: `P/52H >= 0.75`, meaning the stock is no more than 25% below its 52-week peak).

### Exit Rules
The system employs a dynamic trailing exit rather than fixed profit targets.
1. **The Death Cross:** A trade is closed when the trend completely breaks down, defined as the 50-day EMA crossing *below* the 200-day EMA. 

## 4. Execution Workflow (Using `crossover.py`)

1. **Upload Universe:** Provide a `tickers.csv` file containing the universe of stocks to scan.
2. **Configure Screener:** Set your desired Lookback Window and Min P/52H Ratio on the Configuration tab.
3. **Run Screener:** The app fetches the latest 3 years of market data and scans for actionable signals.
4. **Log Trades:** Select actionable signals directly from the screener and log them into the Trade Journal. The app natively tracks your open and closed portfolio state.
5. **Live Monitor:** The Trade Journal actively monitors your open positions in real-time, automatically flagging any trades that trigger the "Death Cross" exit condition.
