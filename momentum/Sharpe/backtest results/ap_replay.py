"""
ap_replay.py
============
Drops the AP Momentum composite (5-signal z-score blend) into the CURRENT LIVE strategy replay on the
NSEAll universe and compares it with the live Sharpe composite, same engine, same rules, same costs.

What is held constant (read from the frozen cache in ranking_cache_nseall/, built from the live pipeline):
  eligibility (52H gate, MDTV, min price, min mcap), regime score, dynamic_n, allow_new, VOL, PCT_FROM_52H,
  REL_52H_DD, and every portfolio rule in sweep_replay.simulate (4 exit triggers, hold lock 28d, rank buffer
  from config, fill-open-slots entries, inverse-vol sizing capped at 1/MAX_N, 0.20% friction, 6% cash yield).
What changes per arm: COMPOSITE and therefore RANK (and the order in which entries are picked, and sizing).

AP signals (identical definitions to AP Momentum/zscore_backtest.py, computed per ticker on its own
dropna()'d price series, MIN_HISTORY = 278 obs): mom_12_1, mom_6_1, high_52w, clenow (90d), vol_adj.
Cross-sectional z-score (ddof=0) -> weighted sum -> normalise_composite (as live) -> rank among cached-eligible.

Arms:
  SHARPE_BASE            cached live composite (control)
  AP_EQ / AP_V1 / AP_V2  AP weightings, z over the whole scored universe (as live Sharpe z is)
  AP_V1_ELIGZ            AP_V1, z computed over eligible names only
  AP_V1_WINS3            AP_V1, per-signal z clipped to +/-3 before the weighted sum
  BLEND50                50/50 of standardised Sharpe composite and AP_V1
  <signal>_ONLY          each single AP signal alone (diagnostic: where does any edge come from)
  *_EWSIZE               equal-weight sizing instead of composite/vol (separates signal from sizing)

    python ap_replay.py            # builds _ap_signals.pkl once (a few minutes), then runs all arms
    python ap_replay.py --rebuild  # force-recompute signals

Read-only w.r.t. live files. Outputs: run_ap_replay_nseall_<timestamp>/
"""

import os
import sys
import math
import pickle
import argparse
import datetime
import warnings

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SHARPE_ROOT = os.path.dirname(HERE)
for p in (HERE, SHARPE_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)
import sweep_replay as sr
import momentum_lib as ml

warnings.filterwarnings("ignore")

CACHE_DIR = sr.CACHE_DIR
SIG_PKL = os.path.join(HERE, "_ap_signals.pkl")

# AP definitions (AP Momentum/zscore_backtest.py)
L12, L6, SKIP, L52, CLENOW_L, VOL_L = 252, 126, 21, 252, 90, 252
MIN_HISTORY = L12 + SKIP + 5          # 278
SIGNALS = ["mom_12_1", "mom_6_1", "high_52w", "clenow", "vol_adj"]
WEIGHTS = {
    "AP_EQ": dict(mom_12_1=.20, mom_6_1=.20, high_52w=.20, clenow=.20, vol_adj=.20),
    "AP_V1": dict(mom_12_1=.30, mom_6_1=.20, high_52w=.20, clenow=.20, vol_adj=.10),
    "AP_V2": dict(mom_12_1=.15, mom_6_1=.20, high_52w=.15, clenow=.25, vol_adj=.25),
}


# ── AP signals, per ticker, on its own dropna()'d series ─────────────────────

def _clenow(window):
    if np.any(window <= 0):
        return np.nan
    y = np.log(window)
    n = len(y)
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    sxy = ((x - xm) * (y - ym)).sum()
    syy = ((y - ym) ** 2).sum()
    if sxx == 0 or syy == 0:
        return np.nan
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy)
    return (math.exp(slope * 252) - 1.0) * r2


