"""
SANDBOX - no live code touched. Backtest N1000 vs NSEAll under the live-style rules
(hold convention, live regime 35/25/25/15, gate 0.40 / full 0.75, min_n 5, max_n 30, hold buffer 75,
28-day lock, EXIT_52H / EXIT_FILTER / EXIT_REL_DD / EXIT_RANK, MDTV >= 1.0 Cr, friction 0.2%, biweekly).

Universes (all cut from the 10-year NSEAll history file; Z-scores, 52H breadth, EMA breadth and
momentum breadth are recomputed over each universe only):
  nseall      : every ticker in the file (weekly cache from sim_regime_nseall_live.py)
  n1000_cur   : today's N1000 members (base files/N1000.xlsx) - HINDSIGHT membership, biased upward
  top1000_pit : point-in-time proxy - at each date the 1000 most liquid names (trailing 12M median
                turnover) among stocks with a current price; uses only information available then
usage: python sim_universe_compare.py <History_updated_NSEAll.xlsx> [step=2]
"""
import sys, os, pickle, datetime, warnings, io, contextlib
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import momentum_lib as ml
warnings.filterwarnings("ignore")

FILE = sys.argv[1]
STEP = int(sys.argv[2]) if len(sys.argv) > 2 else 2
OLD_CACHE = os.path.join(HERE, ".cache_regnseall_nseall.pkl")
NEW_CACHE = os.path.join(HERE, ".cache_universe_compare.pkl")
N1000_FILE = "C:/Users/ameet/Documents/Github/dhan_datahq/base files/N1000.xlsx"
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
RFR_ANNUAL, TRADING_DAYS, FRICTION = 0.07, 252, 0.002
MIN_N, MAX_N, REL_DD, HOLD_BUF, MIN_HOLD = 5, 30, -20, 75, 28
MIN_TURN = 1.0
W = (0.35, 0.25, 0.25, 0.15)
GATE, FULL = 0.40, 0.75
CASH_WK = (1.06 ** (STEP / 52)) - 1.0
rfr_daily = RFR_ANNUAL / TRADING_DAYS

prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
volume_df = ml.load_volume(FILE).reindex(index=prices_df.index, columns=prices_df.columns)
tick = list(prices_df.index)
trow = {t: i for i, t in enumerate(tick)}
dt_idx = pd.DatetimeIndex(dates)
eow = [dates[i] for i in range(len(dt_idx) - 1)
       if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
eow.append(dates[-1])
valid_dates = [d for d in eow if dates.index(d) >= 252][::STEP]
P = prices_df.values
ncol = P.shape[1]
E50 = prices_df.T.ewm(span=50, adjust=False).mean().T.values
E200 = prices_df.T.ewm(span=200, adjust=False).mean().T.values
LVP = np.maximum.accumulate(np.where(~np.isnan(P), np.arange(ncol)[None, :], -1), axis=1)
PF = prices_df.ffill(axis=1).values
nifty_ff = nifty_series.ffill()

# ---- member sets ----
n1000_names = pd.read_excel(N1000_FILE, sheet_name="DATA", header=0).iloc[:, 0].astype(str).str.strip().tolist()
n1000_set = set(n1000_names) & set(tick)
print(f"N1000 template names {len(n1000_names)}, present in history file {len(n1000_set)}", flush=True)

# ---- point-in-time turnover (same semantics as ml.compute_turnover) ----
D = (P * volume_df.values) / 1e7
nz = [np.where(~np.isnan(D[r]))[0] for r in range(len(tick))]
vals = [D[r][nz[r]] for r in range(len(tick))]
def turn_at(idx):
    t12 = np.full(len(tick), np.nan); t6 = np.full(len(tick), np.nan)
    for r in range(len(tick)):
        k = np.searchsorted(nz[r], idx, side="right")
        if k < 20:
            continue
        v = vals[r][:k]
        t12[r] = np.median(v[-252:]) if min(k, 252) >= 10 else np.nan
        t6[r] = np.median(v[-126:]) if min(k, 126) >= 10 else np.nan
    return pd.Series(t12, index=tick), pd.Series(t6, index=tick)

def members_for(uni, idx, t12):
    if uni == "n1000_cur":
        return [t for t in tick if t in n1000_set]
    if uni == "top1000_pit":
        has_px = pd.Series(LVP[:, idx] >= idx - 5, index=tick)          # priced within the last ~week (idx can be a holiday column)
        cand = t12[has_px & t12.notna()]
        return cand.nlargest(1000).index.tolist()
    return tick

def lean(uni, idx, t12, t6):
    mem = members_for(uni, idx, t12)
    sl = prices_df.loc[mem].iloc[:, :idx + 1]
    with contextlib.redirect_stdout(io.StringIO()):
        _, z = ml.compute_sharpe(sl, mem, SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
        pct = ml.compute_pct_from_52h(sl, mem)
    comp = z["COMPOSITE"].map(ml.normalise_composite)
    rdd = ml.compute_relative_52h_dd(pct, nifty_series.iloc[:idx + 1], eligible_mask=pct >= -25)
    adtv = ((t12.reindex(mem) >= MIN_TURN) | (t6.reindex(mem) >= MIN_TURN)).fillna(False)
    elig = (pct >= -25) & adtv
    rank = comp[elig].rank(ascending=False, method="first", na_option="bottom").sort_values()
    # regime (whole-universe signals, same definition as ml.compute_regime_score)
    rows = np.array([trow[t] for t in mem]); lv = LVP[rows, idx]; ok = lv >= 0
    r_, c_ = rows[ok], lv[ok]
    ema50 = float((P[r_, c_] > E50[r_, c_]).sum()) / max(1, ok.sum())
    trend = float((E50[r_, c_] > E200[r_, c_]).sum()) / max(1, ok.sum())
    m52 = pct >= -25
    breadth = int(m52.sum()) / len(mem)
    mom = int((comp[m52] > 1.5).sum()) / max(1, int(m52.sum()))
    res = pd.DataFrame({"RANK": rank, "COMPOSITE": comp, "PCT_FROM_52H": pct, "REL_52H_DD": rdd})
    return {"res": res, "ema50": ema50, "trend": trend, "breadth": breadth, "mom": {1.5: mom}, "n": len(mem)}

# ---- phase 1: weekly data per universe ----
cache = pickle.load(open(NEW_CACHE, "rb")) if os.path.exists(NEW_CACHE) else {}
old = pickle.load(open(OLD_CACHE, "rb"))
t0 = datetime.datetime.now()
TURN = {}
for i, d in enumerate(valid_dates[:-1]):
    idx = dates.index(d)
    need = [u for u in ("n1000_cur", "top1000_pit") if (u, d) not in cache]
    if d not in old and ("nseall", d) not in cache:
        need.append("nseall")                       # weeks missing from the biweekly NSEAll cache (STEP=1)
    elif i in (60, 150) and ("val", d) not in cache and d in old:
        need.append("nseall")                       # validation of the lean engine against the old cache
    if not need:
        continue
    t12, t6 = turn_at(idx)
    for u in need:
        out = lean(u, idx, t12, t6)
        if u == "nseall" and d in old:
            ref = old[d]
            top_l = set(out["res"]["RANK"].dropna().nsmallest(30).index)
            top_r = set(ref["res"]["RANK"].dropna().nsmallest(30).index)
            n_l, n_r = out["res"]["RANK"].notna().sum(), ref["res"]["RANK"].notna().sum()
            print(f"\nVALIDATE nseall {d}: ranked {n_l} vs {n_r}; top30 identical {top_l == top_r}; "
                  f"ema50 {out['ema50']:.3f}/{ref['ema50']:.3f} trend {out['trend']:.3f}/{ref['trend']:.3f} "
                  f"breadth {out['breadth']:.3f}/{ref['breadth']:.3f} mom {out['mom'][1.5]:.3f}/{ref['mom'][1.5]:.3f}", flush=True)
            cache[("val", d)] = True
        elif u == "nseall" and d not in old:
            cache[("nseall", d)] = out
        else:
            cache[(u, d)] = out
    if (i + 1) % 10 == 0:
        pickle.dump(cache, open(NEW_CACHE + ".tmp", "wb")); os.replace(NEW_CACHE + ".tmp", NEW_CACHE)
    sys.stdout.write(f"\r  rank [{i+1}/{len(valid_dates)-1}] {d} elapsed {(datetime.datetime.now()-t0).total_seconds():6.0f}s")
    sys.stdout.flush()
pickle.dump(cache, open(NEW_CACHE + ".tmp", "wb")); os.replace(NEW_CACHE + ".tmp", NEW_CACHE)
print("\nphase 1 done", flush=True)

def get_data(u, d):
    if u == "nseall":
        return old[d] if d in old else cache[("nseall", d)]
    return cache[(u, d)]

# ---- phase 2 ----
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

UNIS = ["nseall", "n1000_cur", "top1000_pit"]
states = {(u, m): {"port": {}, "equity": 2_000_000.0, "nifty": 2_000_000.0, "log": []}
          for u in UNIS for m in ("hold", "cash")}
for i in range(len(valid_dates) - 1):
    t_date, next_date = valid_dates[i], valid_dates[i + 1]
    idx, nidx = dates.index(t_date), dates.index(next_date)
    nifty_ret = nifty_ff.iloc[nidx] / nifty_ff.iloc[idx] - 1.0
    for (u, mode), st in states.items():
        c = get_data(u, t_date)
        res = c["res"]
        rank_s, comp_s, pct_s, rdd_s = res["RANK"], res["COMPOSITE"], res["PCT_FROM_52H"], res["REL_52H_DD"]
        ranked = rank_s.dropna().sort_values().index.tolist()
        score = W[0] * c["ema50"] + W[1] * c["trend"] + W[2] * c["breadth"] + W[3] * c["mom"][1.5]
        allow_new = score >= GATE
        dyn_n = int(MIN_N + min(score / FULL, 1.0) * (MAX_N - MIN_N))
        cur, nxt = st["port"], []
        for tk, s in cur.items():
            held = (t_date - s["entry_date"]).days
            rk = rank_s.get(tk, np.nan); p52 = pct_s.get(tk, np.nan); rd = rdd_s.get(tk, np.nan)
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
        tot = sum(raw.values()); cap = 1.0 / MAX_N
        actual = {}
        for tk in nxt:
            nw = raw[tk] / tot if tot > 0 else 1.0 / len(nxt)
            actual[tk] = {"entry_date": cur[tk]["entry_date"] if tk in cur else t_date, "weight": min(cap, nw)}
        if mode == "cash" and not allow_new:
            gross = CASH_WK
        elif actual:
            r2 = np.array([trow[t] for t in actual]); wts = np.array([actual[t]["weight"] for t in actual])
            rets = PF[r2, nidx] / PF[r2, idx] - 1.0
            gross = float(np.nansum(rets * wts)) + max(0.0, 1.0 - wts.sum()) * CASH_WK
        else:
            gross = CASH_WK
        if pd.isna(gross):
            gross = 0.0
        chg = sum(abs((actual[t]["weight"] if t in actual else 0.0) - (cur[t]["weight"] if t in cur else 0.0))
                  for t in set(cur) | set(actual))
        net = gross - chg * FRICTION
        st["equity"] *= 1 + net
        st["nifty"] *= 1 + nifty_ret
        st["log"].append({"date": t_date, "score": score, "allow_new": allow_new, "dyn_n": dyn_n,
                          "held": len(actual), "turnover": chg / 2 * 100, "net": net, "equity": st["equity"],
                          "nifty_equity": st["nifty"], "universe_n": c.get("n", np.nan), "ranked": len(ranked)})
        st["port"] = actual
    sys.stdout.write(f"\r  sim [{i+1}/{len(valid_dates)-1}] {t_date}"); sys.stdout.flush()

print()
years = (valid_dates[-1] - valid_dates[0]).days / 365.25
ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
out = os.path.join(HERE, f"run_universe_compare_step{STEP}_{ts}")
os.makedirs(out, exist_ok=True)
for (u, mode), st in states.items():
    df = pd.DataFrame(st["log"])
    df.to_csv(os.path.join(out, f"res_{u}_{mode}.csv"), index=False)
    cagr = ((st["equity"] / 2e6) ** (1 / years) - 1) * 100
    mdd = ((df["equity"] / df["equity"].cummax()) - 1).min() * 100
    sh = (df["net"].mean() - CASH_WK) / df["net"].std(ddof=1) * np.sqrt(52 / STEP)
    print(f"{u:12s} {mode:5s}: CAGR {cagr:5.1f}%  MDD {mdd:6.1f}%  Sharpe {sh:4.2f}  blocked {(~df['allow_new']).sum():3d}  "
          f"avgHeld {df['held'].mean():4.1f}  avgDynN {df['dyn_n'].mean():4.1f}  avgRanked {df['ranked'].mean():5.0f}  "
          f"turnover/step {df['turnover'].mean():4.1f}%  avgScore {df['score'].mean():.3f}")
nf = states[("nseall", "hold")]
print(f"NIFTY: CAGR {((nf['nifty']/2e6)**(1/years)-1)*100:.1f}%")
for (u, mode), st in states.items():                     # yearly returns, hold mode
    if mode != "hold":
        continue
    df = pd.DataFrame(st["log"]); df["yr"] = pd.to_datetime(df["date"]).dt.year
    yr = df.groupby("yr").apply(lambda g: (g["equity"].iloc[-1] / (g["equity"].iloc[0] / (1 + g["net"].iloc[0])) - 1) * 100)
    print(u, {int(k): round(v, 1) for k, v in yr.items()})
print("Saved to", out)
