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
ALLOCATION_PCT = 0.10
STOP_LOSS_PCT = 0.10

OUT_DIR = "breakout_backtest_results"

def fast_rsquared(y_series):
    # Very fast R-squared calculation using numpy
    y = y_series.values
    mask = ~np.isnan(y)
    if mask.sum() < 10:
        return np.nan
    y_clean = y[mask]
    x = np.arange(len(y_clean))
    # Pearson correlation coefficient squared
    r_matrix = np.corrcoef(x, y_clean)
    if r_matrix.shape == (2, 2):
        return r_matrix[0, 1] ** 2
    return np.nan

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

def calculate_index_indicators(index_data):
    if isinstance(index_data.columns, pd.MultiIndex):
        df = pd.DataFrame({'Close': index_data['Close'].iloc[:, 0]})
    else:
        df = index_data[['Close']].copy()
        
    df.dropna(inplace=True)
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['Regime_Bullish'] = df['Close'] > df['EMA_50']
    return df

def calculate_daily_indicators(df, index_df):
    df = df.copy()
    
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    df['High_52W'] = df['High'].rolling(252, min_periods=100).max()
    df['P52H_Ratio'] = df['Close'] / df['High_52W']
    df['ROC_3M'] = df['Close'] / df['Close'].shift(63) - 1
    
    # User's conditions:
    # 1. Price > 50EMA > 200EMA
    cond_trend1 = (df['Close'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200']) & (df['EMA_200'] > 0)
    # 2. Price within 5% of 52week high
    cond_near_high = df['Close'] >= (df['High_52W'] * 0.95)
    
    df['Entry_Signal'] = cond_trend1 & cond_near_high
    
    return df

def run_simulation(indicators, index_df, all_dates):
    cash = INITIAL_CAPITAL
    positions = {} # ticker -> {shares, entry_price, entry_date, highest_close}
    equity_curve = {}
    trade_log = []
    
    print("Starting day-by-day simulation...")
    
    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i+1] # T+1 Open execution
        
        # Determine Market Regime on eval_date
        if eval_date in index_df.index:
            regime_bullish = index_df.loc[eval_date, 'Regime_Bullish']
        else:
            # Fallback to previous known regime
            prev_dates = index_df.index[index_df.index <= eval_date]
            regime_bullish = index_df.loc[prev_dates[-1], 'Regime_Bullish'] if len(prev_dates) > 0 else False
            
        tickers_to_exit = []
        
        # 1. CHECK EXITS
        for ticker, pos in positions.items():
            if eval_date not in indicators[ticker].index:
                continue
                
            current_close = indicators[ticker].loc[eval_date, 'Close']
            
            ema_50 = indicators[ticker].loc[eval_date, 'EMA_50']
            
            # Stop loss when price closes below 50EMA
            if current_close < ema_50:
                # We will execute at next day's Open
                if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Open']):
                    exit_exec_price = indicators[ticker].loc[exec_date, 'Open']
                else:
                    exit_exec_price = current_close # Fallback
                tickers_to_exit.append((ticker, exit_exec_price, "Close Below 50EMA"))
                
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
            
        # 2. CHECK ENTRIES
        open_slots = MAX_POSITIONS - len(positions)
        if open_slots > 0 and regime_bullish:
            # Calculate 90th percentile of ROC_3M across universe for today
            valid_rocs = []
            for ticker, df in indicators.items():
                if eval_date in df.index and pd.notna(df.loc[eval_date, 'ROC_3M']):
                    valid_rocs.append(df.loc[eval_date, 'ROC_3M'])
            
            roc_90th = np.percentile(valid_rocs, 90) if valid_rocs else np.inf
            
            potential_entries = []
            for ticker, df in indicators.items():
                if ticker in positions: continue
                if eval_date not in df.index: continue
                
                row = df.loc[eval_date]
                if exec_date not in df.index or pd.isna(df.loc[exec_date, 'Open']):
                    continue
                    
                if row['Entry_Signal'] and pd.notna(row['ROC_3M']) and row['ROC_3M'] >= roc_90th:
                    entry_exec_price = df.loc[exec_date, 'Open']
                    potential_entries.append((ticker, entry_exec_price, row['ROC_3M']))
                    
            # Sort highest 3-Month ROC first
            potential_entries.sort(key=lambda x: x[2], reverse=True)
            
            for ticker, entry_price, _ in potential_entries[:open_slots]:
                mtm = cash
                for t, p in positions.items():
                    if eval_date in indicators[t].index:
                        mtm += p['shares'] * indicators[t].loc[eval_date, 'Close']
                    else:
                        mtm += p['shares'] * p['entry_price']
                        
                alloc = mtm * ALLOCATION_PCT
                if alloc > cash: alloc = cash # Don't spend more than we have
                
                shares = int(alloc / entry_price)
                if shares > 0:
                    cost = shares * entry_price
                    cash -= cost
                    positions[ticker] = {
                        'shares': shares,
                        'entry_price': entry_price,
                        'entry_date': exec_date,
                        'highest_close': entry_price
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
    
    index_df = calculate_index_indicators(index_data)
    
    indicators = {}
    print("Calculating Daily Indicators for Simplified Breakout Strategy...")
    
    # Process one by one with a simple progress print
    total_tickers = len(market_data.columns.levels[0])
    for idx, ticker in enumerate(market_data.columns.levels[0]):
        if idx % 50 == 0:
            print(f"Processed {idx}/{total_tickers} tickers...")
            
        df = market_data[ticker].copy()
        df.dropna(subset=['Close', 'High', 'Low'], inplace=True)
        if len(df) < 252:
            continue
            
        try:
            indicators[ticker] = calculate_daily_indicators(df, index_df)
        except Exception as e:
            pass
            
    # Common dates (last 5 years)
    all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365*5))
    all_dates = [d for d in all_dates if d >= cutoff_date]
    
    trade_log_df, strategy_equity = run_simulation(indicators, index_df, all_dates)
    
    if not trade_log_df.empty:
        trade_log_df.to_csv(f"{OUT_DIR}/breakout_trade_log.csv", index=False)
        print(f"\nTrade log saved. Total trades: {len(trade_log_df)}")
        win_rate = len(trade_log_df[trade_log_df['Return (%)'] > 0]) / len(trade_log_df) * 100
        avg_win = trade_log_df[trade_log_df['Return (%)'] > 0]['Return (%)'].mean()
        avg_loss = trade_log_df[trade_log_df['Return (%)'] <= 0]['Return (%)'].mean()
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Average Winner: {avg_win:.2f}% | Average Loser: {avg_loss:.2f}%")
        
    # Baseline Buy & Hold Nifty 500
    print("Calculating Baseline Buy & Hold (Nifty 500)...")
    index_eval = index_df.loc[(index_df.index >= cutoff_date) & (index_df.index <= all_dates[-1])]
    if not index_eval.empty:
        baseline_equity = index_eval['Close'] / index_eval['Close'].iloc[0] * INITIAL_CAPITAL
    else:
        baseline_equity = pd.Series()
        
    # Plotting
    plt.figure(figsize=(12, 6))
    
    if not strategy_equity.empty and not baseline_equity.empty:
        strat_norm = strategy_equity / strategy_equity.iloc[0] * 100
        base_norm = baseline_equity / baseline_equity.iloc[0] * 100
        
        plt.plot(strat_norm.index, strat_norm.values, label='Daily Breakout Strategy', color='green')
        plt.plot(base_norm.index, base_norm.values, label='Nifty 500 (Buy & Hold)', color='gray', alpha=0.7)
        
        years = (strat_norm.index[-1] - strat_norm.index[0]).days / 365.25
        strat_cagr = ((strat_norm.iloc[-1] / strat_norm.iloc[0]) ** (1/years) - 1) * 100
        base_cagr = ((base_norm.iloc[-1] / base_norm.iloc[0]) ** (1/years) - 1) * 100
        
        plt.title(f'Breakout Strategy vs Nifty 500 (Last 5 Years)\nBreakout CAGR: {strat_cagr:.1f}% | Nifty 500 CAGR: {base_cagr:.1f}%')
        plt.xlabel('Date')
        plt.ylabel('Normalized Equity (Base 100)')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        plot_path = f"{OUT_DIR}/breakout_vs_index.png"
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"\nEquity curve plot saved to: {plot_path}")
    else:
        print("Not enough data to plot.")

if __name__ == "__main__":
    main()
