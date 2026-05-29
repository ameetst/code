# Breakout Trading Strategy Documentation

This document outlines the core logic, inputs, parameters, and expected outputs of the Daily Breakout Trading Strategy implemented in the accompanying Python/Streamlit application, as well as the complete algorithm for historical backtesting.

## 1. Strategy Overview

This strategy is a momentum and breakout-based trading system. It scans a broad universe of stocks to identify those in a solid uptrend, displaying strong relative momentum versus the broader market, and breaking out of their recent 3-month consolidation ranges while remaining below their longer-term 6-month highs. This combination specifically targets "early-stage" breakouts or "cup and handle" recoveries — stocks breaking out *before* the crowd notices them, with meaningful room to run toward the 6-month high.

The strategy includes strict volume confirmation, trend-smoothness checks, a liquidity floor, and a macro-market regime filter to minimise drawdowns during bearish market conditions. Candidates are ranked by 3-month relative strength versus the Nifty 500 index, so the strongest performers rise to the top when multiple signals fire on the same day.

---

## 2. Inputs & Data Sources

### Universe
- **Target Universe**: Nifty Total Market (approx. 750 stocks).
- **Source File**: `ind_niftytotalmarket_list (3).csv` containing the official NSE listing.

### Market Data
- **Provider**: Yahoo Finance (`yfinance` API).
- **Data Points**: Daily Open, High, Low, Close (OHLC), Adjusted Close, and Volume.
- **Lookback Period**: 1 year of historical daily data is fetched for the screener; 5–10 years for backtesting.

### Benchmark Index
- **Index**: Nifty 500 (`^CRSLDX`). Used for two purposes:
  1. Determining the overall market regime (Close vs 50EMA).
  2. Computing each stock's 3-month relative strength (`RS_3M`).

---

## 3. Core Indicators & Calculations

For every stock in the universe, the following indicators are calculated daily:

1. **Moving Averages**
   - `50EMA`: 50-day Exponential Moving Average.
   - `200EMA`: 200-day Exponential Moving Average.

2. **Breakout Levels (BO)**
   - Uses **calendar days** mapped to trading days (e.g., 90 calendar days ≈ 63 trading days).
   - *Lag*: A 7-calendar-day (5-trading-day) lag is applied to avoid comparing today's price to today's high.
   - `3M_High_Lagged`: Highest high over a 63-trading-day window, shifted back by 5 days.
   - `6M_High_Lagged`: Highest high over a 126-trading-day window, shifted back by 5 days.
   - `3M BO`: `(Current Close − 3M_High_Lagged) / 3M_High_Lagged`
   - `6M BO`: `(Current Close − 6M_High_Lagged) / 6M_High_Lagged`

3. **Risk/Reward Ratios**
   - `3M_Low_Lagged`: Lowest low over a 63-trading-day window, shifted back by 5 days.
   - `6M_Low_Lagged`: Lowest low over a 126-trading-day window, shifted back by 5 days.
   - `3M R/R`: `(3M_High_Lagged − Close) / (Close − 3M_Low_Lagged)` — upside to 3M resistance vs downside to 3M support.
   - `6M R/R`: `(6M_High_Lagged − Close) / (Close − 6M_Low_Lagged)` — the more relevant ratio for 3M breakout trades, as the 6M high is the natural price target. Negative values (price below range low) are treated as `NaN` and excluded.

4. **Volume Metrics**
   - `1M_MED_VOL`: Median volume over the last 21 trading days (~1 month).
   - `Prior_3M_MED_VOL`: Median volume over the 63 trading days *prior* to the last 1 month (shifted by 21 days).
   - `VCHK` (V_RANK): `1M_MED_VOL / Prior_3M_MED_VOL`. Values > 1.5 indicate a meaningful volume expansion relative to the stock's own baseline.
   - `INR_VOL`: Median daily traded value in INR over the last 21 days (`Volume × Close`). Used as an absolute liquidity floor.

5. **Proximity to 52-Week High**
   - `52W_High`: Highest high over the last 252 trading days.
   - `P/52H`: `Current Close / 52W_High`. Range 0–1; a value of 0.85 means the stock is within 15% of its 52-week high.

6. **Smoothness of Trend (R-Squared)**
   - Linear regression is performed on the closing prices of the last 63 trading days (~90 calendar days). The resulting R² value measures how steadily the stock is trending upward versus exhibiting erratic volatility. Range 0–1; higher is smoother.

7. **3-Month Relative Strength (RS_3M)**
   - `RS_3M`: `(Stock 3M return) − (Nifty 500 3M return)`, both computed as 63-day price returns.
   - Positive = stock is outperforming the index over the last 3 months.
   - Used as the primary **ranking signal** to sort candidates, not as a hard filter. This preserves early-stage breakouts that are just beginning to show leadership, while still surfacing the strongest performers first when multiple signals fire.

---

