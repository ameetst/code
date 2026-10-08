# N750 — Excel (RANKING sheet) vs Python (Sharpe.py / momentum_lib.py) top-25 comparison

Excel file: `trading/Momentum/N750.xlsx` (as of 01-Oct-2026, cached values)
Python:     `N750_updated.xlsx` (Dhan-sourced) → `ml.compute_universe_rankings()` with `dashboard_config.json` (as of 01-Oct-2026)
No Python/Excel files were modified. Analysis scripts are in the session scratchpad only.

## 0. Verification of the method
* Python re-run reproduces the live `N750_rankings.xlsx` exactly (STLTECH 4.4946, same order).
* An independent pandas replica of the Excel formulas (S12 → SHARPE_VOL_Z-SCORES → RANKING) reproduces the cached
  Excel N_WAZS_ALL to 1e-15 and both Excel top-25 lists 25/25. So every statement below is measured, not inferred.

## 1. Two Excel ranks, one Python rank
| Column | Definition | Python equivalent |
|---|---|---|
| RANKING!A `ALL-RANK` | rank of `N_WAZS_ALL` among stocks with P/52H <= 0.25 | `RANK` (COMPOSITE) — the apples-to-apples comparison |
| RANKING!B `VA-RANK2` | rank of `N_WAZS_ALL / WA_VOL` (vol-adjusted) | none — Python has no vol-adjusted score |

## 2. Result
Excel ALL-RANK top 25 vs Python top 25: **22 / 25 names in common** (Spearman of ranks over 465 common eligible names 0.98).
Excel VA-RANK2 top 25 vs Python: 15 / 25 (different metric, expected).

| Only in Excel top 25 | Only in Python top 25 |
|---|---|
| MTARTECH (Excel 12 / Py 26), ATHERENERG (23 / 29), SAILIFE (25 / 27) | RUBICON (Excel 107 / Py 14), PAISALO (26 / 22), AVALON (27 / 25) |

Biggest rank-order moves inside the common names: SIGMAADV (Excel 1 / Py 15), E2E (15 / 9), HAPPYFORGE (17 / 19), MTARTECH, RUBICON.

## 3. Root causes (ordered by impact)

### A. Excel bug — Z9 uses the wrong column  (SHARPE_VOL_Z-SCORES!E2:E752)
`E2 = (H2-$U$1)/$V$1` standardises the **12M** score (col H, MR12) with the **9M** mean/std (U1,V1 = stats of col I).
Correct: `=IFERROR((I2-$U$1)/$V$1,0)`. Effect: the 9M leg is a second copy of the 12M leg, so 12M is double weighted and 9M is
ignored; a stock whose 12M score is "ERR" gets Z9 = 0 as well. Fixing it alone moves Excel→Python overlap 22 → 23 and changes
Excel's own top 25 by 2 names (AVALON, PAISALO in; ATHERENERG, AETHER out). Reproduced the cached values exactly with the bug, not with the fix.

### B. Different momentum formula (the structural difference)
| Item | Excel (S12 sheet) | Python (`_sharpe_ratio`) |
|---|---|---|
| Numerator | **simple** point-to-point return from calendar lookback date (365/270/180/90 days) × 1, **1.33, 2, 4** | mean daily **log** excess return over the last 252/189/126/63 bars (= annualised log return − 7% RFR) |
| RFR | none | 7% p.a. subtracted |
| Denominator | STDEV.P of **simple** daily returns × √252 (population) | std (ddof=1) of **log** excess returns × √252 |
| Window | return window (calendar) and vol window (N bars) are not the same length | one window for both |
| Short-window annualisation | rounded 1.33 / 2 / 4 (non-compounded) | exact (√252 scaling) |
Using simple returns makes multi-baggers explode (SIGMAADV simple 12M return +603% vs log +195%), so the cross-sectional z-scores are
driven by a few outliers. Stepping Excel→Python formulas one change at a time on identical prices (top-25 overlap with Python-on-Excel-data):
calendar→bar windows 24→22, simple→log numerator →20, RFR →21, log/ddof=1 vol →21 (all differences in order and 2-4 marginal names).

### C. Data-source differences (Excel = Bing/Refinitiv STOCKHISTORY, Python = Dhan data in `N750_updated.xlsx`)
_Correction (05-Oct): an earlier draft labelled the Python data as yfinance. It is Dhan: `N750_updated.xlsx` matches `dhan_datahq/base files/N750_OHLC.csv` value for value (e.g. SIGMAADV 161 bars from 06-Feb-2026, TDPOWERSYS 753.75 on 20-Aug-2026) and its NIFTY500 row is the plain price index, not yfinance's TRI._
Prices match within 0.3% for 724 of 749 common tickers. Material exceptions:
1. **SIGMAADV** — Python history starts 06-Feb-2026 (161 bars, looks like NSE-main-board listing after SME migration); Excel has 248 bars
   from Sep-2025 (153 → 1077). Python's 90%-coverage rule (`len(px) < 0.9*window`) sets S_12M/S_9M to NaN → Z = 0, so it ranks **15** instead of **1**.
   This is a Python data gap, not a formula issue.
