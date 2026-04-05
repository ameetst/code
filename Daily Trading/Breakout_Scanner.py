"""
N500 Breakout Scanner v3 — Price + INR Volume Confirmation
============================================================
Combines the 6-signal price scanner with 4 INR volume signals
for a maximum Confirmation Score of 10/10.

INPUT FILES:
  n500.xlsx        — price data   (tickers as rows, dates as columns)
  n500_volume.xlsx — volume data  (same layout, values = number of shares traded)

  Volume in INR is computed by the script as:
      Volume_INR (per day) = Shares_Traded × Close_Price (same ticker, same date)

OUTPUT:
  breakout_results_v3.csv — ranked breakout signals with full breakdown

CONFIRMATION SIGNALS (10 total):
  ── Price signals (6) ──────────────────────────────────────
  1. MACD crossover          — momentum direction
  2. ADX > 25                — trend strength, not a choppy range
  3. Bollinger Band squeeze  — tight consolidation before breakout
  4. EMA stack (9>21>50)     — all MAs aligned with the signal
  5. Consecutive closes      — breakout holding for 2+ days
  6. RSI sweet spot          — 55–75 BUY / 25–45 SELL
  ── Volume signals (4) ─────────────────────────────────────
  7. Volume spike on breakout day   — ≥1.5× 20-day avg (institutional)
  8. Volume dry-up before breakout  — low vol consolidation → high vol break
  9. Rising volume into breakout    — 5-day uptrend in volume before signal
 10. OBV confirmation               — On-Balance Volume making new highs/lows

Usage:
  pip install pandas openpyxl numpy
  python n500_breakout_scanner_v3.py
"""

import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
PRICE_FILE          = "n500.xlsx"
VOLUME_FILE         = "n500_volume.xlsx"        # same layout, INR values
OUTPUT_FILE         = "breakout_results_v3.csv"

LOOKBACK            = 60      # days for consolidation range (3 months)
ATR_PERIOD          = 14      # ATR smoothing period
RR_RATIO            = 3.0     # reward : risk for target price
MIN_CONFIRM_SCORE   = 6       # min signals to include in output (out of 10)
PCT_FROM_52WK_HIGH  = 15.0    # max % below 52-week high for clean BUY

# Price indicator settings
EMA_SHORT, EMA_MED, EMA_LONG = 9, 21, 50
BB_PERIOD, BB_STD             = 20, 2.0
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
ADX_PERIOD                    = 14

# Volume signal settings
VOL_SPIKE_MULTIPLIER   = 1.5   # breakout day vol must be ≥ this × 20-day avg
VOL_DRY_UP_THRESHOLD   = 0.7   # pre-breakout avg vol < 70% of 20-day avg
VOL_DRY_UP_LOOKBACK    = 5     # days to measure dry-up window
VOL_RISING_DAYS        = 5     # days to check for rising volume trend
OBV_LOOKBACK           = 20    # days to check OBV trend


# ─────────────────────────────────────────────────────────────
# STEP 1 — Load data
# ─────────────────────────────────────────────────────────────
def load_wide_excel(filepath: str, value_col_name: str) -> pd.DataFrame:
    """
    Load a wide-format Excel file (tickers as rows, dates as columns).
    Returns a long DataFrame: [Ticker, Date, value_col_name]
    """
    df_raw = pd.read_excel(filepath, sheet_name="DATA", header=0)
    date_cols = [c for c in df_raw.columns if hasattr(c, "year")]

    rows = []
    for _, row in df_raw.iterrows():
        ticker = str(row["TICKER"])
        for col in date_cols:
            val = row[col]
            if pd.notna(val) and val != 0:
                rows.append({"Ticker": ticker, "Date": col,
                             value_col_name: float(val)})
    return pd.DataFrame(rows)


