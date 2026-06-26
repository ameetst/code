# Backtest.md — Daily Breakout Strategy Backtester

This document explains how `backtest.py` works: what it does at each stage, what assumptions are baked in, how to run it, and how to interpret the outputs.

---

## 1. Purpose

`backtest.py` replays the Daily Breakout Strategy over 5 years of historical data to answer the question: *how would this system have performed if run mechanically, every day, from the start?*

It is a **walk-forward portfolio simulation** — not a trade-by-trade analysis. At every trading day it holds a live portfolio, generates signals using only data available on that day, executes orders at the following day's open, and tracks the equity curve dollar-for-dollar.

---

## 2. Dependencies

`backtest.py` must live in the same directory as `data_engine.py` and `strategy.py`. It imports the fetch functions from `data_engine.py` directly.

**Python packages required:**

```
pandas, numpy, scipy, yfinance, matplotlib
```

Install with:

```bash
pip install pandas numpy scipy yfinance matplotlib
```

---

## 3. How to Run

```bash
# Default: 5 years, ₹10 lakh starting capital
python backtest.py

# Custom parameters
python backtest.py --years 5 --capital 5000000 --r2 0.65 --vchk 1.8

# Re-use previously downloaded data (skip the slow download step)
python backtest.py --data backtest_results/market_data_5y.pkl

# Full options
python backtest.py --help
```

### CLI Arguments

| Argument | Default | Description |
|---|---|---|
| `--years` | `5` | How many years of history to fetch and simulate |
| `--capital` | `1000000` | Starting portfolio value in INR |
| `--csv` | `ind_niftytotalmarket_list (3).csv` | Path to the Nifty Total Market universe file |
| `--r2` | `0.60` | R-Squared smoothness threshold for entry |
| `--vchk` | `1.5` | Volume expansion ratio threshold for entry |
| `--out` | `backtest_results/` | Output directory for all result files |
| `--data` | *(none)* | Path to a pre-downloaded `.pkl` file to skip re-download |

---

## 4. Internal Architecture

The script is structured as four independent stages that run in sequence.

### Stage 1 — Data Download

On first run, `fetch_market_data()` and `fetch_index_data()` from `data_engine.py` are called to download 5 years of daily OHLCV data for all ~750 Nifty Total Market tickers plus the Nifty 500 index (`^CRSLDX`) via yfinance.

Data is saved immediately to:
- `backtest_results/market_data_5y.pkl`
- `backtest_results/index_data_5y.pkl`

On subsequent runs, the cached `.pkl` files are loaded directly — no re-download. Pass `--data` to point at an existing file explicitly.

**Expected download time:** 10–20 minutes for 750 tickers × 5 years on a typical broadband connection.

---

### Stage 2 — Indicator Pre-computation (`compute_indicators_full`)

Before the simulation loop starts, every indicator is computed over the full price history of every ticker in one vectorised pass. This avoids recomputing indicators inside the daily loop and makes the simulation fast.

All calculations are **strictly causal** — rolling windows and `.shift()` operations only look backward. There is no look-ahead bias.

| Indicator | Calculation |
|---|---|
| `50EMA` / `200EMA` | Exponential moving averages, `adjust=False` |
| `52W_High` | Rolling 252-day max of High |
| `P52H` | `Close / 52W_High` |
| `3M_High_Lag` | Rolling 63-day max of High, shifted back 5 days |
| `6M_High_Lag` | Rolling 126-day max of High, shifted back 5 days |
| `3M_Low_Lag` | Rolling 63-day min of Low, shifted back 5 days |
| `3M_BO` | `(Close − 3M_High_Lag) / 3M_High_Lag` |
| `6M_BO` | `(Close − 6M_High_Lag) / 6M_High_Lag` |
| `VCHK` | `Median Volume (21d) / Median Volume (prior 63d)` |
| `INR_VOL` | Rolling 21-day median of `Volume × Close` |
| `R2` | Rolling 63-day R² of a linear regression on Close prices |
| `RS_3M` | `Stock 3M return − Nifty 500 3M return` |

The R² calculation (`_r2_rolling`) uses `.rolling().apply()` with a raw numpy function — it is the most CPU-intensive step (~3–5 minutes for 750 stocks).

The **regime** is computed separately from the Nifty 500 index as a boolean Series: `True` where `Nifty 500 Close > 50EMA`.

---

### Stage 3 — Portfolio Simulation (`run_backtest`)

The simulation walks forward through every trading day in chronological order. On each day, three things happen in order:

#### Step 1 — Check exits (end of day)

For each open position, the current close is compared to the highest close seen since entry. If the current close is ≤ 90% of that peak (`high_since_entry × 0.90`), the position is flagged for a **Trailing Stop Loss (TSL)** exit.

The TSL check runs on today's close but the exit itself executes tomorrow.

#### Step 2 — Execute exits (T+1 open)

Flagged positions are exited at the **next trading day's Open price**.

**Gap-down protection:** if the T+1 open is *below* the theoretical stop price, the exit is forced at the actual open — not the stop. This accurately models the real-world scenario where a stock gaps down past your stop overnight.

A 0.15% slippage/brokerage cost is deducted from the exit price. Proceeds are returned to cash.

Each closed trade is recorded in the trade log with: ticker, entry date, exit date, entry price, exit price, shares, cost, proceeds, P&L (INR), P&L (%), and exit reason.

