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
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import momentum_lib as ml

from webapp.core import config_store, dhan_client
from webapp.settings import DATA_DIR

# Same constants the dashboard defines locally (not exported by momentum_lib).
RFR_ANNUAL = 0.07
TRADING_DAYS = 252
WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
VOL_WINDOWS = (252, 189, 126, 63)
ELIGIBLE_52H_FLOOR = -25  # matches the "Eligible (52H)" metric and the master eligibility gate
STOCKDB_CSV = DATA_DIR / "STOCKDB.csv"  # Series/Band/Market-Cap source (retired Price_Band_List.csv)


@dataclass(frozen=True)
class Bundle:
    prices_df: pd.DataFrame
    result: pd.DataFrame
    regime_score: float
    regime_detail: dict
    dates: list
    latest_prices: dict
    market_caps_raw: dict = field(default_factory=dict)  # ticker -> raw Rs market cap from STOCKDB.csv (NOT Cr); see configuration.mcap_pass_count
    # ticker -> why it can't be bought under the config this bundle was built with ("" = purchasable)
    purchase_block: pd.Series | None = None


_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_MAX_ENTRIES = 4


def clear_cache() -> None:
    """Drops every cached ranking bundle, so the next get_bundle() recomputes from the data
    files and the current saved Configuration. Normally unnecessary -- the cache key already
    tracks the data files' mtimes and every compute-affecting setting."""
    with _cache_lock:
        _cache.clear()


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


def _load_market_caps_raw() -> dict:
    """{ticker: raw Rs market cap} from STOCKDB.csv's MARKETCAP_CR column (misnamed --
    it's absolute Rs, not Cr; divide by 1e7 to get Rs Cr). Empty dict if unavailable."""
    if not STOCKDB_CSV.exists():
        return {}
    try:
        df = pd.read_csv(STOCKDB_CSV)
        caps = {}
        for _, row in df.iterrows():
            try:
                caps[str(row["SYMBOL"]).strip()] = float(row["MARKETCAP_CR"])
            except (ValueError, TypeError, KeyError):
                pass
        return caps
    except Exception:
        return {}


def get_bundle(cfg: dict) -> Bundle:
    filename = config_store.resolve_file(cfg)
    path = DATA_DIR / filename
    if not filename or not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    key = (
        str(path), _mtime(path), _mtime(STOCKDB_CSV),
        cfg["min_turnover"], cfg["min_cmp"], cfg["min_market_cap"], cfg["eq_series_filter"],
        cfg["circuit_filter_enabled"], cfg["circuit_threshold"], cfg["min_n"], cfg["max_n"],
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
            min_cmp=cfg["min_cmp"],
            min_market_cap=cfg["min_market_cap"],
            eq_series_filter=cfg["eq_series_filter"],
            circuit_filter_enabled=cfg["circuit_filter_enabled"],
            circuit_threshold=int(cfg["circuit_threshold"]),
            stockdb_csv_path=str(STOCKDB_CSV),
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
            # unpriced tickers are omitted (not 0.0) so consumers fall back to cost / entry price
            latest_prices={t: px for t in stock_tickers if (px := _latest_price(t, prices_df)) > 0},
            market_caps_raw=_load_market_caps_raw(),
            purchase_block=ml.compute_purchase_blocks(
                result, prices_df,
                min_turnover_cr=cfg["min_turnover"], min_cmp=cfg["min_cmp"],
                min_market_cap=cfg["min_market_cap"], eq_series_filter=cfg["eq_series_filter"],
                circuit_filter_enabled=cfg["circuit_filter_enabled"],
                circuit_threshold=int(cfg["circuit_threshold"]),
                stockdb_csv_path=str(STOCKDB_CSV)),
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


def regime_badge(score: float) -> str:
    if score >= 0.65:
        return "high"
    if score >= ml.DEFAULT_NEW_ENTRY_THRESHOLD:
        return "mid"
    return "low"


def effective_prices(bundle: Bundle) -> dict:
    """bundle.latest_prices overridden by the last "Refresh Live Market Prices" fetch, including
    tickers the workbook has no usable close for."""
    known = bundle.prices_df.index if bundle.prices_df is not None else None
    return dhan_client.apply_cached_prices(bundle.latest_prices, known=known)


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
                         "PCT_FROM_52H", "REL_52H_DD", "BETA"]