def load_all_data(price_file: str, volume_file: str) -> dict:
    """
    Loads price and share-volume files, merges them by Ticker + Date,
    then computes INR volume as:  Volume_INR = Shares_Traded × Close_Price.

    Returns {ticker: {"prices": Series, "volume": Series (INR),
                       "close_now": float, "high_52wk": float}}
    """
    print(f"  Loading price data        : {price_file}")
    price_long = load_wide_excel(price_file, "Close")

    print(f"  Loading share volume data : {volume_file}")
    vol_long   = load_wide_excel(volume_file, "Shares_Traded")

    # Load metadata (CLOSE and 52WK HIGH) from price file
    meta_df = pd.read_excel(price_file, sheet_name="DATA", header=0)
    meta    = meta_df.set_index("TICKER")[["CLOSE", "52WK HIGH"]].to_dict("index")

    # Merge price + shares on Ticker + Date
    merged = pd.merge(price_long, vol_long, on=["Ticker", "Date"], how="inner")
    merged = merged.sort_values(["Ticker", "Date"])

    # Compute INR volume: shares × price on the same day
    # This normalises volume across all stocks regardless of share price
    # (e.g. 1000 shares of MRF @ ₹1,29,575 vs 1000 shares of NSLNISP @ ₹34)
    merged["Volume_INR"] = merged["Shares_Traded"] * merged["Close"]

    ticker_data = {}
    for ticker, grp in merged.groupby("Ticker"):
        grp    = grp.sort_values("Date").set_index("Date")
        prices = grp["Close"]
        volume = grp["Volume_INR"]   # already in INR from here on

        min_len = EMA_LONG + LOOKBACK + OBV_LOOKBACK + 5
        if len(prices) >= min_len and ticker in meta:
            ticker_data[ticker] = {
                "prices":    prices,
                "volume":    volume,
                "close_now": float(meta[ticker]["CLOSE"]),
                "high_52wk": float(meta[ticker]["52WK HIGH"]),
            }
    return ticker_data


# ─────────────────────────────────────────────────────────────
# STEP 2 — Price indicators
# ─────────────────────────────────────────────────────────────
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def compute_atr_simple(prices: pd.Series, period: int) -> float:
    return prices.diff().abs().dropna().iloc[-period:].mean()

def compute_rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff().dropna()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.iloc[-1]
    return round(float(val), 1) if pd.notna(val) else 50.0

def compute_macd_side(prices: pd.Series) -> str:
    macd_line   = ema(prices, MACD_FAST) - ema(prices, MACD_SLOW)
    signal_line = ema(macd_line, MACD_SIG)
    diff        = macd_line - signal_line
    for i in [-1, -2, -3]:
        if diff.iloc[i] > 0 and diff.iloc[i - 1] <= 0:
            return "bullish"
        if diff.iloc[i] < 0 and diff.iloc[i - 1] >= 0:
            return "bearish"
    return "bullish_side" if diff.iloc[-1] > 0 else "bearish_side"

def compute_adx(prices: pd.Series, period: int = 14) -> float:
    diff         = prices.diff().dropna()
    dm_plus      = diff.clip(lower=0).rolling(period).mean()
    dm_minus     = (-diff).clip(lower=0).rolling(period).mean()
    smooth_range = prices.diff().abs().dropna().rolling(period).mean()
    di_plus  = 100 * dm_plus  / smooth_range.replace(0, np.nan)
    di_minus = 100 * dm_minus / smooth_range.replace(0, np.nan)
    dx       = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx      = dx.rolling(period).mean()
    val      = adx.iloc[-1]
    return round(float(val), 1) if pd.notna(val) else 0.0

