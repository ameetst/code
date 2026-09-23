"""
Regime Score Breakdown. Ported from sharpe_dashboard_dhan.py: compute_ad_ratio (pure,
no network) plus the regime-history trend chart. Read-only difference: this module
never calls append_regime_history -- that file is written only by the Streamlit
dashboard/Sharpe.py, and read here the same way core.performance reads equity_history.json.
"""
import json

import pandas as pd

from webapp.settings import DATA_DIR

SIGNAL_WEIGHTS_PCT = {
    "ema50_score": 35, "ema_trend_score": 25, "breadth_score": 25, "momentum_score": 15,
}
SIGNAL_LABELS = {
    "ema50_score": "EMA50 Breadth", "ema_trend_score": "EMA Trend Breadth",
    "breadth_score": "52H Breadth", "momentum_score": "Momentum Breadth",
}


def compute_ad_ratio(prices_df: pd.DataFrame) -> tuple[int, int, float | None]:
    """1-day Advance/Decline ratio. Uses each stock's own last two VALID closes
    (dropna first) rather than the raw last two columns -- a freshly appended date
    column whose closes haven't been pulled in yet would otherwise make every
    stock's "1-day" return NaN, driving adv=dec=0 and the ratio to a misleading
    `inf` instead of the genuine "no data yet" case."""
    if len(prices_df.columns) < 2:
        return 0, 0, None
    rets = []
    for ticker in prices_df.index:
        px = prices_df.loc[ticker].dropna()
        if len(px) < 2:
            continue
        rets.append(px.iloc[-1] - px.iloc[-2])
    if not rets:
        return 0, 0, None
    rets = pd.Series(rets)
    adv = int((rets > 0).sum())
    dec = int((rets < 0).sum())
    if adv == 0 and dec == 0:
        return adv, dec, None
    ratio = adv / dec if dec > 0 else float("inf")
    return adv, dec, ratio


def load_regime_history(universe: str) -> list[dict]:
    path = DATA_DIR / f"{universe}_regime_history.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def trend_chart_records(history: list[dict], entry_threshold: float) -> str | None:
    """JSON records for static/js/line_chart.js: one series per Composite/Breadth/Momentum,
    plus a flat Entry Threshold reference line. None if fewer than 2 days are recorded
    (matches the dashboard's own "come back tomorrow" gate)."""
    if len(history) < 2:
        return None
    df = pd.DataFrame(history)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date")

    records = []
    for _, row in df.iterrows():
        records.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "Composite": float(row["Composite"]),
            "Breadth": float(row["Breadth"]),
            "Momentum": float(row["Momentum"]),
            "Entry Threshold": float(entry_threshold),
        })
    return json.dumps(records).replace("</", "<\\/")
