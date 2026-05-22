===============================================================================
 N500 / N750 / NSEAll MOMENTUM SCORING — EXECUTION GUIDE
===============================================================================

OVERVIEW
--------
Two Python scripts compute momentum scores for NSE stocks and output a ranked
Excel workbook with exit recommendations and position management.

  momentum_lib.py   Library of reusable scoring functions (do not run directly)
  Sharpe.py         Main script — loads data, evaluates exits, writes output


REQUIRED FILES (all in the same folder)
----------------------------------------
  momentum_lib.py                  (library — must be present)
  Sharpe.py                        (main script)
  <UNIVERSE>_updated.xlsx          (input price data — see format notes below)
  <UNIVERSE>_positions_ledger.json (auto-created on first run if missing)


DEPENDENCIES
------------
Install once using pip:

  pip install pandas numpy openpyxl scipy

Python version: 3.9 or higher recommended.


INPUT FILE FORMAT  (<UNIVERSE>_updated.xlsx)
---------------------------------------------
Sheet name  : DATA
Row 1       : Header row
  - Column 1        : TICKER  (text label e.g. "ASHOKLEY", "NIFTY500")
  - Column 2        : CLOSE   (latest close price — informational only)
  - Column 3        : 52WK HIGH
  - Columns 4+      : Daily close prices, one column per trading date
                      Column header must be a date value (Excel date format)

Special rows:
  - NIFTY500        : Must be present — used as the market benchmark for
                      residual momentum. Automatically separated from the
                      stock universe before scoring.

Price data rules:
  - Missing / zero prices are treated as NaN and excluded from calculations
  - Stocks need at least 90% data coverage for a given window to receive a score
  - Duplicate tickers: if a ticker appears more than once, only the FIRST
    occurrence is used. Remove duplicates from the file before running.


HOW TO RUN
----------
Step 1.  Place all files in the same folder.

Step 2.  Open a terminal / command prompt and navigate to that folder.

Step 3.  Run the script — pass the universe name as the first argument:

           python Sharpe.py N500          # reads N500_updated.xlsx
           python Sharpe.py N750          # reads N750_updated.xlsx
           python Sharpe.py NSEAll        # reads NSEAll_updated.xlsx

         Derived automatically from the UNIVERSE argument:
           Input   : <UNIVERSE>_updated.xlsx
           Output  : <UNIVERSE>_rankings.xlsx
           Ledger  : <UNIVERSE>_positions_ledger.json

         Optional — override the ledger file path:
           python Sharpe.py N500 path/to/custom_ledger.json

Step 4.  The script prints progress, exit actions, and entry candidates to
         the console.

Step 5.  Output file is written to the same folder:
           <UNIVERSE>_rankings.xlsx


WHAT THE SCRIPT COMPUTES (in order)
-------------------------------------
1. SHARPE RATIOS
   Windows : 3M (63d), 6M (126d), 9M (189d), 12M (252d)
   Method  : Annualised Sharpe = mean(excess log-returns) / std * sqrt(252)
             Excess = log-return minus daily risk-free rate (7% / 252)
   Output  : S_3M, S_6M, S_9M, S_12M

2. SHARPE Z-SCORES
   Each window is Z-scored cross-sectionally (mean=0, std=1 across all stocks)
   Output  : Z_3M, Z_6M, Z_9M, Z_12M

3. COMPOSITE SCORES (derived from Z-scores)
   SHARPE_ALL  = mean(Z_12M, Z_9M, Z_6M, Z_3M)  — 4-window equal-weighted
                 This is the PRIMARY RANKING metric (also shown as COMPOSITE)
   SHARPE_3    = mean(Z_12M, Z_6M, Z_3M)         — 3-window (excludes 9M)
                 Display and cross-check only; not used for ranking

   Note: SHARPE_ALL / COMPOSITE is normalised via normalise_composite()
   before ranking. MOM_ACCEL and SHARPE_ST / SHARPE_LT are NOT computed
   in the current version.

4. 52-WEEK HIGH FILTER
   PCT_FROM_52H = (last price / 52-week high - 1) * 100
   Eligibility  : PCT_FROM_52H >= -25  (stock must be within 25% of its high)
   Stocks outside this range receive no RANK (NaN); all scores are still computed.
   An existing held stock that fails this filter triggers an EXIT_52H signal.

5. RESIDUAL MOMENTUM  (windows: 3M, 6M, 9M, 12M)
   Method  : For each window, regress stock log-returns on NIFTY500 log-returns
             using OLS. Compute Sharpe ratio on the residuals.
             High score = strong return not explained by market movement.
   Z-scored cross-sectionally per window → RES_MOM composite
   RES_MOM is displayed only — not used in ranking or exit logic.