def compute_bollinger(prices: pd.Series) -> dict:
    sma       = prices.rolling(BB_PERIOD).mean()
    std       = prices.rolling(BB_PERIOD).std()
    upper     = sma + BB_STD * std
    lower     = sma - BB_STD * std
    bandwidth = (upper - lower) / sma * 100
    bw_now    = bandwidth.iloc[-1]
    bw_avg    = bandwidth.iloc[-90:].mean() if len(bandwidth) >= 90 else bandwidth.mean()
    latest    = prices.iloc[-1]
    
    pre_squeeze = False
    if len(bandwidth) >= 6:
        for bw_val in bandwidth.iloc[-6:-1]:
            if bw_val < (0.5 * bw_avg):
                pre_squeeze = True

    return {
        "upper":       float(upper.iloc[-1]),
        "lower":       float(lower.iloc[-1]),
        "bandwidth":   round(float(bw_now), 2),
        "squeeze":     bw_now < (0.5 * bw_avg),
        "pre_squeeze": pre_squeeze,
        "above_upper": latest > float(upper.iloc[-1]),
        "below_lower": latest < float(lower.iloc[-1]),
    }

def compute_ema_stack(prices: pd.Series) -> dict:
    e9  = float(ema(prices, EMA_SHORT).iloc[-1])
    e21 = float(ema(prices, EMA_MED).iloc[-1])
    e50 = float(ema(prices, EMA_LONG).iloc[-1])
    p   = float(prices.iloc[-1])
    return {
        "ema9": round(e9, 2), "ema21": round(e21, 2), "ema50": round(e50, 2),
        "bullish_stack": p > e9 > e21 > e50,
        "bearish_stack": p < e9 < e21 < e50,
    }

def count_consecutive_closes(prices: pd.Series, level: float, above: bool) -> int:
    count = 0
    for price in reversed(prices.values):
        if (above and price > level) or (not above and price < level):
            count += 1
        else:
            break
    return count


# ─────────────────────────────────────────────────────────────
# STEP 3 — Volume indicators
# ─────────────────────────────────────────────────────────────
def fmt_inr(value: float) -> str:
    """Format INR value into readable crore/lakh string."""
    if value >= 1e7:
        return f"₹{value/1e7:.1f}Cr"
    elif value >= 1e5:
        return f"₹{value/1e5:.1f}L"
    return f"₹{value:,.0f}"


def check_volume_spike(volume: pd.Series, lookback: int = 20) -> dict:
    """
    Signal 7: Breakout day volume ≥ VOL_SPIKE_MULTIPLIER × 20-day average.
    The most important volume signal — confirms institutional participation.
    """
    avg_vol     = volume.iloc[-(lookback + 1):-1].mean()   # exclude today
    today_vol   = volume.iloc[-1]
    ratio       = today_vol / avg_vol if avg_vol > 0 else 0
    passed      = ratio >= VOL_SPIKE_MULTIPLIER
    return {
        "passed":    passed,
        "ratio":     round(ratio, 2),
        "today_vol": today_vol,
        "avg_vol":   avg_vol,
        "label": (f"✓ {ratio:.1f}× avg ({fmt_inr(today_vol)} vs avg {fmt_inr(avg_vol)})"
                  if passed else
                  f"✗ Only {ratio:.1f}× avg ({fmt_inr(today_vol)} vs avg {fmt_inr(avg_vol)})")
    }


def check_volume_dry_up(volume: pd.Series, avg_period: int = 20) -> dict:
    """
    Signal 8: Volume dried up in the days BEFORE the breakout.
    Low-volume consolidation → high-volume breakout is the ideal setup.
    Pre-breakout window = VOL_DRY_UP_LOOKBACK days before today.
    """
    lookback_avg = volume.iloc[-(avg_period + 1):-1].mean()
    pre_break    = volume.iloc[-(VOL_DRY_UP_LOOKBACK + 1):-1]  # last N days before today
    pre_avg      = pre_break.mean()
    ratio        = pre_avg / lookback_avg if lookback_avg > 0 else 1
    passed       = ratio < VOL_DRY_UP_THRESHOLD
    return {
        "passed": passed,
        "ratio":  round(ratio, 2),
        "label": (f"✓ Pre-breakout vol was {ratio:.0%} of 20d avg (tight consolidation)"
                  if passed else
                  f"✗ Pre-breakout vol was {ratio:.0%} of 20d avg (no dry-up)")
    }


