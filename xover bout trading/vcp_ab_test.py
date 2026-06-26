import pandas as pd
import numpy as np
import datetime
import os
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

warnings.filterwarnings('ignore')

MARKET_DATA_FILE = "milt25_historical_data.pkl"
INDEX_FILE = "nifty500_data.pkl"
INITIAL_CAPITAL = 200000.0
MAX_POSITIONS = 20
RISK_PCT = 0.02
STOP_LOSS_PCT = 0.08

OUT_DIR = "vcp_breakout_results"

# Define test variants: (name, range_pct, rsi_min, rsi_max, dryup_enabled, dryup_window, dryup_pct, holding_enabled)
VARIANTS = [
    ("A: Baseline (Original)",        0.05, 40, 60, False, 5, 0.70, False),
    ("B: RSI 35-55 only",             0.05, 35, 55, False, 5, 0.70, False),
    ("C: Range 8% only",              0.08, 40, 60, False, 5, 0.70, False),
    ("D: Range 12% only",             0.12, 40, 60, False, 5, 0.70, False),
    ("E: Vol Dryup 0.7x only",        0.05, 40, 60, True,  5, 0.70, False),
    ("F: Holding Above Lows only",    0.05, 40, 60, False, 5, 0.70, True),
    ("G: RSI 35-55 + Dryup 0.7x",     0.05, 35, 55, True,  5, 0.70, False),
    ("H: Range 8% + Dryup 0.7x",      0.08, 40, 60, True,  5, 0.70, False),
    ("I: Best Combo (8%+RSI35-55+Dryup+Holding)", 0.08, 35, 55, True, 5, 0.70, True),
]

def calculate_indicators(df, range_pct, rsi_min, rsi_max, dryup_enabled, dryup_window, dryup_pct, holding_enabled):
    df = df.copy()
    
    df['10D_High'] = df['High'].rolling(10).max().shift(1)
    df['10D_Low'] = df['Low'].rolling(10).min().shift(1)
    df['Range_10D'] = (df['10D_High'] - df['10D_Low']) / (df['10D_Low'] + 1e-8)
    
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-8)
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    
    df['Vol_20D_Avg'] = df['Volume'].rolling(20).mean().shift(1)
    df['Vol_ND_Avg'] = df['Volume'].rolling(dryup_window).mean().shift(1)
    df['Vol_Surge'] = df['Volume'] / (df['Vol_20D_Avg'] + 1e-8)
    
    df['Price_Up'] = df['Close'] > df['Close'].shift(1)
    
    df['Low_20D'] = df['Low'].rolling(20).min().shift(1)
    df['Prev_Close'] = df['Close'].shift(1)
    
    cond_range = df['Range_10D'] < range_pct
    cond_rsi = (df['RSI'] >= rsi_min) & (df['RSI'] <= rsi_max)
    cond_vol = df['Vol_Surge'] >= 3.0
    cond_price = df['Price_Up']
    
    cond_dryup = df['Vol_ND_Avg'] < (df['Vol_20D_Avg'] * dryup_pct) if dryup_enabled else True
    cond_holding = df['Prev_Close'] > df['Low_20D'] if holding_enabled else True
    
    df['Entry_Signal'] = cond_range & cond_rsi & cond_vol & cond_price & cond_dryup & cond_holding
    
    return df

