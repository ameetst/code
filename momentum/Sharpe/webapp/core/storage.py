"""
Single-writer JSON storage. Ported from safe_write_json in sharpe_dashboard_dhan.py,
with an in-process lock added so concurrent requests can't interleave writes.

Not used by any route yet — the write tabs (Tradelog, Cash Ledger, Config) will
call it. Until then the app is read-only (see settings.READ_ONLY).
"""
import json
import shutil
import threading
from functools import wraps
from pathlib import Path

from flask import abort

from webapp import settings

_write_lock = threading.Lock()


def safe_write_json(path, data) -> None:
    """Atomic JSON write: write to .tmp, back up the existing file to .bak, rename .tmp → target."""
    path = Path(path)
    tmp_path = path.with_suffix(".tmp")
    bak_path = path.with_suffix(".bak")
    with _write_lock:
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            if path.exists():
                shutil.copy2(path, bak_path)
            shutil.move(str(tmp_path), str(path))
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise


def require_writable(view):
    """Route decorator for write endpoints: 403s while settings.READ_ONLY is set."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if settings.READ_ONLY:
            abort(403, description="webapp is read-only (SHARPE_READ_ONLY=1). "
                                    "Writes are disabled until cutover.")
        return view(*args, **kwargs)
    return wrapped
