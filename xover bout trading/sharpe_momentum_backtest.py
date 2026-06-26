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
MAX_POSITIONS = 20
REBALANCE_DAYS = 21
LOOKBACK_DAYS = 63

OUT_DIR = "sharpe_momentum_results"

def load_data():
    if not os.path.exists(MARKET_DATA_FILE):
        print(f"Error: {MARKET_DATA_FILE} not found. Please run milt25 backtest to download data first.")
        return None, None
        
    print(f"Loading cached market data from {MARKET_DATA_FILE}...")
    market_data = pd.read_pickle(MARKET_DATA_FILE)
    
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
    
    # Calculate daily returns
    df['Daily_Return'] = df['Close'].pct_change()
    
    # Calculate rolling 63-day Mean and Volatility
    # Shift by 1 so the metric on day T only includes data up to T-1
    roll_mean = df['Daily_Return'].rolling(LOOKBACK_DAYS).mean().shift(1)
    roll_std = df['Daily_Return'].rolling(LOOKBACK_DAYS).std().shift(1)
    
    # Calculate 3M Sharpe (annualized for scaling, though ranking is the same)
    # Adding a tiny constant to avoid division by zero
    df['3M_Sharpe'] = (roll_mean / (roll_std + 1e-8)) * np.sqrt(252)
    
    return df

