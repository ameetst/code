# Sharpe webapp (Flask + Jinja + HTMX)

Replacement for `sharpe_dashboard_dhan.py`. Runs alongside it — nothing in the parent
folder is modified. `momentum_lib` is imported as-is; data files are read from the parent folder.

Everything under `webapp/` is a Python package (`webapp.app`, `webapp.core.rankings`, ...),
so commands are run **from `momentum/Sharpe/`** (the parent of `webapp/`), not from inside it.

## Run

Day to day (waitress, no reloader):

    webapp\run_webapp.bat                 # http://127.0.0.1:8000

Development (from momentum/Sharpe/, Werkzeug reloader on):

    webapp\.venv\Scripts\python -m webapp.app

Tests (uses live data, read-only; ~15s because of the ranking compute):

    webapp\.venv\Scripts\python -m pytest webapp/tests

## Safety while both UIs exist

- **Read-only by default** (`SHARPE_READ_ONLY=1`). JSON/API-style write routes should use
  `core.storage.require_writable`, which 403s until you opt in. HTMX-driven write routes
  (Tradelog's add/edit/delete) instead check `settings.READ_ONLY` inline and, when blocked,
  re-render their normal content partial with an error banner at **200**, not 403 — HTMX
  does not swap in error-status response bodies by default, so a 403 would silently do
  nothing on screen.
- Loading rankings here **does not write** anything. The Streamlit page appends to
  `*_regime_history.json` and rewrites `*_positions_ledger.json` on every load; the
  webapp deliberately doesn't. A test guards this.
- To develop against a copy of the data: `set SHARPE_DATA_DIR=C:\path\to\copy`.
- Don't enter trades in both UIs at once — there is no cross-process lock.
- **The first request after a cold start (or any config/data change) takes ~10-15s**
  (the ranking compute, plus first-time Jinja template compilation) — every request in
  that window blocks on the same lock, which can look like a hang if you fire off several
  requests before the first one returns. Wait for one request to complete before sending
  more; the background warm-up thread already pays this cost once at startup so real users
  shouldn't normally see it.

## Layout

    app.py                 Flask app factory, blueprint registration, warm-up
    settings.py             CODE_DIR / DATA_DIR / READ_ONLY
    core/                    logic ported from the dashboard, no Flask imports
      rankings.py              load + compute (cached on file mtime + config), Top-N + Full Rankings rows
      regime.py                  A/D ratio, regime-history loading, trend-chart JSON
      dhan_client.py              live India VIX (Dhan primary, Yahoo Finance fallback, 1h cache)
      cap_tier.py                 STOCKDB-based dual cap-tier breakdown + opt-in Yahoo live fetch (24h cache)
      actions.py                positions-ledger loading, exit-signal evaluation, entry-candidate sizing
      tradelog.py                load_tradelog + calculate_holdings_and_pnl (average-cost engine) +
                                    validate_tradelog_integrity + save_tradelog + sync_to_positions_ledger
      cash_ledger.py             load/save + summary/running-balance for the independent cash ledger
      configuration.py           field bounds/clamping, MDTV/circuit informational counts,
                                    the read-only Strategy Parameters block
      performance.py            equity_history.json → NAV/alpha/drawdown metrics + chart records
      config_store.py          dashboard_config.json + universe file resolution
      storage.py                safe_write_json + lock, require_writable decorator
    blueprints/              one per tab
      config.py                 /api/health, /api/config (JSON API, unrelated to the page below)
      rankings.py                /  and /rankings/full (pages), /rankings/table and
                                    /rankings/full/table (HTMX partials), /rankings/cap-tier/yahoo
                                    (opt-in live fetch partial)
      actions.py                 /actions
      tradelog.py                 /tradelog and every add/edit/delete route
      cash_ledger.py              /cash and every add/edit/delete route
      configuration.py            /config-page and /config-page/save (HTML page; kept off
                                    /config to stay clear of the /api/config JSON route)
      performance.py             /performance
    templates/
      base.html                  shared shell: title, read-only banner, nav tabs (incl. the live
                                    🔴/🟢 Actions Monitor dot), HTMX script tag
      rankings.html               Top-N regime header + Regime Score Breakdown (VIX/A-D-ratio/
                                    trend chart) + Market Cap Momentum Breakdown expanders +
                                    HTMX-loaded table container
      _rankings_table.html         the Top-N table partial
      _cap_tier_yahoo.html         the opt-in Yahoo market-cap fetch result/button, swapped into
                                    #cap-tier-yahoo-root
      full_rankings.html          Full Rankings filter form (eligibility / top N / sort)
      _full_rankings_table.html    the Full Rankings table partial — filters hx-get this on change
      actions.html                exit signals, holdings/exits/new-positions metrics, new-entry
                                    candidates, full positions table — all one page, no HTMX needed
      tradelog.html                page shell wrapping #tradelog-content
      _tradelog_content.html        metrics + holdings + form wrapper + history — the swap target
                                      for every add/edit/delete
      _trade_form.html              Log New Transaction form; also the row-click-to-prefill target
      _edit_form.html                inline edit panel for one transaction, swapped into
                                      #edit-form-wrapper by a row's "Edit" link
      cash_ledger.html              page shell wrapping #cash-content
      _cash_ledger_content.html      metrics + add-entry form + history — the swap target
      _cash_edit_form.html           inline edit panel, swapped into #cash-edit-form-wrapper
      configuration.html            page shell wrapping #config-content
      _configuration_content.html    the whole settings form + read-only Strategy Parameters —
                                       one big form, one Save button (no live preview; see Known gaps)
      performance.html            NAV/alpha/drawdown metrics + equity chart + daily-detail expander
    static/
      css/theme.css             same palette as the Streamlit dashboard
      js/equity_chart.js        vanilla SVG equity chart (hover crosshair + tooltip), ported out
                                   of equity_chart.py's Streamlit components.html() blob
      js/line_chart.js          generic N-series version of the same chart, used by the
                                   Regime Score Trend chart
      js/sortable_tables.js     click-to-sort on every table.data-table header, app-wide
    tests/

## Pattern this app follows (all tabs are now ported; this is for the next write path, whatever it is)

Read-only pages (Full Rankings, Performance, Actions Monitor): a route + a full-page template.

Pages with server-driven interactivity (Tradelog and Cash Ledger's row-click-to-form and
add/edit/delete; Configuration's one big form): `hx-get`/`hx-post` from a row, link or form onto
a route that renders a **partial** template and swaps it into a named `<div>`. No client-side
state to manage — the server always renders the next state of the DOM fragment. Tradelog and
Cash Ledger both use two swap granularities: a small one (`#trade-form-wrapper`,
`#edit-form-wrapper` / `#cash-edit-form-wrapper`) for pure UI state like "which row did you
click", and a large one (`#tradelog-content` / `#cash-content` / `#config-content`) for anything
that can change several things on the page at once — i.e. every actual mutation.

## Status

Done:
- **Top-N Rankings** — regime header, held-row highlighting, LTP from last data-sheet price.
  Its **Regime Score Breakdown** expander (a first pass had drifted from the dashboard —
  mislabeled column, missing signals) now matches in full: all 4 weighted signal scores
  with their % labels, live India VIX (`core/dhan_client.py`, Dhan primary/Yahoo Finance
  fallback, same as the dashboard, 1h cache), 1D Advance/Decline ratio (`core/regime.py`,
  pure pandas), a composite-score progress bar, and the Regime Score Trend line chart
  (reads `*_regime_history.json` read-only, `static/js/line_chart.js` — a generic version
  of the equity chart's SVG/hover-tooltip machinery). A second, separate **Market Cap
  Momentum Breakdown** expander was missing outright: the instant STOCKDB.csv-based dual
  bars (63-day return vs. 3M Sharpe positive-% per cap tier, `core/cap_tier.py`) render
  as plain server-side HTML (no JS needed — the dashboard's version was just computed
  pixel widths too), plus a nested, opt-in "Load Yahoo Market Cap Data" button
  (`/rankings/cap-tier/yahoo`, ~15s live fetch across the universe, 24h cache, same
  opt-in shape as the dashboard's own button rather than fetching automatically).
- **Full Rankings** — eligibility filter, top-N range, sort-by, all three row-highlight rules
  (disqualified / Rel-52H-DD breach / within regime N) as CSS classes instead of a pandas Styler.
  Filters are a single `<form hx-get hx-trigger="change">` swapping `#full-table` — no page reload.
- **Performance Tracker** — NAV/alpha/drawdown metric cards, the equity curve (same hover
  crosshair + tooltip as the Streamlit version, now `static/js/equity_chart.js` instead of a
  `components.html()` blob), and the collapsible daily-NAV table. Handles the 0/1/2+ day
  states the same way the dashboard does.
- **Actions Monitor** — exit-signal evaluation (52H/circuit/series/MDTV/filter breaches, rank-drop
  and relative-52H-drawdown exits, the 28-day hold lock), the holdings/exits/new-positions metric
  row, inverse-vol-weighted new-entry candidates (capped at Max Position Size, pro-rata scaled if
  cash is short), and the full positions table with the dashboard's four row-highlight states
  (sell-immediately / sell / locked / healthy). Fully read-only page, no HTMX needed. The nav bar's
  🔴/🟢 dot next to "Actions Monitor" is live on every page (via a Flask `context_processor`), same
  signal as the Streamlit tab label.
- **Tradelog & MTM** — metric cards (Total Invested, Market Value, Unrealized/Realized PnL),
  the Active Holdings table where **clicking a row prefills the Log New Transaction form**
  (ticker, qty, current price) via `hx-get` into `#trade-form-wrapper`, the trade-entry form
  itself, and Transaction History with per-row **Edit** (`hx-get` into `#edit-form-wrapper`)
  and multi-select **Delete**. All three mutating routes (add/edit/delete) are fully
  implemented against `core/tradelog.py`'s ported `validate_tradelog_integrity` +
  `calculate_holdings_and_pnl` + `sync_to_positions_ledger`, and are exercised end to end —
  they're just gated behind `settings.READ_ONLY` (default on) until cutover, at which point
  flipping the flag is the only change needed to enable them for real.
- **Cash Ledger** — deposit/withdrawal/dividend/interest/fee tracking, independent of stock
  trades. Metric cards (Total Deposits, Withdrawals, Dividends+Interest, Net Cash Contributed),
  add-entry form, and history with running balance, per-row **Edit**, and multi-select **Delete**.
  Matches the dashboard's one quirk exactly: an outflow that exceeds net cash contributed is a
  **warning**, not a rejection — it's still recorded (a portfolio's real cash position can
  legitimately exceed the ledger's own running total via investment gains/proceeds outside it).
- **Configuration** — Data Source, Capital & Sizing (with the derived Max Position Size shown
  live), MDTV filter with pass-count captions, a **Min CMP filter** and a **Min Market Cap
  filter**, each with its own pass-count caption, the Series-EQ and Circuit-Hit-Frequency toggles
  (the latter's threshold field shows/hides via a couple lines of vanilla JS, not a round trip),
  the Rel-52H-DD and Rank-Buffer exit thresholds, one Save button, and the read-only Strategy
  Parameters summary. **Min CMP and Min Market Cap are architecturally different from every
  other filter here**: neither feeds the master `eligible`/`RANK` gate in
  `momentum_lib.compute_universe_rankings` (unlike MDTV/EQ-series/circuit), so neither nulls a
  held position's RANK or triggers a forced exit — a ticker failing either still ranks normally,
  it's only skipped when `entry_candidates()` (here and in Sharpe.py) fills open slots for NEW
  buys. This was a deliberate choice: MDTV/EQ/circuit failures already force an immediate exit on
  a held position with no 28-day hold-lock grace period, and both a raw price threshold and a
  market-cap threshold were judged too noisy day-to-day (or, for market cap, too dependent on a
  manually-maintained CSV) to carry that same immediate-exit behavior — existing holdings are
  grandfathered regardless of price or market cap.
  **Min Market Cap's data source**: `STOCKDB.csv`'s `MARKETCAP_CR` column — despite the name, this
  holds absolute Rs, not Rs Cr (divide by 1e7; verified against the file's own LARGECAP/MIDCAP/
  SMALLCAP/MICROCAP tier boundaries, which line up exactly once converted). A ticker missing from
  `STOCKDB.csv` entirely fails the filter; a value of exactly 0 is treated as "not yet populated"
  and passes rather than fails, since the file uses 0 as a placeholder (e.g. SCI, SBICARD) rather
  than a genuine zero market cap.
  **`Price_Band_List.csv` is fully retired** as of this change — `momentum_lib.py`'s
  `compute_universe_rankings()`/`compute_circuit_hits()` now read Series/Band from `STOCKDB.csv`
  instead (param renamed `band_csv_path` → `stockdb_csv_path`, columns `Symbol`/`Series`/`Band` →
  `SYMBOL`/`SERIES`/`BAND`). The two files' data genuinely differ for some tickers (not just
  formatting) — e.g. HFCL (a live holding) is `EQ` in the old file, `BE` in `STOCKDB.csv` — so this
  changes real EQ-Series/Circuit-Hit filter outcomes if either is enabled, though both are off by
  default today. A redundant `result["SERIES"]` recomputation that existed separately in both
  Streamlit dashboards (duplicating what `compute_universe_rankings()` already sets) was deleted
  rather than migrated, since it was dead weight even before this change.
  **Deliberate simplification vs. Streamlit**: this page does not
  live-preview ranking impact as you type — the ranking cache is keyed on the *saved* config,
  so a change takes effect project-wide only after Save (and the next page load). Streamlit's
  live preview is a side effect of its rerun-everything-on-every-widget-change model, which
  doesn't map onto a request/response server without either a very different architecture or an
  ~10s recompute per keystroke — the page says so explicitly, rather than silently behaving
  differently from what a Streamlit user would expect.
- A **nav bar** in `base.html` links all seven pages (plain `<a>` tags, full page loads —
  no client-side router needed), in the same order as the Streamlit tab bar.
- **Layout now fills the browser window** (`.app` is `width: 100%` instead of a fixed
  `max-width` centered box) — the wide tables (Full Rankings, Tradelog/Cash history) benefit most.
- **`TEMPLATES_AUTO_RELOAD = True`** in `create_app()`. Without it, Jinja compiles each
  template once and never re-checks its mtime under waitress (no debug reloader) — an edited
  `.html` file keeps silently serving its old version until the process restarts. Bit us
  twice during development (a hidden `circuit-threshold-row` that stayed visible, a fixed
  CDN integrity hash that kept failing) before this was added. Cost is one mtime stat per
  template per render — negligible.
- **Every `<table class="data-table">` header is click-to-sort** (`static/js/sortable_tables.js`)
  — no per-table wiring needed, it applies to the whole app via one script tag in `base.html`.
  Numeric/currency cells ("Rs 1,234.56", "+3.88%") sort by value via a light strip-and-parse,
  date-like cells sort chronologically via `Date.parse`, everything else sorts as text; blank/
  placeholder cells ("—") always sort last regardless of direction. Re-scans on every
  `htmx:afterSettle`, so a table that arrives later (Full Rankings after a filter change,
  Tradelog/Cash Ledger after add/edit/delete) becomes sortable automatically, with no extra
  code at the call site. **Bug caught during verification**: the script's own initial version
  called `document.body.addEventListener(...)` directly at the top level — since the script
  tag sits in `<head>`, `document.body` is still `null` at that point, so the whole script
  silently threw and never even registered `DOMContentLoaded`. Nothing sorted, no visible
  error unless you opened devtools. Fixed by nesting that registration inside the
  `DOMContentLoaded` handler.

68 tests pass: Tradelog's and Cash Ledger's pure-logic tests (average-cost accounting, oversell
rejection, inflow/outflow summation, chronological running balance, atomic save/backup — all
against `tmp_path`, never live files), Configuration's clamping/params/ADTV/circuit-count/Min-CMP-
and Min-Market-Cap-pass-count tests against the live bundle (read-only) — including a regression
test that HFCL's SERIES resolves to `BE` (STOCKDB.csv) rather than the old `EQ` (Price_Band_List.csv)
to prove the source actually switched — and HTTP-level tests everywhere that prefill/edit-form
loaders return the exact requested values and that every mutating route is blocked with a
200 + banner (not a silent 403) and touches no data file while read-only.

**Not yet covered by an automated test**: a real end-to-end write (add/edit/delete, or a config
Save, actually persisting) with `SHARPE_READ_ONLY=0`. `settings.DATA_DIR` is imported by value
into several core modules at import time, so redirecting it mid-test-run means monkeypatching
each of those modules individually rather than one central patch -- doable, but deliberately
deferred until closer to cutover rather than adding fragile cross-module patching now. `configuration.save()` and `cash_ledger.save_cash_ledger()` do each have a direct `tmp_path` test
(monkeypatching just their own module's `DATA_DIR`), so the write mechanics themselves are
covered — what's not covered end-to-end is the full HTTP round trip with the flag actually off.
The Actions Monitor's exit-trigger and new-entry-candidate paths were similarly verified against
an in-memory synthetic ledger during development (not committed as a test) since the live N750
portfolio currently has zero exit signals to exercise those branches naturally.

**Tradelog's "Refresh Live Market Prices"** (`core/dhan_client.refresh_live_prices`) is also
done: Dhan's batched LTP first, per-ticker yfinance thread pool fallback, same as the
dashboard's button. Cached in-process with no TTL (only refreshes on click, like the
dashboard). `dhan_client.apply_cached_prices()` is the shared merge -- overrides the
workbook's last price wherever it's applied, until the next click. Not gated by
`SHARPE_READ_ONLY` -- it's a live external read with no file write, same category as the
VIX/cap-tier fetches. **The override is global, matching the dashboard's own
`st.session_state.live_prices` semantics**: Tradelog applies it via `_effective_prices()`,
and Top-N Rankings' LTP column applies the exact same cache via
`core.rankings.top_rows(..., prices=dhan_client.apply_cached_prices(...))` -- one button,
one shared cache, both pages read it. Verified live: refreshing on Tradelog changed
WELCORP to Rs 2,796.80 there, and the Top Rankings page (a full navigation away, no shared
browser state involved) showed the identical Rs 2,796.80 in its LTP column.

Next, in order:
1. Before flipping `SHARPE_READ_ONLY=0` for real: write the end-to-end write test noted above
   against a scratch `SHARPE_DATA_DIR` copy, then use both UIs side by side for a few days
   with Streamlit as the only writer, before trusting the webapp with real trades
2. All tabs from the Streamlit dashboard are now ported. Remaining polish: vendor HTMX locally
   (see below), trim the equity-chart tooltip's HTML-string construction (see Known gaps)

Known gaps: the "held" flag reads the positions ledger
rather than recomputing from the tradelog, and the equity chart tooltip builds HTML from
equity_history.json fields via template strings -- fine since that file is only ever written
by `Sharpe.py`, not by any user input, but worth tightening if a write path ever feeds into
it. HTMX and the emoji glyphs assume a browser with a decent font; no IE-era fallback.

HTMX is loaded from a CDN for now — vendor `htmx.min.js` into `static/js/` before relying
on this running without internet access.
