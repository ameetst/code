import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
from tqdm import tqdm

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
PRICE_FILE          = "n500.xlsx"
VOLUME_FILE         = "n500_volume.xlsx"
OUTPUT_TRADES       = "backtest_trades.csv"
OUTPUT_EQUITY_CHART = "equity_curve.png"

TRADE_AMOUNT        = 25000.0   # Fixed 25,000 INR per trade
STARTING_CAPITAL    = 250000.0  # 2.5 Lakh starting capital
MAX_CONCURRENT      = 10        # Max trades open at once (to limit capital usage)
TIME_STOP_DAYS      = 15        # Days to wait before cutting a stalled setup

LOOKBACK            = 60
ATR_PERIOD          = 14
RR_RATIO            = 3.0
MIN_CONFIRM_SCORE   = 6
PCT_FROM_52WK_HIGH  = 15.0

EMA_SHORT, EMA_MED, EMA_LONG = 9, 21, 50
BB_PERIOD, BB_STD             = 20, 2.0
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
ADX_PERIOD                    = 14
VOL_SPIKE_MULTIPLIER          = 1.5
VOL_DRY_UP_THRESHOLD          = 0.7
VOL_RISING_DAYS               = 5

# ─────────────────────────────────────────────────────────────
# PREPARE DATA (Vectorized)
# ─────────────────────────────────────────────────────────────
def load_and_pivot(filepath: str) -> pd.DataFrame:
    df = pd.read_excel(filepath, sheet_name="DATA", header=0)
    df = df.set_index("TICKER").drop(columns=["NAME", "INDUSTRY", "52WK HIGH", "CLOSE", "Mkt Cap"], errors="ignore")
    df = df.astype(float).T
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df

print("Loading historical data... (this might take a few seconds)")
prices_df = load_and_pivot(PRICE_FILE)
shares_df = load_and_pivot(VOLUME_FILE)

# Retain only common tickers and dates
common_tickers = list(set(prices_df.columns).intersection(set(shares_df.columns)))
common_dates = list(set(prices_df.index).intersection(set(shares_df.index)))

prices_df = prices_df.loc[common_dates, common_tickers].sort_index().replace(0, np.nan).fillna(method="ffill")
shares_df = shares_df.loc[common_dates, common_tickers].sort_index().fillna(0)

# Volume INR = Shares * Close
volume_df = shares_df * prices_df

print(f"Data loaded: {prices_df.shape[0]} days x {prices_df.shape[1]} tickers.")

# Precalculate 52wk high (last 252 days)
high_52wk = prices_df.rolling(252, min_periods=100).max()

# ─────────────────────────────────────────────────────────────
# VECTORIZED INDICATORS (across all tickers simultaneously)
# ─────────────────────────────────────────────────────────────
print("Computing vectorized indicators...")

# EMAs
ema9 = prices_df.ewm(span=EMA_SHORT, adjust=False).mean()
ema21 = prices_df.ewm(span=EMA_MED, adjust=False).mean()
ema50 = prices_df.ewm(span=EMA_LONG, adjust=False).mean()

# MACD
macd_line = prices_df.ewm(span=MACD_FAST, adjust=False).mean() - prices_df.ewm(span=MACD_SLOW, adjust=False).mean()
macd_sig  = macd_line.ewm(span=MACD_SIG, adjust=False).mean()
macd_hist = macd_line - macd_sig
macd_bullish = (macd_hist > 0) & (macd_hist.shift(1) <= 0)
macd_state   = (macd_hist > 0)

# ATR & Range
diff = prices_df.diff()
abs_diff = diff.abs()
atr = abs_diff.rolling(ATR_PERIOD).mean()
range_high = prices_df.shift(1).rolling(LOOKBACK).max()  # shift(1) to get prior N days up to yesterday

