"""
Market Cap Momentum Breakdown. Ported from sharpe_dashboard_dhan.py:
load_stockdb, compute_cap_tier_dual (instant, STOCKDB.csv-based) and
compute_cap_tier_momentum (the opt-in, ~15s live Yahoo Finance market-cap fetch).
"""
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from webapp.settings import DATA_DIR

CAP_ORDER = ["LARGECAP", "MIDCAP", "SMALLCAP", "MICROCAP"]
CAP_LABELS = {"LARGECAP": "Large Cap", "MIDCAP": "Mid Cap", "SMALLCAP": "Small Cap", "MICROCAP": "Micro Cap"}
TIER_ORDER = ["Large Cap (1-100)", "Mid Cap (101-250)", "Small Cap (251-500)", "Micro Cap (501+)"]

_YAHOO_TTL = 86400  # matches @st.cache_data(ttl=86400) on the dashboard's compute_cap_tier_momentum
_yahoo_cache: dict = {}


def load_stockdb() -> dict:
    """{ticker: cap-tier string} from STOCKDB.csv. Empty dict if the file is absent."""
    path = DATA_DIR / "STOCKDB.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return dict(zip(df["SYMBOL"].str.strip(), df["MARKETCAP"].str.strip()))


def tier_color(pct: float) -> tuple[str, str, str]:
    """(fill, track-background, text) hex colors for a given %-positive value."""
    if pct >= 60:
        return "#2E7D32", "#E8F5E9", "#2E7D32"
    if pct >= 30:
        return "#F57F17", "#FFF8E1", "#E65100"
    return "#C62828", "#FFEBEE", "#C62828"


def compute_cap_tier_dual(result_df: pd.DataFrame, prices_df: pd.DataFrame,
                           stock_tickers: list, stockdb: dict) -> list[dict]:
    """Signal 1: 63-day raw price return > 0%. Signal 2: 3M Sharpe score > 0
    (risk-adjusted). Returns one row per cap tier with both signals and their delta."""
    records = []
    for t in stock_tickers:
        tier = stockdb.get(t)
        if tier is None:
            continue
        px = prices_df.loc[t].dropna() if t in prices_df.index else pd.Series(dtype=float)
        ret_63 = float(px.iloc[-1] / px.iloc[-63] - 1.0) if len(px) >= 63 else np.nan
        s3m = result_df.loc[t, "S_3M"] if (t in result_df.index and "S_3M" in result_df.columns) else np.nan
        records.append({
            "ticker": t, "cap_tier": tier,
            "ret_pos": bool(ret_63 > 0) if pd.notna(ret_63) else False,
            "sharpe_pos": bool(float(s3m) > 0) if pd.notna(s3m) else False,
        })

    df = pd.DataFrame(records)
    rows = []
    for tier in CAP_ORDER:
        sub = df[df["cap_tier"] == tier] if not df.empty else df
        if sub.empty:
            continue
        total = len(sub)
        ret_pos = int(sub["ret_pos"].sum())
        sharpe_pos = int(sub["sharpe_pos"].sum())
        delta = sharpe_pos - ret_pos
        ret_pct = round(ret_pos / total * 100, 1)
        sharpe_pct = round(sharpe_pos / total * 100, 1)
        rf, rt, rtxt = tier_color(ret_pct)
        sf, st_, stxt = tier_color(sharpe_pct)
        rows.append({
            "cap_tier": CAP_LABELS.get(tier, tier), "total": total,
            "ret_pos": ret_pos, "ret_pct": ret_pct, "ret_colors": (rf, rt, rtxt),
            "sharpe_pos": sharpe_pos, "sharpe_pct": sharpe_pct, "sharpe_colors": (sf, st_, stxt),
            "delta": delta,
        })
    return rows


def compute_cap_tier_momentum(prices_df: pd.DataFrame, stock_tickers: list, *, force: bool = False) -> list[dict]:
    """% of stocks with positive 63-day momentum per cap tier, tier classified by live
    Yahoo Finance market cap. Slow (~15s across the universe); cached _YAHOO_TTL seconds
    unless force=True. Caller triggers this explicitly (an HTMX "Load"/"Refresh" button),
    same opt-in shape as the dashboard's own button."""
    now = time.time()
    cached = _yahoo_cache.get("value")
    if not force and cached and now - cached[1] < _YAHOO_TTL:
        return cached[0]

    import yfinance as yf

    mom_63 = {}
    for t in stock_tickers:
        px = prices_df.loc[t].dropna() if t in prices_df.index else pd.Series(dtype=float)
        mom_63[t] = (px.iloc[-1] / px.iloc[-63]) - 1.0 if len(px) >= 63 else np.nan

    def fetch_mcap(ticker):
        try:
            return ticker, yf.Ticker(f"{ticker}.NS").fast_info.market_cap
        except Exception:
            try:
                return ticker, yf.Ticker(f"{ticker}.BO").fast_info.market_cap
            except Exception:
                return ticker, 0

    market_caps = {}
    with ThreadPoolExecutor(max_workers=30) as exe:
        for t, mcap in exe.map(fetch_mcap, stock_tickers):
            market_caps[t] = mcap

    df = pd.DataFrame({"MOM_63": pd.Series(mom_63), "MCAP": pd.Series(market_caps)})
    df = df[df["MCAP"] > 0].sort_values("MCAP", ascending=False)
    df["Rank"] = range(1, len(df) + 1)

    def get_tier(rank):
        if rank <= 100:
            return "Large Cap (1-100)"
        if rank <= 250:
            return "Mid Cap (101-250)"
        if rank <= 500:
            return "Small Cap (251-500)"
        return "Micro Cap (501+)"

    df["Cap Tier"] = df["Rank"].map(get_tier)
    df["Is_Positive"] = df["MOM_63"] > 0

    summary = df.groupby("Cap Tier")["Is_Positive"].agg(["count", "sum"])
    rows = []
    for tier in TIER_ORDER:
        if tier not in summary.index:
            continue
        total = int(summary.loc[tier, "count"])
        positive = int(summary.loc[tier, "sum"])
        pct = round(positive / total * 100, 1) if total else 0.0
        fill, track, txt = tier_color(pct)
        rows.append({"tier": tier, "total": total, "positive": positive, "pct": pct,
                      "colors": (fill, track, txt)})

    _yahoo_cache["value"] = (rows, now)
    return rows


def clear_yahoo_cache() -> None:
    _yahoo_cache.pop("value", None)
