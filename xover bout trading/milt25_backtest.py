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

DATA_FILE = "milt25_historical_data.pkl"
UNIVERSE_FILE = "ind_niftytotalmarket_list (3).csv"
INITIAL_CAPITAL = 1000000.0
MAX_POSITIONS = 25
ALLOCATION_PCT = 0.04

# Strategy Parameters
BB_PERIOD = 20
BB_STD = 3.7
MA_PERIOD = 23
MA_TYPE = 'SMA' # SMA or EMA
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.8
STOP_LOSS_PCT = 0.20

OUT_DIR = "milt25_backtest_results"

def get_tickers_from_csv(file_path):
    try:
        df = pd.read_csv(file_path)
        # Handle cases where column names might have spaces or different casing
        cols = [c.upper().strip() for c in df.columns]
        df.columns = cols
        
        symbol_col = 'SYMBOL' if 'SYMBOL' in cols else 'TICKER'
        if symbol_col in cols:
            tickers = df[symbol_col].dropna().astype(str).tolist()
            return [f"{t.strip()}.NS" for t in tickers if t.strip()]
        else:
            print(f"Error: Symbol/Ticker column not found in {file_path}")
            return []
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

def load_data(tickers):
    end_date = datetime.date.today()
    # 6 years for the test + 1 year buffer
    start_date = end_date - datetime.timedelta(days=365*7) 
    
    if os.path.exists(DATA_FILE):
        print(f"Loading cached market data...")
        market_data = pd.read_pickle(DATA_FILE)
    else:
        print(f"Downloading 7 years of history for {len(tickers)} tickers... This might take a while.")
        market_data = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True)
        market_data.to_pickle(DATA_FILE)
        
    return market_data

def wilders_smoothing(series, periods):
    # Wilder's Smoothing for ATR
    res = np.zeros(len(series))
    res[0] = series.iloc[0]
    for i in range(1, len(series)):
        res[i] = res[i-1] + (series.iloc[i] - res[i-1]) / periods
    return pd.Series(res, index=series.index)

def calculate_weekly_indicators(df):
    # Resample daily to weekly (Friday)
    df_weekly = df.resample('W-FRI').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    })
    df_weekly.dropna(subset=['Close'], inplace=True)
    
    # Bollinger Bands
    df_weekly['BB_Mid'] = df_weekly['Close'].rolling(window=BB_PERIOD).mean()
    df_weekly['BB_Std'] = df_weekly['Close'].rolling(window=BB_PERIOD).std()
    df_weekly['BB_Upper'] = df_weekly['BB_Mid'] + (BB_STD * df_weekly['BB_Std'])
    
    # Moving Average
    if MA_TYPE == 'EMA':
        df_weekly['Trend_MA'] = df_weekly['Close'].ewm(span=MA_PERIOD, adjust=False).mean()
    else:
        df_weekly['Trend_MA'] = df_weekly['Close'].rolling(window=MA_PERIOD).mean()
        
    # ATR Calculation
    high_low = df_weekly['High'] - df_weekly['Low']
    high_close = np.abs(df_weekly['High'] - df_weekly['Close'].shift())
    low_close = np.abs(df_weekly['Low'] - df_weekly['Close'].shift())
    
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df_weekly['ATR'] = wilders_smoothing(tr, ATR_PERIOD)
    
    # 52-Week (1 Year) ROC
    df_weekly['ROC_52W'] = df_weekly['Close'] / df_weekly['Close'].shift(52) - 1
    
    # Entry Signal
    df_weekly['Entry_Signal'] = df_weekly['Close'] > df_weekly['BB_Upper']
    
    return df_weekly

