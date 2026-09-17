"""
phase1_check_premise.py
=========================
PROTOTYPE — standalone, does NOT touch live code.

Quick sanity check (not a hard gate) on whether the paper's core claim holds
on this repo's own data before the risk-on/risk-off gate is wired into the
backtest: do Bull/Rebound months show better forward NIFTY500 returns than
Bear/Correction months?

Data: same file backtest_turning_points.py will use.
Output: prototype_turning_points/phase1_output/{state_summary.csv,
        conditional_returns.png}
"""

import os
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import momentum_lib as ml
import turning_points_lib as tpl

FILE = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated.xlsx"
OUT_DIR = os.path.join(os.path.dirname(__file__), "phase1_output")
os.makedirs(OUT_DIR, exist_ok=True)

print(f"Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

state_df = tpl.build_state_series(nifty_series, dates)
valid = state_df.dropna(subset=["state"]).copy()

# Forward 1-month return: next month's `ret`, aligned to the CURRENT month's state
valid["fwd_ret"] = valid["ret"].shift(-1)
valid = valid.dropna(subset=["fwd_ret"])

print(f"\nUsable monthly observations (state + forward return both available): {len(valid)}")

# ── 1. State frequency table ───────────────────────────────────────────────
counts = valid["state"].value_counts().reindex(tpl.STATES).fillna(0).astype(int)
pct = (counts / counts.sum() * 100).round(1)
freq_table = pd.DataFrame({"count": counts, "pct": pct})
print("\n--- State frequency (this data) vs paper (US 1969-2018) ---")
paper_pct = {"Bull": 48.3, "Correction": 24.5, "Bear": 16.7, "Rebound": 10.5}
freq_table["paper_pct"] = [paper_pct[s] for s in freq_table.index]
print(freq_table)

for s in tpl.STATES:
    if counts[s] < 8:
        print(f"  [!] {s}: only {counts[s]} occurrences — too sparse to trust beyond direction.")

# ── 2. State-conditional forward return ────────────────────────────────────
cond = valid.groupby("state")["fwd_ret"].agg(["mean", "std", "count"])
cond["mean_annlzd_%"] = (cond["mean"] * 12 * 100).round(2)
cond["std_annlzd_%"] = (cond["std"] * np.sqrt(12) * 100).round(2)
cond = cond.reindex(tpl.STATES)
print("\n--- Forward 1-month NIFTY500 return, conditioned on current-month state ---")
print(cond)

bull_mean = cond.loc["Bull", "mean"]
bear_mean = cond.loc["Bear", "mean"]
corr_mean = cond.loc["Correction", "mean"]
reb_mean = cond.loc["Rebound", "mean"]

print("\n--- Does the risk-on/risk-off grouping hold directionally? ---")
print(f"  Bull ({bull_mean:+.4f}) vs Correction ({corr_mean:+.4f}): "
      f"{'OK - Bull > Correction' if bull_mean > corr_mean else '[!] MISMATCH - Bull <= Correction'}")
print(f"  Rebound ({reb_mean:+.4f}) vs Bear ({bear_mean:+.4f}): "
      f"{'OK - Rebound > Bear' if reb_mean > bear_mean else '[!] MISMATCH - Rebound <= Bear'}")

risk_on_mean = valid.loc[valid["risk_on"] == True, "fwd_ret"].mean()
risk_off_mean = valid.loc[valid["risk_on"] == False, "fwd_ret"].mean()
print(f"\n  Risk-ON (Bull+Rebound) avg forward return:  {risk_on_mean:+.4f}  (n={int((valid['risk_on']==True).sum())})")
print(f"  Risk-OFF (Bear+Correction) avg forward return: {risk_off_mean:+.4f}  (n={int((valid['risk_on']==False).sum())})")
print(f"  {'OK - risk-on grouping supported' if risk_on_mean > risk_off_mean else '[!] risk-on grouping NOT supported on this data'}")

# ── 3. Transition matrix (diagnostic, quick) ───────────────────────────────
valid["next_state"] = valid["state"].shift(-1)
trans = pd.crosstab(valid["state"], valid["next_state"], normalize="index").round(2)
trans = trans.reindex(index=tpl.STATES, columns=tpl.STATES)
print("\n--- State transition matrix P(next_state | current_state) ---")
print(trans)

# ── Save outputs ────────────────────────────────────────────────────────────
freq_table.to_csv(os.path.join(OUT_DIR, "state_summary.csv"))
cond.to_csv(os.path.join(OUT_DIR, "conditional_returns.csv"))

fig, ax = plt.subplots(figsize=(8, 5))
colors = {"Bull": "#2E7D32", "Rebound": "#66BB6A", "Correction": "#EF6C00", "Bear": "#C62828"}
bars = ax.bar(cond.index, cond["mean_annlzd_%"], yerr=cond["std_annlzd_%"] / np.sqrt(cond["count"]),
               color=[colors[s] for s in cond.index], capsize=5)
ax.axhline(0, color="black", lw=0.8)
ax.set_ylabel("Avg forward 1-month return (annualized %)")
ax.set_title(f"NIFTY500 forward return by cycle state\n(n={len(valid)} months, "
             f"{valid.index[0].strftime('%b %Y')}-{valid.index[-1].strftime('%b %Y')})")
for i, s in enumerate(cond.index):
    ax.text(i, cond["mean_annlzd_%"].iloc[i], f"n={int(cond['count'].iloc[i])}",
            ha="center", va="bottom" if cond["mean_annlzd_%"].iloc[i] >= 0 else "top", fontsize=9)
plt.tight_layout()
out_png = os.path.join(OUT_DIR, "conditional_returns.png")
plt.savefig(out_png, dpi=150)
plt.close()

print(f"\nSaved: {os.path.join(OUT_DIR, 'state_summary.csv')}")
print(f"Saved: {os.path.join(OUT_DIR, 'conditional_returns.csv')}")
print(f"Saved: {out_png}")
