"""
Flask app factory.

Dev:   .venv\\Scripts\\python -m webapp.app          (from momentum/Sharpe/, debug reloader on)
Prod:  webapp\\run_webapp.bat                         (waitress, no reloader)
"""
import datetime
import threading

from flask import Flask

from webapp import settings
from webapp.blueprints import actions as actions_bp
from webapp.blueprints import cash_ledger as cash_ledger_bp
from webapp.blueprints import config as config_bp
from webapp.blueprints import configuration as configuration_bp
from webapp.blueprints import performance as performance_bp
from webapp.blueprints import rankings as rankings_bp
from webapp.blueprints import tradelog as tradelog_bp
from webapp.core import actions, config_store, rankings, tradelog


def _warm_cache() -> None:
    """Pay the ~11s ranking compute at startup instead of on the first page load."""
    try:
        rankings.get_bundle(config_store.load_config())
    except Exception as e:  # never block startup; the page will surface the real error
        print(f"[warmup] skipped: {e}")


def _actions_dot() -> str:
    """🔴/🟢 nav indicator, same signal as the Streamlit tab label. Bundle is cached, so
    this costs one small JSON read plus a short Python loop on every page render."""
    try:
        cfg = config_store.load_config()
        bundle = rankings.get_bundle(cfg)
        universe = config_store.universe_name(config_store.resolve_file(cfg))
        ledger = actions.load_ledger(universe)
        if not ledger:
            return "\U0001f7e2"
        tl = tradelog.load_tradelog(universe)
        pnl = tradelog.calculate_holdings_and_pnl(tl, bundle.latest_prices)
        n_exits = actions.evaluate_exits(bundle, cfg, ledger, pnl["holdings_metrics"],
                                          datetime.date.today())["n_exits"]
        return "\U0001f534" if n_exits > 0 else "\U0001f7e2"
    except Exception:
        return "\U0001f7e2"


def create_app() -> Flask:
    app = Flask(__name__)
    # Checks each template's mtime on every render regardless of debug mode -- negligible
    # cost, and without it Jinja caches compiled templates forever under waitress (no
    # debug reloader), so an edited .html silently keeps serving the old version until
    # the process restarts. Bit us twice during development before this was added.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.register_blueprint(rankings_bp.bp)
    app.register_blueprint(config_bp.bp)
    app.register_blueprint(performance_bp.bp)
    app.register_blueprint(actions_bp.bp)
    app.register_blueprint(tradelog_bp.bp)
    app.register_blueprint(cash_ledger_bp.bp)
    app.register_blueprint(configuration_bp.bp)

    @app.context_processor
    def inject_globals():
        return {"read_only": settings.READ_ONLY, "actions_dot": _actions_dot()}

    threading.Thread(target=_warm_cache, daemon=True).start()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