def run_simulation(indicators, index_data, all_dates):
    cash = INITIAL_CAPITAL
    positions = {} # ticker -> {shares, entry_price, entry_date}
    equity_curve = {}
    trade_log = []
    
    print("Starting rotational simulation...")
    
    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i+1] # T+1 Open execution
        
        # Calculate Current Equity
        current_equity = cash
        for t, p in positions.items():
            if eval_date in indicators[t].index and pd.notna(indicators[t].loc[eval_date, 'Close']):
                current_equity += p['shares'] * indicators[t].loc[eval_date, 'Close']
            else:
                current_equity += p['shares'] * p['entry_price']
                
        # Time to Rebalance?
        if i % REBALANCE_DAYS == 0 and i >= LOOKBACK_DAYS:
            # 1. Rank universe
            current_scores = []
            for ticker, df in indicators.items():
                if eval_date in df.index:
                    score = df.loc[eval_date, '3M_Sharpe']
                    if pd.notna(score):
                        current_scores.append((ticker, score))
            
            # Sort by Sharpe descending
            current_scores.sort(key=lambda x: x[1], reverse=True)
            
            # Identify Top N
            top_tickers = [x[0] for x in current_scores[:MAX_POSITIONS]]
            
            # 2. Sell positions that fell out of Top N
            tickers_to_sell = [t for t in positions.keys() if t not in top_tickers]
            
            for ticker in tickers_to_sell:
                df = indicators[ticker]
                # Exec price
                if exec_date in df.index and pd.notna(df.loc[exec_date, 'Open']):
                    exit_price = df.loc[exec_date, 'Open']
                else:
                    exit_price = df.loc[eval_date, 'Close'] if eval_date in df.index else positions[ticker]['entry_price']
                
                pos = positions.pop(ticker)
                proceeds = pos['shares'] * exit_price
                cash += proceeds
                current_equity += (proceeds - (pos['shares'] * df.loc[eval_date, 'Close'])) if eval_date in df.index else 0
                
                trade_log.append({
                    'Ticker': ticker.replace('.NS', ''),
                    'Entry Date': pos['entry_date'].date(),
                    'Entry Price': pos['entry_price'],
                    'Exit Date': exec_date.date(),
                    'Exit Price': exit_price,
                    'Return (%)': (exit_price - pos['entry_price']) / pos['entry_price'] * 100,
                    'Reason': 'Rotated Out'
                })
            
            # Re-calculate equity after sells just to be precise with cash
            current_equity = cash
            for t, p in positions.items():
                if eval_date in indicators[t].index and pd.notna(indicators[t].loc[eval_date, 'Close']):
                    current_equity += p['shares'] * indicators[t].loc[eval_date, 'Close']
                else:
                    current_equity += p['shares'] * p['entry_price']
            
            # 3. Buy/Rebalance into Top N
            # Equal weight all positions
            target_alloc_per_position = current_equity / MAX_POSITIONS
            
            for ticker in top_tickers:
                if ticker not in positions:
                    df = indicators[ticker]
                    if exec_date in df.index and pd.notna(df.loc[exec_date, 'Open']):
                        entry_price = df.loc[exec_date, 'Open']
                        shares = int(target_alloc_per_position / entry_price)
                        
                        cost = shares * entry_price
                        if cost > cash:
                            shares = int(cash / entry_price)
                            cost = shares * entry_price
                            
                        if shares > 0:
                            cash -= cost
                            positions[ticker] = {
                                'shares': shares,
                                'entry_price': entry_price,
                                'entry_date': exec_date
                            }
                # (We ignore sizing adjustments for existing positions to save trading costs
                # and simplify the model, treating it as a pure rotational system)
                
        # Record equity at the close of exec_date
        mtm = cash
        for ticker, pos in positions.items():
            if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Close']):
                mtm += pos['shares'] * indicators[ticker].loc[exec_date, 'Close']
            else:
                mtm += pos['shares'] * pos['entry_price']
        equity_curve[exec_date] = mtm
        
    # Close out any remaining positions at the end of simulation
    final_date = all_dates[-1]
    for ticker in list(positions.keys()):
        df = indicators[ticker]
        exit_price = df.loc[final_date, 'Close'] if final_date in df.index else positions[ticker]['entry_price']
        pos = positions.pop(ticker)
        trade_log.append({
            'Ticker': ticker.replace('.NS', ''),
            'Entry Date': pos['entry_date'].date(),
            'Entry Price': pos['entry_price'],
            'Exit Date': final_date.date(),
            'Exit Price': exit_price,
            'Return (%)': (exit_price - pos['entry_price']) / pos['entry_price'] * 100,
            'Reason': 'End of Simulation'
        })
        
    return pd.DataFrame(trade_log), pd.Series(equity_curve)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    
    market_data, index_data = load_data()
    if market_data is None: return
    
    indicators = {}
    print("Calculating Daily Indicators for Sharpe Momentum Strategy...")
    
    total_tickers = len(market_data.columns.levels[0])
    for idx, ticker in enumerate(market_data.columns.levels[0]):
        if idx % 50 == 0:
            print(f"Processed {idx}/{total_tickers} tickers...")
            
        df = market_data[ticker].copy()
        df.dropna(subset=['Close'], inplace=True)
        if len(df) < LOOKBACK_DAYS + 10:
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
        trade_log_df.to_csv(f"{OUT_DIR}/sharpe_trade_log.csv", index=False)
        print(f"\nTrade log saved. Total trades: {len(trade_log_df)}")
        win_rate = len(trade_log_df[trade_log_df['Return (%)'] > 0]) / len(trade_log_df) * 100
        avg_win = trade_log_df[trade_log_df['Return (%)'] > 0]['Return (%)'].mean() if len(trade_log_df[trade_log_df['Return (%)'] > 0]) > 0 else 0
        avg_loss = trade_log_df[trade_log_df['Return (%)'] <= 0]['Return (%)'].mean() if len(trade_log_df[trade_log_df['Return (%)'] <= 0]) > 0 else 0
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
        
        plt.plot(strat_norm.index, strat_norm.values, label='Sharpe Momentum Strategy', color='blue')
        plt.plot(base_norm.index, base_norm.values, label='Nifty 500 (Buy & Hold)', color='gray', alpha=0.7)
        
        years = (strat_norm.index[-1] - strat_norm.index[0]).days / 365.25
        strat_cagr = ((strat_norm.iloc[-1] / strat_norm.iloc[0]) ** (1/years) - 1) * 100
        base_cagr = ((base_norm.iloc[-1] / base_norm.iloc[0]) ** (1/years) - 1) * 100
        
        plt.title(f'Sharpe Momentum vs Nifty 500 (Last 5 Years)\nMomentum CAGR: {strat_cagr:.1f}% | Nifty 500 CAGR: {base_cagr:.1f}%')
        plt.xlabel('Date')
        plt.ylabel('Normalized Equity (Base 100)')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        plot_path = f"{OUT_DIR}/sharpe_vs_index.png"
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"\nEquity curve plot saved to: {plot_path}")
    else:
        print("Not enough data to plot.")

if __name__ == "__main__":
    main()
