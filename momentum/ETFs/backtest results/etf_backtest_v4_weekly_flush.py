"""
ETF Momentum Backtest Engine -- v4 (Weekly Full-Flush)
======================================================
Isolated test to determine whether weekly rebalance frequency ALONE adds value
before layering in selective-exit complexity (v3).

Strategy:
  Scoring   : 0.4*Sharpe_1M + 0.4*Sharpe_3M - 0.2*Sharpe_6M  (v1 momentum-acceleration)
               If 6M unavailable: 0.5*Sh1M + 0.5*Sh3M
               If 1M or 3M unavailable: ETF excluded
  Screen    : NAV within 25% of 52-week high
  Regime    : 3-state (v1 logic, evaluated weekly on prev trading day):
                BULL    : EMA50 > EMA100 AND Price > EMA50  -> 5 slots
                PARTIAL : Price > EMA100 (not BULL)         -> 3 slots
                BEAR    : Price <= EMA100                   -> 0 slots, 100% cash
  Rebalance : FULL FLUSH every Monday (first trading day of week):
                1. Exit ALL open positions at Monday NAV
                2. Re-score universe as of previous Friday
                3. In BULL/PARTIAL: enter top-N/top-3 ETFs (sector cap 1)
                4. In BEAR: stay 100% cash until next week
  Exit rules:
    1. Daily TSL: 10% trailing stop from running peak -> immediate exit that day
    (Full flush on Monday also implicitly handles rank/regime exits)
  Position  : Equal weight (1/5th of portfolio per slot, based on TOP_N=5)
  Sector cap: 1 ETF per sector
  Costs     : INR 20 per trade leg
  Cash rate : 2% p.a. on idle cash
  RF rate   : 6% p.a.

Comparison chart: v1 (Monthly) vs v4 (Weekly Full-Flush) vs Nifty 500.

Usage:
  python etf_backtest_v4_weekly_flush.py
"""

import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import yfinance as yf

# =========================================================
# CONFIG
# =========================================================
_BASE = Path(__file__).resolve().parent

class CONFIG:
    INPUT_FILE       = str(_BASE / "ETF - Backtest  - Copy.xlsx")
    TRADE_LOG_FILE   = str(_BASE / "v4_backtest_trade_log.csv")
    EQUITY_LOG_FILE  = str(_BASE / "v4_backtest_equity.csv")
    CHART_FILE       = str(_BASE / "v4_equity_curve.png")
    COMPARE_CHART    = str(_BASE / "v4_comparison_equity_curve.png")

    # v1 equity CSV for comparison overlay (produced by etf_backtest.py)
    V1_EQUITY_FILE   = str(_BASE / "backtest_equity.csv")

    START_CAPITAL    = 1_000_000.0   # INR 10 lakh
    CASH_INTEREST_PA = 0.02          # 2% p.a. on idle cash
    TRADE_COST_FIXED = 20.0          # INR per trade leg
    TSL_THRESHOLD    = 0.10          # 10% trailing stop loss (same as v1)

    TOP_N            = 5             # max simultaneous positions in BULL regime
    TOP_N_PARTIAL    = 3             # max positions in PARTIAL regime
    SECTOR_CAP       = 1             # max ETFs per sector

    WINDOW_6M        = 126           # 6-month lookback (trading days)
    WINDOW_3M        = 63            # 3-month lookback (trading days)
    WINDOW_1M        = 21            # 1-month lookback (trading days)
    ANNUALIZE        = 252
    DAILY_RF         = 0.06 / 252    # 6% p.a. risk-free rate
    MAX_DD_FROM_HIGH = 0.25          # 52-week high proximity screen

    # 3-state regime (v1 logic, evaluated weekly)
    TREND_FAST_EMA   = 50            # EMA50
    TREND_SLOW_EMA   = 100           # EMA100
    BENCHMARK_TICKER = "^CRSLDX"    # Nifty 500 index via Yahoo Finance


