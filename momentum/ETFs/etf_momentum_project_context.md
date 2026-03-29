# ETF Dual Momentum Portfolio Allocator — Project Context

## Overview
This document summarises all work done on a Python-based ETF momentum ranking and portfolio allocation system for Indian ETFs. Share this with Claude to resume work in a new session.

---

## Input Data

**File:** `ETF.xlsx`  
**Sheet:** `DATA`  
**Structure:**
- Column A: ETF full name (e.g. "Aditya BSL BSE Sensex ETF")
- Column B: Ticker symbol
- Column C: Current close price
- Column D: 52-week high
- Column E onwards: Daily adjusted close prices, one column per trading date
- **Orientation:** ETF-per-row (transposed vs standard format)
- **Non-trading days:** Stored as `0` (replaced with NaN, then forward-filled)
- **Date range:** ~260 trading days (~1 year of data)
- **Universe:** 224 ETFs

---

## Output Files

| File | Description |
|---|---|
| `etf_momentum_ranking.py` | Main script — run this |
| `ETF.xlsx` | Input data — refresh monthly |
| `etf_rankings.xlsx` | Output — Rankings, Rebalance, Allocation, Regime sheets |
| `holdings_log.json` | Auto-maintained monthly holdings history — never edit manually |

**Run command:**
```bash
python etf_momentum_ranking.py
```
All three files must be in the same folder. No path config needed.

**Dependencies:**
```bash
pip install pandas numpy scipy openpyxl
```

---

## Scoring Pipeline (in order)

### Step 1 — Screen (applied BEFORE ranking)
Two hard filters. An ETF must pass BOTH to be "investable":

