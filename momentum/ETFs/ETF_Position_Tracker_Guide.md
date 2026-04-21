# ETF Momentum Position & Rebalance Tracker

> **Core Engine:** `etf_momentum_ranking.py`
> **Data Store:** `holdings_log.json`
> **Output Report:** `"Rebalance"` sheet in `etf_rankings.xlsx`

There is no need for a separate tracking script. The position tracking and monthly diff logic is **fully integrated** into the main ranking script. Whenever you run the script, it automatically logs its current decisions, compares them to the last recorded month, and generates detailed tracking reports.

---

## 1. How the Tracker Works Internally

The system leverages a local JSON database (`holdings_log.json`) designed specifically for monthly rebalance workflows. 

### The Logging Sequence
1. **Compute Allocation:** The script calculates the optimal allocation for today.
2. **Read History:** It opens `holdings_log.json` to find the allocation belonging to the most recently logged month.
3. **Compare (Diff Engine):** By comparing the two allocations ticker-by-ticker, it determines the exact actions required:
   - `[+ BUY]` : Ticker is new to the portfolio.
   - `[- SELL]` : Ticker dropped out of top ranks or regime forced a cash exit.
   - `[^ ADD]` : An existing position's weight increased (e.g., from 20% to 33%).
   - `[v TRIM]` : An existing position's weight decreased.
   - `[= HOLD]` : Position maintained without changes.
   - `[! REGIME]`: Regime state flipped (e.g. BULL -> PARTIAL -> BEAR).
4. **Append & Save:** The new allocation is saved to the JSON file using the current `YYYY-MM` as a key. If you run the script multiple times in the same month, the latest run simply overwrites the snapshot for that month.

---

## 2. The Excel Output ("Rebalance" Sheet)

The script automatically generates a dedicated **Rebalance** tab inside `etf_rankings.xlsx`. This serves as the master dashboard for portfolio execution, split into three sections.

### Section 1: Current Allocation
Displays exactly what your portfolio should look like today.
- Active slots vs Cash slots.
- Weights (equal weighting distributed across active slots).
- Assigned Action (Buy, Hold, etc.) based on the diff.

### Section 2: Changes vs Previous Month
This is your **actionable trade blotter**. It lists only the ETFs where action is required.
- **Previous Weight vs Current Weight:** Instantly shows how many slots to add or trim.
- **Rank Drift:** If a held ETF starts slipping in ranks (e.g., Rank 1 -> Rank 4), it notifies you with a rank drift warning label `(-3)`. 
- **Color Coded:** Green for Buys, Red for Sells, Amber for Trims.

### Section 3: Last 12 Months History
A persistent tracking grid that proves exactly how the system navigated the past year.
- Shows the past 12 recorded months sequentially.
- Logs the **Regime State** (Bull, Partial, Bear) matching each month.
- Tracks the weight of every ticker ever held over that timeframe.

---

## 3. Handling Live Execution

Because the tracking relies on `holdings_log.json`, **you must not delete this file**. 
- To **reset** the portfolio history entirely and start fresh, simply delete or rename `holdings_log.json`. The script will gracefully start logging from month 1 on the next run.
- Keep the script and JSON in the same directory (`/momentum/ETFs/`).
- The script uses the current month internally (`YYYY-MM`). You only need to run it once on the first trading day. The JSON file guarantees that state persists flawlessly until next month.
