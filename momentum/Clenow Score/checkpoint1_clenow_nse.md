# Checkpoint #1 — Clenow Momentum Ranking for NSE 500/750
**Date:** March 12, 2026  
**Status:** Script operational, tested on N500 and N750 universes

---

## 1. Strategy Overview

Based on Andreas Clenow's algorithm from *Stocks on the Move: Beating the Market with Hedge Fund Momentum Strategies*.

### Core Momentum Score Formula
```
Momentum Score = Annualized Exponential Regression Slope × R²
```
- **Slope** — how fast the stock is trending upward (via log-linear regression over 90 trading days, annualised)
- **R²** — how smoothly/consistently it is trending (penalises erratic movers)

### Strategy Rules
| Parameter | Value |
|-----------|-------|
| Momentum window | 90 trading days |
| Stock MA filter | 100-day SMA (stock must be above) |
| Market filter | 200-day SMA on NIFTY500 (index must be above) |
| Gap disqualification | Any single-day move > 15% in last 90 days |
| Rebalance frequency | Weekly (Clenow uses Wednesdays) |
| Position sizing | Inverse volatility weighting (20-day annualised std dev) |

### Signal Definitions
| Signal | Meaning |
|--------|---------|
| **BUY** | Top N by score, above 100-day MA, market filter passed |
| **HOLD** | Top N by score, above 100-day MA, but market filter failed (index below 200-day MA) |
| **HOLD / SELL** | Outside top N, or below 100-day MA — exit existing positions, don't initiate new ones |
| **DISQUALIFIED** | Insufficient data, or gap > 15% detected |

---

## 2. Input File Format

Both N500 and N750 files follow the same structure:

| Column | Content |
|--------|---------|
| A | TICKER (stock symbols + index row named `NIFTY500`) |
| B | CLOSE (latest closing price) |
| C | 52WK HIGH |
| D onwards | Weekly date headers; price on that date (0 = non-trading / missing) |

- Sheet name: `DATA`
- Zeros are treated as missing (non-trading days)
- Index ticker `NIFTY500` is embedded as one of the rows

---

## 3. Data Assessment

| File | Tickers | Date Range | Trading Days | Sufficient? |
|------|---------|------------|-------------|-------------|
| n500.xlsx | 502 (+ index) | Mar 12 2025 → Mar 11 2026 | 246 | ✓ Yes |
| n750.xlsx | 751 (+ index) | Mar 12 2025 → Mar 11 2026 | 246 | ✓ Yes |

Minimum required: 200 trading days (for 200-day index MA). Both files clear this comfortably.

---

## 4. Script — `clenow_nse500.py`

### Usage
```bash
python clenow_nse500.py \
  --file n500.xlsx \
  --top_n 20 \
  --account_value 1000000 \
  --output clenow_ranked.csv
```

### Parameters
| Argument | Default | Description |
|----------|---------|-------------|
| `--file` | *(required)* | Path to input Excel file (n500.xlsx or n750.xlsx) |
| `--top_n` | 50 | Number of top momentum stocks to rank |
| `--account_value` | 0 | Portfolio value in INR for rupee allocation output (optional) |
| `--output` | clenow_ranked.csv | Output CSV filename |

### Output Columns
| Column | Description |
|--------|-------------|
| rank | Momentum rank (1 = highest score) |
| ticker | NSE ticker symbol |
| signal | BUY / HOLD / HOLD/SELL / DISQUALIFIED |
| momentum_score | Annualised slope × R² |
| annualized_slope | Annualised exponential regression slope (%) |
| r_squared | R² of the log-linear regression |
| last_close | Most recent closing price |
| ma_100 | 100-day simple moving average |
| below_ma100 | True/False — whether stock is below its 100-day MA |
| volatility_20d | 20-day annualised volatility (std dev of daily returns) |
| inv_vol_weight_pct | Portfolio weight via inverse volatility (%) |
| allocated_amount | INR allocated (only if --account_value provided) |
| approx_shares | Approximate shares to buy at last close |
| 52wk_high | 52-week high price |
| disqualify_reason | Reason for disqualification (if applicable) |

### Dependencies
```bash
pip install pandas numpy scipy openpyxl
```
Python 3.10+ required.

---

## 5. Position Sizing — Inverse Volatility Weighting

```
Weight_i      = (1 / Vol_i) / Σ(1 / Vol_j)   for all top-N stocks
Allocated_i   = Account Value × Weight_i
Approx Shares = Allocated_i / Last Close
```

- Volatility is the **20-day annualised standard deviation of daily returns**
- Lower volatility stocks receive larger allocations — each stock contributes roughly equal risk
- ATR-based sizing (Clenow original) was deliberately excluded from this implementation