def check_rising_volume(volume: pd.Series, days: int = 5) -> dict:
    """
    Signal 9: Volume trending upward in the VOL_RISING_DAYS before the breakout.
    Checks if a simple linear regression slope over recent volume is positive.
    Rising volume before a price break = accumulation in progress.
    """
    recent = volume.iloc[-(days + 1):-1].values   # exclude today
    if len(recent) < 3:
        return {"passed": False, "label": "✗ Insufficient data"}
    x      = np.arange(len(recent))
    slope  = np.polyfit(x, recent, 1)[0]
    passed = slope > 0
    trend  = "rising" if passed else "falling/flat"
    return {
        "passed": passed,
        "slope":  round(slope, 0),
        "label": (f"✓ Volume trending {trend} into breakout ({days}d slope: +{fmt_inr(abs(slope))}/day)"
                  if passed else
                  f"✗ Volume was {trend} before breakout ({days}d slope: -{fmt_inr(abs(slope))}/day)")
    }


def check_obv(prices: pd.Series, volume: pd.Series, is_buy: bool,
              lookback: int = 20) -> dict:
    """
    Signal 10: On-Balance Volume (OBV) confirming the price breakout.
    OBV = cumulative sum of (+volume on up-days, -volume on down-days).
    For BUY : OBV should be at or near its N-day high (money flowing in).
    For SELL: OBV should be at or near its N-day low  (money flowing out).
    Divergence (price breaks but OBV doesn't follow) = red flag.
    """
    price_diff = prices.diff().fillna(0)
    direction  = np.sign(price_diff)
    obv        = (volume * direction).cumsum()

    obv_now    = float(obv.iloc[-1])
    obv_window = obv.iloc[-lookback:]
    obv_high   = float(obv_window.max())
    obv_low    = float(obv_window.min())
    obv_range  = obv_high - obv_low if obv_high != obv_low else 1

    # Position within range (0 = at low, 1 = at high)
    obv_pct    = (obv_now - obv_low) / obv_range

    if is_buy:
        passed = obv_pct >= 0.75   # OBV in top 25% of its range
        label  = (f"✓ OBV at {obv_pct:.0%} of {lookback}d range (money flowing IN)"
                  if passed else
                  f"✗ OBV at {obv_pct:.0%} of {lookback}d range (divergence — caution)")
    else:
        passed = obv_pct <= 0.25   # OBV in bottom 25% of its range
        label  = (f"✓ OBV at {obv_pct:.0%} of {lookback}d range (money flowing OUT)"
                  if passed else
                  f"✗ OBV at {obv_pct:.0%} of {lookback}d range (divergence — caution)")

    return {"passed": passed, "obv_pct": round(obv_pct, 2), "label": label}


