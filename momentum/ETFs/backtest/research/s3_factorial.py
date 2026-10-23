"""Stage 3: factorial over the candidate changes; judge by MAIN EFFECTS, not the single best cell."""
import itertools, sys
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


def build(cfg):
    p = Params(n_slots=cfg["slots"], exit_rank=cfg["rank"], tsl=cfg["tsl"], regime=cfg["regime"],
               riskoff_liquidate=cfg["liq"], cash_rate=cfg.get("cash", 0.02))
    strat = make_rerank(extras={"m12_1": cfg["m12"]}) if cfg["m12"] else None
    return p, strat


def work(cfg):
    p, strat = build(cfg)
    S = _ctx.first_trade
    r = run(_ctx, p, strat); e = r["equity"]
    m, m1, m2 = metrics(e, S), metrics(e, S, MID - pd.Timedelta(days=1)), metrics(e, MID)
    ms = metrics(run(_ctx, replace(p, slip=SLIP), strat)["equity"], S)
    tr = r["trades"]
    return {**cfg, "CAGR": m["CAGR"], "Vol": m["Vol"], "MaxDD": m["MaxDD"], "Sharpe": m["Sharpe"], "Calmar": m["Calmar"],
            "P1_Sh": m1["Sharpe"], "P2_Sh": m2["Sharpe"], "P1_CAGR": m1["CAGR"], "P2_CAGR": m2["CAGR"],
            "Sh@20bp": ms["Sharpe"], "CAGR@20bp": ms["CAGR"], "buys": int((tr.TYPE == "BUY").sum())}


GRID = {
    "slots": [5, 8, 10],
    "rank": [40, 60, None],
    "m12": [0.0, 0.25, 0.5],
    "reg": [("ema50", False), ("ema200", True)],
    "tsl": [0.05, 0.07],
}

if __name__ == "__main__":
    cfgs = [dict(slots=s, rank=r, m12=m, regime=reg, liq=liq, tsl=t)
            for s, r, m, (reg, liq), t in itertools.product(GRID["slots"], GRID["rank"], GRID["m12"], GRID["reg"], GRID["tsl"])]
    with ProcessPoolExecutor(max_workers=4, initializer=_init) as ex:
        rows = list(ex.map(work, cfgs, chunksize=2))
    df = pd.DataFrame(rows)
    df.to_csv("s3_factorial.csv", index=False)
    print(len(df), "configs done")
