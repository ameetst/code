# Nifty 750 Momentum Screener — Strategy Logic

## Overview

A rule-based momentum + breakout screener that runs every **Monday at 3:00 PM** on the Nifty 750 universe. The goal is to identify stocks with strong trend, recent breakout, and rising volume — then rank them to select the best 20 for equal-weight entry.

---

## Universe

- **Nifty 750** stocks listed on NSE
- Tickers entered in column A of the `Ticker Data` sheet
- yfinance fetches data using the `.NS` suffix (e.g. `RELIANCE.NS`)

---

## Step 1 — Data Fetched Per Ticker

| Field | Description |
|---|---|
| `Price` | Latest closing price |
| `EMA50` | 50-day Exponential Moving Average |
| `EMA200` | 200-day Exponential Moving Average |
| `High52W` | Highest high over last 252 trading days |
| `High3M` | Highest high over last 65 trading days |
| `Low3M` | Lowest close over last 65 trading days |
| `Vol1M` | Median daily volume over last 21 trading days |
| `Vol3M` | Median daily volume over last 63 trading days |
| `Return3M` | `(Price today − Price 65 days ago) / Price 65 days ago × 100` |

---

## Step 2 — Filters (All 4 Must Pass)

A stock is only eligible for ranking if it passes **every** filter below.

### Filter 1 — Trend Alignment
```
Price > 50 EMA > 200 EMA > 0
```
Ensures the stock is in a clear uptrend across short, medium, and long-term horizons.

### Filter 2 — Proximity to 52-Week High
```
Price ≥ 52W High × 0.85
```
Stock must be within 15% of its 52-week high. Eliminates stocks that are recovering from deep drawdowns.

### Filter 3 — 3-Month Breakout
```
Price ≥ 3M High  (highest high of last 65 days)
```
Stock must be making a new 65-day high — confirming a fresh breakout, not a faded rally.

### Filter 4 — Volume Surge
```
1M Median Volume > 3M Median Volume
```
Recent volume (21-day median) exceeds the prior baseline (63-day median), indicating rising accumulation and institutional interest.

---

## Step 3 — Ranking Factors

Only stocks that pass all 4 filters are ranked. Four factors are computed and converted to **percentile scores (0–100)** within the qualifying universe.

| Factor | Formula | Rationale |
|---|---|---|
| **3M Momentum** | `Return3M %` | Strongest recent price performance |
| **Volume Surge Ratio** | `Vol1M / Vol3M` | Degree of accumulation vs baseline |
| **Breakout Strength** | `(Price − 3M High) / 3M High × 100` | How decisively price broke out |
| **EMA Separation** | `(Price − 50 EMA) / 50 EMA × 100` | Trend conviction and velocity |

### Percentile Scoring
Each factor is ranked across all qualifying stocks:
```
Score = (Number of stocks with factor ≤ this stock's value) / Total stocks × 100
```
This converts raw values into a 0–100 scale, making factors comparable regardless of unit.

---

## Step 4 — Composite Score

Weighted sum of the four percentile scores:

```
Composite Score =
    (Momentum Score  × 0.30) +
    (Volume Score    × 0.25) +
    (Breakout Score  × 0.25) +
    (EMA Sep Score   × 0.20)
```

| Factor | Weight | Reason |
|---|---|---|
| 3M Momentum | 30% | Primary driver of short-term returns |
| Volume Surge | 25% | Confirms institutional participation |
| Breakout Strength | 25% | Quality of the breakout |
| EMA Separation | 20% | Supporting trend strength signal |

Stocks are sorted by composite score descending. The **top 20** are selected for entry.

---

## Step 5 — Trade Management

| Parameter | Rule |
|---|---|
| **Entry** | Every Monday at 3:00 PM (market order near close) |
| **Position sizing** | Equal weight — Portfolio ÷ 20 |
| **Max open positions** | 20 |
| **Trailing Stop Loss** | Entry Price × 0.95 (5% TSL) |
| **Profit target** | Entry Price × 1.10 (10% target) |
| **Max holding period** | 1 calendar month |
| **Exit trigger** | Whichever comes first — TSL hit, target hit, or 1 month elapsed |
| **Re-entry** | Allowed the following Monday if stock re-qualifies |

---

## Execution Flow (Python Script)

```
nifty750_screener.xlsx  (tickers in column A)
        │
        ▼
fetch_and_screen.py
        │
        ├── Read tickers from Excel
        ├── Fetch 260 days OHLCV via yfinance (.NS suffix)
        ├── Calculate EMAs, highs, volumes, returns
        ├── Apply 4 filters → flag Pass / Fail
        ├── Compute ranking factors + percentile scores
        ├── Calculate composite score → sort → top 20
        ├── Print results to terminal
        └── Write back to nifty750_screener_updated.xlsx
                └── Top 20 Signals tab (with TSL + target per stock)
```

---

## File Structure

```
📁 your-folder/
├── nifty750_screener.xlsx        ← Add tickers here (Ticker Data → col A)
├── fetch_and_screen.py           ← Run this every Monday
└── nifty750_screener_updated.xlsx ← Output (auto-generated)
```

---

## Setup & Usage

```bash
# One-time install
pip install yfinance openpyxl pandas numpy

# Every Monday at ~2:45 PM
python fetch_and_screen.py
```

---

## Notes & Assumptions

- All price data sourced from **Yahoo Finance** via `yfinance`. Prices are adjusted for splits and dividends (`auto_adjust=True`).
- **3M High** uses the last 65 trading days as a proxy for 3 calendar months.
- **1M Median Volume** uses 21 trading days; **3M Median Volume** uses 63 trading days. Median is used instead of mean to reduce distortion from volume spikes.
- Tickers with fewer than 200 days of history are skipped.
- If fewer than 20 stocks pass all filters on a given Monday, only the qualifying stocks are entered.
- The screener does **not** account for corporate actions (bonus, rights, splits) beyond yfinance's built-in adjustment.
