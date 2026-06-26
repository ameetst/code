"""
backtest.py — 5-Year Portfolio Backtest for the Daily Breakout Strategy
========================================================================
Implements the full algorithm from Strategy.md §5:
  - T+1 execution at Open price
  - 0.15% slippage/brokerage per trade
  - 10% trailing stop loss on Close (gap-down protection at T+1 Open)
  - 10 max concurrent positions, equal-weighted (10% equity each)
  - Signal ranking by RS_3M descending
  - Regime filter: no new entries when Nifty 500 < 50EMA

Usage:
    python backtest.py
    python backtest.py --years 5 --capital 1000000 --csv path/to/universe.csv

Outputs (written to ./backtest_results/):
    trade_log.csv       — every entry and exit with P&L
    equity_curve.csv    — daily portfolio value
    summary.txt         — performance statistics
"""

import pandas as pd
import numpy as np
from scipy.stats import linregress
import datetime
import argparse
import os
import sys
import matplotlib
matplotlib.use('Agg')   # non-interactive backend — safe for scripts
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

# ── Re-use data_engine fetch functions ────────────────────────────────
try:
    from data_engine import get_nifty_universe, fetch_market_data, fetch_index_data
except ImportError:
    print("ERROR: data_engine.py not found. Place backtest.py in the same directory.")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════
# INDICATOR ENGINE (vectorised per-stock, full history)
# ══════════════════════════════════════════════════════════════════════

def compute_indicators_full(df, index_closes):
    """
    Computes all strategy indicators on the full price history of one stock.
    Returns a DataFrame with one row per trading day.
    No look-ahead: all rolling/shift operations are causal.
    """
    if len(df) < 200:
        return None

    df = df.copy()

    df['50EMA']  = df['Close'].ewm(span=50,  adjust=False).mean()
    df['200EMA'] = df['Close'].ewm(span=200, adjust=False).mean()

    df['52W_High']      = df['High'].rolling(252, min_periods=100).max()
    df['P52H']          = df['Close'] / df['52W_High']

    df['3M_High_Lag']   = df['High'].shift(5).rolling(63,  min_periods=40).max()
    df['6M_High_Lag']   = df['High'].shift(5).rolling(126, min_periods=80).max()
    df['3M_Low_Lag']    = df['Low'].shift(5).rolling(63,   min_periods=40).min()

    df['3M_BO']         = (df['Close'] - df['3M_High_Lag']) / df['3M_High_Lag']
    df['6M_BO']         = (df['Close'] - df['6M_High_Lag']) / df['6M_High_Lag']

    df['1M_MED_VOL']       = df['Volume'].rolling(21).median()
    df['Prior_3M_MED_VOL'] = df['Volume'].shift(21).rolling(63).median()
    df['VCHK']             = df['1M_MED_VOL'] / df['Prior_3M_MED_VOL'].replace(0, np.nan)

    df['INR_VOL']  = (df['Volume'] * df['Close']).rolling(21).median()

    # R-Squared: rolling 63-day regression — vectorised via expanding apply
    df['R2'] = (
        df['Close']
        .rolling(63, min_periods=40)
        .apply(_r2_rolling, raw=True)
    )

    # RS_3M: stock 3M return minus index 3M return
    if index_closes is not None:
        idx = index_closes.reindex(df.index, method='ffill')
        df['RS_3M'] = (df['Close'] / df['Close'].shift(63) - 1) - \
                      (idx / idx.shift(63) - 1)
    else:
        df['RS_3M'] = np.nan

    return df


def _r2_rolling(y):
    """Raw numpy function for rolling R² — called by .rolling().apply()."""
    n = len(y)
    if n < 10:
        return np.nan
    x = np.arange(n)
    mask = ~np.isnan(y)
    if mask.sum() < 10:
        return np.nan
    slope, intercept, r, *_ = linregress(x[mask], y[mask])
    return r ** 2


# ══════════════════════════════════════════════════════════════════════
# REGIME FILTER (vectorised over full index history)
# ══════════════════════════════════════════════════════════════════════

def compute_regime(index_data):
    """
    Returns a boolean Series (indexed by date): True = Bullish regime.
    Nifty 500 Close > 50EMA.
    """
    if isinstance(index_data.columns, pd.MultiIndex):
        close = index_data['Close'].iloc[:, 0]
    else:
        close = index_data['Close']
    ema50 = close.ewm(span=50, adjust=False).mean()
    return (close > ema50).rename('regime_bullish')