def run_simulation(indicators, all_dates):
    cash = INITIAL_CAPITAL
    positions = {} # ticker -> {shares, entry_price, entry_date, highest_close}
    equity_curve = {}
    trade_log = []
    
    print("Starting week-by-week simulation...")
    
    # Loop from week 0 to len-2. We evaluate on week i (Friday), execute on week i+1 (Monday/Open)
    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i+1] # Next week's data holds the Open price we want to execute at
        
        tickers_to_exit = []
        
        # 1. CHECK EXITS based on eval_date (Friday Close)
        for ticker, pos in positions.items():
            if eval_date not in indicators[ticker].index:
                continue
                
            row = indicators[ticker].loc[eval_date]
            current_close = row['Close']
            
            # Update highest close for ATR trailing stop
            if current_close > pos['highest_close']:
                positions[ticker]['highest_close'] = current_close
                
            trailing_stop_price = pos['highest_close'] - (ATR_MULTIPLIER * row['ATR'])
            hard_stop_price = pos['entry_price'] * (1 - STOP_LOSS_PCT)
            
            exit_reason = None
            if current_close < hard_stop_price:
                exit_reason = f"Hard Stop (20%)"
            elif current_close < row['Trend_MA']:
                exit_reason = f"Trend MA Cross Down"
            elif current_close < trailing_stop_price:
                exit_reason = f"ATR Trailing Stop"
                
            if exit_reason:
                # We will execute at next week's Open
                if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date]['Open']):
                    exit_exec_price = indicators[ticker].loc[exec_date]['Open']
                else:
                    exit_exec_price = current_close # Fallback
                tickers_to_exit.append((ticker, exit_exec_price, exit_reason))
                
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
            
        # 2. CHECK ENTRIES based on eval_date (Friday Close)
        open_slots = MAX_POSITIONS - len(positions)
        if open_slots > 0:
            potential_entries = []
            for ticker, df in indicators.items():
                if ticker in positions: continue
                if eval_date not in df.index: continue
                
                row = df.loc[eval_date]
                # Ensure we have data for next week's open
                if exec_date not in df.index or pd.isna(df.loc[exec_date]['Open']):
                    continue
                    
                if row['Entry_Signal'] and pd.notna(row['ROC_52W']):
                    entry_exec_price = df.loc[exec_date]['Open']
                    potential_entries.append((ticker, entry_exec_price, row['ROC_52W']))
                    
            # Sort highest 52-Week ROC first
            potential_entries.sort(key=lambda x: x[2], reverse=True)
            
            for ticker, entry_price, _ in potential_entries[:open_slots]:
                # Calculate Portfolio MTM to find 4% alloc
                mtm = cash
                for t, p in positions.items():
                    if eval_date in indicators[t].index:
                        mtm += p['shares'] * indicators[t].loc[eval_date]['Close']
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
                        'highest_close': entry_price # Initialize highest close with entry price
                    }
                    
        # 3. MARK TO MARKET using exec_date's close
        mtm = cash
        for ticker, pos in positions.items():
            if exec_date in indicators[ticker].index:
                mtm += pos['shares'] * indicators[ticker].loc[exec_date]['Close']
            else:
                mtm += pos['shares'] * pos['entry_price'] # fallback
        equity_curve[exec_date] = mtm
        
    return pd.DataFrame(trade_log), pd.Series(equity_curve)

