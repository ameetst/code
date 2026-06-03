import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import os
import warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Suppress warnings
warnings.filterwarnings('ignore')

DATA_FILE = "ema_historical_data.pkl"
INDEX_FILE = "ema_index_data.pkl"
INITIAL_CAPITAL = 1000000.0
MAX_POSITIONS = 20

def get_tickers_from_csv(file_path="tickers.csv"):
    try:
        df = pd.read_csv(file_path)
        if 'TICKER' in df.columns:
            tickers = df['TICKER'].dropna().astype(str).tolist()
            return [f"{t.strip()}.NS" for t in tickers if t.strip()]
        return []
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

def load_data(tickers):
    end_date = datetime.date.today()
    # 5 years for the test + 1 year buffer for the initial 52W high calculation
    start_date = end_date - datetime.timedelta(days=365*6) 
    
    if os.path.exists(DATA_FILE):
        print(f"Loading cached market data...")
        market_data = pd.read_pickle(DATA_FILE)
    else:
        print("Downloading 6 years of history... This might take 3-5 minutes.")
        market_data = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True)
        market_data.to_pickle(DATA_FILE)
        
    INDEX_FILE_MOM = "ema_index_data_mom100.pkl"
    if os.path.exists(INDEX_FILE_MOM):
        print(f"Loading cached index data...")
        index_data = pd.read_pickle(INDEX_FILE_MOM)
    else:
        print("Downloading Nifty Midcap 100 ETF data...")
        index_data = yf.download("MOM100.NS", start=start_date, end=end_date) 
        index_data.to_pickle(INDEX_FILE_MOM)
        
    return market_data, index_data

