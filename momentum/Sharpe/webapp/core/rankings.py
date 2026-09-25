"""
Rankings pipeline + Top-N table. Ported from sharpe_dashboard_dhan.py (load_data,
compute_all, and the Top-N tab), with one deliberate difference: this module is
strictly read-only. The Streamlit page also appends to the regime history and
re-syncs the positions ledger on every load; neither happens here.

The ~11s load + compute is cached in-process, keyed on the input file mtimes and
the config values that affect the result, so it reruns only when data or config change.
"""
import datetime
import json
import math
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

import momentum_lib as ml

from webapp.core import config_store
from webapp.settings import DATA_DIR

# Same constants the dashboard defines locally (not exported by momentum_lib).
RFR_ANNUAL = 0.07
TRADING_DAYS = 252
WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
VOL_WINDOWS = (252, 189, 126, 63)
ELIGIBLE_52H_FLOOR = -25  # matches the "Eligible (52H)" metric and the master eligibility gate
BAND_CSV = DATA_DIR / "Price_Band_List.csv"


@dataclass(frozen=True)
class Bundle:
    prices_df: pd.DataFrame
    result: pd.DataFrame
    regime_score: float
    regime_detail: dict
    dates: list
    latest_prices: dict


_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_MAX_ENTRIES = 4


def _mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _latest_price(ticker, prices_df) -> float:
    if ticker in prices_df.index:
        series = prices_df.loc[ticker].dropna()
        if not series.empty:
            return float(series.iloc[-1])
    return 0.0


def get_bundle(cfg: dict) -> Bundle:
    filename = config_store.resolve_file(cfg)
    path = DATA_DIR / filename
    if not filename or not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    key = (
        str(path), _mtime(path), _mtime(BAND_CSV),
        cfg["min_turnover"], cfg["eq_series_filter"], cfg["circuit_filter_enabled"],
        cfg["circuit_threshold"], cfg["min_n"], cfg["max_n"],
    )
    with _cache_lock:  # also stops two first-requests from both paying the 11s compute
        if key in _cache:
            return _cache[key]

        prices_df, nifty_series, stock_tickers, dates = ml.load_prices(str(path))
        try:
            volume_df = ml.load_volume(str(path))
        except Exception:
            volume_df = None  # VOLUME sheet is optional

        result, regime_score, regime_detail = ml.compute_universe_rankings(
            prices_df, nifty_series, stock_tickers,
            volume_df=volume_df,
            min_turnover_cr=cfg["min_turnover"],
            eq_series_filter=cfg["eq_series_filter"],
            circuit_filter_enabled=cfg["circuit_filter_enabled"],
            circuit_threshold=int(cfg["circuit_threshold"]),
            band_csv_path=str(BAND_CSV),
            windows=WINDOWS,
            trading_days=TRADING_DAYS,
            rfr_annual=RFR_ANNUAL,
            signal_weights=ml.DEFAULT_SIGNAL_WEIGHTS,
            min_n=int(cfg["min_n"]),
            max_n=int(cfg["max_n"]),
            new_entry_threshold=ml.DEFAULT_NEW_ENTRY_THRESHOLD,
        )
        bundle = Bundle(
            prices_df=prices_df,
            result=result,
            regime_score=float(regime_score),
            regime_detail=regime_detail,
            dates=list(dates),
            latest_prices={t: _latest_price(t, prices_df) for t in stock_tickers},
        )
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.pop(next(iter(_cache)))
        _cache[key] = bundle
        return bundle


def finite(x):
    """NaN/inf → None (JSON has no NaN); numpy scalars → float."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def mean_volatility(ticker, prices_df):
    """Annualised volatility, mean across the four ranking windows (same as the weight-sizing engine)."""
    if ticker not in prices_df.index:
        return None
    px = prices_df.loc[ticker].dropna()
    if len(px) <= 10:
        return None
    vols = []
    for w in VOL_WINDOWS:
        pw = px.iloc[-w:] if len(px) >= w else px
        lr = np.diff(np.log(pw.values))
        if len(lr) > 5:
            vols.append(np.std(lr, ddof=1) * np.sqrt(252))
    return float(np.mean(vols)) if vols else None


def held_tickers(universe: str) -> set:
    """Tickers with an open position, from the positions ledger (read-only; the Streamlit app keeps it synced)."""
    path = config_store.positions_ledger_path(universe)
    if not path.exists():
        return set()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return set()
    return {t for t, rec in raw.items() if isinstance(rec, dict) and float(rec.get("qty", 1) or 0) > 0}


def regime_badge(score: float) -> str:
    if score >= 0.65:
        return "high"
    if score >= ml.DEFAULT_NEW_ENTRY_THRESHOLD:
        return "mid"
    return "low"


def top_rows(bundle: Bundle, limit: int, held: set, prices: dict | None = None) -> list[dict]:
    """`prices` overrides bundle.latest_prices for the LTP column when given -- pass
    dhan_client.apply_cached_prices(bundle.latest_prices) to reflect Tradelog's
    "Refresh Live Market Prices" button here too, matching the dashboard's single
    global price override."""
    prices = prices if prices is not None else bundle.latest_prices
    top_tickers = bundle.result.head(limit).index.tolist()

    # Circuit-hit frequency (UC/LC over trailing 252 sessions) — always computed
    # here regardless of the Circuit Hit Frequency Filter setting, matching the
    # dashboard's New Entry Candidates precedent.
    try:
        circuit_df = ml.compute_circuit_hits(
            bundle.prices_df, top_tickers, str(BAND_CSV), lookback_period=252)
    except Exception:
        circuit_df = None

    rows = []
    for ticker, row in bundle.result.head(limit).iterrows():
        mean_vol = mean_volatility(ticker, bundle.prices_df)
        comp = finite(row["COMPOSITE"])
        vol_adj = round(comp / mean_vol, 3) if (comp is not None and mean_vol and mean_vol > 0) else None
        ltp = prices.get(ticker, 0.0)
        rank = finite(row["RANK"])
        if circuit_df is not None and ticker in circuit_df.index:
            circuit_hits = f"{int(circuit_df.loc[ticker, 'UC_COUNT'])} / {int(circuit_df.loc[ticker, 'LC_COUNT'])}"
        else:
            circuit_hits = None
        rows.append({
            "rank": int(rank) if rank is not None else None,
            "ticker": ticker,
            "composite": comp,
            "res_mom": finite(row.get("RES_MOM")),
            "volatility_pct": round(mean_vol * 100, 1) if mean_vol is not None else None,
            "vol_adj_score": vol_adj,
            "ltp": ltp if ltp > 0 else None,
            "circuit_hits": circuit_hits,
            "held": ticker in held,
        })
    return rows


def eligible_52h_count(bundle: Bundle) -> int:
    return int((bundle.result["PCT_FROM_52H"] >= ELIGIBLE_52H_FLOOR).sum())


def iso(d) -> str:
    return d.isoformat() if isinstance(d, (datetime.date, datetime.datetime)) else str(d)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(d) -> str:
    """%d-%b-%y, matching the Streamlit dashboard's date formatting."""
    if isinstance(d, str):
        d = datetime.date.fromisoformat(d)
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.strftime('%y')}"


