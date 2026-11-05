from flask import Blueprint, abort, render_template

import corr_matrix

from webapp.core import actions, config_store, rankings

bp = Blueprint("correlation", __name__)


@bp.route("/correlation")
def index():
    """Co-relation Matrix of the current holdings. Same shared module as the Streamlit
    dashboards (Sharpe/corr_matrix.py), so the numbers and colours are identical."""
    cfg = config_store.load_config()
    try:
        bundle = rankings.get_bundle(cfg)
    except FileNotFoundError as e:
        abort(404, description=str(e))
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    ledger = actions.load_ledger(universe)
    matrix_html, message = corr_matrix.render(bundle.prices_df, list(ledger.keys()))
    return render_template("correlation.html", matrix_html=matrix_html, message=message,
                           corr_css=corr_matrix.CSS)
