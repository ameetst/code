"""Stage 2: one-change-at-a-time sweeps around the live baseline."""
import sys
import numpy as np, pandas as pd
from dataclasses import replace
from lib import Ctx, Params, run, metrics, buy_hold
from strategies import make_rerank

pd.set_option("display.width", 220); pd.set_option("display.max_rows", 500)
ctx = Ctx()
START = ctx.first_trade
MID = pd.Timestamp("2022-01-01")
SLIP = 0.002
BASE = Params()


def evaluate(label, p=BASE, strat=None):
    r = run(ctx, p, strat)
    e = r["equity"]
    m, m1, m2 = metrics(e, START), metrics(e, START, MID - pd.Timedelta(days=1)), metrics(e, MID)
    rs = run(ctx, replace(p, slip=SLIP), strat)["equity"]
    ms = metrics(rs, START)
    tr = r["trades"]
    return {"variant": label, "CAGR": m["CAGR"], "Vol": m["Vol"], "MaxDD": m["MaxDD"], "Sharpe": m["Sharpe"],
            "Calmar": m["Calmar"], "P1_CAGR": m1["CAGR"], "P1_Sh": m1["Sharpe"], "P2_CAGR": m2["CAGR"],
            "P2_Sh": m2["Sharpe"], "Sh@20bp": ms["Sharpe"], "CAGR@20bp": ms["CAGR"],
            "buys": int((tr.TYPE == "BUY").sum()), "invested": r["invested"].loc[START:].mean()}


groups = {}
groups["slots"] = [(f"n_slots={n}", replace(BASE, n_slots=n), None) for n in (3, 4, 5, 6, 8, 10)]
groups["tsl"] = [(f"tsl={t}", replace(BASE, tsl=t), None) for t in (None, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20)]
groups["exit_rank"] = [(f"exit_rank={k}", replace(BASE, exit_rank=k), None) for k in (None, 10, 20, 30, 40, 60, 100)]
groups["exit_dd"] = [(f"exit_dd={k}", replace(BASE, exit_dd=k), None) for k in (None, 0.15, 0.20, 0.25, 0.35, 0.50)]
groups["regime"] = [(f"regime={k}", replace(BASE, regime=k), None)
                    for k in ("ema20", "ema30", "ema50", "ema75", "ema100", "ema200", "sma100", "sma200", "ema50_and_trend", "always")]
groups["regime_liq"] = [(f"regime={k}+liquidate", replace(BASE, regime=k, riskoff_liquidate=True), None)
                        for k in ("ema20", "ema50", "ema100", "ema200", "sma200")]
groups["cash"] = [(f"cash_rate={c}", replace(BASE, cash_rate=c), None) for c in (0.0, 0.02, 0.05, 0.065)]
groups["cadence"] = [(f"every={k}wk", replace(BASE, every=k), None) for k in (1, 2, 3, 4)]
groups["sector_cap"] = [(f"sector_cap={k}", replace(BASE, sector_cap=k), None) for k in (1, 2, 3, 5)]
groups["screen_thr"] = [(f"screen<= {t:.0%} from high", BASE, make_rerank(thr=t)) for t in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 1.0)]
groups["blend_wC"] = [(f"wClenow={w}", BASE, make_rerank(wS=1 - w, wC=w)) for w in (0.0, 0.25, 0.5, 0.75, 1.0)]
groups["sharpe_6m3m"] = [(f"w6M={w6}/w3M={1-w6}", BASE, make_rerank(w6=w6, w3=1 - w6)) for w6 in (0.0, 0.25, 0.5, 0.75, 1.0)]
groups["extra_factors"] = (
    [(f"+{f} w=0.25", BASE, make_rerank(extras={f: 0.25})) for f in ("m12_1", "r12m", "r1m", "lowvol")]
    + [(f"+{f} w=0.5", BASE, make_rerank(extras={f: 0.5})) for f in ("m12_1", "lowvol")]
)
groups["abs_filters"] = [(f"require {f}>0", BASE, make_rerank(filters=[f])) for f in ("r6m", "r3m", "r12m", "above200")]

which = sys.argv[1:] or list(groups)
rows = []
for g in which:
    print(f"\n=== {g} ===", flush=True)
    out = pd.DataFrame([evaluate(*v) for v in groups[g]])
    out.insert(0, "group", g)
    rows.append(out)
    print(out.drop(columns="group").round(3).to_string(index=False), flush=True)
pd.concat(rows).to_csv("s2_sweeps_" + ("all" if not sys.argv[1:] else "_".join(sys.argv[1:])) + ".csv", index=False)
