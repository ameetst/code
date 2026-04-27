# ETF Momentum Strategy — Full Documentation

> **Script:** `etf_momentum_ranking.py`
> **Universe:** NSE-listed ETFs (equity, sectoral, thematic, factor, gold, silver, bonds)
> **Rebalance:** Monthly (first trading day of month)
> **Monitoring:** Daily trailing stop loss via `--tsl` flag (live) / automatic (backtest)

---

## Overview

The strategy selects the **Top 5 ETFs** from a broad universe using a momentum-based ranking engine.
A tiered market regime filter dynamically adjusts position count based on market conditions, and
a sector cap enforces diversification across themes.

The pipeline executes in this fixed order:

```
SCREEN  -->  SCORE  -->  REGIME  -->  ALLOCATE
```

---

## Step 1 — Screen (Investability Filter)

Each ETF must pass a hard filter before it is eligible for ranking:

### 52-Week High Proximity Filter

| Parameter | Value |
|:---|:---|
| Lookback window | 252 trailing trading days (~1 year) |
| Max drawdown from high | 25% |
| Rule | ETF NAV must be >= 75% of its 52-week high |

```
pct_from_high = (52wk_high - current_NAV) / 52wk_high
PASS  if  pct_from_high <= 0.25
FAIL  otherwise  (ETF excluded from investable universe)
```

**Rationale:** Removes ETFs in sustained downtrends or bouncing off bottoms. Only ETFs close to
their structural highs exhibit genuine upward momentum.

> Note: 52-week high and current NAV are both derived dynamically from the historical price grid
> in ETF.xlsx — no manual input required.

---

## Step 2 — Score (Ranking Metrics)

All ETFs in the universe are scored on two metrics. Only screen-pass ETFs are ranked for
allocation purposes.

### A. Weighted Sharpe — Primary Ranking Metric (used for allocation)

| Parameter | Value |
|:---|:---|
| 6-Month Sharpe weight | 50% |
| 3-Month Sharpe weight | 50% |
| Lookback 6M | 126 trading days |
| Lookback 3M | 63 trading days |
| Risk-free rate | 7% p.a. (0.07 / 252 per day) |
| Annualisation factor | 252 |

```
Sharpe(window)    = (mean(excess log returns) / std(excess log returns)) * sqrt(252)
Weighted Sharpe   = 0.5 * Sharpe_6M + 0.5 * Sharpe_3M
```

**Data rules:**
- ETFs with < 3 months of data are excluded entirely
- ETFs with 3–6 months of data: Sharpe_6M is set to 0 (3M Sharpe contributes fully)

### B. SR2 Blend — Reference Metric (displayed only, not used for allocation)

Sharpe × R-squared: rewards ETFs with both strong risk-adjusted returns AND a clean, consistent
upward price trend. ETFs with choppy/erratic NAVs are penalised through the R² component.

```
R2(window)    = R-squared of log-linear regression over last N trading days
SR2_BLEND     = (Sharpe_6M x R2_6M + Sharpe_3M x R2_3M) / 2
```

R² = 1.0 means a perfectly linear trend; R² = 0 means random noise.

### C. Additional Reference Columns (output only)

| Column | Description |
|:---|:---|
| EMA 100 | 100-day exponential moving average of ETF NAV |
| % From EOM | Return since last trading day of previous month |
| % From 52Wk High | Drawdown from the 52-week peak |

---

## Step 3 — Regime Filter

**Proxy ticker:** `MONIFTY500` (Mirae Asset Nifty 500 ETF)
**Fallbacks (in order):** `BSE500IETF` → `HDFCBSE500` → `NIFTYBEES`
**In backtest:** `^CRSLDX` (Nifty 500 index via Yahoo Finance — real historical data)

**Rationale for Nifty 500:** Covers large, mid, and small caps. Mid/small caps typically roll over
before large caps during Indian market corrections — this makes Nifty 500 a more sensitive
early-warning indicator than Nifty 50.

### Three-State Tiered Regime

| State | Condition | Active Slots | Portfolio Action |
|:---|:---|:---:|:---|
| **BULL** | EMA50 > EMA100 AND Price > EMA50 | **5** | Fully invested — enter Top 5 ETFs |
| **PARTIAL** | Price > EMA100 (but not BULL) | **3** | Top 3 ETFs held; 2 slots idle in cash |
| **BEAR** | Price <= EMA100 | **0** | 100% cash (liquid fund) |

```
EMA50  = 50-day EMA of MONIFTY500
EMA100 = 100-day EMA of MONIFTY500

if   EMA50 > EMA100  AND  Price > EMA50   ->  BULL    (5 slots)
elif Price > EMA100  AND  Price < EMA50   ->  PARTIAL (3 slots)
elif Price < EMA100  AND  Price < EMA50   ->  BEAR    (0 slots, full cash)
else                                      ->  BEAR    (0 slots, full cash)
```