---

## 6. Current Market Status (as of Mar 11, 2026)

| Metric | Value |
|--------|-------|
| NIFTY500 Last Close | 22,042.30 |
| NIFTY500 200-day MA | 23,298.49 |
| Market Filter | ❌ FAILED — index below 200-day MA |
| Active Signal | HOLD (no new BUY signals) |

---

## 7. N750 Top 20 Results (Checkpoint Snapshot)

Portfolio basis: ₹10,00,000. Market filter active — all signals show HOLD.

| Rank | Ticker | Score | Ann.Slope% | R² | Vol(20d)% | Weight% | Alloc (₹) | Shares |
|------|--------|-------|-----------|------|----------|---------|-----------|--------|
| 1 | NATIONALUM | 290.80 | 345.49 | 0.84 | 41.5 | 4.13 | 41,289 | 103 |
| 2 | SANSERA | 258.21 | 299.43 | 0.86 | 61.1 | 2.81 | 28,074 | 12 |
| 3 | ASHOKLEY | 214.20 | 237.43 | 0.90 | 39.1 | 4.38 | 43,803 | 237 |
| 4 | VEDL | 213.69 | 236.51 | 0.90 | 33.0 | 5.19 | 51,886 | 71 |
| 5 | SHRIRAMFIN | 142.32 | 163.21 | 0.87 | 44.7 | 3.84 | 38,376 | 37 |
| 6 | MCX | 136.00 | 153.16 | 0.89 | 30.5 | 5.63 | 56,266 | 22 |
| 7 | FORCEMOT | 127.27 | 179.89 | 0.71 | 60.7 | 2.82 | 28,246 | 1 |
| 8 | KARURVYSYA | 120.75 | 154.21 | 0.78 | 38.6 | 4.45 | 44,471 | 145 |
| 9 | UNIONBANK | 108.57 | 132.24 | 0.82 | 38.8 | 4.41 | 44,137 | 243 |
| 10 | BHARATFORG | 106.54 | 145.59 | 0.73 | 30.0 | 5.72 | 57,152 | 31 |
| 11 | APLAPOLLO | 103.97 | 128.03 | 0.81 | 26.8 | 6.40 | 64,012 | 31 |
| 12 | KIRLOSENG | 85.77 | 133.86 | 0.64 | 28.3 | 6.06 | 60,637 | 41 |
| 13 | HINDZINC | 82.29 | 148.72 | 0.55 | 34.3 | 5.00 | 49,967 | 85 |
| 14 | SBIN | 81.51 | 104.78 | 0.78 | 22.0 | 7.80 | 77,969 | 71 |
| 15 | FEDERALBNK | 69.60 | 85.09 | 0.82 | 25.5 | 6.72 | 67,155 | 249 |
| 16 | GESHIP | 68.43 | 99.35 | 0.69 | 35.1 | 4.89 | 48,896 | 34 |
| 17 | MAHABANK | 66.66 | 90.44 | 0.74 | 49.3 | 3.48 | 34,780 | 518 |
| 18 | ABSLAMC | 63.86 | 90.65 | 0.70 | 55.6 | 3.08 | 30,826 | 30 |
| 19 | HINDALCO | 61.39 | 83.33 | 0.74 | 34.1 | 5.03 | 50,299 | 52 |
| 20 | AJANTPHARM | 60.53 | 71.59 | 0.85 | 21.0 | 8.18 | 81,758 | 26 |

---

## 8. Key Design Decisions

- **SMA chosen over EMA** for both MA filters — consistent with Clenow's original specification. At 100 and 200-day lengths the difference is negligible and SMA is simpler to audit.
- **ATR sizing excluded** by design — inverse volatility weighting used instead as it is scale-independent and cleaner.
- **No backtesting performed** — script implements a single-snapshot ranking. Walk-forward backtesting is a future consideration.
- **Adjusted prices caveat** — input data should ideally use split/dividend-adjusted closing prices for clean regression slopes. The 15% gap filter partially mitigates distortions from unadjusted data.
- **N750 file uses NIFTY500 as index ticker** — the 251 additional stocks beyond N500 are ranked against the same NIFTY500 market filter.

---

## 9. Files at Checkpoint

| File | Description |
|------|-------------|
| `clenow_nse500.py` | Main ranking script (works for both N500 and N750) |
| `n500.xlsx` | NSE 500 universe input file |
| `n750.xlsx` | NSE 750 universe input file |
| `clenow_ranked.csv` | Last N500 run output |
| `clenow_n750_top20.csv` | N750 top 20 snapshot (this checkpoint) |

---

*Checkpoint #1 — generated March 12, 2026*