# ══════════════════════════════════════════════════════════════════════
# SIGNAL GENERATION (per day, all stocks)
# ══════════════════════════════════════════════════════════════════════

def generate_signals(indicators, date,
                     r2_threshold=0.60,
                     vchk_threshold=1.5,
                     inr_floor=10_000_000):
    """
    Given the indicators dict {ticker: full_indicator_df} and a date,
    returns a DataFrame of stocks passing all entry rules on that date,
    sorted by RS_3M descending.
    """
    rows = []
    for ticker, df in indicators.items():
        if date not in df.index:
            continue
        row = df.loc[date]

        # All 6 entry rules
        try:
            if not (row['Close'] > row['50EMA'] > row['200EMA'] > 0):
                continue
            if not (row['3M_BO'] > 0 and row['6M_BO'] < 0):
                continue
            if not (row['3M_BO'] < 0.10):
                continue
            if not (row['VCHK'] > vchk_threshold):
                continue
            if not (pd.notna(row['INR_VOL']) and row['INR_VOL'] >= inr_floor):
                continue
            if not (pd.notna(row['R2']) and row['R2'] > r2_threshold):
                continue
        except (KeyError, TypeError):
            continue

        rows.append({
            'ticker':  ticker,
            'close':   row['Close'],
            'rs3m':    row.get('RS_3M', np.nan),
            'p52h':    row.get('P52H',  np.nan),
            'r2':      row['R2'],
            'loss_pct': 0.10,
        })

    if not rows:
        return pd.DataFrame()

    sig = pd.DataFrame(rows)
    # Rank: RS_3M descending, fall back to P/52H descending
    if sig['rs3m'].notna().any():
        sig = sig.sort_values('rs3m', ascending=False)
    else:
        sig = sig.sort_values('p52h', ascending=False)
    return sig.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO SIMULATION
# ══════════════════════════════════════════════════════════════════════

SLIPPAGE = 0.0015   # 0.15% per trade
MAX_POSITIONS = 10

