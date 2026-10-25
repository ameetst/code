# ETF Momentum Strategy — Full Documentation

> **Script:** `etf_momentum_ranking.py` (live) · **Config:** `strategy_config.json` (overrides the defaults in the script's `CONFIG` class)
> **Dashboard:** `etf_dashboard.py` (Streamlit)
> **Universe:** NSE-listed ETFs (equity, sectoral, thematic, factor, gold, silver, bonds); LIQUID funds are excluded from ranking
> **Rebalance:** Weekly, first trading day of the week (Monday)
> **Monitoring:** Daily trailing-stop check via `--tsl` (live, advisory only) / applied automatically in backtests
> **Last verified against the code:** 25-Oct-2026 (`etf_momentum_ranking.py` last modified 22-Oct-2026, `strategy_config.json` 22-Oct-2026)

---

## What changed since the previous version of this document

| Item | Previous document | Live now |
|:---|:---|:---|
| Score | Weighted Sharpe: 1M 40% / 3M 40% / 6M 20% | **50% Sharpe composite + 50% Clenow-90D**, both as cross-sectional z-scores |
| Sharpe windows | 1M, 3M, 6M | **6M and 3M only**, 50/50 |
| Risk-free rate | 6% | **5%** (`DAILY_RF_ANNUAL` in `strategy_config.json`) |
| Regime | Three states (BULL 5 slots / PARTIAL 3 slots / BEAR 0) using EMA50 and EMA100 | **Binary**: Nifty 500 above its 50-day EMA = BULL, otherwise BEAR. EMA100 is display-only |
| Risk-off behaviour | Cash parked in a liquid fund, all positions evaluated for exit | **Existing positions are kept**; they close only on their own exit rules. No new buys. Vacated slots stay in cash |
| Rank exit | Not part of the rules | **Exit if investable rank > 40** |
| Rebalance style | Monthly | **Weekly hold-and-replace** |

---

## Overview

The strategy holds up to **5 ETFs, equal-weighted (20% each)**, chosen from a broad NSE ETF universe by a momentum score
that blends a risk-adjusted return measure (Sharpe) with a trend-quality measure (Clenow). A binary market regime gates
**new** purchases, and a one-ETF-per-sector cap prevents duplicate exposure.

The pipeline runs in this fixed order:

```
SCREEN  -->  SCORE  -->  REGIME  -->  ALLOCATE (hold-and-replace)
```

---

## Step 1 — Screen (investability filter)

| Parameter | Value |
|:---|:---|
| Lookback | Last 252 price rows (~1 year) |
| Max distance below 52-week high | 25% (`MAX_DRAWDOWN_FROM_HIGH = 0.25`) |
| Excluded sectors | `LIQUID` (`EXCLUDE_SECTORS`) |

```
pct_from_high = (52wk_high - current_price) / 52wk_high
PASS if pct_from_high <= 0.25 and sector not in EXCLUDE_SECTORS
```

- ETFs that fail get no investable rank and are not part of the z-score reference pool.
- ETFs with missing price/high data pass the 52-week test (no penalty for short history).
- LIQUID funds are removed because their near-zero volatility produces extreme Sharpe values that distort the z-scores.
- The 52-week-high test is the only price screen. There is no EMA-based investability filter.

---

## Step 2 — Score

Both components are z-scored **across the screen-pass ETFs only**, then blended.

### A. Sharpe composite

| Parameter | Value |
|:---|:---|
| 6M window | 126 trading days (`WINDOW_6M`) |
| 3M window | 63 trading days (`WINDOW_3M`) |
| Weights | 0.5 / 0.5 (`SHARPE_W6M`, `SHARPE_W3M`) |
| Risk-free rate | 5% p.a., applied daily (`DAILY_RF_ANNUAL`) |
| Annualisation | 252 |

```
Sharpe(window)    = mean(daily log return - rf_daily) / std(daily log return - rf_daily) * sqrt(252)
Sharpe composite  = 0.5 * Z(Sharpe_6M) + 0.5 * Z(Sharpe_3M)
```

A window needs at least `window + 1` price points, otherwise that Sharpe is NaN. If only one window is available, the
composite uses that window's z-score with its own weight (0.5) and is not rescaled.

### B. Clenow 90-day score

| Parameter | Value |
|:---|:---|
| Window | 90 trading days (`CLENOW_WINDOW`) |
| Annualisation | 250 (`CLENOW_TRADING_DAYS`) |
| Gap exclusion | Any 1-day move > 15% inside the window gives NaN (`CLENOW_GAP_MAX`) |

```
slope  = OLS slope of ln(price) over the last 90 closes
score  = (exp(slope * 250) - 1) * 100 * R^2
Clenow z = Z(score)
```

### C. Final blended score and rank

```
Blend = BLEND_W_SHARPE * Sharpe composite + BLEND_W_CLENOW * Clenow z      (0.5 / 0.5)
RANK_INVESTABLE = rank of Blend among screen-pass ETFs (1 = best)
```

- If one component is NaN for an ETF, the other is used at its own weight (not rescaled).
- Setting `BLEND_W_CLENOW = 0` reproduces the earlier Sharpe-only ranking exactly.
- The Rankings sheet also shows universe-wide `WTD_SHARPE`, `WTD_CLENOW` and `WTD_BLEND` (z-scored over all ETFs except excluded sectors). These are for display; allocation uses `RANK_INVESTABLE`.

### Reference columns (output only)

`EMA 100`, `% From EOM` (return since the last trading day of the previous month), `% From 52Wk High`.

---

## Step 3 — Regime filter (binary)

| State | Condition | Effect |
|:---|:---|:---|
| **BULL (risk-on)** | Nifty 500 price > its 50-day EMA | Up to 5 positions; open slots are refilled from the ranking |
| **BEAR (risk-off)** | Nifty 500 price <= its 50-day EMA | **No new buys.** Existing holdings stay until their own exit rule fires; vacated slots sit in cash |

```
risk_on = nifty_price > EMA50(nifty)          # EMA with span 50, adjust=False
```

**Trend source, in priority order:**
1. Live Nifty 500 index via yfinance (`REGIME_INDEX_TICKER = ^CRSLDX`)
2. `REGIME_TICKER = MONIFTY500` from the ETF price file
3. `REGIME_FALLBACKS`: `BSE500IETF`, `HDFCBSE500`, `NIFTYBEES`
4. If nothing is available, the regime defaults to BULL (a warning is printed)

With fewer than 50 observations the regime also defaults to BULL. `TREND_EMA_WINDOW` (100) is computed for display only.

---

## Step 4 — Allocation (weekly hold-and-replace)

| Parameter | Value |
|:---|:---|
| Slots | 5 (`TOP_N`) |
| Position size | Equal weight, 1/5 of the portfolio per slot |
| Sector cap | 1 ETF per sector (`SECTOR_CAP`) |
| Empty slots | Held as cash |

Each weekly run:
1. **Evaluate every held position** against the exit rules below. Any that trigger are closed.
2. **Keep every survivor**, regardless of regime.
3. **If risk-on**, fill open slots (up to 5) by walking down `RANK_INVESTABLE`, skipping ETFs already held and ETFs whose sector is already filled.
4. **If risk-off**, do not open positions. Open slots stay in cash.
5. On the first run (no previous allocation) it behaves as a fresh top-5 pick when risk-on, or all cash when risk-off.

The previous allocation comes from `holdings_log.json` (keyed by week), or from the positions ledger when one is seeded.

Because replacements are drawn from the full ranked list after the sector cap, a new buy can have a rank well below 5.

---

## Entry criteria

An ETF is bought only when **all** of these hold:

1. It passes the 52-week-high screen and is not in an excluded sector
2. It is the next-best-ranked investable ETF after skipping held names and capped sectors
3. The regime is BULL
4. A slot is open

## Exit criteria

A held position is closed if **any** of these fires:

| Trigger | Rule | When checked |
|:---|:---|:---|
| **52-week-high drawdown** | More than 25% below the 52-week high (`EXIT_MAX_DD_FROM_HIGH`) | Weekly |
| **Rank degradation** | `RANK_INVESTABLE` > 40 (`EXIT_MAX_RANK`), or the ticker is no longer in the ranking | Weekly |
| **Trailing stop** | Drawdown from its peak since entry >= 5% (`TSL_THRESHOLD`) | Daily via `--tsl` (advisory, no auto-sell); also evaluated at the weekly rebalance |

The regime never closes a position on its own in the live rules.

---

## Variants and paper trading

`STRATEGY_VARIANT` (currently `live`) selects the rule set. A second variant is paper-traded alongside it:

| Variant | Regime | On risk-off | Rank exit |
|:---|:---|:---|:---|
| `live` | Price vs 50-day EMA | Hold existing, no new buys | > 40 |
| `v2_trend200` | Price vs 200-day SMA | **Liquidate all positions** | > 60 |

With `PAPER_TRADE_ENABLED = true`, every run also updates a simulated portfolio running `PAPER_VARIANT` (currently `v2_trend200`): ₹10,00,000 start capital, 20 bps one-way slippage on fills, and no real orders. It writes only to `paper_trade_log.json`. It is skipped if the price data is more than 7 days old (`PAPER_MAX_DATA_AGE_DAYS`).

---

## Monitoring schedule

| Event | Frequency | Action |
|:---|:---|:---|
| Portfolio rebalance | Weekly, Monday / first trading day | Evaluate exits, check regime, refill open slots |
| TSL check | Daily | `python etf_momentum_ranking.py --tsl` fetches live NAVs via yfinance for held ETFs, compares them with stored peaks and flags breaches |
| Paper portfolio only | On demand | `python etf_momentum_ranking.py --paper [--force]` |

```bash
# Weekly rebalance (standard run; optional args: input file, output file)
python etf_momentum_ranking.py

# Daily trailing-stop check
python etf_momentum_ranking.py --tsl

# Dashboard
streamlit run etf_dashboard.py
```

---

## Configuration (`strategy_config.json`, values currently in force)

```json
{
  "INPUT_FILE": "ETF_updated.xlsx",   "OUTPUT_FILE": "etf_rankings.xlsx",
  "WINDOW_6M": 126, "WINDOW_3M": 63,  "ANNUALIZE": 252,
  "TOP_N": 5,       "TOP_N_PARTIAL": 3,
  "MAX_DRAWDOWN_FROM_HIGH": 0.25,     "EXCLUDE_SECTORS": ["LIQUID"],
  "SHARPE_W6M": 0.5, "SHARPE_W3M": 0.5,
  "CLENOW_WINDOW": 90, "CLENOW_TRADING_DAYS": 250, "CLENOW_GAP_MAX": 0.15,
  "BLEND_W_SHARPE": 0.5, "BLEND_W_CLENOW": 0.5,
  "REGIME_TICKER": "MONIFTY500", "REGIME_FALLBACKS": ["BSE500IETF", "HDFCBSE500", "NIFTYBEES"],
  "REGIME_INDEX_TICKER": "^CRSLDX",
  "TREND_FAST_EMA_WINDOW": 50, "TREND_EMA_WINDOW": 100,
  "SECTOR_CAP": 1, "DAILY_RF_ANNUAL": 0.05,
  "TSL_THRESHOLD": 0.05, "EXIT_MAX_DD_FROM_HIGH": 0.25, "EXIT_MAX_RANK": 40,
  "HISTORY_PERIODS": 52,
  "STRATEGY_VARIANT": "live", "PAPER_TRADE_ENABLED": true, "PAPER_VARIANT": "v2_trend200",
  "PAPER_START_CAPITAL": 1000000, "PAPER_SLIPPAGE_BPS": 20, "PAPER_MAX_DATA_AGE_DAYS": 7
}
```

Notes on defaults in the script vs the JSON:
- `TOP_N_PARTIAL` is unused by the binary regime; it is kept for compatibility with older logs.
- The `CONFIG` class in the script carries a 7% risk-free default, but the JSON sets 5%, so **5% is what runs**.

---

## Data input (`ETF_updated.xlsx`)

| Layout | Content |
|:---|:---|
| Sheet | `DATA` |
| Column A | Ticker (the current layout has no ETF name column; names are enriched from a legacy `ETF.xlsx` in the same folder when present) |
| Columns B+ | Daily prices with real, static dates in row 1 |

- Zeros are treated as missing.
- A legacy layout (`ETF_NAME`, `TICKER`, prices) is also supported, with dates rebuilt as the last N business days.
- Sector comes from `ETF_SECTOR.xlsx` / name-keyword classification (`classify_sector`).
- `etf_update_prices_dhan.py` is the price-refresh script in this folder.

---

## Output files

### `etf_rankings.xlsx` — sheets: Rankings, Rebalance, Allocation, Regime

| Sheet | Description |
|:---|:---|
| **Rankings** | All ETFs, investable rank first, with all scoring columns |
| **Rebalance** | Current allocation, BUY/SELL/HOLD changes versus the previous week, and a history grid (`HISTORY_PERIODS = 52` weeks) |
| **Allocation** | The 5 slots with sector, weight and the reason each ETF was selected, held or skipped |
| **Regime** | Nifty 500 price, EMA50, EMA100, label and active slots |

### Supporting files

| File | Description |
|:---|:---|
| `holdings_log.json` | Weekly model-allocation history. Maintained by the script; do not edit manually |
| `ETF_tradelog.json` / `ETF_positions_ledger.json` | Real trades and positions used by the dashboard's Tradelog & MTM tab |
| `paper_trade_log.json` | Simulated portfolio for the paper variant |

### Key Rankings-sheet columns

`RANK_INVESTABLE`, `SCREEN_PASS`, `SHARPE_6M`, `SHARPE_3M`, `CLENOW_90D`, `WTD_SHARPE`, `WTD_CLENOW`, `WTD_BLEND`, `PCT_FROM_HIGH`, `EOM_PCT`, `EMA_100`, `SECTOR`.

### Rebalance sheet change codes

BUY, SELL, ADD, TRIM, HOLD, REGIME.

### Dashboard tabs

Current Recommendation · Full Rankings · Configuration · Tradelog & MTM (active holdings with exit-rule alerts) · Paper Trade (v2).

---

## Backtest evidence (as of the 18–19 Oct 2026 runs)

Backtest engine (`backtest/etf_backtest_current_vs_nifty500.py` and the scripts built on it): weekly hold-and-replace, binary Nifty 500 > EMA50 regime, top 5, sector cap 1, the three exit rules above, idle cash earning 2% p.a., and a **flat ₹20 per trade leg with no spread or slippage**. Data: `History_ETFs.xlsx`, 283 tickers, 2016-09-12 to 2026-09-10, with 70 bad-print fixes applied.

Source: `backtest/noentry_run.log`. "Sharpe" in these logs is CAGR divided by volatility, with no risk-free rate subtracted.

| Full period 2016-09 to 2026-09 | CAGR | Volatility | Max drawdown | CAGR / vol |
|:---|:---:|:---:|:---:|:---:|
| **Live algorithm (Sharpe + Clenow blend, 52-week-high filter)** | 12.21% | 13.3% | -21.7% | 0.92 |
| Previous algorithm (Sharpe only, 52-week-high filter) | 10.71% | 12.5% | -21.5% | 0.85 |
| Nifty 500 buy & hold | 11.96% | 15.8% | -38.3% | 0.76 |

Trading profile of the live algorithm: 185 buys, 52% win rate, average hold 74 days, notional traded about 8.6x average equity per year.

**Cost sensitivity.** An estimate made by deducting a one-way cost from the saved equity curve at each trade date, over 2017-09-11 to 2026-09-10 (the first trade date):

| Assumption | CAGR | Max drawdown |
|:---|:---:|:---:|
| As backtested (₹20 per leg) | 13.47% | -21.7% |
| 10 bps one-way | 12.57% | -22.4% |
| 20 bps one-way | 11.67% | -23.1% |
| 30 bps one-way | 10.78% | -23.8% |
| Nifty 500 buy & hold (same dates) | 11.23% | -38.3% |

At realistic ETF spreads and impact (the paper portfolio assumes 20 bps), the return advantage over the index is small. The main benefit is the much shallower drawdown (about -23% against -38%).

Related runs: `blend_methods_noliquid_run.log` (blended vs Clenow-only vs Sharpe-only sleeves), `momaccel_live_run.log` (momentum-acceleration screen, not adopted), `noentry_run.log` (removing the 52-week-high entry filter, not adopted).

---

## Known limitations

- Backtests use a flat ₹20 per trade and no spread or market impact. Treat the cost-sensitivity table above as the more realistic picture.
- The 52-week-high entry filter adds about 0.6 percentage points of CAGR over the full period (12.21% with it, 11.60% without), most of it in the 2018–2020 sub-period (8.5% against 6.8% CAGR).
- The strategy was tuned on one 10-year sample of this ETF universe; the number of backtest variants run raises the risk of overfitting.
