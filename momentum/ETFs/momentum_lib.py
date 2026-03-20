"""
momentum_lib.py
===============
Reusable computation library for momentum scoring of NSE stocks.

Functions
---------
load_prices(filepath)
    Load price data from the DATA sheet of an n500-format xlsx file.
    Returns (prices_df, nifty_series, stock_tickers, dates)

compute_sharpe(prices_df, stock_tickers, windows, rfr_daily, trading_days)
    Compute per-window Sharpe ratios and cross-sectional Z-scores.
    Returns (sharpe_df, z_df)
    z_df contains Z_<label> columns plus COMPOSITE, SHARPE_ALL,
    SHARPE_ST, SHARPE_LT, SHARPE_3, MOM_ACCEL.

compute_clenow(prices_df, stock_tickers, windows, trading_days)
    Compute per-window Clenow scores (AnnSlope × R²) and Z-scores.
    Returns (slope_df, r2_df, raw_df, cz_df)
    cz_df contains CZ_<label> columns plus CLENOW_Z.

compute_residual_momentum(prices_df, stock_tickers, nifty_series, windows, trading_days)
    Compute per-window residual Sharpe (OLS vs NIFTY500) and Z-scores.
    Returns (resmom_df, rs_z_df)
    rs_z_df contains RZ_<label> columns plus RES_MOM.

compute_returns(prices_df, stock_tickers)
    Compute 1M / 3M / 12M price returns for each stock.
    Returns ret_df with columns 1M%, 3M%, 12M%.

compute_pct_from_52h(prices_df, stock_tickers)
    Compute % distance from 52-week high for each stock.
    Returns a Series (negative = below high).

compute_market_regime(nifty_series)
    EMA-based market regime check on NIFTY500.
    Returns 'BUY', 'NOT BUY (Risk Off)', or 'UNKNOWN'.

normalise_composite(v)
    Non-linear normalisation: v>1 → v+1, v<0 → 1/(1-v), else unchanged.
"""

import datetime
import warnings

import numpy as np
import pandas as pd
import openpyxl
from scipy import stats

warnings.filterwarnings("ignore")


# ── DATA LOADING ──────────────────────────────────────────────────────────────

def load_prices(filepath: str):
    """
    Load the DATA sheet from an n500-format xlsx file.

    Returns
    -------
    prices_df     : DataFrame  (index=ticker, columns=dates) — stocks only
    nifty_series  : Series     — NIFTY500 daily closes
    stock_tickers : list[str]  — all tickers except NIFTY500
    dates         : list       — date objects for each price column
    """
    wb       = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    ws       = wb["DATA"]
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    header       = all_rows[0]
    date_indices = [i for i, h in enumerate(header)
                    if isinstance(h, (datetime.datetime, datetime.date))]
    dates        = [header[i] for i in date_indices]

    tickers, price_matrix = [], []
    for row in all_rows[1:]:
        if row[0] is None:
            continue
        px = []
        for i in date_indices:
            v = row[i]
            try:
                px.append(float(v) if v and float(v) > 0 else np.nan)
            except Exception:
                px.append(np.nan)
        tickers.append(str(row[0]).strip())
        price_matrix.append(px)

    prices_df     = pd.DataFrame(price_matrix, index=tickers, columns=dates)
    nifty_series  = prices_df.loc["NIFTY500"].copy()
    stock_tickers = [t for t in tickers if t != "NIFTY500"]
    prices_df     = prices_df.loc[stock_tickers]

    return prices_df, nifty_series, stock_tickers, dates


# ── SHARPE ────────────────────────────────────────────────────────────────────

def _sharpe_ratio(series: pd.Series, window: int,
                  rfr_daily: float, trading_days: int) -> float:
    """Annualised Sharpe for a single stock over `window` trading days."""
    px = series.dropna()
    if len(px) < window * 0.90:
        return np.nan
    px_w     = px if len(px) < window + 1 else px.iloc[-(window + 1):]
    log_rets = np.diff(np.log(px_w.values))
    excess   = log_rets - rfr_daily
    sd       = excess.std(ddof=1)
    if sd < 1e-12:
        return np.nan
    return (excess.mean() / sd) * np.sqrt(trading_days)