def build_signals():
    d = pickle.load(open(os.path.join(CACHE_DIR, "_data.pkl"), "rb"))
    prices_df, idx_list, tickers = d["prices_df"], d["idx_list"], list(d["prices_df"].index)
    n_weeks = len(idx_list) - 1       # one ranking per week i = 0..total-1 (matches rank_XXXX.pkl)
    out = np.full((n_weeks, len(tickers), len(SIGNALS)), np.nan)
    vals = prices_df.values
    for k, t in enumerate(tickers):
        row = vals[k]
        pos = np.flatnonzero(~np.isnan(row))
        if len(pos) < MIN_HISTORY:
            continue
        v = row[pos]
        for i in range(n_weeks):
            n = int(np.searchsorted(pos, idx_list[i], side="right"))
            if n < MIN_HISTORY:
                continue
            s = v[:n]
            p_end12, p_st12 = s[-1 - SKIP], s[-1 - SKIP - L12]
            p_st6 = s[-1 - SKIP - L6]
            m12 = p_end12 / p_st12 - 1.0 if p_st12 > 0 else np.nan
            m6 = p_end12 / p_st6 - 1.0 if p_st6 > 0 else np.nan
            hi = s[-L52:].max()
            h52 = s[-1] / hi if hi > 0 else np.nan
            cl = _clenow(s[-CLENOW_L:])
            seg = s[-VOL_L:]
            rets = seg[1:] / seg[:-1] - 1.0
            vol = np.std(rets, ddof=1) * math.sqrt(252) if len(rets) > 1 else np.nan
            va = m12 / vol if (vol and vol == vol and vol != 0) else np.nan
            out[i, k] = (m12, m6, h52, cl, va)
        if (k + 1) % 250 == 0:
            print(f"  signals: {k + 1}/{len(tickers)} tickers", flush=True)
    return dict(tickers=tickers, signals=SIGNALS, arr=out)


