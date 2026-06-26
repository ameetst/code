import os
import warnings
import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

MARKET_DATA_FILE = 'milt25_historical_data.pkl'
INDEX_FILE = 'nifty500_data.pkl'
INITIAL_CAPITAL = 200000.0
MAX_POSITIONS = 20
RISK_PCT = 0.02
VOL_DRYUP_PCT = 0.70
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0
ATR_TRAIL_MULTIPLIER = 2.5
TREND_CONFIRM_DAYS = 20
RS_LOOKBACK = 63
OUT_DIR = 'vcp_breakout_results'


def load_data():
    if not os.path.exists(MARKET_DATA_FILE):
        print(f'Error: {MARKET_DATA_FILE} not found.')
        return None, None

    print(f'Loading cached market data from {MARKET_DATA_FILE}...')
    market_data = pd.read_pickle(MARKET_DATA_FILE)

    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=365 * 7)

    if os.path.exists(INDEX_FILE):
        print(f'Loading cached index data from {INDEX_FILE}...')
        index_data = pd.read_pickle(INDEX_FILE)
    else:
        print('Downloading Nifty 500 (^CRSLDX) index data...')
        index_data = yf.download('^CRSLDX', start=start_date, end=end_date)
        index_data.to_pickle(INDEX_FILE)

    return market_data, index_data


