"""Stage 7: certify the fixed candidate vs the live baseline."""
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.dates as mdates
from dataclasses import replace
from lib import Ctx, Params, run, metrics, buy_hold, block_bootstrap_diff

pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
ctx = Ctx(); S = ctx.first_trade; MID = pd.Timestamp("2022-01-01")
BASE = Params(name="Live baseline")
CAND = Params(exit_rank=60, regime="sma200", riskoff_liquidate=True, name="Proposed")
bh = buy_hold(ctx, S)

ladder = [("0. Live baseline", BASE),
          ("1. + 200d-SMA regime, liquidate in risk-off", replace(BASE, regime="sma200", riskoff_liquidate=True)),
          ("2. + rank-exit 40 -> 60  (= Proposed)", CAND)]
ablate = [("Proposed minus regime change (EMA50, hold)", replace(CAND, regime="ema50", riskoff_liquidate=False)),
          ("Proposed minus liquidation (SMA200, hold)", replace(CAND, riskoff_liquidate=False)),
          ("Proposed with rank-exit 40 (live)", replace(CAND, exit_rank=40)),
          ("Proposed with rank-exit 100", replace(CAND, exit_rank=100)),
          ("Proposed with rank-exit OFF", replace(CAND, exit_rank=None)),
          ("Proposed + 6 slots", replace(CAND, n_slots=6)),
          ("Proposed + 8 slots", replace(CAND, n_slots=8)),
          ("Proposed + TSL 7%", replace(CAND, tsl=0.07)),
          ("Sensitivity: 200d EMA regime", replace(CAND, regime="ema200")),
          ("Sensitivity: 150d SMA regime", replace(CAND, regime="sma150")),
          ("Sensitivity: 250d SMA regime", replace(CAND, regime="sma250"))]
rows, eqs = [], {}
for label, p in ladder + ablate:
    r = run(ctx, p); e = r["equity"]; eqs[label] = e
    m, m1, m2 = metrics(e, S), metrics(e, S, MID - pd.Timedelta(days=1)), metrics(e, MID)
    tr = r["trades"]; sells = tr[tr.TYPE == "SELL"]
    rows.append({"variant": label, "CAGR": m["CAGR"], "Vol": m["Vol"], "MaxDD": m["MaxDD"], "Sharpe": m["Sharpe"],
                 "Calmar": m["Calmar"], "P1_CAGR": m1["CAGR"], "P1_Sh": m1["Sharpe"], "P2_CAGR": m2["CAGR"], "P2_Sh": m2["Sharpe"],
                 "buys": int((tr.TYPE == "BUY").sum()), "win%": (sells.PNL > 0).mean()})
T = pd.DataFrame(rows)
print(f"Evaluation window {S.date()} -> {ctx.dates[-1].date()}  (P1 to 2021-12, P2 from 2022-01)")
print("\nCUMULATIVE LADDER"); print(T.iloc[:3].round(3).to_string(index=False))
print("\nABLATIONS / SENSITIVITIES (relative to Proposed)"); print(T.iloc[3:].round(3).to_string(index=False))
T.to_csv("s7_ladder.csv", index=False)

eb, ec = eqs[ladder[0][0]], eqs[ladder[-1][0]]

# 2) costs & cash yield
print("\nTRADING-COST STRESS (one-way slippage+spread) and CASH YIELD")
rows = []
for slip in (0.0, 0.001, 0.002, 0.004):
    a = metrics(run(ctx, replace(BASE, slip=slip))["equity"], S); b = metrics(run(ctx, replace(CAND, slip=slip))["equity"], S)
    rows.append({"slip_bp": slip * 1e4, "Base CAGR": a["CAGR"], "Base Sharpe": a["Sharpe"], "Prop CAGR": b["CAGR"], "Prop Sharpe": b["Sharpe"],
                 "Prop MaxDD": b["MaxDD"]})
print(pd.DataFrame(rows).round(3).to_string(index=False))
for cr in (0.02, 0.065):
    a = metrics(run(ctx, replace(BASE, cash_rate=cr))["equity"], S); b = metrics(run(ctx, replace(CAND, cash_rate=cr))["equity"], S)
    print(f"cash yield {cr:.1%}: baseline CAGR {a['CAGR']:.2%} Sharpe {a['Sharpe']:.2f} | proposed CAGR {b['CAGR']:.2%} Sharpe {b['Sharpe']:.2f} MaxDD {b['MaxDD']:.1%}")