# ─────────────────────────────────────────────────────────────
# STEP 4 — Full confirmation scorer (price + volume)
# ─────────────────────────────────────────────────────────────
def score_all(prices: pd.Series, volume: pd.Series,
              signal: str, range_high: float, range_low: float) -> dict:
    is_buy = "BUY" in signal
    score  = 0
    bd     = {}

    # ── Price signals (1–6) ────────────────────────────────
    macd_state = compute_macd_side(prices)
    if (is_buy and "bullish" in macd_state) or (not is_buy and "bearish" in macd_state):
        score += 1; bd["1_MACD"] = f"✓ {'Bullish' if is_buy else 'Bearish'}"
    else:
        bd["1_MACD"] = "✗ Against signal"

    adx = compute_adx(prices, ADX_PERIOD)
    if adx >= 25:
        score += 1; bd["2_ADX"] = f"✓ {adx} (strong trend)"
    else:
        bd["2_ADX"] = f"✗ {adx} (weak / choppy)"

    bb = compute_bollinger(prices)
    if is_buy and (bb["squeeze"] or bb["above_upper"]):
        score += 1; bd["3_BollingerBand"] = "✓ Squeeze / price above upper band"
    elif not is_buy and (bb["squeeze"] or bb["below_lower"]):
        score += 1; bd["3_BollingerBand"] = "✓ Squeeze / price below lower band"
    else:
        bd["3_BollingerBand"] = "✗ No squeeze or band expansion"

    ema_info = compute_ema_stack(prices)
    if (is_buy and ema_info["bullish_stack"]) or (not is_buy and ema_info["bearish_stack"]):
        score += 1; bd["4_EMA_Stack"] = f"✓ {'Bullish' if is_buy else 'Bearish'} (price{'>' if is_buy else '<'}9{'>' if is_buy else '<'}21{'>' if is_buy else '<'}50)"
    else:
        bd["4_EMA_Stack"] = "✗ EMAs not aligned"

    # Signal 5: Impulsive Breakout Candle
    latest_diff = float(prices.iloc[-1] - prices.iloc[-2])
    atr_val = compute_atr_simple(prices, ATR_PERIOD)
    if is_buy and latest_diff >= (1.5 * atr_val):
        score += 1; bd["5_Impulsive"] = f"✓ Impulsive candle (size {latest_diff:.2f} >= 1.5 ATR)"
    elif not is_buy and (-latest_diff) >= (1.5 * atr_val):
        score += 1; bd["5_Impulsive"] = f"✓ Impulsive candle (size {-latest_diff:.2f} >= 1.5 ATR)"
    else:
        bd["5_Impulsive"] = f"✗ Weak candle (size {abs(latest_diff):.2f} < 1.5 ATR)"

    rsi = compute_rsi(prices)
    if (is_buy and 55 <= rsi <= 75) or (not is_buy and 25 <= rsi <= 45):
        score += 1; bd["6_RSI"] = f"✓ {rsi} ({'bullish' if is_buy else 'bearish'} momentum zone)"
    else:
        bd["6_RSI"] = f"✗ {rsi} (outside sweet spot)"

    # ── Volume signals (7–10) ─────────────────────────────
    vol_spike   = check_volume_spike(volume, 20)
    vol_dry_up  = check_volume_dry_up(volume, 20)
    vol_rising  = check_rising_volume(volume, VOL_RISING_DAYS)
    obv_check   = check_obv(prices, volume, is_buy, OBV_LOOKBACK)

    if vol_spike["passed"]:
        score += 1
    bd["7_Vol_Spike"] = vol_spike["label"]

    if vol_dry_up["passed"]:
        score += 1
    bd["8_Vol_DryUp"] = vol_dry_up["label"]

    if vol_rising["passed"]:
        score += 1
    bd["9_Vol_Rising"] = vol_rising["label"]

    if obv_check["passed"]:
        score += 1
    bd["10_OBV"] = obv_check["label"]

    return {
        "score":      score,
        "breakdown":  bd,
        "adx":        adx,
        "rsi":        rsi,
        "ema_info":   ema_info,
        "bb":         bb,
        "vol_spike":  vol_spike,
        "vol_dry_up": vol_dry_up,
        "vol_rising": vol_rising,
        "obv":        obv_check,
    }


