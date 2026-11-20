"""
Holdings allocation donuts for the Dashboard: share of current market value by market-cap tier
(STOCKDB.csv) and by sector (SECTOR.csv, joined on Symbol = ticker). Pure server-side SVG, no chart library.
"""
import math

import momentum_lib as ml

from webapp.core import cap_tier
from webapp.settings import DATA_DIR

R = 44.0            # donut radius (stroke centre line) in a 120x120 viewBox
STROKE = 24.0
CIRC = 2 * math.pi * R


def _arcs(slices: list[dict]) -> list[dict]:
    """Adds colour + stroke-dasharray/offset to each slice (circles drawn from 12 o'clock)."""
    colors = ml.donut_colors([s["label"] for s in slices])
    out, start = [], 0.0
    for s, color in zip(slices, colors):
        length = CIRC * s["pct"] / 100.0
        out.append({**s, "color": color,
                    "dash": f"{length:.3f} {CIRC - length:.3f}", "offset": f"{-start:.3f}"})
        start += length
    return out


def _donut(title: str, slices: list[dict], note: str | None = None) -> dict:
    return {"title": title, "slices": _arcs(slices), "note": note,
            "r": R, "stroke": STROKE, "n_stocks": sum(s["n"] for s in slices)}


def build(holdings_rows: list[dict]) -> dict | None:
    """{"cap": donut, "sector": donut} for the open positions, or None when there are none."""
    if not holdings_rows:
        return None
    cap_by_ticker = {t: ml.CAP_TIER_LABELS.get(tier, tier) for t, tier in cap_tier.load_stockdb().items()}
    sector_by_ticker = ml.load_sector_map(DATA_DIR / "SECTOR.csv")

    cap = ml.allocation_breakdown(holdings_rows, cap_by_ticker, order=list(ml.CAP_TIER_LABELS.values()))
    sector = ml.allocation_breakdown(holdings_rows, sector_by_ticker)
    if not cap:
        return None

    sector_note = None
    unclassified = next((s for s in sector if s["label"] == ml.UNCLASSIFIED), None)
    if unclassified:
        sector_note = (f"{unclassified['pct']:.0f}% of holdings value ({unclassified['n']} "
                       f"stock{'' if unclassified['n'] == 1 else 's'}) is not in SECTOR.csv (shown as Unclassified).")
    return {"cap": _donut("Holdings by Market Cap", cap),
            "sector": _donut("Holdings by Sector", sector, sector_note)}
