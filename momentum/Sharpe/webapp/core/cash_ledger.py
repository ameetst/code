"""
Cash Ledger. Ported from sharpe_dashboard_dhan.py: load/save + summary/running-balance
helpers (load_cash_ledger, save_cash_ledger, compute_cash_ledger_summary,
compute_cash_ledger_running_balance).

Read-only difference: load_cash_ledger never creates the file when it's missing.
"""
import json

from webapp.core.storage import safe_write_json
from webapp.settings import DATA_DIR

CASH_ENTRY_TYPES = ["Deposit", "Withdrawal", "Dividend", "Interest", "Fees/Charges"]
CASH_INFLOW_TYPES = {"Deposit", "Dividend", "Interest"}
CASH_OUTFLOW_TYPES = {"Withdrawal", "Fees/Charges"}


def load_cash_ledger(universe: str) -> list[dict]:
    path = DATA_DIR / f"{universe}_cash_ledger.json"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        bak_path = path.with_suffix(".bak")
        if bak_path.exists():
            try:
                with open(bak_path) as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
        return []


def save_cash_ledger(universe: str, entries: list[dict]) -> None:
    """Caller must gate this behind settings.READ_ONLY -- it performs no check itself."""
    safe_write_json(DATA_DIR / f"{universe}_cash_ledger.json", entries)


def compute_summary(entries: list[dict]) -> dict:
    totals = {t: 0.0 for t in CASH_ENTRY_TYPES}
    for e in entries:
        totals[e["type"]] = totals.get(e["type"], 0.0) + float(e["amount"])
    total_in = totals["Deposit"] + totals["Dividend"] + totals["Interest"]
    total_out = totals["Withdrawal"] + totals["Fees/Charges"]
    return {"by_type": totals, "total_in": total_in, "total_out": total_out, "net": total_in - total_out}


def running_balance(entries: list[dict]) -> list[dict]:
    """Replays entries chronologically and returns them with a running balance, oldest-first."""
    try:
        sorted_entries = sorted(entries, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_entries = entries
    running = 0.0
    out = []
    for e in sorted_entries:
        amt = float(e["amount"])
        running += amt if e["type"] in CASH_INFLOW_TYPES else -amt
        out.append({**e, "balance": running})
    return out
