"""
"Blocked from purchase" under the current Configuration: the shared helper
(momentum_lib.compute_purchase_blocks) plus how the webapp uses it -- Top-N hides blocked
names, Full Rankings greys them out with a reason, entry candidates skip them.
"""
import numpy as np
import pandas as pd
import pytest

import momentum_lib as ml
from webapp.core import actions, config_store, rankings


def _stockdb(tmp_path, rows):
    path = tmp_path / "STOCKDB.csv"
    pd.DataFrame(rows, columns=["SYMBOL", "SERIES", "BAND", "MARKETCAP", "MARKETCAP_CR"]).to_csv(path, index=False)
    return str(path)


def _universe():
    tickers = ["OK", "CHEAP", "SMALL", "NOCAP", "ZEROCAP", "ILLIQ", "BEE", "CIRC"]
    prices = pd.DataFrame(
        {"d1": [200, 63, 200, 200, 200, 200, 200, 200], "d2": [210, 64, 210, 210, 210, 210, 210, np.nan]},
        index=tickers)                                    # CIRC's last close is NaN -> falls back to d1
    result = pd.DataFrame({
        "RANK": range(1, len(tickers) + 1),
        "TURNOVER_12M": [5, 5, 5, 5, 5, 0.2, 5, 5], "TURNOVER_6M": [5, 5, 5, 5, 5, 0.3, 5, 5],
        "SERIES": ["EQ", "EQ", "EQ", "EQ", "EQ", "EQ", "BE", "EQ"],
        "TOTAL_CIRCUIT_HITS": [0, 0, 0, 0, 0, 0, 0, 30],
    }, index=tickers)
    return tickers, prices, result


def test_compute_purchase_blocks_reasons(tmp_path):
    _, prices, result = _universe()
    stockdb = _stockdb(tmp_path, [
        ["OK", "EQ", 20, "X", 5_000 * 1e7], ["CHEAP", "EQ", 20, "X", 5_000 * 1e7],
        ["SMALL", "EQ", 20, "X", 300 * 1e7],   # Rs 300 Cr
        ["ZEROCAP", "EQ", 20, "X", 0],         # 0 == "not populated" -> passes
        ["ILLIQ", "EQ", 20, "X", 5_000 * 1e7], ["BEE", "EQ", 20, "X", 5_000 * 1e7],
        ["CIRC", "EQ", 20, "X", 5_000 * 1e7],  # NOCAP deliberately absent from the file
    ])
    blocks = ml.compute_purchase_blocks(
        result, prices, min_turnover_cr=1.0, min_cmp=100, min_market_cap=1000,
        eq_series_filter=True, circuit_filter_enabled=True, circuit_threshold=20,
        stockdb_csv_path=stockdb)

    assert blocks["OK"] == ""
    assert blocks["CHEAP"] == "Min CMP: Rs 64.00 < Rs 100"
    assert blocks["SMALL"] == "Min Market Cap: Rs 300 Cr < Rs 1,000 Cr"
    assert blocks["NOCAP"] == "Min Market Cap: not in STOCKDB.csv"
    assert blocks["ZEROCAP"] == ""                               # exactly 0 is "unknown", not a fail
    assert blocks["ILLIQ"] == "MDTV: Rs 0.30 Cr < Rs 1.00 Cr"
    assert blocks["BEE"] == "Series BE (EQ only)"
    assert blocks["CIRC"] == "Circuit hits 30 >= 20"


def test_compute_purchase_blocks_disabled_filters_block_nothing(tmp_path):
    _, prices, result = _universe()
    blocks = ml.compute_purchase_blocks(
        result.drop(columns=["TURNOVER_12M", "TURNOVER_6M", "TOTAL_CIRCUIT_HITS"]), prices,
        min_cmp=0, min_market_cap=0, eq_series_filter=False, circuit_filter_enabled=False,
        stockdb_csv_path=str(tmp_path / "missing.csv"))
    assert (blocks == "").all()


def test_multiple_reasons_are_joined(tmp_path):
    _, prices, result = _universe()
    stockdb = _stockdb(tmp_path, [["CHEAP", "EQ", 20, "X", 100 * 1e7]])
    blocks = ml.compute_purchase_blocks(result, prices, min_cmp=100, min_market_cap=1000,
                                        stockdb_csv_path=stockdb)
    assert blocks["CHEAP"] == "Min CMP: Rs 64.00 < Rs 100; Min Market Cap: Rs 100 Cr < Rs 1,000 Cr"


# ── webapp behaviour on a synthetic bundle ────────────────────────────────────

def _bundle(blocks):
    cols = {c: 1.0 for c in rankings.FULL_DISPLAY_COLUMNS if c not in ("RANK", "SERIES")}
    result = pd.DataFrame({"RANK": [1.0, 2.0, 3.0, 4.0, np.nan], "SERIES": "EQ", **cols},
                          index=["A", "B", "C", "D", "E"])
    return rankings.Bundle(prices_df=None, result=result, regime_score=0.5,
                           regime_detail={"dynamic_n": 3}, dates=[], latest_prices={},
                           purchase_block=pd.Series(blocks, index=result.index, dtype=object))