def _cross_section_z(series: pd.Series) -> pd.Series:
    """Z-score a series cross-sectionally (mean=0, std=1)."""
    mu, sd = series.mean(), series.std(ddof=1)
    return (series - mu) / sd if sd > 0 else series * 0.0


def compute_sharpe(prices_df: pd.DataFrame,
                   stock_tickers: list,
                   windows: dict,
                   rfr_daily: float,
                   trading_days: int = 252):
    """
    Compute Sharpe ratios and cross-sectional Z-scores for all windows.

    Parameters
    ----------
    windows : dict  e.g. {"12M":252, "9M":189, "6M":126, "3M":63, "1M":21}
              The COMPOSITE (SHARPE_ALL) uses all windows except "1M" if present.
              Short-term for MOM_ACCEL = mean(Z_1M, Z_3M, Z_6M) if 1M present,
              else mean(Z_3M, Z_6M).
              Long-term = mean(Z_9M, Z_12M).

    Returns
    -------
    sharpe_df : DataFrame  raw Sharpe per window, cols = window labels
    z_df      : DataFrame  Z_<label> per window + COMPOSITE / SHARPE_ALL /
                           SHARPE_ST / SHARPE_LT / SHARPE_3 / MOM_ACCEL
    """
    print("Computing Sharpe ratios ...")
    sharpe_data = {}
    for label, window in windows.items():
        col = [_sharpe_ratio(prices_df.loc[t], window, rfr_daily, trading_days)
               for t in stock_tickers]
        valid = sum(1 for v in col if not np.isnan(v))
        sharpe_data[label] = col
        print(f"  {label} ({window}d): {valid}/{len(stock_tickers)} valid")

    sharpe_df = pd.DataFrame(sharpe_data, index=stock_tickers)

    print("\nZ-scoring Sharpe cross-sectionally ...")
    z_df = pd.DataFrame(index=stock_tickers)
    for label in windows:
        z_df[f"Z_{label}"] = _cross_section_z(sharpe_df[label])

    # COMPOSITE / SHARPE_ALL: 4 core windows (exclude 1M if present)
    core_labels = [l for l in windows if l != "1M"]
    z_cols             = [f"Z_{l}" for l in core_labels]
    z_df["COMPOSITE"]  = z_df[z_cols].mean(axis=1)

    # Short-term: 1M+3M+6M if 1M available, else 3M+6M
    st_cols = (["Z_1M", "Z_3M", "Z_6M"] if "1M" in windows
               else ["Z_3M", "Z_6M"])
    lt_cols = ["Z_9M", "Z_12M"]

    z_df["SHARPE_ST"] = z_df[st_cols].mean(axis=1)
    z_df["SHARPE_LT"] = z_df[lt_cols].mean(axis=1)
    z_df["SHARPE_3"]  = z_df[["Z_12M", "Z_6M", "Z_3M"]].mean(axis=1)

    accel_raw         = z_df["SHARPE_ST"] - z_df["SHARPE_LT"]
    z_df["MOM_ACCEL"] = _cross_section_z(accel_raw)

    accel_pos = (z_df["MOM_ACCEL"] > 0).sum()
    accel_neg = (z_df["MOM_ACCEL"] < 0).sum()
    print(f"  MOM_ACCEL  — accelerating (>0): {accel_pos}  |  "
          f"decelerating (<0): {accel_neg}")

    return sharpe_df, z_df


# ── CLENOW ────────────────────────────────────────────────────────────────────