def load_signals(rebuild=False):
    if os.path.exists(SIG_PKL) and not rebuild:
        return pickle.load(open(SIG_PKL, "rb"))
    print("Building AP signals (one-off)...", flush=True)
    s = build_signals()
    tmp = SIG_PKL + ".tmp"
    pickle.dump(s, open(tmp, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, SIG_PKL)
    return s


# ── composite construction ───────────────────────────────────────────────────

def zscore(a, mask=None, winsor=None, rankpct=False):
    """a: (tickers, signals). z over rows where mask (default: any finite) -> NaN elsewhere. ddof=0.
    winsor=(lo_pct, hi_pct): clip each RAW signal column to those cross-sectional percentiles BEFORE
    the mean/std are computed. rankpct=True: replace raw values by centred percentile ranks (unit-variance
    scaled) instead of z-scoring."""
    z = np.full_like(a, np.nan)
    scored = np.isfinite(a).any(axis=1) if mask is None else mask & np.isfinite(a).any(axis=1)
    sub = a[scored].copy()
    if rankpct:
        for j in range(sub.shape[1]):
            col = sub[:, j]
            ok = np.isfinite(col)
            if ok.sum() > 1:
                r = pd.Series(col[ok]).rank().values
                sub[ok, j] = ((r - 0.5) / ok.sum() - 0.5) * math.sqrt(12)
        z[scored] = sub
        return z, scored
    if winsor is not None:
        for j in range(sub.shape[1]):
            col = sub[:, j]
            ok = np.isfinite(col)
            if ok.sum() > 10:
                lo, hi = np.nanpercentile(col[ok], [winsor[0], winsor[1]])
                sub[ok, j] = np.clip(col[ok], lo, hi)
    mu = np.nanmean(sub, axis=0)
    sd = np.nanstd(sub, axis=0)
    sd[sd == 0] = np.nan
    z[scored] = (sub - mu) / sd
    return z, scored


def inv_norm(v):
    """inverse of ml.normalise_composite"""
    v = np.asarray(v, dtype=float)
    return np.where(v >= 1.0, v - 1.0, 1.0 - 1.0 / v)


def norm(x):
    x = np.asarray(x, dtype=float)
    return np.where(x >= 0, x + 1.0, 1.0 / (1.0 - x))


def ap_raw(sig_week, weights, mask=None, clip=None, winsor=None, rankpct=False):
    z, scored = zscore(sig_week, mask, winsor=winsor, rankpct=rankpct)
    if clip is not None:
        z = np.clip(z, -clip, clip)
    w = np.array([weights.get(s, 0.0) for s in SIGNALS])
    comp = np.nansum(np.where(np.isfinite(z), z, 0.0) * w, axis=1)   # missing signal -> 0 like AP engine
    comp[~scored] = np.nan
    return comp


def rerank(base, comp_norm, tickers):
    """base: prepared week dict. comp_norm: array aligned to `tickers`. Returns a new week dict where
    COMPOSITE/RANK/order come from comp_norm, restricted to names the live cache considers eligible."""
    cidx = {t: k for k, t in enumerate(tickers)}
    elig = [t for t in base["order"] if not (base["rank"].get(t) != base["rank"].get(t))]   # RANK notna
    comp = {t: float(comp_norm[cidx[t]]) for t in base["order"] if t in cidx}
    cand = [(t, comp[t]) for t in elig if t in comp and comp[t] == comp[t]]
    cand.sort(key=lambda x: -x[1])
    rank = {t: float(r) for r, (t, _) in enumerate(cand, 1)}
    ranked = [t for t, _ in cand]
    rest = [t for t in base["order"] if t not in rank]
    nw = dict(base)
    nw["comp"] = comp
    nw["rank"] = {**{t: math.nan for t in rest}, **rank}
    nw["order"] = ranked + rest
    return nw


def make_arm(cache, sigs, builder, ew=False):
    tickers = sigs["tickers"]
    arr = sigs["arr"]
    prepared = []
    for i, base in enumerate(cache["prepared"]):
        elig_mask = np.array([base["rank"].get(t, math.nan) == base["rank"].get(t, math.nan) for t in tickers])
        comp_raw = builder(i, arr[i], base, elig_mask)
        nw = rerank(base, norm(comp_raw), tickers)
        if ew:
            nw["comp"] = {t: 1.0 for t in nw["comp"]}
            nw["vol"] = {}
        prepared.append(nw)
    return dict(cache, prepared=prepared)


def base_arm(cache, ew=False):
    if not ew:
        return cache
    prepared = []
    for base in cache["prepared"]:
        nw = dict(base)
        nw["comp"] = {t: 1.0 for t in base["comp"]}
        nw["vol"] = {}
        prepared.append(nw)
    return dict(cache, prepared=prepared)


# ── reporting ────────────────────────────────────────────────────────────────

def seg_stats(eq_w, dates_w):
    """eq_w: array of equity incl. starting value; dates_w: dates for those points."""
    yrs = (dates_w[-1] - dates_w[0]).days / 365.25
    cagr = (eq_w[-1] / eq_w[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    wk = eq_w[1:] / eq_w[:-1] - 1
    vol = np.std(wk, ddof=1) * math.sqrt(52)
    mdd = float(np.min(eq_w / np.maximum.accumulate(eq_w) - 1))
    return cagr, vol, (cagr - sr.RFR_ANNUAL) / vol if vol > 0 else np.nan, mdd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--wins", action="store_true",
                    help="winsorised / rank-based composite arms only (plus control, AP_V1, AP_EQ)")
    a = ap.parse_args()

    cache = sr.load_cache()
    total = len(cache["prepared"])
    assert total == cache["total"], "ranking cache incomplete"
    sigs = load_signals(a.rebuild)
    assert sigs["arr"].shape[0] == total, (sigs["arr"].shape, total)
    tickers = sigs["tickers"]
    # cached eligible tickers per week must align with the signal ticker order
    assert set(tickers) == set(cache["row"].keys())

    arms = {}
    arms["SHARPE_BASE"] = cache
    if a.wins:
        arms["AP_V1"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"]))
        arms["AP_EQ"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_EQ"]))
        arms["AP_V2"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V2"]))
        arms["AP_V1_Z3CLIP"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"], clip=3.0))
        for tag, wz in [("W1_99", (1, 99)), ("W2.5_97.5", (2.5, 97.5)), ("W5_95", (5, 95))]:
            for wn in ("AP_V1", "AP_EQ", "AP_V2"):
                arms[f"{wn}_{tag}"] = make_arm(
                    cache, sigs, lambda i, s, b, m, wn=wn, wz=wz: ap_raw(s, WEIGHTS[wn], winsor=wz))
        arms["AP_V1_W1_99_Z3CLIP"] = make_arm(
            cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"], winsor=(1, 99), clip=3.0))
        arms["AP_V1_W1_99_ELIGZ"] = make_arm(
            cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"], mask=m, winsor=(1, 99)))
        arms["AP_V2_W1_99_Z3CLIP"] = make_arm(
            cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V2"], winsor=(1, 99), clip=3.0))
        arms["AP_V2_W1_99_ELIGZ"] = make_arm(
            cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V2"], mask=m, winsor=(1, 99)))
        for wn in ("AP_V1", "AP_EQ", "AP_V2"):
            arms[f"{wn}_RANKPCT"] = make_arm(
                cache, sigs, lambda i, s, b, m, wn=wn: ap_raw(s, WEIGHTS[wn], rankpct=True))
        arms["AP_V1_W1_99_EWSIZE"] = make_arm(
            cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"], winsor=(1, 99)), ew=True)
        arms["SHARPE_BASE_EWSIZE"] = base_arm(cache, ew=True)
    else:
        arms["SHARPE_BASE_EWSIZE"] = base_arm(cache, ew=True)

    if not a.wins:
        for name, w in WEIGHTS.items():
            arms[name] = make_arm(cache, sigs, lambda i, s, b, m, w=w: ap_raw(s, w))
        arms["AP_V1_ELIGZ"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"], mask=m))
        arms["AP_V1_WINS3"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"], clip=3.0))
        arms["AP_V1_EWSIZE"] = make_arm(cache, sigs, lambda i, s, b, m: ap_raw(s, WEIGHTS["AP_V1"]), ew=True)

        def blend(i, s, b, m):
            sh = inv_norm(np.array([b["comp"].get(t, np.nan) for t in tickers]))
            ap1 = ap_raw(s, WEIGHTS["AP_V1"])
            ok = np.isfinite(sh) & np.isfinite(ap1)
            out = np.full(len(tickers), np.nan)
            if ok.sum() > 10:
                out[ok] = 0.5 * sh[ok] / np.std(sh[ok]) + 0.5 * ap1[ok] / np.std(ap1[ok])
            return out
        arms["BLEND50"] = make_arm(cache, sigs, blend)

        for k, sname in enumerate(SIGNALS):
            w = {sname: 1.0}
            arms[f"{sname}_ONLY"] = make_arm(cache, sigs, lambda i, s, b, m, w=w: ap_raw(s, w))

    print(f"Running {len(arms)} arms on {total} weeks "
          f"(max_n={sr.MAX_N}, buffer={sr.LIVE_BUFFER}, lock={sr.LIVE_LOCK}d, friction={sr.FRICTION:.2%}) ...", flush=True)

    dates = cache["dates"]
    mid = total // 2
    rows, split, yearly, sims = [], [], {}, {}
    for name, c in arms.items():
        sim = sr.simulate(c, sr.LIVE_BUFFER, sr.LIVE_LOCK)
        sims[name] = sim
        m = sr.metrics(c, sim)
        rows.append(dict(arm=name, **m))
        eq = np.concatenate([[sr.START_EQUITY], sim["equity"]])
        dts = [dates[0]] + [dates[i + 1] for i in range(total)]
        c1 = seg_stats(eq[:mid + 1], dts[:mid + 1])
        c2 = seg_stats(eq[mid:], dts[mid:])
        split.append(dict(arm=name, h1_start=str(dts[0]), h1_end=str(dts[mid]), h2_end=str(dts[-1]),
                          h1_cagr=c1[0], h1_vol=c1[1], h1_sharpe=c1[2], h1_mdd=c1[3],
                          h2_cagr=c2[0], h2_vol=c2[1], h2_sharpe=c2[2], h2_mdd=c2[3]))
        ser = pd.Series(eq, index=pd.to_datetime(dts))
        ye = ser.groupby(ser.index.year).last()
        ys = ye / ye.shift(1) - 1
        ys.iloc[0] = ye.iloc[0] / sr.START_EQUITY - 1
        yearly[name] = ys

    summ = pd.DataFrame(rows).set_index("arm")
    spl = pd.DataFrame(split).set_index("arm")
    yr = pd.DataFrame(yearly)

    # paired weekly differences vs control, and rank-overlap of top 20
    base_w = np.diff(np.concatenate([[sr.START_EQUITY], sims["SHARPE_BASE"]["equity"]])) / \
        np.concatenate([[sr.START_EQUITY], sims["SHARPE_BASE"]["equity"]])[:-1]
    paired = []
    for name, c in arms.items():
        e = np.concatenate([[sr.START_EQUITY], sims[name]["equity"]])
        w = np.diff(e) / e[:-1]
        d = w - base_w
        t = d.mean() / d.std(ddof=1) * math.sqrt(len(d)) if d.std(ddof=1) > 0 else np.nan
        d1, d2 = d[:mid], d[mid:]
        t1 = d1.mean() / d1.std(ddof=1) * math.sqrt(len(d1)) if d1.std(ddof=1) > 0 else np.nan
        t2 = d2.mean() / d2.std(ddof=1) * math.sqrt(len(d2)) if d2.std(ddof=1) > 0 else np.nan
        ov = []
        for bw, aw in zip(cache["prepared"], c["prepared"]):
            tb = [t_ for t_ in bw["order"][:20] if bw["rank"].get(t_) == bw["rank"].get(t_)]
            ta = [t_ for t_ in aw["order"][:20] if aw["rank"].get(t_) == aw["rank"].get(t_)]
            if tb and ta:
                ov.append(len(set(tb) & set(ta)) / len(set(tb) | set(ta)))
        paired.append(dict(arm=name, wk_diff_bp=d.mean() * 1e4, t_stat=t, t_h1=t1, t_h2=t2,
                           top20_jaccard_vs_base=float(np.mean(ov))))
    pr = pd.DataFrame(paired).set_index("arm")

    out = os.path.join(HERE, ("run_ap_wins_nseall_" if a.wins else "run_ap_replay_nseall_") + datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    os.makedirs(out)
    summ.to_csv(os.path.join(out, "summary.csv"))
    spl.to_csv(os.path.join(out, "split_period.csv"))
    yr.to_csv(os.path.join(out, "yearly.csv"))
    pr.to_csv(os.path.join(out, "paired_vs_base.csv"))
    pd.DataFrame({n: s["equity"] for n, s in sims.items()},
                 index=[dates[i + 1] for i in range(total)]).to_csv(os.path.join(out, "equity_weekly.csv"))

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    show = summ[["cagr", "vol", "sharpe", "mdd", "avg_held", "avg_turnover_pct", "median_hold_wks"]].copy()
    for c_ in ("cagr", "vol", "mdd"):
        show[c_] = (show[c_] * 100).round(1)
    show = show.round(2)
    print("\n=== FULL PERIOD (live rules, net of 0.20% friction) ===")
    print(show.to_string())
    s2 = spl[["h1_cagr", "h1_sharpe", "h1_mdd", "h2_cagr", "h2_sharpe", "h2_mdd"]].copy()
    for c_ in ("h1_cagr", "h1_mdd", "h2_cagr", "h2_mdd"):
        s2[c_] = (s2[c_] * 100).round(1)
    print(f"\n=== SPLIT: H1 {split[0]['h1_start']} -> {split[0]['h1_end']} | H2 -> {split[0]['h2_end']} ===")
    print(s2.round(2).to_string())
    print("\n=== PAIRED vs SHARPE_BASE (weekly diff in bp, t-stats; overlap of top-20 sets) ===")
    print(pr.round(2).to_string())
    print("\n=== CALENDAR YEARS (%) ===")
    print((yr[["SHARPE_BASE", "AP_V1", "AP_V1_W1_99", "AP_V1_RANKPCT"] if a.wins else ["SHARPE_BASE", "AP_V1", "AP_V2", "BLEND50"]] * 100).round(1).to_string())
    print(f"\nOutputs: {out}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6.5))
    xs = pd.to_datetime([dates[i + 1] for i in range(total)])
    plot_arms = ([("SHARPE_BASE", "-"), ("AP_V1", "-"), ("AP_V1_W1_99", "-"), ("AP_V1_RANKPCT", "-"),
                  ("AP_V1_W1_99_EWSIZE", ":"), ("SHARPE_BASE_EWSIZE", ":")] if a.wins else
                 [("SHARPE_BASE", "-"), ("AP_V1", "-"), ("AP_V2", "-"), ("BLEND50", "-"),
                  ("AP_V1_EWSIZE", ":"), ("SHARPE_BASE_EWSIZE", ":")])
    for n, st in plot_arms:
        ax.plot(xs, sims[n]["equity"] / sr.START_EQUITY, st, label=n, lw=2 if n == "SHARPE_BASE" else 1.5)
    ax.plot(xs, sims["SHARPE_BASE"]["nifty"] / sr.START_EQUITY, "--", color="grey", label="NIFTY500")
    ax.set_yscale("log"); ax.grid(alpha=.3, which="both"); ax.legend()
    ax.set_title("NSEAll, live rules: Sharpe composite vs AP Momentum composite (net of friction)")
    fig.tight_layout(); fig.savefig(os.path.join(out, "equity_curves.png"), dpi=140)


if __name__ == "__main__":
    main()