2. **Corporate-action adjustment** — the *Excel* (Bing) series is the unadjusted one. TDPOWERSYS, PGIL and KIRLPNU show a 2:1 corporate action on 24-Aug-2026 as an overnight −50% price crash in Excel (TDPOWERSYS 1534.8 → 780.1) while the Dhan series is continuous (767.4 → 780.1); HEGAM and EMBDL differ by 39%+ the same way. This is an Excel-side data error, not a Python one. TCS/INDIAMART/GENUSPOWER etc. differ ~1-1.5% (dividend adjustment).
3. **Recent listings**: Excel returns "ERR" for 12M when the stock did not exist 1 year ago → Z12 = 0 (and Z9 = 0 via the bug above), so **RUBICON**
   (listed Oct-2025, 237 bars) ranks 107 in Excel / 303 after the bug-fix interaction, but Python accepts it (≥ 90% of 252 bars) and ranks it 14.
   TIMEX, SEDEMAC, SHADOWFAX, OMNI behave the same way (Python's rule excludes them, Excel's z of 0 handles them differently).
4. BAGMANE, BIRET, EMBASSY: no data in Python (NaN, rank blank); Excel ranks them (BAGMANE 1Y return falls back to −1 by IFERROR).
5. Excel's 249 date columns include the Sunday 01-Feb-2026 Budget session; Python's DATA sheet does not (one extra return in Excel's vol windows).

### D. Universe / eligibility differences
* Python ranks only stocks passing 52H >= −25% **and MDTV** (median daily turnover ≥ ₹1 Cr in 12M or 6M). Excel ALL-RANK has **no liquidity gate** (VOLUME sheet only displays). No effect on today's top 25 (tested), but it does change deeper ranks.
* 52H gate boundary: CHAMBLFERT, COLPAL, DBL, HYUNDAI, TRIDENT sit at −25.03% to −26.6% (Excel P/52H uses closes since 01-Oct-2025; Python uses last 252 bars) — Excel eligible, Python not. Irrelevant for top 25.
* Excel z-score statistics include the NIFTY 500 index row and duplicate tickers (PTCIL ×2, ECLERX ×2 with different values). Python excludes the benchmark and de-duplicates. Negligible on rank, but not identical.
* Python `min_cmp`/`min_market_cap` are informational (NEW-entry only), not rank gates — nothing to mirror in Excel.
* Excel's RES_MOM sheet is not wired into RANKING (nor does Python's residual momentum feed RANK) — consistent.

## 4. What to change, where, to make both identical

**Cheapest route (recommended): change Excel to Python (Python is the backtested reference).**
1. `SHARPE_VOL_Z-SCORES!E2:E752` → `=IFERROR((I2-$U$1)/$V$1,0)` (bug fix; do this regardless).
2. `S12!M:P` (and the E:H return columns they use): replace endpoint-return/vol with the Python Sharpe, per window N = 252,189,126,63, on the stock's own price row:
   `=LET(p,FILTER($Q3:$JG3,ISNUMBER($Q3:$JG3)), IF(COUNT(p)<0.9*N,"ERR", LET(w,TAKE(p,,-(N+1)), r,LN(DROP(w,,1)/DROP(w,,-1)), (AVERAGE(r)-REF!$B$8/252)/STDEV.S(r)*SQRT(252))))`
   (REF!B8 = 7% RFR already exists). Drop the 1.33/2/4 multipliers and the REF!B13:B16 calendar lookbacks from the rank path. Note: Excel prices need ≥ 253 trading days of history in the S12 spill (currently 249 from `REF!B13`): extend the start date (REF!B13 ~ 400 calendar days back) or the 12M window will use 248 bars instead of 252.
3. `RANKING!A2:A752` (ALL-RANK) → add liquidity gate: `IF(AND($G2<=0.25, OR(<12M median turnover Cr>=1, <6M median turnover Cr>=1)), …)`. VOLUME sheet has 12M/3M/1M medians only — add a 6M (126-day) median column.
4. Exclude the NIFTY 500 row and duplicate tickers from the z-score stat ranges (R1:AB1 on SHARPE_VOL_Z-SCORES use whole columns H:K).
5. Rank ties: Excel uses count-greater+1 (ties share a rank); Python uses `method="first"`. Immaterial.

**Python-side items (data, not code logic — not changed here):**
* Backfill SIGMAADV (and any SME-migrated / renamed ticker) history in `N750_updated.xlsx` (Dhan only has the NSE-EQ security from 06-Feb-2026); otherwise its 12M/9M legs are zeroed.
* (Excel side, not Python) STOCKHISTORY is unadjusted for splits/bonus: TDPOWERSYS, PGIL, KIRLPNU, HEGAM, EMBDL have fake crashes in Excel and are mis-scored there.
* BAGMANE, BIRET, EMBASSY have no price data in `N750_updated.xlsx`.
* If you want VA-RANK2 parity, Python would need a new vol-adjusted column (`COMPOSITE / mean(annualised vol)`); it does not exist today.

## 5. Expected outcome after steps 1–4 + data fixes
Rank logic becomes identical; remaining differences are only data-vendor price noise (≤0.3% on 97% of names), which can swap 1–2 names at ranks 20–30
(e.g. AETHER/SAILIFE/AVALON/PAISALO have Python composites within ~0.06 of each other: 2.695–2.753).
