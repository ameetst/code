# ETF Strategy: File → Database Migration Proposal

Status: **proposal only** — no code changed. Written after auditing every read/write
path in `etf_momentum_ranking.py` and `etf_dashboard.py`.

---

## 1. Inventory of current persistence

| File | Written by | Read by | Shape | Nature |
|---|---|---|---|---|
| `strategy_config.json` | `save_config_to_json()` (dashboard "Save Configuration" / "Run Rebalance" buttons) | `load_config_from_json()` at module import (`_apply_json_config`) | flat dict, ~20 scalar keys + 1 list (`REGIME_FALLBACKS`) | Singleton config, low write frequency |
| `nifty500_cache.csv` | `fetch_nifty500_index()` | same function, staleness gated on **file mtime** (≤3 days old) | 2-col time series (`Date`, `Close`) | Cache of an external API call (yfinance) |
| `holdings_log.json` | `save_holdings_log()` inside `update_log()` (every `run_pipeline()`), and again inside `check_tsl()` (peak updates) | `load_holdings_log()` in `update_log()`, `check_tsl()`, dashboard TSL tab | dict keyed by **ISO week** (`"2026-W30"`, misleadingly called `month_key` in code) → `{run_date, regime, active_slots, allocation: [ {slot, ticker, etf_name, sector, weight, inv_rank, entry_price, peak} ]}` | Weekly rebalance history — the core audit trail |
| `ETF_tradelog.json` | `save_tradelog()` via `safe_write_json()` (atomic tmp+bak+rename) | `load_tradelog()`, replayed in full on every page load by `calculate_holdings_and_pnl()` / `validate_tradelog_integrity()` | append-mostly list of `{id, date, timestamp, ticker, action, quantity, price}` | Transaction ledger — should be append-only |
| `ETF_positions_ledger.json` | `sync_to_positions_ledger()`, called after **every** trade add/edit/delete and on **every page load** | not read anywhere in the reviewed code (write-only artifact) | dict `ticker → {entry_date, entry_price}` | **Fully derived** from `ETF_tradelog.json` — pure cache |
| `ETF.xlsx` / `ETF_SECTOR.xlsx` | not written by these scripts (externally maintained) | `load_etf_data()`, `_load_sector_lookup()` | price grid / ticker→sector lookup | External input feed, out of scope |
| `etf_rankings.xlsx` | `save_excel()` at the end of `run_pipeline()` | dashboard "Download Rankings Excel" button only | multi-sheet workbook (Rankings, Allocation, Regime, Rebalance) | Generated **report**, overwritten every run — no history retained beyond what's already in `holdings_log.json` |

Two atomic-write mechanisms coexist: `safe_write_json()` (tmp → bak → rename, used for
tradelog/ledger) and plain `open(..., "w")` (used for config/holdings-log/cache) — the
latter has no crash-safety at all.

## 2. Pain points this creates

1. **Duplicated source of truth.** `ETF_positions_ledger.json` is a materialized view of
   `ETF_tradelog.json`, kept in sync by explicit calls scattered across 4 call sites
   (`save_tradelog`, edit form, delete button, dashboard load). Any call site that forgets
   to sync silently drifts.
2. **No real transactions.** `validate_tradelog_integrity()` replays the *entire* trade
   history on every single write just to prevent a negative-quantity SELL — an O(n) check
   that a `CHECK` constraint / running balance in a real DB gives for free.
3. **Partial-write risk.** `holdings_log.json` and `strategy_config.json` are written with
   a bare `open(...).write()` — a crash mid-write (or two Streamlit sessions writing at
   once) corrupts the file. `safe_write_json` avoids this for the tradelog but wasn't
   applied everywhere.
4. **History is trapped in nested JSON.** Answering something like "what was the average
   holding period per sector" or "how often does PARTIAL regime precede a BEAR" requires
   loading and walking the whole `holdings_log.json` dict in Python — there's no query
   surface.
5. **No history for the scored universe.** `etf_rankings.xlsx` is overwritten every run;
   only the *selected* slots survive into `holdings_log.json`. The Sharpe/rank of ETFs
   that *didn't* make the cut is lost every week — you can't backtest the scoring model
   itself later.
