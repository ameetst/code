"""
Things that matter once the webapp is used for real (write mode): the daily regime
record, the Actions dot failing visibly, and the write-mode banner.
"""
import datetime
import json

import pytest

from webapp import settings
from webapp.core import regime


DETAIL = {"breadth_score": 0.6345, "momentum_score": 0.4567, "dynamic_n": 16}


@pytest.fixture
def hist_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(regime, "DATA_DIR", tmp_path)
    return tmp_path


def _read(d):
    return json.loads((d / "TESTU_regime_history.json").read_text())


# ── record_today ──────────────────────────────────────────────────────────────

def test_record_today_creates_the_file_with_one_rounded_entry(hist_dir):
    assert regime.record_today("TESTU", 0.412345, DETAIL, today=datetime.date(2026, 10, 19)) is True
    assert _read(hist_dir) == [{"date": "2026-10-19", "Composite": 0.4123, "Breadth": 0.6345,
                                "Momentum": 0.4567, "Dynamic N": 16}]


def test_record_today_is_idempotent_and_skips_identical_rewrites(hist_dir):
    day = datetime.date(2026, 10, 19)
    regime.record_today("TESTU", 0.41, DETAIL, today=day)
    path = hist_dir / "TESTU_regime_history.json"
    before = path.stat().st_mtime_ns
    assert regime.record_today("TESTU", 0.41, DETAIL, today=day) is False
    assert path.stat().st_mtime_ns == before                       # no rewrite
    assert len(_read(hist_dir)) == 1


def test_record_today_overwrites_same_day_and_appends_new_days(hist_dir):
    regime.record_today("TESTU", 0.41, DETAIL, today=datetime.date(2026, 10, 19))
    assert regime.record_today("TESTU", 0.45, DETAIL, today=datetime.date(2026, 10, 19)) is True
    assert [(h["date"], h["Composite"]) for h in _read(hist_dir)] == [("2026-10-19", 0.45)]
    regime.record_today("TESTU", 0.5, DETAIL, today=datetime.date(2026, 10, 20))
    assert [h["date"] for h in _read(hist_dir)] == ["2026-10-19", "2026-10-20"]


def test_record_today_never_overwrites_a_file_it_cannot_parse(hist_dir):
    path = hist_dir / "TESTU_regime_history.json"
    path.write_text("{ this is not json")
    assert regime.record_today("TESTU", 0.41, DETAIL, today=datetime.date(2026, 10, 19)) is False
    assert path.read_text() == "{ this is not json"


# ── routes ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from webapp.app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_top_rankings_records_today_only_when_write_enabled(client, hist_dir, monkeypatch):
    from webapp.core import config_store
    universe = config_store.universe_name(config_store.resolve_file(config_store.load_config()))
    path = hist_dir / f"{universe}_regime_history.json"

    monkeypatch.setattr(settings, "READ_ONLY", True)
    assert client.get("/").status_code == 200
    assert not path.exists()                                       # read-only: never writes

    monkeypatch.setattr(settings, "READ_ONLY", False)
    assert client.get("/").status_code == 200
    entries = json.loads(path.read_text())
    assert [e["date"] for e in entries] == [datetime.date.today().isoformat()]
    mtime = path.stat().st_mtime_ns
    client.get("/")                                                # second load: nothing changes
    assert path.stat().st_mtime_ns == mtime


def test_write_mode_banner_replaces_the_read_only_banner(client, monkeypatch):
    monkeypatch.setattr(settings, "READ_ONLY", True)
    html = client.get("/performance").data.decode()
    assert "Read-only preview" in html and "Write mode" not in html
    monkeypatch.setattr(settings, "READ_ONLY", False)
    html = client.get("/performance").data.decode()
    assert "Write mode" in html and "Read-only preview" not in html


# ── Actions dot ───────────────────────────────────────────────────────────────

def test_actions_dot_is_grey_with_the_error_when_the_exit_check_fails(client, monkeypatch):
    from webapp import app as app_module
    from webapp.core import actions

    def boom(*a, **k):
        raise RuntimeError("exit check exploded")

    monkeypatch.setattr(actions, "evaluate_exits", boom)
    dot, err = app_module._actions_status()
    assert dot == "⚪" and "exit check exploded" in err        # never a silent green

    html = client.get("/").data.decode()
    assert "⚪ Actions Monitor" in html
    assert 'title="Exit status unavailable: RuntimeError: exit check exploded"' in html
    assert "\U0001f7e2 Actions Monitor" not in html


# ── Refresh Rankings button (Configuration tab only) ─────────────────────────

@pytest.fixture(scope="module")
def app_client():
    from webapp.app import create_app
    return create_app().test_client()


def test_refresh_button_is_on_the_configuration_tab_only(app_client):
    assert "Refresh Rankings" in app_client.get("/config-page").get_data(as_text=True)
    for path in ("/", "/rankings/full", "/actions", "/tradelog", "/cash", "/performance"):
        assert "Refresh Rankings" not in app_client.get(path).get_data(as_text=True), path


def test_refresh_rankings_recomputes_and_clears_caches_without_writing(app_client):
    from webapp.core import config_store, dhan_client, rankings
    cfg = config_store.load_config()
    before = rankings.get_bundle(cfg)
    dhan_client._vix_cache["value"] = (1.0, 1.0, 1.0)
    resp = app_client.post("/config-page/refresh-rankings")
    assert resp.status_code == 200
    assert "Rankings refreshed" in resp.get_data(as_text=True)
    assert "value" not in dhan_client._vix_cache or dhan_client._vix_cache["value"] != (1.0, 1.0, 1.0)
    assert rankings.get_bundle(cfg) is not before  # recomputed, not served from the old cache
