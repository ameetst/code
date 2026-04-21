"""
ETF Alpha + LowVol Momentum Backtest (Clipped to Apr 2021)
==========================================================
Replicates the ETF Run 1 Backtest but swaps Weighted Sharpe
for a Jensen's Alpha + Inverse Volatility composite metric.
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
    OUT_DIR          = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\run_2026-04-18_alpha_lowvol"

    START_CAPITAL    = 1_000_000.0
    CASH_INTEREST_PA = 0.02          # 2% p.a.
    TRADE_COST_FIXED = 20.0          # INR per leg
    TSL_THRESHOLD    = 0.10          # 10% TSL

    TOP_N            = 5
    TOP_N_PARTIAL    = 3
    SECTOR_CAP       = 1

    WINDOW_12M       = 252           # 12M lookback for Alpha/Vol
    ANNUALIZE        = 252
    DAILY_RF         = 0.07 / 252
    MAX_DD_FROM_HIGH = 0.25

    TREND_FAST_EMA   = 50
    TREND_SLOW_EMA   = 100
    BENCHMARK_TICKER = "^CRSLDX"


# =========================================================
# UTILITIES
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


def load_data(filepath: str):
    print(f"Loading {filepath} ...")
    raw       = pd.read_excel(filepath, sheet_name="DATA", header=None)
    header    = raw.iloc[0]
    date_cols = [c for c in range(2, raw.shape[1])
                 if pd.notna(header.iloc[c]) and isinstance(header.iloc[c], (datetime, pd.Timestamp))]
    dates     = pd.to_datetime([header.iloc[c] for c in date_cols])
    meta      = pd.DataFrame({
        "ETF_NAME": raw.iloc[1:, 0].fillna("").astype(str).str.strip(),
        "TICKER"  : raw.iloc[1:, 1].astype(str).str.strip(),
    }).reset_index(drop=True)
    price_df  = raw.iloc[1:, date_cols].apply(pd.to_numeric, errors="coerce").replace(0, np.nan)
    price_df.columns = dates
    price_df.index   = meta["TICKER"]
    prices    = price_df.T.sort_index().ffill()
    return meta, prices


def get_regime(regime_series: pd.Series, as_of: pd.Timestamp) -> tuple:
    s = regime_series.loc[:as_of].dropna()
    if len(s) < CONFIG.TREND_SLOW_EMA:
        return "BULL", CONFIG.TOP_N

    price  = float(s.iloc[-1])
    ema50  = float(s.ewm(span=CONFIG.TREND_FAST_EMA, adjust=False).mean().iloc[-1])
    ema100 = float(s.ewm(span=CONFIG.TREND_SLOW_EMA, adjust=False).mean().iloc[-1])

    if ema50 > ema100 and price > ema50:
        return "BULL", CONFIG.TOP_N
    elif price > ema100:
        return "PARTIAL", CONFIG.TOP_N_PARTIAL
    else:
        return "BEAR", 0


def rank_etfs(hist_prices: pd.DataFrame, meta: pd.DataFrame, nifty_series: pd.Series) -> pd.DataFrame:
    rows = []
    window = CONFIG.WINDOW_12M
    nifty_px = nifty_series.dropna()
    
    if len(nifty_px) < window * 0.90:
        return pd.DataFrame()
        
    nifty_px_w = nifty_px.iloc[-(window+1):] if len(nifty_px) > window else nifty_px
    mkt_rets = np.diff(np.log(nifty_px_w.values))
    mkt_excess = mkt_rets - CONFIG.DAILY_RF
    
    alpha_data, vol_data = {}, {}
    for _, m in meta.iterrows():
        t = m["TICKER"]
        if t not in hist_prices.columns: continue
        s = hist_prices[t].dropna()
        if len(s) < window * 0.90: continue
        
        n = min(len(s) - 1, window)
        s_rets = np.diff(np.log(s.iloc[-n-1:].values))
        s_excess = s_rets - CONFIG.DAILY_RF
        
        m_exc = mkt_excess[-n:]
        s_exc = s_excess[-n:]
        
        if len(s_exc) != len(m_exc) or len(s_exc) < 10:
            continue
            
        vol = np.std(s_rets, ddof=1) * np.sqrt(252)
        inv_vol = 1.0 / vol if vol > 1e-6 else np.nan
        
        X = np.column_stack([np.ones(len(m_exc)), m_exc])
        try:
            coeffs, _, _, _ = np.linalg.lstsq(X, s_exc, rcond=None)
            intercept = coeffs[0]
            annual_alpha = intercept * 252
        except np.linalg.LinAlgError:
            continue
            
        high_52 = s.tail(252).max()
        close_p = s.iloc[-1]
        
        if pd.isna(high_52) or high_52 <= 0: continue
        # 52w high proximity screen
        if (high_52 - close_p) / high_52 <= CONFIG.MAX_DD_FROM_HIGH:
            alpha_data[t] = annual_alpha
            vol_data[t] = inv_vol
            rows.append({"TICKER": t, "NAME": m["ETF_NAME"], "SECTOR": classify_sector(m["ETF_NAME"], t)})
            
    if not rows:
        return pd.DataFrame()
        
    df = pd.DataFrame(rows)
    df["ALPHA"] = df["TICKER"].map(alpha_data)
    df["INV_VOL"] = df["TICKER"].map(vol_data)
    
    def z_score(series):
        mu, sd = series.mean(), series.std(ddof=1)
        return (series - mu) / sd if sd > 0 else series * 0.0
        
    df["Z_ALPHA"]   = z_score(df["ALPHA"])
    df["Z_INV_VOL"] = z_score(df["INV_VOL"])
    # 50/50 composite of Alpha Z and Vol Z
    df["COMPOSITE"] = 0.5 * df["Z_ALPHA"] + 0.5 * df["Z_INV_VOL"]
    
    return df.sort_values("COMPOSITE", ascending=False).reset_index(drop=True)


def select_top_n(rank_df: pd.DataFrame, n: int) -> list:
    selected = []
    sec_counts = {}
    for _, r in rank_df.iterrows():
        sec = r["SECTOR"]
        if sec_counts.get(sec, 0) < CONFIG.SECTOR_CAP:
            selected.append(r)
            sec_counts[sec] = sec_counts.get(sec, 0) + 1
        if len(selected) >= n:
            break
    return selected


# =========================================================
# BACKTEST ENGINE
# =========================================================
def run_backtest():
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates = prices.index

    print("Fetching benchmark (^CRSLDX / Nifty 500) for regime/alpha regression...")
    try:
        rr = yf.download(CONFIG.BENCHMARK_TICKER,
                         start=all_dates[0].strftime("%Y-%m-%d"),
                         end=(all_dates[-1] + pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
                         auto_adjust=True, progress=False)
        regime_series = rr["Close"].squeeze().dropna()
        regime_series.index = pd.to_datetime(regime_series.index).tz_localize(None)
        regime_series = regime_series.reindex(all_dates, method="ffill")
        bench_ok = True
    except Exception as e:
        print(f"Failed to fetch Nifty 500: {e}")
        return

    month_groups = all_dates.to_series().groupby(all_dates.to_period("M"))
    first_days   = month_groups.min().tolist()
    last_days    = month_groups.max().tolist()

    cash        = CONFIG.START_CAPITAL
    portfolio   = {}
    trade_log   = []
    equity_hist = []

    print("Backtest starting... (Alpha+LowVol Composite, clipped to Apr 2021)")
    
    for m_idx, (start_date, end_date) in enumerate(zip(first_days, last_days)):
        prev_month_end = last_days[m_idx - 1] if m_idx > 0 else None
        if prev_month_end is None:
            equity_hist.append({"date": start_date, "equity": cash})
            continue

        hist_prices = prices.loc[:prev_month_end]
        
        regime_label, active_slots = get_regime(regime_series, prev_month_end)
        rank_df = rank_etfs(hist_prices, meta, regime_series.loc[:prev_month_end])

        if rank_df.empty or "COMPOSITE" not in rank_df.columns:
            equity_hist.append({"date": start_date, "equity": cash})
            continue
            
        selected = select_top_n(rank_df, active_slots) if active_slots > 0 else []
        
        # Monthly Full Reset
        for t in list(portfolio.keys()):
            pos      = portfolio[t]
            p_exit   = prices.loc[start_date, t] if t in prices.columns else np.nan
            if pd.isna(p_exit): p_exit = pos["entry_price"]
            proceeds = pos["shares"] * p_exit
            cost     = CONFIG.TRADE_COST_FIXED
            pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
            cash    += proceeds - cost
            trade_log.append({
                "TYPE": "SELL", "REASON": "MONTHLY_REBALANCE", "TICKER": t,
                "NAME": pos["name"], "ENTRY_DATE": pos["entry_date"],
                "EXIT_DATE": start_date, "HOLDING_DAYS": (start_date - pos["entry_date"]).days,
                "ENTRY_PRICE": pos["entry_price"], "EXIT_PRICE": p_exit,
                "SHARES": pos["shares"], "GROSS_PNL": proceeds - pos["shares"] * pos["entry_price"],
                "COSTS": cost, "NET_PNL": pnl, "REGIME": regime_label,
            })
            del portfolio[t]

        if selected:
            slot_size = cash / CONFIG.TOP_N  
            for r in selected:
                t = r["TICKER"]
                p_entry = prices.loc[start_date, t] if t in prices.columns else np.nan
                if pd.isna(p_entry): continue
                    
                shares = slot_size / p_entry
                portfolio[t] = {
                    "shares": shares, "entry_price": p_entry, "peak": p_entry,
                    "entry_date": start_date, "name": r["NAME"]
                }
                cash -= (slot_size + CONFIG.TRADE_COST_FIXED)
                trade_log.append({
                    "TYPE": "BUY", "REASON": "MONTHLY_REBALANCE", "TICKER": t,
                    "NAME": r["NAME"], "ENTRY_DATE": start_date, "EXIT_DATE": None,
                    "HOLDING_DAYS": None, "ENTRY_PRICE": p_entry, "EXIT_PRICE": None,
                    "SHARES": shares, "GROSS_PNL": None, "COSTS": CONFIG.TRADE_COST_FIXED,
                    "NET_PNL": None, "REGIME": regime_label,
                })

        # Daily Tracking
        month_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]
        for d in month_dates:
            cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)
            for t in list(portfolio.keys()):
                if t not in prices.columns: continue
                p_curr = prices.loc[d, t]
                if pd.isna(p_curr): continue
                    
                pos = portfolio[t]
                if p_curr > pos["peak"]: pos["peak"] = p_curr
                    
                dd = (pos["peak"] - p_curr) / pos["peak"]
                if dd >= CONFIG.TSL_THRESHOLD:
                    proceeds = pos["shares"] * p_curr
                    cost     = CONFIG.TRADE_COST_FIXED
                    pnl      = proceeds - (pos["shares"] * pos["entry_price"]) - cost
                    cash    += proceeds - cost
                    trade_log.append({
                        "TYPE": "SELL", "REASON": f"TSL_HIT ({dd*100:.1f}%)", "TICKER": t,
                        "NAME": pos.get("name", ""), "ENTRY_DATE": pos["entry_date"],
                        "EXIT_DATE": d, "HOLDING_DAYS": (d - pos["entry_date"]).days,
                        "ENTRY_PRICE": pos["entry_price"], "EXIT_PRICE": p_curr,
                        "SHARES": pos["shares"], "GROSS_PNL": proceeds - pos["shares"] * pos["entry_price"],
                        "COSTS": cost, "NET_PNL": pnl, "REGIME": regime_label,
                    })
                    del portfolio[t]

            port_val = sum(pos["shares"] * prices.loc[d, t]
                           for t, pos in portfolio.items()
                           if t in prices.columns and not pd.isna(prices.loc[d, t]))
            equity_hist.append({"date": d, "equity": cash + port_val})

    # =========================================================
    # RESULTS
    # =========================================================
    out = Path(CONFIG.OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    
    res = pd.DataFrame(equity_hist).set_index("date")
    
    # ── DATE FILTER FOR FAIR COMPARISON (Apr 2021) ──
    cut_date = pd.to_datetime('2021-04-01')
    res_full = res.copy()
    
    start_val = res.loc[cut_date, 'equity'] if cut_date in res.index else res.iloc[0]['equity']
    res_clip = res.loc[cut_date:].copy()
    
    bx = regime_series.loc[cut_date:].copy()
    b_initial = bx.iloc[0]
    bx = (bx / b_initial) * start_val
    
    initial = float(start_val)
    final   = float(res_clip.iloc[-1]['equity'])
    years   = (res_clip.index[-1] - res_clip.index[0]).days / 365.25
    cagr    = (final / initial) ** (1 / years) - 1
    
    res_clip['peak'] = res_clip['equity'].cummax()
    max_dd  = ((res_clip['equity'] - res_clip['peak']) / res_clip['peak']).min()
    vol     = res_clip['equity'].pct_change().dropna().std() * np.sqrt(252)
    sharpe  = cagr / vol if vol > 0 else 0
    
    b_final  = float(bx.iloc[-1])
    b_cagr   = (b_final / initial) ** (1 / years) - 1
    b_vol    = bx.pct_change().dropna().std() * np.sqrt(252)
    b_dd     = ((bx - bx.cummax()) / bx.cummax()).min()
    b_sharpe = b_cagr / b_vol if b_vol > 0 else 0

    print("\n" + "="*50)
    print("  CLIPPED BACKTEST: Alpha + LowVol (Run 1 Config)")
    print("="*50)
    print(f"  Period       : {res_clip.index[0].date()} -> {res_clip.index[-1].date()}")
    print(f"  Start Capital: INR {initial:>12,.0f}")
    print(f"  End Capital  : INR {final:>12,.0f}")
    print(f"  Total Return : {(final/initial - 1):>10.2%}")
    print(f"  CAGR         : {cagr:>10.2%}")
    print(f"  Max Drawdown : {max_dd:>10.2%}")
    print(f"  Annual Vol   : {vol:>10.2%}")
    print(f"  Sharpe Ratio : {sharpe:>10.2f}")
    print("="*50)
    print(f"  Bench CAGR   : {b_cagr:>10.2%}")
    print(f"  Bench DD     : {b_dd:>10.2%}")
    print(f"  Bench Sharpe : {b_sharpe:>10.2f}")

    # Output CSVs
    res.to_csv(out / "backtest_equity.csv")
    tlog = pd.DataFrame(trade_log)
    tlog.to_csv(out / "backtest_trade_log.csv", index=False)
    
    sells = tlog[tlog["TYPE"] == "SELL"] if len(tlog) else pd.DataFrame()
    tsl_hits = sells[sells["REASON"].str.startswith("TSL")] if len(sells) else pd.DataFrame()
    
    print("\nLogs:")
    print(f"  Trades: {len(tlog)} | Monthly sells: {len(sells[sells['REASON']=='MONTHLY_REBALANCE'])} | TSL hits: {len(tsl_hits)}")
    if len(sells) > 0:
        print(f"  Avg Net P&L: INR {sells['NET_PNL'].mean():,.0f} | Win Rate: {(sells['NET_PNL'] > 0).mean():.1%}")

    # Chart
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.plot(res_clip["equity"], label="Strategy (Alpha+LowVol, 10% TSL)", linewidth=2, color="mediumpurple")
    ax.plot(bx, label="Benchmark (Nifty 500)", linewidth=1.5, alpha=0.75, color="darkorange", linestyle="--")
    ax.axhline(initial, color="grey", linewidth=0.8, linestyle=":")
    ax.fill_between(res_clip.index, res_clip["equity"], initial, alpha=0.07, color="mediumpurple")
    ax.set_title("Alpha+LowVol ETF Momentum | Run 1 Regime | Apr 2021 Onwards")
    ax.set_ylabel("Equity (INR)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "equity_curve.png", dpi=150)
    plt.close(fig)

if __name__ == "__main__":
    run_backtest()
