"""
Configuration. Ported from sharpe_dashboard_dhan.py's tab_config block.

Routed at /config-page (not /config) to keep clear of the existing JSON API at
/api/config (blueprints/config.py) -- different purpose, same word.
"""
import datetime

from flask import Blueprint, render_template, request

from webapp import settings
from webapp.core import config_store, configuration, rankings

bp = Blueprint("configuration", __name__)


def _build_context(cfg: dict = None, *, error=None, success=None) -> dict:
    cfg = cfg or config_store.load_config()
    files = config_store.available_files()
    universe = config_store.universe_name(config_store.resolve_file(cfg))

    bundle = None
    try:
        bundle = rankings.get_bundle(cfg)
    except FileNotFoundError:
        pass

    adtv = configuration.adtv_pass_counts(bundle, float(cfg["min_turnover"])) if bundle else None
    circuit_exceed = (configuration.circuit_hit_exceed_count(bundle, int(cfg["circuit_threshold"]))
                       if bundle and cfg["circuit_filter_enabled"] else None)

    cfg_path = configuration.config_path()
    last_saved = (datetime.datetime.fromtimestamp(cfg_path.stat().st_mtime).strftime("%d-%b-%Y %H:%M")
                  if cfg_path.exists() else None)

    return {
        "cfg": cfg, "files": files, "universe": universe,
        "adtv": adtv, "circuit_exceed": circuit_exceed,
        "params": configuration.strategy_params(cfg, universe),
        "last_saved": last_saved,
        "error": error, "success": success,
    }


@bp.route("/config-page")
def index():
    return render_template("configuration.html", **_build_context())


@bp.route("/config-page/save", methods=["POST"])
def save():
    cfg = config_store.load_config()

    if settings.READ_ONLY:
        ctx = _build_context(cfg, error="This webapp is read-only right now — "
                              "configuration changes stay in the Streamlit dashboard for now.")
        return render_template("_configuration_content.html", **ctx)

    form = request.form

    def get_float(key, default):
        try:
            return float(form.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_int(key, default):
        try:
            return int(float(form.get(key, default)))
        except (TypeError, ValueError):
            return default

    files = config_store.available_files()
    selected_file = form.get("file", cfg["file"])

    new_cfg = dict(cfg)
    new_cfg["file"] = selected_file if selected_file in files else cfg["file"]
    new_cfg["capital"] = configuration.clamp("capital", get_float("capital", cfg["capital"]))
    new_cfg["min_n"] = int(configuration.clamp("min_n", get_int("min_n", cfg["min_n"])))
    new_cfg["max_n"] = int(configuration.clamp("max_n", get_int("max_n", cfg["max_n"])))
    if new_cfg["min_n"] > new_cfg["max_n"]:  # keep the pair sane regardless of what was posted
        new_cfg["min_n"], new_cfg["max_n"] = new_cfg["max_n"], new_cfg["min_n"]
    new_cfg["min_turnover"] = configuration.clamp("min_turnover", get_float("min_turnover", cfg["min_turnover"]))
    new_cfg["eq_series_filter"] = form.get("eq_series_filter") == "on"
    new_cfg["circuit_filter_enabled"] = form.get("circuit_filter_enabled") == "on"
    new_cfg["circuit_threshold"] = int(configuration.clamp("circuit_threshold",
                                        get_int("circuit_threshold", cfg["circuit_threshold"])))
    new_cfg["rel_dd_breach_threshold"] = int(configuration.clamp("rel_dd_breach_threshold",
                                              get_int("rel_dd_breach_threshold", cfg["rel_dd_breach_threshold"])))
    new_cfg["hold_rank_buffer"] = int(configuration.clamp("hold_rank_buffer",
                                       get_int("hold_rank_buffer", cfg["hold_rank_buffer"])))

    configuration.save(new_cfg)
    ctx = _build_context(new_cfg, success="Configuration saved.")
    return render_template("_configuration_content.html", **ctx)
