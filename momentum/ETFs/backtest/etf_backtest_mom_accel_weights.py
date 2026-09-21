"""
ETF Momentum Backtest -- robustness of the MOM_ACCEL window weights.
=====================================================================================
EVALUATION ONLY -- backtest-only, NOT implemented in etf_momentum_ranking.py.

Screen: PCT_FROM_HIGH <= 25%  AND  MOM_ACCEL_z > 0   (entry-only variant, "ACCEL"
from etf_backtest_mom_accel_filter.py), where

    raw = w1*Sharpe_1M + w3*Sharpe_3M - w6*Sharpe_6M      (rf=0; 21/63/126 days)

z-scored across the 52wk-high-pass pool and required > 0. Because of the z-score
and the 0 threshold, only weight RATIOS matter, so w3 is fixed at 1 and the grid
sweeps w1 x w6. (1,1,1) is the variant already backtested; (1,1,0.5) is the
0.4/0.4/-0.2 ratio quoted in etf_momentum_weekly.py.

Per-week Sharpes/rankings are computed once and cached, then every weight combo
reuses them on the identical engine (binary Nifty500>50EMA regime, weekly
hold-and-replace, top 5, sector cap 1, exits 52wk DD>25% / rank>EXIT_MAX_RANK /
TSL 5%).

Usage:
  python etf_backtest_mom_accel_weights.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

_BACKTEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKTEST_DIR))

import etf_backtest_rank_cutoff_fixed_vs_pct as rc                     # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean   # noqa: E402

bt, emr, CONFIG = rc.bt, rc.emr, rc.CONFIG
WINDOW_1M = 21

W1_GRID = [0.0, 0.5, 1.0, 1.5, 2.0]
W6_GRID = [0.0, 0.5, 1.0, 1.5, 2.0]

_CACHE: dict = {}


def _zscore(s: pd.Series) -> pd.Series:
    mu, sig = s.mean(), s.std()
    if sig == 0 or np.isnan(sig):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sig


def _base(meta, hist):
    """Live ranking + raw rf=0 Sharpes for 1M/3M/6M, cached per as-of date."""
    key = hist.index[-1]
    if key not in _CACHE:
        df = emr.build_ranking(meta, hist)
        if not df.empty:
            sh = {"S1": [], "S3": [], "S6": []}
            for t in df["TICKER"]:
                s = hist[t] if t in hist.columns else pd.Series(dtype=float)
                sh["S1"].append(emr.sharpe_score(s, WINDOW_1M, daily_rf=0.0))
                sh["S3"].append(emr.sharpe_score(s, CONFIG.WINDOW_3M, daily_rf=0.0))
                sh["S6"].append(emr.sharpe_score(s, CONFIG.WINDOW_6M, daily_rf=0.0))
            for k, v in sh.items():
                df[k] = v
        _CACHE[key] = df
    return _CACHE[key].copy()


def make_ranking_fn(w1, w3, w6):
    def ranking_fn(meta, hist):
        df = _base(meta, hist)
        if df.empty:
            return df
        s1, s3, s6 = df["S1"], df["S3"], df["S6"]
        raw = w1 * s1 + w3 * s3 - w6 * s6.fillna(0.0)
        raw[s1.isna() | s3.isna()] = np.nan
        pool = df["SCREEN_PASS"].astype(bool)
        z = pd.Series(np.nan, index=df.index)
        if pool.sum() > 0:
            z[pool] = _zscore(raw[pool])
        final = pool & (z > 0)
        new_rank = pd.Series(np.nan, index=df.index)
        new_rank[final] = df.loc[final, "RANK_INVESTABLE"].rank(method="first")
        df["RANK_INVESTABLE"] = new_rank
        df["SCREEN_PASS"] = final
        return df.drop(columns=["S1", "S3", "S6"])
    return ranking_fn


def summarise(res, mid):
    eq = res["equity"]["equity"]
    m = rc.compute_metrics(eq)
    h1 = rc.compute_metrics(eq.loc[:mid])
    h2 = rc.compute_metrics(eq.loc[mid:])
    tr = res["trades"]
    sells = tr[tr["TYPE"] == "SELL"] if len(tr) else tr
    return {
        "CAGR": m["cagr"], "MaxDD": m["max_dd"], "Vol": m["vol"], "Sharpe": m["sharpe"],
        "Buys": int((tr["TYPE"] == "BUY").sum()) if len(tr) else 0,
        "WinRate": float((sells["NET_PNL"] > 0).mean()) if len(sells) else np.nan,
        "CAGR_H1": h1["cagr"], "CAGR_H2": h2["cagr"],
        "Sharpe_H1": h1["sharpe"], "Sharpe_H2": h2["sharpe"],
    }


def main():
    meta, prices_raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, _ = detect_and_clean(prices_raw)
    all_dates = prices.index
    mid = all_dates[len(all_dates) // 2]
    regime_s, ema50, _ = bt.fetch_benchmark_series(all_dates)
    print(f"\nSplit-half boundary: {mid.date()}")

    rows = []

    print("\n[run] BASELINE (52wk-high screen only)")
    res = rc.run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed", ranking_fn=_base)
    base = summarise(res, mid)
    rows.append({"w1": np.nan, "w3": np.nan, "w6": np.nan, "label": "BASELINE", **base})

    total = len(W1_GRID) * len(W6_GRID)
    n = 0
    for w1 in W1_GRID:
        for w6 in W6_GRID:
            n += 1
            if w1 == 0 and w6 == 0:
                pass  # pure 3M Sharpe -- still a valid variant
            res = rc.run_strategy_rank_cutoff(meta, prices, regime_s, ema50, mode="fixed",
                                              ranking_fn=make_ranking_fn(w1, 1.0, w6))
            r = summarise(res, mid)
            rows.append({"w1": w1, "w3": 1.0, "w6": w6, "label": f"({w1:g},1,{w6:g})", **r})
            print(f"[{n:>2}/{total}] w1={w1:<3g} w6={w6:<3g} CAGR={r['CAGR']:.2%} "
                  f"MaxDD={r['MaxDD']:.1%} Sharpe={r['Sharpe']:.2f} buys={r['Buys']}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(_BACKTEST_DIR / "momaccel_weights_results.csv", index=False)
    grid = out[out["label"] != "BASELINE"].copy()

    print(f"\n{'='*78}\n  BASELINE: CAGR={base['CAGR']:.2%}  MaxDD={base['MaxDD']:.1%}  "
          f"Vol={base['Vol']:.1%}  Sharpe={base['Sharpe']:.2f}  Buys={base['Buys']}\n{'='*78}")

    for metric, fmt in [("CAGR", "{:.2%}"), ("MaxDD", "{:.1%}"), ("Sharpe", "{:.2f}"), ("Buys", "{:.0f}")]:
        pv = grid.pivot(index="w1", columns="w6", values=metric)
        print(f"\n  {metric}   (rows = w1 [1M weight], cols = w6 [6M weight], w3 = 1)")
        print(pv.map(lambda v: fmt.format(v)).to_string())

    ref = grid[(grid.w1 == 1.0) & (grid.w6 == 1.0)].iloc[0]
    print(f"\n  Reference (1,1,1): CAGR={ref['CAGR']:.2%} MaxDD={ref['MaxDD']:.1%} Sharpe={ref['Sharpe']:.2f}")

    print(f"\n{'-'*78}\n  Robustness vs BASELINE across {len(grid)} weight combos")
    for col, better in [("CAGR", "higher"), ("Sharpe", "higher"), ("MaxDD", "shallower")]:
        wins = (grid[col] > base[col]).sum()
        print(f"    {col:<7} better in {wins:>2}/{len(grid)} combos   "
              f"min={grid[col].min():.3f}  median={grid[col].median():.3f}  max={grid[col].max():.3f}"
              f"   (baseline {base[col]:.3f})")
    for h in ["H1", "H2"]:
        w = (grid[f"CAGR_{h}"] > base[f"CAGR_{h}"]).sum()
        w2 = (grid[f"Sharpe_{h}"] > base[f"Sharpe_{h}"]).sum()
        print(f"    split-half {h}: CAGR better in {w}/{len(grid)}, Sharpe better in {w2}/{len(grid)}   "
              f"(baseline CAGR {base['CAGR_'+h]:.2%}, Sharpe {base['Sharpe_'+h]:.2f})")
    both = ((grid["Sharpe_H1"] > base["Sharpe_H1"]) & (grid["Sharpe_H2"] > base["Sharpe_H2"])).sum()
    print(f"    Sharpe better in BOTH halves: {both}/{len(grid)} combos")

    print("\n  Top 5 by Sharpe:")
    print(grid.sort_values("Sharpe", ascending=False).head(5)[
        ["label", "CAGR", "MaxDD", "Sharpe", "Buys", "Sharpe_H1", "Sharpe_H2"]].round(3).to_string(index=False))
    print("\n  Bottom 5 by Sharpe:")
    print(grid.sort_values("Sharpe").head(5)[
        ["label", "CAGR", "MaxDD", "Sharpe", "Buys", "Sharpe_H1", "Sharpe_H2"]].round(3).to_string(index=False))

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    for ax, (metric, title, cmap, fmt) in zip(axes, [
            ("CAGR", "CAGR", "RdYlGn", "{:.1%}"),
            ("Sharpe", "Sharpe (CAGR/vol)", "RdYlGn", "{:.2f}"),
            ("MaxDD", "Max drawdown", "RdYlGn", "{:.0%}")]):
        pv = grid.pivot(index="w1", columns="w6", values=metric)
        im = ax.imshow(pv.values, cmap=cmap, aspect="auto", origin="lower")
        ax.set_xticks(range(len(pv.columns)), [f"{c:g}" for c in pv.columns])
        ax.set_yticks(range(len(pv.index)), [f"{i:g}" for i in pv.index])
        ax.set_xlabel("w6 (6M weight, subtracted)")
        ax.set_ylabel("w1 (1M weight)")
        ax.set_title(f"{title}  [baseline {fmt.format(base[metric])}]")
        for i in range(pv.shape[0]):
            for j in range(pv.shape[1]):
                ax.text(j, i, fmt.format(pv.values[i, j]), ha="center", va="center", fontsize=8)
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle("MOM_ACCEL weight sensitivity: w1*Sh1M + 1*Sh3M - w6*Sh6M  (entry-only screen)")
    fig.tight_layout()
    fig.savefig(_BACKTEST_DIR / "momaccel_weights_heatmap.png", dpi=140)
    print(f"\nHeatmap -> {_BACKTEST_DIR / 'momaccel_weights_heatmap.png'}")


if __name__ == "__main__":
    main()
