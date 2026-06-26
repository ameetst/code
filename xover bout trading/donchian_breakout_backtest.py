import yfinance as yf
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
INITIAL_CAPITAL = 1000000.0
MAX_POSITIONS = 10
RISK_PCT = 0.02

OUT_DIR = "donchian_breakout_results"

def load_data():
    if not os.path.exists(MARKET_DATA_FILE):
        print(f"Error: {MARKET_DATA_FILE} not found. Please run milt25 backtest to download data first.")
        return None, None
        
    print(f"Loading cached market data from {MARKET_DATA_FILE}...")
    market_data = pd.read_pickle(MARKET_DATA_FILE)
    
    # Get date range from market data to match
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=365*7)
    
    if os.path.exists(INDEX_FILE):
        print(f"Loading cached index data from {INDEX_FILE}...")
        index_data = pd.read_pickle(INDEX_FILE)
    else:
        print("Downloading Nifty 500 (^CRSLDX) index data...")
        index_data = yf.download("^CRSLDX", start=start_date, end=end_date)
        index_data.to_pickle(INDEX_FILE)
        
    return market_data, index_data

def calculate_daily_indicators(df):
    df = df.copy()
    
    # 20-day High, shifted 1 day so it doesn't include today's high
    df['High_20D'] = df['High'].rolling(20).max().shift(1)
    
    # 10-day Low, shifted 1 day so it doesn't include today's low
    df['Low_10D'] = df['Low'].rolling(10).min().shift(1)
    
    # Entry Signal
    df['Entry_Signal'] = df['Close'] > df['High_20D']
    
    return df

