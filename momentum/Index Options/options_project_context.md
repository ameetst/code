# NSE Options Trading System — Project Context

## Purpose
Share this file (along with `etf_momentum_project_context.md`) with Claude
to resume work on the options trading system in a new session.

---

## System Overview

Three Python scripts in the same folder. They share a common set of JSON
files and read the ETF momentum system outputs as upstream inputs.

### Scripts

| Script | Purpose | When to run |
|---|---|---|
| `options_trader.py` | Generates new buy signals | Weekly — Monday or Friday |
| `options_monitor.py` | MTM check + exit alerts | Daily — after market close (3:35–3:45pm IST) |
| `options_tradelog.py` | Closes trades, tracks P&L | When exiting a position |

### Files maintained automatically (never edit manually)

| File | Contents |
|---|---|
| `signals_log.json` | All signals ever generated — status is ACTIVE or CLOSED |
| `trade_log.json` | Closed trades with exit price, exit reason, P&L |
| `iv_history.json` | Rolling daily ATM IV per index — builds up week by week |

### Manual input files

Option chain CSVs downloaded from **nseindia.com/option-chain**:
- Select index → select expiry → click CSV download button
- Place in same folder as scripts — **do NOT rename**
- NSE names them: `option-chain-ED-NIFTY-27-Mar-2026.csv`
- Script auto-detects index name and expiry from filename
- Download one file per index per expiry
- For best results: download both weekly AND monthly expiry CSVs

### Upstream inputs (read-only, from ETF system)

| File | What is read |
|---|---|
| `etf_rankings.xlsx` | Rankings sheet — top investable ETF sectors + Clenow scores |
| `holdings_log.json` | Current regime (BULL / PARTIAL / BEAR) + active allocation |

---

## CONFIG (current values)

```python
MAX_PREMIUM       = 25_000    # ₹ hard ceiling per trade
TARGET_DELTA_HIGH = 0.42      # R² > 0.85 → near ATM
TARGET_DELTA_LOW  = 0.32      # R² ≤ 0.85 → slightly OTM
IVR_MAX_TO_BUY    = 50        # skip if IV Rank > 50
IVR_CHEAP         = 30        # IVR < 30 = prefer weekly expiry
SL_PCT            = 0.50      # stop-loss at 50% of premium paid
TARGET_PCT        = 2.00      # profit target at 2× premium (100% gain)
TIME_STOP_DAYS    = 2         # exit 2 calendar days before expiry
MIN_DTE_WEEKLY    = 5         # weekly needs ≥ 5 days to be considered
HIGH_CONF_R2      = 0.85      # R² threshold for ATM strikes + weekly preference
```

---

## Indices Traded

| Index | Sector mapping | Lot size |
|---|---|---|
| NIFTY | GOLD, SILVER, METAL, ENERGY, IT_TECH, HEALTHCARE, BROAD_MARKET, FACTOR_* | 75 |
| BANKNIFTY | PSU_BANK, PRIVATE_BANK, BANKING_BROAD | 15 |
| FINNIFTY | FACTOR_VALUE, FINANCIAL | 40 |
| MIDCPNIFTY | MIDCAP, SMALLCAP | 75 |

---

## Strategy

- **Direction:** Buy calls only (no puts, no naked selling)
- **Regime gate:** No trades in BEAR regime — full cash
- **PARTIAL regime:** Trades allowed, but only if IVR is favourable
- **IV Rank:** Computed from rolling `iv_history.json` — reliable after ~5 weekly runs
- **Expiry selection:** Script decides weekly vs monthly based on IVR + R²
  - Weekly if: IVR < 30 AND R² > 0.85 AND ≥ 5 days to expiry
  - Monthly otherwise (safer default)
- **Strike selection:** Delta proxy via moneyness — targets 0.42 delta (high conf) or 0.32 (low conf)

---

## Exit Rules (hardcoded — no discretion)

