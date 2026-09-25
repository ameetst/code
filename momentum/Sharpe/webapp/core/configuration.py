"""
Configuration tab. Ported from the tab_config block of sharpe_dashboard_dhan.py:
capital/sizing inputs, ADTV/circuit/series filter toggles, the relative-52H-drawdown
and rank-drop exit thresholds, and the read-only Strategy Parameters summary.

Unlike the Streamlit dashboard, changes here do NOT live-preview the ranking impact
as you type -- momentum_lib's ranking compute is cached and keyed on the *saved*
config (see core.rankings.get_bundle), so the informational captions below (ADTV
pass counts, circuit-hit-exceeding count) reflect the currently saved values, and
any change takes effect project-wide only after Save (and only on the next page load).
"""
from pathlib import Path

import momentum_lib as ml

from webapp.core import config_store, rankings

# (min, max) per field; None means unbounded on that side. Mirrors the Streamlit
# number_input min_value/max_value pairs, which silently clamp rather than reject --
# we do the same server-side, since a raw POST could send anything.
FIELD_BOUNDS = {
    "capital":                 (100_000, None),
    "min_n":                   (3, 10),
    "max_n":                   (15, 30),
    "min_turnover":            (0.0, 10.0),
    "circuit_threshold":       (5, 100),
    "rel_dd_breach_threshold": (-50, 0),
    "hold_rank_buffer":        (1, 5000),
}


def clamp(key: str, value):
    lo, hi = FIELD_BOUNDS[key]
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def config_path() -> Path:
    return config_store.DATA_DIR / ml.CONFIG_FILENAME


def save(cfg: dict) -> Path:
    """Caller must gate this behind settings.READ_ONLY -- it performs no check itself."""
    return ml.save_config(cfg, str(config_store.DATA_DIR))


def adtv_pass_counts(bundle: rankings.Bundle, min_turnover_cr: float) -> dict | None:
    """None if the workbook has no VOLUME sheet (no TURNOVER_* columns in the result)."""
    result = bundle.result
    if "TURNOVER_12M" not in result.columns:
        return None
    turnover_6m = result["TURNOVER_6M"] if "TURNOVER_6M" in result.columns else result["TURNOVER_12M"] * 0
    return {
        "total": len(result),
        "pass_12m": int((result["TURNOVER_12M"] >= min_turnover_cr).sum()),
        "pass_6m": int((turnover_6m >= min_turnover_cr).sum()),
    }


def circuit_hit_exceed_count(bundle: rankings.Bundle, circuit_threshold: int) -> int | None:
    """Stocks whose trailing-252-day circuit-hit count is at/above the given threshold.
    Recomputed fresh (not read off `result`) so it reflects circuit_threshold even when
    circuit_filter_enabled is currently off in the saved config."""
    if not rankings.BAND_CSV.exists():
        return None
    try:
        c_df = ml.compute_circuit_hits(bundle.prices_df, list(bundle.prices_df.index),
                                        str(rankings.BAND_CSV), lookback_period=252)
    except Exception:
        return None
    return int((c_df["TOTAL_CIRCUIT_HITS"] >= circuit_threshold).sum())


def strategy_params(cfg: dict, universe: str) -> dict:
    """The dashboard's read-only 'Strategy Parameters' block, plus the derived Max
    Position Size line (auto-computed, not an independent setting)."""
    max_n = int(cfg["max_n"])
    max_wt_pct = (1.0 / max_n) * 100.0 if max_n else 0.0
    ledger_path = config_store.positions_ledger_path(universe)
    return {
        "RFR": "7.0%",
        "Windows": "12M / 9M / 6M / 3M",
        "Top N": f"Dynamic ({cfg['min_n']}–{cfg['max_n']})",
        "Entry Gate": f"Regime score >= {ml.DEFAULT_NEW_ENTRY_THRESHOLD}",
        "Hold Lock": "28 days",
        "52H Filter": ">= -25%",
        "Rel 52H DD Exit": f"< {float(cfg['rel_dd_breach_threshold']):.0f}% (respects hold lock)",
        "ADTV Filter": f">= {cfg['min_turnover']} Cr (12M or 6M median)",
        "Series EQ Filter": "Enabled" if cfg["eq_series_filter"] else "Disabled",
        "Circuit Filter": (f"Enabled (>= {cfg['circuit_threshold']} days)"
                           if cfg["circuit_filter_enabled"] else "Disabled"),
        "Rank Buffer": str(cfg["hold_rank_buffer"]),
        "Cash Yield": "6% p.a.",
        "Ledger File": ledger_path.name,
        "Max Position Size": f"Rs {cfg['capital'] / max_n:,.0f} ({max_wt_pct:.1f}% of capital)" if max_n
                              else "—",
    }
