"""
ETF Momentum Backtest — Run 1 + TSL Replacement
=================================================
Identical to Run 1 (monthly full-reset, 3-state regime, 10% daily TSL)
with ONE modification:

  When a TSL hit occurs mid-month, immediately replace the exited
  position with the next highest-ranked ETF from the SAME month's
  rankings (computed at month start). Replacement uses the TSL sale
  proceeds for sizing and must respect the sector cap.

  If the replacement also hits TSL, cascade: replace again from the
  same ranked list. At the next monthly rebalance, everything resets
  from scratch as usual.
"""

import pandas as pd
import numpy as np
from scipy import stats as spstats
from pathlib import Path
import matplotlib.pyplot as plt
from datetime import datetime
import yfinance as yf

OUT_DIR = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\run_2026-04-27_tsl_replace"

# =========================================================
# CONFIG
# =========================================================
class CONFIG:
    INPUT_FILE       = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\ETF - Backtest  - Copy.xlsx"
    TRADE_LOG_FILE   = OUT_DIR + r"\backtest_trade_log.csv"
    EQUITY_LOG_FILE  = OUT_DIR + r"\backtest_equity.csv"
    CHART_FILE       = OUT_DIR + r"\equity_curve.png"

    START_CAPITAL    = 1_000_000.0
    CASH_INTEREST_PA = 0.02
    TRADE_COST_FIXED = 20.0
    TSL_THRESHOLD    = 0.10

    TOP_N            = 5
    TOP_N_PARTIAL    = 3
    SECTOR_CAP       = 1

    WINDOW_6M        = 126
    WINDOW_3M        = 63
    ANNUALIZE        = 252
    DAILY_RF         = 0.07 / 252
    MAX_DD_FROM_HIGH = 0.25

    TREND_FAST_EMA   = 50
    TREND_SLOW_EMA   = 100
    BENCHMARK_TICKER = "^CRSLDX"


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
# SCORING
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
# DATA LOADING
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
# BACKTEST ENGINE — Run 1 + TSL Replacement
# =========================================================
def run_backtest(ranking_metric: str = "WT_SHARPE") -> dict:
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates = prices.index

    # Fetch regime data
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

    month_groups = all_dates.to_series().groupby(all_dates.to_period("M"))
    first_days   = month_groups.min().tolist()
    last_days    = month_groups.max().tolist()

    cash           = CONFIG.START_CAPITAL
    portfolio      = {}
    equity_history = []
    trade_log      = []
    tsl_replacements = 0

    print(f"Starting backtest: {first_days[0].date()} -> {last_days[-1].date()}")
    print(f"TSL: {CONFIG.TSL_THRESHOLD*100:.0f}%  |  Cash rate: {CONFIG.CASH_INTEREST_PA*100:.0f}% pa  "
          f"|  Cost: INR {CONFIG.TRADE_COST_FIXED}  |  Rank: {ranking_metric}")
    print(f"TSL REPLACEMENT: ON — mid-month replacements from month-start rankings\n")

    for m_idx, (start_date, end_date) in enumerate(zip(first_days, last_days)):

        prev_month_end = last_days[m_idx - 1] if m_idx > 0 else None
        if prev_month_end is None:
            equity_history.append({"date": start_date, "equity": cash})
            continue

        hist_prices = prices.loc[:prev_month_end]
        if len(hist_prices) < CONFIG.WINDOW_3M:
            equity_history.append({"date": start_date, "equity": cash})
            continue

        # ── Regime ────────────────────────────────────────────
        regime_slots = CONFIG.TOP_N
        regime_label = "BULL"
        if regime_series is not None:
            s_reg = regime_series.loc[:prev_month_end].dropna()
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

        # ── Score & rank ALL investable ETFs (kept for mid-month replacement) ──
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
            w_sharpe  = 0.5 * _sh6 + 0.5 * _sh3
            sr2_blend = 0.0  # not used for ranking here
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
            month_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]
            for d in month_dates:
                cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)
                equity_history.append({"date": d, "equity": cash})
            continue

        # Full ranked list — sorted by WT_SHARPE descending
        # This is the "bench" for mid-month TSL replacements
        full_rank_df = pd.DataFrame(rank_list).sort_values(ranking_metric, ascending=False)

        # Sector-capped selection for initial entry
        selected = []
        sec_counts: dict = {}
        for _, r in full_rank_df.iterrows():
            sec = r["SECTOR"]
            if sec_counts.get(sec, 0) < CONFIG.SECTOR_CAP:
                selected.append(r)
                sec_counts[sec] = sec_counts.get(sec, 0) + 1
            if len(selected) >= regime_slots:
                break

        # ── SELL: liquidate all previous positions ────────────
        for t in list(portfolio.keys()):
            pos    = portfolio[t]
            p_exit = prices.loc[start_date, t] if t in prices.columns else np.nan
            if pd.isna(p_exit):
                p_exit = pos["entry_price"]
            proceeds = pos["shares"] * p_exit
            cost     = CONFIG.TRADE_COST_FIXED
            pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
            cash    += proceeds - cost
            trade_log.append({
                "TYPE"        : "SELL",
                "REASON"      : "MONTHLY_REBALANCE",
                "TICKER"      : t,
                "NAME"        : pos.get("name", ""),
                "ENTRY_DATE"  : pos["entry_date"],
                "EXIT_DATE"   : start_date,
                "HOLDING_DAYS": (start_date - pos["entry_date"]).days,
                "ENTRY_PRICE" : round(pos["entry_price"], 4),
                "EXIT_PRICE"  : round(p_exit, 4),
                "SHARES"      : round(pos["shares"], 4),
                "GROSS_PNL"   : round(proceeds - pos["shares"] * pos["entry_price"], 2),
                "COSTS"       : cost,
                "NET_PNL"     : round(pnl, 2),
                "REGIME"      : regime_label,
            })
        portfolio = {}

        # ── BUY: enter fresh positions ────────────────────────
        num_to_buy = len(selected)
        if num_to_buy > 0:
            slot_size = cash / CONFIG.TOP_N
            for r in selected:
                t       = r["TICKER"]
                p_entry = prices.loc[start_date, t] if t in prices.columns else np.nan
                if pd.isna(p_entry):
                    continue
                shares = slot_size / p_entry
                portfolio[t] = {
                    "shares"     : shares,
                    "entry_price": p_entry,
                    "peak"       : p_entry,
                    "entry_date" : start_date,
                    "name"       : r["NAME"],
                    "sector"     : r["SECTOR"],
                }
                cash -= (slot_size + CONFIG.TRADE_COST_FIXED)
                trade_log.append({
                    "TYPE"        : "BUY",
                    "REASON"      : "MONTHLY_REBALANCE",
                    "TICKER"      : t,
                    "NAME"        : r["NAME"],
                    "ENTRY_DATE"  : start_date,
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

        # ── DAILY monitoring: TSL + cash interest + REPLACEMENT ──
        month_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]
        for d in month_dates:
            cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)

            # Check TSL for all held positions
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
                    # ── TSL HIT: sell ──
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
                    exited_sector = pos.get("sector", "")
                    sale_proceeds = proceeds - cost
                    del portfolio[t]

                    # ── TSL REPLACEMENT: find next best ETF ──
                    held_tickers = set(portfolio.keys())
                    held_sectors = {portfolio[ht].get("sector", "") for ht in held_tickers}

                    replacement = None
                    for _, r in full_rank_df.iterrows():
                        rt = r["TICKER"]
                        rs = r["SECTOR"]
                        if rt in held_tickers:
                            continue  # already held
                        if rt == t:
                            continue  # just exited this one
                        if rs in held_sectors and CONFIG.SECTOR_CAP <= 1:
                            continue  # sector cap conflict
                        # Check if we can get a price for this day
                        if rt in prices.columns:
                            rp = prices.loc[d, rt]
                            if not pd.isna(rp):
                                replacement = (rt, r["NAME"], rs, rp)
                                break

                    if replacement:
                        rt, rname, rsec, rp = replacement
                        rshares = sale_proceeds / rp
                        portfolio[rt] = {
                            "shares"     : rshares,
                            "entry_price": rp,
                            "peak"       : rp,
                            "entry_date" : d,
                            "name"       : rname,
                            "sector"     : rsec,
                        }
                        cash -= (sale_proceeds + CONFIG.TRADE_COST_FIXED)
                        trade_log.append({
                            "TYPE"        : "BUY",
                            "REASON"      : "TSL_REPLACEMENT",
                            "TICKER"      : rt,
                            "NAME"        : rname,
                            "ENTRY_DATE"  : d,
                            "EXIT_DATE"   : None,
                            "HOLDING_DAYS": None,
                            "ENTRY_PRICE" : round(rp, 4),
                            "EXIT_PRICE"  : None,
                            "SHARES"      : round(rshares, 4),
                            "GROSS_PNL"   : None,
                            "COSTS"       : CONFIG.TRADE_COST_FIXED,
                            "NET_PNL"     : None,
                            "REGIME"      : regime_label,
                        })
                        tsl_replacements += 1

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
    replacements = tlog[tlog["REASON"] == "TSL_REPLACEMENT"] if len(tlog) else pd.DataFrame()

    print(f"\nTrade Log -> {CONFIG.TRADE_LOG_FILE}")
    print(f"  Total trades     : {len(tlog)}")
    print(f"  Total buys       : {len(tlog[tlog['TYPE']=='BUY'])}" if len(tlog) else "")
    print(f"  Monthly sells    : {len(sells[sells['REASON']=='MONTHLY_REBALANCE'])}" if len(sells) else "")
    print(f"  TSL hits         : {len(tsl_hits)}")
    print(f"  TSL replacements : {len(replacements)}")
    if len(sells) > 0:
        print(f"  Avg Net P&L      : INR {sells['NET_PNL'].mean():,.0f}")
        print(f"  Win Rate         : {(sells['NET_PNL'] > 0).mean():.1%}")

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
    print(f"  Run 1 + TSL REPLACEMENT  |  10% TSL  |  Monthly")
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
    ax.plot(res["equity"], label=f"Run 1 + TSL Replace ({ranking_metric}, 10% TSL)",
            linewidth=2, color="steelblue")
    if bench_ok:
        ax.plot(bench, label="Benchmark (Nifty 500)", linewidth=1.5,
                alpha=0.75, color="darkorange", linestyle="--")
    ax.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax.fill_between(res.index, res["equity"], initial, alpha=0.07, color="steelblue")
    ax.set_title("ETF Momentum — Run 1 + TSL Replacement | Monthly Rebalance | 10% TSL")
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
    run_backtest("WT_SHARPE")
