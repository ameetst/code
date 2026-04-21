# Sharpe Momentum Strategy — Backtesting Architecture
### Simulation Engine Specifications (`backtest.py`)

## 1. Core Mechanics
The backtest is a **vectorized, point-in-time** historical simulation. At every rebalance date, the engine purposefully blinds itself to future data, slices the historical prices up to that exact day, and asks the ranking algorithm: *"What is the active regime, and what are the Top 20 stocks today?"*

*   **Initial Capital:** Base 100 (`equity = 100.0`). The test tracks pure percentage growth multiplier, agnostic of actual wallet size (e.g., if the equity curve ends at 510, the total return is 410%, or a 5.1x multiplier on any starting capital).
*   **Compounding Approach:** Fully Compounding. At the end of every month, realized returns (minus friction) are fully reinvested. The portfolio is constantly equal-weighted; meaning at every rebalance, the total compounded capital is split cleanly into 20 equal 5% buckets.
*   **Rebalance Frequency:** Monthly (executed strictly on the last chronological trading day of every month).

---

## 2. Market Regime Translation
The backtest translates `momentum_lib.py`'s macro market signals into hard allocation overrides.

### 2.1 The `CASH` Rule (Death Cross)
*   **Trigger:** NIFTY500 `EMA(50) < EMA(200)`
*   **Action:** 100% of the portfolio's compounded value is instantly dumped into cash.
*   **Performance Assumption:** While in CASH, the portfolio is mathematically credited with a `~0.16%` monthly return (calculating back from an estimated `2.0%` annualized yield of a conservative savings/liquid fund). 
*   **Friction:** The engine charges a 100% turnover friction cost to sell the whole portfolio, but no cost while it sits in cash.

### 2.2 The `NOT BUY` Rule (Entry Freeze)
*   **Trigger:** NIFTY500 `Price < EMA(50)` (but Death Cross hasn't triggered yet).
*   **Action:** Portfolio enters a defensive freeze. No new capital is deployed to snap up fresh stocks. 
*   **Execution:** Existing stocks are evaluated. If an existing stock drops below the Rank 40 exit buffer or crashes past the 52-Week High safety net, it is sold. The cash from that sale is *not* reinvested in new stocks; it sits idle alongside the remaining active portfolio until the regime flips back to `BUY`.

---

## 3. Position Management & Friction

### 3.1 The Rank 40 Hysteresis Buffer
The engine mimics institutional execution logic by not churning stocks that flutter temporarily. 
*   **Entry:** A new stock must rank between 1 and 20.
*   **Exit:** A portfolio incumbent is only forcibly sold if its rank drops to **41 or worse**. 

### 3.2 Friction and Slippage Modeling
Momentum strategies naturally demand high equity churn, which historically eats into gross returns. The backtest aggressively penalizes the portfolio for trading.
*   **Trading Cost:** Modeled at **0.20% per trade** (20 basis points).
*   **Round-Trip Cost:** Every stock swap (selling the outgoing loser + buying the incoming winner) costs **0.40%** in combined transaction and slippage drag.
*   **Dynamic Calculation:** At every rebalance, the engine calculates exact `Turnover` (what percentage of the 20 slots changed hands) and mathematically subtracts `Turnover * 0.40%` directly from the month's gross return.

---

## 4. Benchmark tracking
*   **The Yardstick:** `NIFTY500` index.
*   **Methodology:** The NIFTY500's daily price closes are tracked simultaneously on a Base 100 compounding curve. The NIFTY curve does *not* suffer transaction friction, simulating a theoretical, completely frictionless buy-and-hold index tracker to serve as an uncompromising baseline benchmark.

---

## 5. Artifact Output
At the conclusion of a sequence, the engine autonomously creates an archival record of the simulation:
1.  **Directory:** Generates a timestamped root folder (e.g., `backtest results/run_YYYY-MM-DD_HH-MM-SS`).
2.  **Trade Log:** Saves a high-fidelity CSV (`backtest_results.csv`) logging every single month's equity value, Nifty value, regime state, turnover penalty, and the exact comma-separated list of the 20 tickers held.
3.  **Visual Graph:** Compiles a `matplotlib` `.png` chart tracking the Strategy Equity vs Benchmark Equity, featuring highlighted vertical bands during months the protocol retreated to CASH.
4.  **Logic Archive:** Duplicates the exact `backtest.py` python script into the folder to prevent future confusion regarding which parameters generated the results.
