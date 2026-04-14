import pandas as pd
import numpy as np
from scipy import stats
from pathlib import Path
import matplotlib.pyplot as plt
from datetime import datetime
import yfinance as yf

# =========================================================
# CONFIG FOR BACKTEST
# =========================================================
class CONFIG:
    INPUT_FILE       = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\ETF - Backtest  - Copy.xlsx"
    TRADE_LOG_FILE   = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\backtest_trade_log.csv"
    EQUITY_LOG_FILE  = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\backtest_equity.csv"
    CHART_FILE       = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\equity_curve.png"

    START_CAPITAL    = 1_000_000.0
    CASH_INTEREST_PA = 0.02   # 2% annual interest on idle cash
    TRADE_COST_FIXED = 20.0   # INR per buy/sell
    TSL_THRESHOLD    = 0.10   # 10% trailing stop loss (was 5%)

    # Portfolio Slots
    TOP_N         = 5
    TOP_N_PARTIAL = 3
    SECTOR_CAP    = 1

    # Momentum windows (trading days)
    WINDOW_6M  = 126
    WINDOW_3M  = 63
    ANNUALIZE  = 252
    DAILY_RF   = 0.07 / 252

    # Screens
    MAX_DRAWDOWN_FROM_HIGH = 0.25

    # Regime
    REGIME_TICKER         = "MONIFTY500"
    TREND_FAST_EMA_WINDOW = 50
    TREND_EMA_WINDOW      = 100
    BENCHMARK_TICKER      = "^CRSLDX"   # Nifty 500 index on Yahoo Finance


# =========================================================
# SECTOR CLASSIFICATION
# =========================================================
_SECTOR_RULES = [
    ("PSU_BANK",         ["psu bank","psubnk","psubank","bse psu bank"]),
    ("PRIVATE_BANK",     ["private bank","pvt bank","pvtban","nifty pb "]),
    ("BANKING_BROAD",    ["nifty bank","bse bank"," bank ","banketf","bankbees","banknifty","nifban"]),
    ("IT_TECH",          ["nifty it","bse it"," it etf","itbees","itietf","nifit","tech etf"]),
    ("HEALTHCARE",       ["healthcare","pharma","health "," hc "," hc\\"]),
    ("METAL",            ["metal"]),
    ("ENERGY",           ["energy","oil & gas","o&g","oilietf","power etf","bse power"]),
    ("INFRA",            ["infra"]),
    ("CONSUMPTION",      ["consumption","consump","fmcg","consumer"]),
    ("REALTY",           ["realty","real estate"]),
    ("DEFENCE",          ["defence","dfnc"]),
    ("PSE",              ["pse etf","cpse","nifty pse","bharat 22","cpseetf"]),
    ("AUTO",             ["auto"]),
    ("CHEMICALS",        ["chemical"]),
    ("FIN_SERVICES",     ["fin serv","financial serv","finietf","bfsi","capital mkt","captl mkt","capital market","cptmkt","capital mrkts"]),
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
    ("BROAD_MARKET",     ["nifty 50","nifty50","sensex","nifty 100","nifty100","nifty 200","nifty 500","nifty500","total market","total mrkt","bse 500","bse500","multicap","mltcp","lgmdcp","gth sectors","flexicap","flexi"]),
    ("SERVICES",         ["services","svcs"]),
]

def classify_sector(etf_name: str, ticker: str) -> str:
    n, t = etf_name.lower(), ticker.lower()
    for sector, keywords in _SECTOR_RULES:
        if any(kw in n or kw in t for kw in keywords): return sector
    return "OTHER"


# =========================================================
# SCORING
# =========================================================
def sharpe_score(series: pd.Series, window: int) -> float:
    if len(series) < 3: return np.nan
    if len(series) < window + 1: return np.nan
    log_ret = np.log(series.iloc[-window - 1:] / series.iloc[-window - 1:].shift(1)).dropna()
    excess = log_ret - CONFIG.DAILY_RF
    return (excess.mean() / excess.std()) * np.sqrt(CONFIG.ANNUALIZE) if excess.std() > 0 else np.nan


def r2_score(series: pd.Series, window: int) -> float:
    """R-squared from log-linear regression (trend consistency). 0=random, 1=perfect trend."""
    clean = series.dropna()
    if len(clean) < window: return np.nan
    from scipy import stats as spstats
    y = np.log(clean.iloc[-window:].values.astype(float))
    x = np.arange(len(y))
    _, _, r, _, _ = spstats.linregress(x, y)
    return r ** 2


