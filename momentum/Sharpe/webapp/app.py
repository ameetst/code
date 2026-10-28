"""
Flask app factory.

Dev:   .venv\\Scripts\\python -m webapp.app          (from momentum/Sharpe/, debug reloader on)
Prod:  webapp\\run_webapp.bat                         (waitress, no reloader)
"""
import datetime
import threading

from flask import Flask
from markupsafe import escape

from webapp import settings
from webapp.blueprints import actions as actions_bp
from webapp.blueprints import cash_ledger as cash_ledger_bp
from webapp.blueprints import config as config_bp
from webapp.blueprints import configuration as configuration_bp
from webapp.blueprints import performance as performance_bp
from webapp.blueprints import rankings as rankings_bp
from webapp.blueprints import tradelog as tradelog_bp
from webapp.core import actions, config_store, rankings, regime, tradelog
from momentum_lib import PriceDataError


def _warm_cache() -> None:
    """Pay the ~11s ranking compute at startup instead of on the first page load."""
    try:
        cfg = config_store.load_config()
        bundle = rankings.get_bundle(cfg)
        if not settings.READ_ONLY:  # write-enabled only: a server left running still logs the day
            regime.record_today(config_store.universe_name(config_store.resolve_file(cfg)),
                                bundle.regime_score, bundle.regime_detail)
    except Exception as e:  # never block startup; the page will surface the real error
        print(f"[warmup] skipped: {e}")


def _actions_status() -> tuple[str, str | None]:
    """(nav dot, error). 🔴/🟢 is the same signal as the Streamlit tab label. Bundle is
    cached, so this costs one small JSON read plus a short Python loop on every render.
    If the exit check itself fails the dot is a grey ⚪ plus the error text, never a
    silent 🟢 -- "no exits" must not be what a broken calculation looks like."""
    try:
        cfg = config_store.load_config()
        bundle = rankings.get_bundle(cfg)
        universe = config_store.universe_name(config_store.resolve_file(cfg))
        ledger = actions.load_ledger(universe)
        if not ledger:
            return "🟢", None
        tl = tradelog.load_tradelog(universe)
        pnl = tradelog.calculate_holdings_and_pnl(tl, bundle.latest_prices)
        n_exits = actions.evaluate_exits(bundle, cfg, ledger, pnl["holdings_metrics"],
                                          datetime.date.today())["n_exits"]
        return ("🔴" if n_exits > 0 else "🟢"), None
    except Exception as e:
        print(f"[actions-dot] exit check failed: {e!r}")
        return "⚪", f"{type(e).__name__}: {e}"


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

    @app.errorhandler(PriceDataError)
    def price_data_error(e):
        # Standalone page (not base.html): the shell itself needs the same price data.
        return (f"<!doctype html><meta charset='utf-8'><title>Price data problem</title>"
                f"<body style='font-family:sans-serif;max-width:640px;margin:48px auto'>"
                f"<h2>⚠️ Price data problem</h2><p>{escape(str(e))}</p>"
                f"<p>Nothing was computed from this file. Re-run the price update, "
                f"then reload.</p></body>", 503)

    @app.context_processor
    def inject_globals():
        # Nav label and footer download mirror the Streamlit dashboards' tab label
        # ("Top {MAX_N} Rankings") and bottom "Actions" section.
        max_n, rankings_xlsx, price_date = None, None, None
        try:
            cfg = config_store.load_config()
            max_n = int(cfg["max_n"])
            universe = config_store.universe_name(config_store.resolve_file(cfg))
            path = config_store.rankings_xlsx_path(universe)
            rankings_xlsx = path.name if path else None
            last = rankings.get_bundle(cfg).dates[-1]
            if isinstance(last, str):
                last = datetime.date.fromisoformat(last)
            price_date = f"{last.day} {rankings.MONTHS[last.month - 1]}"  # top-bar status pill
        except Exception:
            pass
        dot, dot_error = _actions_status()
        return {"read_only": settings.READ_ONLY, "actions_dot": dot, "actions_dot_error": dot_error,
                "nav_max_n": max_n, "rankings_xlsx": rankings_xlsx,
                "price_date": price_date}

    threading.Thread(target=_warm_cache, daemon=True).start()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