def run_simulation(indicators, index_df, all_dates):
    cash = INITIAL_CAPITAL
    positions = {} # ticker -> {shares, entry_price, entry_date, initial_stop}
    equity_curve = {}
    trade_log = []
    
    print("Starting day-by-day simulation...")
    
    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i+1] # T+1 Open execution
            
        tickers_to_exit = []
        
        # 1. CHECK EXITS
        for ticker, pos in positions.items():
            if eval_date not in indicators[ticker].index:
                continue
                
            current_close = indicators[ticker].loc[eval_date, 'Close']
            low_10d = indicators[ticker].loc[eval_date, 'Low_10D']
            
            # Stop loss / Trailing stop when price closes below 10-day Low
            if pd.notna(low_10d) and current_close < low_10d:
                # We will execute at next day's Open
                if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Open']):
                    exit_exec_price = indicators[ticker].loc[exec_date, 'Open']
                else:
                    exit_exec_price = current_close # Fallback
                tickers_to_exit.append((ticker, exit_exec_price, "Close Below 10D Low"))
                
        # Execute exits
        for ticker, exit_price, reason in tickers_to_exit:
            pos = positions.pop(ticker)
            proceeds = pos['shares'] * exit_price
            cash += proceeds
            trade_log.append({
                'Ticker': ticker.replace('.NS', ''),
                'Entry Date': pos['entry_date'].date(),
                'Entry Price': pos['entry_price'],
                'Exit Date': exec_date.date(),
                'Exit Price': exit_price,
                'Return (%)': (exit_price - pos['entry_price']) / pos['entry_price'] * 100,
                'Reason': reason
            })
            
        # Current Equity for position sizing
        current_equity = cash
        for t, p in positions.items():
            if eval_date in indicators[t].index:
                current_equity += p['shares'] * indicators[t].loc[eval_date, 'Close']
            else:
                current_equity += p['shares'] * p['entry_price']
                
        # 2. CHECK ENTRIES
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
                    low_10d_at_entry = row['Low_10D']
                    
                    if pd.notna(low_10d_at_entry) and entry_exec_price > low_10d_at_entry:
                        potential_entries.append((ticker, entry_exec_price, low_10d_at_entry))
                    
            # For simplicity, if we have more signals than slots, just pick the first ones
            for ticker, entry_price, low_10d in potential_entries[:open_slots]:
                risk_amount = current_equity * RISK_PCT
                stop_distance = entry_price - low_10d
                
                # Prevent division by zero or negative stop distance
                if stop_distance <= 0:
                    continue
                    
                shares = int(risk_amount / stop_distance)
                
                # Check if we can afford these shares, cap by available cash
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
                        'initial_stop': low_10d
                    }
                    
        # 3. MARK TO MARKET using exec_date's close
        mtm = cash
        for ticker, pos in positions.items():
            if exec_date in indicators[ticker].index:
                mtm += pos['shares'] * indicators[ticker].loc[exec_date, 'Close']
            else:
                mtm += pos['shares'] * pos['entry_price'] # fallback
        equity_curve[exec_date] = mtm
        
    return pd.DataFrame(trade_log), pd.Series(equity_curve)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    
    market_data, index_data = load_data()
    if market_data is None: return
    
    indicators = {}
    print("Calculating Daily Indicators for Donchian Breakout Strategy...")
    
    total_tickers = len(market_data.columns.levels[0])
    for idx, ticker in enumerate(market_data.columns.levels[0]):
        if idx % 50 == 0:
            print(f"Processed {idx}/{total_tickers} tickers...")
            
        df = market_data[ticker].copy()
        df.dropna(subset=['Close', 'High', 'Low'], inplace=True)
        if len(df) < 50:
            continue
            
        try:
            indicators[ticker] = calculate_daily_indicators(df)
        except Exception as e:
            pass
            
    # Common dates (last 5 years)
    all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365*5))
    all_dates = [d for d in all_dates if d >= cutoff_date]
    
    trade_log_df, strategy_equity = run_simulation(indicators, index_data, all_dates)
    
    if not trade_log_df.empty:
        trade_log_df.to_csv(f"{OUT_DIR}/donchian_trade_log.csv", index=False)
        print(f"\nTrade log saved. Total trades: {len(trade_log_df)}")
        win_rate = len(trade_log_df[trade_log_df['Return (%)'] > 0]) / len(trade_log_df) * 100
        avg_win = trade_log_df[trade_log_df['Return (%)'] > 0]['Return (%)'].mean()
        avg_loss = trade_log_df[trade_log_df['Return (%)'] <= 0]['Return (%)'].mean()
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Average Winner: {avg_win:.2f}% | Average Loser: {avg_loss:.2f}%")
        
    # Baseline Buy & Hold Nifty 500
    print("Calculating Baseline Buy & Hold (Nifty 500)...")
    
    if isinstance(index_data.columns, pd.MultiIndex):
        index_close = index_data['Close'].iloc[:, 0]
    else:
        index_close = index_data['Close']
        
    index_eval = index_close.loc[(index_close.index >= cutoff_date) & (index_close.index <= all_dates[-1])]
    if not index_eval.empty:
        baseline_equity = index_eval / index_eval.iloc[0] * INITIAL_CAPITAL
    else:
        baseline_equity = pd.Series()
        
    # Plotting
    plt.figure(figsize=(12, 6))
    
    if not strategy_equity.empty and not baseline_equity.empty:
        strat_norm = strategy_equity / strategy_equity.iloc[0] * 100
        base_norm = baseline_equity / baseline_equity.iloc[0] * 100
        
        plt.plot(strat_norm.index, strat_norm.values, label='Donchian Breakout Strategy', color='green')
        plt.plot(base_norm.index, base_norm.values, label='Nifty 500 (Buy & Hold)', color='gray', alpha=0.7)
        
        years = (strat_norm.index[-1] - strat_norm.index[0]).days / 365.25
        strat_cagr = ((strat_norm.iloc[-1] / strat_norm.iloc[0]) ** (1/years) - 1) * 100
        base_cagr = ((base_norm.iloc[-1] / base_norm.iloc[0]) ** (1/years) - 1) * 100
        
        plt.title(f'Donchian Breakout vs Nifty 500 (Last 5 Years)\nBreakout CAGR: {strat_cagr:.1f}% | Nifty 500 CAGR: {base_cagr:.1f}%')
        plt.xlabel('Date')
        plt.ylabel('Normalized Equity (Base 100)')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        plot_path = f"{OUT_DIR}/donchian_vs_index.png"
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"\nEquity curve plot saved to: {plot_path}")
    else:
        print("Not enough data to plot.")

if __name__ == "__main__":
    main()
