"""
ranking_cache_build.py
======================
One-time, expensive step for parameter sweeps. Runs the live production ranking
pipeline (momentum_lib.compute_universe_rankings, imported read-only) at every
weekly rebalance of the NSEAll history and saves what a portfolio replay needs.
Replaying buy/sell rules on the cache takes seconds instead of ~4 hours.

    python ranking_cache_build.py prep                       # once: load xlsx, save _data.pkl + weekly arrays
    python ranking_cache_build.py config                     # once: freeze dashboard_config.json -> _config.json
    python ranking_cache_build.py build --shard 0 --nshards 3   # run one per shard, in parallel
    python ranking_cache_build.py status

Resumable: existing rank_XXXX.pkl files are skipped, writes are atomic.
Cache config is a frozen copy of the live dashboard_config.json (min/max N, MDTV, min CMP, min market
cap, EQ/circuit flags), read-only. NOTE: STOCKDB market cap is today's value, applied to all history.
Does not modify any live file.
"""

import os
import sys
import pickle
import argparse
import datetime
import warnings
from contextlib import contextmanager

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SHARPE_ROOT = os.path.dirname(HERE)
if SHARPE_ROOT not in sys.path:
    sys.path.insert(0, SHARPE_ROOT)
import momentum_lib as ml

warnings.filterwarnings("ignore")

FILE      = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated_NSEAll.xlsx"
STOCKDB_CSV = os.path.join(SHARPE_ROOT, "STOCKDB.csv")
LIVE_CONFIG_JSON = os.path.join(SHARPE_ROOT, "dashboard_config.json")
CACHE_DIR = os.path.join(HERE, "ranking_cache_nseall")
DATA_PKL  = os.path.join(CACHE_DIR, "_data.pkl")
WEEKLY_PKL = os.path.join(CACHE_DIR, "_weekly.pkl")
CONFIG_SNAPSHOT = os.path.join(CACHE_DIR, "_config.json")

RFR_ANNUAL, TRADING_DAYS = 0.07, 252
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
NEW_ENTRY_THRESHOLD = 0.40
SIGNAL_WEIGHTS = {"ema50_breadth": 0.35, "ema_trend_breadth": 0.25, "breadth": 0.25, "momentum": 0.15}
START_IDX = 252
EMA_PERIODS = [20, 50, 100, 200]
KEEP_COLS = ["RANK", "COMPOSITE", "PCT_FROM_52H", "REL_52H_DD", "ADTV_ELIGIBLE",
             "CMP_ELIGIBLE", "MCAP_ELIGIBLE", "RES_MOM"]


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old


def atomic_pickle(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def rank_path(i):
    return os.path.join(CACHE_DIR, f"rank_{i:04d}.pkl")


def week_end_dates(dates):
    dt_idx = pd.DatetimeIndex(dates)
    eow = [dates[i] for i in range(len(dt_idx) - 1)
           if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
    eow.append(dates[-1])
    return [d for d in eow if dates.index(d) >= START_IDX]


def prep():
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"Loading {FILE} ...", flush=True)
    prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
    volume_df = ml.load_volume(FILE)
    valid_dates = week_end_dates(dates)
    idx_list = [dates.index(d) for d in valid_dates]
    print(f"{len(stock_tickers)} tickers, {len(dates)} days, {len(valid_dates)} weekly dates", flush=True)

    ffill = prices_df.ffill(axis=1)
    weekly_px = ffill.iloc[:, idx_list]
    nifty_w = nifty_series.ffill().iloc[idx_list]

    # EMA is causal, so computing it once on full history == computing it point-in-time.
    ema = {}
    for p in EMA_PERIODS:
        frames = {}
        for t in prices_df.index:
            s = prices_df.loc[t].dropna()
            if len(s):
                frames[t] = s.ewm(span=p, adjust=False).mean()
        e = pd.DataFrame(frames).T.reindex(index=prices_df.index, columns=prices_df.columns).ffill(axis=1)
        ema[p] = e.iloc[:, idx_list].astype("float32")
        print(f"  EMA{p} done", flush=True)

    atomic_pickle(dict(prices_df=prices_df, nifty_series=nifty_series, stock_tickers=stock_tickers,
                       dates=dates, volume_df=volume_df, valid_dates=valid_dates, idx_list=idx_list), DATA_PKL)
    atomic_pickle(dict(valid_dates=valid_dates, idx_list=idx_list, weekly_px=weekly_px.astype("float64"),
                       nifty_w=nifty_w, ema=ema, tickers=list(prices_df.index),
                       config=dict(windows=SHARPE_WINDOWS, source=FILE,
                                   built=datetime.datetime.now().isoformat(timespec="seconds"))),
                  WEEKLY_PKL)
    print("prep done.", flush=True)


def mean_vols(arr):
    """Per-ticker mean annualised vol over 252/189/126/63d windows, same math as Sharpe.py sizing."""
    out = np.full(arr.shape[0], np.nan)
    for r in range(arr.shape[0]):
        px = arr[r]
        px = px[~np.isnan(px)]
        if len(px) <= 10:
            continue
        vols = []
        for w in (252, 189, 126, 63):
            px_w = px[-w:] if len(px) >= w else px
            with np.errstate(divide="ignore", invalid="ignore"):
                log_r = np.diff(np.log(px_w))
            if len(log_r) > 5:
                vols.append(np.std(log_r, ddof=1) * np.sqrt(252))
        if vols and np.mean(vols) > 0:
            out[r] = np.mean(vols)
    return out


def snapshot_config(force=False):
    """Freeze the live config (read-only copy of dashboard_config.json) so every worker and the replay agree."""
    import json, subprocess
    if os.path.exists(CONFIG_SNAPSHOT) and not force:
        print("config snapshot already exists (use --force to overwrite)"); return
    os.makedirs(CACHE_DIR, exist_ok=True)
    cfg = json.load(open(LIVE_CONFIG_JSON))
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=SHARPE_ROOT,
                              capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "momentum_lib.py", "Sharpe.py"],
                                    cwd=SHARPE_ROOT, capture_output=True, text=True).stdout.strip())
    except Exception:
        head, dirty = "unknown", None
    cfg["_snapshot"] = dict(taken=datetime.datetime.now().isoformat(timespec="seconds"),
                            git_head=head, live_files_uncommitted=dirty)
    json.dump(cfg, open(CONFIG_SNAPSHOT, "w"), indent=2)
    print(json.dumps(cfg, indent=2))