#### Step 3 — Generate entries (T+1 open)

If the regime is Bullish and there are open position slots (max 10), `generate_signals()` evaluates every stock against all 6 entry rules using today's indicator values:

1. `Close > 50EMA > 200EMA`
2. `3M_BO > 0` AND `6M_BO < 0`
3. `3M_BO < 0.10` (within 10% of breakout level)
4. `VCHK > vchk_threshold`
5. `INR_VOL ≥ ₹1 Crore`
6. `R2 > r2_threshold`

Passing stocks are ranked by **RS_3M descending** (strongest relative performers first). If RS_3M is unavailable, ranking falls back to P52H descending.

Already-held tickers are excluded. The top N candidates fill the available slots, where N = `MAX_POSITIONS − open_positions`.

Entries execute at the **next trading day's Open price** + 0.15% slippage.

**Position sizing:** each new position allocates exactly 10% of current total equity (cash + mark-to-market value of all open positions). If available cash is less than the 10% allocation, cash is the ceiling.

#### Step 4 — Mark to market

After all exits and entries are resolved, the portfolio is valued at today's close prices and recorded in the equity curve.

#### End-of-backtest

Any positions still open on the final day are force-closed at the last available close price (minus slippage) and recorded in the trade log with `exit_reason = END_OF_BACKTEST`.

---

### Stage 4 — Statistics & Outputs (`compute_stats`, `plot_equity_curve`)

After the simulation completes, performance statistics are calculated and four output files are written to the `--out` directory.

---

## 5. Outputs

All outputs are written to `backtest_results/` (or the directory specified by `--out`).

### `trade_log.csv`

One row per closed trade. Columns:

| Column | Description |
|---|---|
| `ticker` | NSE symbol (without `.NS`) |
| `entry_date` | Date position was opened (T+1 from signal) |
| `exit_date` | Date position was closed |
| `entry_price` | Actual buy price including slippage |
| `exit_price` | Actual sell price net of slippage |
| `shares` | Number of shares held |
| `cost` | Total capital deployed (entry_price × shares) |
| `proceeds` | Total received on exit |
| `pnl` | Absolute profit/loss in INR |
| `pnl_pct` | Percentage return on the position |
| `exit_reason` | `TSL` or `END_OF_BACKTEST` |

### `equity_curve.csv`

Daily mark-to-market portfolio value. Two columns: `date` and `equity`.

### `equity_curve.png`

A two-panel dark-themed chart at 150 DPI:

**Top panel** — Strategy equity (green) vs Nifty 500 rebased to starting capital (blue dashed). Final values annotated. A subtitle bar shows the key summary stats.

**Bottom panel** — Portfolio drawdown as a filled red area, expressed as a percentage from the running peak.

### `summary.txt`

Plain-text performance summary printed to the console and saved to disk. Contains:

| Metric | Description |
|---|---|
| Initial Capital | Starting portfolio value |
| Final Equity | Ending portfolio value |
| Total Return | Overall percentage gain/loss |
| CAGR | Compound Annual Growth Rate |
| Sharpe Ratio | Annualised Sharpe (daily returns, 252 trading days) |
| Max Drawdown | Largest peak-to-trough decline |
| Benchmark Return | Nifty 500 total return over the same period |
| Benchmark CAGR | Nifty 500 CAGR over the same period |
| Total Closed Trades | Count of completed trades (excludes open at end) |
| Win Rate | % of closed trades with positive P&L |
| Avg Win | Average return of winning trades |
| Avg Loss | Average return of losing trades |
| Profit Factor | Gross profit / gross loss |
| Backtest Period | Start and end dates of simulation |

---

## 6. Key Assumptions & Limitations

**Survivorship bias.** The backtest uses the *current* Nifty Total Market constituents. Stocks that were delisted, merged, or went bankrupt over the 5-year period are not included. This overstates performance — in reality, some of those stocks would have been held and lost money.

**Point-in-time data.** yfinance provides adjusted historical prices but does not indicate when a stock joined or left the index. Stocks are treated as if they were always in the universe.

**No sector cap.** The sector cap (max 2 positions per sector) documented in Strategy.md is not yet implemented. In concentrated market environments (e.g., an IT or financial sector bull run), the portfolio may take correlated positions.

**Liquidity assumption.** The INR_VOL floor (₹1 Crore/day) screens for tradeable stocks, but the backtest does not model market impact. Large positions in smaller stocks could move the price in practice.

**Fixed TSL.** A flat 10% trailing stop is applied uniformly across all stocks regardless of their individual volatility. A stock with 40% annualised volatility and a 10% stop will be stopped out frequently on noise. ATR-based stops would be more robust.

**No partial exits.** Positions are always exited in full when the TSL triggers. No profit-taking or partial position reduction is modelled.

---

## 7. Extending the Backtest

The modular structure makes it straightforward to extend:

- **Sector cap:** add a sector mapping CSV and filter `generate_signals()` to enforce max 2 per sector before filling slots
- **ATR-based TSL:** replace the `0.90 × high_since_entry` check with `high_since_entry − N × ATR`
- **Parameter sweep:** wrap `run_backtest()` in a loop over `r2_threshold` and `vchk_threshold` values to find optimal parameters (be cautious of overfitting)
- **Walk-forward validation:** split the 5-year window into in-sample (train) and out-of-sample (test) periods and run separately