> Regime is evaluated using the **last trading day of the previous month's close**.
> No look-ahead bias.

### PARTIAL Regime — How slots are filled

In PARTIAL, the top 3 from the investable ranked list (sorted by **Weighted Sharpe**, same as BULL)
are selected. The same sector cap (1 per sector) applies. The remaining 2 slots stay in cash
earning 2% p.a. All other logic (rebalance, TSL) is identical.

PARTIAL acts as an **early warning buffer** — it reduces exposure before the EMA50/EMA100
crossover (BEAR) is confirmed, but without going to full cash prematurely.

---

## Step 4 — Allocation

| Parameter | Value |
|:---|:---|
| Max slots — BULL | 5 ETFs |
| Max slots — PARTIAL | 3 ETFs |
| Max slots — BEAR | 0 (100% cash) |
| Position sizing | Equal weight — 1/5th of total portfolio per slot |
| Sector cap | 1 ETF per sector |
| Transaction cost | INR 20 per trade leg |

### Sector Cap

A maximum of 1 ETF per sector is included in the allocation. This prevents concentration in
multiple ETFs tracking the same underlying index (e.g., two Nifty Banking ETFs).

Sectors are auto-classified from the ETF name using keyword matching (most-specific first):
PSU_BANK, PRIVATE_BANK, BANKING_BROAD, IT_TECH, HEALTHCARE, METAL, ENERGY, INFRA,
CONSUMPTION, REALTY, DEFENCE, PSE, AUTO, CHEMICALS, FIN_SERVICES, GOLD, SILVER,
GOVT_BONDS, FACTOR_MOMENTUM, FACTOR_VALUE, FACTOR_QUALITY, FACTOR_LOW_VOL,
INTERNATIONAL, MIDCAP, SMALLCAP, BROAD_MARKET, and more.

---

## Entry Criteria

An ETF enters the portfolio when ALL of the following are simultaneously true:

1. **Screen PASS** — NAV is within 25% of its 52-week high
2. **Top Weighted Sharpe rank** — ranks in the top N among all screen-pass ETFs, after sector cap
3. **Regime allows entry** — current regime is BULL (5 slots) or PARTIAL (3 slots)
4. **Sector slot available** — no other ETF from the same sector is already selected

---

## Exit Criteria

| Trigger | Timing | Description |
|:---|:---|:---|
| **Monthly Rebalance** | First trading day of each month | ALL positions are exited unconditionally. Fresh rankings are computed and new positions entered from scratch based on the current regime. |
| **Trailing Stop Loss (10%)** | Daily — intra-month | If any position drops 10% from its intra-month running peak (highest NAV since entry), it is immediately exited. Capital stays in cash (2% p.a.) until the next monthly rebalance. |
| **Regime turns BEAR** | Next monthly rebalance | No new positions are entered. All existing positions are exited at the start of the next month. Capital goes to cash. |

> The monthly rebalance exits **all** positions unconditionally — including profitable ones.
> This ensures rankings and regime state are always freshly evaluated without carrying stale bets.

---

## Monitoring Schedule

| Event | Frequency | Action |
|:---|:---|:---|
| Portfolio rebalance | Monthly — first trading day | Exit all → evaluate regime → enter Top N |
| TSL check | Daily — via `python etf_momentum_ranking.py --tsl` | Fetches live NAVs via yfinance, compares against stored peaks, alerts on ≥10% drawdown |
| Regime evaluation | Monthly — at rebalance | Uses previous month-end close of MONIFTY500 |
| Cash interest accrual | Daily | 2% p.a. on all idle/cash capital |

---

## Backtest Performance (Apr 2020 – Apr 2026 | INR 10L start | 10% TSL | INR 20/trade)

| Metric | **Strategy (Run 1)** | Nifty 500 | Strategy Edge |
|:---|:---:|:---:|:---:|
| **CAGR** | **22.23%** | 20.92% | +1.31% |
| **Total Return** | **233%** | ~170% | +63% |
| **Max Drawdown** | **-15.67%** | -18.84% | +3.17% less DD |
| **Annual Volatility** | **12.43%** | ~16% | Lower risk |
| **Sharpe Ratio** | **1.79** | 1.30 | +0.49 |
| **Win Rate (closed trades)** | **70.6%** | — | — |

### Alternative Regime Configurations Tested

