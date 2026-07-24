"""
ETF Momentum Backtest Engine — v2 (Weekly, Selective Exit)
===========================================================
Strategy v2 parameters (from ETF_Momentum_Strategy - v2.md):

  Scoring   : 0.4*Sharpe_1M + 0.4*Sharpe_3M - 0.2*Sharpe_6M  (MOM_ACCEL style)
              If 6M unavailable: 0.4*Sh1M + 0.4*Sh3M
              If 1M or 3M unavailable: excluded
  Screen    : NAV within 25% of 52-week high
  Regime    : BUY  (Price > EMA50)  -> up to 5 slots, fill empty positions
              HOLD (Price <= EMA50) -> no new entries; hold existing till exit triggered
  Rebalance : Weekly -- first trading day of each calendar week (Mon or next available)
  Exit rules:
    1. Daily  TSL: 5% trailing stop from running peak -> immediate exit that day
    2. Weekly rank: exit if rank > RANK_RETENTION (20) among screen-pass ETFs on Mon
    No mandatory full flush — selective exit only
  Position  : Equal weight, 1/5th of total portfolio per slot
  Sector cap: 1 ETF per sector
  Costs     : INR 20 per trade leg
  Cash rate : 2% p.a. on all idle cash
  RF rate   : 6% p.a.

Comparison:
  Loads v1 equity CSV automatically and produces a dual-strategy equity curve chart
  vs Nifty 500 (^CRSLDX) benchmark.

Usage:
  python etf_backtest_v2.py
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
    TRADE_LOG_FILE   = str(_BASE / "v2_backtest_trade_log.csv")
    EQUITY_LOG_FILE  = str(_BASE / "v2_backtest_equity.csv")
    CHART_FILE       = str(_BASE / "v2_equity_curve.png")
    COMPARE_CHART    = str(_BASE / "comparison_equity_curve.png")

    # v1 equity CSV for comparison overlay (produced by etf_backtest.py)
    V1_EQUITY_FILE   = str(_BASE / "backtest_equity.csv")

    START_CAPITAL    = 1_000_000.0   # INR 10 lakh
    CASH_INTEREST_PA = 0.02          # 2% p.a. on idle cash
    TRADE_COST_FIXED = 20.0          # INR per trade leg
    TSL_THRESHOLD    = 0.05          # 5% trailing stop loss

    TOP_N            = 5             # max simultaneous positions in BUY regime
    SECTOR_CAP       = 1             # max ETFs per sector
    RANK_RETENTION   = 20            # exit if rank > this among screen-pass ETFs

    WINDOW_6M        = 126           # 6-month lookback (trading days)
    WINDOW_3M        = 63            # 3-month lookback (trading days)
    WINDOW_1M        = 21            # 1-month lookback (trading days)
    ANNUALIZE        = 252
    DAILY_RF         = 0.06 / 252    # 6% p.a. risk-free rate
    MAX_DD_FROM_HIGH = 0.25          # 52-week high proximity screen

    TREND_EMA        = 50            # EMA50 for BUY/HOLD regime (^CRSLDX)
    BENCHMARK_TICKER = "^CRSLDX"    # Nifty 500 index via Yahoo Finance


# =========================================================
# SECTOR CLASSIFICATION (extended — same as etf_backtest.py)
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
# SCORING — v2 formula: 0.4*Sh1M + 0.4*Sh3M - 0.2*Sh6M
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
    v2 composite score.
    - Excludes ETF entirely if 1M or 3M is NaN (< ~3 months data).
    - Drops 6M term if NaN (3–6 months data). 
    - Subtracts 6M when available (momentum acceleration: recent > long-term).
    """
    if np.isnan(sh1) or np.isnan(sh3):
        return np.nan
    if np.isnan(sh6):
        return 0.4 * sh1 + 0.4 * sh3
    return 0.4 * sh1 + 0.4 * sh3 - 0.2 * sh6


# =========================================================
# DATA LOADING (same format as etf_backtest.py)
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
    """
    Returns the first available trading day of each calendar week.
    If Monday is a holiday, returns Tuesday (or next available day).
    """
    week_starts = []
    prev_key = None
    for d in all_dates:
        # isocalendar week key = (year, week_number)
        key = (d.isocalendar()[0], d.isocalendar()[1])
        if key != prev_key:
            week_starts.append(d)
            prev_key = key
    return week_starts


