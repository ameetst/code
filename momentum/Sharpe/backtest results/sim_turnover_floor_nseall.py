"""
SANDBOX - no live code touched. NSEAll min-turnover (MDTV) floor test under live-style rules.
Re-uses the weekly ranking cache of sim_regime_nseall_live.py (built with floor 1.0 Cr) and
re-derives RANK for a higher floor point-in-time:
    eligible(X) = (RANK present at 1.0 Cr)  &  (12M median turnover >= X  OR  6M median >= X)
    RANK(X)     = rank of COMPOSITE among eligible(X)
Regime score / dynamic N are unaffected by the floor (whole-universe signals), so they come from the cache.
Live variant only (35/25/25/15, bar 1.5, gate 0.40/0.75), max_n 30, hold-style + cash-style, biweekly.
usage: python sim_turnover_floor_nseall.py <History_updated_NSEAll.xlsx> [step=2]
"""
import sys, os, pickle, datetime, warnings
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import momentum_lib as ml
warnings.filterwarnings("ignore")

FILE = sys.argv[1]
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 2
CACHE = os.path.join(HERE, ".cache_regnseall_nseall.pkl")
FLOORS = [1.0, 2.0, 3.0, 5.0, 10.0]
FRICTION, MIN_N, MAX_N, REL_DD, HOLD_BUF, MIN_HOLD = 0.002, 5, 30, -20, 75, 28
CASH_WK = (1.06 ** (STEP / 52)) - 1.0
W = (0.35, 0.25, 0.25, 0.15)
GATE, FULL = 0.40, 0.75

prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
volume_df = ml.load_volume(FILE)
dt_idx = pd.DatetimeIndex(dates)
eow = [dates[i] for i in range(len(dt_idx) - 1)
       if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
eow.append(dates[-1])
valid_dates = [d for d in eow if dates.index(d) >= 252][::STEP]
cache = pickle.load(open(CACHE, "rb"))
tick = list(prices_df.index)
trow = {t: i for i, t in enumerate(tick)}
PF = prices_df.ffill(axis=1).values
nifty_ff = nifty_series.ffill()

# ---- point-in-time median turnover (same semantics as ml.compute_turnover), vectorised per ticker ----
D = (prices_df.values * volume_df.reindex(index=prices_df.index, columns=prices_df.columns).values) / 1e7
nz = [np.where(~np.isnan(D[r]))[0] for r in range(len(tick))]
vals = [D[r][nz[r]] for r in range(len(tick))]
def turn_at(idx):
    t12, t6 = {}, {}
    for r, tk in enumerate(tick):
        k = np.searchsorted(nz[r], idx, side="right")      # entries with col <= idx
        if k < 20:
            continue
        v = vals[r][:k]
        t12[tk] = float(np.median(v[-252:])) if min(k, 252) >= 10 else np.nan
        t6[tk] = float(np.median(v[-126:])) if min(k, 126) >= 10 else np.nan
    return pd.Series(t12), pd.Series(t6)

print("Computing point-in-time turnover ...", flush=True)
TURN = {}
for i, d in enumerate(valid_dates[:-1]):
    TURN[d] = turn_at(dates.index(d))
    if (i + 1) % 40 == 0:
        print(f"  {i+1}/{len(valid_dates)-1}", flush=True)

def ranks_for(d, floor):
    res = cache[d]["res"]
    t12, t6 = TURN[d]
    ok = ((t12.reindex(res.index) >= floor) | (t6.reindex(res.index) >= floor)).fillna(False)
    elig = res["RANK"].notna() & ok
    comp = res.loc[elig, "COMPOSITE"]
    return comp.rank(ascending=False, method="first", na_option="bottom").sort_values()

# validation: floor 1.0 must reproduce the cached rank set
d0 = valid_dates[60]
r1 = ranks_for(d0, 1.0)
base = cache[d0]["res"]["RANK"].dropna()
print(f"validate @1.0: re-derived {len(r1)} vs cached {len(base)}, "
      f"identical top30: {set(r1.index[:30]) == set(base.sort_values().index[:30])}", flush=True)

vol_cache = {}
def get_vol(tk, idx):
    key = (tk, idx)
    if key in vol_cache:
        return vol_cache[key]
    px = prices_df.loc[tk].iloc[:idx + 1].dropna()
    out = None
    if len(px) > 10:
        vs = []
        for w in (252, 189, 126, 63):
            pw = px.iloc[-w:] if len(px) >= w else px
            lr = np.diff(np.log(pw.values))
            if len(lr) > 5:
                vs.append(np.std(lr, ddof=1) * np.sqrt(252))
        if vs and np.mean(vs) > 0:
            out = float(np.mean(vs))
    vol_cache[key] = out
    return out

states = {(f, m): {"port": {}, "equity": 2_000_000.0, "log": [], "held_turn": [], "elig": []}
          for f in FLOORS for m in ("hold", "cash")}
for i in range(len(valid_dates) - 1):
    t_date, next_date = valid_dates[i], valid_dates[i + 1]
    idx, nidx = dates.index(t_date), dates.index(next_date)
    c = cache[t_date]
    res = c["res"]
    comp_s, pct_s, rdd_s = res["COMPOSITE"], res["PCT_FROM_52H"], res["REL_52H_DD"]
    score = W[0] * c["ema50"] + W[1] * c["trend"] + W[2] * c["breadth"] + W[3] * c["mom"][1.5]
    allow_new = score >= GATE
    dyn_n = int(MIN_N + min(score / FULL, 1.0) * (MAX_N - MIN_N))
    t12, t6 = TURN[t_date]
    rank_by_floor = {f: ranks_for(t_date, f) for f in FLOORS}
    for (floor, mode), st in states.items():
        rank_s = rank_by_floor[floor]
        ranked = rank_s.index.tolist()
        cur, nxt = st["port"], []
        for tk, s in cur.items():
            held = (t_date - s["entry_date"]).days
            rk = rank_s.get(tk, np.nan)
            p52, rd = pct_s.get(tk, np.nan), rdd_s.get(tk, np.nan)
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
        cap = 1.0 / MAX_N
        actual = {}
        for tk in nxt:
            nw = raw[tk] / tot if tot > 0 else 1.0 / len(nxt)
            actual[tk] = {"entry_date": cur[tk]["entry_date"] if tk in cur else t_date, "weight": min(cap, nw)}
        if mode == "cash" and not allow_new:
            gross = CASH_WK
        elif actual:
            r_ = np.array([trow[t] for t in actual])
            wts = np.array([actual[t]["weight"] for t in actual])
            rets = PF[r_, nidx] / PF[r_, idx] - 1.0
            gross = float(np.nansum(rets * wts)) + max(0.0, 1.0 - wts.sum()) * CASH_WK
        else:
            gross = CASH_WK
        if pd.isna(gross):
            gross = 0.0
        chg = sum(abs((actual[t]["weight"] if t in actual else 0.0) - (cur[t]["weight"] if t in cur else 0.0))
                  for t in set(cur) | set(actual))
        net = gross - chg * FRICTION
        st["equity"] *= 1 + net
        st["log"].append({"date": t_date, "score": score, "allow_new": allow_new, "held": len(actual),
                          "turnover": chg / 2 * 100, "net": net, "equity": st["equity"]})
        # median 12M turnover (Cr) of the names actually held, for the liquidity picture
        if actual:
            st["held_turn"].append(float(np.nanmedian([t12.get(t, np.nan) for t in actual])))
            st["held_min"] = min(st.get("held_min", 1e9), float(np.nanmin([t12.get(t, np.nan) for t in actual])))
        st["elig"].append(len(ranked))
        st["port"] = actual
    sys.stdout.write(f"\r  sim [{i+1}/{len(valid_dates)-1}] {t_date}")
    sys.stdout.flush()

print()
years = (valid_dates[-1] - valid_dates[0]).days / 365.25
ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
out = os.path.join(HERE, f"run_turnfloor_nseall_step{STEP}_{ts}")
os.makedirs(out, exist_ok=True)
for (floor, mode), st in states.items():
    df = pd.DataFrame(st["log"])
    df.to_csv(os.path.join(out, f"res_floor{floor:g}_{mode}.csv"), index=False)
    cagr = ((st["equity"] / 2e6) ** (1 / years) - 1) * 100
    mdd = ((df["equity"] / df["equity"].cummax()) - 1).min() * 100
    sh = (df["net"].mean() - CASH_WK) / df["net"].std(ddof=1) * np.sqrt(52 / STEP)
    print(f"floor {floor:4.1f} Cr {mode:5s}: CAGR {cagr:5.1f}%  MDD {mdd:6.1f}%  Sharpe {sh:4.2f}  "
          f"avgHeld {df['held'].mean():4.1f}  avgTurnover/step {df['turnover'].mean():4.1f}%  "
          f"avgEligible {np.mean(st['elig']):5.0f}  medHeld12MTurnover {np.median(st['held_turn']):6.1f} Cr  "
          f"minHeld {st.get('held_min', np.nan):5.2f} Cr")
print("Saved to", out)