6. MARKET REGIME — DYNAMIC REGIME SCORE

   Uses a continuous Regime Strength Score (0.0 to 1.0) built from 4 signals.
   The score drives Dynamic Top-N selection so the portfolio size shrinks in
   weak markets and expands in strong markets. Unallocated capital flows
   automatically to the 6% Liquid Fund.

   Signal Composition:
     Signal 1 — EMA50 Distance     (weight 35%)
       How far NIFTY500 price is above/below its own EMA50.
       +10% above EMA50 -> score 1.0 | At EMA50 -> 0.5 | -10% below -> 0.0
       Band: EMA50_BAND = 10%

     Signal 2 — EMA Trend          (weight 25%)
       Distance between EMA50 and EMA200 — measures trend alignment.
       EMA50 5%+ above EMA200 -> score 1.0 | Inline -> 0.5 | Below -> 0.0
       Band: EMA_TREND_BAND = 5%

     Signal 3 — 52H Breadth        (weight 25%)
       Fraction of stocks within -25% of their 52-week high.
       Already computed as eligible_count / total_stocks.
       High value = many stocks in uptrends = strong breadth.

     Signal 4 — Momentum Breadth   (weight 15%)
       Fraction of eligible stocks with COMPOSITE score > 1.5.
       Measures quality of opportunity (not just index level).

   Composite Score:
     regime_score = 0.35 * ema50_score
                  + 0.25 * ema_trend_score
                  + 0.25 * breadth_score
                  + 0.15 * momentum_score

   Score -> Dynamic Top-N Mapping:
     dynamic_n = MIN_N + regime_score * (MAX_N - MIN_N)
     MIN_N = 5   MAX_N = 25

     Score 0.9-1.0 -> N = 23-25  (strong bull, full deployment)
     Score 0.7-0.9 -> N = 19-23  (healthy uptrend)
     Score 0.5-0.7 -> N = 15-19  (neutral / cautious)
     Score 0.3-0.5 -> N = 10-14  (weak / deteriorating)
     Score 0.0-0.3 -> N =  5- 9  (near-bear, maximum caution)

   New Entry Gate:
     New buys are only permitted when regime_score >= NEW_ENTRY_THRESHOLD (0.40).
     Below this threshold: exit evaluation continues normally; no new slots filled.
     Portfolio shrinks organically as exits occur — freed capital -> Liquid Fund.

   Backtest Evidence vs V1 (Apr 2020 - Apr 2026, N500 universe):
     Binary EMA50 (v1) : CAGR 38.3%  /  MDD -16.3%  / Avg Holdings 20 / Avg Cash ~15%
     Dynamic Score (v2): CAGR 41.8%  /  MDD -17.2%  / Avg Holdings 16.5 / Avg Cash 24.2%
     Net change        : +3.5% CAGR  /  -0.9% MDD — significantly better risk-adjusted return

7. RANKING
   Ranked by SHARPE_ALL (COMPOSITE) descending, eligible stocks only
   (PCT_FROM_52H >= -25). Ineligible stocks receive RANK = NaN.
   Ties broken by "first" method (order of appearance in data).

8. CAPITAL ALLOCATION  (volatility-weighted, capped at 5% per stock)
   For each Top-N stock, raw weight = composite_score / mean_annualised_vol
   Weights are normalised across Top N and capped at 5.0% per position.
   Remaining weight goes to Cash (earning LIQUID_YIELD_PA = 6% p.a.).
   Allocation displayed in INR for a baseline capital of INR 10,00,000.


EXIT LOGIC
-----------
Two distinct exit triggers are evaluated every rebalance against the position
ledger. They are intentionally separate — EXIT_52H overrides the hold lock;
EXIT_RANK respects it.

  1. EXIT_52H — 52H disqualification  (overrides 28-day hold lock)
       If PCT_FROM_52H < -25%, the stock has RANK = NaN.
       Any held stock with NaN rank is flagged EXIT_52H and must be sold
       immediately, regardless of how recently it was bought.

  2. EXIT_RANK — rank-based exit  (respects 28-day hold lock)
       If a held stock's rank drops to > HOLD_RANK_BUFFER (40)
       AND the stock has been held for >= MIN_HOLD_DAYS (28 calendar days),
       it is flagged EXIT_RANK.
       If the hold lock is still active (< 28 days), the stock is retained
       with a note showing how many days remain on the lock.

  3. REGIME GATE on new entries
       New buys are only permitted when regime_score >= NEW_ENTRY_THRESHOLD (0.40).
       Existing positions are always evaluated for exit normally.
       Portfolio size scales with regime_score; unallocated weight -> Liquid Fund.


POSITION LEDGER
---------------
Positions are tracked in a JSON file (<UNIVERSE>_positions_ledger.json):

  {
    "TICKER": {
      "entry_date":  "YYYY-MM-DD",
      "entry_price": <float>
    },
    ...
  }

- The ledger is loaded at the start of each run.
- Exits are removed; new entries are appended with today's price and date.
- The updated ledger is saved at the end of every run.
- Do not edit the ledger manually.
- If the file does not exist, the script starts with an empty ledger.


