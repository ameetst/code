"""
fetch_migrated_history.py
=========================
Daily OHLCV (full available history) for the NSE Emerge -> Main Board
migrated tickers listed in SME/nse_migrated_list.csv, via Dhan.
Resolves symbols against ALL NSE equity series (EQ/BE/BZ/SM/ST ...), since
dhandata.resolve_security_id only knows EQ/BE. Cached per symbol in
SME/migrated_cache/. Shared dhandata library is not modified.
"""
import sys, time, datetime as dt
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
DHAN = Path(r"C:\Users\ameet\Documents\Github\dhan_datahq")
sys.path.insert(0, str(DHAN))
import dhandata as dh  # noqa

CACHE = HERE / "migrated_cache"
CACHE.mkdir(exist_ok=True)
lst = pd.read_csv(HERE / "nse_migrated_list.csv", parse_dates=["mig_date"])

m = pd.read_csv(DHAN / "api-scrip-master-detailed.csv", low_memory=False,
                usecols=["EXCH_ID", "SEGMENT", "SECURITY_ID", "UNDERLYING_SYMBOL", "SERIES"])
m = m[(m.EXCH_ID == "NSE") & (m.SEGMENT == "E")]
pref = {"EQ": 0, "BE": 1, "SM": 2, "ST": 3, "BZ": 4}
m["p"] = m.SERIES.map(pref).fillna(9)
m = m.sort_values("p").drop_duplicates("UNDERLYING_SYMBOL").set_index("UNDERLYING_SYMBOL")

from_date = "2015-01-01"
to_date = (dt.date.today() + dt.timedelta(days=1)).isoformat()
unres, failed, ok = [], {}, 0
t0 = time.time()
for i, r in enumerate(lst.itertuples(), 1):
    s = r.symbol.strip()
    f = CACHE / f"{s}.csv"
    if f.exists():
        ok += 1
        continue
    if s not in m.index:
        unres.append(s)
        continue
    try:
        df = dh.get_daily_history_df(int(m.loc[s, "SECURITY_ID"]), "NSE_EQ", "EQUITY",
                                     from_date=from_date, to_date=to_date)
        if df is None or df.empty:
            failed[s] = "empty"
        else:
            df.to_csv(f)
            ok += 1
    except Exception as e:  # noqa
        failed[s] = str(e)[:120]
    if i % 25 == 0:
        print(f"[{i}/{len(lst)}] ok={ok} unresolved={len(unres)} failed={len(failed)} ({time.time()-t0:.0f}s)", flush=True)
print("DONE ok", ok, "| unresolved", len(unres), unres, "| failed", failed)
