"""
ETF Momentum Backtest — Hold Winners Variant
=============================================
Same regime / scoring / screen as Run 1, but with a fundamentally
different rebalancing philosophy:

  - Check every MONDAY (not monthly)
  - Only SELL positions that drop out of the Top-N ranking
  - HOLD positions that remain in the Top-N (no unnecessary sell+buy)
  - New entries sized at 1/5th of current total portfolio value
  - Daily 10% TSL still applies

This tests whether letting winners ride (instead of monthly full-reset)
improves risk-adjusted returns.
"""

import pandas as pd
import numpy as np
from scipy import stats as spstats
from pathlib import Path
import matplotlib.pyplot as plt
from datetime import datetime
import yfinance as yf

OUT_DIR = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\run_2026-04-27_hold_winners"

# =========================================================
# CONFIG
# =========================================================
class CONFIG:
    INPUT_FILE       = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\ETF - Backtest  - Copy.xlsx"
    TRADE_LOG_FILE   = OUT_DIR + r"\backtest_trade_log.csv"
    EQUITY_LOG_FILE  = OUT_DIR + r"\backtest_equity.csv"
    CHART_FILE       = OUT_DIR + r"\equity_curve.png"

    START_CAPITAL    = 1_000_000.0
    CASH_INTEREST_PA = 0.02          # 2% p.a. on idle cash
    TRADE_COST_FIXED = 20.0          # INR per trade leg
    TSL_THRESHOLD    = 0.10          # 10% trailing stop loss

    TOP_N            = 5             # slots in BULL
    TOP_N_PARTIAL    = 3             # slots in PARTIAL
    SECTOR_CAP       = 1             # max ETFs per sector

    WINDOW_6M        = 126
    WINDOW_3M        = 63
    ANNUALIZE        = 252
    DAILY_RF         = 0.07 / 252
    MAX_DD_FROM_HIGH = 0.25          # 52-week high screen

    TREND_FAST_EMA   = 50
    TREND_SLOW_EMA   = 100
    BENCHMARK_TICKER = "^CRSLDX"     # Nifty 500 index


# =========================================================
# SECTOR CLASSIFICATION  (identical to Run 1)
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
# SCORING  (identical to Run 1)
# =========================================================
def sharpe_score(series: pd.Series, window: int) -> float:
    clean = series.dropna()
    if len(clean) < window + 1:
        return np.nan
    log_ret = np.log(clean.iloc[-window-1:] / clean.iloc[-window-1:].shift(1)).dropna()
    excess  = log_ret - CONFIG.DAILY_RF
    return (excess.mean() / excess.std()) * np.sqrt(CONFIG.ANNUALIZE) if excess.std() > 0 else np.nan


def r2_score(series: pd.Series, window: int) -> float:
    clean = series.dropna()
    if len(clean) < window:
        return np.nan
    y = np.log(clean.iloc[-window:].values.astype(float))
    x = np.arange(len(y))
    _, _, r, _, _ = spstats.linregress(x, y)
    return r ** 2


# =========================================================
# DATA LOADING  (identical to Run 1)
# =========================================================
def load_data(filepath: str):
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
    return meta, prices


# =========================================================
# HELPER: sell a position
# =========================================================
def _sell(portfolio, t, p_exit, date, reason, regime_label, trade_log):
    pos      = portfolio[t]
    proceeds = pos["shares"] * p_exit
    cost     = CONFIG.TRADE_COST_FIXED
    pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
    trade_log.append({
        "TYPE"        : "SELL",
        "REASON"      : reason,
        "TICKER"      : t,
        "NAME"        : pos.get("name", ""),
        "ENTRY_DATE"  : pos["entry_date"],
        "EXIT_DATE"   : date,
        "HOLDING_DAYS": (date - pos["entry_date"]).days,
        "ENTRY_PRICE" : round(pos["entry_price"], 4),
        "EXIT_PRICE"  : round(p_exit, 4),
        "SHARES"      : round(pos["shares"], 4),
        "GROSS_PNL"   : round(proceeds - pos["shares"] * pos["entry_price"], 2),
        "COSTS"       : cost,
        "NET_PNL"     : round(pnl, 2),
        "REGIME"      : regime_label,
    })
    del portfolio[t]
    return proceeds - cost


