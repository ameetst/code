"""
mtm.py
======
On-demand mark-to-market snapshot of the current open book — independent
of clenow_runner.py's Wednesday cadence. Run any day, any time:

    python mtm.py

Reads holdings.csv (current open positions) and fetches a live quote for
every ticker via the same provider clenow_runner.py is configured to use
(clenow_config.json's price_provider — yfinance or dhan), one batched
call. Anything a live quote can't be found for falls back to
clenow_ranked.csv's last_close from the most recent weekly run. Prints
cost basis / current market value / unrealized P&L per position and in
total. Read-only — writes nothing, touches no state clenow_runner.py
owns.

Flags:
    --price_provider {yfinance,dhan}   Override the configured provider
                                        for this check only (no effect on
                                        clenow_config.json).
    --no_live_price                    Skip live quotes entirely; use
                                        clenow_ranked.csv's last_close for
                                        every position (whatever price
                                        that snapshot happened to be from).
"""

import argparse

import pandas as pd

import clenow_runner as cr


def compute_mtm(price_provider: str = None, use_live: bool = True) -> pd.DataFrame:
    if not cr.HOLDINGS_PATH.exists():
        raise SystemExit(f"No {cr.HOLDINGS_PATH.name} found — nothing held yet.")
    holdings = pd.read_csv(cr.HOLDINGS_PATH)
    if not len(holdings):
        raise SystemExit("holdings.csv is empty — no open positions.")

    config = cr.load_config()
    provider = price_provider or config["price_provider"]

    fallback_closes = {}
    if cr.RANKED_OUTPUT_PATH.exists():
        ranked = pd.read_csv(cr.RANKED_OUTPUT_PATH)[["ticker", "last_close"]]
        fallback_closes = dict(zip(ranked["ticker"], ranked["last_close"]))

    tickers = holdings["ticker"].tolist()
    if use_live:
        print(f"Fetching live prices via {provider} for {len(tickers)} held ticker(s) ...")
        live_prices, live_statuses = cr.fetch_live_prices_bulk(tickers, provider)
    else:
        live_prices, live_statuses = {}, {}

    rows = []
    for _, h in holdings.iterrows():
        t = h["ticker"]
        price = live_prices.get(t)
        if price is not None:
            source = live_statuses.get(t, "live")
        else:
            price = fallback_closes.get(t)
            reason = live_statuses.get(t, "live price disabled" if not use_live else "no live price")
            source = (f"fallback: clenow_ranked.csv last_close ({reason})"
                       if price is not None else f"no price available ({reason})")

        cost_basis = float(h["entry_price"]) * float(h["shares"])
        market_value = float(price) * float(h["shares"]) if price is not None else None
        unrealized_pnl = (market_value - cost_basis) if market_value is not None else None
        unrealized_pnl_pct = ((price / h["entry_price"] - 1) * 100) if price is not None else None

        rows.append(dict(
            ticker=t, entry_date=h["entry_date"], entry_price=h["entry_price"],
            current_price=round(price, 4) if price is not None else None,
            price_source=source, shares=h["shares"],
            cost_basis=round(cost_basis, 2),
            market_value=round(market_value, 2) if market_value is not None else None,
            unrealized_pnl=round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
            unrealized_pnl_pct=round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
        ))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="On-demand mark-to-market snapshot of the current open book")
    parser.add_argument("--price_provider", choices=["yfinance", "dhan"], default=None,
                         help="Override clenow_config.json's provider for this check only (doesn't persist)")
    parser.add_argument("--no_live_price", action="store_true",
                         help="Skip live quotes; use clenow_ranked.csv's last_close for everything")
    args = parser.parse_args()

    df = compute_mtm(args.price_provider, use_live=not args.no_live_price)
    priced = df[df["current_price"].notna()]
    unpriced = df[df["current_price"].isna()]

    pd.set_option("display.width", 160)
    print()
    print(df[["ticker", "entry_date", "entry_price", "current_price", "shares",
              "cost_basis", "market_value", "unrealized_pnl", "unrealized_pnl_pct", "price_source"]]
          .sort_values("unrealized_pnl_pct", ascending=False, na_position="last")
          .to_string(index=False))

    print(f"\n  Priced positions     : {len(priced)} of {len(df)}")
    if len(priced):
        total_cost = priced["cost_basis"].sum()
        total_value = priced["market_value"].sum()
        total_unrealized = priced["unrealized_pnl"].sum()
        pct = (total_value / total_cost - 1) * 100 if total_cost else float("nan")
        print(f"  Total cost basis     : ₹{total_cost:,.2f}")
        print(f"  Total market value   : ₹{total_value:,.2f}")
        print(f"  Total unrealized P&L : ₹{total_unrealized:,.2f}  ({pct:+.2f}%)")
    if len(unpriced):
        print(f"  ⚠ No price available for: {', '.join(unpriced['ticker'])}")


if __name__ == "__main__":
    main()