def _clenow_window(series: pd.Series, window: int,
                   trading_days: int) -> tuple:
    """Fit log(price) = a + b*t for last `window` days. Returns (slope, r2, slope*r2)."""
    px = series.dropna()
    if len(px) < window * 0.90:
        return np.nan, np.nan, np.nan
    n         = min(len(px), window)
    px_w      = px.iloc[-n:].values
    x         = np.arange(n)
    y         = np.log(px_w)
    slope, _, r, _, _ = stats.linregress(x, y)
    r2        = r ** 2
    ann_slope = slope * trading_days
    return ann_slope, r2, ann_slope * r2


def compute_clenow(prices_df: pd.DataFrame,
                   stock_tickers: list,
                   windows: dict,
                   trading_days: int = 252):
    """
    Compute multi-window Clenow scores and cross-sectional Z-scores.

    Returns
    -------
    slope_df : DataFrame  CL_<label>  annualised slope per window
    r2_df    : DataFrame  CR_<label>  R² per window
    raw_df   : DataFrame  CS_<label>  raw Clenow (slope × R²) per window
    cz_df    : DataFrame  CZ_<label>  Z-scored Clenow + CLENOW_Z composite
    """
    print("\nComputing multi-window Clenow scores ...")
    slope_data, r2_data, raw_data = {}, {}, {}

    for label, window in windows.items():
        slopes, r2s, raws = [], [], []
        for t in stock_tickers:
            sl, r2, raw = _clenow_window(prices_df.loc[t], window, trading_days)
            slopes.append(sl); r2s.append(r2); raws.append(raw)
        slope_data[f"CL_{label}"] = slopes
        r2_data[f"CR_{label}"]    = r2s
        raw_data[f"CS_{label}"]   = raws
        valid = sum(1 for v in raws if not np.isnan(v))
        print(f"  {label} ({window}d): {valid}/{len(stock_tickers)} valid")

    slope_df = pd.DataFrame(slope_data, index=stock_tickers)
    r2_df    = pd.DataFrame(r2_data,    index=stock_tickers)
    raw_df   = pd.DataFrame(raw_data,   index=stock_tickers)

    cz_df = pd.DataFrame(index=stock_tickers)
    for label in windows:
        cz_df[f"CZ_{label}"] = _cross_section_z(raw_df[f"CS_{label}"])

    cz_cols           = [f"CZ_{l}" for l in windows]
    cz_df["CLENOW_Z"] = cz_df[cz_cols].mean(axis=1)

    return slope_df, r2_df, raw_df, cz_df


# ── RESIDUAL MOMENTUM ─────────────────────────────────────────────────────────

def _residual_sharpe(stock_series: pd.Series, mkt_rets: np.ndarray,
                     window: int, trading_days: int) -> float:
    """OLS-regress stock returns on market returns; return Sharpe of residuals."""
    px = stock_series.dropna()
    if len(px) < window * 0.90:
        return np.nan
    n      = min(len(px) - 1, window)
    s_rets = np.diff(np.log(px.iloc[-n-1:].values))
    m_rets = mkt_rets[-n:]
    if len(s_rets) != len(m_rets) or len(s_rets) < 10:
        return np.nan
    X = np.column_stack([np.ones(len(m_rets)), m_rets])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(X, s_rets, rcond=None)
    except np.linalg.LinAlgError:
        return np.nan
    residuals = s_rets - X @ coeffs
    sd = residuals.std(ddof=1)
    if sd < 1e-12:
        return np.nan
    return (residuals.mean() / sd) * np.sqrt(trading_days)