# 3) year by year & rolling windows
def yr(e):
    x = e.loc[S:]; y = x.groupby(x.index.year).last(); return y.pct_change()
Y = pd.DataFrame({"Baseline": yr(eb), "Proposed": yr(ec), "N500 B&H": yr(bh)}) * 100
Y["Prop-Base"] = Y.Proposed - Y.Baseline
print("\nCALENDAR YEARS % (2017 partial omitted)"); print(Y.iloc[1:].round(1).to_string())
print(f"Proposed beat baseline in {(Y['Prop-Base'].iloc[1:] > 0).sum()} of {len(Y)-1} calendar years")
wb, wc = eb.loc[S:].resample("W-FRI").last().pct_change().dropna(), ec.loc[S:].resample("W-FRI").last().pct_change().dropna()
roll = pd.DataFrame({"sh_b": wb.rolling(156).mean() / wb.rolling(156).std(), "sh_c": wc.rolling(156).mean() / wc.rolling(156).std()}).dropna() * np.sqrt(52)
print(f"Rolling 3-yr Sharpe: Proposed > Baseline in {(roll.sh_c > roll.sh_b).mean():.0%} of {len(roll)} windows "
      f"(min diff {(roll.sh_c-roll.sh_b).min():.2f}, median {(roll.sh_c-roll.sh_b).median():.2f})")

# 4) bootstrap
bs = block_bootstrap_diff(ec.loc[S:], eb.loc[S:])
print("\nPAIRED BLOCK BOOTSTRAP (8-wk blocks, 2000 resamples), Proposed minus Baseline")
print({k: round(float(v), 3) for k, v in bs.items()})

# 5) proposal diagnostics
r = run(ctx, CAND); tr = r["trades"]; sells = tr[tr.TYPE == "SELL"].copy()
sells["hold"] = (pd.to_datetime(sells.EXIT) - pd.to_datetime(sells.ENTRY)).dt.days
sells["why"] = sells.REASON.str.split("|").str[0]
print("\nPROPOSED exit reasons:"); print(sells.groupby("why").agg(n=("PNL", "size"), avg_pnl=("PNL", "mean"), hold=("hold", "mean"), win=("PNL", lambda x: (x > 0).mean())).round(2).to_string())
print(f"buys {int((tr.TYPE=='BUY').sum())} | avg invested {r['invested'].loc[S:].mean():.1%}")

# 6) chart
fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [3, 1.3]}, sharex=True)
for lab, s, col in [("Live baseline", eb, "#7f8c8d"), ("Proposed", ec, "#c0392b"), ("Nifty 500 buy & hold", bh, "#2980b9")]:
    s = s.loc[S:]; ax.plot(s.index, s / s.iloc[0] * 100, label=lab, color=col, lw=1.5)
    d = (s / s.cummax() - 1) * 100; ax2.plot(d.index, d, color=col, lw=0.9)
risk = ctx.regime_risk_on["sma200"].reindex(ctx.dates).ffill().loc[S:]
off = (~risk).astype(int); starts = off.diff().fillna(0)
for a_, b_ in zip(off.index[starts == 1], off.index[starts == -1].append(pd.DatetimeIndex([off.index[-1]]))[:int((starts == 1).sum())]):
    ax2.axvspan(a_, b_, color="#f5b7b1", alpha=0.3, lw=0)
ax.set_yscale("log"); ax.set_ylabel("Growth of 100 (log)"); ax.legend(); ax.grid(alpha=.3)
ax.set_title("Proposed vs live baseline (from first trade 2017-09-11; shaded = 200d-SMA risk-off)")
ax2.set_ylabel("Drawdown %"); ax2.grid(alpha=.3)
ax2.xaxis.set_major_locator(mdates.YearLocator()); ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
fig.tight_layout(); fig.savefig("../comparison_proposed_vs_baseline.png", dpi=140)
pd.DataFrame({"baseline": eb, "proposed": ec}).to_csv("../proposed_vs_baseline_equity.csv")
print("\nchart saved")
