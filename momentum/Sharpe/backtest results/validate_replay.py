"""
validate_replay.py
==================
Independent check of sweep_replay.py: re-runs the first K weeks the slow way (real
momentum_lib.compute_universe_rankings call each week + a plain-pandas copy of the live
Sharpe.py exit / entry logic and the baseline-backtest sizing / return maths) and compares the
equity curve with the cache-based replay. They must agree to ~1e-9.

    python validate_replay.py 16        # K weeks (needs rank_0000..rank_{K-1} in the cache)

Read-only: imports momentum_lib, does not modify any live file.
"""

import os
import sys
import pickle
import warnings
from contextlib import contextmanager

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SHARPE_ROOT = os.path.dirname(HERE)
sys.path.insert(0, SHARPE_ROOT)
sys.path.insert(0, HERE)
import momentum_lib as ml
import sweep_replay as sr

warnings.filterwarnings("ignore")
K = int(sys.argv[1]) if len(sys.argv) > 1 else 16
cfg = sr.CFG
STOCKDB = os.path.join(SHARPE_ROOT, "STOCKDB.csv")
WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
SIGNAL_WEIGHTS = {"ema50_breadth": 0.35, "ema_trend_breadth": 0.25, "breadth": 0.25, "momentum": 0.15}
MAX_WT = 1.0 / cfg["max_n"]


@contextmanager
def quiet():
    with open(os.devnull, "w") as dn:
        old, sys.stdout = sys.stdout, dn
        try:
            yield
        finally:
            sys.stdout = old


d = pickle.load(open(os.path.join(sr.CACHE_DIR, "_data.pkl"), "rb"))
prices_df, nifty_series, tickers = d["prices_df"], d["nifty_series"], d["stock_tickers"]
volume_df, valid_dates, idx_list = d["volume_df"], d["valid_dates"], d["idx_list"]
ffill, nifty_ffill = prices_df.ffill(axis=1), nifty_series.ffill()

cur, equity, nifty_eq = {}, sr.START_EQUITY, sr.START_EQUITY
ref_eq, ref_nq, n_exits = [], [], 0
cash_wk = (1.0 + sr.LIQUID_YIELD_PA) ** (1 / 52) - 1.0

for i in range(K):
    t_date, idx, nidx = valid_dates[i], idx_list[i], idx_list[i + 1]
    sliced = prices_df.iloc[:, :idx + 1]
    with quiet():
        result, score, detail = ml.compute_universe_rankings(
            sliced, nifty_series.iloc[:idx + 1], tickers, volume_df=volume_df.iloc[:, :idx + 1],
            min_turnover_cr=cfg["min_turnover"], min_cmp=cfg["min_cmp"], min_market_cap=cfg["min_market_cap"],
            eq_series_filter=cfg["eq_series_filter"], circuit_filter_enabled=cfg["circuit_filter_enabled"],
            circuit_threshold=cfg["circuit_threshold"], stockdb_csv_path=STOCKDB, windows=WINDOWS,
            trading_days=252, rfr_annual=0.07, signal_weights=SIGNAL_WEIGHTS, min_n=cfg["min_n"],
            max_n=cfg["max_n"], new_entry_threshold=0.40)
    dyn, allow = detail["dynamic_n"], detail["allow_new"]

    # exits — Sharpe.py trigger chain
    keep = []
    for tk, st in cur.items():
        held = (t_date - st["entry"]).days
        rk = result.loc[tk, "RANK"] if tk in result.index else np.nan
        pc = result.loc[tk, "PCT_FROM_52H"] if tk in result.index else np.nan
        rd = result.loc[tk, "REL_52H_DD"] if tk in result.index else np.nan
        if pd.notna(pc) and pc < -25:
            n_exits += 1
        elif pd.isna(rk):
            n_exits += 1
        elif pd.notna(rd) and rd < sr.LIVE_RELDD:
            if held >= sr.LIVE_LOCK: n_exits += 1
            else: keep.append(tk)
        elif rk > sr.LIVE_BUFFER:
            if held >= sr.LIVE_LOCK: n_exits += 1
            else: keep.append(tk)
        else:
            keep.append(tk)

    # entries — Sharpe.py fill-open-slots walk
    n_new = dyn - len(keep)
    if allow and n_new > 0:
        cands = []
        for t in result.index:
            if t in keep: continue
            if not bool(result.loc[t].get("CMP_ELIGIBLE", True)): continue
            if not bool(result.loc[t].get("MCAP_ELIGIBLE", True)): continue
            if len(cands) >= n_new: break
            cands.append(t)
        keep = keep + cands

    # sizing — baseline-backtest maths
    raw = {}
    for t in keep:
        comp = result.loc[t, "COMPOSITE"]
        px = sliced.loc[t].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                pw = px.iloc[-w:] if len(px) >= w else px
                lr = np.diff(np.log(pw.values))
                if len(lr) > 5:
                    vols.append(np.std(lr, ddof=1) * np.sqrt(252))
            raw[t] = comp / np.mean(vols) if vols and np.mean(vols) > 0 else comp
        else:
            raw[t] = comp
    tot = sum(raw.values())
    new = {}
    for t in keep:
        nw = raw[t] / tot if tot > 0 else 1.0 / len(keep)
        new[t] = dict(entry=cur[t]["entry"] if t in cur else t_date, w=min(MAX_WT, nw))

    # returns
    if (not allow) and not new:
        gross = cash_wk
    else:
        lst = list(new)
        cash_w = max(0.0, 1.0 - sum(v["w"] for v in new.values()))
        if lst:
            rets = ffill.loc[lst].iloc[:, nidx] / ffill.loc[lst].iloc[:, idx] - 1.0
            gross = (rets * pd.Series({t: new[t]["w"] for t in lst})).sum() + cash_w * cash_wk
        else:
            gross = cash_wk
        if pd.isna(gross): gross = 0.0
    ab = sum(abs((new[t]["w"] if t in new else 0.0) - (cur[t]["w"] if t in cur else 0.0)) for t in set(cur) | set(new))
    net = gross - ab * sr.FRICTION
    equity *= 1 + net
    nifty_eq *= nifty_ffill.iloc[nidx] / nifty_ffill.iloc[idx]
    ref_eq.append(equity); ref_nq.append(nifty_eq)
    cur = new
    print(f"  ref week {i+1}/{K}: equity {equity:,.0f}  held {len(new)}  dyn_n {dyn}", flush=True)

cache = sr.load_cache(K)
assert len(cache["prepared"]) == K, f"cache only has {len(cache['prepared'])} weeks"
sim = sr.simulate(cache, sr.LIVE_BUFFER, sr.LIVE_LOCK)
ref_eq, ref_nq = np.array(ref_eq), np.array(ref_nq)
rel = np.abs(sim["equity"] - ref_eq) / ref_eq
nrel = np.abs(sim["nifty"] - ref_nq) / ref_nq
print(f"\nExits exercised in reference run: {n_exits}")
print(f"Max relative equity diff replay vs reference: {rel.max():.2e}   nifty: {nrel.max():.2e}")
print(f"Final equity  reference {ref_eq[-1]:,.4f}   replay {sim['equity'][-1]:,.4f}")
print("VALIDATION", "PASSED" if rel.max() < 1e-9 and nrel.max() < 1e-9 else "FAILED")