# =========================================================
# SECTOR CLASSIFICATION
# =========================================================
_SECTOR_RULES = [
    ("PSU_BANK",         ["psu bank","psubnk","psubank","bse psu bank"]),
    ("PRIVATE_BANK",     ["private bank","pvt bank","pvtban","nifty pb "]),
    ("BANKING_BROAD",    ["nifty bank","bse bank"," bank ","banketf","bankbees","banknifty","nifban"]),
    ("IT_TECH",          ["nifty it","bse it"," it etf","itbees","itietf","nifit","tech etf"]),
    ("HEALTHCARE",       ["healthcare","pharma","health "," hc "]),
    ("METAL",            ["metal"]),
    ("ENERGY",           ["energy","oil & gas","o&g","oilietf","power etf","bse power"]),
    ("INFRA",            ["infra"]),
    ("CONSUMPTION",      ["consumption","consump","fmcg","consumer"]),
    ("REALTY",           ["realty","real estate"]),
    ("DEFENCE",          ["defence","dfnc"]),
    ("PSE",              ["pse etf","cpse","nifty pse","bharat 22","cpseetf"]),
    ("AUTO",             ["auto"]),
    ("CHEMICALS",        ["chemical"]),
    ("FIN_SERVICES",     ["fin serv","financial serv","finietf","bfsi","capital mkt","captl mkt",
                          "capital market","cptmkt","capital mrkts"]),
    ("COMMODITIES",      ["commodity","commo"]),
    ("MANUFACTURING",    ["manufactur","manu"]),
    ("EV_MOBILITY",      ["ev&new","ev new","nifty ev"]),
    ("DIGITAL_INTERNET", ["internet","digital"]),
    ("RAILWAY",          ["railway"]),
    ("TOURISM",          ["trsm","tourism"]),
    ("MNC",              ["mnc"]),
    ("GOLD",             ["gold"]),
    ("SILVER",           ["silver"]),
    ("GOVT_BONDS",       ["g-sec","gsec","gilt","bond etf","bharat bond","ebbetf"]),
    ("DIVIDEND",         ["dividend","div opp"]),
    ("IPO",              ["ipo"]),
    ("ESG",              ["esg"]),
    ("FACTOR_MOMENTUM",  ["momentum","mmt","mmntm"]),
    ("FACTOR_VALUE",     ["value 20","value 30","value 50","enhanced val","enhval"]),
    ("FACTOR_QUALITY",   ["quality","qlty"," ql "," ql30","qual30"]),
    ("FACTOR_LOW_VOL",   ["low vol","lowvol","lw- vol"]),
    ("FACTOR_ALPHA",     ["alpha"]),
    ("FACTOR_EQUAL_WT",  ["equal weight","eq weight","eqwt","eqlwgt","eqlwght","equal wt"]),
    ("INTERNATIONAL",    ["nasdaq","s&p 500","hang seng","hangseng","hngsng","msci","fang+"]),
    ("MIDCAP",           ["midcap","mid cap","mdsmc","midsmall"]),
    ("SMALLCAP",         ["smallcap","small cap","sml100","smcp"]),
    ("NEXT_50",          ["next 50","next50","juniorbees","jr bees"]),
    ("BROAD_MARKET",     ["nifty 50","nifty50","sensex","nifty 100","nifty100","nifty 200",
                          "nifty 500","nifty500","total market","total mrkt","bse 500","bse500",
                          "multicap","mltcp","lgmdcp","gth sectors","flexicap","flexi"]),
    ("SERVICES",         ["services","svcs"]),
]

def classify_sector(name: str, ticker: str) -> str:
    n, t = name.lower(), ticker.lower()
    for sector, kws in _SECTOR_RULES:
        if any(kw in n or kw in t for kw in kws):
            return sector
    return "OTHER"


# =========================================================
# SCORING -- v1 formula: 0.4*Sh1M + 0.4*Sh3M - 0.2*Sh6M
# =========================================================
def sharpe_score(series: pd.Series, window: int) -> float:
    """Annualised Sharpe over the last `window` trading days."""
    clean = series.dropna()
    if len(clean) < window + 1:
        return np.nan
    log_ret = np.log(clean.iloc[-window - 1:] / clean.iloc[-window - 1:].shift(1)).dropna()
    excess  = log_ret - CONFIG.DAILY_RF
    std = excess.std()
    return (excess.mean() / std) * np.sqrt(CONFIG.ANNUALIZE) if std > 0 else np.nan


def compute_wtd_sharpe(sh1: float, sh3: float, sh6: float) -> float:
    """
    v1 composite score: momentum-acceleration (recent Sharpe beats long-term).
      0.4*Sh1M + 0.4*Sh3M - 0.2*Sh6M
    - Exclude entirely if 1M or 3M is NaN.
    - Drop 6M term if NaN; rescale to 0.5/0.5.
    """
    if np.isnan(sh1) or np.isnan(sh3):
        return np.nan
    if np.isnan(sh6):
        return 0.5 * sh1 + 0.5 * sh3
    return 0.4 * sh1 + 0.4 * sh3 - 0.2 * sh6


