"""
SANDBOX simulation - no live code touched. Re-runs the earlier regime-filter variants
under both blocked-week conventions:
  cash : earlier-backtest convention - blocked week = whole portfolio earns cash
  hold : live behaviour - existing positions kept under normal exit rules
         (rank<=40 / 28-day lock); only NEW buys stop

Variants (weights, momentum-breadth bar on normalised COMPOSITE, entry gate, full-N score):
  live        35/25/25/15  1.5  0.40 / 0.75
  eqw         25/25/25/25  1.5  0.40 / 0.75
  thr1        35/25/25/15  1.0  0.40 / 0.75
  g035_066    35/25/25/15  1.5  0.35 / 0.66
  eqw_g       25/25/25/25  1.5  0.35 / 0.66
  thr1_g      35/25/25/15  1.0  0.35 / 0.66
  combined    25/25/25/25  1.0  0.35 / 0.66
Rankings, COMPOSITE and eligibility are computed once per week and shared, so variants
differ ONLY by the regime score / gate / dynamic N.  Validation: live/cash must reproduce
the entry-gate run (40.8% / -18.7%) and live/hold 36.2% / -22.7%.
"""
import sys, os, datetime, warnings
import numpy as np
import pandas as pd
from contextlib import contextmanager

SHARPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SHARPE_ROOT)
import momentum_lib as ml

warnings.filterwarnings("ignore")

FILE = sys.argv[1]
TAG = sys.argv[2] if len(sys.argv) > 2 else "run"
RFR_ANNUAL, TRADING_DAYS, FRICTION = 0.07, 252, 0.002
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily = RFR_ANNUAL / TRADING_DAYS
MIN_N, MAX_N = 5, 25
CASH_WK = (1.06 ** (1 / 52)) - 1.0
LIVE_W = (0.35, 0.25, 0.25, 0.15)
EQ_W = (0.25, 0.25, 0.25, 0.25)
VARIANTS = {
    "live":     dict(w=LIVE_W, thr=1.5, gate=0.40, full=0.75),
    "eqw":      dict(w=EQ_W,   thr=1.5, gate=0.40, full=0.75),
    "thr1":     dict(w=LIVE_W, thr=1.0, gate=0.40, full=0.75),
    "g035_066": dict(w=LIVE_W, thr=1.5, gate=0.35, full=0.66),
    "eqw_g":    dict(w=EQ_W,   thr=1.5, gate=0.35, full=0.66),
    "thr1_g":   dict(w=LIVE_W, thr=1.0, gate=0.35, full=0.66),
    "combined": dict(w=EQ_W,   thr=1.0, gate=0.35, full=0.66),
}
MODES = ["cash", "hold"]


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as dn:
        old = sys.stdout
        sys.stdout = dn
        try:
            yield
        finally:
            sys.stdout = old


