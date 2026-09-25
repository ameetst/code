"""
Smoke + safety tests against the live data files (read-only).

The first request pays the ~11s ranking compute; the module-scoped client
fixture shares one cached bundle across all tests.
"""
import pytest

from webapp import settings
from webapp.app import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_health_reports_read_only(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["read_only"] is True


def test_config_lists_universe(client):
    body = client.get("/api/config").get_json()
    assert body["file"] in body["files"]
    assert body["universe"] and body["max_n"] >= body["min_n"]


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Sharpe Momentum Strategy" in r.data
    assert b'hx-get="/rankings/table"' in r.data


def test_rankings_table_partial_shape_and_order(client):
    import re
    r = client.get("/rankings/table")
    assert r.status_code == 200
    html = r.data.decode()
    assert html.count('<tr class="held-row">') + html.count('<tr class="">') >= 1
    # Ranks render 1..N with no gaps, in document order.
    rows = re.findall(r"<tr class=\"[^\"]*\">\s*<td>(\d+)</td>", html)
    assert [int(x) for x in rows] == list(range(1, len(rows) + 1))


def test_rankings_matches_library_output(client):
    """The page must show exactly what momentum_lib ranks — no re-derivation drift."""
    from webapp.core import config_store, rankings
    bundle = rankings.get_bundle(config_store.load_config())
    expected = list(bundle.result.head(5).index)
    html = client.get("/rankings/table?limit=5").data.decode()
    for ticker in expected:
        assert f"<td>{ticker}</td>" in html


def test_held_rows_match_positions_ledger(client):
    from webapp.core import config_store, rankings
    cfg = config_store.load_config()
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    held = rankings.held_tickers(universe)
    html = client.get("/rankings/table?limit=200").data.decode()
    import re
    for ticker, row_html in re.findall(r'<tr class="([^"]*)">\s*<td>\d+</td>\s*<td>(\w+)</td>', html):
        assert ("held-row" in ticker) == (row_html in held)


def test_requests_do_not_modify_data_files(client):
    """Unlike the Streamlit page, loading rankings must not write regime history or ledgers."""
    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}
    client.get("/rankings/table")
    client.get("/api/config")
    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after


def test_write_routes_are_blocked(client):
    """No write routes exist yet; once they do, they must 403 under READ_ONLY. Placeholder guard."""
    assert settings.READ_ONLY is True


def test_full_rankings_page_and_table(client):
    import re
    r = client.get("/rankings/full")
    assert r.status_code == 200
    assert b'hx-get="/rankings/full/table' in r.data

    r = client.get("/rankings/full/table?eligibility=All&sort_col=RANK&top_n=20")
    assert r.status_code == 200
    html = r.data.decode()
    assert html.count("<tr") - 1 == 20  # header row + 20 body rows
    assert "Universe:" in html and "Regime N (green rows):" in html
    # eligibility filter actually filters
    dq_html = client.get("/rankings/full/table?eligibility=Disqualified only&top_n=500").data.decode()
    dq_ranks = re.findall(r"<td>(\d+)</td>", dq_html)
    assert dq_ranks == []  # disqualified names never get a RANK (master eligibility gate excludes them)


def test_full_rankings_row_classes_are_mutually_exclusive(client):
    import re
    html = client.get("/rankings/full/table?top_n=200").data.decode()
    classes = re.findall(r'<tr class="([^"]*)">', html)
    assert classes  # sanity: the universe isn't empty
    assert all(c.count(" ") == 0 for c in classes)  # exactly zero or one class, never combined


def test_actions_page_renders_and_matches_ledger(client):
    from webapp.core import actions, config_store

    cfg = config_store.load_config()
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    ledger = actions.load_ledger(universe)
    assert ledger  # sanity: N750_positions_ledger.json has open positions

    r = client.get("/actions")
    assert r.status_code == 200
    html = r.data.decode()
    assert "Actions Monitor" in html
    assert "All Positions" in html
    # every ledger ticker appears exactly once in the All Positions table
    for ticker in ledger:
        assert html.count(f"<td>{ticker}</td>") == 1


def test_actions_row_classes_are_mutually_exclusive_and_valid(client):
    import re
    html = client.get("/actions").data.decode()
    valid = {"row-sell-immediate", "row-sell", "row-locked", "row-healthy", "row-regime", ""}
    classes = re.findall(r'<tr class="([^"]*)">', html)
    assert classes
    for c in classes:
        assert c.count(" ") == 0
        assert c in valid


def test_actions_summary_counts_add_up(client):
    from webapp.core import actions, config_store, rankings, tradelog
    import datetime

    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    ledger = actions.load_ledger(universe)
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, bundle.latest_prices)
    evaluation = actions.evaluate_exits(bundle, cfg, ledger, pnl["holdings_metrics"], datetime.date.today())

    assert len(evaluation["rows"]) == len(ledger)
    triggers = {r["trigger"] for r in evaluation["rows"]}
    assert triggers <= {"52H_BREACH", "CIRCUIT_BREACH", "SERIES_BREACH", "ADTV_BREACH",
                         "FILTER_BREACH", "REL_DD_BREACH", "REL_DD_LOCK", "RANK_EXIT",
                         "HOLD_LOCK", "HEALTHY"}
    # exit_triggers is exactly the BREACH/RANK_EXIT subset (mirrors the dashboard's regex filter)
    expected_exit_tickers = {r["ticker"] for r in evaluation["rows"]
                              if "BREACH" in r["trigger"] or "RANK_EXIT" in r["trigger"]}
    assert {r["ticker"] for r in evaluation["exit_triggers"]} == expected_exit_tickers


def test_actions_requests_do_not_modify_data_files(client):
    watched = list(settings.DATA_DIR.glob("*.json")) + list(settings.DATA_DIR.glob("*.bak"))
    before = {p: p.stat().st_mtime_ns for p in watched}
    client.get("/actions")
    after = {p: p.stat().st_mtime_ns for p in watched}
    assert before == after


def test_nav_actions_dot_reflects_exit_state(client):
    """The 🔴/🟢 nav indicator on every page must match the Actions page's own n_exits."""
    from webapp.core import actions, config_store, rankings, tradelog
    import datetime

    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    universe = config_store.universe_name(config_store.resolve_file(cfg))
    ledger = actions.load_ledger(universe)
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, bundle.latest_prices)
    n_exits = actions.evaluate_exits(bundle, cfg, ledger, pnl["holdings_metrics"],
                                      datetime.date.today())["n_exits"]
    expected_dot = "\U0001f534" if n_exits > 0 else "\U0001f7e2"

    html = client.get("/").data.decode()
    assert f'{expected_dot} Actions Monitor' in html


def test_performance_page_renders_chart_data(client):
    r = client.get("/performance")
    assert r.status_code == 200
    html = r.data.decode()
    assert b"equity-chart-data" in r.data
    assert "initEquityChart(" in html
    # the embedded JSON must be valid and non-empty when equity history exists
    import json
    import re
    m = re.search(r'<script id="equity-chart-data"[^>]*>(.*?)</script>', html, re.S)
    assert m
    records = json.loads(m.group(1))
    assert len(records) >= 2
    assert set(records[0]) == {"date", "port", "bench", "n_held", "invested", "method"}