# =========================================================
# DATA LOADING
# =========================================================
def load_data(filepath: str):
    """Load ETF price data from the backtest Excel file (DATA sheet, dates in row 1)."""
    print(f"Loading data from {filepath} ...")
    raw = pd.read_excel(filepath, sheet_name="DATA", header=None)
    header    = raw.iloc[0]
    date_cols = [c for c in range(2, raw.shape[1])
                 if pd.notna(header.iloc[c]) and isinstance(header.iloc[c], (datetime, pd.Timestamp))]
    dates = pd.to_datetime([header.iloc[c] for c in date_cols])

    meta = pd.DataFrame({
        "ETF_NAME": raw.iloc[1:, 0].fillna("").astype(str).str.strip(),
        "TICKER":   raw.iloc[1:, 1].astype(str).str.strip(),
    }).reset_index(drop=True)

    price_df = raw.iloc[1:, date_cols].apply(pd.to_numeric, errors="coerce").replace(0, np.nan)
    price_df.columns = dates
    price_df.index   = meta["TICKER"]
    prices = price_df.T.sort_index().ffill()
    print(f"  {len(meta)} ETFs | {len(dates)} date columns "
          f"({prices.index[0].date()} -> {prices.index[-1].date()})\n")
    return meta, prices


# =========================================================
# WEEK START DETECTION
# =========================================================
def get_week_start_days(all_dates: pd.DatetimeIndex) -> list:
    """Returns the first available trading day of each calendar week."""
    week_starts = []
    prev_key = None
    for d in all_dates:
        key = (d.isocalendar()[0], d.isocalendar()[1])
        if key != prev_key:
            week_starts.append(d)
            prev_key = key
    return week_starts


