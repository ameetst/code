import pandas as pd
import numpy as np
from scipy.stats import linregress

def calculate_indicators(df, index_closes=None):
    """
    Calculates technical indicators for a single stock's dataframe.
    df should have 'Close', 'High', 'Low', 'Volume'.
    index_closes: optional pd.Series of Nifty 500 closes aligned to trading dates,
                  used to compute 3M relative strength vs the index.
    """
    if len(df) < 200:
        return None  # Not enough data

    df = df.copy()

    # EMAs
    df['50EMA'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['200EMA'] = df['Close'].ewm(span=200, adjust=False).mean()

    # 52-Week High (approx 252 trading days)
    df['52W_High'] = df['High'].rolling(window=252, min_periods=100).max()
    # P/52H: ratio of current Close to 52W High. 1.0 = at the high, 0.85 = 15% below.
    df['P/52H'] = df['Close'] / df['52W_High']

    # 6M HIGH — uses the lagged high to stay consistent with 6M BO calculation
    df['6M_High'] = df['High'].shift(5).rolling(window=126, min_periods=80).max()

    # Breakouts: 90 and 180 calendar days, lagged by 7 days.
    # Since yfinance gives us trading days, 90 calendar days is ~63 trading days.
    # 7 days lag is ~5 trading days.

    # High over the past 63 trading days (lagged by 5 days)
    df['3M_High_Lagged'] = df['High'].shift(5).rolling(window=63, min_periods=40).max()
    # High over the past 126 trading days (lagged by 5 days)
    df['6M_High_Lagged'] = df['High'].shift(5).rolling(window=126, min_periods=80).max()

    # 1M breakout (21 trading days, lagged by 5 days)
    df['1M_High_Lagged'] = df['High'].shift(5).rolling(window=21, min_periods=15).max()

    # Calculate BO: >0 means current Close is higher than the historical high
    df['3M BO'] = (df['Close'] - df['3M_High_Lagged']) / df['3M_High_Lagged']
    df['6M BO'] = (df['Close'] - df['6M_High_Lagged']) / df['6M_High_Lagged']
    df['1M BO'] = (df['Close'] - df['1M_High_Lagged']) / df['1M_High_Lagged']

    # Risk/Reward ratios
    # 3M R/R: ratio of upside potential (distance to 3M high) vs downside (distance below)
    df['3M_Low_Lagged'] = df['Low'].shift(5).rolling(window=63, min_periods=40).min()
    df['6M_Low_Lagged'] = df['Low'].shift(5).rolling(window=126, min_periods=80).min()
    df['3M R/R'] = (df['3M_High_Lagged'] - df['Close']) / (df['Close'] - df['3M_Low_Lagged']).replace(0, np.nan)
    df['6M R/R'] = (df['6M_High_Lagged'] - df['Close']) / (df['Close'] - df['6M_Low_Lagged']).replace(0, np.nan)

    # Volumes: Median volume over 1M (~21 days) and Prior 3M (~63 days, shifted by 21)
    df['1M_MED_VOL'] = df['Volume'].rolling(window=21).median()
    df['Prior_3M_MED_VOL'] = df['Volume'].shift(21).rolling(window=63).median()
    df['V_RANK'] = df['1M_MED_VOL'] / df['Prior_3M_MED_VOL']

    # INR_VOL: median daily traded value in INR over last 21 days (shares × close price)
    # Used for liquidity filtering. 1 Cr = 10_000_000 INR.
    df['Daily_INR_Vol'] = df['Volume'] * df['Close']
    df['INR_VOL'] = df['Daily_INR_Vol'].rolling(window=21).median()

    # RS_3M: 3-month relative strength vs Nifty 500.
    # = (stock 3M return) - (index 3M return), expressed as a decimal.
    # Positive = outperforming the index over 3 months.
    if index_closes is not None:
        try:
            # Align index to stock dates
            idx_aligned = index_closes.reindex(df.index, method='ffill')
            stock_ret_3m = df['Close'] / df['Close'].shift(63) - 1
            index_ret_3m = idx_aligned / idx_aligned.shift(63) - 1
            df['RS_3M'] = stock_ret_3m - index_ret_3m
        except Exception:
            df['RS_3M'] = np.nan
    else:
        df['RS_3M'] = np.nan

    # LOSS = 10% of current price (i.e. the stop loss amount)
    df['LOSS'] = df['Close'] * 0.10

    return df


def get_latest_r_squared(series):
    """Calculates the R-squared of a linear regression on the given series."""
    if len(series) < 2:
        return 0.0
    x = np.arange(len(series))
    y = series.values
    # Filter out NaN
    mask = ~np.isnan(y)
    if mask.sum() < 2:
        return 0.0
    slope, intercept, r_value, p_value, std_err = linregress(x[mask], y[mask])
    return r_value ** 2


def check_regime(index_data):
    """Check the market regime: Nifty 500 Close vs 50EMA."""
    try:
        if isinstance(index_data.columns, pd.MultiIndex):
            close_series = index_data['Close'].iloc[:, 0]
        else:
            close_series = index_data['Close']

        ema_series = close_series.ewm(span=50, adjust=False).mean()

        nifty_close = float(close_series.iloc[-1])
        nifty_ema = float(ema_series.iloc[-1])
        regime_bullish = nifty_close > nifty_ema

        return regime_bullish, nifty_close, nifty_ema
    except Exception as e:
        print(f"Error checking market regime: {e}")
        return True, 0.0, 0.0


def build_universe_table(market_data, index_data=None):
    """
    Builds a full indicator table for all stocks in the universe,
    matching the column layout of the Excel 'DATA' sheet.

    index_data: optional index DataFrame (Nifty 500) used to compute RS_3M.
    Returns a DataFrame with one row per stock.
    """
    all_stocks = []

    # Extract index close series once for RS calculation
    index_closes = None
    if index_data is not None:
        try:
            if isinstance(index_data.columns, pd.MultiIndex):
                index_closes = index_data['Close'].iloc[:, 0]
            else:
                index_closes = index_data['Close']
        except Exception:
            index_closes = None

    # Get ticker list
    if isinstance(market_data.columns, pd.MultiIndex):
        tickers = market_data.columns.get_level_values(0).unique().tolist()
    else:
        tickers = [market_data.columns.name] if market_data.columns.name else []

    for ticker in tickers:
        try:
            if isinstance(market_data.columns, pd.MultiIndex):
                df = market_data[ticker].dropna(subset=['Close'])
            else:
                df = market_data.dropna(subset=['Close'])

            if len(df) < 200:
                continue

            df = calculate_indicators(df, index_closes=index_closes)
            if df is None:
                continue

            latest = df.iloc[-1]
            # R-Squared on last 63 trading days (~3 months)
            r2 = get_latest_r_squared(df['Close'].tail(63))

            # 6M R/R: filter out negative values (meaningless — price below range low)
            rr6m = latest.get('6M R/R', np.nan)
            if pd.notna(rr6m) and rr6m < 0:
                rr6m = np.nan

            all_stocks.append({
                'Symbol':     ticker.replace('.NS', ''),
                'Price':      latest['Close'],
                'P/52H':      latest['P/52H'],
                '50EMA':      latest['50EMA'],
                '200EMA':     latest['200EMA'],
                '3M BO':      latest['3M BO'],
                '6M BO':      latest['6M BO'],
                'VCHK':       latest['V_RANK'],
                '3M R/R':     latest.get('3M R/R', np.nan),
                '6M R/R':     rr6m,
                '6M HIGH':    latest.get('6M_High_Lagged', np.nan),
                'LOSS':       latest['LOSS'],
                'R-Squared':  r2,
                'RS_3M':      latest.get('RS_3M', np.nan),
                'INR_VOL':    latest.get('INR_VOL', np.nan),
            })
        except Exception:
            pass

    return pd.DataFrame(all_stocks)


def run_screener(universe_df, r2_threshold=0.6, regime_bullish=True):
    """
    Filters the universe table to find stocks meeting all entry criteria.
    R-Squared is pre-computed in build_universe_table — no second pass needed.
    Candidates are sorted by RS_3M descending (strongest relative strength first).
    """
    if not regime_bullish:
        return pd.DataFrame()

    if universe_df.empty:
        return pd.DataFrame()

    df = universe_df.copy()

    # Rule 1: Trend Alignment — Price > 50EMA > 200EMA
    df = df[(df['Price'] > df['50EMA']) & (df['50EMA'] > df['200EMA']) & (df['200EMA'] > 0)]

    # Rule 2: 3M BO > 0 AND 6M BO < 0
    df = df[(df['3M BO'] > 0) & (df['6M BO'] < 0)]

    # Rule 3: Proximity — 3M BO < 0.10 (within 10% of breakout level)
    df = df[df['3M BO'] < 0.10]

    # Rule 4: Volume Confirmation — VCHK > 1.5
    df = df[df['VCHK'] > 1.5]

    # Rule 5: Liquidity — median daily INR traded volume >= ₹1 Crore (10_000_000)
    INR_1CR = 10_000_000
    if 'INR_VOL' in df.columns:
        df = df[df['INR_VOL'].notna() & (df['INR_VOL'] >= INR_1CR)]

    # Rule 6: R-Squared (smoothness) — pre-computed in build_universe_table
    if 'R-Squared' in df.columns:
        df = df[df['R-Squared'].notna() & (df['R-Squared'] > r2_threshold)]

    # Sort by RS_3M descending — strongest relative performers vs Nifty 500 ranked first.
    # Falls back to P/52H if RS_3M is unavailable.
    if 'RS_3M' in df.columns and df['RS_3M'].notna().any():
        df = df.sort_values(by='RS_3M', ascending=False).reset_index(drop=True)
    else:
        df = df.sort_values(by='P/52H', ascending=False).reset_index(drop=True)

    return df