def run_backtest(market_data, index_data, initial_capital=1_000_000,
                 r2_threshold=0.60, vchk_threshold=1.5,
                 inr_floor=10_000_000):
    """
    Main backtest loop. Returns:
        trade_log  : pd.DataFrame — one row per closed trade
        equity_curve: pd.Series  — daily portfolio value
    """
    print("Computing indicators for all tickers...")

    # ── Index closes for RS_3M ────────────────────────────────────────
    if isinstance(index_data.columns, pd.MultiIndex):
        index_closes = index_data['Close'].iloc[:, 0]
    else:
        index_closes = index_data['Close']

    regime = compute_regime(index_data)

    # ── Pre-compute indicators per ticker ─────────────────────────────
    if isinstance(market_data.columns, pd.MultiIndex):
        tickers = market_data.columns.get_level_values(0).unique().tolist()
    else:
        tickers = []

    indicators = {}
    for i, ticker in enumerate(tickers):
        if i % 50 == 0:
            print(f"  Processing {i}/{len(tickers)} tickers...")
        try:
            df = market_data[ticker].dropna(subset=['Close'])
            result = compute_indicators_full(df, index_closes)
            if result is not None:
                indicators[ticker] = result
        except Exception:
            continue

    print(f"Indicators computed for {len(indicators)} tickers.")

    # ── Trading dates: union of all indicator dates ───────────────────
    all_dates = sorted(set().union(*[set(df.index) for df in indicators.values()]))
    all_dates = [d for d in all_dates if d in regime.index]

    # ── Portfolio state ───────────────────────────────────────────────
    cash         = float(initial_capital)
    positions    = {}   # ticker → {shares, entry_price, entry_date, cost, high_since_entry}
    trade_log    = []
    equity_curve = {}

    for idx, date in enumerate(all_dates):
        is_bullish = bool(regime.get(date, False))

        # ── 1. Check exits on today's CLOSE (TSL triggered?) ──────────
        # We check if close fell 10% below the running high.
        # Actual exit executes at T+1 Open (handled next iteration).
        tsl_triggered = set()
        for ticker, pos in positions.items():
            if date not in indicators[ticker].index:
                continue
            close = indicators[ticker].loc[date, 'Close']
            pos['high_since_entry'] = max(pos['high_since_entry'], close)
            stop_price = pos['high_since_entry'] * 0.90
            if close <= stop_price:
                tsl_triggered.add(ticker)

        # ── 2. Execute TSL exits at T+1 OPEN ─────────────────────────
        next_idx = idx + 1
        next_date = all_dates[next_idx] if next_idx < len(all_dates) else None

        for ticker in list(tsl_triggered):
            pos = positions.pop(ticker)
            exit_price = pos['high_since_entry'] * 0.90  # theoretical stop

            # Gap protection: if T+1 Open is below stop, use actual open
            if next_date is not None and next_date in indicators[ticker].index:
                t1_open = indicators[ticker].loc[next_date].get('Open', exit_price)
                if pd.notna(t1_open) and t1_open < exit_price:
                    exit_price = float(t1_open)   # gap-down: exit at open

            exit_price_net = exit_price * (1 - SLIPPAGE)
            proceeds = pos['shares'] * exit_price_net
            cash += proceeds
            pnl = proceeds - pos['cost']
            pnl_pct = pnl / pos['cost']

            trade_log.append({
                'ticker':      ticker.replace('.NS', ''),
                'entry_date':  pos['entry_date'],
                'exit_date':   next_date or date,
                'entry_price': pos['entry_price'],
                'exit_price':  round(exit_price_net, 2),
                'shares':      pos['shares'],
                'cost':        round(pos['cost'], 2),
                'proceeds':    round(proceeds, 2),
                'pnl':         round(pnl, 2),
                'pnl_pct':     round(pnl_pct * 100, 2),
                'exit_reason': 'TSL',
            })

        # ── 3. Generate new signals if regime is bullish ───────────────
        if is_bullish and next_date is not None:
            n_open = len(positions)
            slots   = MAX_POSITIONS - n_open
            if slots > 0:
                signals = generate_signals(
                    indicators, date,
                    r2_threshold=r2_threshold,
                    vchk_threshold=vchk_threshold,
                    inr_floor=inr_floor,
                )

                # Skip if no signals or DataFrame is empty/has no columns
                if signals.empty or 'ticker' not in signals.columns:
                    continue

                # Exclude already-held tickers
                signals = signals[~signals['ticker'].isin(positions.keys())]
                if signals.empty:
                    continue

                for _, sig in signals.head(slots).iterrows():
                    ticker = sig['ticker']
                    if ticker not in indicators:
                        continue
                    # T+1 execution at next day's Open
                    if next_date not in indicators[ticker].index:
                        continue
                    t1_open = indicators[ticker].loc[next_date].get('Open', np.nan)
                    if pd.isna(t1_open) or t1_open <= 0:
                        continue

                    # Position size: 10% of current equity
                    equity = cash + sum(
                        p['shares'] * indicators[p_ticker].loc[date, 'Close']
                        for p_ticker, p in positions.items()
                        if date in indicators[p_ticker].index
                    )
                    alloc  = equity * (1 / MAX_POSITIONS)
                    if alloc > cash:
                        alloc = cash   # don't allocate more than available cash

                    buy_price  = float(t1_open) * (1 + SLIPPAGE)
                    shares     = int(alloc / buy_price)
                    if shares < 1:
                        continue

                    cost  = shares * buy_price
                    cash -= cost

                    positions[ticker] = {
                        'shares':            shares,
                        'entry_price':       round(buy_price, 2),
                        'entry_date':        next_date,
                        'cost':              cost,
                        'high_since_entry':  float(t1_open),
                    }

        # ── 4. Mark-to-market equity ───────────────────────────────────
        mtm = cash
        for ticker, pos in positions.items():
            if date in indicators[ticker].index:
                mtm += pos['shares'] * indicators[ticker].loc[date, 'Close']
        equity_curve[date] = mtm

    # ── Force-close any open positions at last available price ─────────
    last_date = all_dates[-1]
    for ticker, pos in positions.items():
        if last_date in indicators[ticker].index:
            exit_price = indicators[ticker].loc[last_date, 'Close'] * (1 - SLIPPAGE)
        else:
            exit_price = pos['entry_price']
        proceeds = pos['shares'] * exit_price
        pnl      = proceeds - pos['cost']
        trade_log.append({
            'ticker':      ticker.replace('.NS', ''),
            'entry_date':  pos['entry_date'],
            'exit_date':   last_date,
            'entry_price': pos['entry_price'],
            'exit_price':  round(exit_price, 2),
            'shares':      pos['shares'],
            'cost':        round(pos['cost'], 2),
            'proceeds':    round(proceeds, 2),
            'pnl':         round(pnl, 2),
            'pnl_pct':     round(pnl / pos['cost'] * 100, 2),
            'exit_reason': 'END_OF_BACKTEST',
        })

    trade_df  = pd.DataFrame(trade_log)
    equity_s  = pd.Series(equity_curve)
    return trade_df, equity_s


