"""
ETF Momentum Backtest -- New Approach (EMA200 HOLD)
===================================================
Regime Filter (2-state, evaluated monthly using prev month-end close of ^CRSLDX):
  BULL  : EMA50 > EMA200  ->  Full monthly flush, enter fresh Top 5
  HOLD  : EMA50 < EMA200  ->  Keep positions still in Top 5; exit dropouts; NO new entries

Transition logic:
  BULL -> BULL  : Full exit + fresh Top 5 (same as Run 1)
  BULL -> HOLD  : Keep positions in Top 5, exit dropouts, no new buys
  HOLD -> HOLD  : Keep positions in Top 5, exit dropouts, no new buys
  HOLD -> BULL  : Keep positions in Top 5, fill empty slots with new buys (incremental)

TSL monitoring : Weekly (every Monday), 10% trailing stop loss
Cash interest  : 6% p.a. on all idle / TSL-exit capital
Ranking metric : Weighted Sharpe (0.5 x Sharpe6M + 0.5 x Sharpe3M)
Screen         : 52-week high proximity (NAV within 25% of 52-week high)
"""

import pandas as pd
import numpy as np
from scipy import stats as spstats
from pathlib import Path
import matplotlib.pyplot as plt
from datetime import datetime
import yfinance as yf


# =========================================================
# CONFIG
# =========================================================
class CONFIG:
    INPUT_FILE       = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\ETF - Backtest  - Copy.xlsx"
    OUT_DIR          = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\run_2026-04-18_ema200-hold"

    START_CAPITAL    = 1_000_000.0
    CASH_INTEREST_PA = 0.06          # 6% p.a. on idle cash / liquid fund
    TRADE_COST_FIXED = 20.0          # INR per trade leg
    TSL_THRESHOLD    = 0.10          # 10% trailing stop loss

    TOP_N            = 5             # max slots (BULL and HOLD)
    SECTOR_CAP       = 1             # max ETFs per sector

    WINDOW_6M        = 126
    WINDOW_3M        = 63
    ANNUALIZE        = 252
    DAILY_RF         = 0.07 / 252
    MAX_DD_FROM_HIGH = 0.25

    TREND_FAST_EMA   = 50
    TREND_SLOW_EMA   = 200           # KEY DIFFERENCE: 200 vs 100 in Run 1
    BENCHMARK_TICKER = "^CRSLDX"


# =========================================================
# SECTOR MAP
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
    lr = np.log(clean.iloc[-window-1:] / clean.iloc[-window-1:].shift(1)).dropna()
    ex = lr - CONFIG.DAILY_RF
    return (ex.mean() / ex.std()) * np.sqrt(CONFIG.ANNUALIZE) if ex.std() > 0 else np.nan


# =========================================================
# DATA LOADING
# =========================================================
def load_data(filepath: str):
    print(f"Loading {filepath} ...")
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
# REGIME
# =========================================================
def get_regime(regime_series: pd.Series, as_of: pd.Timestamp) -> str:
    """
    BULL : EMA50 > EMA200
    HOLD : EMA50 <= EMA200  (no new entries, keep existing top-ranked positions)
    Requires TREND_SLOW_EMA (200) data points; defaults to BULL if insufficient data.
    """
    s = regime_series.loc[:as_of].dropna()
    if len(s) < CONFIG.TREND_SLOW_EMA:
        return "BULL"          # insufficient data -> default BULL
    ema50  = float(s.ewm(span=CONFIG.TREND_FAST_EMA, adjust=False).mean().iloc[-1])
    ema200 = float(s.ewm(span=CONFIG.TREND_SLOW_EMA, adjust=False).mean().iloc[-1])
    return "BULL" if ema50 > ema200 else "HOLD"