| Config | CAGR | Max DD | Sharpe | Verdict |
|:---|:---:|:---:|:---:|:---|
| **Run 1** — EMA100 / PARTIAL-BEAR / Daily TSL | **22.23%** | **-15.67%** | **1.79** | Best overall |
| EMA100 / BULL-HOLD / Weekly TSL | 21.42% | -22.40% | 1.24 | More risk, less return |
| EMA200 / BULL-HOLD / Weekly TSL | 23.05% | -22.40% | 1.32 | More return, much more risk |

*Run 1's PARTIAL state acts as an early-warning buffer — it cuts exposure before a full EMA crossover is confirmed, which is why Max DD and Volatility are significantly lower than HOLD-based alternatives.*

---

## Configuration Reference

```python
# etf_momentum_ranking.py  CONFIG class

INPUT_FILE             = "ETF.xlsx"
OUTPUT_FILE            = "etf_rankings.xlsx"

WINDOW_6M              = 126          # 6-month lookback (trading days)
WINDOW_3M              = 63           # 3-month lookback (trading days)
ANNUALIZE              = 252          # annualisation factor

TOP_N                  = 5            # active slots in BULL regime
TOP_N_PARTIAL          = 3            # active slots in PARTIAL regime
MAX_DRAWDOWN_FROM_HIGH = 0.25         # 52-week high screen threshold (25%)

SHARPE_W6M             = 0.5          # weight of 6M Sharpe in composite
SHARPE_W3M             = 0.5          # weight of 3M Sharpe in composite

REGIME_TICKER          = "MONIFTY500"
REGIME_FALLBACKS       = ["BSE500IETF", "HDFCBSE500", "NIFTYBEES"]
TREND_FAST_EMA_WINDOW  = 50           # fast EMA for regime (EMA50)
TREND_EMA_WINDOW       = 100          # slow EMA for regime (EMA100)

SECTOR_CAP             = 1            # max ETFs per sector in allocation
DAILY_RF               = 0.07 / 252  # daily risk-free rate (7% p.a.)
TSL_THRESHOLD          = 0.10         # 10% trailing stop loss (live + backtest)

# Backtest only (etf_backtest.py)
CASH_INTEREST_PA       = 0.02         # 2% p.a. interest on idle cash
TRADE_COST_FIXED       = 20.0         # INR per trade leg
```

### Live TSL Monitoring

```bash
# Monthly rebalance (standard run)
python etf_momentum_ranking.py

# Daily TSL check — fetches live NAVs via yfinance for held ETFs
python etf_momentum_ranking.py --tsl
```

The `--tsl` flag loads `holdings_log.json`, fetches real-time prices for the 3-5 held
positions via yfinance (appends `.NS` for NSE), and displays a dashboard showing:
- Entry price, peak price, TSL trigger price, current NAV, drawdown %
- Flags any position with ≥10% drawdown as `!! TSL BREACH !!`
- Advisory only — does not auto-sell. Updated peaks are saved back to `holdings_log.json`.

---

## Data Input Format (ETF.xlsx)

| Column | Content |
|:---|:---|
| Column A | ETF full name |
| Column B | NSE ticker symbol |
| Column C+ | Historical daily NAV — dates auto-generated in Row 1 via Excel formula |

**Notes:**
- Zeros treated as missing data (NaN)
- Data forward-filled across trading holidays and weekends
- 52-week high and current price derived dynamically — no static input columns needed
- Date headers in Row 1 are a dynamic Excel array formula (`=TRANSPOSE(FILTER(...SEQUENCE...TODAY()...))`)
  The ranking script reads prices directly via openpyxl and reconstructs dates using `pd.bdate_range`

---

## Output Files

| File | Description |
|:---|:---|
| `etf_rankings.xlsx` | Full ranked output with all metrics, regime status, and allocation |
| `backtest_trade_log.csv` | Every buy/sell with entry/exit date, price, P&L, and exit reason |
| `backtest_equity.csv` | Daily portfolio equity series |
| `equity_curve.png` | Equity curve chart vs Nifty 500 benchmark |

### Key Output Columns in etf_rankings.xlsx

| Column | Description |
|:---|:---|
| `RANK_INVESTABLE` | Rank within screen-pass universe — drives allocation |
| `RANK_UNIVERSE` | Rank across all ETFs regardless of screen |
| `SCREEN_PASS` | True/False — 52-week high proximity filter |
| `WTD_SHARPE` | Primary ranking metric |
| `SR2_BLEND` | Reference metric (Sharpe x R² blend) |
| `SHARPE_6M` / `SHARPE_3M` | Component Sharpe ratios |
| `R2_6M` / `R2_3M` | Trend consistency scores |
| `% From 52Wk High` | Drawdown from 52-week peak |
| `% From EOM` | Return since prev month-end |
| `EMA 100` | 100-day EMA of ETF NAV |