## 4. Trading Rules (Screener)

### A. Market Regime Filter (Macro Override)
Before evaluating individual stocks, the broader market regime is checked.
- **Rule**: Nifty 500 Current Close > Nifty 500 50EMA.
- **Action**: If False, the market regime is considered **BEARISH** and no new buy signals are generated. Existing positions are held until their individual trailing stop losses are triggered.

### B. Entry Criteria (ALL must be true)
If the market regime is Bullish, a stock must pass all of the following checks to generate a buy signal:

1. **Trend Alignment**: `Close > 50EMA > 200EMA`
2. **Breakout Context**: `3M BO > 0` AND `6M BO < 0`
   - Stock has broken above its 3-month range (short-term momentum confirmed) but remains below its 6-month high (not yet extended; room to run).
3. **Proximity to Breakout**: `3M BO < 0.10` (i.e., `Close < 1.10 × 3M_High_Lagged`)
   - Avoids chasing — entry must be within 10% of the breakout level.
4. **Volume Confirmation**: `VCHK > 1.5` (configurable)
   - Recent 1-month median volume must be at least 1.5× the prior 3-month baseline.
5. **Liquidity Floor**: `INR_VOL ≥ ₹1 Crore` median daily traded value.
   - Ensures the stock is practically tradeable at meaningful position sizes.
6. **Smoothness**: `R-Squared > threshold` (default 0.60, configurable)
   - Confirms the uptrend is steady rather than erratic.

### C. Signal Ranking (Tie-Breaker)
When multiple stocks pass all entry criteria on the same day, candidates are ranked by **RS_3M descending** — the stock with the strongest 3-month outperformance versus the Nifty 500 is prioritised. Falls back to P/52H descending if RS_3M data is unavailable.

### D. Exit Criteria
- **Trailing Stop Loss (TSL)**: 10% below the highest subsequent closing price since entry.
- `LOSS` column in the screener displays the absolute INR amount at risk per share at the current price (i.e., `Close × 0.10`).

---

## 5. Configurable Parameters (Sidebar)

All thresholds are adjustable at runtime via the Streamlit sidebar without requiring a data refresh:

| Parameter | Default | Description |
|---|---|---|
| R-Squared Threshold | 0.60 | Minimum trend smoothness to qualify |
| VCHK Threshold | 1.50 | Minimum volume expansion ratio |
| 6M R/R Threshold | 1.50 | Minimum risk/reward to 6M high vs 6M low |
| P/52H Threshold | 0.85 | Minimum proximity to 52-week high for green highlight |

---

## 6. Backtesting & Portfolio Construction Algorithm

To evaluate this strategy historically, a vectorised portfolio simulation will be used rather than isolated trade analysis.

### A. Signal Execution & Frictions
- **T+1 Execution**: Signals are generated using EOD Close prices; actual execution occurs at the **Open price of the next trading day (T+1)**.
- **Slippage & Brokerage**: A penalty of **0.15% per trade** is deducted for transaction costs.
- **Gap Risk**: If a stop loss is triggered but the stock gaps down below the stop level, the backtest forces exit at the **actual T+1 Open price**.
- **Corporate Actions**: Adjusted Close prices are used throughout to neutralise splits and dividends.

### B. Capital Allocation & Sizing
- **Portfolio Capacity**: Maximum of **10 concurrent positions**.
- **Position Sizing**: Equal weighting — each trade allocates exactly **10% of total current portfolio equity**.
- **Signal Ranking**: When more signals exist than available slots, candidates are ranked by **RS_3M descending** (strongest relative strength first).
- **Sector Caps**: Maximum of **2 open positions per sector** at any given time to prevent correlated drawdowns. *(Implementation pending — requires sector mapping data.)*

### C. Regime Management
- When the regime filter flips to **Bearish**, all new buy signals are blocked.
- Existing positions are held until their individual 10% trailing stop losses are triggered, gracefully moving the portfolio to cash during corrections.

---

## 7. App Execution & Outputs

The Streamlit app automates the daily signal generation:
1. Loads the universe of tickers from the local CSV.
2. Downloads/updates OHLCV data via Yahoo Finance (cached locally as `.pkl`).
3. Calculates all indicators including RS_3M vs Nifty 500 for every stock.
4. Displays the market regime status (Bullish/Bearish) with Nifty 500 close vs 50EMA.
5. **Buy Signals tab**: stocks passing all 6 entry criteria, sorted by RS_3M descending.
6. **All Stocks (DATA) tab**: full universe with colour-coded indicators and dual green/orange filter dropdowns for interactive exploration.

### DATA Tab Colour Legend
| Colour | Meaning |
|---|---|
| 🟢 Green | Cell passes its entry criterion at the configured threshold |
| 🟡 Orange | `6M BO < 0` — stock is below its 6M high (ideal breakout context) |
| No colour | Does not meet the criterion; no negative signal implied |