| Rule | Trigger | Action |
|---|---|---|
| Stop-loss | LTP drops to 50% of entry premium | Exit same day — no exceptions |
| Profit target | LTP reaches 2× entry premium | Book profit immediately |
| Time stop | 2 calendar days before expiry | Exit regardless of P&L |

**When to run options_monitor.py:** After market close — 3:35 to 3:45pm IST.
Download fresh CSVs first, then run the script. Act on whatever the Action
column says. Do NOT run during the last 30 mins of trading — LTPs are noisy
and you risk acting on intraday moves that reverse before close.

Exception: if an option is down 70–80%+ intraday and the underlying is in
clear freefall, exit intraday without waiting. Use judgment only for extreme
moves — default is always post-close.

---

## How to Close a Trade

1. Run `options_monitor.py` — confirm the exit signal
2. Place the exit order in your broker terminal
3. Run `options_tradelog.py` — enter the actual exit price when prompted
4. Choose exit reason: Stop-loss / Target hit / Time stop / Manual
5. Script updates `signals_log.json` (ACTIVE → CLOSED) and appends to `trade_log.json`
6. `trade_log.xlsx` is regenerated with updated P&L

---

## trade_log.xlsx Structure

| Sheet | Contents |
|---|---|
| Trade Log | Every closed trade — entry/exit, P&L per lot, total P&L, hold days, exit reason |
| P&L Summary | Month-by-month — trades, win rate, gross P&L, avg win/loss, return on premium |
| Stats | Win rate, profit factor, expectancy, best/worst trade, max consec wins/losses, exit breakdown |

---

## Current Status (as of 17-Mar-2026)

- **Regime:** PARTIAL (weak TREND)
- **Active trades:** 2 open positions (generated today)
  - Details in `signals_log.json` — filter for `"status": "ACTIVE"`
- **IV history:** Started building today — IVR will be reliable after ~5 weeks
  - Until then, IVR defaults to 40 (neutral) — treat signals with slightly more caution
- **Closed trades:** 0 — `trade_log.json` is empty until first exit

---

## Dependencies

```bash
pip install pandas openpyxl python-dateutil
```

No API keys required. No live data feeds. All market data is either:
- Read from your existing ETF system files (upstream)
- Downloaded manually as CSVs from nseindia.com/option-chain

---

## Next Planned Extension: Stock F&O

**Goal:** `stock_fno_trader.py` — a fourth script that uses stock options
instead of index options, activated as a fallback when the index signal
is skipped (IVR too high / no CSV / over budget).

**Key design decisions already made:**
- Universe: F&O stocks in top-ranked ETF sectors only (~30–50 names, not all 180)
- Price data: `yfinance` with `.NS` suffix — automatic, no manual downloads
- Options chain: same manual CSV download, stock name instead of index
- Earnings hard block: skip any stock with results within 10 days
- Same Clenow + Sharpe scoring, same screens, same exit rules
- New additions needed: sector-to-FnO-stock mapping table, yfinance fetcher,
  NSE earnings calendar check, per-stock lot size lookup

**To resume:** Start a new chat, upload both context files, say
"build stock_fno_trader.py — continue from where we left off."

---

## Folder Structure

```
momentum/Options/
├── options_trader.py          ← weekly signal generator
├── options_monitor.py         ← daily MTM checker
├── options_tradelog.py        ← trade closer + P&L tracker
├── etf_rankings.xlsx          ← from ETF system (read-only)
├── holdings_log.json          ← from ETF system (read-only)
├── signals_log.json           ← auto-maintained
├── trade_log.json             ← auto-maintained
├── iv_history.json            ← auto-maintained
├── options_trades.xlsx        ← output from options_trader.py
├── options_monitor.xlsx       ← output from options_monitor.py
├── trade_log.xlsx             ← output from options_tradelog.py
└── option-chain-ED-*.csv      ← manual downloads from NSE
```