6. **Config has no audit trail.** Every "Save Configuration" click silently overwrites
   `strategy_config.json` with no record of what changed or when.
7. **Cache staleness tied to filesystem mtime**, not the data itself — copying/restoring
   the file resets the staleness clock incorrectly.

## 3. Proposed target: SQLite

For a single-user local Streamlit app, SQLite is the right fit — zero ops, one file,
full ACID transactions, and `pandas.read_sql`/`to_sql` drop in cleanly where the code
already uses DataFrames. Postgres would only earn its keep if this ever needs concurrent
multi-user writers (e.g. deployed for a team) — not the case today, so it's called out
as a possible **later** swap, not part of this plan. Enable `PRAGMA journal_mode=WAL;`
so the dashboard can read while `run_pipeline()` writes.

## 4. Proposed schema

```sql
-- Singleton config row (mirrors the existing _apply_json_config allowlist 1:1)
CREATE TABLE strategy_config (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    input_file             TEXT NOT NULL,
    output_file            TEXT NOT NULL,
    window_6m              INTEGER NOT NULL,
    window_3m              INTEGER NOT NULL,
    annualize              INTEGER NOT NULL,
    top_n                  INTEGER NOT NULL,
    top_n_partial          INTEGER NOT NULL,
    max_drawdown_from_high REAL NOT NULL,
    sharpe_w6m             REAL NOT NULL,
    sharpe_w3m             REAL NOT NULL,
    regime_ticker          TEXT NOT NULL,
    regime_fallbacks       TEXT NOT NULL,   -- JSON array as text; small + fixed, not worth a child table
    regime_index_ticker    TEXT NOT NULL,
    trend_fast_ema_window  INTEGER NOT NULL,
    trend_ema_window       INTEGER NOT NULL,
    sector_cap             INTEGER NOT NULL,
    daily_rf_annual        REAL NOT NULL,
    tsl_threshold          REAL NOT NULL,
    exit_max_dd_from_high  REAL NOT NULL,
    exit_max_rank          INTEGER NOT NULL,
    history_periods        INTEGER NOT NULL,
    updated_at             TEXT NOT NULL
);

-- Free audit trail — every save appends here (new capability, item #6 above)
CREATE TABLE strategy_config_history AS SELECT * FROM strategy_config WHERE 0;
ALTER TABLE strategy_config_history ADD COLUMN changed_at TEXT;

-- One row per weekly rebalance (replaces the dict-of-weeks in holdings_log.json)
CREATE TABLE rebalance_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    week_key     TEXT UNIQUE NOT NULL,      -- '2026-W30'
    run_date     TEXT NOT NULL,
    regime_label TEXT NOT NULL,
    active_slots INTEGER NOT NULL,
    top_n        INTEGER NOT NULL           -- CONFIG.TOP_N at run time, for audit
);

-- Replaces the "allocation" array inside each week's dict
CREATE TABLE allocation_slots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES rebalance_runs(run_id) ON DELETE CASCADE,
    slot         INTEGER NOT NULL,
    ticker       TEXT NOT NULL,
    etf_name     TEXT,
    sector       TEXT,
    weight       REAL NOT NULL,
    inv_rank     INTEGER,                  -- NULL for CASH instead of the "-" sentinel string
    entry_price  REAL,
    peak         REAL,
    UNIQUE(run_id, slot)
);

-- NEW: full-universe scoring snapshot per run — currently thrown away every week (item #5)
CREATE TABLE ranking_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES rebalance_runs(run_id) ON DELETE CASCADE,
    ticker           TEXT NOT NULL,
    etf_name         TEXT,
    sector           TEXT,
    close            REAL,
    high_52w         REAL,
    pct_from_high    REAL,
    ema_100          REAL,
    eom_pct          REAL,
    sharpe_6m        REAL,
    sharpe_3m        REAL,
    wtd_sharpe       REAL,
    rank_investable  INTEGER,
    screen_pass      BOOLEAN,
    UNIQUE(run_id, ticker)
);

-- Append-only trade ledger (replaces ETF_tradelog.json)
CREATE TABLE trades (
    id         TEXT PRIMARY KEY,            -- keep existing uuid4 strings, trivial import
    trade_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    action     TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    quantity   REAL NOT NULL CHECK (quantity > 0),
    price      REAL NOT NULL CHECK (price > 0)
);

-- Regime index cache (replaces nifty500_cache.csv); staleness = MAX(date), not file mtime
CREATE TABLE regime_index_cache (
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,
    close      REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);

-- Optional, phase 2: ticker -> sector, ingested from ETF_SECTOR.xlsx
CREATE TABLE sectors (
    ticker TEXT PRIMARY KEY,
    sector TEXT NOT NULL
);
```