# =========================================================
# BACKTEST ENGINE — HOLD WINNERS
# =========================================================
def run_backtest() -> dict:
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates = prices.index

    # Fetch real Nifty 500 index for regime filter
    print("Fetching Nifty 500 for regime filter (^CRSLDX) ...")
    regime_series = None
    try:
        reg_raw = yf.download(
            CONFIG.BENCHMARK_TICKER,
            start=all_dates[0].strftime("%Y-%m-%d"),
            end=(all_dates[-1] + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            auto_adjust=True, progress=False,
        )
        regime_series = reg_raw["Close"].squeeze().dropna()
        regime_series.index = pd.to_datetime(regime_series.index).tz_localize(None)
        regime_series = regime_series.reindex(all_dates, method="ffill")
        print(f"  Regime data: {regime_series.dropna().index[0].date()} -> "
              f"{regime_series.dropna().index[-1].date()}  ({regime_series.notna().sum()} pts)\n")
    except Exception as e:
        print(f"  [WARN] Could not fetch regime data: {e}. Defaulting to BULL.")

    # All Mondays in the date range
    mondays = [d for d in all_dates if d.weekday() == 0]

    cash           = CONFIG.START_CAPITAL
    portfolio      = {}   # ticker -> {shares, entry_price, peak, entry_date, name}
    trade_log      = []
    equity_history = []

    print(f"Starting backtest: {all_dates[0].date()} -> {all_dates[-1].date()}")
    print(f"Strategy: HOLD WINNERS | Weekly Monday check | 10% TSL | "
          f"Equal wt (1/{CONFIG.TOP_N} portfolio)\n")

    for idx, monday in enumerate(mondays):

        # Use the previous trading day's close (no look-ahead)
        prev_dates = all_dates[all_dates < monday]
        if len(prev_dates) == 0:
            continue
        prev_day = prev_dates[-1]

        hist_prices = prices.loc[:prev_day]
        if len(hist_prices) < CONFIG.WINDOW_6M:
            continue

        # ── Regime ────────────────────────────────────────────
        regime_slots = CONFIG.TOP_N
        regime_label = "BULL"
        if regime_series is not None:
            s_reg = regime_series.loc[:prev_day].dropna()
            if len(s_reg) >= CONFIG.TREND_SLOW_EMA:
                p_now  = float(s_reg.iloc[-1])
                ema50  = float(s_reg.ewm(span=CONFIG.TREND_FAST_EMA, adjust=False).mean().iloc[-1])
                ema100 = float(s_reg.ewm(span=CONFIG.TREND_SLOW_EMA, adjust=False).mean().iloc[-1])
                if ema50 > ema100 and p_now > ema50:
                    regime_slots = CONFIG.TOP_N;         regime_label = "BULL"
                elif p_now > ema100:
                    regime_slots = CONFIG.TOP_N_PARTIAL;  regime_label = "PARTIAL"
                else:
                    regime_slots = 0;                     regime_label = "BEAR"

        # ── BEAR: sell everything, skip ranking ───────────────
        if regime_slots == 0:
            for t in list(portfolio.keys()):
                p_exit = prices.loc[monday, t] if t in prices.columns else portfolio[t]["entry_price"]
                if pd.isna(p_exit):
                    p_exit = portfolio[t]["entry_price"]
                cash += _sell(portfolio, t, p_exit, monday, "REGIME_BEAR", regime_label, trade_log)
            # Track equity for the week
            next_mon = mondays[idx+1] if idx+1 < len(mondays) else all_dates[-1] + pd.Timedelta(days=1)
            week_dates = all_dates[(all_dates >= monday) & (all_dates < next_mon)]
            for d in week_dates:
                cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)
                equity_history.append({"date": d, "equity": cash})
            continue

        # ── Score & rank ETFs ─────────────────────────────────
        rank_list = []
        for _, m_row in meta.iterrows():
            t = m_row["TICKER"]
            if t not in hist_prices.columns:
                continue
            s = hist_prices[t].dropna()
            if len(s) < CONFIG.WINDOW_3M:
                continue
            sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
            sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
            _sh6 = 0.0 if pd.isna(sh6) else sh6
            _sh3 = 0.0 if pd.isna(sh3) else sh3
            w_sharpe = 0.5 * _sh6 + 0.5 * _sh3
            high_52 = s.tail(252).max()
            close_p = s.iloc[-1]
            if pd.isna(high_52) or high_52 <= 0:
                continue
            if (high_52 - close_p) / high_52 <= CONFIG.MAX_DD_FROM_HIGH:
                rank_list.append({
                    "TICKER"    : t,
                    "NAME"      : m_row["ETF_NAME"],
                    "WT_SHARPE" : w_sharpe,
                    "SECTOR"    : classify_sector(m_row["ETF_NAME"], t),
                })

        if not rank_list:
            next_mon = mondays[idx+1] if idx+1 < len(mondays) else all_dates[-1] + pd.Timedelta(days=1)
            week_dates = all_dates[(all_dates >= monday) & (all_dates < next_mon)]
            for d in week_dates:
                cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)
                equity_history.append({"date": d, "equity": cash})
            continue

        rank_df = pd.DataFrame(rank_list).sort_values("WT_SHARPE", ascending=False)

        # Sector-capped Top-N selection
        selected = []
        sec_counts: dict = {}
        for _, r in rank_df.iterrows():
            sec = r["SECTOR"]
            if sec_counts.get(sec, 0) < CONFIG.SECTOR_CAP:
                selected.append(r)
                sec_counts[sec] = sec_counts.get(sec, 0) + 1
            if len(selected) >= regime_slots:
                break

        new_top_n = {r["TICKER"]: r for r in selected}

        # ── SELL: positions NOT in the new Top-N ──────────────
        held_tickers = set(portfolio.keys())
        to_sell = held_tickers - set(new_top_n.keys())
        for t in to_sell:
            p_exit = prices.loc[monday, t] if t in prices.columns else portfolio[t]["entry_price"]
            if pd.isna(p_exit):
                p_exit = portfolio[t]["entry_price"]
            cash += _sell(portfolio, t, p_exit, monday, "RANK_DROP", regime_label, trade_log)

        # ── If regime reduced slots (BULL->PARTIAL), trim excess ──
        # We may still hold positions that ARE in top-N but exceed slot count
        while len(portfolio) > regime_slots:
            # Sell the held position with the lowest WT_SHARPE in the new ranking
            worst_t = None
            worst_score = float("inf")
            for t in portfolio:
                score = new_top_n[t]["WT_SHARPE"] if t in new_top_n else -999
                if score < worst_score:
                    worst_score = score
                    worst_t = t
            if worst_t:
                p_exit = prices.loc[monday, worst_t] if worst_t in prices.columns else portfolio[worst_t]["entry_price"]
                if pd.isna(p_exit):
                    p_exit = portfolio[worst_t]["entry_price"]
                cash += _sell(portfolio, worst_t, p_exit, monday, "REGIME_TRIM", regime_label, trade_log)

        # ── BUY: new entries to fill empty slots ──────────────
        empty_slots = regime_slots - len(portfolio)
        if empty_slots > 0:
            # Compute total portfolio equity for sizing
            port_val = sum(
                pos["shares"] * (prices.loc[monday, t] if t in prices.columns and not pd.isna(prices.loc[monday, t]) else pos["entry_price"])
                for t, pos in portfolio.items()
            )
            total_equity = cash + port_val
            slot_size = total_equity / CONFIG.TOP_N   # always 1/5th of portfolio

            # Buy ETFs from new Top-N that we don't already hold
            to_buy = [t for t in new_top_n if t not in portfolio]
            bought = 0
            for t in to_buy:
                if bought >= empty_slots:
                    break
                p_entry = prices.loc[monday, t] if t in prices.columns else np.nan
                if pd.isna(p_entry):
                    continue
                shares = slot_size / p_entry
                portfolio[t] = {
                    "shares"     : shares,
                    "entry_price": p_entry,
                    "peak"       : p_entry,
                    "entry_date" : monday,
                    "name"       : new_top_n[t]["NAME"],
                }
                cash -= (slot_size + CONFIG.TRADE_COST_FIXED)
                trade_log.append({
                    "TYPE"        : "BUY",
                    "REASON"      : "NEW_ENTRY",
                    "TICKER"      : t,
                    "NAME"        : new_top_n[t]["NAME"],
                    "ENTRY_DATE"  : monday,
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
                bought += 1

        # ── DAILY monitoring: TSL + cash interest ─────────────
        next_mon = mondays[idx+1] if idx+1 < len(mondays) else all_dates[-1] + pd.Timedelta(days=1)
        week_dates = all_dates[(all_dates >= monday) & (all_dates < next_mon)]
        for d in week_dates:
            cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)
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
                    cash += _sell(portfolio, t, p_curr, d,
                                 f"TSL_HIT ({drawdown*100:.1f}%)",
                                 regime_label, trade_log)

            port_val = sum(
                pos["shares"] * prices.loc[d, t]
                for t, pos in portfolio.items()
                if t in prices.columns and not pd.isna(prices.loc[d, t])
            )
            equity_history.append({"date": d, "equity": cash + port_val})

    # =========================================================
    # RESULTS
    # =========================================================
    res = pd.DataFrame(equity_history).set_index("date")
    res.to_csv(CONFIG.EQUITY_LOG_FILE)

    tlog  = pd.DataFrame(trade_log)
    tlog.to_csv(CONFIG.TRADE_LOG_FILE, index=False)
    sells     = tlog[tlog["TYPE"] == "SELL"] if len(tlog) else pd.DataFrame()
    tsl_hits  = sells[sells["REASON"].str.startswith("TSL")] if len(sells) else pd.DataFrame()
    rank_drops = sells[sells["REASON"] == "RANK_DROP"] if len(sells) else pd.DataFrame()
    regime_sells = sells[sells["REASON"].str.startswith("REGIME")] if len(sells) else pd.DataFrame()

    print(f"\nTrade Log -> {CONFIG.TRADE_LOG_FILE}")
    print(f"  Total trades  : {len(tlog)}")
    print(f"  Total buys    : {len(tlog[tlog['TYPE']=='BUY'])}" if len(tlog) else "")
    print(f"  Rank drops    : {len(rank_drops)}")
    print(f"  Regime sells  : {len(regime_sells)}")
    print(f"  TSL hits      : {len(tsl_hits)}")
    if len(sells) > 0:
        print(f"  Avg Net P&L   : INR {sells['NET_PNL'].mean():,.0f}")
        print(f"  Win Rate      : {(sells['NET_PNL'] > 0).mean():.1%}")
        print(f"  Avg Hold Days : {sells['HOLDING_DAYS'].mean():.0f}")

    initial   = CONFIG.START_CAPITAL
    final     = res.iloc[-1]["equity"]
    years     = (res.index[-1] - res.index[0]).days / 365.25
    cagr      = (final / initial) ** (1 / years) - 1
    res["peak"]     = res["equity"].cummax()
    res["drawdown"] = (res["equity"] - res["peak"]) / res["peak"]
    max_dd    = res["drawdown"].min()
    daily_ret = res["equity"].pct_change().dropna()
    vol       = daily_ret.std() * np.sqrt(252)
    sharpe    = cagr / vol if vol > 0 else 0

    print("\n" + "="*55)
    print(f"  BACKTEST: HOLD WINNERS  |  10% TSL  |  Weekly Monday")
    print("="*55)
    print(f"  Period       : {res.index[0].date()} -> {res.index[-1].date()}")
    print(f"  Start Capital: INR {initial:>12,.0f}")
    print(f"  End Capital  : INR {final:>12,.0f}")
    print(f"  Total Return : {(final/initial - 1):>10.2%}")
    print(f"  CAGR         : {cagr:>10.2%}")
    print(f"  Max Drawdown : {max_dd:>10.2%}")
    print(f"  Annual Vol   : {vol:>10.2%}")
    print(f"  Sharpe Ratio : {sharpe:>10.2f}")
    print("="*55)

    # Benchmark
    print("\nFetching benchmark (^CRSLDX / Nifty 500) ...")
    bench_ok = False
    b_cagr = b_dd = b_sharpe = None
    bench = None
    try:
        braw  = yf.download(CONFIG.BENCHMARK_TICKER,
                            start=res.index[0].strftime("%Y-%m-%d"),
                            end=res.index[-1].strftime("%Y-%m-%d"),
                            auto_adjust=True, progress=False)
        bench = braw["Close"].squeeze().dropna()
        bench.index = pd.to_datetime(bench.index).tz_localize(None)
        bench = bench.reindex(res.index, method="ffill")
        bench = (bench / bench.iloc[0]) * initial
        b_final  = bench.iloc[-1]
        b_cagr   = (b_final / initial) ** (1 / years) - 1
        b_ret    = bench.pct_change().dropna()
        b_dd     = ((bench - bench.cummax()) / bench.cummax()).min()
        b_vol    = b_ret.std() * np.sqrt(252)
        b_sharpe = b_cagr / b_vol if b_vol > 0 else 0
        print(f"  Benchmark CAGR  : {b_cagr:.2%}")
        print(f"  Benchmark Max DD: {b_dd:.2%}")
        print(f"  Benchmark Sharpe: {b_sharpe:.2f}")
        bench_ok = True
    except Exception as e:
        print(f"  [WARN] {e}")

    # Chart
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(res["equity"], label="Hold Winners (Weekly Mon, 10% TSL)",
            linewidth=2, color="seagreen")
    if bench_ok:
        ax.plot(bench, label="Benchmark (Nifty 500)", linewidth=1.5,
                alpha=0.75, color="darkorange", linestyle="--")
    ax.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax.fill_between(res.index, res["equity"], initial, alpha=0.07, color="seagreen")
    ax.set_title("ETF Momentum — Hold Winners | Weekly Monday Check | 10% TSL | Run 1 Regime")
    ax.set_ylabel("Equity (INR)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(CONFIG.CHART_FILE, dpi=150)
    plt.close(fig)
    print(f"\nEquity curve -> {CONFIG.CHART_FILE}")

    return {
        "cagr": cagr, "max_dd": max_dd, "vol": vol, "sharpe": sharpe,
        "total_ret": final / initial - 1,
        "b_cagr": b_cagr, "b_dd": b_dd, "b_sharpe": b_sharpe,
    }


if __name__ == "__main__":
    run_backtest()
