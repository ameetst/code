# Momentum Stock Ranker — Project Blueprint (Updated)
### Based on: N750.xlsx (NSE ~750-stock universe)

## 1. System Overview
Multi-factor momentum ranking system on the NSE N750 universe.
1. Load daily closing prices (Inverted format: Tickers in rows, Dates in columns).
2. Compute multi-period Sharpe-like momentum scores (12M, 9M, 6M, 3M).
3. Z-score metrics cross-sectionally, weight them → **WAZS**.
4. Normalise within-universe → **NORM_WAZS**.
5. Apply Skewness and Kurtosis adjustments → **SCORE+SK**.
6. Apply EMA trend filters and Liquidity filters.
7. Surface top 20 for investment.
8. Portfolio allocation based on `WT_SCR` with a 5% cap per position.

---

## 2. Data Architecture

### 2.1 Input File Format (Inverted)
- **Column A**: NSE Ticker Symbol (e.g., `RELIANCE`, `TCS`).
- **Column B**: Type of stock (Largecap, Midcap Smallcap, Microcap) 
- **Column C**: `ATH` (All-Time High) value for the ticker.
- **Column D onwards**: Daily closing prices with Date headers.
- **Benchmark**: `NIFTY500` will be included as a row in the same file.

### 2.2 Universe
- ~741 tickers (Largecap, Midcap, Smallcap, Microcap).

---

## 3. Computation Pipeline

### Step 1: Load Price Data
- Simple price data only (No volume data for now).
- Benchmark: `NIFTY500` ticker row.

### Step 2: Multi-Period Returns and Volatility
- Windows: 252, 189, 126, 63 trading days.
- Returns and Volatility are **annualized**.
- **Skewness & Kurtosis**: Calculated on **simple returns** (12M window).

### Step 3: Sharpe-like Momentum
- `RFR`: 7.0% (Annualized).
- `Sharpe = (Annualized_Return - RFR) / Annualized_Vol`.

### Step 4: Z-Scores & WAZS
- Cross-sectional Z-scores for each period.
- **WAZS**: Equal weights for 12M, 9M, 6M, 3M (Parametrizable).

### Step 5: SCORE+SK (Adjusted Score)
- `SCORE+SK = WAZS + (0.1 * Z_SKEW) - (0.05 * Z_KURT)`.
- **Role**: Currently for display and analysis. Will be used in backtesting to compare performance against `N_WAZS`.

### Step 6: Normalization
- Normalise `WAZS` across the **entire universe** first to get `N_WAZS`.
- Normalise `SCORE+SK` across the entire universe to get `N_SCORE+SK`.

### Step 7: Filters & Eligibility (Applied after Normalization)
- **Trend Filter**: Price > 200 EMA of price.
- **High Filter**: Price must be within 25% of its 52-Week High (calculated dynamically from rolling 252 trading days).
- **Liquidity Filter**: (Logic ready for 12M Median Vol >= 1 CR).
- **Minimum Data**: Ticker must have at least 3 months (63 trading days) of price data to be eligible. If data is missing for a specific lookback period (e.g., 12M), the Z-score for that specific period is set to 0.

### Step 8: Final Ranking & Selection
- Rank eligible stocks by `N_WAZS` (Descending).
- Select **Top 20** stocks.
- **Alert**: System alert if more than 50% of the Top 20 allocation is skewed toward a single stock category (Largecap, Midcap, Smallcap, Microcap).

---

## 4. Portfolio & Execution

### 4.1 Position Sizing (WT_SCR)
- `WT_SCR = WAZS_ALL / WA_VOL`.
- **Initial Capital**: ₹20,00,000.
- **Allocation**: Proportional to the stock's `WT_SCR` relative to the total `WT_SCR` of the Top 20.
- **Constraint**: Maximum position size is **5%** of the portfolio value.

### 4.2 Rebalance Rules
- **Frequency**: Weekly (Every Monday).
- **Minimum Hold**: 1 Month (4 weeks).
- **Regime Exit Override**: If a Market Regime Exit is triggered, the 1-month minimum hold is ignored, and positions are closed immediately.

### 4.3 Market Regime Detection (NIFTY500)
- **Exit All**: If `NIFTY500 50EMA < NIFTY500 200EMA`.
- **No Fresh Positions**: If `NIFTY500 Price < NIFTY500 50EMA` (Monitor existing for exit only).

---

## 5. Development Priorities
1. **Core Logic**: Implement the ranking engine and validate against Excel results.
2. **Backtest**: Run historical simulations to track performance and trade logs.
3. **UI**: Build the dashboard only after the backtest is validated.

---

## 6. Decision Log (Confirmation Table)

| # | Parameter | Value |
|---|---|---|
| 1 | WAZS Weights | Equal (12/9/6/3) |
| 2 | RFR | 7% Annualized |
| 3 | Returns Type | Simple Returns (for Skew/Kurt) |
| 4 | Position Cap | 5% |
| 5 | Initial Capital | ₹20,00,000 |
| 6 | Rebalance | Weekly (Monday) |
| 7 | Min Hold | 1 Month (unless Regime Exit) |
| 8 | Benchmark | NIFTY500 (in-file) |