def build(shard, nshards):
    import json
    cfg = json.load(open(CONFIG_SNAPSHOT))
    d = pickle.load(open(DATA_PKL, "rb"))
    prices_df, nifty_series, stock_tickers = d["prices_df"], d["nifty_series"], d["stock_tickers"]
    volume_df, idx_list, valid_dates = d["volume_df"], d["idx_list"], d["valid_dates"]
    values = prices_df.values
    todo = [i for i in range(len(valid_dates) - 1) if i % nshards == shard and not os.path.exists(rank_path(i))]
    print(f"shard {shard}/{nshards}: {len(todo)} rebalances to build", flush=True)
    t0 = datetime.datetime.now()
    for n, i in enumerate(todo, 1):
        idx = idx_list[i]
        sliced_prices = prices_df.iloc[:, :idx + 1]
        with suppress_stdout():
            result, regime_score, regime_detail = ml.compute_universe_rankings(
                sliced_prices, nifty_series.iloc[:idx + 1], stock_tickers,
                volume_df=volume_df.iloc[:, :idx + 1] if volume_df is not None else None,
                min_turnover_cr=cfg["min_turnover"], min_cmp=cfg["min_cmp"],
                min_market_cap=cfg["min_market_cap"], eq_series_filter=cfg["eq_series_filter"],
                circuit_filter_enabled=cfg["circuit_filter_enabled"], circuit_threshold=cfg["circuit_threshold"],
                stockdb_csv_path=STOCKDB_CSV, windows=SHARPE_WINDOWS, trading_days=TRADING_DAYS,
                rfr_annual=RFR_ANNUAL, signal_weights=SIGNAL_WEIGHTS,
                min_n=cfg["min_n"], max_n=cfg["max_n"], new_entry_threshold=NEW_ENTRY_THRESHOLD)
        vol = pd.Series(mean_vols(values[:, :idx + 1]), index=prices_df.index)
        df = result[KEEP_COLS].copy()
        df["VOL"] = vol.reindex(df.index).values
        atomic_pickle(dict(i=i, date=valid_dates[i], regime_score=regime_score,
                           dynamic_n=regime_detail["dynamic_n"], allow_new=regime_detail["allow_new"],
                           df=df), rank_path(i))
        el = (datetime.datetime.now() - t0).total_seconds()
        print(f"  shard {shard}: [{n}/{len(todo)}] i={i} {valid_dates[i]} elapsed {el:,.0f}s", flush=True)
    print(f"shard {shard} done.", flush=True)


def status():
    if not os.path.exists(WEEKLY_PKL):
        print("not prepped"); return
    w = pickle.load(open(WEEKLY_PKL, "rb"))
    total = len(w["valid_dates"]) - 1
    have = sum(os.path.exists(rank_path(i)) for i in range(total))
    print(f"{have}/{total} rebalances cached")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "config", "build", "status"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    a = ap.parse_args()
    {"prep": prep, "config": lambda: snapshot_config(a.force),
     "build": lambda: build(a.shard, a.nshards), "status": status}[a.cmd]()