print(f"Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
assert set(prices_df.index) == set(stock_tickers)
tickers = list(prices_df.index)
trow = {t: i for i, t in enumerate(tickers)}
ncol = prices_df.shape[1]

dt_idx = pd.DatetimeIndex(dates)
eow = [dates[i] for i in range(len(dt_idx) - 1)
       if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
eow.append(dates[-1])
valid_dates = [d for d in eow if dates.index(d) >= 252]
print(f"Rebalance points: {len(valid_dates)}")

P = prices_df.values
E50 = prices_df.T.ewm(span=50, adjust=False).mean().T.values
E200 = prices_df.T.ewm(span=200, adjust=False).mean().T.values
pos = np.where(~np.isnan(P), np.arange(ncol)[None, :], -1)
LVP = np.maximum.accumulate(pos, axis=1)
PF = prices_df.ffill(axis=1).values
nifty_ff = nifty_series.ffill()

vol_cache = {}
def get_vol(ticker, idx, sliced):
    key = (ticker, idx)
    if key in vol_cache:
        return vol_cache[key]
    px = sliced.loc[ticker].dropna()
    out = None
    if len(px) > 10:
        vols = []
        for w in [252, 189, 126, 63]:
            pw = px.iloc[-w:] if len(px) >= w else px
            lr = np.diff(np.log(pw.values))
            if len(lr) > 5:
                vols.append(np.std(lr, ddof=1) * np.sqrt(252))
        if vols and np.mean(vols) > 0:
            out = float(np.mean(vols))
    vol_cache[key] = out
    return out


def new_state():
    return {"port": {}, "equity": 2_000_000.0, "nifty": 2_000_000.0, "log": []}

states = {(v, m): new_state() for v in VARIANTS for m in MODES}
sig_rows = []

print("Running ...")
for i in range(len(valid_dates) - 1):
    t_date, next_date = valid_dates[i], valid_dates[i + 1]
    idx, nidx = dates.index(t_date), dates.index(next_date)
    sliced = prices_df.iloc[:, :idx + 1]

    with suppress_stdout():
        _, z_df = ml.compute_sharpe(sliced, stock_tickers, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct52 = ml.compute_pct_from_52h(sliced, stock_tickers)
    comp = z_df[[f"Z_{l}" for l in SHARPE_WINDOWS]].mean(axis=1).map(ml.normalise_composite)

    elig_mask = pct52 >= -25
    elig_comp = comp[elig_mask]
    rank = elig_comp.rank(ascending=False, method="first", na_option="bottom").sort_values()
    top_candidates = rank.index.tolist()
    rank_map = rank.to_dict()
    comp_map = comp.to_dict()
    n_elig = int(elig_mask.sum())

    s_breadth = n_elig / len(elig_mask)
    mom = {thr: float((elig_comp > thr).sum()) / max(1, n_elig) for thr in (1.5, 1.0)}
    lv = LVP[:, idx]
    ok = lv >= 0
    rows, cols = np.where(ok)[0], lv[ok]
    e50, e200, pl = E50[rows, cols], E200[rows, cols], P[rows, cols]
    s_ema50 = float((pl > e50).sum()) / ok.sum()
    s_trend = float((e50 > e200).sum()) / ok.sum()
    sig_rows.append({"date": t_date, "ema50": s_ema50, "trend": s_trend, "breadth": s_breadth,
                     "mom15": mom[1.5], "mom10": mom[1.0]})

    nifty_ret = nifty_ff.iloc[nidx] / nifty_ff.iloc[idx] - 1.0

    for (vname, mode), st in states.items():
        cfg = VARIANTS[vname]
        w = cfg["w"]
        score = w[0] * s_ema50 + w[1] * s_trend + w[2] * s_breadth + w[3] * mom[cfg["thr"]]
        allow_new = score >= cfg["gate"]
        dyn_n = int(MIN_N + min(score / cfg["full"], 1.0) * (MAX_N - MIN_N))
        cur = st["port"]
        nxt = []
        for tk, s in cur.items():
            if tk in rank_map:
                if rank_map[tk] <= 40 or (t_date - s["entry_date"]).days < 28:
                    nxt.append(tk)
        if allow_new:
            slots = dyn_n - len(nxt)
            for tk in top_candidates:
                if slots <= 0:
                    break
                if tk not in nxt:
                    nxt.append(tk)
                    slots -= 1

        raw_w = {}
        for tk in nxt:
            mv = get_vol(tk, idx, sliced)
            raw_w[tk] = comp_map[tk] / mv if mv else comp_map[tk]
        tot = sum(raw_w.values())
        actual = {}
        for tk in nxt:
            nw = raw_w[tk] / tot if tot > 0 else 1.0 / len(nxt)
            actual[tk] = {"entry_date": cur[tk]["entry_date"] if tk in cur else t_date,
                          "weight": min(0.05, nw)}

        eq_w = 0.0
        if mode == "cash" and not allow_new:
            gross = CASH_WK
        else:
            if actual:
                r_ = np.array([trow[t] for t in actual])
                wts = np.array([actual[t]["weight"] for t in actual])
                rets = PF[r_, nidx] / PF[r_, idx] - 1.0
                cash_w = max(0.0, 1.0 - wts.sum())
                gross = float(np.nansum(rets * wts)) + cash_w * CASH_WK
                eq_w = float(wts.sum())
            else:
                gross = CASH_WK
            if pd.isna(gross):
                gross = 0.0

        chg = 0.0
        for tk in set(cur) | set(actual):
            chg += abs((actual[tk]["weight"] if tk in actual else 0.0) -
                       (cur[tk]["weight"] if tk in cur else 0.0))
        net = gross - chg * FRICTION
        st["equity"] *= 1 + net
        st["nifty"] *= 1 + nifty_ret
        st["log"].append({"date": t_date, "score": score, "allow_new": allow_new, "dyn_n": dyn_n,
                          "held": len(actual), "eq_w": eq_w, "turnover": chg / 2 * 100,
                          "gross": gross, "net": net, "nifty_ret": nifty_ret,
                          "equity": st["equity"], "nifty_equity": st["nifty"]})
        st["port"] = actual

    sys.stdout.write(f"\r  [{i+1}/{len(valid_dates)-1}] {t_date}")
    sys.stdout.flush()

print()
ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"run_regvariants_{TAG}_{ts}")
os.makedirs(out, exist_ok=True)
pd.DataFrame(sig_rows).to_csv(os.path.join(out, "signals.csv"), index=False)
years = (valid_dates[-1] - valid_dates[0]).days / 365.25
for (vname, mode), st in states.items():
    df = pd.DataFrame(st["log"])
    df.to_csv(os.path.join(out, f"res_{vname}_{mode}.csv"), index=False)
    cagr = ((st["equity"] / 2e6) ** (1 / years) - 1) * 100
    mdd = ((df["equity"] / df["equity"].cummax()) - 1).min() * 100
    sh = (df["net"].mean() - CASH_WK) / df["net"].std(ddof=1) * np.sqrt(52)
    print(f"{mode:5s} {vname:9s}: CAGR {cagr:5.1f}%  MDD {mdd:6.1f}%  Sharpe {sh:4.2f}  "
          f"blocked {(~df['allow_new']).sum():3d}  avgN {df['held'].mean():4.1f}  final {st['equity']:,.0f}")
print("Saved to", out)