# ADX
dm_plus = diff.clip(lower=0).rolling(ADX_PERIOD).mean()
dm_minus = (-diff).clip(lower=0).rolling(ADX_PERIOD).mean()
smooth_range = abs_diff.rolling(ADX_PERIOD).mean()
di_plus = 100 * dm_plus / smooth_range.replace(0, np.nan)
di_minus = 100 * dm_minus / smooth_range.replace(0, np.nan)
dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
adx = dx.rolling(ADX_PERIOD).mean()

# Bollinger Bands
bb_sma = prices_df.rolling(BB_PERIOD).mean()
bb_std = prices_df.rolling(BB_PERIOD).std()
bb_upper = bb_sma + BB_STD * bb_std
bb_lower = bb_sma - BB_STD * bb_std
bb_bw = (bb_upper - bb_lower) / bb_sma * 100
bb_avg_bw = bb_bw.rolling(90, min_periods=20).mean()
bb_squeeze = bb_bw < (0.5 * bb_avg_bw)
bb_above_upper = prices_df > bb_upper

# RSI
gain = diff.clip(lower=0).rolling(14).mean()
loss = (-diff).clip(upper=0).rolling(14).mean()
rs = gain / loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

# Volume Indicators
avg_vol_20d = volume_df.shift(1).rolling(20).mean()
spiked_vol = volume_df >= (avg_vol_20d * VOL_SPIKE_MULTIPLIER)

pre_break_vol = volume_df.shift(1).rolling(5).mean()
vol_dried_up = pre_break_vol < (avg_vol_20d * VOL_DRY_UP_THRESHOLD)

# Rising Volume Trend (simplified: today vol > 2 days ago > 4 days ago)
vol_rising = (volume_df.shift(1) > volume_df.shift(3)) & (volume_df.shift(3) > volume_df.shift(5))

# OBV
direction = np.sign(diff.fillna(0))
obv = (volume_df * direction).cumsum()
obv_max = obv.rolling(20).max()
obv_min = obv.rolling(20).min()
obv_range = obv_max - obv_min
obv_pct = (obv - obv_min) / obv_range.replace(0, np.nan)
obv_high = obv_pct >= 0.75

# ─────────────────────────────────────────────────────────────
# SCORING & SIGNALS
# ─────────────────────────────────────────────────────────────
print("Generating signals...")

s1 = macd_state | macd_bullish
s2 = adx >= 25
s3 = bb_squeeze | bb_above_upper
s4 = (prices_df > ema9) & (ema9 > ema21) & (ema21 > ema50)
s5 = (prices_df - prices_df.shift(1)) >= (1.5 * atr) # Impulsive breakout candle
s6 = (rsi >= 55) & (rsi <= 75)
s7 = spiked_vol
s8 = vol_dried_up
s9 = vol_rising
s10 = obv_high

total_score = s1.astype(int) + s2.astype(int) + s3.astype(int) + s4.astype(int) + s5.astype(int) + \
              s6.astype(int) + s7.astype(int) + s8.astype(int) + s9.astype(int) + s10.astype(int)

# Hard filters:
hard_trend = (prices_df > ema50) & (ema21 > ema50)
hard_volume = s7 | s9

# Core Breakout Condition
# Note: since we only check close, a stock could have broken out above range_high today.
breakout_today = (prices_df > range_high) & (prices_df.shift(1) <= range_high)
close_to_52h = ((high_52wk - prices_df) / high_52wk * 100) <= PCT_FROM_52WK_HIGH

buy_signals = breakout_today & close_to_52h & (total_score >= MIN_CONFIRM_SCORE) & hard_trend & hard_volume
buy_signals = buy_signals.fillna(False)

# Convert boolean matrix to list of events
signal_indices = np.where(buy_signals)
dates_idx = signal_indices[0]
tickers_idx = signal_indices[1]

