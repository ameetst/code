from flask import Blueprint, render_template

from webapp.core import config_store, performance

bp = Blueprint("performance", __name__)


@bp.route("/performance")
def index():
    cfg = config_store.load_config()
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    equity_data = performance.load_equity_history(universe)
    view = performance.build_view(equity_data)
    return render_template(
        "performance.html",
        universe=universe,
        view=view,
        single_day=(len(equity_data) == 1),
        single_day_date=equity_data[0]["date"] if equity_data else None,
    )