FULL_COLUMN_LABELS = {
    "RANK": "Rank", "SERIES": "Series", "COMPOSITE": "Composite", "SHARPE_3": "Sharpe 12/6/3M",
    "RES_MOM": "Res Mom", "CLENOW_SCORE": "Clenow Score",
    "PCT_FROM_52H": "% From 52H", "PCT_FROM_ATH": "% From ATH", "REL_52H_DD": "Rel 52H DD", "BETA": "Beta",
}
SORT_OPTIONS = ["RANK", "COMPOSITE", "RES_MOM", "PCT_FROM_52H", "REL_52H_DD"]
ELIGIBILITY_OPTIONS = ["All", "Eligible only", "Disqualified only"]
_ONE_DECIMAL_COLS = {"PCT_FROM_52H", "PCT_FROM_ATH", "REL_52H_DD", "CLENOW_SCORE"}
# Columns that come from the rankings workbook's CALCS sheet, not from the ranking result.
_WORKBOOK_COLS = {"CLENOW_SCORE", "PCT_FROM_ATH"}
_TWO_DECIMAL_COLS = {"BETA"}


def load_clenow(cfg: dict):
    """(Series | None, workbook-generated datetime | None): CLENOW_SCORE from this universe's
    Sharpe.py rankings workbook. Strictly `<universe>_rankings.xlsx` (no N750/NSEAll fallback,
    unlike the download button, so another universe's scores are never shown). Not part of the
    ranking result: Sharpe.py computes it for the workbook only, so it is as of that run."""
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    return ml.load_clenow_scores_from_workbook(DATA_DIR / f"{universe}_rankings.xlsx")


def load_ath(cfg: dict):
    """(DataFrame | None, workbook-generated datetime | None): ATH / ATH_DATE / PCT_FROM_ATH from this
    universe's Sharpe.py rankings workbook (CALCS sheet), as of that run. Same rules as load_clenow."""
    return load_ath_for_universe(config_store.universe_name(config_store.resolve_file(cfg)))


def load_ath_for_universe(universe: str):
    return ml.load_ath_from_workbook(DATA_DIR / f"{universe}_rankings.xlsx")


def full_display_columns(bundle: Bundle, clenow=None, ath=None) -> list[str]:
    cols = [c for c in FULL_DISPLAY_COLUMNS if c in bundle.result.columns]
    if clenow is not None and "RES_MOM" in cols:
        cols.insert(cols.index("RES_MOM") + 1, "CLENOW_SCORE")   # right after Res Mom
    if ath is not None and "PCT_FROM_52H" in cols:
        cols.insert(cols.index("PCT_FROM_52H") + 1, "PCT_FROM_ATH")   # right after % From 52H
    return cols


def sort_options(clenow=None, ath=None) -> list[str]:
    opts = list(SORT_OPTIONS)
    if clenow is not None:
        opts.insert(opts.index("RES_MOM") + 1, "CLENOW_SCORE")
    if ath is not None:
        opts.insert(opts.index("PCT_FROM_52H") + 1, "PCT_FROM_ATH")
    return opts


def search_terms(search: str) -> list[str]:
    """Upper-cased ticker fragments from a free-text box; commas and whitespace both separate."""
    return ml.ticker_search_terms(search)


def ath_tooltip(ath, ticker) -> str:
    """'ATH Rs 1,234.50 on 09-Nov-26' for the % From ATH cell, '' when the workbook has no ATH for it."""
    if ath is None or ticker not in ath.index:
        return ""
    price, when = finite(ath.at[ticker, "ATH"]), ath.at[ticker, "ATH_DATE"]
    if price is None or not isinstance(when, str):
        return ""
    return f"ATH Rs {price:,.2f} on {fmt_date(when)}"


