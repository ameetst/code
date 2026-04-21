# N500 Sharpe Momentum Strategy — Project Blueprint
### Universe: NSE Nifty 500 (`n500_bt.xlsx`)

---

## 1. System Overview
A systematic, multi-timeframe pure momentum ranking engine that isolates statistically smooth upward trends within the Nifty 500 universe, coupled with a macro regime filter and a structured exit framework.

1. **Data Loading**: Ingest daily closing prices, normalising missing data and benchmarking against `NIFTY500`.
2. **Multi-Window Sharpe**: Computes annualised Sharpe Ratios over 4 distinct horizons (12M, 9M, 6M, 3M).
3. **Cross-Sectional Z-Scoring**: Standardises Sharpe ratios within the daily universe to produce comparable scores (`Z_12M`, `Z_9M`, `Z_6M`, `Z_3M`).
4. **Composite Metric (`SHARPE_ALL`)**: Equal-weights the 4 Z-scored windows into a master momentum score.
5. **Residual Momentum (`RES_MOM`)**: Regresses stock returns against NIFTY500 to compute idiosyncratic (market-adjusted) trend strength. **Display only — not used in ranking or exit logic.**
6. **Risk Filter**: Disqualifies stocks more than 25% below their 52-Week High.
7. **Macro Regime Check**: Uses NIFTY500 EMAs to determine whether new entries are permitted.
8. **Exit Evaluation**: Applies two independent exit triggers against the position ledger each rebalance.
9. **Output**: Selects Top 20 eligible stocks, sizes positions by volatility weighting capped at 5%.

---

## 2. Core Computations & Metrics

### 2.1 Sharpe Ratios & Z-Scores
- **Windows Used (Trading Days)**: 12M (252), 9M (189), 6M (126), 3M (63).
- **Risk-Free Rate (RFR)**: 7.0% Annualised.
- **Formula**: `(Annualised_Return - RFR) / Annualised_Volatility`
- **Z-Score Normalisation**: For each window, the stock's Sharpe ratio is Z-scored cross-sectionally relative to the full universe on that date.

### 2.2 Composite Metric
- **`SHARPE_ALL`**: Primary ranking mechanism. Simple arithmetic mean of `Z_12M`, `Z_9M`, `Z_6M`, `Z_3M`. The 1M window is intentionally excluded to avoid short-term mean-reversion noise.
- **Normalisation**: Raw `SHARPE_ALL` (centred around 0) is passed through a non-linear scaling formula mapping it to a positive range (typically `[0, ~3.0]`) for readability.

### 2.3 Residual Momentum (`RES_MOM`)
- Each stock's returns are regressed against NIFTY500 via OLS. The Sharpe ratio of the residuals (market-beta-stripped returns) is computed across all four windows and Z-scored.
- `RES_MOM` = equal-weighted mean of the four residual Z-scores.
- **Role**: Display only. Shown in output for informational purposes. Not used in ranking, entry, or exit decisions.

---

## 3. Filters & Eligibility

### 3.1 Proximity to 52-Week High (Risk Gate)
- **Rule**: `PCT_FROM_52H >= -25%`
- **Logic**: Any stock whose current price is more than 25% below its trailing 252-day peak is immediately disqualified — `RANK` is set to `NaN`. This prevents buying falling knives regardless of historical Sharpe performance.

### 3.2 Macro Market Regime
Evaluated strictly on the `NIFTY500` index using two EMAs. Two states only:

| Regime | Condition | Action |
|---|---|---|
| **BUY** | `Price > EMA(50)` | New entries permitted. Exits evaluated normally. |
| **NOT BUY** | `Price <= EMA(50)` | New entries blocked. Existing positions monitored for exit. |

- There is no separate CASH / full-liquidation state. A market downturn is treated as NOT BUY — entries freeze, but existing positions are not force-sold.
- Only **EMA50** is used in the regime decision. EMA200 and all other moving averages are not involved.

---

## 4. Execution Architecture

### 4.1 Trade & Rebalance Rules
- **Frequency**: Weekly — evaluated at Friday close, executed Monday open.
- **Portfolio Size**: Top 20 stocks.
- **Position Sizing**: Volatility-adjusted weighting. Each position's weight = `COMPOSITE_SCORE / mean_volatility`, normalised across the Top 20, then hard-capped at **5% per position**. Residual unallocated capital is parked in a Liquid Fund at **6% p.a.**
- **Transaction Friction**: 0.20% per trade (0.40% round-trip).

### 4.2 Entry Criteria
A stock is eligible for entry only when **all three** conditions are met:
1. Ranked in the **Top 20** by `SHARPE_ALL`
2. Passes the **52-Week High filter** (`PCT_FROM_52H >= -25%`)
3. Market regime is **BUY**

No new entries are made in a NOT BUY regime.

### 4.3 Exit Criteria
Two independent exit triggers are evaluated every Monday rebalance. They are intentionally separate — the 52H exit overrides the hold lock; the rank exit respects it.

#### Trigger 1 — 52H Disqualification (Emergency Exit)
- **Condition**: `PCT_FROM_52H < -25%` → stock receives `RANK = NaN`
- **Action**: Exit immediately at next execution.
- **Hold lock**: Overridden. The 28-day lock does not apply.
- **Rationale**: A 52H breach is a structural disqualification, not a rank fluctuation. It warrants immediate exit regardless of how recently the stock was bought.

#### Trigger 2 — Rank-Based Exit
- **Condition**: `RANK > 40` AND position has been held for `>= 28 calendar days`
- **Action**: Exit at next execution.
- **Hold lock**: Enforced. If rank drops above 40 but the stock has been held fewer than 28 days, it is retained and re-evaluated the following week.
- **Rationale**: The Rank 40 buffer (hysteresis) and the 28-day lock together prevent whipsaw — small rank fluctuations near the Top 20 boundary do not trigger costly round-trips.

### 4.4 Position Ledger
A persistent JSON ledger (`positions_ledger.json`) tracks all open positions with entry date and entry price. It is loaded at the start of each weekly run and updated at the end after exits are removed and new entries are added. The 28-day hold lock is calculated from the `entry_date` field in the ledger.

---

## 5. Decision Log

| # | Parameter | Value |
|---|---|---|
| 1 | Target Universe | NSE Nifty 500 (N500) |
| 2 | Benchmark | NIFTY500 |
| 3 | Composite Weights | Equal (25% each — 12M / 9M / 6M / 3M) |
| 4 | Risk-Free Rate (RFR) | 7.00% Annualised |
| 5 | Selection Size | Top 20 Stocks |
| 6 | Position Sizing | Volatility-weighted, capped at 5% per stock |
| 7 | Residual Cash | Parked in Liquid Fund at 6% p.a. |
| 8 | Rebalance Frequency | Weekly (Friday close / Monday execution) |
| 9 | Transaction Friction | 0.20% per trade (0.40% round-trip) |
| 10 | Regime Filter | BUY / NOT BUY — price vs EMA50 only |
| 11 | No CASH State | Any downturn = NOT BUY. No forced liquidation. |
| 12 | 52H Risk Gate | Disqualified if price > 25% below 52-week high |
| 13 | Emergency Exit | 52H breach exits immediately, overrides hold lock |
| 14 | Rank Exit Buffer | Exit if rank > 40 AND held >= 28 days |
| 15 | Minimum Hold Lock | 28 calendar days — protects against rank whipsaw |
| 16 | RES_MOM | Computed and displayed. Not used in any decision logic. |