def compute_residual_momentum(prices_df: pd.DataFrame,
                               stock_tickers: list,
                               nifty_series: pd.Series,
                               windows: dict,
                               trading_days: int = 252):
    """
    Compute residual Sharpe after regressing each stock against NIFTY500.

    Returns
    -------
    resmom_df : DataFrame  RS_<label>  residual Sharpe per window
    rs_z_df   : DataFrame  RZ_<label>  Z-scored + RES_MOM composite
    """
    print("\nComputing residual momentum scores ...")
    nifty_log_rets = np.diff(np.log(nifty_series.dropna().values))

    resmom_data = {}
    for label, window in windows.items():
        col = [_residual_sharpe(prices_df.loc[t], nifty_log_rets,
                                window, trading_days)
               for t in stock_tickers]
        valid = sum(1 for v in col if not np.isnan(v))
        resmom_data[f"RS_{label}"] = col
        print(f"  {label} ({window}d): {valid}/{len(stock_tickers)} valid")

    resmom_df = pd.DataFrame(resmom_data, index=stock_tickers)

    rs_z_df = pd.DataFrame(index=stock_tickers)
    for label in windows:
        rs_z_df[f"RZ_{label}"] = _cross_section_z(resmom_df[f"RS_{label}"])

    rz_cols            = [f"RZ_{l}" for l in windows]
    rs_z_df["RES_MOM"] = rs_z_df[rz_cols].mean(axis=1)

    return resmom_df, rs_z_df


# ── RETURN CONTEXT ────────────────────────────────────────────────────────────

def compute_returns(prices_df: pd.DataFrame,
                    stock_tickers: list) -> pd.DataFrame:
    """
    Compute 1M (22d), 3M (63d), 12M (245d) price returns for each stock.

    Returns DataFrame with columns 1M%, 3M%, 12M%.
    """
    def safe_ret(series, n):
        px = series.dropna()
        return (px.iloc[-1] / px.iloc[-n] - 1) * 100 if len(px) > n else np.nan

    ret_data = {t: {
        "1M%":  safe_ret(prices_df.loc[t], 22),
        "3M%":  safe_ret(prices_df.loc[t], 63),
        "12M%": safe_ret(prices_df.loc[t], 245),
    } for t in stock_tickers}

    return pd.DataFrame(ret_data).T


# ── 52-WEEK HIGH ──────────────────────────────────────────────────────────────

def compute_pct_from_52h(prices_df: pd.DataFrame,
                          stock_tickers: list,
                          window: int = 252) -> pd.Series:
    """
    Compute % distance from 52-week high for each stock.
    Negative = price is below its 52W high.
    """
    def _pct(series):
        px = series.dropna()
        if len(px) < 2:
            return np.nan
        px_w     = px.iloc[-window:] if len(px) >= window else px
        high_52w = px_w.max()
        last_px  = px.iloc[-1]
        if high_52w <= 0:
            return np.nan
        return (last_px / high_52w - 1) * 100

    return pd.Series(
        {t: _pct(prices_df.loc[t]) for t in stock_tickers},
        name="PCT_FROM_52H"
    )


# ── MARKET REGIME ─────────────────────────────────────────────────────────────

def compute_market_regime(nifty_series: pd.Series) -> str:
    """
    EMA-based regime check on NIFTY500.
    BUY if: last_price > EMA(50) AND EMA(21) > EMA(63).

    Returns 'BUY', 'NOT BUY (Risk Off)', or 'UNKNOWN'.
    """
    px = nifty_series.dropna()
    if len(px) < 63:
        return "UNKNOWN"
    n50    = px.ewm(span=50, adjust=False).mean().iloc[-1]
    n21    = px.ewm(span=21, adjust=False).mean().iloc[-1]
    n63    = px.ewm(span=63, adjust=False).mean().iloc[-1]
    last_n = px.iloc[-1]
    return "BUY" if (last_n > n50 and n21 > n63) else "NOT BUY (Risk Off)"


# ── NORMALISATION ─────────────────────────────────────────────────────────────

def normalise_composite(v: float) -> float:
    """
    Non-linear rescale so all composite values are positive and spread out:
      v > 1  →  v + 1
      v < 0  →  1 / (1 - v)   maps to (0, 1]
      0 ≤ v ≤ 1  →  unchanged
    """
    if pd.isna(v):
        return np.nan
    if v > 1:
        return v + 1.0
    if v < 0:
        return 1.0 / (1.0 - v)
    return v