def run_crossover_comparison(market_data):
    # Quick implementation of the 50x200 EMA strategy for comparison
    cash = INITIAL_CAPITAL
    positions = {}
    equity_curve = {}
    
    # We will resample the comparison to weekly as well to match dates, 
    # but base the crossover on daily EMA.
    print("Calculating Crossover Strategy Baseline...")
    
    cross_indicators = {}
    for ticker in market_data.columns.levels[0]:
        df = market_data[ticker].copy()
        df.dropna(subset=['Close', 'High'], inplace=True)
        if len(df) < 200: continue
        
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        df['Cross_Up'] = (df['EMA_50'] > df['EMA_200']) & (df['EMA_50'].shift(1) <= df['EMA_200'].shift(1))
        df['Cross_Down'] = (df['EMA_50'] < df['EMA_200']) & (df['EMA_50'].shift(1) >= df['EMA_200'].shift(1))
        
        # Resample to weekly to match MILT dates
        df_weekly = df.resample('W-FRI').agg({
            'Open': 'first',
            'Close': 'last'
        })
        # Did a cross up or down happen at any point during this week?
        df_weekly['Cross_Up_Weekly'] = df['Cross_Up'].resample('W-FRI').max() > 0
        df_weekly['Cross_Down_Weekly'] = df['Cross_Down'].resample('W-FRI').max() > 0
        
        cross_indicators[ticker] = df_weekly
        
    all_dates = sorted(set().union(*[set(df.index) for df in cross_indicators.values()]))
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365*5))
    all_dates = [d for d in all_dates if d >= cutoff_date]
    
    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i+1]
        
        # Exits
        tickers_to_exit = []
        for ticker, pos in positions.items():
            if eval_date not in cross_indicators[ticker].index: continue
            row = cross_indicators[ticker].loc[eval_date]
            if row['Cross_Down_Weekly']:
                if exec_date in cross_indicators[ticker].index and pd.notna(cross_indicators[ticker].loc[exec_date]['Open']):
                    exit_price = cross_indicators[ticker].loc[exec_date]['Open']
                else:
                    exit_price = row['Close']
                tickers_to_exit.append((ticker, exit_price))
                
        for ticker, exit_price in tickers_to_exit:
            pos = positions.pop(ticker)
            cash += pos['shares'] * exit_price
            
        # Entries
        open_slots = MAX_POSITIONS - len(positions)
        if open_slots > 0:
            potential_entries = []
            for ticker, df in cross_indicators.items():
                if ticker in positions: continue
                if eval_date not in df.index: continue
                
                row = df.loc[eval_date]
                if row['Cross_Up_Weekly']:
                    if exec_date in df.index and pd.notna(df.loc[exec_date]['Open']):
                        potential_entries.append((ticker, df.loc[exec_date]['Open']))
                        
            for ticker, entry_price in potential_entries[:open_slots]:
                mtm = cash + sum([p['shares'] * cross_indicators[t].loc[eval_date]['Close'] for t, p in positions.items() if eval_date in cross_indicators[t].index])
                alloc = mtm * ALLOCATION_PCT
                if alloc > cash: alloc = cash
                shares = int(alloc / entry_price)
                if shares > 0:
                    cash -= shares * entry_price
                    positions[ticker] = {'shares': shares, 'entry_price': entry_price}
                    
        # MTM
        mtm = cash
        for ticker, pos in positions.items():
            if exec_date in cross_indicators[ticker].index:
                mtm += pos['shares'] * cross_indicators[ticker].loc[exec_date]['Close']
            else:
                mtm += pos['shares'] * pos['entry_price']
        equity_curve[exec_date] = mtm
        
    return pd.Series(equity_curve)

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    
    tickers = get_tickers_from_csv(UNIVERSE_FILE)
    if not tickers:
        print("Failed to load tickers.")
        return
        
    # Limit for testing if needed, but we will run the full set
    # tickers = tickers[:50] 
    
    market_data = load_data(tickers)
    
    indicators = {}
    print("Calculating Weekly Indicators for MILT 25...")
    for ticker in market_data.columns.levels[0]:
        df = market_data[ticker].copy()
        df.dropna(subset=['Close', 'High'], inplace=True)
        if len(df) < 252: # Need at least a year of data
            continue
        try:
            indicators[ticker] = calculate_weekly_indicators(df)
        except Exception as e:
            # print(f"Error calculating for {ticker}: {e}")
            pass
            
    # Get common dates (last 5 years)
    all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365*5))
    all_dates = [d for d in all_dates if d >= cutoff_date]
    
    # Run MILT 25
    trade_log_df, milt_equity = run_simulation(indicators, all_dates)
    
    if not trade_log_df.empty:
        trade_log_df.to_csv(f"{OUT_DIR}/milt25_trade_log.csv", index=False)
        print(f"Trade log saved. Total trades: {len(trade_log_df)}")
        win_rate = len(trade_log_df[trade_log_df['Return (%)'] > 0]) / len(trade_log_df) * 100
        avg_win = trade_log_df[trade_log_df['Return (%)'] > 0]['Return (%)'].mean()
        avg_loss = trade_log_df[trade_log_df['Return (%)'] <= 0]['Return (%)'].mean()
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Average Winner: {avg_win:.2f}% | Average Loser: {avg_loss:.2f}%")
        
    # Run Crossover Comparison
    cross_equity = run_crossover_comparison(market_data)
    
    # Plotting
    plt.figure(figsize=(12, 6))
    
    # Normalize to 100 for easy comparison
    if not milt_equity.empty and not cross_equity.empty:
        milt_norm = milt_equity / milt_equity.iloc[0] * 100
        cross_norm = cross_equity / cross_equity.iloc[0] * 100
        
        plt.plot(milt_norm.index, milt_norm.values, label='MILT 25 (Momentum)', color='blue')
        plt.plot(cross_norm.index, cross_norm.values, label='50/200 EMA Crossover', color='gray', alpha=0.7)
        
        # Calculate CAGR
        years = (milt_norm.index[-1] - milt_norm.index[0]).days / 365.25
        milt_cagr = ((milt_norm.iloc[-1] / milt_norm.iloc[0]) ** (1/years) - 1) * 100
        cross_cagr = ((cross_norm.iloc[-1] / cross_norm.iloc[0]) ** (1/years) - 1) * 100
        
        plt.title(f'MILT 25 vs 50/200 EMA Crossover (Last 5 Years)\nMILT 25 CAGR: {milt_cagr:.1f}% | Crossover CAGR: {cross_cagr:.1f}%')
        plt.xlabel('Date')
        plt.ylabel('Normalized Equity (Base 100)')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        plot_path = f"{OUT_DIR}/milt25_vs_crossover.png"
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"\nEquity curve plot saved to: {plot_path}")
    else:
        print("Not enough data to plot.")

if __name__ == "__main__":
    main()
