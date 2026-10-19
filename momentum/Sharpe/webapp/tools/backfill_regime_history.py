"""
Rebuilds a universe's Regime Score history for past trading days from the price workbook,
so the Regime Score Trend chart isn't a handful of points joined by long straight lines.

For each past date D the price data is cut at D and the same pipeline the live ranking uses
is run (Sharpe composite -> 52H mask -> momentum_lib.compute_regime_score). Real recorded
entries always win; only missing dates are filled. Stamping follows how the live history is
written: an entry is stamped with the RUN date but computed from the previous close (validated:
your recorded 13-Oct entry equals a recompute on 12-Oct data), so the entry for price day P is
dated the next trading day. Dates before 200 trading-day columns exist
are skipped: compute_regime_score itself falls back to a neutral 0.5 there ("insufficient
data"), which would be noise, not history.

SAFE BY DEFAULT: writes a NEW file (<UNIVERSE>_regime_history_backfill.json) and never touches
the live history. It also re-derives some of your REAL recorded entries as a validation of the
method. Copy the file over the live one yourself once you're happy with it.

    python webapp/tools/backfill_regime_history.py NSEAll
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_DIR))

import momentum_lib as ml  # noqa: E402

WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
TRADING_DAYS = 252
MIN_COLUMNS = 200          # compute_regime_score needs >= 200 valid NIFTY closes / columns


def regime_at(prices_df, nifty, tickers, k, cfg):
    """Regime entry as of the (k+1)-th price column."""
    p, n = prices_df.iloc[:, : k + 1], nifty.iloc[: k + 1]
    _, z_df = ml.compute_sharpe(p, tickers, WINDOWS, 0.07 / TRADING_DAYS, TRADING_DAYS)
    comp = z_df["COMPOSITE"].map(ml.normalise_composite)
    mask = ml.compute_pct_from_52h(p, tickers) >= -25
    score, detail = ml.compute_regime_score(
        n, mask, comp, prices_df=p, signal_weights=ml.DEFAULT_SIGNAL_WEIGHTS,
        min_n=int(cfg["min_n"]), max_n=int(cfg["max_n"]),
        new_entry_threshold=ml.DEFAULT_NEW_ENTRY_THRESHOLD)
    return {"Composite": round(float(score), 4), "Breadth": round(detail["breadth_score"], 4),
            "Momentum": round(detail["momentum_score"], 4), "Dynamic N": detail["dynamic_n"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("universe", help="e.g. NSEAll or N750")
    ap.add_argument("--from", dest="start", help="first date to fill (YYYY-MM-DD); default: earliest possible")
    args = ap.parse_args()

    cfg = ml.load_config(str(CODE_DIR))
    prices_df, nifty, tickers, _ = ml.load_prices(str(CODE_DIR / f"{args.universe}_updated.xlsx"))
    cols = [c.date() if hasattr(c, "date") else c for c in prices_df.columns]
    live_path = CODE_DIR / f"{args.universe}_regime_history.json"
    real = json.loads(live_path.read_text()) if live_path.exists() else []
    real_by_date = {e["date"]: e for e in real}
    print(f"{args.universe}: {len(cols)} price columns {cols[0]} -> {cols[-1]}; {len(real)} real history entries")

    # 1. Validate the method against recent real entries. Entry D was computed from the price
    #    column just BEFORE D (run date vs previous close). Older entries were recorded under
    #    earlier data/universe states and cannot be reproduced, so they are not compared.
    idx_of = {c.isoformat(): i for i, c in enumerate(cols)}
    cutoff = (cols[-1] - datetime.timedelta(days=14)).isoformat()
    print("\nValidation on recent real entries (recorded vs recomputed from the previous close):")
    worst = 0.0
    for d, e in sorted(real_by_date.items()):
        if d >= cutoff and d in idx_of and idx_of[d] >= MIN_COLUMNS:
            got = regime_at(prices_df, nifty, tickers, idx_of[d] - 1, cfg)
            diff = max(abs(got["Composite"] - e["Composite"]), abs(got["Breadth"] - e["Breadth"]),
                       abs(got["Momentum"] - e["Momentum"]))
            worst = max(worst, diff)
            print(f"  {d}: recorded C={e['Composite']:.4f} B={e['Breadth']:.4f} M={e['Momentum']:.4f} | "
                  f"recomputed C={got['Composite']:.4f} B={got['Breadth']:.4f} M={got['Momentum']:.4f} | max diff {diff:.4f}")
    print(f"  worst difference: {worst:.4f}")
    skipped = [d for d in real_by_date if d < cutoff]
    print(f"  ({len(skipped)} older real entries kept as recorded, not compared)")

    # 2. Fill the missing dates.
    first_k = MIN_COLUMNS - 1
    start = datetime.date.fromisoformat(args.start) if args.start else cols[first_k]
    todo = [(i, cols[i + 1]) for i in range(first_k, len(cols) - 1)
            if cols[i + 1] >= start and cols[i + 1].isoformat() not in real_by_date]
    print(f"\nBackfilling {len(todo)} missing trading days "
          f"({todo[0][1]} -> {todo[-1][1]})" if todo else "\nNothing to backfill.")
    filled = []
    for n, (i, c) in enumerate(todo, 1):
        filled.append({"date": c.isoformat(), **regime_at(prices_df, nifty, tickers, i, cfg)})
        if n % 10 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)} ({c})", flush=True)

    merged = sorted(real + filled, key=lambda e: e["date"])
    out = CODE_DIR / f"{args.universe}_regime_history_backfill.json"
    out.write_text(json.dumps(merged, indent=2))
    print(f"\nWrote {out.name}: {len(merged)} entries ({len(real)} real kept + {len(filled)} backfilled). "
          f"Live file untouched.")


if __name__ == "__main__":
    main()