trade_signals = []
for i in range(len(dates_idx)):
    dt = prices_df.index[dates_idx[i]]
    tkr = prices_df.columns[tickers_idx[i]]
    start_price = prices_df.at[dt, tkr]
    atr_val = atr.at[dt, tkr]
    score_val = total_score.at[dt, tkr]
    
    stop_loss = start_price - 1.5 * atr_val
    target = start_price + RR_RATIO * atr_val
    
    trade_signals.append({
        'date': dt,
        'ticker': tkr,
        'entry': start_price,
        'stop': stop_loss,
        'target': target,
        'atr': atr_val,
        'score': score_val
    })

trade_signals = pd.DataFrame(trade_signals)
if not trade_signals.empty:
    trade_signals = trade_signals.sort_values(by="date")
    print(f"Found {len(trade_signals)} initial BUY signals across history.")
else:
    print("Found 0 BUY signals. Aborting backtest.")
    exit()

# ─────────────────────────────────────────────────────────────
# EVENT-DRIVEN PORTFOLIO SIMULATION
# ─────────────────────────────────────────────────────────────
print("Running event-driven portfolio simulation...")

capital = STARTING_CAPITAL
active_trades = [] # dicts of open trades
closed_trades = []
equity_curve = []
dates_list = prices_df.index

# Group signals by date
signals_by_date = trade_signals.groupby("date")

for dt in tqdm(dates_list, desc="Simulation Days"):
    # 1. Evaluate Active Trades (Exits) using today's Close
    still_active = []
    
    # We only have `Close` price, so we process exits logic against today's close
    for tr in active_trades:
        tkr = tr['ticker']
        if pd.isna(prices_df.at[dt, tkr]):
             still_active.append(tr)
             continue
             
        today_close = prices_df.at[dt, tkr]
        tr['days_held'] += 1
        
        exited = False
        exit_price = today_close
        exit_reason = ""
        
        # Rule 1: Hard Stop / Trailing Stop
        if today_close <= tr['current_stop']:
            exited = True
            exit_reason = "Stop Loss"
            
        # Rule 2: Target (DISABLED - Let Winners Run)
        # elif today_close >= tr['target']:
        #     exited = True
        #     exit_reason = "Target"
            
        # Rule 3: Time Stop (No meaningful move in TIME_STOP_DAYS)
        elif tr['days_held'] >= TIME_STOP_DAYS and today_close < tr['entry'] + 0.5 * tr['atr']:
            exited = True
            exit_reason = "Time Stop"
            
        else:
            # Check Trailing Triggers
            # Breakeven condition
            if today_close >= tr['entry'] + 1.5 * tr['atr'] and tr['current_stop'] < tr['entry']:
                tr['current_stop'] = tr['entry']
            # Trail at 90% of price once it crosses +3.0 ATR
            if today_close >= tr['entry'] + 3.0 * tr['atr']:
                new_trail = today_close * 0.90
                if new_trail > tr['current_stop']:
                    tr['current_stop'] = new_trail
            # Trailing after +2.5 ATR
            elif today_close >= tr['entry'] + 2.5 * tr['atr']:
                new_trail = today_close - 1.5 * tr['atr']
                if new_trail > tr['current_stop']:
                    tr['current_stop'] = new_trail
                    
        if exited:
            # Calculate PnL (ignoring slippage for now)
            pct_return = (exit_price - tr['entry']) / tr['entry']
            profit_loss = tr['invested'] * pct_return
            capital += tr['invested'] + profit_loss
            
            closed_trades.append({
                'ticker': tkr,
                'entry_date': tr['entry_date'],
                'exit_date': dt,
                'entry': tr['entry'],
                'exit': exit_price,
                'pct_return': pct_return,
                'pnl': profit_loss,
                'reason': exit_reason,
                'score': tr['score']
            })
        else:
            still_active.append(tr)
            
    active_trades = still_active

    # 2. Open New Trades if signal exists
    if dt in signals_by_date.groups:
        todays_signals = signals_by_date.get_group(dt)
        # Sort by highest score first
        todays_signals = todays_signals.sort_values(by="score", ascending=False)
        
        for _, sig in todays_signals.iterrows():
            if len(active_trades) < MAX_CONCURRENT and capital >= TRADE_AMOUNT:
                # Open trade
                capital -= TRADE_AMOUNT
                active_trades.append({
                    'ticker': sig['ticker'],
                    'entry_date': dt,
                    'entry': sig['entry'],
                    'target': sig['target'],
                    'atr': sig['atr'],
                    'current_stop': sig['stop'],
                    'invested': TRADE_AMOUNT,
                    'days_held': 0,
                    'score': sig['score']
                })

    # Record Mark-To-Market Equity
    open_value = 0
    for tr in active_trades:
        current_close = prices_df.at[dt, tr['ticker']]
        if not pd.isna(current_close):
            open_value += tr['invested'] * (current_close / tr['entry'])
        else:
            open_value += tr['invested']
            
    equity_curve.append({'date': dt, 'equity': capital + open_value})

