"""Stage 4a: is the 200-day regime result a plateau or a spike? Plus episode count."""
import numpy as np, pandas as pd
from dataclasses import replace
from lib import Ctx, Params, run, metrics

pd.set_option("display.width", 220)
ctx = Ctx(); S = ctx.first_trade; MID = pd.Timestamp("2022-01-01")
base = Params(exit_rank=None)          # rank-exit off, everything else live

rows = []
for reg in ["ema50", "ema100", "ema150", "ema200", "ema250", "ema300", "sma100", "sma150", "sma200", "sma250"]:
    for liq in (False, True):
        p = replace(base, regime=reg, riskoff_liquidate=liq)
        e = run(ctx, p)["equity"]
        m, m1, m2 = metrics(e, S), metrics(e, S, MID - pd.Timedelta(days=1)), metrics(e, MID)
        es = run(ctx, replace(p, slip=0.002))["equity"]
        rows.append({"regime": reg, "liquidate": liq, "CAGR": m["CAGR"], "MaxDD": m["MaxDD"], "Sharpe": m["Sharpe"],
                     "Calmar": m["Calmar"], "P1_Sh": m1["Sharpe"], "P2_Sh": m2["Sharpe"], "Sh@20bp": metrics(es, S)["Sharpe"]})
d = pd.DataFrame(rows)
print("Regime lookback neighbourhood (rank-exit OFF, 5 slots, TSL 5%):")
print(d.round(3).to_string(index=False))

# episodes: contiguous risk-off spans for ema200 over the trading window
r = ctx.regime_risk_on["ema200"].reindex(ctx.dates).ffill().loc[S:]
off = ~r
grp = (off != off.shift()).cumsum()
ep = [(g.index[0].date(), g.index[-1].date(), len(g)) for _, g in off[off].groupby(grp[off])]
print(f"\nema200 risk-off episodes after {S.date()}: {len(ep)}  (days off: {int(off.sum())}/{len(off)} = {off.mean():.1%})")
e_nl = run(ctx, replace(base, regime="ema200", riskoff_liquidate=False))["equity"]
e_l = run(ctx, replace(base, regime="ema200", riskoff_liquidate=True))["equity"]
nif = ctx.regime
print(f"{'start':<12}{'end':<12}{'days':>5}{'Nifty500 chg':>14}{'hold(no liq)':>14}{'liquidate':>11}")
for a, b, n in ep:
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    f = lambda s: s.loc[b] / s.loc[a] - 1
    print(f"{str(a.date()):<12}{str(b.date()):<12}{n:>5}{f(nif):>14.1%}{f(e_nl):>14.1%}{f(e_l):>11.1%}")
