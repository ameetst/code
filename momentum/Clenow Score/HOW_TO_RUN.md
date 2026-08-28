# How to Run Clenow.py — Weekly Operating Guide

This is the operating rhythm for running the momentum system week to week:
when to run it, how to read BUY/HOLD/SELL/WATCHLIST, and how to keep
`holdings.csv` in sync with what you actually own.

---

## 1. Cadence — run it once a week

Clenow's book rebalances weekly, on Wednesdays. Practically:

- Get a fresh price file (`N750_updated.xlsx` or equivalent) that includes
  that week's most recent close for every ticker, plus `NIFTY500`.
- Run the script once, on the same day each week.
- Don't run it daily and react to every wiggle — the whole point of the
  weekly cadence (and the 20% rebalance band) is to avoid overtrading on
  noise. Off-cycle checks are fine for curiosity, but only act on a
  scheduled weekly run.

---

## 2. First run ever — no holdings yet

If you don't currently hold anything from this system, run without
`--holdings`:

```
python Clenow.py --file N750_updated.xlsx --account_value 1000000
```

Every row in the output is either:

- **BUY** — top-ranked, passes all filters, market regime is favorable
  (NIFTY500 above its 200-day MA). These are the positions to actually
  open. `approx_shares` / `allocated_amount` tell you how much of each to
  buy, sized so each position risks roughly the same amount (0.1% of
  account value by default) against a 1-ATR move.
- **WATCHLIST** — would be a BUY, but the market filter is blocking new
  entries (index below its 200-day MA). Don't buy these — wait for the
  filter to clear, then re-check on your next scheduled run.
- **NO SIGNAL** — not top-ranked enough right now. Ignore.
- **DISQUALIFIED** — failed a basic data/gap/liquidity check. Ignore, and
  don't second-guess it by buying anyway — the disqualify_reason column
  tells you why.

Place your trades for the BUY rows. Then **immediately record them** in a
new `holdings.csv`:

```
ticker,entry_date,entry_price
NATIONALUM,2026-08-28,
ASHOKLEY,2026-08-28,
...
```

Use the actual date you filled the order (not the price file's date if
they differ), and optionally your actual fill price. This file is now
your source of truth for what the system thinks you own — keep it
alongside the script.

---

## 3. Every subsequent run — pass your holdings

```
python Clenow.py --file N750_updated.xlsx --account_value 1000000 --holdings holdings.csv
```

Now the signals mean something different for tickers you hold vs. not:

| Signal | Applies to | What to do |
|---|---|---|
| **HOLD** | a ticker in `holdings.csv` | Keep it. No action. |
| **SELL** | a ticker in `holdings.csv` | Close the position now. Check `signal_reason` — it's one of: stopped out (price broke the trailing ATR stop), dropped below its 100-day MA, or its momentum rank fell outside the top 20% band. |
| **BUY** | a ticker NOT in `holdings.csv` | New entry, opened specifically to fill a slot freed up by a SELL (or to reach `--top_n` for the first time). Buy it. |
| **WATCHLIST** | a ticker NOT in `holdings.csv` | Ranks well and a slot may be open, but the market regime filter is blocking new buys. Don't buy — recheck next week. |
| **NO SIGNAL / DISQUALIFIED** | anything else | No action either way. |

After you've executed the trades:

1. **Remove** every SELL ticker from `holdings.csv`.
2. **Add** every BUY ticker to `holdings.csv`, with today's date as
   `entry_date`.
3. Leave HOLD tickers untouched in the file — don't reset their
   `entry_date`, since that's what lets the trailing stop track the real
   high since you entered.

That updated `holdings.csv` is what you feed into next week's run. This
loop — run, act on SELL/BUY, update the file — is the entire operating
cycle.

---

## 4. What the market filter does and doesn't do

If NIFTY500 is below its 200-day MA, the terminal prints a warning and
**no new BUYs are issued** — but existing HOLDs are not force-liquidated.
They keep being managed individually (still subject to their own stop
and rank-band checks). This matches the book: the regime filter pauses
new risk-taking, it doesn't dump the whole book on a single index
crossing. Don't override this by manually buying WATCHLIST names, and
don't panic-sell HOLD positions just because the filter is off — let the
per-stock rules (stop, MA, rank) decide those.

---

## 5. A few operating rules worth internalizing

- **Don't override the signals.** The entire premise of a systematic
  momentum system is mechanical, unemotional execution. If you start
  hand-picking which SELLs to ignore or which WATCHLIST names to buy
  anyway, you're no longer running Clenow's system — you're running your
  own intuition with extra steps.
- **Keep `holdings.csv` accurate.** The script's HOLD/SELL logic is only
  as good as what you tell it you own. If it drifts out of sync with your
  actual brokerage positions, the rank-band and stop calculations for
  "held" tickers stop meaning anything.
- **Refresh the price file every time.** Don't rerun against a stale
  `N750_updated.xlsx` — signals are only as current as the last close
  price in that file's date columns.
- **`--top_n` is your target position count**, not a hard cap you need to
  hit exactly. Some weeks fewer slots open up than others; that's normal.