# ══════════════════════════════════════════════════════════════════════
# PERFORMANCE STATISTICS
# ══════════════════════════════════════════════════════════════════════

def compute_stats(trade_df, equity_curve, initial_capital, index_data):
    """Computes and returns a stats dict."""
    if trade_df.empty or equity_curve.empty:
        return {}

    final_equity  = equity_curve.iloc[-1]
    total_return  = (final_equity / initial_capital - 1) * 100
    n_days        = (equity_curve.index[-1] - equity_curve.index[0]).days
    n_years       = n_days / 365.25
    cagr          = ((final_equity / initial_capital) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

    daily_returns = equity_curve.pct_change().dropna()
    sharpe        = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                     if daily_returns.std() > 0 else 0)

    roll_max      = equity_curve.cummax()
    drawdown      = (equity_curve - roll_max) / roll_max
    max_dd        = drawdown.min() * 100

    # Benchmark: Nifty 500
    if isinstance(index_data.columns, pd.MultiIndex):
        idx_close = index_data['Close'].iloc[:, 0]
    else:
        idx_close = index_data['Close']
    idx_close = idx_close.reindex(equity_curve.index, method='ffill').dropna()
    if len(idx_close) >= 2:
        bench_return = (idx_close.iloc[-1] / idx_close.iloc[0] - 1) * 100
        bench_cagr   = ((idx_close.iloc[-1] / idx_close.iloc[0]) ** (1 / n_years) - 1) * 100
    else:
        bench_return = bench_cagr = 0

    closed = trade_df[trade_df['exit_reason'] != 'END_OF_BACKTEST']
    n_trades   = len(closed)
    n_winners  = (closed['pnl'] > 0).sum()
    win_rate   = n_winners / n_trades * 100 if n_trades else 0
    avg_win    = closed.loc[closed['pnl'] > 0, 'pnl_pct'].mean() if n_winners else 0
    avg_loss   = closed.loc[closed['pnl'] <= 0, 'pnl_pct'].mean() if (n_trades - n_winners) else 0
    profit_factor = (
        closed.loc[closed['pnl'] > 0, 'pnl'].sum() /
        abs(closed.loc[closed['pnl'] <= 0, 'pnl'].sum())
        if (closed['pnl'] <= 0).any() else np.inf
    )

    return {
        'Initial Capital':      f"₹{initial_capital:,.0f}",
        'Final Equity':         f"₹{final_equity:,.0f}",
        'Total Return':         f"{total_return:.1f}%",
        'CAGR':                 f"{cagr:.1f}%",
        'Sharpe Ratio':         f"{sharpe:.2f}",
        'Max Drawdown':         f"{max_dd:.1f}%",
        'Benchmark Return':     f"{bench_return:.1f}%  (Nifty 500)",
        'Benchmark CAGR':       f"{bench_cagr:.1f}%  (Nifty 500)",
        'Total Closed Trades':  n_trades,
        'Win Rate':             f"{win_rate:.1f}%",
        'Avg Win':              f"{avg_win:.1f}%",
        'Avg Loss':             f"{avg_loss:.1f}%",
        'Profit Factor':        f"{profit_factor:.2f}",
        'Backtest Period':      f"{equity_curve.index[0].date()} → {equity_curve.index[-1].date()}",
    }


# ══════════════════════════════════════════════════════════════════════
# EQUITY CURVE CHART
# ══════════════════════════════════════════════════════════════════════

