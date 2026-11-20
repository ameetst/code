"""
Active Holdings + real-time MTM view model, shown on the Dashboard (moved there from the
Tradelog tab). Read-only: the figures come from the tradelog replayed against the effective
prices (the workbook's last close, overridden by the last "Refresh Live Market Prices" fetch).
"""
from webapp.core import allocation, rankings, tradelog


def build(universe: str, bundle: rankings.Bundle, *, error: str | None = None,
          success: str | None = None) -> dict:
    tl = tradelog.load_tradelog(universe)
    pnl = tradelog.calculate_holdings_and_pnl(tl, rankings.effective_prices(bundle))
    holdings_metrics = pnl["holdings_metrics"]

    total_invested = sum(h["Cost Value"] for h in holdings_metrics)
    total_market = sum(h["Market Value"] for h in holdings_metrics)
    total_unrealized = total_market - total_invested
    total_unrealized_pct = (total_unrealized / total_invested * 100) if total_invested > 0 else 0.0

    holdings_rows = sorted(holdings_metrics, key=lambda h: h["Unrealized PnL"], reverse=True)
    for h in holdings_rows:
        # Share of total holdings market value (weights add up to 100%; cash is not included).
        h["weight_pct"] = (h["Market Value"] / total_market * 100) if total_market > 0 else 0.0
        h["row_class"] = ("row-regime" if h["Unrealized PnL"] > 0
                           else "row-breach" if h["Unrealized PnL"] < 0 else "")
        fbd = h["First Buy Date"]
        h["First Buy Date"] = fbd.isoformat() if hasattr(fbd, "isoformat") else fbd

    return {
        "total_invested": total_invested, "total_market": total_market,
        "total_unrealized": total_unrealized, "total_unrealized_pct": total_unrealized_pct,
        "realized_pnl": pnl["realized_pnl"],
        "missing_price_tickers": pnl["missing_price_tickers"],
        "holdings_rows": holdings_rows,
        "allocation": allocation.build(holdings_rows),
        "mtm_error": error, "mtm_success": success,
    }
