"""
Config + universe-file resolution. Logic ported from the CONFIGURATION block of
sharpe_dashboard_dhan.py; the persisted file (dashboard_config.json) is shared
with the Streamlit dashboards and Sharpe.py, and is only ever read here.
"""
from pathlib import Path

import momentum_lib as ml

from webapp.settings import DATA_DIR

_PREFERRED_FILES = ["N750_updated.xlsx", "NSEAll_updated.xlsx", "N500_updated.xlsx"]


def load_config() -> dict:
    return ml.load_config(str(DATA_DIR))


def available_files() -> list[str]:
    """Universe workbooks, preferring *_updated.xlsx over the raw template of the same name."""
    all_xlsx = [f.name for f in DATA_DIR.glob("*.xlsx")
                if not f.name.startswith("~") and "ranking" not in f.name.lower()]
    updated = {f for f in all_xlsx if f.endswith("_updated.xlsx")}
    return sorted(
        f for f in all_xlsx
        if f.endswith("_updated.xlsx") or f"{f.replace('.xlsx', '')}_updated.xlsx" not in updated
    )


def resolve_file(cfg: dict) -> str:
    """The workbook the dashboard would open: saved choice if it exists, else a sensible default."""
    files = available_files()
    saved = cfg.get("file", "")
    if saved in files:
        return saved
    preferred = next((f for f in _PREFERRED_FILES if f in files), None)
    return preferred or (files[0] if files else "")


def universe_name(filename: str) -> str:
    return filename.replace("_updated.xlsx", "").replace(".xlsx", "")


def positions_ledger_path(universe: str) -> Path:
    candidates = [DATA_DIR / f"{universe}_positions_ledger.json", DATA_DIR / "positions_ledger.json"]
    return next((p for p in candidates if p.exists()), candidates[0])