def run_simulation(indicators, all_dates):
    cash = INITIAL_CAPITAL
    positions = {}
    equity_curve = {}
    trade_log = []
    
    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i+1]
        
        tickers_to_exit = []
        
        for ticker, pos in positions.items():
            if eval_date not in indicators[ticker].index:
                continue
            low = indicators[ticker].loc[eval_date, 'Low']
            close = indicators[ticker].loc[eval_date, 'Close']
            ema_21 = indicators[ticker].loc[eval_date, 'EMA_21']
            
            if low <= pos['sl']:
                tickers_to_exit.append((ticker, pos['sl'], "Stop Loss", eval_date))
            elif pd.notna(ema_21) and close < ema_21:
                if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Open']):
                    exit_exec_price = indicators[ticker].loc[exec_date, 'Open']
                else:
                    exit_exec_price = close
                tickers_to_exit.append((ticker, exit_exec_price, "Trailing Stop (21EMA)", exec_date))
                
        for ticker, exit_price, reason, exit_date_actual in tickers_to_exit:
            pos = positions.pop(ticker)
            proceeds = pos['shares'] * exit_price
            cash += proceeds
            trade_log.append({
                'Return (%)': (exit_price - pos['entry_price']) / pos['entry_price'] * 100,
                'Reason': reason
            })
            
        current_equity = cash
        for t, p in positions.items():
            if eval_date in indicators[t].index and pd.notna(indicators[t].loc[eval_date, 'Close']):
                current_equity += p['shares'] * indicators[t].loc[eval_date, 'Close']
            else:
                current_equity += p['shares'] * p['entry_price']
                
        open_slots = MAX_POSITIONS - len(positions)
        if open_slots > 0:
            potential_entries = []
            for ticker, df in indicators.items():
                if ticker in positions: continue
                if eval_date not in df.index: continue
                row = df.loc[eval_date]
                if exec_date not in df.index or pd.isna(df.loc[exec_date, 'Open']):
                    continue
                if row['Entry_Signal']:
                    entry_exec_price = df.loc[exec_date, 'Open']
                    potential_entries.append((ticker, entry_exec_price, row['Vol_Surge']))
                    
            potential_entries.sort(key=lambda x: x[2], reverse=True)
            
            for ticker, entry_price, _ in potential_entries[:open_slots]:
                risk_amount = current_equity * RISK_PCT
                stop_distance_price = entry_price * STOP_LOSS_PCT
                if stop_distance_price <= 0: continue
                shares = int(risk_amount / stop_distance_price)
                cost = shares * entry_price
                if cost > cash:
                    shares = int(cash / entry_price)
                    cost = shares * entry_price
                if shares > 0:
                    cash -= cost
                    positions[ticker] = {
                        'shares': shares,
                        'entry_price': entry_price,
                        'entry_date': exec_date,
                        'sl': entry_price * (1 - STOP_LOSS_PCT)
                    }
                    
        mtm = cash
        for ticker, pos in positions.items():
            if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Close']):
                mtm += pos['shares'] * indicators[ticker].loc[exec_date, 'Close']
            else:
                mtm += pos['shares'] * pos['entry_price']
        equity_curve[exec_date] = mtm
        
    return pd.DataFrame(trade_log), pd.Series(equity_curve)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    
    if not os.path.exists(MARKET_DATA_FILE):
        print(f"Error: {MARKET_DATA_FILE} not found.")
        return
        
    print(f"Loading cached market data from {MARKET_DATA_FILE}...")
    market_data = pd.read_pickle(MARKET_DATA_FILE)
    
    print(f"Loading cached index data from {INDEX_FILE}...")
    index_data = pd.read_pickle(INDEX_FILE)
    
    # Pre-process all ticker dataframes once
    print("Pre-processing ticker data...")
    clean_dfs = {}
    for idx, ticker in enumerate(market_data.columns.levels[0]):
        df = market_data[ticker].copy()
        df.dropna(subset=['Close', 'High', 'Low', 'Volume'], inplace=True)
        if len(df) >= 50:
            clean_dfs[ticker] = df
    print(f"  {len(clean_dfs)} tickers ready.\n")
    
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365*5))
    
    # Baseline index
    if isinstance(index_data.columns, pd.MultiIndex):
        index_close = index_data['Close'].iloc[:, 0]
    else:
        index_close = index_data['Close']
    
    results = []
    
    for variant in VARIANTS:
        name, range_pct, rsi_min, rsi_max, dryup_enabled, dryup_window, dryup_pct, holding_enabled = variant
        print(f"Running: {name}...")
        
        indicators = {}
        for ticker, df in clean_dfs.items():
            try:
                indicators[ticker] = calculate_indicators(df, range_pct, rsi_min, rsi_max, dryup_enabled, dryup_window, dryup_pct, holding_enabled)
            except:
                pass
                
        all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
        all_dates = [d for d in all_dates if d >= cutoff_date]
        
        trade_log_df, strategy_equity = run_simulation(indicators, all_dates)
        
        if strategy_equity.empty:
            print(f"  No equity data.\n")
            continue
            
        total_trades = len(trade_log_df)
        if total_trades > 0:
            winners = trade_log_df[trade_log_df['Return (%)'] > 0]
            losers = trade_log_df[trade_log_df['Return (%)'] <= 0]
            win_rate = len(winners) / total_trades * 100
            avg_win = winners['Return (%)'].mean() if len(winners) > 0 else 0
            avg_loss = losers['Return (%)'].mean() if len(losers) > 0 else 0
        else:
            win_rate = avg_win = avg_loss = 0
            
        years = (strategy_equity.index[-1] - strategy_equity.index[0]).days / 365.25
        total_return = (strategy_equity.iloc[-1] / INITIAL_CAPITAL - 1) * 100
        cagr = ((strategy_equity.iloc[-1] / strategy_equity.iloc[0]) ** (1/years) - 1) * 100
        max_dd = ((strategy_equity / strategy_equity.cummax()) - 1).min() * 100
        
        results.append({
            'Variant': name,
            'Trades': total_trades,
            'Win Rate': f"{win_rate:.1f}%",
            'Avg Win': f"{avg_win:.1f}%",
            'Avg Loss': f"{avg_loss:.1f}%",
            'Total Return': f"{total_return:.1f}%",
            'CAGR': f"{cagr:.1f}%",
            'Max Drawdown': f"{max_dd:.1f}%",
            'Final Equity': f"INR {strategy_equity.iloc[-1]:,.0f}"
        })
        print(f"  Trades: {total_trades} | Win: {win_rate:.1f}% | Return: {total_return:.1f}% | MaxDD: {max_dd:.1f}%\n")
    
    # Print comparison table
    results_df = pd.DataFrame(results)
    print("\n" + "="*120)
    print("SYSTEMATIC A/B TEST RESULTS")
    print("="*120)
    print(results_df.to_string(index=False))
    
    # Save to CSV
    results_df.to_csv(f"{OUT_DIR}/ab_test_results.csv", index=False)
    print(f"\nResults saved to {OUT_DIR}/ab_test_results.csv")

if __name__ == "__main__":
    main()
