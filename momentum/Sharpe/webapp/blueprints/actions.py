import datetime

from flask import Blueprint, abort, render_template

import momentum_lib as ml

from webapp.core import actions, config_store, rankings, tradelog

bp = Blueprint("actions", __name__)


def _entries_section(bundle, cfg, ledger, holdings_metrics, exit_triggers,
                      dynamic_n: int, allow_new: bool, n_current_holdings: int, n_exits: int):
    n_new_positions = dynamic_n - (n_current_holdings - n_exits)
    if n_new_positions > 0 and allow_new:
        exit_tickers = {r["ticker"] for r in exit_triggers}
        section = actions.entry_candidates(bundle, cfg, ledger, holdings_metrics, exit_tickers, n_new_positions)
    elif not allow_new:
        section = {"status": "regime_blocked"}
    else:
        section = None  # portfolio full, regime otherwise open -- nothing to show, matches the dashboard
    return n_new_positions, section


@bp.route("/actions")
def index():
    cfg = config_store.load_config()
    try:
        bundle = rankings.get_bundle(cfg)
    except FileNotFoundError as e:
        abort(404, description=str(e))

    universe = config_store.universe_name(config_store.resolve_file(cfg))
    ledger = actions.load_ledger(universe)
    view = {"has_ledger": bool(ledger)}

    if ledger:
        tl = tradelog.load_tradelog(universe)
        pnl = tradelog.calculate_holdings_and_pnl(tl, bundle.latest_prices)
        holdings_metrics = pnl["holdings_metrics"]

        evaluation = actions.evaluate_exits(bundle, cfg, ledger, holdings_metrics, datetime.date.today())
        exit_rows, exit_triggers, n_exits = evaluation["rows"], evaluation["exit_triggers"], evaluation["n_exits"]

        detail = bundle.regime_detail
        dynamic_n, allow_new = int(detail["dynamic_n"]), bool(detail["allow_new"])
        n_current_holdings = len(ledger)
        n_new_positions, entries_section = _entries_section(
            bundle, cfg, ledger, holdings_metrics, exit_triggers,
            dynamic_n, allow_new, n_current_holdings, n_exits)

        view.update({
            "n_exits": n_exits, "exit_triggers": exit_triggers, "exit_rows": exit_rows,
            "dynamic_n": dynamic_n, "allow_new": allow_new,
            "regime_score": bundle.regime_score, "entry_threshold": ml.DEFAULT_NEW_ENTRY_THRESHOLD,
            "n_current_holdings": n_current_holdings, "n_new_positions": n_new_positions,
            "entries_section": entries_section,
            "summary": {
                "positions": len(exit_rows),
                "n_52h_exits": sum(1 for r in exit_rows if r["trigger"] == "52H_BREACH"),
                "n_reldd_exits": sum(1 for r in exit_rows if r["trigger"] == "REL_DD_BREACH"),
                "n_rank_exits": sum(1 for r in exit_rows if r["trigger"] == "RANK_EXIT"),
                "n_hold_locked": sum(1 for r in exit_rows if r["trigger"] in ("HOLD_LOCK", "REL_DD_LOCK")),
                "n_healthy": sum(1 for r in exit_rows if r["trigger"] == "HEALTHY"),
            },
        })

    return render_template("actions.html", view=view)