# =========================================================
# DATA LOADING
# =========================================================
def load_data(filepath):
    print(f"Loading data from {filepath}...")
    raw = pd.read_excel(filepath, sheet_name="DATA", header=None)
    header = raw.iloc[0]
    date_cols = [c for c in range(2, raw.shape[1])
                 if pd.notna(header.iloc[c]) and isinstance(header.iloc[c], (datetime, pd.Timestamp))]
    dates = pd.to_datetime([header.iloc[c] for c in date_cols])

    meta = pd.DataFrame({
        "ETF_NAME" : raw.iloc[1:, 0].fillna("").astype(str).str.strip(),
        "TICKER"   : raw.iloc[1:, 1].astype(str).str.strip(),
    }).reset_index(drop=True)

    price_df = raw.iloc[1:, date_cols].apply(pd.to_numeric, errors="coerce").replace(0, np.nan)
    price_df.columns = dates
    price_df.index   = meta["TICKER"]
    prices = price_df.T.sort_index().ffill()
    return meta, prices


# =========================================================
# BACKTEST ENGINE
# =========================================================
def run_backtest(ranking_metric: str = "WT_SHARPE") -> dict:
    """Run the full backtest.
    ranking_metric: 'WT_SHARPE' or 'SR2_BLEND'
    Returns a dict with equity series and performance stats.
    """
    meta, prices = load_data(CONFIG.INPUT_FILE)
    all_dates = prices.index

    # Fetch real Nifty 500 index for regime filter (replaces flat MONIFTY500 from Excel)
    print("Fetching Nifty 500 index for regime filter (^CRSLDX)...")
    try:
        reg_raw = yf.download(
            CONFIG.BENCHMARK_TICKER,
            start=all_dates[0].strftime("%Y-%m-%d"),
            end=all_dates[-1].strftime("%Y-%m-%d"),
            auto_adjust=True, progress=False,
        )
        regime_series = reg_raw["Close"].squeeze().dropna()
        regime_series.index = pd.to_datetime(regime_series.index).tz_localize(None)
        regime_series = regime_series.reindex(all_dates, method="ffill")
        print(f"  Regime data: {regime_series.index[0].date()} -> {regime_series.index[-1].date()}  ({regime_series.notna().sum()} pts)\n")
    except Exception as e:
        print(f"  [WARN] Could not fetch regime data: {e}. Defaulting to BULL.")
        regime_series = None

    month_groups = all_dates.to_series().groupby(all_dates.to_period('M'))
    first_days = month_groups.min().tolist()
    last_days  = month_groups.max().tolist()

    cash           = CONFIG.START_CAPITAL
    portfolio      = {}   # ticker -> {shares, entry_price, peak, entry_date, name}
    equity_history = []
    trade_log      = []   # All buy/sell records

    print(f"Starting Backtest from {first_days[0].date()} to {last_days[-1].date()}...")
    print(f"TSL: {CONFIG.TSL_THRESHOLD*100:.0f}%  |  Cash Interest: {CONFIG.CASH_INTEREST_PA*100:.0f}% pa  |  Cost: INR {CONFIG.TRADE_COST_FIXED} per trade\n")

    for m_idx, (start_date, end_date) in enumerate(zip(first_days, last_days)):

        prev_month_end = last_days[m_idx - 1] if m_idx > 0 else None
        if prev_month_end is None:
            equity_history.append({'date': start_date, 'equity': cash})
            continue

        hist_prices = prices.loc[:prev_month_end]
        if len(hist_prices) < CONFIG.WINDOW_3M:
            equity_history.append({'date': start_date, 'equity': cash})
            continue

        # ── Regime check (using live ^CRSLDX Nifty 500 index) ────
        regime_slots = CONFIG.TOP_N   # default BULL if no regime data
        regime_label = "BULL"
        if regime_series is not None:
            s_reg = regime_series.loc[:prev_month_end].dropna()
            if len(s_reg) >= CONFIG.TREND_EMA_WINDOW:
                p_now  = float(s_reg.iloc[-1])
                ema50  = float(s_reg.ewm(span=CONFIG.TREND_FAST_EMA_WINDOW, adjust=False).mean().iloc[-1])
                ema100 = float(s_reg.ewm(span=CONFIG.TREND_EMA_WINDOW,      adjust=False).mean().iloc[-1])
                if ema50 > ema100 and p_now > ema50:
                    regime_slots = CONFIG.TOP_N;         regime_label = "BULL"
                elif p_now > ema100:
                    regime_slots = CONFIG.TOP_N_PARTIAL; regime_label = "PARTIAL"
                else:
                    regime_slots = 0;                    regime_label = "BEAR"

        # ── Scoring & selection ───────────────────────────────
        rank_list = []
        for _, m_row in meta.iterrows():
            t = m_row["TICKER"]
            if t not in hist_prices.columns: continue
            s = hist_prices[t].dropna()
            if len(s) < CONFIG.WINDOW_3M: continue

            sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
            sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
            r3  = r2_score(s, CONFIG.WINDOW_3M)
            r6  = r2_score(s, CONFIG.WINDOW_6M)

            _sh6 = 0.0 if pd.isna(sh6) else sh6
            _sh3 = 0.0 if pd.isna(sh3) else sh3
            _r6  = 0.0 if (r6 is None or pd.isna(r6)) else r6
            _r3  = 0.0 if (r3 is None or pd.isna(r3)) else r3

            w_sharpe  = 0.5 * _sh6 + 0.5 * _sh3
            sr2_blend = ((_sh6 * _r6) + (_sh3 * _r3)) / 2.0

            high_52 = s.tail(252).max()
            close_p = s.iloc[-1]
            if (high_52 - close_p) / high_52 <= CONFIG.MAX_DRAWDOWN_FROM_HIGH:
                rank_list.append({
                    'TICKER'   : t,
                    'NAME'     : m_row['ETF_NAME'],
                    'WT_SHARPE': w_sharpe,
                    'SR2_BLEND': sr2_blend,
                    'SECTOR'   : classify_sector(m_row['ETF_NAME'], t),
                })

        rank_df  = pd.DataFrame(rank_list).sort_values(ranking_metric, ascending=False)
        selected = []
        sec_counts: dict = {}
        for _, r in rank_df.iterrows():
            sec = r['SECTOR']
            if sec_counts.get(sec, 0) < CONFIG.SECTOR_CAP:
                selected.append(r)
                sec_counts[sec] = sec_counts.get(sec, 0) + 1
            if len(selected) >= regime_slots: break

        # ── SELL: liquidate all previous month positions ───────
        if portfolio:
            for t, pos in portfolio.items():
                p_exit = prices.loc[start_date, t]
                if pd.isna(p_exit): p_exit = pos['entry_price']  # fallback
                proceeds = pos['shares'] * p_exit
                cost     = CONFIG.TRADE_COST_FIXED
                pnl      = proceeds - (pos['shares'] * pos['entry_price']) - cost
                cash += proceeds - cost
                trade_log.append({
                    'TYPE'        : 'SELL',
                    'REASON'      : 'MONTHLY_REBALANCE',
                    'TICKER'      : t,
                    'NAME'        : pos.get('name', ''),
                    'ENTRY_DATE'  : pos['entry_date'],
                    'EXIT_DATE'   : start_date,
                    'HOLDING_DAYS': (start_date - pos['entry_date']).days,
                    'ENTRY_PRICE' : round(pos['entry_price'], 4),
                    'EXIT_PRICE'  : round(p_exit, 4),
                    'SHARES'      : round(pos['shares'], 4),
                    'GROSS_PNL'   : round(proceeds - pos['shares'] * pos['entry_price'], 2),
                    'COSTS'       : cost,
                    'NET_PNL'     : round(pnl, 2),
                    'REGIME'      : regime_label,
                })
            portfolio = {}

        # ── BUY: enter fresh positions ─────────────────────────
        total_pool = cash
        num_to_buy = len(selected)
        if num_to_buy > 0:
            cash -= num_to_buy * CONFIG.TRADE_COST_FIXED
            slot_size = (total_pool - num_to_buy * CONFIG.TRADE_COST_FIXED) / CONFIG.TOP_N
            for r in selected:
                t       = r['TICKER']
                p_entry = prices.loc[start_date, t]
                if pd.isna(p_entry): continue
                shares  = slot_size / p_entry
                portfolio[t] = {
                    'shares'      : shares,
                    'entry_price' : p_entry,
                    'peak'        : p_entry,
                    'entry_date'  : start_date,
                    'name'        : r['NAME'],
                }
                cash -= slot_size
                trade_log.append({
                    'TYPE'        : 'BUY',
                    'REASON'      : 'MONTHLY_REBALANCE',
                    'TICKER'      : t,
                    'NAME'        : r['NAME'],
                    'ENTRY_DATE'  : start_date,
                    'EXIT_DATE'   : None,
                    'HOLDING_DAYS': None,
                    'ENTRY_PRICE' : round(p_entry, 4),
                    'EXIT_PRICE'  : None,
                    'SHARES'      : round(shares, 4),
                    'GROSS_PNL'   : None,
                    'COSTS'       : CONFIG.TRADE_COST_FIXED,
                    'NET_PNL'     : None,
                    'REGIME'      : regime_label,
                })

        # ── DAILY MONITORING: TSL + Cash interest ─────────────
        month_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]
        for d in month_dates:
            cash *= (1 + CONFIG.CASH_INTEREST_PA / 365.0)

            to_exit = []
            for t, pos in portfolio.items():
                p_curr = prices.loc[d, t]
                if pd.isna(p_curr): continue
                if p_curr > pos['peak']: pos['peak'] = p_curr
                drawdown = (pos['peak'] - p_curr) / pos['peak']
                if drawdown >= CONFIG.TSL_THRESHOLD:
                    proceeds = pos['shares'] * p_curr
                    cost     = CONFIG.TRADE_COST_FIXED
                    pnl      = proceeds - (pos['shares'] * pos['entry_price']) - cost
                    cash += proceeds - cost
                    trade_log.append({
                        'TYPE'        : 'SELL',
                        'REASON'      : f'TSL_HIT ({drawdown*100:.1f}% from peak)',
                        'TICKER'      : t,
                        'NAME'        : pos.get('name', ''),
                        'ENTRY_DATE'  : pos['entry_date'],
                        'EXIT_DATE'   : d,
                        'HOLDING_DAYS': (d - pos['entry_date']).days,
                        'ENTRY_PRICE' : round(pos['entry_price'], 4),
                        'EXIT_PRICE'  : round(p_curr, 4),
                        'SHARES'      : round(pos['shares'], 4),
                        'GROSS_PNL'   : round(proceeds - pos['shares'] * pos['entry_price'], 2),
                        'COSTS'       : cost,
                        'NET_PNL'     : round(pnl, 2),
                        'REGIME'      : regime_label,
                    })
                    to_exit.append(t)

            for t in to_exit: del portfolio[t]

            port_val = sum(pos['shares'] * prices.loc[d, t] for t, pos in portfolio.items())
            equity_history.append({'date': d, 'equity': cash + port_val})

    # =========================================================
    # RESULTS
    # =========================================================
    res = pd.DataFrame(equity_history).set_index('date')
    res.to_csv(CONFIG.EQUITY_LOG_FILE)

    # Trade log to CSV
    tlog = pd.DataFrame(trade_log)
    tlog.to_csv(CONFIG.TRADE_LOG_FILE, index=False)
    sells = tlog[tlog['TYPE'] == 'SELL']
    tsl_hits = sells[sells['REASON'].str.startswith('TSL')]
    print(f"\nTrade Log saved -> {CONFIG.TRADE_LOG_FILE}")
    print(f"  Total trades  : {len(tlog)}")
    print(f"  Total buys    : {len(tlog[tlog['TYPE']=='BUY'])}")
    print(f"  Monthly sells : {len(sells[sells['REASON']=='MONTHLY_REBALANCE'])}")
    print(f"  TSL hits      : {len(tsl_hits)}")
    if len(sells) > 0:
        avg_pnl = sells['NET_PNL'].mean()
        win_rate = (sells['NET_PNL'] > 0).mean()
        print(f"  Avg Net P&L/trade: INR {avg_pnl:,.0f}")
        print(f"  Win Rate          : {win_rate:.1%}")

    # Performance summary
    initial = CONFIG.START_CAPITAL
    final   = res.iloc[-1]['equity']
    years   = (res.index[-1] - res.index[0]).days / 365.25
    cagr    = (final / initial) ** (1 / years) - 1
    res['peak']     = res['equity'].cummax()
    res['drawdown'] = (res['equity'] - res['peak']) / res['peak']
    max_dd  = res['drawdown'].min()
    daily_ret = res['equity'].pct_change().dropna()
    vol     = daily_ret.std() * np.sqrt(252)
    sharpe  = (cagr / vol) if vol > 0 else 0

    print("\n" + "="*45)
    print(f"  BACKTEST PERFORMANCE SUMMARY  ({CONFIG.TSL_THRESHOLD*100:.0f}% TSL)")
    print("="*45)
    print(f"  Start Date   : {res.index[0].date()}")
    print(f"  End Date     : {res.index[-1].date()}")
    print(f"  Start Capital: INR {initial:>12,.0f}")
    print(f"  End Capital  : INR {final:>12,.0f}")
    print(f"  Total Return : {(final/initial - 1):>10.2%}")
    print(f"  CAGR         : {cagr:>10.2%}")
    print(f"  Max Drawdown : {max_dd:>10.2%}")
    print(f"  Annual Vol   : {vol:>10.2%}")
    print(f"  Sharpe Ratio : {sharpe:>10.2f}")
    print("="*45)

    # Benchmark
    print("\nFetching benchmark (^CRSLDX / Nifty 500)...")
    bench_ok = False
    try:
        bench_raw = yf.download(CONFIG.BENCHMARK_TICKER,
                                start=res.index[0].strftime("%Y-%m-%d"),
                                end=res.index[-1].strftime("%Y-%m-%d"),
                                auto_adjust=True, progress=False)
        bench = bench_raw["Close"].squeeze().dropna()
        bench.index = pd.to_datetime(bench.index).tz_localize(None)
        bench = bench.reindex(res.index, method="ffill")
        bench = (bench / bench.iloc[0]) * CONFIG.START_CAPITAL
        b_final  = bench.iloc[-1]
        b_cagr   = (b_final / initial) ** (1 / years) - 1
        b_ret    = bench.pct_change().dropna()
        b_dd     = ((bench - bench.cummax()) / bench.cummax()).min()
        b_vol    = b_ret.std() * np.sqrt(252)
        b_sharpe = (b_cagr / b_vol) if b_vol > 0 else 0
        print(f"  Benchmark CAGR  : {b_cagr:.2%}")
        print(f"  Benchmark Max DD: {b_dd:.2%}")
        print(f"  Benchmark Sharpe: {b_sharpe:.2f}")
        bench_ok = True
    except Exception as e:
        print(f"  [WARN] {e}")

    return {
        "label"   : ranking_metric,
        "equity"  : res["equity"],
        "cagr"    : cagr,
        "max_dd"  : max_dd,
        "vol"     : vol,
        "sharpe"  : sharpe,
        "bench"   : bench if bench_ok else None,
        "b_cagr"  : b_cagr if bench_ok else None,
        "b_dd"    : b_dd   if bench_ok else None,
        "b_sharpe": b_sharpe if bench_ok else None,
    }


