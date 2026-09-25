"""
Cash Ledger. Ported from sharpe_dashboard_dhan.py's tab_cash block.

Same read-only pattern as Tradelog: mutating routes check settings.READ_ONLY inline
and, when blocked, re-render the content partial with a banner at 200 (not 403), since
HTMX does not swap in error-status response bodies by default.
"""
import datetime
import uuid

from flask import Blueprint, render_template, request

from webapp import settings
from webapp.core import cash_ledger, config_store

bp = Blueprint("cash_ledger", __name__)


def _build_context(universe: str, *, error=None, warning=None, success=None, editing_id=None) -> dict:
    entries = cash_ledger.load_cash_ledger(universe)
    summary = cash_ledger.compute_summary(entries)
    with_balance = cash_ledger.running_balance(entries)

    history_rows = []
    for e in reversed(with_balance):
        history_rows.append({
            **e,
            "row_class": "row-regime" if e["type"] in cash_ledger.CASH_INFLOW_TYPES else "row-breach",
        })

    editing_entry = next((e for e in entries if e["id"] == editing_id), None) if editing_id else None

    return {
        "universe": universe,
        "summary": summary,
        "history_rows": history_rows,
        "entry_types": cash_ledger.CASH_ENTRY_TYPES,
        "editing_entry": editing_entry,
        "error": error, "warning": warning, "success": success,
        "today": datetime.date.today().isoformat(),
    }


def _universe() -> str:
    cfg = config_store.load_config()
    return config_store.universe_name(config_store.resolve_file(cfg))


@bp.route("/cash")
def index():
    ctx = _build_context(_universe())
    return render_template("cash_ledger.html", **ctx)


@bp.route("/cash/edit-form/<entry_id>")
def edit_form(entry_id):
    ctx = _build_context(_universe(), editing_id=entry_id)
    return render_template("_cash_edit_form.html", **ctx)


@bp.route("/cash/add", methods=["POST"])
def add():
    universe = _universe()

    if settings.READ_ONLY:
        ctx = _build_context(universe, error="This webapp is read-only right now "
                              "— cash entries stay in the Streamlit dashboard for now.")
        return render_template("_cash_ledger_content.html", **ctx)

    entry_type = request.form.get("type", "")
    date_str = request.form.get("date") or datetime.date.today().isoformat()
    note = (request.form.get("note") or "").strip()
    try:
        amount = float(request.form.get("amount", 0))
    except ValueError:
        amount = 0.0

    error, warning, success = None, None, None
    if entry_type not in cash_ledger.CASH_ENTRY_TYPES:
        error = f"Unknown entry type: {entry_type}"
    elif amount <= 0:
        error = "Amount must be greater than zero. Entry not recorded."
    else:
        entries = cash_ledger.load_cash_ledger(universe)
        summary = cash_ledger.compute_summary(entries)
        if entry_type in cash_ledger.CASH_OUTFLOW_TYPES and amount > summary["net"] + 1e-9:
            warning = (f"This {entry_type.lower()} of Rs {amount:,.2f} exceeds the current net cash "
                       f"contributed (Rs {summary['net']:,.2f}). Recorded anyway — investment "
                       f"gains/proceeds outside the ledger may cover the difference.")
        new_entry = {
            "id": str(uuid.uuid4()), "date": date_str,
            "timestamp": datetime.datetime.now().isoformat(),
            "type": entry_type, "amount": amount, "note": note,
        }
        cash_ledger.save_cash_ledger(universe, entries + [new_entry])
        success = f"Recorded {entry_type} of Rs {amount:,.2f}."

    ctx = _build_context(universe, error=error, warning=warning, success=success)
    return render_template("_cash_ledger_content.html", **ctx)


@bp.route("/cash/edit/<entry_id>", methods=["POST"])
def edit(entry_id):
    universe = _universe()

    if settings.READ_ONLY:
        ctx = _build_context(universe, error="This webapp is read-only right now "
                              "— editing cash entries stays in the Streamlit dashboard for now.")
        return render_template("_cash_ledger_content.html", **ctx)

    entries = cash_ledger.load_cash_ledger(universe)
    idx = next((i for i, e in enumerate(entries) if e["id"] == entry_id), None)

    error, success = None, None
    if idx is None:
        error = "Entry not found — it may have already been deleted."
    else:
        entry_type = request.form.get("type", entries[idx]["type"])
        date_str = request.form.get("date") or entries[idx]["date"]
        note = (request.form.get("note") or "").strip()
        try:
            amount = float(request.form.get("amount", 0))
        except ValueError:
            amount = 0.0

        if entry_type not in cash_ledger.CASH_ENTRY_TYPES:
            error = f"Unknown entry type: {entry_type}"
        elif amount <= 0:
            error = "Amount must be greater than zero. Entry not updated."
        else:
            candidate = [e.copy() for e in entries]
            candidate[idx].update({"type": entry_type, "date": date_str, "amount": amount, "note": note})
            cash_ledger.save_cash_ledger(universe, candidate)
            success = "Cash entry updated."

    ctx = _build_context(universe, error=error, success=success)
    return render_template("_cash_ledger_content.html", **ctx)


@bp.route("/cash/delete", methods=["POST"])
def delete():
    universe = _universe()

    if settings.READ_ONLY:
        ctx = _build_context(universe, error="This webapp is read-only right now "
                              "— deleting cash entries stays in the Streamlit dashboard for now.")
        return render_template("_cash_ledger_content.html", **ctx)

    ids_to_delete = set(request.form.getlist("entry_id"))
    entries = cash_ledger.load_cash_ledger(universe)

    error, success = None, None
    if not ids_to_delete:
        error = "No entries selected."
    else:
        candidate = [e for e in entries if e["id"] not in ids_to_delete]
        cash_ledger.save_cash_ledger(universe, candidate)
        success = f"Deleted {len(ids_to_delete)} cash entr{'y' if len(ids_to_delete) == 1 else 'ies'}."

    ctx = _build_context(universe, error=error, success=success)
    return render_template("_cash_ledger_content.html", **ctx)
