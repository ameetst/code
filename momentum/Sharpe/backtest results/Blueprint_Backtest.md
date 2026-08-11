# Sharpe Momentum Strategy — Backtesting Architecture
### Simulation Engine Specifications (`backtest.py`)

## 1. Core Mechanics
The backtest is a **vectorised, point-in-time** historical simulation. At every rebalance date, the engine slices the historical prices up to that exact day, purposefully blinding itself to future data, and evaluates the current state of the market.

*   **Initial Capital:** Starts with a base equity of `2,000,000.0` INR.
*   **Compounding Approach:** Fully Compounding. At the end of every week, realized returns (minus friction) are fully reinvested.
*   **Rebalance Frequency:** Weekly (evaluated specifically on detected week-ends). A 252-day warm-up period is required to generate the first 12M Sharpe data.

---

## 2. Dynamic Market Regime (Continuous Filter)
The backtest mirrors the live script by abandoning binary BUY/CASH toggles in favor of a **Continuous Regime Score (0.0 to 1.0)**. 

### 2.1 Regime Score Calculation
The score evaluates four macro signals on the `NIFTY500` index:
1.  **EMA50 Distance (35%):** Nifty 500 price relative to its 50-day EMA.
2.  **EMA Trend (25%):** Alignment of the 50-day EMA vs the 200-day EMA.
3.  **Breadth (25%):** Percentage of the universe passing the -25% 52-week high filter.
4.  **Momentum Breadth (15%):** Percentage of eligible stocks with a strongly positive composite score (> 1.5).

### 2.2 Portfolio Action
The computed score drives two critical backtest parameters:
*   **Dynamic Portfolio Sizing (`dynamic_n`):** The target number of stock holdings scales linearly between `MIN_N = 5` (at 0.0 score) and `MAX_N = 25` (at 1.0 score).
*   **New Entry Gate:** New stocks are only purchased if the regime score is `>= 0.40`. If the score drops below this threshold, the portfolio enters "organic exit mode" — existing positions are sold if they fail exit criteria, but the resulting capital is parked in the Liquid Fund rather than being redeployed.

---

## 3. Position Management & Friction

### 3.1 Eligibility & Entry
*   **Risk Gate:** Stocks must have `PCT_FROM_52H >= -25%`.
*   **Ranking:** Ranked dynamically by the `COMPOSITE` (equal-weighted Z-score of the 12M, 9M, 6M, and 3M Sharpe ratios).
*   **Entry:** Top available candidates are added until `dynamic_n` slots are filled.

### 3.2 Hysteresis & Exit Logic
To minimize "whipsaw" trading costs, strict holding rules are simulated:
*   **Trigger 1 (Emergency Exit):** If a stock breaches the `-25%` 52-week high barrier, it is exited immediately.
*   **Trigger 2 (Rank Decay):** An incumbent is exited if its rank drops to **41 or worse**, AND it has been held for at least **28 calendar days**. If the 28-day lock is still active, the position is held regardless of rank.

### 3.3 Position Sizing & Cash Buffer
*   **Volatility Sizing:** Allocations are determined using inverse-volatility weighting across the trailing 252 days.
*   **5% Hard Cap:** No single stock is permitted to exceed a 5% allocation at rebalance.
*   **Cash Buffer:** Any unallocated capital (due to the 5% cap, or a low `dynamic_n` in weak regimes) is allocated to a theoretical **Liquid Fund** yielding `6% p.a.` (compounded weekly).

### 3.4 Friction and Slippage Modeling
The backtest dynamically and aggressively penalizes the portfolio for trading turnover.
*   **Trading Cost:** Modeled at **0.20% per trade** (20 basis points).
*   **Round-Trip Cost:** Every complete position swap costs **0.40%** in combined transaction and slippage drag.
*   **Calculation:** At every rebalance, the exact absolute weight change is calculated across the portfolio, and `abs_weight_change * 0.20%` is subtracted directly from the week's gross return.

---

## 4. Benchmark Tracking
*   **The Yardstick:** `NIFTY500` index.
*   **Methodology:** The NIFTY500's daily price closes are tracked simultaneously on a compounding curve starting at 2,000,000. The benchmark curve does *not* suffer transaction friction, simulating a theoretical, frictionless buy-and-hold index tracker.

---

## 5. Artifact Output
At the conclusion of a sequence, the engine autonomously creates an archival record of the simulation:
1.  **Directory:** Generates a timestamped root folder (e.g., `backtest results/run_YYYY-MM-DD_HH-MM-SS`).
2.  **Trade Log:** Saves a high-fidelity CSV (`backtest_results.csv`) logging every week's equity value, Nifty value, regime score, turnover penalty, eligible universe count, and the exact comma-separated list of the tickers held.
3.  **Visual Graph:** Compiles a `.png` chart tracking the Strategy Equity vs Benchmark Equity.
4.  **Logic Archive:** Duplicates the exact `backtest.py` python script into the folder to prevent future confusion regarding which parameters generated the results.