def calculate_atr(df, period=ATR_PERIOD):
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift(1)).abs()
    low_close = (df['Low'] - df['Close'].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def build_benchmark_proxy(market_data):
    if isinstance(market_data.columns, pd.MultiIndex):
        close = market_data.xs('Close', axis=1, level='Price')
    else:
        close = market_data.filter(like='Close')

    daily_returns = close.pct_change(fill_method=None)
    benchmark_returns = daily_returns.mean(axis=1, skipna=True).fillna(0)
    benchmark_equity = (1 + benchmark_returns).cumprod()
    if not benchmark_equity.empty:
        benchmark_equity = benchmark_equity / benchmark_equity.iloc[0] * INITIAL_CAPITAL
    return benchmark_equity


def prepare_index_close(index_data):
    if isinstance(index_data.columns, pd.MultiIndex):
        return index_data['Close'].iloc[:, 0].dropna()
    return index_data['Close'].dropna()


def calculate_daily_indicators(df, benchmark_close):
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
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    df['EMA_200_Rising'] = df['EMA_200'] > df['EMA_200'].shift(TREND_CONFIRM_DAYS)

    df['ATR_14'] = calculate_atr(df)

    df['Vol_20D_Avg'] = df['Volume'].rolling(20).mean().shift(1)
    df['Vol_5D_Avg'] = df['Volume'].rolling(5).mean().shift(1)
    df['Vol_Surge'] = df['Volume'] / (df['Vol_20D_Avg'] + 1e-8)

    df['Price_Up'] = df['Close'] > df['Close'].shift(1)

    benchmark_rs = benchmark_close.pct_change(RS_LOOKBACK).reindex(df.index, method='ffill')
    stock_rs = df['Close'].pct_change(RS_LOOKBACK)
    df['RS_63'] = stock_rs - benchmark_rs

    cond_range = df['Range_10D'] < 0.08
    cond_rsi = (df['RSI'] >= 40) & (df['RSI'] <= 60)
    cond_vol = df['Vol_Surge'] >= 3.0
    cond_price = df['Price_Up']
    cond_trend = (df['Close'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200']) & df['EMA_200_Rising']
    cond_rs = df['RS_63'] > 0

    df['Entry_Signal'] = cond_range & cond_rsi & cond_vol & cond_price & cond_trend & cond_rs
    return df


def run_simulation(indicators, all_dates):
    cash = INITIAL_CAPITAL
    positions = {}
    equity_curve = {}
    trade_log = []

    print('Starting day-by-day simulation...')

    for i in range(len(all_dates) - 1):
        eval_date = all_dates[i]
        exec_date = all_dates[i + 1]

        tickers_to_exit = []
        for ticker, pos in list(positions.items()):
            if eval_date not in indicators[ticker].index:
                continue

            row = indicators[ticker].loc[eval_date]
            current_close = row['Close']
            current_low = row['Low']
            current_atr = row['ATR_14']

            if pd.notna(current_atr) and current_atr > 0:
                new_trail = current_close - (ATR_TRAIL_MULTIPLIER * current_atr)
                pos['trail_stop'] = max(pos['trail_stop'], float(new_trail))

            if current_low <= pos['hard_stop']:
                tickers_to_exit.append((ticker, pos['hard_stop'], 'ATR Hard Stop', eval_date))
                continue

            if pd.notna(pos['trail_stop']) and current_close < pos['trail_stop']:
                if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Open']):
                    exit_exec_price = float(indicators[ticker].loc[exec_date, 'Open'])
                else:
                    exit_exec_price = float(current_close)
                tickers_to_exit.append((ticker, exit_exec_price, 'ATR Trailing Stop', exec_date))

        for ticker, exit_price, reason, exit_date_actual in tickers_to_exit:
            pos = positions.pop(ticker)
            proceeds = pos['shares'] * exit_price
            cash += proceeds
            trade_log.append({
                'Ticker': ticker.replace('.NS', ''),
                'Entry Date': pos['entry_date'].date(),
                'Entry Price': pos['entry_price'],
                'Exit Date': exit_date_actual.date(),
                'Exit Price': round(exit_price, 2),
                'Return (%)': round((exit_price - pos['entry_price']) / pos['entry_price'] * 100, 2),
                'Reason': reason,
            })

        current_equity = cash
        for ticker, pos in positions.items():
            if eval_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[eval_date, 'Close']):
                current_equity += pos['shares'] * indicators[ticker].loc[eval_date, 'Close']
            else:
                current_equity += pos['shares'] * pos['entry_price']

        open_slots = MAX_POSITIONS - len(positions)
        if open_slots > 0:
            potential_entries = []
            for ticker, df in indicators.items():
                if ticker in positions:
                    continue
                if eval_date not in df.index:
                    continue

                row = df.loc[eval_date]
                if exec_date not in df.index or pd.isna(df.loc[exec_date, 'Open']):
                    continue

                if row['Entry_Signal'] and pd.notna(row['ATR_14']) and row['ATR_14'] > 0:
                    potential_entries.append((ticker, float(df.loc[exec_date, 'Open']), float(row['Vol_Surge']), float(row['ATR_14'])))

            potential_entries.sort(key=lambda item: item[2], reverse=True)

            for ticker, entry_price, _, entry_atr in potential_entries[:open_slots]:
                risk_amount = current_equity * RISK_PCT
                stop_distance = ATR_STOP_MULTIPLIER * entry_atr
                if stop_distance <= 0:
                    continue

                shares = int(risk_amount / stop_distance)
                if shares <= 0:
                    continue

                cost = shares * entry_price
                if cost > cash:
                    shares = int(cash / entry_price)
                    cost = shares * entry_price

                if shares > 0:
                    cash -= cost
                    hard_stop = entry_price - stop_distance
                    positions[ticker] = {
                        'shares': shares,
                        'entry_price': entry_price,
                        'entry_date': exec_date,
                        'hard_stop': hard_stop,
                        'trail_stop': hard_stop,
                        'peak_close': entry_price,
                        'entry_atr': entry_atr,
                    }

        mtm = cash
        for ticker, pos in positions.items():
            if exec_date in indicators[ticker].index and pd.notna(indicators[ticker].loc[exec_date, 'Close']):
                mtm += pos['shares'] * indicators[ticker].loc[exec_date, 'Close']
            else:
                mtm += pos['shares'] * pos['entry_price']
        equity_curve[exec_date] = mtm

    return pd.DataFrame(trade_log), pd.Series(equity_curve)


def compute_stats(equity_curve, benchmark_curve):
    years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100
    cagr = ((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else np.nan
    max_dd = ((equity_curve / equity_curve.cummax()) - 1).min() * 100

    bench_aligned = benchmark_curve.reindex(equity_curve.index, method='ffill').dropna()
    bench_years = (bench_aligned.index[-1] - bench_aligned.index[0]).days / 365.25
    bench_return = (bench_aligned.iloc[-1] / bench_aligned.iloc[0] - 1) * 100
    bench_cagr = ((bench_aligned.iloc[-1] / bench_aligned.iloc[0]) ** (1 / bench_years) - 1) * 100 if bench_years > 0 else np.nan
    bench_dd = ((bench_aligned / bench_aligned.cummax()) - 1).min() * 100

    return {
        'strategy_total_return': total_return,
        'strategy_cagr': cagr,
        'strategy_max_dd': max_dd,
        'benchmark_total_return': bench_return,
        'benchmark_cagr': bench_cagr,
        'benchmark_max_dd': bench_dd,
    }


def plot_equity_curve(equity_curve, benchmark_curve, out_path, title_suffix=''):
    bench_aligned = benchmark_curve.reindex(equity_curve.index, method='ffill').dropna()
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max * 100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.08}, sharex=True)
    fig.patch.set_facecolor('#0e1117')
    for ax in (ax1, ax2):
        ax.set_facecolor('#0e1117')
        ax.tick_params(colors='#aaaaaa', labelsize=9)
        for spine in ax.spines.values():
            spine.set_color('#333333')

    ax1.plot(equity_curve.index, equity_curve.values, color='#00c896', linewidth=1.8, label='Strategy')
    ax1.plot(bench_aligned.index, bench_aligned.values, color='#5b9bd5', linewidth=1.2, linestyle='--', label='Nifty 750 proxy')
    ax1.fill_between(equity_curve.index, equity_curve.iloc[0], equity_curve.values, where=(equity_curve.values >= equity_curve.iloc[0]), alpha=0.08, color='#00c896')
    ax1.legend(loc='upper left', facecolor='#1a1d27', edgecolor='#333333', labelcolor='#cccccc', fontsize=9)
    ax1.grid(axis='y', color='#222222', linewidth=0.5)
    ax1.grid(axis='x', color='#1a1a1a', linewidth=0.4)
    ax1.set_ylabel('Portfolio Value', color='#aaaaaa', fontsize=9)
    ax1.set_title(f'VCP Breakout vs Nifty 750 Proxy{title_suffix}', color='#dddddd', fontsize=12, pad=10)

    ax2.fill_between(drawdown.index, drawdown.values, 0, color='#ff6b6b', alpha=0.35)
    ax2.plot(drawdown.index, drawdown.values, color='#ff6b6b', linewidth=0.9)
    ax2.set_ylabel('Drawdown %', color='#aaaaaa', fontsize=9)
    ax2.grid(axis='y', color='#222222', linewidth=0.5)
    ax2.axhline(0, color='#444444', linewidth=0.8)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    print(f'Equity curve chart saved to: {out_path}')


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    market_data, index_data = load_data()
    if market_data is None:
        return

    benchmark_proxy = build_benchmark_proxy(market_data)
    benchmark_close = prepare_index_close(index_data)

    if isinstance(market_data.columns, pd.MultiIndex):
        tickers = list(market_data.columns.levels[0])
    else:
        tickers = []

    indicators = {}
    print('Calculating Daily Indicators for VCP Breakout Strategy...')
    for idx, ticker in enumerate(tickers):
        if idx % 50 == 0:
            print(f'Processed {idx}/{len(tickers)} tickers...')
        df = market_data[ticker].copy()
        df.dropna(subset=['Close', 'High', 'Low', 'Volume'], inplace=True)
        if len(df) < 50:
            continue
        try:
            indicators[ticker] = calculate_daily_indicators(df, benchmark_close)
        except Exception:
            pass

    all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
    cutoff_date = pd.Timestamp(datetime.date.today() - datetime.timedelta(days=365 * 5))
    all_dates = [d for d in all_dates if d >= cutoff_date]

    trade_log_df, strategy_equity = run_simulation(indicators, all_dates)

    if not trade_log_df.empty:
        trade_path = os.path.join(OUT_DIR, 'vcp_trade_log.csv')
        trade_log_df.to_csv(trade_path, index=False)
        print(f'\nTrade log saved. Total trades: {len(trade_log_df)}')
        win_rate = len(trade_log_df[trade_log_df['Return (%)'] > 0]) / len(trade_log_df) * 100
        avg_win = trade_log_df.loc[trade_log_df['Return (%)'] > 0, 'Return (%)'].mean()
        avg_loss = trade_log_df.loc[trade_log_df['Return (%)'] <= 0, 'Return (%)'].mean()
        print(f'Win Rate: {win_rate:.2f}%')
        print(f'Average Winner: {avg_win:.2f}% | Average Loser: {avg_loss:.2f}%')

    if strategy_equity.empty:
        print('Not enough equity data to evaluate.')
        return

    stats = compute_stats(strategy_equity, benchmark_proxy)
    comp_df = pd.DataFrame({
        'Metric': ['Total Return', 'CAGR', 'Max Drawdown', 'Final Equity'],
        'VCP Breakout': [
            f"{stats['strategy_total_return']:.2f}%",
            f"{stats['strategy_cagr']:.2f}%",
            f"{stats['strategy_max_dd']:.2f}%",
            f"INR {strategy_equity.iloc[-1]:,.2f}"
        ],
        'Nifty 750 Proxy': [
            f"{stats['benchmark_total_return']:.2f}%",
            f"{stats['benchmark_cagr']:.2f}%",
            f"{stats['benchmark_max_dd']:.2f}%",
            f"INR {benchmark_proxy.reindex(strategy_equity.index, method='ffill').iloc[-1]:,.2f}"
        ]
    })
    comp_path = os.path.join(OUT_DIR, 'vcp_comparison.csv')
    comp_df.to_csv(comp_path, index=False)
    print('\nTabular Comparison:')
    print(comp_df.to_string(index=False))

    chart_path = os.path.join(OUT_DIR, 'vcp_vs_nifty750_proxy.png')
    plot_equity_curve(strategy_equity, benchmark_proxy, chart_path)


if __name__ == '__main__':
    main()