def calculate_indicators(df):
    df = df.copy()
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['EMA_63'] = df['Close'].ewm(span=63, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    df['High_52W'] = df['High'].rolling(window=252, min_periods=63).max()
    df['High_63D'] = df['High'].rolling(window=63, min_periods=63).max()
    
    df['Momentum_63'] = df['Close'] / df['Close'].shift(63) - 1
    
    df['Cross_Up'] = (df['EMA_21'] > df['EMA_63']) & (df['EMA_21'].shift(1) <= df['EMA_63'].shift(1))
    df['Cross_Down'] = (df['EMA_21'] < df['EMA_63']) & (df['EMA_21'].shift(1) >= df['EMA_63'].shift(1))
    
    df['P_52H'] = df['Close'] / df['High_52W']
    
    # Pre-calculate entry signal for faster backtesting loop
    df['Entry_Signal'] = (df['Cross_Up'] & 
                          (df['P_52H'] >= 0.75) & 
                          (df['Close'] > df['EMA_200']) & 
                          (df['Momentum_63'] > 0) & 
                          (df['High_52W'] == df['High_63D']))
    
    return df

def run_backtest():
    tickers = get_tickers_from_csv()
    if not tickers:
        print("No tickers found in CSV.")
        return

    market_data, index_data = load_data(tickers)
    
    print("\nCalculating indicators for all tickers...")
    indicators = {}
    for ticker in tickers:
        if len(tickers) == 1:
            df = market_data.copy()
        else:
            if ticker not in market_data.columns.levels[0]:
                continue
            df = market_data[ticker].copy()
            
        df.dropna(subset=['Close', 'High'], inplace=True)
        
        if len(df) < 252:
            continue
            
        indicators[ticker] = calculate_indicators(df)
        
    print("Running portfolio simulation...")
    
    # Get all trading dates within the last 5 years
    all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365*5))
    all_dates = [d for d in all_dates if d >= cutoff_date]
    
    cash = INITIAL_CAPITAL
    positions = {} # ticker -> {shares, entry_price, entry_date, max_high}
    equity_curve = {}
    trade_log = []
    
    for date in all_dates:
        # 1. CHECK EXITS
        tickers_to_exit = []
        for ticker, pos in positions.items():
            if date not in indicators[ticker].index:
                continue
            
            row = indicators[ticker].loc[date]
            current_close = row['Close']
            current_high = row['High']
            
            # Exit Logic: 21EMA Crosses below 63EMA
            if row['Cross_Down']:
                tickers_to_exit.append((ticker, current_close, "EMA Cross Down"))
                
        for ticker, exit_price, reason in tickers_to_exit:
            pos = positions.pop(ticker)
            proceeds = pos['shares'] * exit_price
            cash += proceeds
            trade_log.append({
                'Ticker': ticker.replace('.NS', ''),
                'Entry Date': pos['entry_date'].date(),
                'Entry Price': pos['entry_price'],
                'Exit Date': date.date(),
                'Exit Price': exit_price,
                'Return (%)': (exit_price - pos['entry_price']) / pos['entry_price'] * 100,
                'Reason': reason
            })
            
        # 2. CHECK ENTRIES
        open_slots = MAX_POSITIONS - len(positions)
        if open_slots > 0:
            potential_entries = []
            for ticker, df in indicators.items():
                if ticker in positions: continue
                if date not in df.index: continue
                
                row = df.loc[date]
                if row['Entry_Signal']:
                    # Rank by 63-day momentum if there are too many signals
                    potential_entries.append((ticker, row['Close'], row['Momentum_63']))
                    
            # Sort highest momentum first
            potential_entries.sort(key=lambda x: x[2], reverse=True)
            
            for ticker, entry_price, _ in potential_entries[:open_slots]:
                # Calculate Portfolio MTM to find 5% alloc
                mtm = cash + sum([p['shares'] * indicators[t].loc[date]['Close'] for t, p in positions.items() if date in indicators[t].index])
                alloc = mtm * 0.05
                if alloc > cash: alloc = cash # Don't spend more than we have
                
                shares = int(alloc / entry_price)
                if shares > 0:
                    cost = shares * entry_price
                    cash -= cost
                    positions[ticker] = {
                        'shares': shares,
                        'entry_price': entry_price,
                        'entry_date': date
                    }
                    
        # 3. MARK TO MARKET
        mtm = cash
        for ticker, pos in positions.items():
            if date in indicators[ticker].index:
                mtm += pos['shares'] * indicators[ticker].loc[date]['Close']
            else:
                mtm += pos['shares'] * pos['entry_price'] # fallback
        equity_curve[date] = mtm
        
    # GENERATE OUTPUTS
    print("\n=======================================================")
    print("               BACKTEST RESULTS (5 YEARS)              ")
    print("=======================================================")
    
    trades_df = pd.DataFrame(trade_log)
    if not trades_df.empty:
        win_rate = len(trades_df[trades_df['Return (%)'] > 0]) / len(trades_df) * 100
        print(f"Total Trades: {len(trades_df)}")
        print(f"Win Rate:     {win_rate:.2f}%")
        print(f"Avg Return per Trade: {trades_df['Return (%)'].mean():.2f}%")
        
    equity_series = pd.Series(equity_curve)
    final_equity = equity_series.iloc[-1]
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    cagr = ((final_equity / INITIAL_CAPITAL) ** (1 / 5) - 1) * 100
    print(f"Initial Capital: INR {INITIAL_CAPITAL:,.2f}")
    print(f"Final Equity:    INR {final_equity:,.2f}")
    print(f"Total Return:    {total_return:.2f}%")
    print(f"Strategy CAGR:   {cagr:.2f}%")
    print("=======================================================\n")
    
    # Process Benchmark Data
    if isinstance(index_data.columns, pd.MultiIndex):
        idx_close = index_data['Close'].iloc[:, 0]
    else:
        idx_close = index_data['Close']
        
    idx_close = idx_close.reindex(equity_series.index, method='ffill').dropna()
    
    if not idx_close.empty:
        bench_return = (idx_close.iloc[-1] - idx_close.iloc[0]) / idx_close.iloc[0] * 100
        bench_cagr = ((idx_close.iloc[-1] / idx_close.iloc[0]) ** (1 / 5) - 1) * 100
        bench_equity = (idx_close / idx_close.iloc[0]) * INITIAL_CAPITAL
        print(f"Nifty Midcap 100 ETF Return: {bench_return:.2f}%")
        print(f"Nifty Midcap 100 ETF CAGR:   {bench_cagr:.2f}%")
    
    # Plotting
    plt.style.use('dark_background')
    plt.figure(figsize=(14, 7))
    plt.plot(equity_series.index, equity_series.values, label=f'Strategy (CAGR: {cagr:.1f}%)', color='cyan', linewidth=2)
    
    if not idx_close.empty:
        plt.plot(bench_equity.index, bench_equity.values, label=f'Nifty Midcap 100 (CAGR: {bench_cagr:.1f}%)', color='gray', linestyle='--')
        plt.fill_between(bench_equity.index, bench_equity.values, INITIAL_CAPITAL, where=(bench_equity.values >= INITIAL_CAPITAL), alpha=0.1, color='gray')
        
    plt.fill_between(equity_series.index, equity_series.values, INITIAL_CAPITAL, where=(equity_series.values >= INITIAL_CAPITAL), alpha=0.1, color='cyan')
    
    plt.title('Daily Market Screener Strategy vs Nifty Midcap 100 (5-Year Equity Curve)')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Value (INR)')
    plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: format(int(x), ',')))
    plt.legend(loc='upper left')
    plt.grid(True, alpha=0.2)
    plt.tight_layout()
    plt.savefig('equity_curve.png')
    print("\nSaved chart to equity_curve.png")

if __name__ == "__main__":
    run_backtest()