# =========================================================
# SCORING SNAPSHOT — rank all ETFs at a given date
# =========================================================
def rank_universe(meta: pd.DataFrame, prices: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """
    Score and rank all ETFs using price data up to (and including) `as_of`.
    Returns DataFrame with columns: TICKER, NAME, SECTOR, SCORE, RANK, CLOSE, SCREEN_PASS
    Rank is within screen-pass ETFs only; non-screen ETFs get RANK=0.
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

        # 52-week high screen
        if high_52 <= 0 or np.isnan(high_52):
            continue
        pct_from_high = (high_52 - close) / high_52
        screen_pass   = pct_from_high <= CONFIG.MAX_DD_FROM_HIGH

        sh1 = sharpe_score(s, CONFIG.WINDOW_1M)
        sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
        sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
        score = compute_wtd_sharpe(sh1, sh3, sh6)

        records.append({
            "TICKER"      : t,
            "NAME"        : row["ETF_NAME"],
            "SECTOR"      : classify_sector(row["ETF_NAME"], t),
            "CLOSE"       : close,
            "SCORE"       : score,
            "SCREEN_PASS" : screen_pass,
            "SHARPE_1M"   : sh1,
            "SHARPE_3M"   : sh3,
            "SHARPE_6M"   : sh6,
            "PCT_FROM_HIGH": pct_from_high * 100,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Rank only within screen-pass ETFs by SCORE (descending)
    sp = df[df["SCREEN_PASS"] & df["SCORE"].notna()].copy()
    if len(sp) > 0:
        sp["RANK"] = sp["SCORE"].rank(ascending=False).astype(int)
        df = df.merge(sp[["TICKER", "RANK"]], on="TICKER", how="left")
        df["RANK"] = df["RANK"].fillna(0).astype(int)
    else:
        df["RANK"] = 0

    return df.sort_values("RANK").reset_index(drop=True)


# =========================================================
# MAIN v2 BACKTEST ENGINE
# =========================================================
def run_backtest_v2() -> dict:
    """
    Full v2 backtest:
      - Weekly selective rebalance (first trading day of week)
      - Daily TSL check (5%)
      - BUY/HOLD regime based on ^CRSLDX vs EMA50
      - No full flush — positions held until TSL or rank > RANK_RETENTION
    """
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates    = prices.index

    # ── Fetch Nifty 500 (^CRSLDX) for regime filter ──
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
        # Align to trading calendar (ffill for ETF holidays)
        regime_s = regime_raw.reindex(all_dates, method="ffill")
        # Precompute EMA50 for all dates
        regime_ema50 = regime_s.ewm(span=CONFIG.TREND_EMA, adjust=False).mean()
        print(f"  Regime data: {regime_s.dropna().index[0].date()} -> "
              f"{regime_s.dropna().index[-1].date()}  ({regime_s.notna().sum()} pts)\n")
    except Exception as e:
        print(f"  [WARN] Could not fetch regime data: {e}. Defaulting to BUY.\n")
        regime_s    = None
        regime_ema50 = None

    week_starts = set(get_week_start_days(all_dates))

    # ── Portfolio state ──
    cash           = CONFIG.START_CAPITAL
    # portfolio: {ticker -> {shares, entry_price, peak, entry_date, name, sector}}
    portfolio      = {}
    equity_history = []
    trade_log      = []

    # Track previous rebalance day for scoring (use data up to prev trading day)
    date_list   = list(all_dates)
    date_index  = {d: i for i, d in enumerate(date_list)}

    print(f"Starting v2 backtest: {date_list[0].date()} -> {date_list[-1].date()}")
    print(f"TSL: {CONFIG.TSL_THRESHOLD*100:.0f}%  |  Rank retention: top {CONFIG.RANK_RETENTION}"
          f"  |  Cost: INR {CONFIG.TRADE_COST_FIXED}  |  RF: 6% p.a.\n")

    regime_label   = "BUY"          # default
    weekly_rank_df = pd.DataFrame() # cached from last Monday score run

    for d in all_dates:
        idx = date_index[d]

        # ── Accrue daily cash interest ──
        cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)

        # ── On rebalance day (first trading day of week): score + rank exits ──
        is_rebalance_day = (d in week_starts)

        if is_rebalance_day:
            # Use data up to previous trading day to avoid look-ahead
            prev_day = date_list[idx - 1] if idx > 0 else d

            # Compute regime using ^CRSLDX through prev_day
            if regime_s is not None and prev_day in regime_ema50.index:
                price_now  = regime_s.loc[prev_day]
                ema50_now  = regime_ema50.loc[prev_day]
                if pd.notna(price_now) and pd.notna(ema50_now):
                    regime_label = "BUY" if price_now > ema50_now else "HOLD"
                # else: keep previous label
            
            # Score all ETFs as of prev_day
            weekly_rank_df = rank_universe(meta, prices, prev_day)

            # ── Rank-based exit check (evaluated weekly on rebalance day) ──
            for t in list(portfolio.keys()):
                if weekly_rank_df.empty:
                    rank = 0
                else:
                    row = weekly_rank_df[weekly_rank_df["TICKER"] == t]
                    rank = int(row["RANK"].iloc[0]) if not row.empty else 0

                # rank == 0 means either screened out or no score -> treat as > RANK_RETENTION
                if rank == 0 or rank > CONFIG.RANK_RETENTION:
                    pos     = portfolio[t]
                    p_exit  = prices.loc[d, t] if t in prices.columns and not pd.isna(prices.loc[d, t]) else pos["entry_price"]
                    proceeds = pos["shares"] * p_exit
                    cost     = CONFIG.TRADE_COST_FIXED
                    pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
                    cash    += proceeds - cost
                    trade_log.append({
                        "TYPE"        : "SELL",
                        "REASON"      : f"RANK_EXIT (rank={rank})",
                        "TICKER"      : t,
                        "NAME"        : pos.get("name", ""),
                        "ENTRY_DATE"  : pos["entry_date"],
                        "EXIT_DATE"   : d,
                        "HOLDING_DAYS": (d - pos["entry_date"]).days,
                        "ENTRY_PRICE" : round(pos["entry_price"], 4),
                        "EXIT_PRICE"  : round(p_exit, 4),
                        "SHARES"      : round(pos["shares"], 4),
                        "GROSS_PNL"   : round(proceeds - pos["shares"] * pos["entry_price"], 2),
                        "COSTS"       : cost,
                        "NET_PNL"     : round(pnl, 2),
                        "REGIME"      : regime_label,
                    })
                    del portfolio[t]

            # ── Buy: fill empty slots (only in BUY regime) ──
            if regime_label == "BUY" and len(portfolio) < CONFIG.TOP_N and not weekly_rank_df.empty:
                # slot size = equal weight on full TOP_N allocation
                slot_size = (cash + _portfolio_value(portfolio, prices, d)) / CONFIG.TOP_N

                sector_count = _current_sector_counts(portfolio)
                candidates   = weekly_rank_df[
                    weekly_rank_df["SCREEN_PASS"] &
                    weekly_rank_df["SCORE"].notna() &
                    (weekly_rank_df["RANK"] > 0)
                ].sort_values("RANK")

                for _, r in candidates.iterrows():
                    if len(portfolio) >= CONFIG.TOP_N:
                        break
                    t      = r["TICKER"]
                    sector = r["SECTOR"]
                    if t in portfolio:
                        continue  # already holding
                    if sector_count.get(sector, 0) >= CONFIG.SECTOR_CAP:
                        continue  # sector cap reached

                    p_entry = prices.loc[d, t] if t in prices.columns else np.nan
                    if pd.isna(p_entry) or p_entry <= 0:
                        continue

                    shares = slot_size / p_entry
                    portfolio[t] = {
                        "shares"      : shares,
                        "entry_price" : p_entry,
                        "peak"        : p_entry,
                        "entry_date"  : d,
                        "name"        : r["NAME"],
                        "sector"      : sector,
                    }
                    sector_count[sector] = sector_count.get(sector, 0) + 1
                    cash -= (slot_size + CONFIG.TRADE_COST_FIXED)

                    trade_log.append({
                        "TYPE"        : "BUY",
                        "REASON"      : f"WEEKLY_ENTRY (rank={r['RANK']})",
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

        # ── Daily TSL check ──
        for t in list(portfolio.keys()):
            if t not in prices.columns:
                continue
            p_curr = prices.loc[d, t]
            if pd.isna(p_curr):
                continue

            pos = portfolio[t]
            # Update running peak
            if p_curr > pos["peak"]:
                pos["peak"] = p_curr

            drawdown = (pos["peak"] - p_curr) / pos["peak"]
            if drawdown >= CONFIG.TSL_THRESHOLD:
                proceeds = pos["shares"] * p_curr
                cost     = CONFIG.TRADE_COST_FIXED
                pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
                cash    += proceeds - cost
                trade_log.append({
                    "TYPE"        : "SELL",
                    "REASON"      : f"TSL_HIT ({drawdown*100:.1f}%)",
                    "TICKER"      : t,
                    "NAME"        : pos.get("name", ""),
                    "ENTRY_DATE"  : pos["entry_date"],
                    "EXIT_DATE"   : d,
                    "HOLDING_DAYS": (d - pos["entry_date"]).days,
                    "ENTRY_PRICE" : round(pos["entry_price"], 4),
                    "EXIT_PRICE"  : round(p_curr, 4),
                    "SHARES"      : round(pos["shares"], 4),
                    "GROSS_PNL"   : round(proceeds - pos["shares"] * pos["entry_price"], 2),
                    "COSTS"       : cost,
                    "NET_PNL"     : round(pnl, 2),
                    "REGIME"      : regime_label,
                })
                del portfolio[t]

        # ── Record daily equity ──
        port_val = _portfolio_value(portfolio, prices, d)
        equity_history.append({"date": d, "equity": cash + port_val, "regime": regime_label})

    # =========================================================
    # RESULTS & METRICS
    # =========================================================
    eq = pd.DataFrame(equity_history).set_index("date")
    eq.to_csv(CONFIG.EQUITY_LOG_FILE)

    tlog = pd.DataFrame(trade_log)
    tlog.to_csv(CONFIG.TRADE_LOG_FILE, index=False)

    sells     = tlog[tlog["TYPE"] == "SELL"] if len(tlog) else pd.DataFrame()
    tsl_hits  = sells[sells["REASON"].str.startswith("TSL")] if len(sells) else pd.DataFrame()
    rank_exits = sells[sells["REASON"].str.startswith("RANK")] if len(sells) else pd.DataFrame()
    buys      = tlog[tlog["TYPE"] == "BUY"] if len(tlog) else pd.DataFrame()

    print(f"Trade Log -> {CONFIG.TRADE_LOG_FILE}")
    print(f"  Total BUYs         : {len(buys)}")
    print(f"  Total SELLs        : {len(sells)}")
    print(f"  TSL exits          : {len(tsl_hits)}")
    print(f"  Rank-based exits   : {len(rank_exits)}")
    if len(sells) > 0:
        wins = (sells["NET_PNL"] > 0).sum()
        print(f"  Win Rate (sells)   : {wins/len(sells):.1%}")
        print(f"  Avg Net P&L/trade  : INR {sells['NET_PNL'].mean():,.0f}")

    years    = (eq.index[-1] - eq.index[0]).days / 365.25
    initial  = CONFIG.START_CAPITAL
    final    = eq["equity"].iloc[-1]
    cagr     = (final / initial) ** (1 / years) - 1
    eq["peak"]     = eq["equity"].cummax()
    eq["drawdown"] = (eq["equity"] - eq["peak"]) / eq["peak"]
    max_dd   = eq["drawdown"].min()
    daily_ret = eq["equity"].pct_change().dropna()
    vol      = daily_ret.std() * np.sqrt(252)
    sharpe   = cagr / vol if vol > 0 else 0

    # Approx annualised turnover
    annual_trades = len(buys) / years

    print("\n" + "=" * 52)
    print(f"  v2 BACKTEST RESULTS  (5% TSL | BUY/HOLD | Weekly)")
    print("=" * 52)
    print(f"  Period           : {eq.index[0].date()} -> {eq.index[-1].date()}")
    print(f"  Start Capital    : INR {initial:>12,.0f}")
    print(f"  End Capital      : INR {final:>12,.0f}")
    print(f"  Total Return     : {(final/initial - 1):>10.2%}")
    print(f"  CAGR             : {cagr:>10.2%}")
    print(f"  Max Drawdown     : {max_dd:>10.2%}")
    print(f"  Annual Volatility: {vol:>10.2%}")
    print(f"  Sharpe Ratio     : {sharpe:>10.2f}")
    print(f"  Annual Trades    : {annual_trades:>10.0f}")
    print("=" * 52)

    # ── Fetch benchmark ──
    print("\nFetching benchmark (^CRSLDX) ...")
    b_cagr = b_dd = b_sharpe = None
    bench   = None
    try:
        braw  = yf.download(CONFIG.BENCHMARK_TICKER,
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
        print(f"  Benchmark CAGR   : {b_cagr:.2%}")
        print(f"  Benchmark Max DD : {b_dd:.2%}")
        print(f"  Benchmark Sharpe : {b_sharpe:.2f}")
    except Exception as e:
        print(f"  [WARN] {e}")

    # ── Single strategy chart ──
    _plot_single(eq["equity"], bench, initial,
                 title="ETF Momentum v2 — Weekly | 5% TSL | BUY/HOLD Regime",
                 filepath=CONFIG.CHART_FILE)

    # ── Comparison chart vs v1 ──
    _plot_comparison(eq["equity"], bench, initial,
                     years=years,
                     v2_cagr=cagr, v2_dd=max_dd, v2_sharpe=sharpe,
                     b_cagr=b_cagr, b_dd=b_dd, b_sharpe=b_sharpe)

    return {
        "cagr": cagr, "max_dd": max_dd, "vol": vol, "sharpe": sharpe,
        "total_ret": final / initial - 1,
        "annual_trades": annual_trades,
        "b_cagr": b_cagr, "b_dd": b_dd, "b_sharpe": b_sharpe,
    }


# =========================================================
# HELPERS
# =========================================================
def _portfolio_value(portfolio: dict, prices: pd.DataFrame, d: pd.Timestamp) -> float:
    """Calculate current market value of all open positions."""
    total = 0.0
    for t, pos in portfolio.items():
        if t in prices.columns:
            p = prices.loc[d, t]
            if not pd.isna(p):
                total += pos["shares"] * p
    return total


def _current_sector_counts(portfolio: dict) -> dict:
    """Return sector -> count mapping for currently held positions."""
    counts = {}
    for pos in portfolio.values():
        sector = pos.get("sector", "OTHER")
        counts[sector] = counts.get(sector, 0) + 1
    return counts


# =========================================================
# CHARTS
# =========================================================
def _plot_single(eq_series: pd.Series, bench, initial: float,
                 title: str, filepath: str):
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(eq_series, label="Strategy v2 (Weekly, 5% TSL)", linewidth=2, color="#2196F3")
    if bench is not None:
        ax.plot(bench, label="Benchmark (Nifty 500)", linewidth=1.5,
                alpha=0.8, color="#FF9800", linestyle="--")
    ax.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax.fill_between(eq_series.index, eq_series, initial, alpha=0.06, color="#2196F3")
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
    print(f"\nv2 equity curve -> {filepath}")


def _plot_comparison(eq_v2: pd.Series, bench, initial: float,
                     years: float,
                     v2_cagr: float, v2_dd: float, v2_sharpe: float,
                     b_cagr, b_dd, b_sharpe):
    """
    Overlay chart: v1 (monthly) vs v2 (weekly) vs Nifty 500.
    Loads v1 equity series from the CSV written by etf_backtest.py.
    """
    # Load v1 equity
    v1_eq = None
    v1_label = "v1 (Monthly, 10% TSL)"
    v1_cagr = v1_dd = v1_sharpe = None
    try:
        v1_raw  = pd.read_csv(CONFIG.V1_EQUITY_FILE, index_col=0, parse_dates=True)
        v1_eq   = v1_raw["equity"].squeeze()
        v1_eq.index = pd.to_datetime(v1_eq.index).tz_localize(None)
        # Compute v1 metrics
        v1_yrs   = (v1_eq.index[-1] - v1_eq.index[0]).days / 365.25
        v1_fin   = v1_eq.iloc[-1]
        v1_cagr  = (v1_fin / initial) ** (1 / v1_yrs) - 1
        v1_pk    = v1_eq.cummax()
        v1_dd    = ((v1_eq - v1_pk) / v1_pk).min()
        v1_vol   = v1_eq.pct_change().dropna().std() * np.sqrt(252)
        v1_sharpe = v1_cagr / v1_vol if v1_vol > 0 else 0
        print(f"\nLoaded v1 equity from {CONFIG.V1_EQUITY_FILE}")
    except Exception as e:
        print(f"  [WARN] Could not load v1 equity for comparison: {e}")

    # Find common date range for alignment
    start = eq_v2.index[0]
    end   = eq_v2.index[-1]
    if v1_eq is not None:
        start = max(start, v1_eq.index[0])
        end   = min(end, v1_eq.index[-1])

    # Normalise to 100 at common start for clean visual comparison
    fig, axes = plt.subplots(2, 1, figsize=(16, 12),
                             gridspec_kw={"height_ratios": [3, 1]})
    ax_eq, ax_dd = axes

    # --- Equity panel ---
    v2_aligned = eq_v2.reindex(eq_v2.loc[start:end].index, method="ffill")
    v2_norm    = (v2_aligned / v2_aligned.iloc[0]) * initial
    ax_eq.plot(v2_norm, label=f"v2 Weekly | CAGR {v2_cagr:.1%} | DD {v2_dd:.1%} | Sharpe {v2_sharpe:.2f}",
               linewidth=2.2, color="#2196F3", zorder=3)

    if v1_eq is not None:
        v1_aligned = v1_eq.loc[start:end]
        v1_norm    = (v1_aligned / v1_aligned.iloc[0]) * initial
        ax_eq.plot(v1_norm,
                   label=f"v1 Monthly | CAGR {v1_cagr:.1%} | DD {v1_dd:.1%} | Sharpe {v1_sharpe:.2f}",
                   linewidth=2.2, color="#4CAF50", zorder=2)

    if bench is not None:
        bench_aligned = bench.reindex(eq_v2.loc[start:end].index, method="ffill")
        bench_norm    = (bench_aligned / bench_aligned.iloc[0]) * initial
        bm_label = (f"Nifty 500 | CAGR {b_cagr:.1%} | DD {b_dd:.1%} | Sharpe {b_sharpe:.2f}"
                    if b_cagr else "Nifty 500")
        ax_eq.plot(bench_norm, label=bm_label,
                   linewidth=1.8, color="#FF9800", linestyle="--", alpha=0.85, zorder=1)

    ax_eq.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax_eq.set_title("ETF Momentum Strategy — v1 (Monthly) vs v2 (Weekly) vs Nifty 500",
                    fontsize=14, fontweight="bold", pad=14)
    ax_eq.set_ylabel("Equity (INR)")
    ax_eq.legend(fontsize=9, loc="upper left")
    ax_eq.grid(True, alpha=0.25)
    ax_eq.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax_eq.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.setp(ax_eq.get_xticklabels(), rotation=20)

    # --- Drawdown panel ---
    v2_dd_series = (v2_norm - v2_norm.cummax()) / v2_norm.cummax() * 100
    ax_dd.fill_between(v2_dd_series.index, v2_dd_series, 0,
                       alpha=0.45, color="#2196F3", label="v2 Drawdown")
    if v1_eq is not None:
        v1_dd_series = (v1_norm - v1_norm.cummax()) / v1_norm.cummax() * 100
        ax_dd.fill_between(v1_dd_series.index, v1_dd_series, 0,
                           alpha=0.35, color="#4CAF50", label="v1 Drawdown")
    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.set_xlabel("")
    ax_dd.legend(fontsize=9, loc="lower left")
    ax_dd.grid(True, alpha=0.25)
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax_dd.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.setp(ax_dd.get_xticklabels(), rotation=20)

    # --- Metrics table annotation ---
    rows = [
        ["Metric",          "v2 Weekly",           "v1 Monthly",
         "Nifty 500"],
        ["CAGR",            f"{v2_cagr:.2%}",
         f"{v1_cagr:.2%}" if v1_cagr else "—",
         f"{b_cagr:.2%}"  if b_cagr  else "—"],
        ["Max Drawdown",    f"{v2_dd:.2%}",
         f"{v1_dd:.2%}"  if v1_dd  else "—",
         f"{b_dd:.2%}"   if b_dd   else "—"],
        ["Sharpe Ratio",    f"{v2_sharpe:.2f}",
         f"{v1_sharpe:.2f}" if v1_sharpe else "—",
         f"{b_sharpe:.2f}"  if b_sharpe  else "—"],
    ]
    table = ax_eq.table(cellText=rows[1:], colLabels=rows[0],
                        loc="lower right", bbox=[0.60, 0.03, 0.39, 0.25])
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    for (r, c), cell in table.get_celld().items():
        if r == 0:
            cell.set_facecolor("#263238")
            cell.set_text_props(color="white", fontweight="bold")
        elif c == 1:
            cell.set_facecolor("#E3F2FD")
        elif c == 2:
            cell.set_facecolor("#E8F5E9")

    fig.tight_layout()
    fig.savefig(CONFIG.COMPARE_CHART, dpi=150)
    plt.close(fig)
    print(f"Comparison chart -> {CONFIG.COMPARE_CHART}")


# =========================================================
# ENTRY POINT
# =========================================================
if __name__ == "__main__":
    results = run_backtest_v2()
    print(f"\n[done] v2 results: {results}")
