# Trade Log & Performance Tracking — Design Plan

Status: **proposal only** — nothing in `Clenow.py` has been touched. This
document is for your review; I'll implement once you sign off (or tell me
to adjust anything below).

---

## 1. What I found reviewing the current setup

### 1.1 What `Clenow.py` already does well
It ranks the universe, applies the market/MA/gap/liquidity filters, and
runs the hold-vs-sell band logic against a `holdings.csv` you pass in.
That part is solid and doesn't need to change.

### 1.2 What it doesn't do (the gap you're asking me to close)
`Clenow.py` is stateless between runs beyond what you manually maintain in
`holdings.csv`. Concretely, today:

- There's no record of *closed* trades — once you delete a SELL row from
  `holdings.csv`, that trade's history (entry, exit, holding period,
  P&L) is gone forever.
- There's no P&L calculation anywhere — realized or unrealized.
- There's no equity tracking — `--account_value` is a number you type
  fresh each run; it never compounds with gains/losses.
- There's no performance statistics — win rate, average win/loss,
  drawdown, benchmark comparison, none of it exists.
- `holdings.csv` is hand-edited every week (remove SELLs, add BUYs) —
  exactly the manual step you want removed.

### 1.3 Real bugs I found in your actual `holdings.csv`
I pulled the current file from your folder to ground this plan in reality
rather than a hypothetical, and it surfaced three concrete parsing issues
the new system needs to fix (these would bite you on the very next run):

1. **Date ambiguity.** Your file has `entry_date = 02-09-2026`, which you
   clearly mean as 2 September 2026 (day-month-year, today). But
   `pandas.to_datetime` without `dayfirst=True` reads it as 9 February
   2026 instead — a silent 7-month error that would corrupt every
   trailing-stop calculation for that position. Verified directly: the
   current loader produces 2026-02-09, not 2026-09-02, for this exact
   value.
2. **Thousands-separator prices.** Excel saved some of your entry prices
   as text with commas — `"2,252.40"`, `"2,941.00"`, `"2,357.00"` — which
   is how AVALON, CARTRADE, PGIL, and SUDEEPPHRM appear in your file
   right now. The current parser (`pd.to_numeric(..., errors="coerce")`)
   silently turns each of those into `NaN`, meaning those four positions'
   entry prices are effectively lost.
3. **Excel row/column bloat.** Your `holdings.csv` has grown to 738 rows
   and 21 columns, but only 8 rows actually have data — the rest are
   blank rows and unnamed trailing columns Excel added when you saved it
   from a spreadsheet with formatting. Harmless today, but a bad
   foundation to build automated logic on top of.

None of this is a criticism of how you've been using it by hand — it's
exactly the kind of thing that becomes a real problem the moment a script
starts *writing* the file automatically instead of you eyeballing it
each week, which is precisely what we're about to build. The new system
fixes all three (explicit day-first date parsing with validation, locale-
aware numeric parsing that strips thousands separators, and a strict
schema that ignores/cleans blank rows and stray columns on load).

---

## 2. Design decisions (confirmed with you)

| Decision | Choice |
|---|---|
| Fill price for P&L | Auto — every BUY/SELL is booked at that run's `last_close`. No manual fill entry. |
| Position sizing over time | Fixed at entry. Share count doesn't change until the position is sold — matches the book, keeps per-trade P&L clean. |
| Account equity | Auto-compounds. A running equity ledger absorbs realized P&L from every closed trade and feeds next week's position sizing automatically. |
| Reporting | Data files (CSV) **plus** a generated HTML performance dashboard each run (equity curve, win/loss, drawdown). |

One consequence of "auto-compounding equity" worth flagging explicitly:
if you ever deposit or withdraw real cash from the account, you'll need
to tell the system that via a small `--cash_flow` adjustment (covered in
§5) — otherwise it would misread a deposit as a trading gain. This is the
one place true hands-off isn't possible, because the script has no way
to observe your bank account.

---

## 3. New file layout

Everything lives in the same `Clenow Score` folder, alongside the
existing files. Nothing here replaces `Clenow.py`'s core ranking engine —
it wraps a new orchestration layer around it.

