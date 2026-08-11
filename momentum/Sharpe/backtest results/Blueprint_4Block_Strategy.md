# The "4-Block" Momentum Strategy — Blueprint
### Universe: NSE Nifty 500 (`n500_bt.xlsx`)

## 1. The 4-Block Concept (The Edge)
Traditional momentum algorithms use overlapping historical windows (e.g., trailing 12M, 9M, 6M, and 3M). Because these periods overlap, the most recent 3 months are counted four separate times, massively overweighting short-term noise and causing portfolios to accidentally buy short-term high-beta spikes that quickly crash.

The **4-Block Algorithm** fixes this by breaking the trailing year into exactly **four non-overlapping, equally weighted quarters**. A stock must prove it generated smooth alpha in *all four distinct seasons* of the year to achieve a high rank.

---

## 2. Core Computations & Metrics

### 2.1 The Block Definitions
The historic 252 trading days are sliced linearly into four independent blocks:
*   **Block 1 (B1):** Most recent 3 months (Days 0 to 63)
*   **Block 2 (B2):** Previous 3 months (Days 64 to 126)
*   **Block 3 (B3):** The 3 months before that (Days 127 to 189)
*   **Block 4 (B4):** The oldest 3 months (Days 190 to 252)

### 2.2 Sharpe Ratios & Z-Scores
Within **each distinct block**, the algorithm computes the annualized Sharpe Ratio:
- **Risk-Free Rate (RFR)**: 7.0% Annualized.
- **Formula**: `(Annualized_Return_of_Block - RFR) / Annualized_Volatility_of_Block`
- **Z-Score**: The Sharpe ratio for Block 1 is Z-scored cross-sectionally against the entire 500-stock universe's Block 1 performance. (Repeated for B2, B3, and B4).

### 2.3 The Composite Metric (`SHARPE_ALL`)
- The final score is a strict **25% equal-weight mean** of the four Z-Scores (`Z_B1`, `Z_B2`, `Z_B3`, `Z_B4`). 
- This mathematically isolates stocks displaying persistent, steady uptrends across the entire year without rewarding recent luck.

---

## 3. Filters & Eligibility

### 3.1 Proximity to 52-Week High (Risk Gate)
- **Rule**: `PCT_FROM_52H >= -25%`
- Any stock whose current price drops more than 25% below its trailing 252-day peak is immediately disqualified (Rank `NaN`).

### 3.2 Macro Market Regime
Evaluated strictly on the `NIFTY500` index:
- **CASH RULE (Highest Priority)**: `EMA(50) < EMA(200)` *(The Death Cross)*
  - **Action**: Liquidate 100% of the equity portfolio immediately. Park all proceeds into a Liquid Fund (yielding an assumed ~2.0% p.a.). 
- **BUY Regime**: `Price > EMA(50)` AND `EMA(50) > EMA(200)`
  - **Action**: Fully allocate capital. Enter new stocks to fill Top 20 slots.
- **NOT BUY (Entry Freeze)**: `Price < EMA(50)` but EMA50 is still > EMA200
  - **Action**: Defensive freeze. Zero new stocks are purchased. Existing strong stocks are held, but cash from exiting losers is safely parked idle until the regime returns to `BUY`.

---

## 4. Execution Rules

### 4.1 Trade Management
- **Frequency**: Monthly (End of Month execution).
- **Portfolio Sizing**: 20 stocks maximum.
- **Position Sizing**: Equal-weighting (5% per position).
- **Transaction Friction**: Modeled at **0.20% per trade** (0.40% round-trip slippage/commissions).

### 4.2 The Rank 40 Hysteresis Buffer
To massively reduce "whipsaw" trading costs, a wide hysteresis net is applied:
- **Entry requirement**: A stock is only bought if it enters the **Top 20**.
- **Exit requirement**: Once bought, the stock is held until it formally plummets to **Rank 41** or worse. (Or if it triggers the `-25%` 52H safety net).

---

## 5. Performance Validation (Backtest Data)
*Simulated from April 2020 to March 2026 handling 0.20% transaction friction.*

| Metric | Traditional Overlapping (12/9/6/3) | Non-Overlapping (4-Block) | NIFTY500 Benchmark |
| :--- | :--- | :--- | :--- |
| **Strategy CAGR** | 31.5% | **37.2%** | 19.2% |
| **Max Drawdown** | -38.8% | **-27.4%** | -18.0% |

**Conclusion:** Splitting momentum calculation into non-overlapping blocks boosted annual return by over 5% while aggressively slashing drawdown risk, resulting in a substantially superior Alpha pipeline.