def test_top_n_hides_blocked_names_and_keeps_original_rank_numbers():
    bundle = _bundle(["", "Min CMP: Rs 63.00 < Rs 100", "", "", "MDTV: no turnover data"])
    visible, hidden = rankings._visible_and_hidden(bundle, 2)
    assert visible == ["A", "C"]                      # B hidden, list still filled to the limit
    assert hidden == [("B", "Min CMP: Rs 63.00 < Rs 100")]   # E is unranked and beyond the cut

    shown = rankings.top_hidden(bundle, 2, held={"B"})
    assert shown == [{"ticker": "B", "reason": "Min CMP: Rs 63.00 < Rs 100", "held": True}]


def test_top_n_without_blocks_is_unchanged():
    bundle = _bundle([""] * 5)
    assert rankings._visible_and_hidden(bundle, 3) == (["A", "B", "C"], [])
    bundle = rankings.Bundle(prices_df=None, result=bundle.result, regime_score=0.5,
                             regime_detail={"dynamic_n": 3}, dates=[], latest_prices={})
    assert rankings._visible_and_hidden(bundle, 3)[0] == ["A", "B", "C"]   # purchase_block=None


def test_full_rankings_greys_out_blocked_rows_with_reason():
    bundle = _bundle(["", "Min CMP: Rs 63.00 < Rs 100", "", "", "MDTV: no turnover data"])
    rows = rankings.full_rankings_rows(bundle, "All", "RANK", 10, -20)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["B"]["row_class"] == "row-blocked"
    assert by_ticker["B"]["blocked"] == "Min CMP: Rs 63.00 < Rs 100"
    assert by_ticker["E"]["row_class"] == "row-blocked"       # unranked but config-blocked
    assert by_ticker["A"]["row_class"] == "row-regime" and by_ticker["A"]["blocked"] == ""

    summary = rankings.blocked_breakdown(rows)
    assert summary == {"n_blocked": 2, "n_shown": 5, "by_reason": [("Min CMP", 1), ("MDTV", 1)]}


# ── live data ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from webapp.app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


def test_live_cmp_and_mcap_blocks_agree_with_the_ranking_columns():
    """The helper re-derives Min CMP / Min Market Cap; it must match what
    compute_universe_rankings put in CMP_ELIGIBLE / MCAP_ELIGIBLE for the same config."""
    bundle = rankings.get_bundle(config_store.load_config())
    r, b = bundle.result, bundle.purchase_block
    cmp_blocked = ~r["CMP_ELIGIBLE"].astype(bool)
    mcap_blocked = ~r["MCAP_ELIGIBLE"].astype(bool)
    assert (b.str.contains("Min CMP") == cmp_blocked).all()
    assert (b.str.contains("Min Market Cap") == mcap_blocked).all()


def test_live_top_table_hides_blocked_names_and_explains_them(client):
    cfg = config_store.load_config()
    bundle = rankings.get_bundle(cfg)
    max_n = int(cfg["max_n"])
    _, hidden = rankings._visible_and_hidden(bundle, max_n)
    html = client.get("/rankings/table").data.decode()    # the partial the index page loads
    top_table = html[html.index("<table"):html.index("</table>")]
    for ticker, _ in hidden:
        assert f"<td>{ticker}</td>" not in top_table
    if hidden:
        assert "hidden — blocked from purchase by the current Configuration" in html
    visible, _ = rankings._visible_and_hidden(bundle, max_n)
    assert top_table.count("<tr") - 1 == len(visible)          # minus the header row


def test_live_full_rankings_marks_blocked_rows(client):
    bundle = rankings.get_bundle(config_store.load_config())
    n_blocked = int((bundle.purchase_block != "").sum())
    html = client.get(f"/rankings/full/table?top_n={len(bundle.result)}").data.decode()
    if n_blocked:
        assert 'class="row-blocked"' in html and "<th>Blocked</th>" in html
        assert "Greyed-out rows are blocked from purchase" in html
    else:
        assert "row-blocked" not in html


def test_entry_candidates_skip_blocked_names():
    cfg = dict(config_store.load_config())
    bundle = rankings.get_bundle(cfg)
    ranked = list(bundle.result.index[bundle.result["RANK"].notna()])
    first_blocked = next((i for i, t in enumerate(ranked) if bundle.purchase_block.get(t, "")), None)
    if first_blocked is None or first_blocked > 60:
        pytest.skip("current Configuration blocks no name near the top of the ranking")
    # ask for enough slots that the first blocked name WOULD be reached if it weren't skipped
    out = actions.entry_candidates(bundle, cfg, ledger={}, holdings_metrics=[], exit_tickers=set(),
                                    n_new_positions=first_blocked + 2)
    picked = [r["ticker"] for r in out["rows"]]
    assert ranked[first_blocked] not in picked
    assert not any(bundle.purchase_block.get(t, "") for t in picked)
