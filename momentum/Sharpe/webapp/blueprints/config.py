from flask import Blueprint, jsonify

from webapp import settings
from webapp.core import config_store

bp = Blueprint("config", __name__)


@bp.route("/api/health")
def health():
    return jsonify(status="ok", read_only=settings.READ_ONLY, data_dir=str(settings.DATA_DIR))


@bp.route("/api/config")
def get_config():
    cfg = config_store.load_config()
    file = config_store.resolve_file(cfg)
    return jsonify({**cfg, "file": file, "files": config_store.available_files(),
                     "universe": config_store.universe_name(file)})