# =========================================================
# COMPARISON RUNNER
# =========================================================
if __name__ == "__main__":
    COLORS = {"WT_SHARPE": "steelblue", "SR2_BLEND": "mediumseagreen"}
    LABELS = {"WT_SHARPE": "Wtd Sharpe", "SR2_BLEND": "SR2 Blend"}

    results = {}
    for metric in ["WT_SHARPE", "SR2_BLEND"]:
        print(f"\n{'='*55}")
        print(f"  Running backtest with ranking metric: {metric}")
        print(f"{'='*55}")
        results[metric] = run_backtest(ranking_metric=metric)

    # Side-by-side equity curve
    fig, ax = plt.subplots(figsize=(15, 7))

    bench_plotted = False
    for metric, res in results.items():
        ax.plot(res["equity"], label=f"Strategy ({LABELS[metric]})",
                linewidth=2, color=COLORS[metric])
        if not bench_plotted and res["bench"] is not None:
            ax.plot(res["bench"], label="Benchmark (Nifty 500)",
                    linewidth=1.5, alpha=0.7, color="darkorange", linestyle="--")
            bench_plotted = True

    ax.axhline(CONFIG.START_CAPITAL, color="grey", linewidth=0.8, linestyle=":")
    ax.set_title("ETF Momentum Strategy: Wtd Sharpe vs SR2 Blend  |  10pct TSL  |  Monthly Rebalance")
    ax.set_ylabel("Equity Value (INR)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(CONFIG.CHART_FILE, dpi=150)
    print(f"\nComparison chart saved -> {CONFIG.CHART_FILE}")

    # Print side-by-side summary
    print(f"\n{'='*60}")
    print(f"  {'Metric':<22} {'Wtd Sharpe':>14} {'SR2 Blend':>14}")
    print(f"  {'-'*50}")
    for key, label in [("cagr","CAGR"), ("max_dd","Max Drawdown"),
                        ("vol","Annual Vol"), ("sharpe","Sharpe Ratio")]:
        ws = results["WT_SHARPE"][key]
        sr = results["SR2_BLEND"][key]
        if key in ("cagr","max_dd","vol"):
            print(f"  {label:<22} {ws:>13.2%} {sr:>13.2%}")
        else:
            print(f"  {label:<22} {ws:>13.2f} {sr:>13.2f}")
    bm = results["WT_SHARPE"]
    if bm["b_cagr"] is not None:
        print(f"  {'-'*50}")
        print(f"  {'Benchmark CAGR':<22} {bm['b_cagr']:>13.2%} {'(same)':>14}")
        print(f"  {'Benchmark Max DD':<22} {bm['b_dd']:>13.2%} {'(same)':>14}")
        print(f"  {'Benchmark Sharpe':<22} {bm['b_sharpe']:>13.2f} {'(same)':>14}")
    print(f"{'='*60}")