# =========================================================
# SCORING SNAPSHOT
# =========================================================
def rank_universe(meta: pd.DataFrame, prices: pd.DataFrame,
                  as_of: pd.Timestamp) -> pd.DataFrame:
    """
    Score and rank all ETFs using price data up to (and including) `as_of`.
    Returns DataFrame sorted by RANK (1 = best) among screen-pass ETFs.
    Non-screen / unscored ETFs get RANK = 0.
    """
    hist = prices.loc[:as_of]
    records = []

    for _, row in meta.iterrows():
        t = row["TICKER"]
        if t not in hist.columns:
            continue
        s = hist[t].dropna()
        if len(s) < CONFIG.WINDOW_1M + 1:
            continue

        close   = float(s.iloc[-1])
        high_52 = float(s.tail(252).max()) if len(s) >= 252 else float(s.max())

        if high_52 <= 0 or np.isnan(high_52):
            continue
        pct_from_high = (high_52 - close) / high_52
        screen_pass   = pct_from_high <= CONFIG.MAX_DD_FROM_HIGH

        sh1   = sharpe_score(s, CONFIG.WINDOW_1M)
        sh3   = sharpe_score(s, CONFIG.WINDOW_3M)
        sh6   = sharpe_score(s, CONFIG.WINDOW_6M)
        score = compute_wtd_sharpe(sh1, sh3, sh6)

        records.append({
            "TICKER"       : t,
            "NAME"         : row["ETF_NAME"],
            "SECTOR"       : classify_sector(row["ETF_NAME"], t),
            "CLOSE"        : close,
            "SCORE"        : score,
            "SCREEN_PASS"  : screen_pass,
            "SHARPE_1M"    : sh1,
            "SHARPE_3M"    : sh3,
            "SHARPE_6M"    : sh6,
            "PCT_FROM_HIGH": pct_from_high * 100,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    sp = df[df["SCREEN_PASS"] & df["SCORE"].notna()].copy()
    if len(sp) > 0:
        sp["RANK"] = sp["SCORE"].rank(ascending=False).astype(int)
        df = df.merge(sp[["TICKER", "RANK"]], on="TICKER", how="left")
        df["RANK"] = df["RANK"].fillna(0).astype(int)
    else:
        df["RANK"] = 0

    return df.sort_values("RANK").reset_index(drop=True)


# =========================================================
# REGIME EVALUATION (3-state, v1 logic)
# =========================================================
def evaluate_regime(regime_s: pd.Series, regime_ema50: pd.Series,
                    regime_ema100: pd.Series, as_of: pd.Timestamp) -> tuple:
    """
    3-state regime:
      BULL    : EMA50 > EMA100 AND Price > EMA50  -> 5 slots
      PARTIAL : Price > EMA100 (not BULL)         -> 3 slots
      BEAR    : Price <= EMA100                   -> 0 slots
    Returns (label, active_slots).
    """
    if as_of not in regime_s.index:
        return "BULL", CONFIG.TOP_N

    price  = regime_s.loc[as_of]
    ema50  = regime_ema50.loc[as_of]
    ema100 = regime_ema100.loc[as_of]

    if pd.isna(price) or pd.isna(ema50) or pd.isna(ema100):
        return "BULL", CONFIG.TOP_N

    if ema50 > ema100 and price > ema50:
        return "BULL",    CONFIG.TOP_N
    elif price > ema100:
        return "PARTIAL", CONFIG.TOP_N_PARTIAL
    else:
        return "BEAR",    0


# =========================================================
# HELPERS
# =========================================================
def _portfolio_value(portfolio: dict, prices: pd.DataFrame, d: pd.Timestamp) -> float:
    total = 0.0
    for t, pos in portfolio.items():
        if t in prices.columns:
            p = prices.loc[d, t]
            if not pd.isna(p):
                total += pos["shares"] * p
    return total


def _sell_position(t: str, pos: dict, price: float, d: pd.Timestamp,
                   reason: str, regime_label: str,
                   cash: float, trade_log: list) -> float:
    """Execute a sell, update cash, log the trade. Returns updated cash."""
    proceeds = pos["shares"] * price
    cost     = CONFIG.TRADE_COST_FIXED
    pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
    trade_log.append({
        "TYPE"        : "SELL",
        "REASON"      : reason,
        "TICKER"      : t,
        "NAME"        : pos.get("name", ""),
        "ENTRY_DATE"  : pos["entry_date"],
        "EXIT_DATE"   : d,
        "HOLDING_DAYS": (d - pos["entry_date"]).days,
        "ENTRY_PRICE" : round(pos["entry_price"], 4),
        "EXIT_PRICE"  : round(price, 4),
        "SHARES"      : round(pos["shares"], 4),
        "GROSS_PNL"   : round(proceeds - pos["shares"] * pos["entry_price"], 2),
        "COSTS"       : cost,
        "NET_PNL"     : round(pnl, 2),
        "REGIME"      : regime_label,
    })
    return cash + proceeds - cost


def _buy_position(t: str, r: pd.Series, slot_size: float, p_entry: float,
                  d: pd.Timestamp, regime_label: str,
                  portfolio: dict, cash: float, trade_log: list) -> float:
    """Execute a buy, update portfolio and cash. Returns updated cash."""
    shares = slot_size / p_entry
    portfolio[t] = {
        "shares"      : shares,
        "entry_price" : p_entry,
        "peak"        : p_entry,
        "entry_date"  : d,
        "name"        : r["NAME"],
        "sector"      : r["SECTOR"],
    }
    trade_log.append({
        "TYPE"        : "BUY",
        "REASON"      : f"WEEKLY_FLUSH_ENTRY (rank={r['RANK']})",
        "TICKER"      : t,
        "NAME"        : r["NAME"],
        "ENTRY_DATE"  : d,
        "EXIT_DATE"   : None,
        "HOLDING_DAYS": None,
        "ENTRY_PRICE" : round(p_entry, 4),
        "EXIT_PRICE"  : None,
        "SHARES"      : round(shares, 4),
        "GROSS_PNL"   : None,
        "COSTS"       : CONFIG.TRADE_COST_FIXED,
        "NET_PNL"     : None,
        "REGIME"      : regime_label,
    })
    return cash - (slot_size + CONFIG.TRADE_COST_FIXED)


# =========================================================
# MAIN v4 BACKTEST ENGINE -- Weekly Full-Flush
# =========================================================
def run_backtest_v4() -> dict:
    """
    Weekly Full-Flush backtest:
      - Every Monday: exit ALL positions, score universe, re-enter top-N
      - Daily 10% TSL check (intra-week exits only)
      - 3-state BULL/PARTIAL/BEAR regime evaluated each Monday
      - BULL: enter top-5, PARTIAL: enter top-3, BEAR: 100% cash
    """
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates    = prices.index

    # -- Fetch ^CRSLDX for regime + benchmark --
    print(f"Fetching Nifty 500 regime data ({CONFIG.BENCHMARK_TICKER}) ...")
    try:
        reg_raw = yf.download(
            CONFIG.BENCHMARK_TICKER,
            start=all_dates[0].strftime("%Y-%m-%d"),
            end=(all_dates[-1] + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            auto_adjust=True, progress=False,
        )
        regime_raw = reg_raw["Close"].squeeze().dropna()
        regime_raw.index = pd.to_datetime(regime_raw.index).tz_localize(None)
        regime_s      = regime_raw.reindex(all_dates, method="ffill")
        regime_ema50  = regime_s.ewm(span=CONFIG.TREND_FAST_EMA,  adjust=False).mean()
        regime_ema100 = regime_s.ewm(span=CONFIG.TREND_SLOW_EMA, adjust=False).mean()
        print(f"  Regime data: {regime_s.dropna().index[0].date()} -> "
              f"{regime_s.dropna().index[-1].date()}  ({regime_s.notna().sum()} pts)\n")
    except Exception as e:
        print(f"  [WARN] Could not fetch regime data: {e}. Defaulting to BULL.\n")
        regime_s = regime_ema50 = regime_ema100 = None

    week_starts = set(get_week_start_days(all_dates))
    date_list   = list(all_dates)
    date_index  = {d: i for i, d in enumerate(date_list)}

    cash           = CONFIG.START_CAPITAL
    portfolio      = {}   # ticker -> {shares, entry_price, peak, entry_date, name, sector}
    equity_history = []
    trade_log      = []
    regime_label   = "BULL"
    active_slots   = CONFIG.TOP_N

    print(f"Starting v4 (Weekly Full-Flush) backtest: {date_list[0].date()} -> {date_list[-1].date()}")
    print(f"TSL: {CONFIG.TSL_THRESHOLD*100:.0f}%  |  Regime: 3-state BULL/PARTIAL/BEAR"
          f"  |  Full flush every Monday  |  RF: 6% p.a.\n")

    for d in all_dates:
        idx = date_index[d]

        # -- Accrue daily cash interest --
        cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)

        is_rebalance = (d in week_starts)

        if is_rebalance:
            # Use previous trading day's data to avoid look-ahead bias
            prev_day = date_list[idx - 1] if idx > 0 else d

            # Evaluate 3-state regime as of prev_day
            if regime_s is not None:
                regime_label, active_slots = evaluate_regime(
                    regime_s, regime_ema50, regime_ema100, prev_day)
            else:
                regime_label, active_slots = "BULL", CONFIG.TOP_N

            # ---- FULL FLUSH: exit ALL open positions ----
            for t in list(portfolio.keys()):
                pos    = portfolio[t]
                p_exit = (prices.loc[d, t]
                          if t in prices.columns and not pd.isna(prices.loc[d, t])
                          else pos["entry_price"])
                cash = _sell_position(t, pos, p_exit, d,
                                      "WEEKLY_FLUSH_EXIT", regime_label,
                                      cash, trade_log)
            portfolio = {}

            # ---- Score universe as of prev_day ----
            rank_df = rank_universe(meta, prices, prev_day)

            # ---- Re-enter if BULL or PARTIAL ----
            if active_slots > 0 and not rank_df.empty:
                total_val = cash   # portfolio is empty after flush
                slot_size = total_val / CONFIG.TOP_N  # divide by 5 for equal weight

                sector_count = {}
                candidates   = rank_df[
                    rank_df["SCREEN_PASS"] &
                    rank_df["SCORE"].notna() &
                    (rank_df["RANK"] > 0)
                ].sort_values("RANK")

                for _, r in candidates.iterrows():
                    if len(portfolio) >= active_slots:
                        break
                    t      = r["TICKER"]
                    sector = r["SECTOR"]
                    if sector_count.get(sector, 0) >= CONFIG.SECTOR_CAP:
                        continue

                    p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                    if pd.isna(p_entry) or p_entry <= 0:
                        continue

                    cash = _buy_position(t, r, slot_size, p_entry, d,
                                         regime_label, portfolio, cash, trade_log)
                    sector_count[sector] = sector_count.get(sector, 0) + 1

        # -- Daily TSL check (10%) --
        for t in list(portfolio.keys()):
            if t not in prices.columns:
                continue
            p_curr = prices.loc[d, t]
            if pd.isna(p_curr):
                continue

            pos = portfolio[t]
            if p_curr > pos["peak"]:
                pos["peak"] = p_curr

            drawdown = (pos["peak"] - p_curr) / pos["peak"]
            if drawdown >= CONFIG.TSL_THRESHOLD:
                cash = _sell_position(t, pos, p_curr, d,
                                      f"TSL_HIT ({drawdown*100:.1f}%)", regime_label,
                                      cash, trade_log)
                del portfolio[t]

        # -- Record daily equity --
        port_val = _portfolio_value(portfolio, prices, d)
        equity_history.append({
            "date"  : d,
            "equity": cash + port_val,
            "regime": regime_label,
        })

    # =========================================================
    # RESULTS & METRICS
    # =========================================================
    eq = pd.DataFrame(equity_history).set_index("date")
    eq.to_csv(CONFIG.EQUITY_LOG_FILE)

    tlog = pd.DataFrame(trade_log)
    tlog.to_csv(CONFIG.TRADE_LOG_FILE, index=False)

    sells       = tlog[tlog["TYPE"] == "SELL"] if len(tlog) else pd.DataFrame()
    tsl_hits    = sells[sells["REASON"].str.startswith("TSL")]          if len(sells) else pd.DataFrame()
    flush_exits = sells[sells["REASON"].str.startswith("WEEKLY_FLUSH")] if len(sells) else pd.DataFrame()
    buys        = tlog[tlog["TYPE"] == "BUY"]  if len(tlog) else pd.DataFrame()

    print(f"Trade Log -> {CONFIG.TRADE_LOG_FILE}")
    print(f"  Total BUYs              : {len(buys)}")
    print(f"  Total SELLs             : {len(sells)}")
    print(f"  Weekly flush exits      : {len(flush_exits)}")
    print(f"  TSL exits (10%)         : {len(tsl_hits)}")
    if len(sells) > 0:
        wins = (sells["NET_PNL"] > 0).sum()
        print(f"  Win Rate (sells)        : {wins/len(sells):.1%}")
        print(f"  Avg Net P&L/trade       : INR {sells['NET_PNL'].mean():,.0f}")

    years     = (eq.index[-1] - eq.index[0]).days / 365.25
    initial   = CONFIG.START_CAPITAL
    final     = eq["equity"].iloc[-1]
    cagr      = (final / initial) ** (1 / years) - 1
    eq["peak"]     = eq["equity"].cummax()
    eq["drawdown"] = (eq["equity"] - eq["peak"]) / eq["peak"]
    max_dd    = eq["drawdown"].min()
    daily_ret = eq["equity"].pct_change().dropna()
    vol       = daily_ret.std() * np.sqrt(252)
    sharpe    = cagr / vol if vol > 0 else 0
    annual_trades = len(buys) / years

    print("\n" + "=" * 58)
    print(f"  v4 BACKTEST RESULTS  (10% TSL | BULL/PARTIAL/BEAR | Weekly Full-Flush)")
    print("=" * 58)
    print(f"  Period             : {eq.index[0].date()} -> {eq.index[-1].date()}")
    print(f"  Start Capital      : INR {initial:>12,.0f}")
    print(f"  End Capital        : INR {final:>12,.0f}")
    print(f"  Total Return       : {(final/initial - 1):>10.2%}")
    print(f"  CAGR               : {cagr:>10.2%}")
    print(f"  Max Drawdown       : {max_dd:>10.2%}")
    print(f"  Annual Volatility  : {vol:>10.2%}")
    print(f"  Sharpe Ratio       : {sharpe:>10.2f}")
    print(f"  Annual Trades      : {annual_trades:>10.0f}")
    print("=" * 58)

    # -- Fetch benchmark --
    print("\nFetching benchmark (^CRSLDX) ...")
    b_cagr = b_dd = b_sharpe = None
    bench  = None
    try:
        braw = yf.download(CONFIG.BENCHMARK_TICKER,
                           start=eq.index[0].strftime("%Y-%m-%d"),
                           end=eq.index[-1].strftime("%Y-%m-%d"),
                           auto_adjust=True, progress=False)
        bench_s = braw["Close"].squeeze().dropna()
        bench_s.index = pd.to_datetime(bench_s.index).tz_localize(None)
        bench   = bench_s.reindex(eq.index, method="ffill")
        bench   = (bench / bench.iloc[0]) * initial
        b_final  = bench.iloc[-1]
        b_cagr   = (b_final / initial) ** (1 / years) - 1
        b_dd     = ((bench - bench.cummax()) / bench.cummax()).min()
        b_vol    = bench.pct_change().dropna().std() * np.sqrt(252)
        b_sharpe = b_cagr / b_vol if b_vol > 0 else 0
        print(f"  Benchmark CAGR     : {b_cagr:.2%}")
        print(f"  Benchmark Max DD   : {b_dd:.2%}")
        print(f"  Benchmark Sharpe   : {b_sharpe:.2f}")
    except Exception as e:
        print(f"  [WARN] {e}")

    _plot_single(eq["equity"], bench, initial,
                 title="ETF Momentum v4 -- Weekly Full-Flush | 10% TSL | BULL/PARTIAL/BEAR Regime",
                 filepath=CONFIG.CHART_FILE)

    _plot_comparison(eq["equity"], bench, initial,
                     v4_cagr=cagr, v4_dd=max_dd, v4_sharpe=sharpe,
                     b_cagr=b_cagr, b_dd=b_dd, b_sharpe=b_sharpe)

    return {
        "cagr": cagr, "max_dd": max_dd, "vol": vol, "sharpe": sharpe,
        "total_ret": final / initial - 1,
        "annual_trades": annual_trades,
        "b_cagr": b_cagr, "b_dd": b_dd, "b_sharpe": b_sharpe,
    }


# =========================================================
# CHARTS
# =========================================================
def _plot_single(eq_series: pd.Series, bench, initial: float,
                 title: str, filepath: str):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(eq_series,
            label="v4 Weekly Full-Flush (10% TSL, 3-state Regime)",
            linewidth=2, color="#E91E63")
    if bench is not None:
        ax.plot(bench, label="Benchmark (Nifty 500)", linewidth=1.5,
                alpha=0.8, color="#FF9800", linestyle="--")
    ax.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax.fill_between(eq_series.index, eq_series, initial, alpha=0.07, color="#E91E63")
    ax.set_title(title, fontsize=13, fontweight="bold", pad=14)
    ax.set_ylabel("Equity (INR)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.xticks(rotation=30)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(filepath, dpi=150)
    plt.close(fig)
    print(f"\nv4 equity curve -> {filepath}")


def _plot_comparison(eq_v4: pd.Series, bench, initial: float,
                     v4_cagr: float, v4_dd: float, v4_sharpe: float,
                     b_cagr, b_dd, b_sharpe):
    """
    3-way comparison: v1 (Monthly) vs v4 (Weekly Full-Flush) vs Nifty 500.
    Loads v1 equity series from CSV written by etf_backtest.py.
    """
    v1_eq = v1_cagr = v1_dd = v1_sharpe = None
    try:
        v1_raw  = pd.read_csv(CONFIG.V1_EQUITY_FILE, index_col=0, parse_dates=True)
        v1_eq   = v1_raw["equity"].squeeze()
        v1_eq.index = pd.to_datetime(v1_eq.index).tz_localize(None)
        v1_yrs   = (v1_eq.index[-1] - v1_eq.index[0]).days / 365.25
        v1_fin   = v1_eq.iloc[-1]
        v1_cagr  = (v1_fin / initial) ** (1 / v1_yrs) - 1
        v1_pk    = v1_eq.cummax()
        v1_dd    = ((v1_eq - v1_pk) / v1_pk).min()
        v1_vol   = v1_eq.pct_change().dropna().std() * np.sqrt(252)
        v1_sharpe = v1_cagr / v1_vol if v1_vol > 0 else 0
        print(f"\nLoaded v1 equity from {CONFIG.V1_EQUITY_FILE}")
    except Exception as e:
        print(f"  [WARN] Could not load v1 equity: {e}")

    # Common date range
    start = eq_v4.index[0]
    end   = eq_v4.index[-1]
    if v1_eq is not None:
        start = max(start, v1_eq.index[0])
        end   = min(end,   v1_eq.index[-1])

    fig, axes = plt.subplots(2, 1, figsize=(16, 12),
                             gridspec_kw={"height_ratios": [3, 1]})
    ax_eq, ax_dd = axes

    # -- Equity panel --
    v4_seg  = eq_v4.loc[start:end]
    v4_norm = (v4_seg / v4_seg.iloc[0]) * initial
    ax_eq.plot(v4_norm,
               label=f"v4 Weekly Full-Flush | CAGR {v4_cagr:.1%} | DD {v4_dd:.1%} | Sharpe {v4_sharpe:.2f}",
               linewidth=2.2, color="#E91E63", zorder=3)

    if v1_eq is not None:
        v1_seg  = v1_eq.loc[start:end]
        v1_norm = (v1_seg / v1_seg.iloc[0]) * initial
        ax_eq.plot(v1_norm,
                   label=f"v1 Monthly | CAGR {v1_cagr:.1%} | DD {v1_dd:.1%} | Sharpe {v1_sharpe:.2f}",
                   linewidth=2.2, color="#4CAF50", zorder=2)

    if bench is not None:
        b_seg  = bench.reindex(v4_seg.index, method="ffill")
        b_norm = (b_seg / b_seg.iloc[0]) * initial
        bm_lbl = (f"Nifty 500 | CAGR {b_cagr:.1%} | DD {b_dd:.1%} | Sharpe {b_sharpe:.2f}"
                  if b_cagr else "Nifty 500")
        ax_eq.plot(b_norm, label=bm_lbl,
                   linewidth=1.8, color="#FF9800", linestyle="--", alpha=0.85, zorder=1)

    ax_eq.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax_eq.set_title("ETF Momentum Strategy -- v1 (Monthly) vs v4 (Weekly Full-Flush) vs Nifty 500",
                    fontsize=14, fontweight="bold", pad=14)
    ax_eq.set_ylabel("Equity (INR)")
    ax_eq.legend(fontsize=9, loc="upper left")
    ax_eq.grid(True, alpha=0.25)
    ax_eq.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax_eq.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.setp(ax_eq.get_xticklabels(), rotation=20)

    # -- Drawdown panel --
    v4_dd_s = (v4_norm - v4_norm.cummax()) / v4_norm.cummax() * 100
    ax_dd.fill_between(v4_dd_s.index, v4_dd_s, 0,
                       alpha=0.45, color="#E91E63", label="v4 Drawdown")
    if v1_eq is not None:
        v1_dd_s = (v1_norm - v1_norm.cummax()) / v1_norm.cummax() * 100
        ax_dd.fill_between(v1_dd_s.index, v1_dd_s, 0,
                           alpha=0.35, color="#4CAF50", label="v1 Drawdown")
    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.set_xlabel("")
    ax_dd.legend(fontsize=9, loc="lower left")
    ax_dd.grid(True, alpha=0.25)
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax_dd.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.setp(ax_dd.get_xticklabels(), rotation=20)

    # -- Metrics table --
    rows = [
        ["Metric",       "v4 Weekly Full-Flush", "v1 Monthly",                   "Nifty 500"],
        ["CAGR",         f"{v4_cagr:.2%}",
         f"{v1_cagr:.2%}" if v1_cagr else "--",
         f"{b_cagr:.2%}" if b_cagr  else "--"],
        ["Max Drawdown", f"{v4_dd:.2%}",
         f"{v1_dd:.2%}"  if v1_dd   else "--",
         f"{b_dd:.2%}"   if b_dd    else "--"],
        ["Sharpe Ratio", f"{v4_sharpe:.2f}",
         f"{v1_sharpe:.2f}" if v1_sharpe else "--",
         f"{b_sharpe:.2f}"  if b_sharpe  else "--"],
    ]
    table = ax_eq.table(cellText=rows[1:], colLabels=rows[0],
                        loc="lower right", bbox=[0.57, 0.03, 0.42, 0.25])
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    for (r, c), cell in table.get_celld().items():
        if r == 0:
            cell.set_facecolor("#263238")
            cell.set_text_props(color="white", fontweight="bold")
        elif c == 1:
            cell.set_facecolor("#FCE4EC")   # pink tint for v4
        elif c == 2:
            cell.set_facecolor("#E8F5E9")   # green tint for v1

    fig.tight_layout()
    fig.savefig(CONFIG.COMPARE_CHART, dpi=150)
    plt.close(fig)
    print(f"Comparison chart -> {CONFIG.COMPARE_CHART}")


# =========================================================
# ENTRY POINT
# =========================================================
if __name__ == "__main__":
    results = run_backtest_v4()
    print(f"\n[done] v4 results: {results}")
