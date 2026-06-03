# EMA Crossover Momentum Strategy

This document outlines the core rules and algorithm for the EMA Crossover Screener and Portfolio Backtester.

## 1. Strategy Overview
This is a long-only trend-following and momentum breakout strategy. It aims to identify stocks entering a new medium-term uptrend while maintaining strong positive momentum and trading near their 52-week highs.

## 2. Core Indicators
The strategy relies on the following daily calculations:
- **Fast EMA (21-day):** Short-term trend measure.
- **Slow EMA (63-day):** Medium-term trend measure.
- **Trend Filter (200-day EMA):** Long-term trend measure.
- **52-Week High (`High_52W`):** Rolling maximum of the `High` price over the past 252 trading days.
- **63-Day High (`High_63D`):** Rolling maximum of the `High` price over the past 63 trading days.
- **63-Day Momentum:** The percentage return over the last 63 trading days (`Close / Close.shift(63) - 1`).
- **P/52H:** Proximity to the 52-week high (`Close / High_52W`).

## 3. Trading Rules

### Entry Rules (ALL must be true)
To trigger a **BUY** signal, a stock must meet all of the following conditions on the same day:
1. **Trend Reversal (Crossover):** The 21-day EMA must cross *above* the 63-day EMA today.
2. **Long-Term Uptrend:** The current `Close` price must be strictly greater than the 200-day EMA.
3. **Positive Momentum:** The stock must have a positive return over the last 63 trading days (`Momentum_63 > 0`).
4. **Proximity to Highs:** The stock must be trading within a configurable threshold of its 52-week high (Default: `P/52H >= 0.75`).
5. **Recent Breakout:** The stock's 52-week high must have been achieved within the last 63 trading days (`High_52W == High_63D`).

### Exit Rules (Stop-Loss & Profit-Taking)
This strategy **does not use a fixed profit target** (e.g., +15% or +20%). Instead, the exit rule is entirely dynamic and acts as both a trailing stop-loss and a profit-taking mechanism.

A trade is closed out when the trend completely breaks down:
1. **Trend Breakdown:** The 21-day EMA crosses *below* the 63-day EMA.

**How it works in practice:**
- **The Ride:** As a stock climbs aggressively, both the 21-day and 63-day EMAs follow the price upwards. Because it's a strong trend, the fast 21-day EMA stays comfortably above the slow 63-day EMA.
- **Taking Profit:** Eventually, the trend exhausts itself and the stock price begins to fall. As it falls, the 21-day EMA hooks downwards. The moment that 21-day EMA crosses back below the 63-day EMA, the position is closed to lock in the profits gained during the run. 

By not capping the upside, the system allows "super-winners" to keep running for months at a time, which mathematically compensates for the smaller, more frequent losses taken when a breakout fails.

## 4. Portfolio Backtesting Algorithm
The `ema_crossover_backtest.py` script simulates historical performance using the following parameters:
- **Initial Capital:** ₹1,000,000
- **Max Open Positions:** 20 concurrent trades.
- **Position Sizing:** Equal weight. Each new trade is allocated exactly **5%** of the *current total portfolio equity* (Cash + Mark-to-Market value of open positions).
- **Signal Ranking:** If multiple entry signals trigger on the same day and there are insufficient open slots (e.g., you only have 2 open slots but 5 stocks triggered a Buy), the candidates are ranked descending by their **63-Day Momentum**, prioritizing the strongest moving stocks.
- **Execution:** Signals are generated on the EOD `Close` price, and trades are simulated at that same price.
- **Benchmark:** Strategy equity curve is plotted against the Nifty Midcap 100 ETF (`MOM100.NS`) index for an apples-to-apples performance comparison.

## 5. Daily Execution Guide (Using `strategy.py`)
To trade this strategy live, use the provided Streamlit screener app (`strategy.py`).

### When to Run the Screener
- Run the screener using `streamlit run strategy.py` in your terminal.
- **Timing:** Execute the screener either exactly at the market close (3:30 PM IST) or shortly after, so that the daily closing prices are finalized. 
- *Optional:* You can run it around 3:15 PM if you wish to take positions before the market closes on the same day (T+0 entry), though the backtest strictly assumes EOD prices.

### How to Execute Trades (Entries)
1. In the Streamlit app, review the **"BUY Signals"** table.
2. If there are valid BUY signals, enter the trades **before the market closes on the same day** (e.g., between 3:15 PM and 3:25 PM). This perfectly replicates the backtest logic which assumes execution at the End of Day (EOD) Close price. 
3. **Position Sizing:** Allocate exactly **5%** of your current total portfolio value to each new trade. Do not exceed a maximum of 20 concurrent open positions.
4. **Ranking:** If you have only 2 open portfolio slots but the screener gives you 5 buy signals, prioritize the stocks with the highest **63-Day Momentum**.

### How to Manage Open Positions (Exits)
1. You must monitor your active holdings daily.
2. The screener's main data table provides the real-time status of the EMAs for all stocks. 
3. If the 21-day EMA for a stock you currently hold crosses **below** the 63-day EMA (Trend Breakdown), you must sell the entire position **before the market closes on the same day** to lock in your profits or cut your losses, perfectly aligning with the backtest.
