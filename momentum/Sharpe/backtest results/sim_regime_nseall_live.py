"""
SANDBOX simulation - no live code touched. Regime-filter variants on the NSEAll universe
with the live-faithful engine of backtest_live_baseline_nseall.py:
  - momentum_lib.compute_universe_rankings() once per week (turnover filter 1.0 Cr, 52H gate)
  - live exits: EXIT_52H / EXIT_FILTER / EXIT_REL_DD (-20, 28d lock) / EXIT_RANK (>75, 28d lock)
  - entry = top dynamic_n by RANK not already held (no backfill); position cap 1/MAX_N
  - blocked week ("hold" mode): exits still run, only new buys stop  (= live behaviour)
  - "cash" mode (earlier convention): blocked week earns the cash rate on the whole book
Weekly ranking outputs are cached once and shared, so variants differ ONLY by regime
score / gate / dynamic N.  Not modelled: min CMP / min market-cap entry flags.
Validation: live / hold / MAX_N=25 must reproduce the earlier live baseline run.
"""
import sys, os, datetime, warnings, pickle
import numpy as np
import pandas as pd
from contextlib import contextmanager

SHARPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SHARPE_ROOT)
import momentum_lib as ml

warnings.filterwarnings("ignore")

FILE = sys.argv[1]
TAG = sys.argv[2] if len(sys.argv) > 2 else "nseall"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, f".cache_regnseall_{TAG}.pkl")

RFR_ANNUAL, TRADING_DAYS, FRICTION = 0.07, 252, 0.002
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
MIN_N = 5
MIN_TURNOVER_CR, REL_DD, HOLD_BUF, MIN_HOLD = 1.0, -20, 75, 28
CASH_WK = (1.06 ** (1 / 52)) - 1.0
LIVE_W = {"ema50_breadth": 0.35, "ema_trend_breadth": 0.25, "breadth": 0.25, "momentum": 0.15}
LW = (0.35, 0.25, 0.25, 0.15)
EW = (0.25, 0.25, 0.25, 0.25)
VARIANTS = {
    "live":     dict(w=LW, thr=1.5, gate=0.40, full=0.75),
    "gate025":  dict(w=LW, thr=1.5, gate=0.25, full=0.75),
    "nogate":   dict(w=LW, thr=1.5, gate=0.00, full=0.75),
    "eqw":      dict(w=EW, thr=1.5, gate=0.40, full=0.75),
    "thr1":     dict(w=LW, thr=1.0, gate=0.40, full=0.75),
    "g035_066": dict(w=LW, thr=1.5, gate=0.35, full=0.66),
    "eqw_g":    dict(w=EW, thr=1.5, gate=0.35, full=0.66),
    "thr1_g":   dict(w=LW, thr=1.0, gate=0.35, full=0.66),
    "combined": dict(w=EW, thr=1.0, gate=0.35, full=0.66),
}
# (variant, mode, max_n)
RUNS = [(v, m, 30) for v in VARIANTS for m in ("hold", "cash")] + [("live", "hold", 25)]


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
volume_df = ml.load_volume(FILE)
print(f"Tickers {len(stock_tickers)}, dates {len(dates)} ({dates[0]} .. {dates[-1]}), "
      f"volume {'loaded' if volume_df is not None else 'MISSING'}")