def plot_equity_curve(equity_curve, index_data, initial_capital, stats, out_path):
    """
    Generates a 2-panel PNG chart:
      Top panel   — Strategy equity vs Nifty 500 (both rebased to initial capital)
      Bottom panel— Portfolio drawdown (shaded red)
    """
    # ── Benchmark: rebase Nifty 500 to initial capital ────────────────
    if isinstance(index_data.columns, pd.MultiIndex):
        idx_close = index_data['Close'].iloc[:, 0]
    else:
        idx_close = index_data['Close']
    idx_close = idx_close.reindex(equity_curve.index, method='ffill').dropna()
    bench = idx_close / idx_close.iloc[0] * initial_capital

    # ── Drawdown series ───────────────────────────────────────────────
    roll_max  = equity_curve.cummax()
    drawdown  = (equity_curve - roll_max) / roll_max * 100   # in %

    # ── Figure layout ─────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        2, 1,
        figsize=(14, 8),
        gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.08},
        sharex=True,
    )
    fig.patch.set_facecolor('#0e1117')
    for ax in (ax1, ax2):
        ax.set_facecolor('#0e1117')
        ax.tick_params(colors='#aaaaaa', labelsize=9)
        ax.spines[:].set_color('#333333')

    # ── Top panel: equity vs benchmark ────────────────────────────────
    ax1.plot(equity_curve.index, equity_curve.values,
             color='#00c896', linewidth=1.8, label='Strategy', zorder=3)
    ax1.plot(bench.index, bench.values,
             color='#5b9bd5', linewidth=1.2, linestyle='--',
             label='Nifty 500 (rebased)', zorder=2)
    ax1.fill_between(equity_curve.index, initial_capital, equity_curve.values,
                     where=(equity_curve.values >= initial_capital),
                     alpha=0.08, color='#00c896')

    # Annotate final values
    ax1.annotate(
        f"₹{equity_curve.iloc[-1]:,.0f}",
        xy=(equity_curve.index[-1], equity_curve.iloc[-1]),
        xytext=(-90, 8), textcoords='offset points',
        color='#00c896', fontsize=8.5, fontweight='bold',
    )
    ax1.annotate(
        f"₹{bench.iloc[-1]:,.0f}",
        xy=(bench.index[-1], bench.iloc[-1]),
        xytext=(-90, -16), textcoords='offset points',
        color='#5b9bd5', fontsize=8.5,
    )

    inr_fmt = FuncFormatter(lambda x, _: f"₹{x/1e5:.0f}L" if x < 1e7 else f"₹{x/1e7:.1f}Cr")
    ax1.yaxis.set_major_formatter(inr_fmt)
    ax1.set_ylabel('Portfolio Value', color='#aaaaaa', fontsize=9)
    ax1.legend(loc='upper left', facecolor='#1a1d27', edgecolor='#333333',
               labelcolor='#cccccc', fontsize=9)
    ax1.grid(axis='y', color='#222222', linewidth=0.5)
    ax1.grid(axis='x', color='#1a1a1a', linewidth=0.4)

    # Key stats annotation box
    stat_text = (
        f"CAGR: {stats.get('CAGR','—')}   "
        f"Sharpe: {stats.get('Sharpe Ratio','—')}   "
        f"Max DD: {stats.get('Max Drawdown','—')}   "
        f"Win Rate: {stats.get('Win Rate','—')}   "
        f"Trades: {stats.get('Total Closed Trades','—')}"
    )
    ax1.set_title(
        'Daily Breakout Strategy — Equity Curve vs Nifty 500',
        color='#eeeeee', fontsize=12, fontweight='bold', pad=10,
    )
    fig.text(0.5, 0.92, stat_text, ha='center', va='top',
             color='#999999', fontsize=8.5,
             bbox=dict(boxstyle='round,pad=0.4', facecolor='#1a1d27',
                       edgecolor='#333333', alpha=0.9))

    # ── Bottom panel: drawdown ────────────────────────────────────────
    ax2.fill_between(drawdown.index, 0, drawdown.values,
                     color='#e05252', alpha=0.7, linewidth=0)
    ax2.plot(drawdown.index, drawdown.values,
             color='#e05252', linewidth=0.8)
    ax2.set_ylabel('Drawdown %', color='#aaaaaa', fontsize=9)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax2.grid(axis='y', color='#222222', linewidth=0.5)
    ax2.grid(axis='x', color='#1a1a1a', linewidth=0.4)
    ax2.set_ylim(drawdown.min() * 1.15, 5)

    # ── X-axis date formatting ────────────────────────────────────────
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    ax2.xaxis.set_minor_locator(mdates.MonthLocator(bymonth=[4, 7, 10]))
    plt.setp(ax2.xaxis.get_majorticklabels(), color='#aaaaaa', fontsize=9)

    period = stats.get('Backtest Period', '')
    ax2.set_xlabel(f'Date  ({period})', color='#888888', fontsize=8.5)

    plt.savefig(out_path, dpi=150, bbox_inches='tight',
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Equity curve chart saved: {out_path}")


# ══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Run 5-year backtest for the Daily Breakout Strategy")
    parser.add_argument('--years',    type=int,   default=5,         help="Lookback years (default 5)")
    parser.add_argument('--capital',  type=float, default=1_000_000, help="Initial capital in INR (default 1,000,000)")
    parser.add_argument('--csv',      type=str,   default="ind_niftytotalmarket_list (3).csv",
                        help="Path to Nifty universe CSV")
    parser.add_argument('--r2',       type=float, default=0.60,      help="R-Squared threshold (default 0.60)")
    parser.add_argument('--vchk',     type=float, default=1.5,       help="VCHK threshold (default 1.5)")
    parser.add_argument('--out',      type=str,   default="backtest_results",
                        help="Output directory (default ./backtest_results)")
    parser.add_argument('--data',     type=str,   default=None,
                        help="Path to pre-downloaded market_data_5y.pkl (skips download if provided)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # ── Data: load cached or download fresh ───────────────────────────
    market_pkl = args.data or os.path.join(args.out, 'market_data_5y.pkl')
    index_pkl  = os.path.join(args.out, 'index_data_5y.pkl')

    if os.path.exists(market_pkl) and os.path.exists(index_pkl):
        print(f"Loading cached data from {market_pkl} ...")
        market_data = pd.read_pickle(market_pkl)
        index_data  = pd.read_pickle(index_pkl)
    else:
        print(f"Downloading {args.years}-year data for Nifty Total Market universe...")
        yf_tickers, _ = get_nifty_universe(args.csv)
        if not yf_tickers:
            print("ERROR: Could not read universe CSV.")
            sys.exit(1)

        market_data = fetch_market_data(yf_tickers, lookback_years=args.years)
        index_data  = fetch_index_data("^CRSLDX",    lookback_years=args.years)

        market_data.to_pickle(market_pkl)
        index_data.to_pickle(index_pkl)
        print(f"Data saved to {market_pkl} and {index_pkl}")

    # ── Run backtest ───────────────────────────────────────────────────
    print("\nStarting portfolio simulation...")
    trade_df, equity_curve = run_backtest(
        market_data, index_data,
        initial_capital=args.capital,
        r2_threshold=args.r2,
        vchk_threshold=args.vchk,
    )

    # ── Compute stats ──────────────────────────────────────────────────
    stats = compute_stats(trade_df, equity_curve, args.capital, index_data)

    # ── Save outputs ───────────────────────────────────────────────────
    trade_path  = os.path.join(args.out, 'trade_log.csv')
    equity_path = os.path.join(args.out, 'equity_curve.csv')
    chart_path  = os.path.join(args.out, 'equity_curve.png')
    stats_path  = os.path.join(args.out, 'summary.txt')

    trade_df.to_csv(trade_path, index=False)
    equity_curve.to_csv(equity_path, header=['equity'])

    # ── Equity curve chart ─────────────────────────────────────────────
    plot_equity_curve(equity_curve, index_data, args.capital, stats, chart_path)

    with open(stats_path, 'w') as f:
        f.write("=" * 50 + "\n")
        f.write("  DAILY BREAKOUT STRATEGY — BACKTEST SUMMARY\n")
        f.write("=" * 50 + "\n\n")
        for k, v in stats.items():
            f.write(f"  {k:<25} {v}\n")
        f.write(f"\n  R-Squared Threshold:      {args.r2}\n")
        f.write(f"  VCHK Threshold:           {args.vchk}\n")
        f.write(f"  Liquidity Floor:          ₹1 Crore / day\n")
        f.write(f"  Max Positions:            {MAX_POSITIONS}\n")
        f.write(f"  Position Sizing:          Equal weight (10% equity)\n")
        f.write(f"  Trailing Stop Loss:       10% from peak close\n")
        f.write(f"  Slippage/Brokerage:       0.15% per trade\n")
        f.write("\n" + "=" * 50 + "\n")

    # ── Print summary to console ───────────────────────────────────────
    print("\n" + open(stats_path).read())
    print(f"Outputs written to: {args.out}/")
    print(f"  {trade_path}")
    print(f"  {equity_path}")
    print(f"  {chart_path}")
    print(f"  {stats_path}")


if __name__ == '__main__':
    main()