# ─────────────────────────────────────────────────────────────
# STEP 5a — Build plain-English exit plan
# ─────────────────────────────────────────────────────────────
def build_exit_plan(entry: float, stop_loss: float, target: float,
                    atr: float, score: int, signal_date: str) -> str:
    """
    Generates a plain-English exit plan for a BUY breakout.

    Three exit rules applied in priority order:
      1. Hard stop loss  — exit immediately if price closes below stop
      2. Trailing stop   — ratchet stop up as price advances
      3. Time stop       — exit if no meaningful move within 5 trading days

    Recommended hold duration scales with confirmation score:
      8–10 → 15–30 days | 6–7 → 8–15 days | 4–5 → 3–8 days
    """
    breakeven_trigger = round(entry + 1.5 * atr, 2)         # move stop to entry after +1.5 ATR
    trail_start       = round(entry + 2.5 * atr, 2)     # start trailing after +2.5 ATR
    trail_stop_then   = round(trail_start - 1.5 * atr, 2)   # trail stop = current − 1.5 ATR

    if score >= 8:
        hold_days = "15–30 trading days"
        conviction = "HIGH CONVICTION"
    elif score >= 6:
        hold_days = "8–15 trading days"
        conviction = "GOOD SETUP"
    else:
        hold_days = "3–8 trading days"
        conviction = "MODERATE — exit fast if stalls"

    # Parse signal date to compute time-stop date (~5 trading days ≈ 7 calendar days)
    try:
        from datetime import datetime, timedelta
        sig_dt       = datetime.strptime(signal_date, "%Y-%m-%d")
        time_stop_dt = sig_dt + timedelta(days=7)
        time_stop    = time_stop_dt.strftime("%d %b %Y")
    except Exception:
        time_stop = "5 trading days from entry"

    # Super-Trend trail: max(10% of high, 2×ATR) so it never gets tighter
    # than volatility on low-ATR stocks or too loose on high-ATR stocks
    super_trend_trigger  = round(entry + 3.0 * atr, 2)
    atr_pct_of_price     = atr / entry                       # ATR as % of entry price
    super_trail_pct      = max(0.10, 2.0 * atr_pct_of_price) # never tighter than 2×ATR
    super_trail_label    = f"{super_trail_pct:.1%}"          # e.g. "10.0%" or "12.4%"

    plan = (
        f"[{conviction} | Hold up to {hold_days}]  "
        f"① Hard stop: EXIT if close < ₹{stop_loss} (loss capped at ₹{round(entry - stop_loss, 2)}/share).  "
        f"② Breakeven: Move stop to ₹{entry} once price hits ₹{breakeven_trigger} (+1.5×ATR — free trade).  "
        f"③ Trail: Once price hits ₹{trail_start} (+2.5×ATR), trail stop to 1.5×ATR below current price "
        f"(initially ₹{trail_stop_then}); keep trailing on every new high.  "
        f"④ Target: Book 50–100% profit at ₹{target} (+3×ATR, 1:{RR_RATIO} R:R). "
        f"If stock keeps running past target, skip full exit and switch to Super-Trend rule.  "
        f"⑤ Super-Trend: Once price clears ₹{super_trend_trigger} (+3×ATR), trail stop at "
        f"{super_trail_label} below the running high (= max of 10% or 2×ATR — "
        f"adapts to this stock's volatility). Let it run until stopped out.  "
        f"⑥ Time stop: If price hasn't moved >₹{round(atr * 0.5, 2)} by {time_stop}, EXIT — capital wasted."
    )
    return plan


