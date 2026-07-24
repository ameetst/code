# ETF Weekly Momentum Strategy — Complete Specification

**Status**: Implementation Ready (Reconciled with Code)
**Version**: 2.1 (Corrected — reviewed against actual source files)
**Original Date**: 2026-07-20
**Reviewed**: 2026-07-21, against `etf_momentum_weekly.py`, `momentum_lib.py`,
`strategy_config.json`, `holdings_log_weekly.json` as actually uploaded.
**Model Used (original draft)**: KIMI3

---

## ⚠️ Reviewer's Note — Read This First

This version corrects **eight discrepancies** found between the original spec
(v2.0) and what the code in `etf_momentum_weekly.py` actually does. The
original doc described the *intended* design accurately in most places, but
in a few important spots it describes something that either isn't
implemented, or is implemented differently than written. Each correction is
marked **[CORRECTED]** inline where it occurs, and summarized here for quick
reference:

| # | Area | Doc said | Code actually does |
|---|:---|:---|:---|
| 1 | **MOM_ACCEL formula** | `0.4*(Z_1m+Z_3m) - 0.2*Z_6m`, using individually z-scored windows | `(Sharpe_1m + Sharpe_3m) - Sharpe_6m` — **equal weights (1, 1, -1)** on raw Sharpes, z-scored **once** as a whole afterward |
| 2 | **Where MOM_ACCEL lives** | `momentum_lib.py`, `compute_sharpe()` | That function is real, but `etf_momentum_weekly.py` **never imports `momentum_lib.py`** — it has its own separate, self-contained MOM_ACCEL logic. Editing `momentum_lib.py` has **zero effect** on the live weekly strategy. |
| 3 | **Excel output** | `etf_rankings_weekly.xlsx` with 4 sheets (Rankings/Rebalance/Allocation/Regime) | **Does not exist.** No Excel-writing code anywhere in the script. `CONFIG.OUTPUT_FILE` is defined but never used. |
| 4 | **Holdings log structure** | Date-keyed: `{"2026-07-20": {"TICKER": {...}}}` | **Flat, ticker-keyed**: `{"TICKER": {...}}` — confirmed against the actual uploaded file. No date history is kept; each run **overwrites** the previous state. |
| 5 | **`RANK_RETENTION_THRESHOLD`** | A configurable top-N retention cutoff | The JSON key exists and is read into `CONFIG`, but under a name (`RANK_RETENTION_THRESHOLD`) that the rest of the code never looks at — the actual logic reads `CONFIG.RANK_RETENTION` (no `_THRESHOLD` suffix), which stays hardcoded at 20 regardless of what the JSON says. |
| 6 | **`REBALANCE_FREQUENCY`** | A configurable rebalance cadence | Not in the JSON-override key list at all, and the Monday-only gate in `weekly_rebalance()` is a hardcoded day-of-week check that never reads this value either way. Purely decorative. |
| 7 | **`--monitor` CLI flag** | `python etf_momentum_weekly.py --monitor` for daily peak-tracking without exits | No argument parsing exists in `__main__`. The script only ever runs the full Monday rebalance path. |
| 8 | **Sample console output** (Section 13) | Shows `SHARPE_1M (21d): 224/224 valid`-style per-window logging | That logging exists in `momentum_lib.py`, not in `etf_momentum_weekly.py`, which has no such print statements. |

Also flagged: `strategy_config.json` carries `R2_W6M` / `R2_W3M` keys that
correspond to nothing in this script at all — leftover from the unrelated
monthly/SR2 config format, silently ignored here.

None of this means the strategy doesn't work — the core screen → score →
regime → allocate → exit logic is intact and internally consistent. It means
the **actual ranking math (item 1)** and the **actual persistence/output
behavior (items 3, 4)** are different from what this doc originally claimed,
which matters if you're trying to reason about *why* the strategy picked what
it picked, or if you're expecting a rankings Excel file / historical holdings
audit trail that isn't actually being produced.

---

## Executive Summary

Upgrade from **monthly to weekly rebalance** with enhanced scoring:

| Aspect | Monthly (v1.0) | Weekly (v2.0, as coded) | Change |
|:---|:---|:---|:---|
| **Rebalance Frequency** | 1st trading day of month | Every Monday | ✓ WEEKLY |
| **MOM_ACCEL Calculation** | (ST - LT) mean Z-scores | `Sharpe_1m + Sharpe_3m - Sharpe_6m` (raw, equal-weighted), z-scored once **[CORRECTED — see #1 above]** | ✓ NEW FORMULA, different from originally documented |
| **Entry Filters** | 52-week high only | 52-week high + MOM_ACCEL>0 | ✓ ADDED MOM_ACCEL |
| **Ranking Metric** | Weighted Sharpe | Weighted Sharpe (unchanged) — this part matches the code exactly | ✓ FILTER ORDER |
| **Exit Timing** | Monthly rebalance | Monday only | ✓ BATCH EXIT |
| **TSL Threshold** | 10% | 5% | ✓ TIGHTER |
| **Rank Retention** | N/A | Top 20 (hardcoded default; JSON override currently broken — see #5) | ✓ NEW |
| **Top Holdings** | 5 (BULL) / 3 (PARTIAL) / 0 (BEAR) | 5 / 3 / 0 (same) | ✓ SAME |
| **Sector Cap** | 1 ETF/sector | 1 ETF/sector (same) | ✓ SAME |
| **Regime Filter** | Monthly cadence | Weekly cadence | ✓ ADJUSTED |
| **Excel Output** | `etf_rankings.xlsx` (monthly script) | **Not implemented** — no output file is generated **[CORRECTED — see #3]** | ✗ MISSING vs monthly version |

---

## 1. MOM_ACCEL Score — Actual Calculation **[CORRECTED]**

### What's actually implemented (in `etf_momentum_weekly.py`, `build_weekly_ranking()`)

```python
# Step 1 — three raw Sharpe ratios, all at 0% risk-free rate
accel_sh1 = sharpe_score(s, WINDOW_1M, daily_rf=0.0)   # 21-day
accel_sh3 = sharpe_score(s, WINDOW_3M, daily_rf=0.0)   # 63-day
accel_sh6 = sharpe_score(s, WINDOW_6M, daily_rf=0.0)   # 126-day

# Step 2 — combine with EQUAL weights (not 0.4/0.4/-0.2)
# if 6M is unavailable, its term is treated as 0
MOM_ACCEL_RAW = accel_sh1 + accel_sh3 - accel_sh6_eff

# Step 3 — z-score the RAW COMPOSITE once, across the 52wk-high-screened
# ("investable") population for that week — NOT three components
# individually z-scored and then blended
MOM_ACCEL = zscore(MOM_ACCEL_RAW | investable population)
```

This is meaningfully different from the originally-specified formula:

| | Originally specified | Actually implemented |
|---|:---|:---|
| Window weights | 0.4 / 0.4 / -0.2 | **1.0 / 1.0 / -1.0** (equal, unweighted) |
| Where z-scoring happens | Each window individually (Z_1M, Z_3M, Z_6M), *then* combined | Combined first as raw Sharpes, *then* z-scored **once** as a single composite |
| Reference population for z-score | "all investable ETFs" (ambiguous in original doc) | ETFs that pass the 52wk-high screen that week (confirmed from code: `inv_mask = df["SCREEN_PASS_HIGH"]`) |

**Practical implication:** because `MOM_ACCEL` is z-scored around the
investable universe's own mean, `MOM_ACCEL > 0` is effectively a **relative,
above-the-median-that-week filter**, not an absolute threshold — by
construction, close to (but not exactly, due to skew) half of the
52wk-high-screened universe will pass this filter in any given week,
regardless of overall market conditions. This is worth knowing when
interpreting why the number of investable ETFs fluctuates week to week.

### Where this actually lives

**File**: `etf_momentum_weekly.py`
**Function**: `build_weekly_ranking()`
**NOT** in `momentum_lib.py` — see Reviewer's Note item #2. `momentum_lib.py`
does contain a `compute_sharpe()` function with a `0.4*(Z_1M+Z_3M) - 0.2*Z_6M`
formula, individually z-scored per window, exactly as originally documented
— but that file is a separate, more general NSE-stock-scoring library that
this weekly ETF script never imports. If `momentum_lib.py` was intended to be
the source of truth, **it currently is not being used**, and the two files'
MOM_ACCEL values will disagree for the same ETF on the same day.

### Windows Used

| Window | Days | Purpose |
|:---|---:|:---|
| 1M | 21 | Short-term momentum accelerator |
| 3M | 63 | Medium-term (shared with Weighted Sharpe ranking) |
| 6M | 126 | Longer-term comparison (shared with Weighted Sharpe ranking) |

---

## 2. Entry Filters — Dual Screening (AND Logic)

*(This section matches the code as written — no corrections needed.)*

Both filters **must pass** for an ETF to be eligible for allocation:

#### Filter 1: 52-Week High Proximity (Existing)

```
pct_from_high = (52wk_high - current_NAV) / 52wk_high
PASS if pct_from_high <= 0.25  (ETF within 25% of peak)
```

**Rationale**: Removes ETFs in sustained downtrends.

#### Filter 2: MOM_ACCEL > 0 (see Section 1 for the actual formula)

```
PASS if MOM_ACCEL > 0  (z-scored composite above the weekly cross-sectional mean)
```

**Rationale**: Ensures only ETFs with above-average momentum acceleration
(relative to that week's investable universe) are selected.

### Combined Condition

```
INVESTABLE = (52WK_HIGH_PASS) AND (MOM_ACCEL > 0)
```

If either filter fails → ETF excluded from allocation consideration.
Confirmed in code as `df["SCREEN_PASS"] = df["SCREEN_PASS_HIGH"] & df["MOM_ACCEL_PASS"]`.

---

## 3. Ranking — Weighted Sharpe (Matches Code Exactly)

Filters (52wk_high, MOM_ACCEL) are used for **screening only**, NOT for ranking.

### Ranking Metric

```
Weighted Sharpe = 0.5 * Z_6M_SHARPE + 0.5 * Z_3M_SHARPE
```

Unlike MOM_ACCEL, this metric **is** computed the way the original doc
describes: `SHARPE_6M` and `SHARPE_3M` (each at the strategy's normal 7% p.a.
risk-free rate) are individually z-scored across the 52wk-high-screened
population, *then* blended 50/50. Confirmed in code (`_Z6_INV`, `_Z3_INV`,
then `CONFIG.SHARPE_W6M * z6i + CONFIG.SHARPE_W3M * z3i`).

**Process** (as coded):
1. Filter 1: Identify ETFs within 25% of 52-week high
2. Compute Weighted Sharpe rank (`RANK_INV_WEIGHTED`) across that screened population
3. Filter 2: Apply MOM_ACCEL > 0 on top of that same population
4. Recompute a final rank (`RANK_FINAL`) only among ETFs passing **both** filters
5. Select top N respecting sector cap, ordered by `RANK_FINAL`

---

## 4. Weekly Rebalance Logic

*(Matches code — no corrections needed.)*

### Rebalance Timing

- **Frequency**: Every Monday — hardcoded via `pd.Timestamp(current_date).dayofweek != 0` check, **not** driven by the `REBALANCE_FREQUENCY` config value (see Reviewer's Note #6).
- **Skipped**: On any non-Monday date, the script exits early with `{"status": "skipped", "reason": "Not a Monday"}`.
- **Exit Timing**: Monday only (no intra-week exits)

### Position Selection

1. **Check Exit Conditions** (applied to current holdings):
   - 5% TSL from running peak since entry
   - Rank (`RANK_FINAL`) outside top 20 (`CONFIG.RANK_RETENTION`)
   - All exits batched on Monday
2. **Retain Existing Positions**: if still ranked ≤ 20 and sector cap allows
3. **Select New Entries**: from the dual-filtered universe, ranked by `RANK_FINAL`, sector cap enforced, filled to `active_slots`

### Example Weekly Rebalance Sequence

```
Monday Morning:
├─ Load latest NAV data
├─ Compute scores (Sharpe 1M/3M/6M, MOM_ACCEL, 52wk_high)
├─ Apply entry filters
├─ Check exit conditions on current holdings
│  ├─ Position A: Exit (5% TSL hit)
│  ├─ Position B: Exit (rank 25, outside top 20)
│  └─ Position C: Retain (rank 8, filters pass)
├─ Evaluate regime
│  └─ BULL regime (5 slots active)
├─ Add existing qualifying positions
│  └─ Retain Position C
├─ Fill remaining 4 slots with new top-ranked candidates
│  ├─ Position D (rank 1)
│  ├─ Position E (rank 2)
│  ├─ Position F (rank 4) [rank 3 has same sector as C]
│  └─ Position G (rank 6) [rank 5 breaches sector cap]
├─ Allocate 20% weight each (1/5th)
└─ Save holdings (overwrites previous state — see Section 8)
```

---

## 5. Exit Rules — Monday-Only Batch Processing

*(Matches code — no corrections needed.)*

### Rule 1: 5% Trailing Stop Loss (TSL)

```
EXIT if current_close < running_peak * (1 - 0.05)
```

**Tracking**: `running_peak` only updates when the script actually **runs**
on a given date (there is no background daily process — see Reviewer's Note
#7 regarding the non-existent `--monitor` flag). If the script is only run
on Mondays, `running_peak` only reflects Monday closes, not the true
intra-week high.

**Example**:
```
Entry: ₹100
Week 1 peak: ₹105 → running_peak = ₹105
TSL trigger: ₹99.75 (105 * 0.95)
Monday check:
  - Current = ₹100 → Above trigger, hold
  - Current = ₹99 → Below trigger, EXIT
  - Current = ₹110 → Above peak, update peak to ₹110, hold
```

### Rule 2: Rank Outside Top 20

```
EXIT if RANK_FINAL > CONFIG.RANK_RETENTION (hardcoded default 20;
                                             JSON override currently broken — see Section 10)
```

### Rule 3: Filters No Longer Pass

Implicit in Rule 2 — if either the 52wk-high or MOM_ACCEL filter fails, the
ETF drops out of `RANK_FINAL` entirely (assigned rank 0 → treated as "outside
top 20" the next time `check_exit_conditions` runs on it, or immediately
flagged as `"No longer in ranking universe"` if it's absent from the ranking
frame altogether).

### Exit Timing

- **Checked**: Every time the script is run (in practice, only Mondays, since nothing else invokes it)
- **Not Checked**: Intra-week, since there's no scheduled daily invocation

---

## 6. Regime Filter — Weekly Cadence

*(Matches code exactly — no corrections needed.)*

| State | Condition | Slots | Action |
|:---|:---|---:|:---|
| **BULL** | EMA50 > EMA100 AND Price > EMA50 | 5 | Enter top 5 ETFs |
| **PARTIAL** | Price > EMA100 (but not BULL) | 3 | Enter top 3 ETFs, 2 to cash |
| **BEAR** | Price ≤ EMA100 | 0 | Full cash, monitor only |

Regime ticker: `MONIFTY500`, falling back through `BSE500IETF` →
`HDFCBSE500` → `NIFTYBEES` if the primary ticker is missing from the data
(confirmed present in the currently uploaded `ETF.xlsx`).

---

## 7. Data Input — ETF.xlsx

*(Matches code — no corrections needed.)*

| Column | Content |
|:---|:---|
| A | ETF Name |
| B | NSE Ticker |
| C onwards | Daily NAV |

Currently uploaded `ETF.xlsx`: 299 tickers, 261 price columns, `DATA` sheet.
Both `MONIFTY500` and `NIFTYBEES` confirmed present.

**Note**: `load_etf_data()` reconstructs dates via
`pd.bdate_range(end=today, periods=n_price_cols)` rather than reading real
dates from the sheet — the price *values* are real, but the *date labels*
assigned to each column are regenerated relative to today's date every time
the script runs. This is fine for the live pipeline (which only ever cares
about "most recent N days"), but means this file's date columns should not be
treated as a reliable historical record if extracted independently.

---

## 8. Position Tracking — JSON Format **[CORRECTED]**

### Actual Holdings Log Format

**File**: `holdings_log_weekly.json`

Confirmed from the actual uploaded file — **flat, ticker-keyed**, not
date-keyed:

```json
{
  "HEALTHCARE": {
    "ticker": "HEALTHCARE",
    "entry_date": "2026-07-20",
    "entry_price": 21.13,
    "running_peak": 21.13,
    "current_price": 21.13,
    "status": "active",
    "sector": "HEALTHCARE"
  },
  "GROWWHOSPI": {
    "ticker": "GROWWHOSPI",
    "entry_date": "2026-07-20",
    "entry_price": 53.89,
    "running_peak": 53.89,
    "current_price": 53.89,
    "status": "active",
    "sector": "OTHER"
  }
}
```

`load_holdings()` / `save_holdings()` do a direct `json.load` / `json.dump`
of this flat structure — there is no outer date key. **Each weekly run
overwrites the entire file with the new state.** No historical snapshots are
retained anywhere.

**Implication:** the original doc's claim that this format "enables P&L
calculation and backtest verification" via historical dated records is not
actually supported by the current code — there's no persisted week-by-week
history to replay. If historical tracking is wanted, the save logic would
need to be changed to append/version by date rather than overwrite in place.

### Tracking Fields (accurate as-is)

| Field | Purpose |
|:---|:---|
| `entry_date` | Date position entered (YYYY-MM-DD) |
| `entry_price` | NAV at entry |
| `running_peak` | Highest NAV seen since entry, updated only when the script runs |
| `current_price` | Set at entry; **not actually refreshed on subsequent runs** in the current code (`current_price` is only written when a position is first opened — no line updates it thereafter) |
| `status` | `"active"`, `"exited"`, or `"cash"` |
| `sector` | Auto-classified sector |
| `exit_date` / `exit_reason` | Present only once a position has exited |

---

## 9. Output Files **[CORRECTED — largest gap from original spec]**

### `etf_rankings_weekly.xlsx` — **Not implemented**

The original spec described a 4-sheet Excel workbook (Rankings, Rebalance,
Allocation, Regime), directly modeled on the monthly script's
`save_excel()`. **No equivalent function exists in `etf_momentum_weekly.py`.**
`CONFIG.OUTPUT_FILE = "etf_rankings_weekly.xlsx"` is set but never referenced
by any write operation in the file. If this output is actually wanted, it
needs to be built — it does not currently exist as working code, only as a
config placeholder.

### `holdings_log_weekly.json` — exists, but see Section 8

Real, and is the *only* file this script actually writes. It is a live
current-state snapshot, not a history log (see Section 8 correction above).

---

## 10. Configuration — What Actually Works **[CORRECTED]**

### `strategy_config.json` (as uploaded)

```json
{
  "INPUT_FILE": "ETF.xlsx",
  "OUTPUT_FILE": "etf_rankings.xlsx",
  "WINDOW_6M": 126,
  "WINDOW_3M": 63,
  "WINDOW_1M": 21,
  "ANNUALIZE": 252,
  "TOP_N": 5,
  "TOP_N_PARTIAL": 3,
  "MAX_DRAWDOWN_FROM_HIGH": 0.25,
  "SHARPE_W6M": 0.5,
  "SHARPE_W3M": 0.5,
  "R2_W6M": 0.5,
  "R2_W3M": 0.5,
  "REGIME_TICKER": "MONIFTY500",
  "REGIME_FALLBACKS": ["BSE500IETF", "HDFCBSE500", "NIFTYBEES"],
  "TREND_FAST_EMA_WINDOW": 50,
  "TREND_EMA_WINDOW": 100,
  "SECTOR_CAP": 1,
  "DAILY_RF_ANNUAL": 0.07,
  "TSL_THRESHOLD": 0.05,
  "REBALANCE_FREQUENCY": "weekly",
  "RANK_RETENTION_THRESHOLD": 20
}
```

Note also: `"OUTPUT_FILE": "etf_rankings.xlsx"` in this JSON doesn't even
match `CONFIG`'s own class default of `"etf_rankings_weekly.xlsx"` — moot
either way since no file is written (Section 9), but worth knowing this key
is currently inconsistent even with itself.

### Key-by-key status

| Key | Actually overridable via JSON? | Actually used in logic? |
|:---|:---:|:---:|
| `INPUT_FILE`, `OUTPUT_FILE` | ✅ | Partially (`OUTPUT_FILE` unused — Section 9) |
| `WINDOW_6M`, `WINDOW_3M`, `WINDOW_1M`, `ANNUALIZE` | ✅ | ✅ |
| `TOP_N`, `TOP_N_PARTIAL` | ✅ | ✅ |
| `MAX_DRAWDOWN_FROM_HIGH` | ✅ | ✅ |
| `SHARPE_W6M`, `SHARPE_W3M` | ✅ | ✅ |
| `R2_W6M`, `R2_W3M` | ❌ (not in `_KEYS`) | ❌ (no R2/SR2 concept anywhere in this script) |
| `REGIME_TICKER`, `REGIME_FALLBACKS` | ✅ | ✅ |
| `TREND_FAST_EMA_WINDOW`, `TREND_EMA_WINDOW` | ✅ | ✅ |
| `SECTOR_CAP` | ✅ | ✅ |
| `DAILY_RF_ANNUAL` | ✅ (derives `CONFIG.DAILY_RF`) | ✅ |
| `TSL_THRESHOLD` | ✅ | ✅ |
| `REBALANCE_FREQUENCY` | ❌ (not in `_KEYS`) | ❌ (Monday check is hardcoded) |
| `RANK_RETENTION_THRESHOLD` | ⚠️ Sets an attribute of this exact name, but... | ❌ (logic reads `CONFIG.RANK_RETENTION`, a differently-named attribute that stays hardcoded at 20) |

---

## 11. Backtest Expectations

*(Unchanged from original — these are forward-looking hypotheses, not
implementation claims, so no correction needed here. Worth noting: any
backtest of "the MOM_ACCEL filter" should be built against the **actual**
equal-weighted, single-pass-z-scored formula in Section 1, not the
0.4/0.4/-0.2 formula originally described, since that's what the live script
runs.)*

### Hypothesis

Weekly rebalance with tighter (5%) TSL should:
- Reduce max drawdown vs monthly 10% TSL
- Increase portfolio turnover (more trading)
- Better capture short-term momentum via MOM_ACCEL filter
- Reduce stale holding periods

### Backtest Plan

Compare 3 scenarios (same data range, Apr 2020 – current):

| Config | CAGR | Max DD | Sharpe | Turnover |
|:---|---:|---:|---:|---:|
| **Monthly 10% TSL** (baseline) | 22.23% | -15.67% | 1.79 | ~48/yr |
| **Weekly 5% TSL** (new) | ? | ? | ? | ~250/yr |
| **Weekly + MOM_ACCEL filter** | ? | ? | ? | ~250/yr |

---

## 12. Implementation Files **[CORRECTED]**

### Files that actually drive live behavior

1. **`etf_momentum_weekly.py`** — the entire pipeline: data loading, scoring
   (including the real MOM_ACCEL formula), regime, ranking, exits, holdings
   persistence. Self-contained; imports nothing from `momentum_lib.py`.
2. **`strategy_config.json`** — see Section 10 for which keys actually do
   anything.
3. **`holdings_log_weekly.json`** — read/written every run; flat structure
   (Section 8), overwritten each time, not appended.

### Files that exist but are NOT used by this strategy

4. **`momentum_lib.py`** — a separate, more general NSE-stock momentum
   library (Clenow scores, residual momentum vs NIFTY500, a *different*
   MOM_ACCEL formula with 0.4/0.4/-0.2 weights). Not imported anywhere in
   `etf_momentum_weekly.py`. Any edits here have no effect on this strategy
   unless/until something actually imports it.

### Output that does NOT currently exist

5. **`etf_rankings_weekly.xlsx`** — described in the original spec, not
   implemented in code (Section 9).

---

## 13. Usage **[CORRECTED]**

### Manual Weekly Rebalance (Every Monday)

```bash
python etf_momentum_weekly.py
```

There are no command-line arguments — `sys.argv` is imported but never read.
Running the script with any extra arguments has no effect; it always runs
the full `weekly_rebalance()` path.

Actual console output (based on the real print statements in the code) looks
like:

```
======================================================================
WEEKLY REBALANCE: 2026-07-20
======================================================================
[load]   ETF.xlsx
         299 ETFs  |  261 date cols  (... -> ...)
Building ranking with MOM_ACCEL filters...

REGIME: BULL (5 active slots)

Checking exit conditions...
  EXIT: BANKBEES - 5% TSL hit: 3.85% drawdown from peak 218.00

New allocation (5 positions):
  NIFTYBEES: sector=BROAD_MARKET, entry=185.50
  ITBEES: sector=IT_TECH, entry=220.30
  ...

[result] {'status': 'success', 'exits': 1, 'new_positions': 5}
```

Note there is **no** `SHARPE_1M (21d): 224/224 valid`-style per-window
validity logging in this script — that output style belongs to
`momentum_lib.py`'s `compute_sharpe()`, which this script doesn't call.

### Daily TSL Monitoring — Does Not Exist

The original spec's `python etf_momentum_weekly.py --monitor` flag is
**not implemented**. There is no code path that updates `running_peak`
without also running the full Monday rebalance logic (day-of-week gate
included). If daily peak-tracking between Mondays is actually wanted, this
would need to be built as new functionality, not configured via an existing
flag.

---

## 14. Transition Plan

*(Unchanged — still forward-looking action items, updated to reflect what
actually needs building vs. what's already there.)*

### Before Going Live

- [ ] Decide whether to keep the equal-weighted MOM_ACCEL formula as-coded, or change it to match the originally-intended 0.4/0.4/-0.2 weighted, per-window-z-scored version
- [ ] Decide whether `momentum_lib.py` should be wired in, or retired/ignored for this strategy
- [ ] Build the missing `etf_rankings_weekly.xlsx` output if it's actually needed for review/audit
- [ ] Decide whether holdings history should be preserved (date-keyed or append-only) rather than overwritten each run
- [ ] Fix the `RANK_RETENTION_THRESHOLD` → `RANK_RETENTION` key mismatch so the JSON value actually takes effect
- [ ] Either wire `REBALANCE_FREQUENCY` into the day-of-week gate, or remove it from the config to avoid implying it's configurable
- [ ] Remove the unused `R2_W6M` / `R2_W3M` keys, or repurpose them if SR2-style scoring is ever added to this script
- [ ] Backtest weekly strategy (Apr 2020 – now) using the **actual** MOM_ACCEL formula
- [ ] Verify 1M Sharpe calculation against real ETF.xlsx data
- [ ] Dry-run 2-3 weeks of rebalance logic
- [ ] Build a real `--monitor` mode if daily (not just Monday) peak-tracking is required

### Go-Live Steps

1. **Week 1 (Dry-Run)**: Execute rebalance Monday, log results, don't trade
2. **Week 2 (Pilot)**: Execute 1 rebalance with real trades on small account (10% capital)
3. **Week 3+**: Full deployment with monitoring

---

## 15. Key Differences from Monthly Strategy

| Aspect | Monthly | Weekly (as coded) | Impact |
|:---|:---|:---|:---|
| Rebalance Day | 1st trading day | Every Monday | More frequent entry/exit decisions |
| MOM_ACCEL | (ST - LT) mean Z-scores | `Sharpe_1m + Sharpe_3m - Sharpe_6m`, equal-weighted, z-scored once **[corrected]** | Different selectivity than originally planned |
| Exit Check | Monthly rebalance | Monday only | Simpler TSL tracking, but no intra-week protection |
| TSL | 10% | 5% | Faster exits on small moves |
| Rank Retention | N/A | Top 20 (hardcoded; JSON override broken) | Allows mid-portfolio holds |
| Regime | Monthly cadence | Weekly cadence | More responsive |
| Holdings Tracking | `holdings_log.json` | `holdings_log_weekly.json`, flat/overwritten **[corrected: not date-keyed]** | No historical audit trail currently |
| Output Workbook | `etf_rankings.xlsx` (exists) | `etf_rankings_weekly.xlsx` **[corrected: does not exist]** | No rankings artifact to review currently |

---

## 16. Risk & Considerations

*(Unchanged from original — still valid forward-looking considerations.)*

### Increased Turnover
- **Weekly rebalance → ~250 trades/year** vs ~48 monthly
- **Transaction costs**: At ₹20/leg = ₹10k+/year for 5-position portfolio
- **Mitigation**: Consider batch trading; broker fee structure

### Tighter TSL (5%)
- **Exit sooner** from positions
- **Potential:** Whipsaws in choppy weeks
- **Benefit:** Lower max drawdown, capital preservation

### MOM_ACCEL Sensitivity
- As actually implemented, MOM_ACCEL is a **relative, weekly-recalculated**
  filter (z-scored around that week's investable universe), so its pass/fail
  boundary moves every week regardless of any absolute momentum threshold —
  worth monitoring how much churn this introduces in the "just above/just
  below zero" ETFs near the cutoff.
- **Mitigation:** Monitor Z-score distributions; consider smoothing if needed

### Regime Switches
- **PARTIAL state** provides buffer; BEAR triggers on Price ≤ EMA100
- **Can hold through regime switches** if rank stays ≤ 20
- **Benefit:** Adaptive to market conditions

### No Historical Persistence
- Because `holdings_log_weekly.json` is overwritten each run (Section 8),
  there is currently no way to reconstruct past weeks' actual holdings from
  this file alone — anyone wanting a P&L audit trail or backtest-vs-live
  reconciliation will need to either archive this file externally each week,
  or change the save logic to append/version it.

---

## 17. Questions & Clarifications

**Q1**: Should intra-week TSL monitoring update `running_peak` if a position makes a new high?
**A1**: That was the original intent, but as coded, `running_peak` only
updates when the script is actually executed — and it's currently only ever
invoked on the Monday rebalance path (Section 13). There's no separate daily
process doing this today.

**Q2**: If regime turns BEAR on Monday, do we exit all positions immediately?
**A2**: No. BEAR regime means **no new entries**. Existing positions are monitored and exited based on TSL/rank rules on subsequent Mondays.

**Q3**: Can an ETF stay in portfolio indefinitely if rank ≤ 20?
**A3**: Yes, as long as both filters pass (52wk_high + MOM_ACCEL > 0) and TSL isn't hit. The weekly rebalance will retain it.

**Q4**: How is MOM_ACCEL calculated if 1M Sharpe is NaN?
**A4**: `MOM_ACCEL_RAW` is set to NaN if either the 1M or 3M Sharpe is
unavailable (6M alone being missing is tolerated — see Section 1). NaN
propagates to `SCREEN_PASS`, so the ETF is excluded from the investable
universe that week.

**Q5**: What if fewer than 5 qualified ETFs in BULL regime?
**A5**: Remaining slots are recorded as a `"CASH"` entry in the holdings
dict with a `weight` field — this is *not* physical cash sitting anywhere,
just a bookkeeping placeholder in the JSON.

**Q6 (new)**: Is the strategy actually broken by any of the corrections in this doc?
**A6**: No — every correction above is a documentation/behavior mismatch, not
a runtime error. The script runs and produces holdings either way. The
corrections matter for (a) understanding what's *actually* driving ETF
selection (the real MOM_ACCEL formula), and (b) knowing that certain
config knobs and outputs described in v2.0 don't currently do anything or
don't currently exist.

---

## Summary of Changes by File (Corrected)

### `etf_momentum_weekly.py` (the actual live strategy)
- Self-contained MOM_ACCEL: equal-weighted raw Sharpes, single-pass z-score — **not** the 0.4/0.4/-0.2 per-window-z-scored formula
- Entry filters: 52wk_high AND MOM_ACCEL > 0 — confirmed as documented
- Exit rules: 5% TSL OR rank > `CONFIG.RANK_RETENTION` (hardcoded 20) — confirmed as documented
- Monday-only rebalance, hardcoded day-of-week check
- Regime filter with weekly cadence — confirmed as documented
- Holdings persistence: flat/overwritten `holdings_log_weekly.json`, **not** date-keyed history
- No Excel output of any kind

### `momentum_lib.py` (NOT used by this strategy)
- Contains the originally-described 0.4/0.4/-0.2 MOM_ACCEL formula, individually z-scored per window
- Also contains Clenow scores, residual momentum vs NIFTY500 — none of which this weekly ETF script uses
- Exists as a standalone general-purpose library; changes here do not affect `etf_momentum_weekly.py` unless it's imported (it currently isn't)

### `strategy_config.json`
- `WINDOW_1M`, `TSL_THRESHOLD` — confirmed working as documented
- `RANK_RETENTION_THRESHOLD` — present but silently ineffective (key-name mismatch with `CONFIG.RANK_RETENTION`)
- `REBALANCE_FREQUENCY` — present but silently ineffective (not read by any logic)
- `R2_W6M` / `R2_W3M` — leftover, unused, correspond to nothing in this script

### `holdings_log_weekly.json`
- Real and actively used, but flat/current-state only — **not** a date-keyed history as originally documented

### `etf_rankings_weekly.xlsx`
- Described in original spec, **does not exist** as working code

---

**Status: functionally running, but this document (v2.0) previously
overstated what's actually implemented in three material ways — the
MOM_ACCEL formula, the Excel output, and the holdings history format. This
v2.1 revision reconciles the spec with the real code as of 2026-07-21.**