OUTPUT FILE  (<UNIVERSE>_rankings.xlsx — 3 sheets)
----------------------------------------------------
Sheet 1: TOP20
  Columns : RNK, TICKER, STATUS, TARGET_WT%, ALLOC_INR,
            SHARPE_ALL, RES_MOM, SHARPE_3, 52H%
  Rows    : Top 20 ranked stocks (eligible only, sorted by SHARPE_ALL)

  STATUS values:
    NEW BUY   — not previously held; added to ledger this run
    HOLD      — already in ledger; rank and 52H within acceptable bounds
    EXIT-52H  — held stock breached the -25% 52H gate; sell immediately
    EXIT-RANK — held stock rank > 40 and held >= 28 days; sell
    WATCH     — in Top 20 but not held and no action this run

  Row colour coding:
    Green tint  — NEW BUY
    Amber tint  — EXIT-52H
    Red tint    — EXIT-RANK
    White       — HOLD / WATCH

Sheet 2: EXITS
  All exit actions this rebalance (both EXIT_52H and EXIT_RANK).
  Columns : TICKER, TRIGGER, RANK, 52H%, HELD_DAYS, ENTRY_DATE,
            ENTRY_PRICE, NOTE
  Empty if no exits this rebalance.

Sheet 3: CALCS
  All stocks, 25 columns:
  RANK, TICKER,
  S_12M, S_9M, S_6M, S_3M,              (raw Sharpe per window)
  Z_12M, Z_9M, Z_6M, Z_3M,              (Sharpe Z-scores per window)
  SHARPE_ALL, SHARPE_3,                  (composite scores)
  RS_12M, RS_9M, RS_6M, RS_3M,          (residual Sharpe per window)
  RZ_12M, RZ_9M, RZ_6M, RZ_3M, RES_MOM,(residual Z-scores + composite)
  1M%, 3M%, 12M%,                        (price returns)
  52H%                                   (% from 52-week high; green if >= -25)


CONFIG PARAMETERS (top of Sharpe.py)
--------------------------------------
  UNIVERSE          N500 / N750 / NSEAll   Passed as command-line arg (default: N500)
  RFR_ANNUAL        0.07                   Risk-free rate (7% p.a.)
  TRADING_DAYS      252                    Trading days per year
  TOP_N             20                     Used for Excel label/max bounds (sizing uses dynamic_n)
  HOLD_RANK_BUFFER  40                     Exit threshold: rank > 40 -> eligible for exit
  MIN_HOLD_DAYS     28                     Calendar days before rank-based exit is permitted
  LIQUID_YIELD_PA   0.06                   6% p.a. on idle cash
  PORTFOLIO_CAPITAL 1,000,000              INR baseline for allocation display
  WINDOWS           12M/9M/6M/3M           Windows for Residual Momentum
  SHARPE_WINDOWS    12M/9M/6M/3M           Windows for Sharpe (same as WINDOWS)
  OUTPUT_FILE       <UNIVERSE>_rankings.xlsx   Auto-derived from UNIVERSE arg

  --- DYNAMIC REGIME PARAMETERS ---
  MIN_N               5      Minimum portfolio size at lowest regime score
  MAX_N               25     Maximum portfolio size at highest regime score
  NEW_ENTRY_THRESHOLD 0.40   Regime score below this -> no new buys
  EMA50_BAND          0.10   ±10% band around EMA50 for signal normalisation
  EMA_TREND_BAND      0.05   ±5% band for EMA50 vs EMA200 trend signal
  SIGNAL_WEIGHTS      ema50=0.35, ema_trend=0.25, breadth=0.25, momentum=0.15


USING momentum_lib.py IN YOUR OWN SCRIPT
-----------------------------------------
The library functions can be imported and called independently:

  import momentum_lib as ml

  prices_df, nifty_series, tickers, dates = ml.load_prices("N500_updated.xlsx")

  sharpe_df, z_df = ml.compute_sharpe(
      prices_df, tickers,
      windows={"12M": 252, "9M": 189, "6M": 126, "3M": 63},
      rfr_daily=0.07/252,
      trading_days=252
  )

  resmom_df, rs_z_df = ml.compute_residual_momentum(
      prices_df, tickers, nifty_series,
      windows={"12M": 252, "9M": 189, "6M": 126, "3M": 63},
      trading_days=252
  )

  ret_df    = ml.compute_returns(prices_df, tickers)
  pct_52h   = ml.compute_pct_from_52h(prices_df, tickers)
  regime    = ml.compute_market_regime(nifty_series)


TYPICAL RUNTIME
---------------
  ~30-60 seconds on a standard laptop for 500+ stocks
  Residual Momentum (OLS per stock per window) is the most compute-intensive step


TROUBLESHOOTING
---------------
  ModuleNotFoundError: momentum_lib    → Sharpe.py and momentum_lib.py must
                                         be in the same directory
  KeyError: 'NIFTY500'                 → DATA sheet must contain a NIFTY500 row
  KeyError: 'DATA'                     → Check xlsx sheet is named exactly DATA
  All scores NaN for a stock           → Stock has fewer than 90% valid prices
                                         for the shortest window required
  Duplicate index error                → Remove duplicate tickers from the
                                         input file before running
  Ledger not found                     → Script will create a new empty ledger
                                         automatically on first run

===============================================================================