```
Clenow Score/
├── Clenow.py                  (unchanged: the ranking/filter engine, now called as a library)
├── clenow_runner.py            NEW — the single command you actually run
├── N750_updated.xlsx           (you still refresh this weekly, same as today)
├── holdings.csv                 REPLACED by a clean, script-owned version (see §3.1)
├── trade_log.csv                NEW — append-only ledger of every BUY/SELL ever
├── equity_history.csv           NEW — one row per run: date, equity, cash, deployed, realized P&L
├── clenow_ranked.csv            (unchanged: full weekly ranking snapshot, as today)
└── reports/
    ├── dashboard_2026-09-09.html   NEW — one HTML report per run
    └── dashboard_latest.html       NEW — always overwritten, so you have one bookmark
```

### 3.1 `holdings.csv` — becomes machine-owned
You'll still be able to open and read it, but `clenow_runner.py` writes
it at the end of every run — you stop hand-editing it. Schema:

| Column | Meaning |
|---|---|
| `ticker` | NSE symbol |
| `entry_date` | ISO `YYYY-MM-DD`, always written by the script (no ambiguity) |
| `entry_price` | Numeric, the `last_close` at entry |
| `shares` | Fixed at entry (per your sizing decision), never changes until exit |
| `trade_id` | Links this open position to its row in `trade_log.csv` |

### 3.2 `trade_log.csv` — the actual trade history you asked for
One row per trade, opened on BUY, completed on SELL. This is the record
that survives forever, independent of what's currently open.

| Column | Meaning |
|---|---|
| `trade_id` | Unique ID (e.g. `TICKER-YYYYMMDD`) |
| `ticker` | NSE symbol |
| `entry_date` / `exit_date` | ISO dates. `exit_date` blank while open |
| `entry_price` / `exit_price` | `last_close` on entry/exit day. `exit_price` blank while open |
| `shares` | Position size |
| `entry_reason` | e.g. `"New entry, rank 3 of 685"` |
| `exit_reason` | e.g. `"Stopped out: close 184.66 < stop 200.95"` — blank while open |
| `holding_days` | Calendar days held (filled on exit) |
| `pnl_amount` | `(exit_price - entry_price) × shares`, filled on exit |
| `pnl_pct` | Return on this trade, filled on exit |
| `status` | `OPEN` or `CLOSED` |

This single file is what makes "proper performance statistics" possible
— every win rate, average win/loss, and profit-factor number below is
computed straight from it.

### 3.3 `equity_history.csv` — one row every time you run
| Column | Meaning |
|---|---|
| `run_date` | The Wednesday this run corresponds to |
| `starting_equity` | Equity carried in from the previous run |
| `realized_pnl_this_run` | Sum of `pnl_amount` for trades closed this run |
| `cash_flow_adjustment` | Manual deposits/withdrawals you declared (usually 0) |
| `ending_equity` | `starting_equity + realized_pnl_this_run + cash_flow_adjustment` — this becomes next run's sizing base |
| `deployed_capital` | Sum of `entry_price × shares` across currently open positions |
| `cash_available` | `ending_equity - deployed_capital` |
| `open_positions` | Count |
| `nifty500_close` | For benchmarking your equity curve against buy-and-hold |

### 3.4 `reports/dashboard_latest.html` — the visual layer
A single self-contained HTML file (matplotlib/plotly rendered to static
HTML, or a lightweight custom chart — I'll pick whichever keeps the file
dependency-free) showing:

- Equity curve vs. a NIFTY500 buy-and-hold line, same starting capital
- Realized P&L per trade (bar chart, wins green / losses red)
- A drawdown chart (peak-to-trough % on the equity curve)
- A stats panel: total return %, CAGR, win rate, average win, average
  loss, profit factor, max drawdown, current number of open positions,
  current deployed capital vs. cash

---

## 4. The one command, end to end

```
python clenow_runner.py --file N750_updated.xlsx --top_n 20
```

That's the entire weekly ritual. Internally, in order:

1. **Load state.** Read `holdings.csv` (current open positions) and
   `equity_history.csv` (last known equity) to get this run's starting
   capital — no `--account_value` needed anymore, it's remembered.
2. **Run the existing ranking engine** (`Clenow.py`'s `rank()` logic,
   imported as a function, unchanged) against the new price file, using
   current holdings for the hold/sell/band logic exactly as it works
   today.