**1a. 52-Week High Proximity Filter**
- ETF must be trading within `MAX_DRAWDOWN_FROM_HIGH = 25%` of its 52-week high
- Removes deep-drawdown ETFs bouncing off a bottom rather than trending upward
- Example: an ETF 33.7% below its 52wk high → **FAIL**
- If 52wk high data is missing → defaults to **PASS** (don't penalise missing data)

**1b. Above 100 EMA Filter**
- ETF's current close must be above its 100-day exponential moving average
- Ensures only ETFs in a confirmed uptrend are ranked as investable
- If fewer than 100 days of price history available → defaults to **PASS** (insufficient history)

Result columns: `HIGH_PASS`, `EMA_PASS`, `SCREEN_PASS` (both must be True)

---

**Scoring is entirely based on Weighted Sharpe (no Clenow):**

**Sharpe Score (6M and 3M)**
```
Sharpe = (mean(log_returns) - DAILY_RF) / std(log_returns) × √252
DAILY_RF = 7% / 252  (repo rate proxy)
```

**Weighted Sharpe (composite ranking metric)**
```
WTD_SHARPE = 50% × Sharpe(6M) + 50% × Sharpe(3M)
```

The Weighted Sharpe is the sole ranking metric — no Clenow or composite blending.

**Two rank columns produced:**
- `RANK_UNIVERSE` — rank among all 224 ETFs (ignores screens, reference only)
- `RANK_INVESTABLE` — rank among screened-pass ETFs only (used for allocation)

---

### Step 3 — Regime Filter (two-layer, tiered)

**Index used:** `MONIFTY500` (Motilal Oswal Nifty 500 ETF)  
Fallbacks: `BSE500IETF → HDFCBSE500 → NIFTYBEES`  
Nifty 500 chosen over Nifty 50 because the 224-ETF universe spans large/mid/small cap — Nifty 500 detects deterioration earlier.

**Layer 1 — Trend**
- MONIFTY500 must be above its 100-day SMA

**Layer 2 — Breadth**
- ≥50% of all 224 ETFs must be above their own 50-day SMA

**Tiered output (3 states):**

| State | Condition | Active slots |
|---|---|---|
| BULL | Both layers pass | 5 (TOP_N) |
| PARTIAL | One layer fails | 3 (TOP_N_PARTIAL) |
| BEAR | Both layers fail | 0 (full cash) |

---

### Step 4 — Portfolio Allocation

**BEAR regime:** All 5 slots = cash immediately.

**PARTIAL regime:** 3 active slots, sorted by **3M Clenow descending** (not composite rank).  
Rationale: when regime weakens, the 2 ETFs with the weakest recent momentum are dropped first.  
Slots 4 & 5 = yellow "regime buffer" cash in Excel.

**BULL regime:** 5 active slots, sorted by composite rank (investable rank).

**Sector cap:** Max `SECTOR_CAP = 2` ETFs per sector across active slots.  
Sectors are auto-derived from ETF name keywords (no manual tagging needed).  
224 ETFs classified into ~40 sectors including: PSU_BANK, PRIVATE_BANK, BANKING_BROAD, IT_TECH, HEALTHCARE, METAL, ENERGY, GOLD, SILVER, GOVT_BONDS, FACTOR_MOMENTUM, FACTOR_VALUE, MIDCAP, SMALLCAP, BROAD_MARKET, INTERNATIONAL, etc.

**Waterfall allocation:** If sector cap is hit for a top-ranked ETF, the system walks down the investable list to find the next qualifying ETF. A slot only becomes cash if the entire investable universe is exhausted.

**Equal weight:** All active slots receive `1/TOP_N = 20%` weight each.

---

## CONFIG Parameters (all in one place at top of script)

```python
class CONFIG:
    INPUT_FILE  = "ETF.xlsx"
    OUTPUT_FILE = "etf_rankings.xlsx"

    WINDOW_6M   = 126      # trading days (~6 months)
    WINDOW_3M   = 63       # trading days (~3 months)
    ANNUALIZE   = 252

    TOP_N         = 5      # slots in BULL regime
    TOP_N_PARTIAL = 3      # slots in PARTIAL regime

    MAX_DRAWDOWN_FROM_HIGH = 0.25   # must be within 25% of 52wk high
                                    # ETF must also be above its 100 EMA

    SHARPE_W6M  = 0.50     # Weighted Sharpe blend
    SHARPE_W3M  = 0.50

    R2_W6M = 0.50          # R² z-score blend (display only, not used in ranking)
    R2_W3M = 0.50

    SECTOR_CAP = 2

    REGIME_TICKER      = "MONIFTY500"
    REGIME_FALLBACKS   = ["BSE500IETF", "HDFCBSE500", "NIFTYBEES"]
    TREND_SMA_WINDOW   = 100      # Layer 1: index vs N-day SMA
    BREADTH_EMA_WINDOW = 50       # Layer 2: % of ETFs above N-day EMA
    BREADTH_THRESHOLD  = 0.50

    DAILY_RF = 0.07 / 252
```

---

## Excel Output — 4 Sheets

### Sheet 1: Rankings
25 columns, all ETFs, sorted by investable rank first then universe rank.

| Col | Content |
|---|---|
| 1 | Investable Rank (blank if screened out) |
| 2 | Universe Rank (all ETFs) |
| 3–5 | Sharpe Rank, R2 Blended Rank, DM 6M Rank |
| 6 | Ticker |
| 7 | ETF Name |
| 8 | Sector (auto-classified) |
| 9–11 | Close, 52Wk High, % From 52Wk High |
| 12–13 | **EMA 50, EMA 100** (new) |
| 14 | Wtd Sharpe Score |
| 15–16 | Sharpe 6M, Sharpe 3M |
| 17–20 | R2 6M, R2 3M, R2 Z-Score 6M, R2 Z-Score 3M |
| 21 | R2 Z-Score Blended |
| 22–23 | DM Return 6M (%), DM Return 3M (%) |
| 24–25 | 52Wk High Filter, Screen Result |

**Colour coding:**
- Darker green = in current allocation (PASS + top N)
- Light green = passes both screens but not in top N
- Orange = fails at least one screen
- **Dark forest green Close cell** = NAV is above both 50 EMA and 100 EMA
- Row 2 = regime status bar (green=BULL, amber=PARTIAL, red=BEAR)

### Sheet 2: Rebalance ⭐ (new)
Three sections:

**Section 1 — Current Allocation**
All 5 slots with Action column (BUY/HOLD/CASH)

**Section 2 — Changes vs Previous Month**
Explicit trade instructions:
- 🟢 BUY — new entry
- 🔴 SELL — exited
- 🔵 ADD — weight increased
- 🟡 TRIM — weight reduced
- ⬜ HOLD — no change (rank drift ≥3 flagged in note)
- ⬛ REGIME — regime state changed (shown first)

**Section 3 — Last 12 Months History Grid**
One row per month, one column per ETF ever held. Green = held, blank = not held.

### Sheet 3: Allocation
5 slots with: Slot, Inv Rank, Sector, Ticker, ETF Name, Weight, Detail (explains why selected/skipped)

### Sheet 4: Regime
Layer-by-layer regime filter detail with pass/fail status.

---

## Holdings Log (`holdings_log.json`)

- Keyed by calendar month (`"2026-03"`)
- Running the script twice in the same month overwrites — latest run wins
- Diff always compares current month vs most recent previous month
- Shows last 12 months in the history grid
- **Never edit manually**

---

## Rebalancing Cadence (recommended)

| Action | Frequency |
|---|---|
| Full rescore + reallocate | Monthly, last trading day |
| Regime check only | Weekly — exit to cash immediately if BEAR, don't wait for month-end |
| Re-entry after regime clears | Next scheduled monthly rebalance only |

---

## Key Design Decisions & Rationale

| Decision | Rationale |
|---|---|
| Nifty 500 (not Nifty 50) for regime | Broader coverage matches universe; mid/small cap rolls over before large cap |
| Absolute momentum screen BEFORE ranking | Investable rank only shows qualifying ETFs; rank 1 = best ETF you'd actually buy |
| Weighted Sharpe as sole ranking metric | Clean, single signal; 50/50 6M-3M blend balances trend persistence with recency |
| 3M Sharpe sort in PARTIAL regime | Drops ETFs whose recent momentum is weakest — most forward-looking signal when regime weakens |
| Sector cap = 2 | Prevents 4/5 PSU Bank ETFs (same index, different fund houses) dominating allocation |
| Waterfall allocation | Slots never go to cash just because a higher-ranked ETF failed sector cap |
| 52wk high filter as hard screen | Deep-drawdown ETFs (e.g. 33% below peak) are bouncing, not trending — filter removes them before ranking |
| 100 EMA filter as hard screen | Ensures only ETFs in a confirmed uptrend are rankable; complements 52wk high filter |
| Missing EMA/52wk data → PASS | Penalising newly listed ETFs for insufficient history would unfairly exclude them |
| Tiered regime (not binary) | PARTIAL allows 3 active slots as a buffer — avoids whipsawing between full-invest and full-cash |

---

## Current Status (as of last run)

- **Regime:** PARTIAL (weak TREND) — MONIFTY500 just below 100d SMA; breadth at 51.8% (just above 50% threshold)
- **Active slots:** 3 of 5
- **Current allocation:** GOLDBEES (20%), METAL (20%), METALIETF (20%), CASH (20%), CASH (20%)
- **Investable universe:** 100 ETFs passing both screens (out of 224)
- **Notable:** SILVERBEES is #1 by universe rank but fails the 52wk high filter (33.7% below peak)

---

## Things Being Tested / Next Steps

- Testing exit signals tomorrow with fresh ETF.xlsx data
- The Rebalance sheet will auto-show BUY/SELL instructions vs current month's holdings

---

## Script Structure

```
etf_momentum_ranking.py
├── CONFIG                      # All parameters
├── SECTOR_RULES                # 40+ keyword-to-sector mappings
├── classify_sector()           # Auto-tags each ETF
├── load_etf_data()             # Reads ETF.xlsx, transposes, cleans zeros
├── sharpe_score()              # Annualised Sharpe vs risk-free
├── r2_score()                  # R² from log-linear regression
├── momentum_return()           # Simple total return %
├── regime_status()             # Two-layer tiered filter
├── build_ranking()             # Screen (52wk+EMA) → score → rank (universe + investable)
├── build_allocation()          # Regime-aware, sector-capped, waterfall
├── print_summary()             # Console output
├── load_holdings_log()         # Read holdings_log.json
├── save_holdings_log()         # Write holdings_log.json
├── record_to_log()             # Serialise current allocation
├── diff_allocations()          # BUY/SELL/HOLD/ADD/TRIM diff
├── update_log()                # Orchestrate log read/diff/write
├── _write_rebalance_sheet()    # Excel Rebalance sheet (3 sections)
├── save_excel()                # Write all 4 sheets (Rankings has EMA 50/100 columns)
└── main                        # Orchestrate full pipeline
```
