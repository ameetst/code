"""Stage 1: verify the harness reproduces the live baseline, then diagnose it."""
import numpy as np
import pandas as pd
from lib import Ctx, Params, run, metrics, buy_hold

pd.set_option("display.width", 200)
ctx = Ctx()
res = run(ctx, Params())
eq = res["equity"]

ref = pd.read_csv("../momaccel_live_base_equity.csv", index_col=0, parse_dates=True)["equity"]
print("max abs equity diff vs archived live-baseline run:", float((eq - ref).abs().max()))
print("final equity: harness", round(eq.iloc[-1]), " archived", round(ref.iloc[-1]))

start = ctx.first_trade
bh = buy_hold(ctx, start)
print(f"\nFirst trading date (after 260-day warm-up): {start.date()}   last: {eq.index[-1].date()}")
rows = {
    "Baseline (full span incl. idle warm-up year)": metrics(eq),
    "Baseline (from first trade)": metrics(eq, start),
    "Nifty500 buy&hold (from first trade)": metrics(bh),
}
print(pd.DataFrame(rows).T.round(4).to_string())

mid = pd.Timestamp("2022-01-01")
for lab, a, b in [("P1 2017-09..2021-12", start, mid - pd.Timedelta(days=1)), ("P2 2022-01..2026-09", mid, None)]:
    print(f"\n{lab}")
    print(pd.DataFrame({"Baseline": metrics(eq, a, b), "N500 B&H": metrics(bh, a, b)}).T.round(4).to_string())

tr = res["trades"]
sells = tr[tr.TYPE == "SELL"].copy()
sells["hold"] = (pd.to_datetime(sells.EXIT) - pd.to_datetime(sells.ENTRY)).dt.days
sells["why"] = sells.REASON.str.split("|").str[0]
print("\nExit reasons (first-listed trigger): count / avg PnL / avg hold-days / win%")
print(sells.groupby("why").agg(n=("PNL", "size"), avg_pnl=("PNL", "mean"), hold=("hold", "mean"),
                               win=("PNL", lambda x: (x > 0).mean())).round(2).to_string())
print(f"\nTotal buys {int((tr.TYPE=='BUY').sum())}, sells {len(sells)}, win-rate {(sells.PNL>0).mean():.1%}, "
      f"avg win {sells.PNL[sells.PNL>0].mean():,.0f}, avg loss {sells.PNL[sells.PNL<=0].mean():,.0f}")

inv = res["invested"].loc[start:]
risk = ctx.regime_risk_on["ema50"].reindex(ctx.dates).ffill().loc[start:]
print(f"\nAvg invested fraction {inv.mean():.1%};  fraction of days risk-OFF {(~risk).mean():.1%};  "
      f"avg invested when risk-off {inv[~risk].mean():.1%}")

yr = pd.DataFrame({"Baseline": eq.loc[start:].groupby(eq.loc[start:].index.year).last(),
                   "BH": bh.groupby(bh.index.year).last()})
yr = yr.pct_change() * 100
print("\nCalendar-year returns % (first partial year omitted):")
print(yr.round(1).to_string())

n_by_year = {}
for d in ctx.week_dates[::26]:
    f = ctx.feats[d]
    n_by_year[d.date()] = (int(f["CLOSE"].notna().sum()), int(f["SCREEN_PASS"].sum()))
print("\nUniverse size over time (ETFs with price, ETFs passing screen):")
print(pd.DataFrame(n_by_year, index=["with_price", "screen_pass"]).T.to_string())