3. **Reconcile SELL signals against `trade_log.csv`.** For every ticker
   the ranking marked SELL: look up its `OPEN` row, fill in `exit_date`,
   `exit_price` (today's `last_close`), `exit_reason`, `holding_days`,
   `pnl_amount`, `pnl_pct`; flip `status` to `CLOSED`.
4. **Reconcile BUY signals.** For every new BUY: append a new `OPEN` row
   to `trade_log.csv` with today's date, `last_close` as entry price, the
   ATR-sized share count, and the entry reason from the ranking.
5. **Update `equity_history.csv`.** Sum this run's realized P&L from step
   3, add it to last run's ending equity, record deployed capital and
   cash from the resulting open book, and append one new row.
6. **Rewrite `holdings.csv`** from the now-current set of `OPEN` rows in
   `trade_log.csv` — clean, ISO dates, no manual editing, ever.
7. **Compute performance statistics** from the full `trade_log.csv` +
   `equity_history.csv` history (see §5 for the exact list).
8. **Render the HTML dashboard** and save it under `reports/`, plus
   overwrite `reports/dashboard_latest.html`.
9. **Print a short terminal summary** — this week's BUYs/SELLs/HOLDs,
   this run's realized P&L, new equity, and the headline stats — so you
   get the gist without opening any file.

You still do exactly one manual thing before running it: drop the
refreshed `N750_updated.xlsx` into the folder. Actually executing the
trades with your broker is obviously still on you too — the script
produces the instructions, it doesn't place orders.

---

## 5. Performance statistics computed

All derived from `trade_log.csv` (closed trades) and `equity_history.csv`
(the equity curve):

- Total return % and CAGR since the system's inception date
- Win rate (% of closed trades with positive `pnl_amount`)
- Average win % / average loss %, and their ratio (payoff ratio)
- Profit factor (gross profit ÷ gross loss)
- Max drawdown on the equity curve, and current drawdown from the last
  peak
- Average holding period (days), separately for winners and losers
- Current exposure: number of open positions, deployed capital as % of
  equity
- Benchmark comparison: your equity curve vs. a same-start NIFTY500
  buy-and-hold line, so you can see if the system is actually earning
  its keep over just holding the index

---

## 6. Handling cash deposits/withdrawals

Since equity auto-compounds from realized P&L, an actual deposit or
withdrawal needs to be told to the system explicitly, or it will
misattribute it as a trading gain or loss:

```
python clenow_runner.py --file N750_updated.xlsx --cash_flow 200000
```

(positive for deposits, negative for withdrawals). This just adds a
`cash_flow_adjustment` row in `equity_history.csv` for that run — no
other effect.

---

## 7. Migrating your existing 4 open positions

Your current `holdings.csv` has AVALON, CARTRADE, PGIL, and QUESS as your
real open positions (the other 4 rows — REDINGTON, STYL, SUDEEPPHRM,
UTLSOLAR — look like they may not have actually been filled, worth you
confirming). Migration means seeding `trade_log.csv` with one `OPEN` row
per real position, using the corrected entry date (2026-09-02) and the
comma-stripped entry price, and setting `equity_history.csv`'s first row
from whatever starting account value you tell me. This is a one-time
step I'd do as part of implementation — I'll ask you to confirm the
exact list and starting equity before writing it.

---

## 8. Automation (getting to "don't even remember to run it")

`clenow_runner.py` itself can't fetch a fresh price file on its own —
that still depends on wherever `N750_updated.xlsx` comes from, which is
outside what I have visibility into from here. But the run itself can be
scheduled via **Windows Task Scheduler** on your machine, so that every
Wednesday morning it runs automatically against whatever the file
contains at that moment (with the terminal summary and dashboard waiting
for you rather than you having to remember to type the command). I can
set that up as a follow-on step once the script itself is built and
you've run it manually a few times to trust it.

---

## 9. What I need from you to proceed

1. Sign-off on the design above (or tell me what to change).
2. Confirm which of the 8 rows in your current `holdings.csv` are real,
   filled positions vs. leftovers, and what starting equity to seed
   `equity_history.csv` with.
3. Confirm you're fine with `holdings.csv` becoming script-owned (i.e.
   you'll stop hand-editing it going forward — manual edits would get
   overwritten on the next run).

Once confirmed, I'll build `clenow_runner.py`, do the one-time migration,
and run it once against your current data so you can see the dashboard
and trade log before trusting it with real weekly use.
