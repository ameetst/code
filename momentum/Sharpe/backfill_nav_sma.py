"""
(Re)writes the 50-trading-day SMA of the portfolio NAV onto every entry of
<UNIVERSE>_equity_history.json. Sharpe.py already saves it on each new entry; run this once
to fill the entries that predate the field, and again if past entries are ever edited or
deleted by hand (later entries' saved SMAs would otherwise go stale).

    python backfill_nav_sma.py NSEAll            # writes (after backing the file up)
    python backfill_nav_sma.py NSEAll --dry-run  # just report

Backs the file up as <file>.pre_sma_<stamp>.bak, writes atomically, then re-reads it and
checks every value against an independent pandas rolling mean.
"""
import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

import momentum_lib as ml

HERE = Path(__file__).resolve().parent


def main(universe: str, dry_run: bool) -> int:
    path = HERE / f"{universe}_equity_history.json"
    if not path.exists():
        print(f"ERROR: {path} not found")
        return 1
    entries = json.loads(path.read_text())
    if entries != sorted(entries, key=lambda e: e["date"]):
        print("ERROR: entries are not in date order -- fix the file first")
        return 1

    smas = ml.nav_sma(entries)
    n_have = sum(v is not None for v in smas)
    print(f"{path.name}: {len(entries)} entries, SMA available on {n_have} "
          f"(first at {next((e['date'] for e, v in zip(entries, smas) if v is not None), 'n/a')})")
    if dry_run:
        print("--dry-run: nothing written")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.pre_sma_{stamp}.bak")
    shutil.copy2(path, backup)
    for e, v in zip(entries, smas):
        e[ml.NAV_SMA_KEY] = v
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    shutil.move(str(tmp), str(path))

    # independent check: last NAV per trading day, pandas rolling mean, mapped back to entries
    df = pd.DataFrame(json.loads(path.read_text()))
    day = df["price_date"].fillna(df["date"])
    per_day = df.groupby(day)["portfolio_nav"].last().sort_index().rolling(ml.NAV_SMA_WINDOW).mean()
    expected = day.map(per_day)
    # an entry's SMA only counts days up to that entry, so compare via the day's value at its own
    # last entry: for non-final entries of a repeated day the helper used the NAV logged so far
    last_idx = df.groupby(day).tail(1).index
    for i in last_idx:
        exp, got = expected[i], df.at[i, ml.NAV_SMA_KEY]
        assert (pd.isna(exp) and pd.isna(got)) or abs(exp - got) < 5e-5, (df.at[i, "date"], exp, got)
    print(f"Wrote {path.name} (backup: {backup.name}); verified against pandas rolling mean "
          f"on {len(last_idx)} trading days.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("universe", help="e.g. NSEAll")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a.universe, a.dry_run))