dt_idx = pd.DatetimeIndex(dates)
eow = [dates[i] for i in range(len(dt_idx) - 1)
       if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
eow.append(dates[-1])
valid_dates = [d for d in eow if dates.index(d) >= 252]
print(f"Rebalance points: {len(valid_dates)}")
pf = prices_df.ffill(axis=1)
nifty_ff = nifty_series.ffill()

# ---------- phase 1: weekly ranking cache (resumable) ----------
cache = {}
if os.path.exists(CACHE):
    with open(CACHE, "rb") as f:
        cache = pickle.load(f)
    print(f"Loaded cache with {len(cache)} weeks")
KEEP = ["RANK", "COMPOSITE", "PCT_FROM_52H", "REL_52H_DD"]
t0 = datetime.datetime.now()
for i in range(len(valid_dates) - 1):
    t_date = valid_dates[i]
    if t_date in cache:
        continue
    idx = dates.index(t_date)
    sp = prices_df.iloc[:, :idx + 1]
    sv = volume_df.iloc[:, :idx + 1] if volume_df is not None else None
    with suppress_stdout():
        result, rs, det = ml.compute_universe_rankings(
            sp, nifty_series.iloc[:idx + 1], stock_tickers, volume_df=sv,
            min_turnover_cr=MIN_TURNOVER_CR, eq_series_filter=False,
            circuit_filter_enabled=False, circuit_threshold=20,
            stockdb_csv_path=os.path.join(SHARPE_ROOT, "STOCKDB.csv"),
            windows=SHARPE_WINDOWS, trading_days=TRADING_DAYS, rfr_annual=RFR_ANNUAL,
            signal_weights=LIVE_W, min_n=MIN_N, max_n=30, new_entry_threshold=0.40)
    res = result[KEEP].copy()
    pct_mask = res["PCT_FROM_52H"] >= -25
    n_elig = int(pct_mask.sum())
    ec = res.loc[pct_mask, "COMPOSITE"]
    cache[t_date] = {
        "res": res,
        "ema50": det["ema50_score"], "trend": det["ema_trend_score"],
        "breadth": det["breadth_score"], "rs_live": rs,
        "mom": {1.5: float((ec > 1.5).sum()) / max(1, n_elig),
                1.0: float((ec > 1.0).sum()) / max(1, n_elig)},
    }
    if (i + 1) % 20 == 0:
        with open(CACHE + ".tmp", "wb") as f:
            pickle.dump(cache, f)
        os.replace(CACHE + ".tmp", CACHE)
    el = (datetime.datetime.now() - t0).total_seconds()
    sys.stdout.write(f"\r  rank [{i+1}/{len(valid_dates)-1}] {t_date} elapsed {el:6.0f}s")
    sys.stdout.flush()
with open(CACHE + ".tmp", "wb") as f:
    pickle.dump(cache, f)
os.replace(CACHE + ".tmp", CACHE)
print("\nRanking cache complete")

# ---------- phase 2: strategies ----------
trow = {t: i for i, t in enumerate(prices_df.index)}
PF = pf.values
vol_cache = {}
def get_vol(tk, idx):
    key = (tk, idx)
    if key in vol_cache:
        return vol_cache[key]
    px = prices_df.loc[tk].iloc[:idx + 1].dropna()
    out = None
    if len(px) > 10:
        vols = []
        for w in (252, 189, 126, 63):
            pw = px.iloc[-w:] if len(px) >= w else px
            lr = np.diff(np.log(pw.values))
            if len(lr) > 5:
                vols.append(np.std(lr, ddof=1) * np.sqrt(252))
        if vols and np.mean(vols) > 0:
            out = float(np.mean(vols))
    vol_cache[key] = out
    return out

states = {r: {"port": {}, "equity": 2_000_000.0, "nifty": 2_000_000.0, "log": []} for r in RUNS}
for i in range(len(valid_dates) - 1):
    t_date, next_date = valid_dates[i], valid_dates[i + 1]
    idx, nidx = dates.index(t_date), dates.index(next_date)
    c = cache[t_date]
    res = c["res"]
    rank_s, comp_s, pct_s, rdd_s = res["RANK"], res["COMPOSITE"], res["PCT_FROM_52H"], res["REL_52H_DD"]
    ranked = rank_s.dropna().index.tolist()     # already sorted by RANK
    nifty_ret = nifty_ff.iloc[nidx] / nifty_ff.iloc[idx] - 1.0

    for run, st in states.items():
        vname, mode, max_n = run
        cfg = VARIANTS[vname]
        w = cfg["w"]
        score = w[0] * c["ema50"] + w[1] * c["trend"] + w[2] * c["breadth"] + w[3] * c["mom"][cfg["thr"]]
        allow_new = score >= cfg["gate"]
        dyn_n = int(MIN_N + min(score / cfg["full"], 1.0) * (max_n - MIN_N))
        cur = st["port"]
        nxt = []
        for tk, s in cur.items():
            held = (t_date - s["entry_date"]).days
            rk = rank_s.get(tk, np.nan)
            p52 = pct_s.get(tk, np.nan)
            rd = rdd_s.get(tk, np.nan)
            if pd.notna(p52) and p52 < -25:
                continue
            elif pd.isna(rk):
                continue
            elif pd.notna(rd) and rd < REL_DD:
                if held >= MIN_HOLD:
                    continue
                nxt.append(tk)
            elif rk > HOLD_BUF:
                if held >= MIN_HOLD:
                    continue
                nxt.append(tk)
            else:
                nxt.append(tk)
        if allow_new:
            hs = set(nxt)
            nxt.extend([t for t in ranked[:dyn_n] if t not in hs])

        raw = {}
        for tk in nxt:
            cs = comp_s.get(tk, 0.0)
            cs = 0.0 if pd.isna(cs) else cs
            mv = get_vol(tk, idx)
            raw[tk] = cs / mv if mv else cs
        tot = sum(raw.values())
        cap = 1.0 / max_n
        actual = {}
        for tk in nxt:
            nw = raw[tk] / tot if tot > 0 else 1.0 / len(nxt)
            actual[tk] = {"entry_date": cur[tk]["entry_date"] if tk in cur else t_date,
                          "weight": min(cap, nw)}

        eq_w = 0.0
        if mode == "cash" and not allow_new:
            gross = CASH_WK
        elif actual:
            r_ = np.array([trow[t] for t in actual])
            wts = np.array([actual[t]["weight"] for t in actual])
            rets = PF[r_, nidx] / PF[r_, idx] - 1.0
            gross = float(np.nansum(rets * wts)) + max(0.0, 1.0 - wts.sum()) * CASH_WK
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
    sys.stdout.write(f"\r  sim [{i+1}/{len(valid_dates)-1}] {t_date}")
    sys.stdout.flush()

print()
ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
out = os.path.join(HERE, f"run_regnseall_{TAG}_{ts}")
os.makedirs(out, exist_ok=True)
years = (valid_dates[-1] - valid_dates[0]).days / 365.25
for (vname, mode, max_n), st in states.items():
    df = pd.DataFrame(st["log"])
    df.to_csv(os.path.join(out, f"res_{vname}_{mode}_n{max_n}.csv"), index=False)
    cagr = ((st["equity"] / 2e6) ** (1 / years) - 1) * 100
    mdd = ((df["equity"] / df["equity"].cummax()) - 1).min() * 100
    print(f"{mode:5s} {vname:9s} N{max_n}: CAGR {cagr:5.1f}%  MDD {mdd:6.1f}%  blocked {(~df['allow_new']).sum():3d}  "
          f"avgHeld {df['held'].mean():4.1f}  final {st['equity']:,.0f}")
nf = states[RUNS[0]]
print(f"NIFTY: CAGR {((nf['nifty']/2e6)**(1/years)-1)*100:.1f}%")
print("Saved to", out)