# ── Full Universe Rankings (ported from the tab_calcs block) ─────────────────

FULL_DISPLAY_COLUMNS = ["RANK", "SERIES", "COMPOSITE", "SHARPE_3", "RES_MOM",
                         "1M%", "3M%", "12M%", "PCT_FROM_52H", "REL_52H_DD", "BETA"]
FULL_COLUMN_LABELS = {
    "RANK": "Rank", "SERIES": "Series", "COMPOSITE": "Composite", "SHARPE_3": "Sharpe 3M",
    "RES_MOM": "Res Mom", "1M%": "1M %", "3M%": "3M %", "12M%": "12M %",
    "PCT_FROM_52H": "% From 52H", "REL_52H_DD": "Rel 52H DD", "BETA": "Beta",
}
SORT_OPTIONS = ["RANK", "COMPOSITE", "RES_MOM", "PCT_FROM_52H", "REL_52H_DD"]
ELIGIBILITY_OPTIONS = ["All", "Eligible only", "Disqualified only"]
_ONE_DECIMAL_COLS = {"PCT_FROM_52H", "REL_52H_DD", "1M%", "3M%", "12M%"}
_TWO_DECIMAL_COLS = {"BETA"}


def full_display_columns(bundle: Bundle) -> list[str]:
    return [c for c in FULL_DISPLAY_COLUMNS if c in bundle.result.columns]


def full_rankings_rows(bundle: Bundle, eligibility: str, sort_col: str,
                        top_n: int, rel_dd_breach_threshold: float) -> list[dict]:
    cols = full_display_columns(bundle)
    df = bundle.result[cols].copy()
    df.index.name = "TICKER"
    df = df.reset_index()

    if eligibility == "Eligible only":
        df = df[df["PCT_FROM_52H"] >= ELIGIBLE_52H_FLOOR]
    elif eligibility == "Disqualified only":
        df = df[df["PCT_FROM_52H"] < ELIGIBLE_52H_FLOOR]

    if sort_col not in SORT_OPTIONS or sort_col not in df.columns:
        sort_col = "RANK"
    df = df.sort_values(sort_col, ascending=(sort_col == "RANK"), na_position="last").head(top_n)

    dynamic_n = int(bundle.regime_detail["dynamic_n"])
    rows = []
    for _, r in df.iterrows():
        rank = finite(r.get("RANK"))
        pct52h = finite(r.get("PCT_FROM_52H"))
        rel_dd = finite(r.get("REL_52H_DD"))
        if pct52h is not None and pct52h < ELIGIBLE_52H_FLOOR:
            row_class = "row-disqualified"
        elif rel_dd is not None and rel_dd < rel_dd_breach_threshold:
            row_class = "row-breach"
        elif rank is not None and rank <= dynamic_n:
            row_class = "row-regime"
        else:
            row_class = ""

        cells = []
        for c in cols:
            if c == "RANK":
                cells.append(str(int(rank)) if rank is not None else "—")
            elif c == "SERIES":
                cells.append(str(r[c]))
            else:
                fv = finite(r[c])
                if fv is None:
                    cells.append("—")
                elif c in _ONE_DECIMAL_COLS:
                    cells.append(f"{fv:.1f}")
                elif c in _TWO_DECIMAL_COLS:
                    cells.append(f"{fv:.2f}")
                else:
                    cells.append(f"{fv:.3f}")
        rows.append({"ticker": r["TICKER"], "row_class": row_class, "cells": cells})
    return rows


def full_rankings_summary(bundle: Bundle, rel_dd_breach_threshold: float) -> dict:
    result = bundle.result
    return {
        "universe_count": len(result),
        "eligible_count": int((result["PCT_FROM_52H"] >= ELIGIBLE_52H_FLOOR).sum()),
        "disqualified_count": int((result["PCT_FROM_52H"] < ELIGIBLE_52H_FLOOR).sum()),
        "breach_count": int((result["REL_52H_DD"] < rel_dd_breach_threshold).sum()),
        "dynamic_n": int(bundle.regime_detail["dynamic_n"]),
    }
