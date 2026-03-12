# Sharpe.py — Changelog

> **Script:** `momentum/Sharpe Score/Sharpe.py`
> **Output:** `n500_rankings.xlsx`
> **Checkpoint:** `checkpoints/Sharpe_checkpoint_2026-03-12.py`

---

## Session — 2026-03-12

### 1. Font Update — Calibri 11 throughout

**What changed:** All Excel cell fonts replaced from `Courier New` (sizes 8/9/10) to `Calibri 11`.

| Constant | Before | After |
|---|---|---|
| `GOLD_FONT` | Courier New, bold, 9 | Calibri, bold, 11 |
| `CYAN_FONT` | Courier New, bold, 9 | Calibri, bold, 11 |
| `TEXT_FONT` | Courier New, 9 | Calibri, 11 |
| `MUTED_FONT` | Courier New, 8 | Calibri, 11 |
| `HDR_FONT` | Courier New, bold, 9 | Calibri, bold, 11 |
| `GREEN_FONT` | Courier New, bold, 9 | Calibri, bold, 11 |
| `RED_FONT` | Courier New, bold, 9 | Calibri, bold, 11 |
| Sheet title cells (both) | Courier New, bold, 10 | Calibri, bold, 11 |

---

### 2. TOP20 Sheet — Removed Sharpe & Z-Score Columns

**What changed:** The 8 individual Sharpe and Z-score columns were removed from the `TOP20` Excel sheet. They remain fully visible in the `CALCS` sheet.

**Removed from TOP20:**
`S_12M`, `S_9M`, `S_6M`, `S_3M`, `Z_12M`, `Z_9M`, `Z_6M`, `Z_3M`

**TOP20 columns now (A → I):**

| Col | Header | Description |
|---|---|---|
| A | `RNK` | Rank |
| B | `TICKER` | Stock ticker |
| C | `SHARPE_Z` | Composite normalised Sharpe Z |
| D | `CLENOW` | Clenow score |
| E | `1M%` | 1-month return |
| F | `3M%` | 3-month return |
| G | `12M%` | 12-month return |
| H | `CLN_SLOPE` | Clenow annualised slope |
| I | `CLN_R2` | Clenow R² |

Title merge updated from `A1:Q1` → `A1:I1`.

---

### 3. Console Output — Simplified

**What changed:** Console table trimmed to 4 columns only.

**Before:** Full wide table with `RNK`, `TICKER`, `S_12M/9M/6M/3M`, `Z_12M/9M/6M/3M`, `SHARPE_Z`, `CLENOW`, `1M%`, `3M%`, `12M%` (138-char wide).

**After:** Compact 48-char table:
```
================================================
  N500 MOMENTUM — TOP 20  ·  Sharpe Z + Clenow
  Clenow: 90d exp-reg  |  RFR=7.0%
================================================
 RNK  TICKER        SHARPE_Z     CLENOW
────────────────────────────────────────────────
   1  TICKER_A         2.341      1.823
  ...
────────────────────────────────────────────────

  SHARPE_Z = mean(Z_12M, Z_9M, Z_6M, Z_3M)  |  CLENOW = AnnSlope × R²
```

---

### 4. CALCS Sheet — New `52H%` Column

**What changed:** Added a new column `52H%` (column T) to the CALCS sheet showing each stock's percentage distance from its 52-week high.

**Formula:**
```
PCT_FROM_52H = (last_price / max_price_last_252_days − 1) × 100
```
A value of `-5.0` means the stock is 5% below its 52-week high. Values are negative (below the high) or zero (at the high).

**Colour coding in Excel:**
- **Green background** (`#003322`) + green font → stock is within 25% of 52W high *(eligible for ranking)*
- **Dark red background** (`#1A0A0A`) + muted font → stock is more than 25% below 52W high *(ineligible)*

---

### 5. Ranking Filter — 52-Week High Proximity

**What changed:** The `RANK` column is now conditional. Only stocks **within 25% of their 52-week high** (`PCT_FROM_52H >= -25`) receive a numeric rank. All other stocks show a **blank rank**.

**Logic:**
```python
eligible = result["PCT_FROM_52H"] >= -25
result["RANK"] = np.nan                          # default all to blank
result.loc[eligible, "RANK"] = (
    result.loc[eligible, "COMPOSITE"]
    .rank(ascending=False, method="first", na_option="bottom")
)
```

The console during computation prints:
```
Computing 52-week high proximity ...
  312 / 503 stocks within 25% of 52W high (eligible for ranking)
```