# ─────────────────────────────────────────────────────────────
# RESULTS & METRICS
# ─────────────────────────────────────────────────────────────
trades_df = pd.DataFrame(closed_trades)
trades_df.to_csv(OUTPUT_TRADES, index=False)

eq_df = pd.DataFrame(equity_curve).set_index("date")
final_equity = eq_df['equity'].iloc[-1]
returns = eq_df['equity'].pct_change().dropna()
cum_rets = (1 + returns).cumprod()
peak = cum_rets.cummax()
drawdown = (cum_rets - peak) / peak

wins = trades_df[trades_df['pnl'] > 0]
losses = trades_df[trades_df['pnl'] <= 0]
win_rate = len(wins) / len(trades_df) if len(trades_df) > 0 else 0

total_return = (final_equity / STARTING_CAPITAL - 1) * 100
max_drawdown = drawdown.min() * 100
avg_profit = wins['pnl'].mean() if len(wins) > 0 else 0
avg_loss = losses['pnl'].mean() if len(losses) > 0 else 0
avg_hold = (trades_df['exit_date'] - trades_df['entry_date']).dt.days.mean()

print("\n" + "="*60)
print("  BACKTEST RESULTS")
print("="*60)
print(f"Total Trades        : {len(trades_df)}")
print(f"Win Rate            : {win_rate:.1%}")
print(f"Total Gross Return  : {total_return:.2f}% (Starting: {STARTING_CAPITAL:,.0f} | Ending: {final_equity:,.0f})")
print(f"Max Drawdown        : {max_drawdown:.2f}%")
print(f"Avg Profit/Trade    : INR {avg_profit:,.2f}")
print(f"Avg Loss/Trade      : INR {avg_loss:,.2f}")
print(f"Average Hold Days   : {avg_hold:.1f} days")
print(f"Most Frequent Exit  : {trades_df['reason'].value_counts().idxmax()} ({trades_df['reason'].value_counts().max()}x)")
print("="*60)
print(f"Detailed trades saved to: {OUTPUT_TRADES}")

# Plot Equity Curve
plt.figure(figsize=(10, 5))
plt.plot(eq_df.index, eq_df['equity'], label="Portfolio Equity", color="#2E75B6", linewidth=2)
plt.fill_between(eq_df.index, STARTING_CAPITAL, eq_df['equity'], where=eq_df['equity']>=STARTING_CAPITAL, interpolate=True, color='green', alpha=0.1)
plt.fill_between(eq_df.index, STARTING_CAPITAL, eq_df['equity'], where=eq_df['equity']<STARTING_CAPITAL, interpolate=True, color='red', alpha=0.1)

plt.axhline(STARTING_CAPITAL, color='gray', linestyle='--', label="Starting Capital")
plt.title(f"Breakout Scanner Backtest Equity Curve (Fixed INR {TRADE_AMOUNT:,.0f} / trade)", fontsize=13)
plt.xlabel("Date", fontsize=11)
plt.ylabel("Portfolio Value (INR)", fontsize=11)
plt.legend()
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(OUTPUT_EQUITY_CHART, dpi=150)
print(f"Equity curve chart saved to: {OUTPUT_EQUITY_CHART}\n")
