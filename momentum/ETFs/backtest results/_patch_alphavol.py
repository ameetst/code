import os
import re

src = r"etf_backtest.py"
content = open(src, encoding="utf-8").read()

# Add CONFIG
content = content.replace("WINDOW_3M              = 63", "WINDOW_3M              = 63\n    WINDOW_12M             = 252")

# Replace rank_etfs
new_rank = """def rank_etfs(hist_prices: pd.DataFrame, meta: pd.DataFrame, nifty_series: pd.Series) -> pd.DataFrame:
    rows = []
    window = CONFIG.WINDOW_12M
    nifty_px = nifty_series.dropna()
    if len(nifty_px) < window * 0.90:
        return pd.DataFrame(columns=["TICKER", "NAME", "SECTOR", "COMPOSITE", "ALPHA", "INV_VOL"])
        
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
        if (high_52 - close_p) / high_52 <= CONFIG.MAX_DRAWDOWN_FROM_HIGH:
            alpha_data[t] = annual_alpha
            vol_data[t] = inv_vol
            rows.append({"TICKER": t, "NAME": m["ETF_NAME"], "SECTOR": classify_sector(m["ETF_NAME"], t)})
            
    if not rows:
        return pd.DataFrame(columns=["TICKER", "NAME", "SECTOR", "COMPOSITE", "ALPHA", "INV_VOL"])
        
    df = pd.DataFrame(rows)
    df["ALPHA"] = df["TICKER"].map(alpha_data)
    df["INV_VOL"] = df["TICKER"].map(vol_data)
    
    def z_score(series):
        mu, sd = series.mean(), series.std(ddof=1)
        return (series - mu) / sd if sd > 0 else series * 0.0
        
    df["Z_ALPHA"]   = z_score(df["ALPHA"])
    df["Z_INV_VOL"] = z_score(df["INV_VOL"])
    df["COMPOSITE"] = 0.5 * df["Z_ALPHA"] + 0.5 * df["Z_INV_VOL"]
    
    return df.sort_values("COMPOSITE", ascending=False).reset_index(drop=True)
"""

content = re.sub(r'def rank_etfs\(.*?\n((?!def select_top_n|# =========================================================\n# BACKTEST).)*', new_rank + '\n\n', content, flags=re.DOTALL)
content = content.replace("rank_df  = rank_etfs(hist_prices, meta)", "rank_df  = rank_etfs(hist_prices, meta, regime_series)")
content = content.replace(', "metric": metric', ', "metric": "COMPOSITE"')

content = content.replace(
    'if metric not in rank_df.columns: metric = "WT_SHARPE"',
    'metric = "COMPOSITE"\n        if "COMPOSITE" not in rank_df.columns:\n            print(f"[{start_date.date()}] Not enough data for Alpha/Vol yet")\n            equity_history.append({"date": start_date, "equity": cash})\n            prev_regime = regime_label\n            continue'
)

# Replace print label for output chart
content = content.replace('{ranking_metric}', 'Alpha+LowVol')
content = content.replace('run_backtest("WT_SHARPE")', 'run_backtest()')
content = content.replace('def run_backtest(ranking_metric="WT_SHARPE"):', 'def run_backtest():')


# Hardcode output paths
out_dir = r"C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\run_2026-04-18_alpha_lowvol"
os.makedirs(out_dir, exist_ok=True)
content = content.replace('CONFIG.CHART_FILE', f'r"{out_dir}\\equity_curve.png"')
content = content.replace('CONFIG.TRADE_LOG_FILE', f'r"{out_dir}\\backtest_trade_log.csv"')
content = content.replace('CONFIG.EQUITY_LOG_FILE', f'r"{out_dir}\\backtest_equity.csv"')

# Output snippet modification - April 2021 comparison
filter_logic = """
    cut_date = pd.to_datetime('2021-04-01')
    if cut_date in res.index:
        start_val = res.loc[cut_date, 'equity']
        res2 = res.loc[cut_date:].copy()
        
        if bench_ok:
            bx = bench.loc[cut_date:].copy()
            b_initial = bx.iloc[0]
            bx = (bx / b_initial) * start_val
            
        initial = float(start_val)
        final = float(res2.iloc[-1]['equity'])
        years = (res2.index[-1] - res2.index[0]).days / 365.25
        cagr = (final / initial) ** (1 / years) - 1
        res2['peak'] = res2['equity'].cummax()
        max_dd = ((res2['equity'] - res2['peak']) / res2['peak']).min()
        vol = res2['equity'].pct_change().dropna().std() * np.sqrt(252)
        sharpe = cagr / vol if vol > 0 else 0
        
        if bench_ok:
            b_final = float(bx.iloc[-1])
            b_cagr = (b_final / initial) ** (1 / years) - 1
            b_vol = bx.pct_change().dropna().std() * np.sqrt(252)
            b_dd = ((bx - bx.cummax()) / bx.cummax()).min()
            b_sharpe = b_cagr / b_vol if b_vol > 0 else 0
            bench = bx
            
        res = res2

    print(f'\\n==> CLIPPED COMPARISON (Started {res.index[0].date()}) <==')
"""
content = re.sub(
    r'initial   = CONFIG\.START_CAPITAL.*?(?=print\("\\n" \+ "="\*45\))',
    filter_logic + '\n    ',
    content,
    flags=re.DOTALL
)

with open(os.path.join(out_dir, "etf_backtest_alphavol.py"), "w", encoding="utf-8") as f:
    f.write(content)

print("Patching complete -> " + os.path.join(out_dir, "etf_backtest_alphavol.py"))