**Bug fix included:** The TOP20 sheet's `int(row["RANK"])` call was crashing (`ValueError`) because `RANK` is now `NaN` for ineligible stocks. Fixed with:
```python
rank_v = int(row["RANK"]) if pd.notna(row["RANK"]) else None
```

---

### 6. COMPOSITE (SHARPE_Z) Normalisation

**What changed:** The raw `COMPOSITE` score (equal-weighted mean of `Z_12M`, `Z_9M`, `Z_6M`, `Z_3M`) is now passed through a normalisation function before ranking and display.

**Normalisation rules:**

| Raw value | Normalised value | Effect |
|---|---|---|
| `v > 1` | `v + 1` | Strong momentum stocks score 2+ |
| `0 ≤ v ≤ 1` | `v` (unchanged) | Mid-range unchanged |
| `v < 0` | `1 / (1 − v)` | Negatives map into `(0, 1]` |

**Examples:**

| Raw | Normalised |
|---|---|
| 2.50 | 3.50 |
| 0.80 | 0.80 |
| −0.50 | 0.667 |
| −1.00 | 0.500 |
| −3.00 | 0.250 |

The transform is monotonically increasing so **relative ranking order is identical** to pre-normalisation. The benefit is a more intuitive absolute scale — strong stocks clearly separate above 2.0, weak stocks compress toward 0.

The normalised value is stored back into `result["COMPOSITE"]` and flows into all downstream outputs: `RANK`, sort order, console table, `TOP20` sheet, and `CALCS` sheet.

---

### 7. NIFTY500 Separation & Residual Momentum

**What changed:** Re-architected code to treat "NIFTY500" as the market benchmark, explicitly filtering it out of the individual stock momentum rankings. Then added a new section to compute the **Residual Momentum** for each stock over the four windows (12M, 9M, 6M, 3M). 

**Logic details:**
- Extract "NIFTY500" to `nifty_series` and only calculate conventional metrics on `stock_tickers`.
- Compute daily log-returns for the stock and for `NIFTY500`.
- Perform an Ordinary Least Squares (OLS) regression: $r_{\text{stock}} = \alpha + \beta \times r_{\text{nifty}} + \epsilon$.
- Return the annualised Sharpe ratio on the residuals ($\epsilon$).
- Cross-sectionally Z-score each window's residual Sharpe and average equally to get the `RES_MOM` score.

**CALCS sheet:** Added 9 additional columns (`RS_12M/9M/6M/3M`, `RZ_12M/9M/6M/3M`, `RES_MOM`) to the spreadsheet display to track this new metric across the universe, increasing total CALCS columns to 24.

---

## File Structure

```
momentum/
└── Sharpe Score/
    ├── Sharpe.py                              ← Active script
    ├── CHANGELOG.md                           ← This file
    └── checkpoints/
        └── Sharpe_checkpoint_2026-03-12.py   ← Today's checkpoint
```

---

## Algorithm Summary (current state)

```
Load n500.xlsx [DATA sheet]
  └─ Build price matrix (tickers × dates)

Compute Sharpe ratios
  └─ Windows: 12M (252d), 9M (189d), 6M (126d), 3M (63d)
  └─ Annualised log-return Sharpe, excess over RFR=7%

Cross-sectional Z-score each window
  └─ COMPOSITE = mean(Z_12M, Z_9M, Z_6M, Z_3M)

Normalise COMPOSITE
  └─ v>1 → v+1  |  v<0 → 1/(1-v)  |  else unchanged

Compute Clenow Score
  └─ 90-day log-linear regression → AnnSlope × R²

Compute 52-week high proximity
  └─ PCT_FROM_52H = (last / max_252d − 1) × 100

Compute Residual Momentum vs NIFTY500
  └─ OLS regression of stock log-rets on market log-rets
  └─ Compute Sharpe on residuals for 12M, 9M, 6M, 3M
  └─ Cross-sectionally Z-score residual Sharpes
  └─ RES_MOM = mean(RZ_12M, RZ_9M, RZ_6M, RZ_3M)

Rank (eligible stocks only: PCT_FROM_52H >= −25)
  └─ Ranked by normalised COMPOSITE, descending

Output
  └─ Console: top 20 (RNK, TICKER, SHARPE_Z, CLENOW)
  └─ TOP20 sheet: 9 cols (RNK..CLN_R2), Calibri 11, dark theme
  └─ CALCS sheet: 24 cols (includes RES_MOM metrics + 52H%), Calibri 11, dark theme
```
