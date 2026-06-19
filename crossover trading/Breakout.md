# Daily Breakout Strategy

## Strategy Algorithm Summary

This strategy systematically scans for high-momentum breakout opportunities in the market. The algorithm operates strictly on the following core rules, designed to identify the strongest market leaders and ride their momentum:

* **1. Market Regime Filter:** The screener will only generate signals if the broader market (Nifty 500) is in a confirmed Bullish Regime, meaning the Nifty 500 index must be trading above its own 50-day EMA.
* **2. Trend Alignment:** The individual stock must be in a highly established, long-term uptrend, defined mathematically as the current `Close > 50-day EMA > 200-day EMA`.
* **3. Proximity to Highs:** The stock must be trading near its absolute peaks. The closing price must be within 5% of both its **52-Week High** (`Close >= 52W High * 0.95`) and its **6-Month High** (`Close >= 6M High * 0.95`). *Note: These percentages are configurable in the live application.*
* **4. Momentum Leadership (Top 10%):** The algorithm filters for true market leaders by calculating the 3-Month Rate of Change (`ROC_3M`) for the entire universe, and strictly requiring the stock to be in the **Top 10 Percentile (90th percentile)** of performers on that given day.
* **5. Signal Ranking:** All qualifying breakout signals are ranked in descending order by their absolute 3-Month ROC, prioritizing the absolute strongest momentum stocks for entry.

---

## Technical Execution Details

### Core Indicators Calculated
- **50-Day & 200-Day EMA:** Defines the primary trend.
- **52-Week & 6-Month Highs:** 252-day and 126-day rolling maximums of the High price, used to ensure the stock is breaking out of a major high.
- **3-Month ROC:** `(Current Close / Close 63-Days Ago) - 1`. Used for both the cross-sectional percentile filter and final ranking.

### Trade Management (Live Specs)
- **Execution:** Run daily in the morning (e.g., 9:30 AM). The engine evaluates signals strictly using **T-1 (yesterday's finalized closing prices)** to avoid intra-day noise.
- **Position Sizing:** Max 10 concurrent positions (10% equity per trade).
- **Structural Stop Loss:** Positions are exited the morning after the stock's daily close falls below its **50-Day EMA**.
- **Safety Switch:** No new entries are taken if the Nifty 500 drops below its 50 EMA.