# =========================================================
# RANKING
# =========================================================
def rank_etfs(hist_prices: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, m in meta.iterrows():
        t = m["TICKER"]
        if t not in hist_prices.columns:
            continue
        s = hist_prices[t].dropna()
        if len(s) < CONFIG.WINDOW_3M:
            continue
        sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
        sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
        _sh3 = 0.0 if pd.isna(sh3) else sh3
        _sh6 = 0.0 if pd.isna(sh6) else sh6
        wt   = 0.5 * _sh6 + 0.5 * _sh3
        high_52 = s.tail(252).max()
        close_p = s.iloc[-1]
        if pd.isna(high_52) or high_52 <= 0:
            continue
        if (high_52 - close_p) / high_52 <= CONFIG.MAX_DD_FROM_HIGH:
            rows.append({"TICKER": t, "NAME": m["ETF_NAME"],
                         "SECTOR": classify_sector(m["ETF_NAME"], t), "WT_SHARPE": wt})
    if not rows:
        return pd.DataFrame(columns=["TICKER", "NAME", "SECTOR", "WT_SHARPE"])
    return pd.DataFrame(rows).sort_values("WT_SHARPE", ascending=False).reset_index(drop=True)


def select_top_n(rank_df: pd.DataFrame, n: int) -> list:
    selected, sec_counts = [], {}
    for _, r in rank_df.iterrows():
        sec = r["SECTOR"]
        if sec_counts.get(sec, 0) < CONFIG.SECTOR_CAP:
            selected.append(r["TICKER"])
            sec_counts[sec] = sec_counts.get(sec, 0) + 1
        if len(selected) >= n:
            break
    return selected


# =========================================================
# BACKTEST ENGINE
# =========================================================
def run_backtest() -> dict:
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates = prices.index

    # Fetch regime data
    print("Fetching ^CRSLDX for regime ...")
    try:
        rr = yf.download(CONFIG.BENCHMARK_TICKER,
                         start=all_dates[0].strftime("%Y-%m-%d"),
                         end=(all_dates[-1] + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                         auto_adjust=True, progress=False)
        regime_series = rr["Close"].squeeze().dropna()
        regime_series.index = pd.to_datetime(regime_series.index).tz_localize(None)
        regime_series = regime_series.reindex(all_dates, method="ffill")
        print(f"  {regime_series.dropna().index[0].date()} -> "
              f"{regime_series.dropna().index[-1].date()}  ({regime_series.notna().sum()} pts)\n")
    except Exception as e:
        print(f"  [WARN] {e} -- defaulting to BULL")
        regime_series = None

    month_groups = all_dates.to_series().groupby(all_dates.to_period("M"))
    first_days   = month_groups.min().tolist()
    last_days    = month_groups.max().tolist()
    mondays      = set(d for d in all_dates if d.weekday() == 0)

    cash        = CONFIG.START_CAPITAL
    portfolio   = {}    # ticker -> {shares, entry_price, peak, entry_date, name}
    trade_log   = []
    equity_hist = []
    prev_regime = None

    print(f"Backtest: {first_days[0].date()} -> {last_days[-1].date()}")
    print(f"TSL {CONFIG.TSL_THRESHOLD*100:.0f}% (weekly Mon)  |  "
          f"Cash {CONFIG.CASH_INTEREST_PA*100:.0f}% pa  |  EMA{CONFIG.TREND_SLOW_EMA} regime\n")

    # ── Helpers ───────────────────────────────────────────
    def sell_pos(ticker, exec_date, reason):
        nonlocal cash
        pos    = portfolio[ticker]
        p_exit = prices.loc[exec_date, ticker] if ticker in prices.columns else np.nan
        if pd.isna(p_exit): p_exit = pos["entry_price"]
        proceeds = pos["shares"] * p_exit
        cost     = CONFIG.TRADE_COST_FIXED
        pnl      = proceeds - pos["shares"] * pos["entry_price"] - cost
        cash    += proceeds - cost
        trade_log.append({
            "TYPE": "SELL", "REASON": reason, "TICKER": ticker,
            "NAME": pos.get("name", ""), "ENTRY_DATE": pos["entry_date"],
            "EXIT_DATE": exec_date, "HOLDING_DAYS": (exec_date - pos["entry_date"]).days,
            "ENTRY_PRICE": round(pos["entry_price"], 4), "EXIT_PRICE": round(p_exit, 4),
            "SHARES": round(pos["shares"], 4),
            "GROSS_PNL": round(proceeds - pos["shares"] * pos["entry_price"], 2),
            "COSTS": cost, "NET_PNL": round(pnl, 2), "REGIME": regime,
        })
        del portfolio[ticker]

    def buy_pos(ticker, name, exec_date, slot_cash):
        nonlocal cash
        p = prices.loc[exec_date, ticker] if ticker in prices.columns else np.nan
        if pd.isna(p): return
        shares = slot_cash / p
        portfolio[ticker] = {"shares": shares, "entry_price": p, "peak": p,
                              "entry_date": exec_date, "name": name}
        cash -= (slot_cash + CONFIG.TRADE_COST_FIXED)
        trade_log.append({
            "TYPE": "BUY", "REASON": "REBALANCE", "TICKER": ticker, "NAME": name,
            "ENTRY_DATE": exec_date, "EXIT_DATE": None, "HOLDING_DAYS": None,
            "ENTRY_PRICE": round(p, 4), "EXIT_PRICE": None,
            "SHARES": round(shares, 4), "GROSS_PNL": None,
            "COSTS": CONFIG.TRADE_COST_FIXED, "NET_PNL": None, "REGIME": regime,
        })

    # ── Main loop ─────────────────────────────────────────
    for m_idx, (start_date, end_date) in enumerate(zip(first_days, last_days)):
        prev_month_end = last_days[m_idx - 1] if m_idx > 0 else None
        if prev_month_end is None:
            equity_hist.append({"date": start_date, "equity": cash})
            continue

        hist_prices = prices.loc[:prev_month_end]
        if len(hist_prices) < CONFIG.WINDOW_3M:
            equity_hist.append({"date": start_date, "equity": cash})
            continue

        # Regime determination
        regime = "BULL"
        if regime_series is not None:
            regime = get_regime(regime_series, prev_month_end)

        # Rank investable ETFs
        rank_df  = rank_etfs(hist_prices, meta)
        top5     = select_top_n(rank_df, CONFIG.TOP_N)
        top5_set = set(top5)

        # ── Rebalance decision ────────────────────────────
        if regime == "BULL" and prev_regime == "HOLD":
            # HOLD -> BULL: keep Top 5 positions, fill gaps
            to_exit = [t for t in portfolio if t not in top5_set]
            for t in list(to_exit):
                sell_pos(t, start_date, "HOLD_BULL_ROTATE")
            new_buys = [t for t in top5 if t not in portfolio]
            if new_buys and cash > 0:
                slot = cash / len(new_buys)
                for t in new_buys:
                    name = rank_df.loc[rank_df["TICKER"] == t, "NAME"].values
                    buy_pos(t, name[0] if len(name) else t, start_date, slot)
            print(f"  [{start_date.date()}] HOLD->BULL: kept {len(portfolio)-len(new_buys)}, "
                  f"rotated {len(to_exit)}, added {len(new_buys)}")

        elif regime == "BULL":
            # BULL -> BULL: full flush + fresh Top 5
            for t in list(portfolio.keys()):
                sell_pos(t, start_date, "MONTHLY_REBALANCE")
            if top5:
                slot = cash / CONFIG.TOP_N
                for t in top5:
                    name = rank_df.loc[rank_df["TICKER"] == t, "NAME"].values
                    buy_pos(t, name[0] if len(name) else t, start_date, slot)

        else:
            # HOLD (any prior state): keep Top 5, exit dropouts, no new entries
            to_exit = [t for t in portfolio if t not in top5_set]
            for t in list(to_exit):
                sell_pos(t, start_date, "HOLD_REBAL_EXIT")
            kept  = len(portfolio)
            exited = len(to_exit)
            if exited or prev_regime != "HOLD":
                print(f"  [{start_date.date()}] HOLD: kept {kept}, exited {exited}, "
                      f"cash slots {CONFIG.TOP_N - kept}")

        prev_regime = regime

        # ── Daily loop: cash interest + weekly TSL ────────
        month_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]
        for d in month_dates:
            # Daily cash interest (6% pa)
            cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)

            # TSL check on Mondays only
            if d in mondays:
                for t in list(portfolio.keys()):
                    if t not in prices.columns: continue
                    p_curr = prices.loc[d, t]
                    if pd.isna(p_curr): continue
                    pos = portfolio[t]
                    if p_curr > pos["peak"]: pos["peak"] = p_curr
                    dd = (pos["peak"] - p_curr) / pos["peak"]
                    if dd >= CONFIG.TSL_THRESHOLD:
                        sell_pos(t, d, f"TSL_HIT ({dd*100:.1f}%)")

            # Daily equity snapshot
            pv = sum(pos["shares"] * prices.loc[d, t]
                     for t, pos in portfolio.items()
                     if t in prices.columns and not pd.isna(prices.loc[d, t]))
            equity_hist.append({"date": d, "equity": cash + pv})

    # =========================================================
    # OUTPUT
    # =========================================================
    out = Path(CONFIG.OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    res = pd.DataFrame(equity_hist).set_index("date")
    res.to_csv(out / "backtest_equity.csv")

    tlog = pd.DataFrame(trade_log)
    tlog.to_csv(out / "backtest_trade_log.csv", index=False)
    sells    = tlog[tlog["TYPE"] == "SELL"] if len(tlog) else pd.DataFrame()
    tsl_hits = sells[sells["REASON"].str.startswith("TSL")] if len(sells) else pd.DataFrame()
    hold_ex  = sells[sells["REASON"].str.startswith("HOLD")] if len(sells) else pd.DataFrame()

    print(f"\nTrades: {len(tlog)}  |  Buys: {len(tlog[tlog['TYPE']=='BUY']) if len(tlog) else 0}")
    print(f"  Monthly sells : {len(sells[sells['REASON']=='MONTHLY_REBALANCE'])}" if len(sells) else "")
    print(f"  HOLD exits    : {len(hold_ex)}")
    print(f"  TSL hits      : {len(tsl_hits)}")
    if len(sells) > 0:
        print(f"  Avg Net P&L   : INR {sells['NET_PNL'].mean():,.0f}")
        print(f"  Win Rate      : {(sells['NET_PNL'] > 0).mean():.1%}")

    initial   = CONFIG.START_CAPITAL
    final_val = res.iloc[-1]["equity"]
    years     = (res.index[-1] - res.index[0]).days / 365.25
    cagr      = (final_val / initial) ** (1 / years) - 1
    res["peak"] = res["equity"].cummax()
    res["dd"]   = (res["equity"] - res["peak"]) / res["peak"]
    max_dd    = res["dd"].min()
    dr        = res["equity"].pct_change().dropna()
    vol       = dr.std() * np.sqrt(252)
    sharpe    = cagr / vol if vol > 0 else 0

    print("\n" + "="*50)
    print("  NEW APPROACH: EMA200-HOLD | 10% TSL (Mon) | 6% Cash")
    print("="*50)
    print(f"  Period       : {res.index[0].date()} -> {res.index[-1].date()}")
    print(f"  Start Capital: INR {initial:>12,.0f}")
    print(f"  End Capital  : INR {final_val:>12,.0f}")
    print(f"  Total Return : {(final_val/initial - 1):>10.2%}")
    print(f"  CAGR         : {cagr:>10.2%}")
    print(f"  Max Drawdown : {max_dd:>10.2%}")
    print(f"  Annual Vol   : {vol:>10.2%}")
    print(f"  Sharpe Ratio : {sharpe:>10.2f}")
    print("="*50)

    # Benchmark
    bench_ok = False
    b_cagr = b_dd = b_sharpe = bench = None
    try:
        braw  = yf.download(CONFIG.BENCHMARK_TICKER,
                            start=res.index[0].strftime("%Y-%m-%d"),
                            end=res.index[-1].strftime("%Y-%m-%d"),
                            auto_adjust=True, progress=False)
        bench = braw["Close"].squeeze().dropna()
        bench.index = pd.to_datetime(bench.index).tz_localize(None)
        bench = bench.reindex(res.index, method="ffill")
        bench = (bench / bench.iloc[0]) * initial
        b_cagr   = (bench.iloc[-1] / initial) ** (1 / years) - 1
        b_ret    = bench.pct_change().dropna()
        b_dd     = ((bench - bench.cummax()) / bench.cummax()).min()
        b_vol    = b_ret.std() * np.sqrt(252)
        b_sharpe = b_cagr / b_vol if b_vol > 0 else 0
        print(f"\n  Benchmark CAGR  : {b_cagr:.2%}")
        print(f"  Benchmark Max DD: {b_dd:.2%}")
        print(f"  Benchmark Sharpe: {b_sharpe:.2f}")
        bench_ok = True
    except Exception as e:
        print(f"  [WARN] Benchmark: {e}")

    # Chart
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(res["equity"], label="New Approach (EMA200-HOLD, 10% TSL Mon, 6% cash)",
            linewidth=2, color="mediumseagreen")
    if bench_ok:
        ax.plot(bench, label="Benchmark (Nifty 500)", linewidth=1.5,
                color="darkorange", linestyle="--", alpha=0.8)
    ax.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax.fill_between(res.index, res["equity"], initial, alpha=0.07, color="mediumseagreen")
    ax.set_title("New Approach: EMA200-HOLD Regime | 10% TSL (Weekly) | 6% Cash | Monthly Rebalance")
    ax.set_ylabel("Equity (INR)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "equity_curve.png", dpi=150)
    plt.close(fig)
    print(f"\nChart -> {out / 'equity_curve.png'}")

    return {"cagr": cagr, "max_dd": max_dd, "vol": vol, "sharpe": sharpe,
            "total_ret": final_val / initial - 1,
            "b_cagr": b_cagr, "b_dd": b_dd, "b_sharpe": b_sharpe}


if __name__ == "__main__":
    run_backtest()
