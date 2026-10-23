"""Stage 5: neighbourhood grid around the plateau-centred candidate; saves all equity curves for walk-forward."""
import itertools, pickle
import numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from lib import Ctx, Params, run, metrics
from strategies import make_rerank

_ctx = None
MID = pd.Timestamp("2022-01-01"); SLIP = 0.002


def _init():
    global _ctx
    _ctx = Ctx()


def work(cfg):
    p = Params(n_slots=cfg["slots"], exit_rank=cfg["rank"], tsl=cfg["tsl"], regime=cfg["regime"],
               riskoff_liquidate=True, cash_rate=0.02)
    strat = make_rerank(extras={"m12_1": cfg["m12"]}) if cfg["m12"] else None
    S = _ctx.first_trade
    r = run(_ctx, p, strat); e = r["equity"]
    m, m1, m2 = metrics(e, S), metrics(e, S, MID - pd.Timedelta(days=1)), metrics(e, MID)
    es = run(_ctx, replace(p, slip=SLIP), strat)["equity"]
    tr = r["trades"]
    row = {**cfg, "CAGR": m["CAGR"], "Vol": m["Vol"], "MaxDD": m["MaxDD"], "Sharpe": m["Sharpe"], "Calmar": m["Calmar"],
           "P1_Sh": m1["Sharpe"], "P2_Sh": m2["Sharpe"], "Sh@20bp": metrics(es, S)["Sharpe"], "CAGR@20bp": metrics(es, S)["CAGR"],
           "buys": int((tr.TYPE == "BUY").sum())}
    return row, e


if __name__ == "__main__":
    cfgs = [dict(regime=reg, slots=s, rank=rk, m12=m, tsl=t)
            for reg, s, rk, m, t in itertools.product(
                ["sma200", "sma200_h1", "sma200_h2", "ema200"], [5, 6, 8], [None, 60], [0.0, 0.25, 0.5], [0.05, 0.07, 0.10])]
    with ProcessPoolExecutor(max_workers=4, initializer=_init) as ex:
        out = list(ex.map(work, cfgs, chunksize=3))
    df = pd.DataFrame([o[0] for o in out])
    eqs = {i: o[1] for i, o in enumerate(out)}
    df.to_csv("s5_grid.csv", index=False)
    pickle.dump(eqs, open("s5_equities.pkl", "wb"))
    print(len(df), "done")