# ─────────────────────────────────────────────────────────────
# STEP 5b — Detect breakout + score
# ─────────────────────────────────────────────────────────────
def detect_breakout(ticker: str, info: dict) -> dict | None:
    prices    = info["prices"]
    volume    = info["volume"]
    high_52wk = info["high_52wk"]

    if len(prices) < EMA_LONG + LOOKBACK + 5:
        return None

    window     = prices.iloc[-(LOOKBACK + 1):-1]
    range_high = window.max()
    range_low  = window.min()
    latest     = prices.iloc[-1]
    prev       = prices.iloc[-2]
    atr        = compute_atr_simple(prices, ATR_PERIOD)
    momentum5d = ((latest - prices.iloc[-6]) / prices.iloc[-6] * 100
                  if len(prices) >= 6 else 0.0)

    magnitude = ""
    if latest > range_high and prev <= range_high:
        window_6m = prices.iloc[-(120 + 1):-1] if len(prices) > 120 else window
        range_high_6m = window_6m.max()
        window_1y = prices.iloc[-(250 + 1):-1] if len(prices) > 250 else window
        range_high_1y = window_1y.max()

        if latest > range_high_1y:
            magnitude = "1-Year Breakout"
        elif latest > range_high_6m:
            magnitude = "6-Month Breakout"
        else:
            magnitude = "3-Month Breakout"

        pct_from_52wk = (high_52wk - latest) / high_52wk * 100
        base_signal   = "BUY" if pct_from_52wk <= PCT_FROM_52WK_HIGH else "BUY (below 52wk zone)"
        stop_loss     = round(latest - 1.5 * atr, 2)
        target        = round(latest + RR_RATIO * atr, 2)

    elif latest < range_low and prev >= range_low:
        return None   # SELL signals excluded — BUY only mode
    else:
        return None

    conf = score_all(prices, volume, base_signal, range_high, range_low)
    
    # Apply Hard Rules before proceeding
    ema = conf["ema_info"]
    vs  = conf["vol_spike"]
    vr  = conf["vol_rising"]
    bb  = conf["bb"]
    
    ema_trend = ema["ema50"] < ema["ema21"] and ema["ema50"] < float(latest)
    vol_confirm = vs["passed"] or vr["passed"]
    vcp_confirm = bb["pre_squeeze"]
    
    if not (ema_trend and vol_confirm and vcp_confirm):
        return None

    if conf["score"] < MIN_CONFIRM_SCORE:
        return None

    bd  = conf["breakdown"]
    sig_date   = prices.index[-1].strftime("%Y-%m-%d")
    exit_plan  = build_exit_plan(float(latest), stop_loss, target,
                                 float(atr), conf["score"], sig_date)

    return {
        # ── Core fields ─────────────────────────────────
        "Ticker":              ticker,
        "Signal":              base_signal,
        "Magnitude":           magnitude,
        "Confirm_Score":       f"{conf['score']}/10",
        "Signal_Date":         sig_date,
        # ── Price levels ────────────────────────────────
        "Latest_Close":        round(float(latest), 2),
        "Stop_Loss":           stop_loss,
        "Target":              target,
        "Risk_Reward":         f"1:{RR_RATIO}",
        "Exit_Plan":           exit_plan,
        "52Wk_High":           round(high_52wk, 2),
        "Range_High_60d":      round(float(range_high), 2),
        "Range_Low_60d":       round(float(range_low), 2),
        # ── Price indicators ────────────────────────────
        "ATR":                 round(float(atr), 2),
        "RSI_14":              conf["rsi"],
        "ADX":                 conf["adx"],
        "EMA9":                ema["ema9"],
        "EMA21":               ema["ema21"],
        "EMA50":               ema["ema50"],
        "BB_Bandwidth":        conf["bb"]["bandwidth"],
        "BB_Squeeze":          "Yes" if conf["bb"]["squeeze"] else "No",
        "Momentum_5d_%":       round(momentum5d, 2),
        # ── Volume indicators ───────────────────────────
        "Vol_Today_INR":       fmt_inr(vs["today_vol"]),
        "Vol_20d_Avg_INR":     fmt_inr(vs["avg_vol"]),
        "Vol_Spike_Ratio":     vs["ratio"],
        "OBV_Position_%":      f"{conf['obv']['obv_pct']:.0%}",
        # ── Signal breakdown (all 10) ───────────────────
        "✓_1_MACD":            bd["1_MACD"],
        "✓_2_ADX":             bd["2_ADX"],
        "✓_3_BollingerBand":   bd["3_BollingerBand"],
        "✓_4_EMA_Stack":       bd["4_EMA_Stack"],
        "✓_5_Impulsive":       bd["5_Impulsive"],
        "✓_6_RSI":             bd["6_RSI"],
        "✓_7_Vol_Spike":       bd["7_Vol_Spike"],
        "✓_8_Vol_DryUp":       bd["8_Vol_DryUp"],
        "✓_9_Vol_Rising":      bd["9_Vol_Rising"],
        "✓_10_OBV":            bd["10_OBV"],
    }


