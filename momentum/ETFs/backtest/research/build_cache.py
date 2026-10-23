"""Build one-off cache for the research harness: cleaned prices, regime series, and the live
emr.build_ranking() feature table for every weekly rebalance date. Run once; engine reads the pickle."""
import sys, io, contextlib, pickle
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import etf_backtest_current_vs_nifty500 as bt                       # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean  # noqa: E402
emr = bt.emr

meta, raw = bt.load_history_etfs(bt.HISTORY_FILE)
prices, _ = detect_and_clean(raw)
dates = prices.index
regime_s, ema50, ema100 = bt.fetch_benchmark_series(dates)
week_starts = [d for d in bt.get_week_starts(dates) if dates.get_loc(d) >= bt.MIN_HISTORY_DAYS]

feats = {}
for k, d in enumerate(week_starts):
    i = dates.get_loc(d)
    hist = prices.loc[:dates[i - 1]]
    with contextlib.redirect_stdout(io.StringIO()):
        feats[d] = emr.build_ranking(meta, hist)
    if k % 50 == 0:
        print(f"{k}/{len(week_starts)} {d.date()}", flush=True)

with open(HERE / "cache.pkl", "wb") as f:
    pickle.dump({"meta": meta, "prices": prices, "regime": regime_s, "feats": feats,
                 "cfg": {k: getattr(emr.CONFIG, k) for k in
                         ["TOP_N", "EXIT_MAX_RANK", "EXIT_MAX_DD_FROM_HIGH", "TSL_THRESHOLD", "SECTOR_CAP",
                          "BLEND_W_SHARPE", "BLEND_W_CLENOW", "SHARPE_W6M", "SHARPE_W3M", "DAILY_RF"]}}, f)
print("saved cache.pkl", len(feats), "weeks")
