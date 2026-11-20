"""
Holdings allocation donuts (Dashboard): market-value share by market-cap tier and by sector.
"""
import math
import re

import pytest

import momentum_lib as ml
from webapp.app import create_app
from webapp.core import allocation


def _h(ticker, mv):
    return {"Ticker": ticker, "Market Value": mv}


def test_breakdown_sums_to_100_and_counts_stocks():
    rows = ml.allocation_breakdown([_h("A", 300.0), _h("B", 100.0), _h("C", 600.0)],
                                   {"A": "Mid Cap", "B": "Mid Cap", "C": "Large Cap"})
    assert [r["label"] for r in rows] == ["Large Cap", "Mid Cap"]            # largest first by default
    assert [r["pct"] for r in rows] == pytest.approx([60.0, 40.0])
    assert [r["n"] for r in rows] == [1, 2]
    assert sum(r["pct"] for r in rows) == pytest.approx(100.0)


def test_unlabelled_tickers_go_to_unclassified_which_is_always_last():
    rows = ml.allocation_breakdown([_h("A", 10.0), _h("B", 900.0), _h("C", 90.0)], {"A": "X", "C": "Y"})
    assert [r["label"] for r in rows] == ["Y", "X", ml.UNCLASSIFIED]          # B is unlabelled: biggest, still last
    assert rows[-1]["pct"] == pytest.approx(90.0)


def test_explicit_order_is_respected_for_cap_tiers():
    labels = list(ml.CAP_TIER_LABELS.values())
    rows = ml.allocation_breakdown([_h("A", 1.0), _h("B", 5.0), _h("C", 2.0)],
                                   {"A": "Nano Cap", "B": "Mid Cap", "C": "Large Cap"}, order=labels)
    assert [r["label"] for r in rows] == ["Large Cap", "Mid Cap", "Nano Cap"]


def test_no_value_gives_no_slices():
    assert ml.allocation_breakdown([], {}) == []
    assert ml.allocation_breakdown([_h("A", 0.0)], {"A": "X"}) == []


def test_unclassified_is_grey_and_other_colours_are_distinct():
    colors = ml.donut_colors(["A", "B", ml.UNCLASSIFIED, "C"])
    assert colors[2] == ml.DONUT_UNCLASSIFIED_COLOR
    assert len({colors[0], colors[1], colors[3]}) == 3


def test_sector_map_loader_tolerates_missing_file_and_blank_rows(tmp_path):
    assert ml.load_sector_map(tmp_path / "nope.csv") == {}
    p = tmp_path / "Sector Metadata.csv"
    p.write_text("TICKER,SECTOR\nAAA ,Healthcare\nBBB,\n,Chemicals\n")
    assert ml.load_sector_map(p) == {"AAA": "Healthcare"}


def test_sector_csv_is_joined_on_symbol_and_industry(tmp_path):
    """SECTOR.csv layout: Company Name, Industry, Symbol, Series, ISIN Code (UTF-8, maybe with a BOM)."""
    p = tmp_path / "SECTOR.csv"
    p.write_bytes(("﻿Company Name,Industry,Symbol,Series,ISIN Code\r\n"
                   "Div Labs,Healthcare, divislab ,EQ,INE1\r\n"
                   "Blank Co,,BLANK,EQ,INE2\r\n").encode("utf-8"))
    m = ml.load_sector_map(p)
    assert m == {"DIVISLAB": "Healthcare"}                    # stripped + upper-cased key; blank industry dropped
    rows = ml.allocation_breakdown([_h("DIVISLAB", 60.0), _h("MISSING", 40.0)], m)
    assert [(r["label"], round(r["pct"])) for r in rows] == [("Healthcare", 60), (ml.UNCLASSIFIED, 40)]


def test_live_sector_csv_matches_the_dashboard_sector_donut(client):
    from pathlib import Path
    csv = Path(__file__).resolve().parents[2] / "SECTOR.csv"
    if not csv.exists():
        pytest.skip("no SECTOR.csv")
    labels = {s["label"] for s in allocation.build([_h("DIVISLAB", 1.0)])["sector"]["slices"]}
    assert labels == {ml.load_sector_map(csv)["DIVISLAB"]}


def test_donut_arcs_tile_the_whole_circle():
    d = allocation._donut("t", ml.allocation_breakdown([_h("A", 1.0), _h("B", 3.0)], {"A": "X", "B": "Y"}))
    lengths = [float(s["dash"].split()[0]) for s in d["slices"]]
    assert sum(lengths) == pytest.approx(2 * math.pi * allocation.R)
    offsets = [float(s["offset"]) for s in d["slices"]]
    assert offsets[0] == 0 and offsets[1] == pytest.approx(-lengths[0])       # next arc starts where the last ended


@pytest.fixture(scope="module")
def client():
    return create_app().test_client()


def test_dashboard_shows_two_donuts_side_by_side_above_the_holdings_table(client):
    html = client.get("/").data.decode()
    seg = html[html.index('id="holdings-content"'):]
    if "No active holdings found" in seg:
        pytest.skip("no open positions in the live tradelog")
    assert re.findall(r'class="donut-title">([^<]*)<', seg) == ["Holdings by Market Cap", "Holdings by Sector"]
    assert seg.count('class="donut-row"') == 1 and seg.count('class="donut-card"') == 2   # one row, two cards
    assert seg.index("donut-row") < seg.index("data-table")                 # donuts above the table
    # the Refresh button is not part of the collapsible: it comes before (outside) the <details> element
    assert seg.index("refresh-prices-btn") < seg.index('<details class="expander"') < seg.index("donut-row")
    for title in ("Market Cap", "Sector"):                                   # legend percentages add up to 100
        j = seg.index("Holdings by " + title)
        pcts = [float(p) for p in re.findall(r'class="donut-pct">([\d.]+)%', seg[j:seg.index("</ul>", j)])]
        assert sum(pcts) == pytest.approx(100.0, abs=0.1 * len(pcts))


def test_donuts_survive_the_refresh_partial(client):
    html = client.post("/dashboard/refresh-prices").data.decode()
    if "No active holdings" in html:
        pytest.skip("no open positions in the live tradelog")
    assert "donut-row" in html
