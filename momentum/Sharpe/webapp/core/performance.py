"""
Performance Tracker. Ported from the Performance Tracker tab of
sharpe_dashboard_dhan.py — reads {universe}_equity_history.json (written by
Sharpe.py, never by this webapp) and derives the NAV/alpha/drawdown metrics
plus the per-day records the equity chart (static/js/equity_chart.js) plots.
"""
import json

import pandas as pd

from webapp.settings import DATA_DIR


def load_equity_history(universe: str) -> list[dict]:
    path = DATA_DIR / f"{universe}_equity_history.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def build_view(equity_data: list[dict]) -> dict | None:
    """None means "not enough data yet" — the caller shows the Streamlit dashboard's
    same one-day / zero-day messaging instead of a chart."""
    if len(equity_data) < 2:
        return None

    df = pd.DataFrame(equity_data)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)

    port_nav = float(df["portfolio_nav"].iloc[-1])
    bench_nav = float(df["benchmark_nav"].iloc[-1])
    port_ret_total = (port_nav / 100.0 - 1.0) * 100
    bench_ret_total = (bench_nav / 100.0 - 1.0) * 100

    port_peak = df["portfolio_nav"].cummax()
    max_dd = float(((df["portfolio_nav"] / port_peak - 1.0) * 100).min())
    bench_peak = df["benchmark_nav"].cummax()
    bench_max_dd = float(((df["benchmark_nav"] / bench_peak - 1.0) * 100).min())

    has_weighting_flag = "qty_weighted" in df.columns
    records = []
    for _, row in df.iterrows():
        if has_weighting_flag and pd.notna(row.get("qty_weighted")):
            method = "weighted" if bool(row["qty_weighted"]) else "fallback"
        else:
            method = "legacy"
        records.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "port": round(float(row["portfolio_nav"]), 4),
            "bench": round(float(row["benchmark_nav"]), 4),
            "n_held": int(row["n_held"]) if pd.notna(row.get("n_held")) else None,
            "invested": float(row["invested_frac"]) if pd.notna(row.get("invested_frac")) else None,
            "method": method,
        })
    # Defensive: guarantee no literal "</script>" can appear if this is ever
    # embedded inside a <script> tag, even though the source data is our own.
    records_json = json.dumps(records).replace("</", "<\\/")

    detail_rows = []
    for _, row in df.iterrows():
        detail_rows.append({
            "date": row["date"].strftime("%d-%b-%Y"),
            "port_nav": round(float(row["portfolio_nav"]), 2),
            "bench_nav": round(float(row["benchmark_nav"]), 2),
            "port_ret": round(float(row["portfolio_ret"]) * 100, 3) if pd.notna(row.get("portfolio_ret")) else None,
            "bench_ret": round(float(row["benchmark_ret"]) * 100, 3) if pd.notna(row.get("benchmark_ret")) else None,
            "n_held": int(row["n_held"]) if pd.notna(row.get("n_held")) else None,
            "invested_pct": round(float(row["invested_frac"]) * 100, 1) if pd.notna(row.get("invested_frac")) else None,
        })

    return {
        "port_nav": port_nav, "bench_nav": bench_nav,
        "port_ret_total": port_ret_total, "bench_ret_total": bench_ret_total,
        "alpha": port_ret_total - bench_ret_total,
        "max_dd": max_dd, "bench_max_dd": bench_max_dd,
        "n_days": len(df),
        "start_date": df["date"].iloc[0].strftime("%d-%b-%Y"),
        "end_date": df["date"].iloc[-1].strftime("%d-%b-%Y"),
        "records_json": records_json,
        "detail_rows": detail_rows,
    }