def full_rankings_rows(bundle: Bundle, eligibility: str, sort_col: str,
                        top_n: int, rel_dd_breach_threshold: float, search: str = "",
                        clenow=None, ath=None, held=None) -> list[dict]:
    """With a non-empty `search`, rows are the tickers CONTAINING any search term (case-insensitive),
    taken from the whole universe: `top_n` is not applied, so a name outside the top N is still found.
    `held` (a set of tickers, or None for no filter) keeps only those names; it too ignores `top_n`."""
    cols = full_display_columns(bundle, clenow, ath)
    df = bundle.result[[c for c in cols if c not in _WORKBOOK_COLS]].copy()
    df.index.name = "TICKER"
    df = df.reset_index()
    if clenow is not None:
        df["CLENOW_SCORE"] = df["TICKER"].map(clenow)
    if ath is not None:
        df["PCT_FROM_ATH"] = df["TICKER"].map(ath["PCT_FROM_ATH"])

    if eligibility == "Eligible only":
        df = df[df["PCT_FROM_52H"] >= ELIGIBLE_52H_FLOOR]
    elif eligibility == "Disqualified only":
        df = df[df["PCT_FROM_52H"] < ELIGIBLE_52H_FLOOR]

    if sort_col not in sort_options(clenow, ath) or sort_col not in df.columns:
        sort_col = "RANK"
    if held is not None:
        df = df[df["TICKER"].isin(held)]
    terms = search_terms(search)
    df = ml.filter_tickers_by_search(df, terms)
    df = df.sort_values(sort_col, ascending=(sort_col == "RANK"), na_position="last")
    if not terms and held is None:
        df = df.head(top_n)

    dynamic_n = int(bundle.regime_detail["dynamic_n"])
    blocks = bundle.purchase_block
    rows = []
    for _, r in df.iterrows():
        rank = finite(r.get("RANK"))
        pct52h = finite(r.get("PCT_FROM_52H"))
        rel_dd = finite(r.get("REL_52H_DD"))
        blocked = blocks.get(r["TICKER"], "") if blocks is not None else ""
        if blocked:
            row_class = "row-blocked"  # greyed out: the current Configuration blocks buying it
        elif pct52h is not None and pct52h < ELIGIBLE_52H_FLOOR:
            row_class = "row-disqualified"
        elif rel_dd is not None and rel_dd < rel_dd_breach_threshold:
            row_class = "row-breach"
        elif rank is not None and rank <= dynamic_n:
            row_class = "row-regime"
        else:
            row_class = ""

        cells, titles = [], []
        for c in cols:
            titles.append(ath_tooltip(ath, r["TICKER"]) if c == "PCT_FROM_ATH" else "")
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
        rows.append({"ticker": r["TICKER"], "row_class": row_class, "cells": cells,
                     "titles": titles, "blocked": blocked})
    return rows


_BLOCK_CATEGORIES = [("Min CMP", "Min CMP"), ("Min Market Cap", "Min Market Cap"),
                     ("MDTV", "MDTV"), ("Series EQ", "Series "), ("Circuit", "Circuit")]


def blocked_breakdown(rows: list[dict]) -> dict:
    """Summary of the blocked rows among those shown, for the table caption:
    {"n_blocked", "n_shown", "by_reason": [(label, count), ...]} (only reasons with rows)."""
    blocked = [r["blocked"] for r in rows if r.get("blocked")]
    by_reason = [(label, sum(1 for b in blocked if key in b)) for label, key in _BLOCK_CATEGORIES]
    return {"n_blocked": len(blocked), "n_shown": len(rows),
            "by_reason": [(label, n) for label, n in by_reason if n]}


def full_rankings_summary(bundle: Bundle, rel_dd_breach_threshold: float) -> dict:
    result = bundle.result
    return {
        "universe_count": len(result),
        "eligible_count": int((result["PCT_FROM_52H"] >= ELIGIBLE_52H_FLOOR).sum()),
        "disqualified_count": int((result["PCT_FROM_52H"] < ELIGIBLE_52H_FLOOR).sum()),
        "breach_count": int((result["REL_52H_DD"] < rel_dd_breach_threshold).sum()),
        "dynamic_n": int(bundle.regime_detail["dynamic_n"]),
    }
