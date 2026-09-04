# How to Run Clenow — Weekly Operating Guide (v2: automated)

This supersedes the earlier manual-editing workflow. `clenow_runner.py`
now does the whole Wednesday-morning ritual in one command: ranking,
evaluating existing holdings, marking sells with real P&L, updating the
equity ledger, rewriting `holdings.csv`, and rendering a performance
dashboard. You no longer hand-edit `holdings.csv` — it's rewritten by the
script every run.

See `TRACKING_SYSTEM_PLAN.md` for the full design write-up behind this.

---

## 1. The one command

```
python clenow_runner.py --file N750_updated.xlsx
```

That's it. Every Wednesday: drop the refreshed price file into this
folder, run that command, done. It prints a full summary and writes:

| File | What it is |
|---|---|
| `holdings.csv` | Your current open positions — machine-owned, don't hand-edit |
| `trade_log.csv` | Permanent record of every trade ever, open or closed, with P&L |
| `equity_history.csv` | One row per run — your compounding equity curve |
| `clenow_ranked.csv` | This week's full ranking snapshot (all 750 tickers) |
| `reports/dashboard_latest.html` | Open this in a browser for the visual view |
| `reports/dashboard_YYYY-MM-DD.html` | A permanent snapshot of that week's dashboard |
| `clenow_config.json` | Persisted settings (currently just `price_provider`) |

Between Wednesdays, `python mtm.py` gives an on-demand mark-to-market
check of the current book (see §6) — it writes nothing.

## 2. What happens on each run

1. Reads your current open positions and last known equity.
2. Runs the ranking engine (unchanged) using those as "held."
3. Every SELL signal closes its trade: fills exit price (that day's
   `last_close`), exit reason, holding period, and P&L into
   `trade_log.csv`.
4. Every BUY signal (not already held) opens a new trade at its
   ATR-sized share count.
5. HOLD positions are left completely untouched — share count is fixed
   from entry to exit, it never gets resized mid-trade.
6. Realized P&L from this run's closes compounds into the next run's
   starting equity automatically.
7. `holdings.csv` and the dashboard are rewritten to reflect the new
   book.

You still do exactly two manual things: drop a fresh price file in the
folder each week, and place the actual trades with your broker based on
the SELL/BUY lists it prints.

## 3. Reading the output

The terminal summary tells you directly what to do:

- **SELL — closed this run**: close these positions today. The
  `exit_reason` column tells you why (stopped out, below its 100-day MA,
  or its rank fell outside the band) — already logged, no action needed
  in the files.
- **BUY — opened this run**: open these positions at the listed share
  count.
- **Current book**: everything you should be holding after today's
  trades — a straight read of the (already-updated) `holdings.csv`.
- **Performance-to-date**: total return, CAGR (once you have ~10 weeks
  of history), win rate, and max drawdown, computed from the full trade
  log — also all in `reports/dashboard_latest.html` with charts.

## 4. Live entry/exit prices

Every actual BUY/SELL this run is booked at a live quote — not the price
file's (possibly stale) last column — so if you run on a Wednesday, you
get Wednesday's real price, not whatever date the workbook happened to
last be refreshed to. Ranking, filters, and ATR sizing still come
entirely from the xlsx history; only the transaction price itself is
live. Prices are fetched in one batched call per run, not one request
per ticker.

Two providers are supported, chosen via `--price_provider`:

```
python clenow_runner.py --file N750_updated.xlsx --price_provider dhan
```

The choice **persists** in `clenow_config.json` — pass the flag once to
switch, then every future run (with no flag) keeps using it. Omit the
flag entirely on a first-ever run and it defaults to `yfinance`.

- `yfinance` (default) — `pip install yfinance` once. Queried one ticker
  at a time (no bulk quote endpoint).
- `dhan` — uses the shared `dhandata` library
  (`C:\Users\ameet\Documents\Github\dhan_datahq\`), one batched
  `/marketfeed/ltp` call for every ticker. Needs that repo's Dhan
  credentials/token cache set up (see its own README) — if it isn't,
  every quote just falls back, same as any other failure below.

If a live quote can't be fetched for some reason (network hiccup, market
not yet open, a ticker the provider doesn't recognize, Dhan auth not set
up), that trade automatically falls back to the xlsx's last_close instead
of failing — and `trade_log.csv` records exactly which happened in the
`entry_price_source` / `exit_price_source` columns, so you can always
check whether a given fill was live or a fallback, and from which
provider.

Pass `--no_live_price` to disable this entirely and always use the xlsx
close instead (useful for testing, or if you deliberately want prices to
match the data file exactly).

## 5. Safety features

- **`--dry_run`** — runs the whole pipeline and prints what it would do,
  writes nothing. Use this if you ever want to preview a week before
  committing it (e.g. testing against a not-yet-final price file).
- **Idempotency guard** — running it twice for the same date is refused
  unless you pass `--force`, so an accidental double-run can't
  double-book the week's trades.
- **Missing tickers** — if something you hold vanishes from the price
  file entirely (delisted/renamed), it's flagged with a warning and left
  OPEN rather than silently closed at a fabricated price — investigate
  manually.
- **Cash-capped buys** — Clenow's ATR-based sizing gives every new BUY
  equal *dollar risk per ATR*, not equal dollar exposure, so a week full
  of low-volatility names can ask for more capital than the account
  actually has. Each run computes real cash on hand (starting equity plus
  this run's realized P&L plus any `--cash_flow`, minus capital already
  tied up in continuing holdings) and fills new BUYs whole, best rank
  first, until that runs out — any candidate that doesn't fit is skipped
  (never partially resized, so every filled position keeps its intended
  risk) and printed in the terminal summary as "skipped (insufficient
  cash at full ATR size)".

## 6. Checking mark-to-market anytime

The weekly run and dashboard only track **realized** P&L — an open
position sits at cost basis until it's actually closed. To check current
unrealized P&L on the book right now, any day, without touching anything
`clenow_runner.py` owns:

```
python mtm.py
```

Fetches one live quote per held ticker (same provider as the weekly
run — see `clenow_config.json` / `--price_provider` above), and prints
cost basis, current market value, and unrealized P&L per position and in
total. Falls back to `clenow_ranked.csv`'s `last_close` for anything a
live quote can't be found for. Pass `--price_provider {yfinance,dhan}` to
check against a specific provider just for this run (doesn't change the
saved config), or `--no_live_price` to skip live quotes entirely and use
last week's ranked-file closes for everything. Read-only — writes
nothing.

## 7. Cash deposits or withdrawals

Equity auto-compounds from realized P&L only. If you add or remove real
money from the account, tell the script on that run:

```
python clenow_runner.py --file N750_updated.xlsx --cash_flow 200000
```

(positive for a deposit, negative for a withdrawal). This adjusts
`equity_history.csv` without being mistaken for a trading gain or loss.

## 8. Other flags

Same as before: `--top_n`, `--risk_factor`, `--min_liquidity` all pass
straight through to the ranking engine. `--seed_equity` only matters on
the very first-ever run (it's remembered after that via
`equity_history.csv`).

## 9. Discipline notes (still true)

- Don't override the signals — the entire point of running a systematic
  system is mechanical, unemotional execution.
- Refresh the price file every time; don't rerun against a stale one.
- `--top_n` is a target, not a hard cap — some weeks fewer slots open up
  than others, that's normal.
