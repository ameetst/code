"""Stage 6: expanding-window walk-forward. Each calendar year, pick the best config by TRAILING Sharpe from a
broad family (includes the live baseline design), apply it to the next year only, chain the out-of-sample returns."""
import itertools, pickle
import numpy as np, pandas as pd
from concurrent.futures import ProcessPoolExecutor
from lib import Ctx, Params, run, metrics

_ctx = None


def _init():
    global _ctx
    _ctx = Ctx()


def work(cfg):
    reg, liq, s, rk, t = cfg
    p = Params(n_slots=s, exit_rank=rk, tsl=t, regime=reg, riskoff_liquidate=liq)
    return cfg, run(_ctx, p)["equity"]


if __name__ == "__main__":
    fam = list(itertools.product(
        [("ema50", False), ("ema50", True), ("sma200", True), ("ema200", True), ("sma100", True), ("ema150", True)],
        [5, 6, 8], [40, 60, None], [0.05, 0.07, 0.10]))
    cfgs = [(r, l, s, rk, t) for (r, l), s, rk, t in fam]
    with ProcessPoolExecutor(max_workers=4, initializer=_init) as ex:
        res = dict(ex.map(work, cfgs, chunksize=4))
    pickle.dump(res, open("s6_family.pkl", "wb"))
    print(len(res), "configs")

    ctx = Ctx(); S = ctx.first_trade
    R = pd.DataFrame({k: v.loc[S:].pct_change() for k, v in res.items()}).iloc[1:]
    base_key = ("ema50", False, 5, 40, 0.05)
    years = sorted(set(R.index.year))
    oos, picks = [], []
    for y in years:
        hist = R[R.index < pd.Timestamp(f"{y}-01-01")]
        if len(hist) < 3 * 252:
            continue
        sh = hist.mean() / hist.std()
        best = sh.idxmax()
        oos.append(R.loc[R.index.year == y, best]); picks.append((y, best, round(float(sh.max() * np.sqrt(252)), 2)))
    oos = pd.concat(oos)
    b = R.loc[oos.index, base_key]
    def summ(r):
        yrs = len(r) / 252
        cagr = (1 + r).prod() ** (1 / yrs) - 1; vol = r.std() * np.sqrt(252); dd = ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
        return {"CAGR": cagr, "Vol": vol, "MaxDD": dd, "Sharpe": cagr / vol}
    print(f"\nOOS window {oos.index[0].date()} -> {oos.index[-1].date()}  (selected each Jan 1 on trailing Sharpe)")
    for y, k, s in picks:
        print(f"  {y}: picked {k}   trailing in-sample Sharpe {s}")
    cand = ("sma200", True, 6, 60, 0.07)
    print(pd.DataFrame({"Walk-forward OOS": summ(oos), "Live baseline": summ(b), "Fixed candidate (sma200/6/60/7%)": summ(R.loc[oos.index, cand])}).T.round(3).to_string())
    rank_of = (R.loc[R.index.year >= years[3]].mean() / R.loc[R.index.year >= years[3]].std()).rank(ascending=False)
    print("\nrank of live baseline among 162 configs over the OOS window:", int(rank_of[base_key]), " | candidate:", int(rank_of[cand]))