Notably **absent**: a table for `ETF_positions_ledger.json`. That file is write-only
(nothing in the reviewed code reads it back) and 100% derivable from `trades` — the
existing `calculate_holdings_and_pnl()` average-cost logic can run straight off
`SELECT * FROM trades ORDER BY trade_date, created_at` on demand. Dropping it removes
an entire class of drift bugs and the 4 scattered call sites that keep it in sync today.

## 5. Migration plan

1. **Add a data-access module** (e.g. `etf_db.py`) with thin functions mirroring today's
   API (`load_holdings_log`, `save_holdings_log`, `load_tradelog`, `save_tradelog`,
   `load_config_from_json`, `save_config_to_json`, `fetch_nifty500_index`) so
   `etf_momentum_ranking.py` / `etf_dashboard.py` eventually swap call targets with
   minimal diffs. Not part of this proposal, but worth planning the function signatures
   around now so the backfill script and the eventual call-site swap agree on shape.
2. **One-time backfill script**: read each existing file and insert into the new tables.
   - `strategy_config.json` → single `strategy_config` row.
   - `holdings_log.json` → one `rebalance_runs` row + N `allocation_slots` rows per week key.
   - `ETF_tradelog.json` → `trades` rows verbatim (ids preserved).
   - `ETF_positions_ledger.json` → **not imported**; recomputed from `trades` and diffed
     against the JSON file as a one-time sanity check, then discarded.
   - `nifty500_cache.csv` → `regime_index_cache` rows.
   - `ranking_snapshots` has no historical source (the data was never retained) — starts
     accumulating from the first post-migration run onward.
3. **Parallel-write validation period**: for a couple of weekly cycles, write to both the
   JSON files and the DB, and diff them after each `run_pipeline()` / trade action before
   trusting the DB exclusively.
4. **Cut over reads**, then stop writing the legacy JSON/CSV files. Keep `ETF.xlsx` /
   `ETF_SECTOR.xlsx` as-is (external input) and keep `etf_rankings.xlsx` as a generated
   export — but regenerate it *from* `rebalance_runs` / `allocation_slots` /
   `ranking_snapshots` rather than treating it as a source of truth.
5. **Retire** `ETF_positions_ledger.json`, `safe_write_json()`'s `.bak` files, and the
   ad-hoc `holdings_log.json.bak` once the DB has been trusted for a few cycles.

## 6. What stays a file (out of scope)

- `ETF.xlsx`, `ETF_SECTOR.xlsx`, `AMFI ETF Codes.csv`, `AMFI_NAV_History.xlsx` — externally
  maintained input feeds, not written by the strategy code.
- `etf_rankings.xlsx` — becomes a generated report *from* the DB rather than a data store.

## 7. Net effect

- Removes 2 of the current 5 writable files outright (`ETF_positions_ledger.json` as a
  derived cache, `nifty500_cache.csv` as a proper cache table).
- Replaces every hand-rolled atomicity trick (`safe_write_json`'s tmp/bak dance, bare
  `open().write()` elsewhere) with real transactions.
- Gains config change history and full-universe ranking history "for free" as a
  byproduct of the schema, neither of which exists today.
- No change to `ETF.xlsx`-based input or the Excel report output format users already rely on.