# ─────────────────────────────────────────────────────────────
# STEP 6 — Run scanner
# ─────────────────────────────────────────────────────────────
def run_scanner(price_file: str, volume_file: str) -> pd.DataFrame:
    print(f"\n{'─'*60}")
    print(f"  N500 Breakout Scanner v3 — Price + Volume (INR computed from shares × price)")
    print(f"{'─'*60}")
    ticker_data = load_all_data(price_file, volume_file)
    print(f"  Tickers with full price+volume history: {len(ticker_data)}")
    print(f"  Min confirmation score required: {MIN_CONFIRM_SCORE}/10\n")

    results = []
    for ticker, info in ticker_data.items():
        r = detect_breakout(ticker, info)
        if r:
            results.append(r)

    if not results:
        print("No confirmed signals found. Try lowering MIN_CONFIRM_SCORE.")
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["_score_int"] = df["Confirm_Score"].str.split("/").str[0].astype(int)
    df["_is_buy"]    = df["Signal"].str.contains("BUY").astype(int)
    df = (df.sort_values(["_is_buy", "_score_int"], ascending=[False, False])
            .drop(columns=["_score_int", "_is_buy"])
            .reset_index(drop=True))
    return df


# ─────────────────────────────────────────────────────────────
# STEP 7 — Main
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    results = run_scanner(PRICE_FILE, VOLUME_FILE)

    if not results.empty:
        # All results are BUY only — SELL filtered at detection level
        buy_df = results[results["Signal"].str.contains("BUY")]

        print(f"{'='*65}")
        print(f"  SCAN COMPLETE — BUY BREAKOUT SIGNALS ONLY")
        print(f"{'='*65}")
        print(f"  Confirmed BUY signals : {len(buy_df)}")
        print(f"{'='*65}\n")

        display_cols = ["Ticker", "Signal", "Confirm_Score", "Latest_Close",
                        "Stop_Loss", "Target", "RSI_14", "ADX",
                        "Vol_Spike_Ratio", "OBV_Position_%"]

        if not buy_df.empty:
            print("── BUY BREAKOUTS (ranked by confirmation score) ───────")
            print(buy_df[display_cols].to_string(index=False))

        # Full breakdown for top 3 BUY signals
        top3 = buy_df.head(3)
        print(f"\n── FULL 10-SIGNAL BREAKDOWN — TOP 3 BUY SIGNALS ──────")
        breakdown_cols = [c for c in results.columns if c.startswith("✓_")]
        for _, row in top3.iterrows():
            print(f"\n  {row['Ticker']}  |  {row['Signal']}  |  Score: {row['Confirm_Score']}")
            print(f"  {'─'*60}")
            print(f"  {'Vol Today':15s} {row['Vol_Today_INR']}  (20d avg: {row['Vol_20d_Avg_INR']})")
            print(f"  {'OBV Position':15s} {row['OBV_Position_%']} of 20-day range")
            print(f"  {'─'*60}")
            for col in breakdown_cols:
                label = col.replace("✓_", "").replace("_", " ")
                print(f"  {label:22s} {row[col]}")
            print(f"\n  EXIT PLAN:")
            # Print exit plan wrapped at ~80 chars per line for readability
            plan_parts = row["Exit_Plan"].split("  ")
            for part in plan_parts:
                if part.strip():
                    print(f"    {part.strip()}")

        results.to_csv(OUTPUT_FILE, index=False)
        print(f"\n✓ Full results saved to: {OUTPUT_FILE}")
        print(f"  Exit_Plan column included for each ticker in the CSV.")
    else:
        print("No BUY signals passed the confirmation threshold.")