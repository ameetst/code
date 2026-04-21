import os
import re

src = r'C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\etf_backtest.py'
content = open(src, encoding='utf-8').read()

out_dir = r'C:\Users\ameet\Documents\Github\code\momentum\ETFs\backtest results\run_2026-04-18_weekly_rebalance'
os.makedirs(out_dir, exist_ok=True)

content = content.replace('CONFIG.OUT_DIR        = ""', f'CONFIG.OUT_DIR        = r"{out_dir}"')
content = content.replace('CHART_FILE          = "equity_curve.png"', f'CHART_FILE          = r"{out_dir}\\equity_curve.png"')
content = content.replace('EQUITY_LOG_FILE     = "backtest_equity.csv"', f'EQUITY_LOG_FILE     = r"{out_dir}\\backtest_equity.csv"')
content = content.replace('TRADE_LOG_FILE      = "backtest_trade_log.csv"', f'TRADE_LOG_FILE      = r"{out_dir}\\backtest_trade_log.csv"')

content = content.replace('Monthly Rebalance', 'Weekly Mon Rebal')

logic_replace = """
    # >>> WEEKLY REBALANCE LOGIC <<<
    mondays = [d for d in all_dates if d.weekday() == 0]
    
    cash        = CONFIG.START_CAPITAL
    portfolio   = {}
    trade_log   = []
    equity_history = []
    
    print(f"Backtest starting: {all_dates[0].date()} -> {all_dates[-1].date()}")

    for idx, start_date in enumerate(mondays):
        # We use the previous trading day's close for regime/rank calculation to avoid lookahead.
        prev_dates = all_dates[all_dates < start_date]
        if len(prev_dates) == 0:
            continue
            
        prev_trading_day = prev_dates[-1]
        
        hist_prices = prices.loc[:prev_trading_day]
        if len(hist_prices) < CONFIG.WINDOW_6M:
            continue
            
        regime_label, active_slots = get_regime(regime_series, prev_trading_day)
"""

content = re.sub(
    r'    month_groups = all_dates.*?regime_label, active_slots = get_regime\(regime_series, prev_month_end\)',
    logic_replace,
    content,
    flags=re.DOTALL
)

content = content.replace(
    'month_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]',
    'next_start = mondays[idx+1] if idx+1 < len(mondays) else all_dates[-1] + pd.Timedelta(days=1)\n        month_dates = all_dates[(all_dates >= start_date) & (all_dates < next_start)]'
)

content = content.replace('"REASON"      : "MONTHLY_REBALANCE",', '"REASON"      : "WEEKLY_REBALANCE",')
content = content.replace("Monthly sells : {len(sells[sells['REASON']=='MONTHLY_REBALANCE'])}", "Weekly sells  : {len(sells[sells['REASON']=='WEEKLY_REBALANCE'])}")

dst = os.path.join(out_dir, 'etf_backtest_weekly.py')
with open(dst, 'w', encoding='utf-8') as f:
    f.write(content)

print(f'Patched file saved to {dst}')
