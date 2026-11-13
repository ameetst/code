"""
Rank-tilt position sizing study on the live NSEAll strategy.

Question: does giving higher-ranked names a larger weight (and lower-ranked names a
smaller one) beat equal weight, holding the SAME names each week?

Same-portfolio design (as in backtest_vol_sizing_nseall.py)
  Which names are held does not depend on weights, so the point-in-time ranking pass is
  run once and every sizing scheme is applied to the identical weekly portfolios.
  This script re-runs that pass because the earlier cache did not store RANK.

Stage 1a  per-week ranking frames (RANK, PCT_FROM_52H, REL_52H_DD, COMPOSITE) + regime,
          computed in parallel worker processes, checkpointed incrementally.
Stage 1b  sequential live-faithful state machine (4 exit triggers, hold lock, entries):
          records the held/new tickers with RANK, COMPOSITE, 4-window vol and next-week return.
Stage 2   apply each sizing scheme (seconds), report full-period, split-period and sweeps.

Variants
  BASELINE        live formula (COMPOSITE/vol normalised, cap 4%). Must equal LIVE_BASELINE.
  EQUAL           flat 4% on all held names, full weekly reweight.
  EQUAL_ENTRY     flat 4% on new entries only, cash-limited, held names untouched (live-style).
  TILT_A          linear rank tilt, weight = 4% * (1 + d*(1-2*pos)), pos 0=best..1=worst among
                  the held names, full weekly reweight.
  TILT_A_REV      same with the sign flipped (overweight WORST ranks). Placebo: if it does as
                  well as TILT_A the rank signal is not what drives any difference.
  TILT_B          stepped: best third 1+d, middle 1, worst third 1-d. Full reweight.
  TILT_C          score tilt: 1 + d*clip(z,-2,2)/2, z = COMPOSITE standardised within held set.
  TILT_A_ENTRY    linear tilt set ONCE at entry from the entry rank (pos=(rank-1)/(N-1) with
                  N=dynamic_n), cash-limited, held weights untouched. Matches how live sizes.
  TILT_B_ENTRY    stepped version of the above.

All multipliers are clipped to [m_lo, m_hi] (default 0.5-1.5 => 2%-6%). Full-reweight variants
are scaled down pro-rata if weights sum above 100%; cash earns LIQUID_YIELD_PA.

Usage
  python backtest_rank_tilt_nseall.py                 # full run, 3 workers, resumable
  python backtest_rank_tilt_nseall.py --workers 2
  python backtest_rank_tilt_nseall.py --smoke 6       # last 6 weeks only, no cache
  python backtest_rank_tilt_nseall.py --use-partial   # analyse weeks computed so far
"""

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")       # avoid BLAS oversubscription across workers
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import sys
import argparse
import datetime
import warnings
import pickle
import shutil
import multiprocessing as mp
from contextlib import contextmanager

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

SHARPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SHARPE_ROOT not in sys.path:
    sys.path.insert(0, SHARPE_ROOT)

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG (mirrors backtest_live_baseline_nseall.py) ────────────────────────
FILE = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated_NSEAll.xlsx"
STOCKDB_CSV = os.path.join(SHARPE_ROOT, "STOCKDB.csv")

RFR_ANNUAL      = 0.07
TRADING_DAYS    = 252
FRICTION        = 0.002
LIQUID_YIELD_PA = 0.06
START_EQUITY    = 2000000.0

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
VOL_WINDOWS    = [252, 189, 126, 63]

MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
SIGNAL_WEIGHTS      = {
    "ema50_breadth":     0.35,
    "ema_trend_breadth": 0.25,
    "breadth":           0.25,
    "momentum":          0.15,
}

MIN_TURNOVER_CR         = 1.0
EQ_SERIES_FILTER        = False
CIRCUIT_FILTER_ENABLED  = False
CIRCUIT_HIT_THRESHOLD   = 20
REL_DD_BREACH_THRESHOLD = -20
HOLD_RANK_BUFFER        = 75      # as used by the earlier baselines (live config now reads 76)
MIN_HOLD_DAYS           = 28

BASE_WT   = 1.0 / MAX_N
MAX_GROSS = 1.0

DEF_DELTA = {"A": 0.5, "B": 0.25, "C": 0.5}
DEF_M_LO, DEF_M_HI = 0.5, 1.5
SPLIT_DATE = pd.Timestamp("2022-01-01")

LIVE_BASELINE_CSV = os.path.join(SHARPE_ROOT, "backtest results",
                                 "run_2026-10-13_18-41-09_live_baseline_nseall", "backtest_results.csv")
CACHE_RANKS = os.path.join(SHARPE_ROOT, "backtest results", ".stage1a_ranks_nseall.pkl")
CACHE_S1    = os.path.join(SHARPE_ROOT, "backtest results", ".stage1_rank_tilt_nseall.pkl")


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old


def atomic_dump(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(obj, f)
    os.replace(tmp, path)


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1a — parallel ranking pass
# ═════════════════════════════════════════════════════════════════════════════

_G = {}


def _init_worker():
    with suppress_stdout():
        prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
        volume_df = ml.load_volume(FILE)
    _G.update(prices_df=prices_df, nifty=nifty_series, tickers=stock_tickers, volume=volume_df)


def _rank_week(idx):
    with suppress_stdout():
        result, regime_score, regime_detail = ml.compute_universe_rankings(
            _G["prices_df"].iloc[:, :idx + 1], _G["nifty"].iloc[:idx + 1], _G["tickers"],
            volume_df=_G["volume"].iloc[:, :idx + 1] if _G["volume"] is not None else None,
            min_turnover_cr=MIN_TURNOVER_CR,
            eq_series_filter=EQ_SERIES_FILTER,
            circuit_filter_enabled=CIRCUIT_FILTER_ENABLED,
            circuit_threshold=CIRCUIT_HIT_THRESHOLD,
            stockdb_csv_path=STOCKDB_CSV,
            windows=SHARPE_WINDOWS,
            trading_days=TRADING_DAYS,
            rfr_annual=RFR_ANNUAL,
            signal_weights=SIGNAL_WEIGHTS,
            min_n=MIN_N,
            max_n=MAX_N,
            new_entry_threshold=NEW_ENTRY_THRESHOLD,
        )
    keep = result[["RANK", "PCT_FROM_52H", "REL_52H_DD", "COMPOSITE"]].copy()   # row order preserved
    return idx, keep, float(regime_score), {"dynamic_n": int(regime_detail["dynamic_n"]),
                                            "allow_new": bool(regime_detail["allow_new"])}


def mean_vol_4w(px):
    if len(px) <= 10:
        return None
    vols = []
    for w in VOL_WINDOWS:
        pw = px.iloc[-w:] if len(px) >= w else px
        lr = np.diff(np.log(pw.values))
        if len(lr) > 5:
            vols.append(np.std(lr, ddof=1) * np.sqrt(252))
    return float(np.mean(vols)) if vols else None


def run_stage1(smoke_weeks=None, use_partial=False, workers=3):
    file_mtime = os.path.getmtime(FILE)

    if use_partial and os.path.exists(CACHE_S1):
        with open(CACHE_S1, "rb") as f:
            c = pickle.load(f)
        print(f"Using stage-1 cache as-is ({len(c['records'])} weeks).")
        return c
    if smoke_weeks is None and os.path.exists(CACHE_S1):
        try:
            with open(CACHE_S1, "rb") as f:
                c = pickle.load(f)
            if c.get("file_mtime") == file_mtime and c.get("complete"):
                print(f"Stage-1 cache complete ({len(c['records'])} weeks) — skipping ranking pass.")
                return c
        except Exception as e:
            print(f"[cache] could not load stage-1 ({e}) — rebuilding.")

    print(f"Loading {FILE} (main process) ...")
    with suppress_stdout():
        prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
    dt_idx = pd.DatetimeIndex(dates)
    eow = [dates[i] for i in range(len(dt_idx) - 1)
           if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
    eow.append(dates[-1])
    valid_dates = [d for d in eow if dates.index(d) >= 252]
    if smoke_weeks:
        valid_dates = valid_dates[-(smoke_weeks + 1):]
    need = [dates.index(d) for d in valid_dates[:-1]]
    print(f"Universe: {len(stock_tickers)} tickers | rebalance weeks: {len(need)} | workers: {workers}")

    ranks = {}
    if smoke_weeks is None and os.path.exists(CACHE_RANKS):
        try:
            with open(CACHE_RANKS, "rb") as f:
                part = pickle.load(f)
            if part.get("file_mtime") == file_mtime:
                ranks = part["ranks"]
                print(f"[CHECKPOINT] {len(ranks)} weekly ranking frames already computed.")
        except Exception as e:
            print(f"[CHECKPOINT] could not load ({e}) — starting fresh.")

    todo = [i for i in need if i not in ranks]
    t0 = datetime.datetime.now()
    if todo:
        ctx = mp.get_context("spawn")
        with ctx.Pool(workers, initializer=_init_worker) as pool:
            for n_done, (idx, fr, rs, rd) in enumerate(pool.imap(_rank_week, todo, chunksize=1), 1):
                ranks[idx] = (fr, rs, rd)
                el = (datetime.datetime.now() - t0).total_seconds()
                eta = el / n_done * (len(todo) - n_done)
                sys.stdout.write(f"\r  ranked {n_done}/{len(todo)} | {dates[idx]:%b %Y} | "
                                 f"elapsed {el/60:6.1f} min | eta {eta/60:6.1f} min")
                sys.stdout.flush()
                if smoke_weeks is None and n_done % 20 == 0:
                    atomic_dump({"ranks": ranks, "file_mtime": file_mtime}, CACHE_RANKS)
        print()
    if smoke_weeks is None:
        atomic_dump({"ranks": ranks, "file_mtime": file_mtime}, CACHE_RANKS)

    # ── Stage 1b — sequential live-faithful state machine on the stored frames ──
    print("Building weekly portfolios ...")
    prices_ffill = prices_df.ffill(axis=1)
    nifty_ffill = nifty_series.ffill()
    records, current = [], {}
    for k in range(len(valid_dates) - 1):
        t_date, next_date = valid_dates[k], valid_dates[k + 1]
        idx, next_idx = dates.index(t_date), dates.index(next_date)
        fr, regime_score, rd = ranks[idx]
        dynamic_n, allow_new = rd["dynamic_n"], rd["allow_new"]

        kept = []
        exit_counts = {"52H": 0, "FILTER": 0, "REL_DD": 0, "RANK": 0}
        for ticker, entry_date in current.items():
            held_days = (t_date - entry_date).days
            inr = ticker in fr.index
            rank_val = fr.at[ticker, "RANK"] if inr else np.nan
            pct52h = fr.at[ticker, "PCT_FROM_52H"] if inr else np.nan
            reldd = fr.at[ticker, "REL_52H_DD"] if inr else np.nan
            if pd.notna(pct52h) and pct52h < -25:
                exit_counts["52H"] += 1
            elif pd.isna(rank_val):
                exit_counts["FILTER"] += 1
            elif pd.notna(reldd) and reldd < REL_DD_BREACH_THRESHOLD:
                if held_days >= MIN_HOLD_DAYS:
                    exit_counts["REL_DD"] += 1
                else:
                    kept.append(ticker)
            elif rank_val > HOLD_RANK_BUFFER:
                if held_days >= MIN_HOLD_DAYS:
                    exit_counts["RANK"] += 1
                else:
                    kept.append(ticker)
            else:
                kept.append(ticker)

        new = []
        if allow_new:
            held_set = set(kept)
            new = [t for t in fr.head(dynamic_n).index.tolist() if t not in held_set]
        tickers = kept + new

        comp, rank, vol4, ret = {}, {}, {}, {}
        for t in tickers:
            comp[t] = float(fr.at[t, "COMPOSITE"]) if t in fr.index else 0.0
            rank[t] = float(fr.at[t, "RANK"]) if t in fr.index else np.nan
            vol4[t] = mean_vol_4w(prices_df.loc[t].iloc[:idx + 1].dropna())
            sp, ep = prices_ffill.loc[t].iloc[idx], prices_ffill.loc[t].iloc[next_idx]
            ret[t] = float(ep / sp - 1.0) if sp and np.isfinite(sp) and np.isfinite(ep) else np.nan

        records.append({
            "date": t_date, "next_date": next_date, "regime_score": regime_score,
            "dynamic_n": dynamic_n, "allow_new": allow_new, "exit_counts": exit_counts,
            "tickers": tickers, "new": set(new),
            "comp": comp, "rank": rank, "vol4": vol4, "ret": ret,
            "nifty_ret": float(nifty_ffill.iloc[next_idx] / nifty_ffill.iloc[idx] - 1.0),
        })
        current = {t: (current[t] if t in current else t_date) for t in tickers}

    cache = {"records": records, "file_mtime": file_mtime, "complete": True,
             "n_universe": len(stock_tickers)}
    if smoke_weeks is None:
        atomic_dump(cache, CACHE_S1)
    return cache


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2 — sizing schemes
# ═════════════════════════════════════════════════════════════════════════════

def _clip_m(m, p):
    return float(np.clip(m, p["m_lo"], p["m_hi"]))


def _positions(rec):
    """pos in [0,1], 0 = best-ranked held name, 1 = worst (ordering among the held set)."""
    order = sorted(rec["tickers"], key=lambda t: rec["rank"][t] if np.isfinite(rec["rank"][t]) else np.inf)
    n = len(order)
    return {t: (i / (n - 1) if n > 1 else 0.5) for i, t in enumerate(order)}


def w_baseline(rec, prev, p):
    raw = {t: (rec["comp"][t] / rec["vol4"][t] if rec["vol4"][t] and rec["vol4"][t] > 0 else rec["comp"][t])
           for t in rec["tickers"]}
    total = sum(raw.values())
    n = len(rec["tickers"])
    return {t: min(BASE_WT, (raw[t] / total) if total > 0 else 1.0 / n) for t in rec["tickers"]}


def w_equal(rec, prev, p):
    return {t: BASE_WT for t in rec["tickers"]}


def w_tilt_a(rec, prev, p):
    pos = _positions(rec)
    return {t: BASE_WT * _clip_m(1 + p["delta"] * (1 - 2 * pos[t]), p) for t in rec["tickers"]}


def w_tilt_b(rec, prev, p):
    pos = _positions(rec)
    def m(x):
        return 1 + p["delta"] if x < 1 / 3 else (1 - p["delta"] if x > 2 / 3 else 1.0)
    return {t: BASE_WT * _clip_m(m(pos[t]), p) for t in rec["tickers"]}


def w_tilt_c(rec, prev, p):
    c = np.array([rec["comp"][t] for t in rec["tickers"]])
    sd = c.std(ddof=1) if len(c) > 1 else 0.0
    z = (c - c.mean()) / sd if sd > 0 else np.zeros(len(c))
    return {t: BASE_WT * _clip_m(1 + p["delta"] * np.clip(zi, -2, 2) / 2, p)
            for t, zi in zip(rec["tickers"], z)}


def _entry_sized(rec, prev, targets):
    """Live-style: held names keep their weight; new entries get their target, scaled pro-rata
    if there is not enough room left under 100%."""
    out = {t: prev[t] for t in rec["tickers"] if t in prev}
    new = [t for t in rec["tickers"] if t not in prev]
    avail = max(0.0, MAX_GROSS - sum(out.values()))
    need = sum(targets[t] for t in new)
    scale = min(1.0, avail / need) if need > 0 else 1.0
    for t in new:
        out[t] = targets[t] * scale
    return out


def w_equal_entry(rec, prev, p):
    return _entry_sized(rec, prev, {t: BASE_WT for t in rec["tickers"]})


def _entry_pos(rec, t):
    r = rec["rank"][t]
    return float(np.clip((r - 1) / max(rec["dynamic_n"] - 1, 1), 0, 1)) if np.isfinite(r) else 1.0


def w_tilt_a_entry(rec, prev, p):
    return _entry_sized(rec, prev, {t: BASE_WT * _clip_m(1 + p["delta"] * (1 - 2 * _entry_pos(rec, t)), p)
                                    for t in rec["tickers"]})


def w_tilt_b_entry(rec, prev, p):
    def m(x):
        return 1 + p["delta"] if x < 1 / 3 else (1 - p["delta"] if x > 2 / 3 else 1.0)
    return _entry_sized(rec, prev, {t: BASE_WT * _clip_m(m(_entry_pos(rec, t)), p) for t in rec["tickers"]})


# name -> (fn, delta_key or None, delta sign)
VARIANTS = {
    "BASELINE":     (w_baseline,     None, 1),
    "EQUAL":        (w_equal,        None, 1),
    "EQUAL_ENTRY":  (w_equal_entry,  None, 1),
    "TILT_A":       (w_tilt_a,       "A",  1),
    "TILT_A_REV":   (w_tilt_a,       "A", -1),
    "TILT_B":       (w_tilt_b,       "B",  1),
    "TILT_C":       (w_tilt_c,       "C",  1),
    "TILT_A_ENTRY": (w_tilt_a_entry, "A",  1),
    "TILT_B_ENTRY": (w_tilt_b_entry, "B",  1),
}


def params_for(name, delta_override=None, m_lo=DEF_M_LO, m_hi=DEF_M_HI):
    _, key, sign = VARIANTS[name]
    d = (delta_override if delta_override is not None else (DEF_DELTA[key] if key else 0.0)) * sign
    return {"delta": d, "m_lo": m_lo, "m_hi": m_hi}


def simulate(records, fn, p):
    equity = START_EQUITY
    cash_wk = (1.0 + LIQUID_YIELD_PA) ** (1 / 52) - 1.0
    prev, rows = {}, []
    for rec in records:
        w = fn(rec, prev, p) if rec["tickers"] else {}
        eq_w = sum(w.values())
        if eq_w > MAX_GROSS:
            w = {t: x * MAX_GROSS / eq_w for t, x in w.items()}
            eq_w = MAX_GROSS
        gross = sum(w[t] * (0.0 if not np.isfinite(rec["ret"][t]) else rec["ret"][t]) for t in w) \
            + max(0.0, 1.0 - eq_w) * cash_wk
        if not w:
            gross = cash_wk
        abs_chg = sum(abs(w.get(t, 0.0) - prev.get(t, 0.0)) for t in set(w) | set(prev))
        net = gross - abs_chg * FRICTION
        equity *= (1 + net)
        rows.append({"date": rec["date"], "net_ret": net, "equity": equity, "eq_weight": eq_w,
                     "turnover": abs_chg / 2.0, "n_held": len(w), "max_w": max(w.values()) if w else 0.0})
        prev = w
    return pd.DataFrame(rows)


def metrics(df, years):
    r = df["net_ret"].values
    eq = START_EQUITY * np.cumprod(1 + r)
    sd = np.std(r, ddof=1)
    cagr = ((eq[-1] / START_EQUITY) ** (1 / years) - 1) * 100
    mdd = (eq / np.maximum.accumulate(eq) - 1.0).min() * 100
    return {
        "CAGR%": cagr, "AnnVol%": sd * np.sqrt(52) * 100,
        "Sharpe": (np.mean(r) - RFR_ANNUAL / 52) / sd * np.sqrt(52) if sd > 0 else np.nan,
        "MaxDD%": mdd, "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "AvgExp%": df["eq_weight"].mean() * 100, "Turn%/wk": df["turnover"].mean() * 100,
        "AvgMaxWt%": df["max_w"].mean() * 100, "WorstWk%": r.min() * 100,
    }


def span_years(d0, d1):
    return max((d1 - d0).days / 365.25, 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=None)
    ap.add_argument("--use-partial", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    cache = run_stage1(smoke_weeks=args.smoke, use_partial=args.use_partial, workers=args.workers)
    records = cache["records"]
    first, last = pd.Timestamp(records[0]["date"]), pd.Timestamp(records[-1]["next_date"])
    years = span_years(first, last)
    held_counts = [len(r["tickers"]) for r in records]
    print(f"\n{len(records)} weeks, {first:%b %Y} to {last:%b %Y}; held names: "
          f"median {int(np.median(held_counts))}, max {max(held_counts)}")

    sims = {n: simulate(records, VARIANTS[n][0], params_for(n)) for n in VARIANTS}
    nifty_df = pd.DataFrame({"net_ret": [r["nifty_ret"] for r in records], "eq_weight": 1.0,
                             "turnover": 0.0, "max_w": 1.0})
    summary = pd.DataFrame({n: metrics(df, years) for n, df in sims.items()}).T
    summary.loc["NIFTY500"] = metrics(nifty_df, years)

    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
    print(f"\n=== RANK-TILT COMPARISON ({years:.2f}y) ===")
    print(summary.to_string())

    # reproduction check against the earlier LIVE_BASELINE run
    if os.path.exists(LIVE_BASELINE_CSV) and not args.smoke:
        live = pd.read_csv(LIVE_BASELINE_CSV)
        n = min(len(live), len(sims["BASELINE"]))
        diff = np.abs(sims["BASELINE"]["equity"].values[:n] / live["Equity"].values[:n] - 1).max() * 100
        print(f"\nBASELINE vs LIVE_BASELINE ({n} weeks): max equity difference {diff:.6f}%  "
              f"| final {sims['BASELINE']['equity'].iloc[n-1]:,.0f} vs {live['Equity'].iloc[n-1]:,.0f}")

    # split-period robustness
    dates_ = pd.Series(pd.to_datetime([r["date"] for r in records]))
    mask = (dates_ < SPLIT_DATE).values
    if mask.any() and (~mask).any():
        print(f"\n=== SPLIT-PERIOD (before / from {SPLIT_DATE:%Y-%m-%d}) — Sharpe | Calmar | CAGR% ===")
        rows = {}
        for n, df in sims.items():
            h1 = metrics(df[mask].reset_index(drop=True), span_years(dates_[mask].iloc[0], dates_[mask].iloc[-1]))
            h2 = metrics(df[~mask].reset_index(drop=True), span_years(dates_[~mask].iloc[0], last))
            rows[n] = {"H1 Sharpe": h1["Sharpe"], "H1 Calmar": h1["Calmar"], "H1 CAGR%": h1["CAGR%"],
                       "H2 Sharpe": h2["Sharpe"], "H2 Calmar": h2["Calmar"], "H2 CAGR%": h2["CAGR%"]}
        split = pd.DataFrame(rows).T
        print(split.to_string())
    else:
        split = None

    # sensitivity on delta and the clip range
    print("\n=== SENSITIVITY: delta x clip range  (Sharpe / Calmar / CAGR% / MaxDD% / Exp%) ===")
    sens = []
    for name in ("TILT_A", "TILT_A_ENTRY", "TILT_B", "TILT_C", "TILT_A_REV"):
        for d in (0.25, 0.5, 0.75):
            for lo, hi in ((0.5, 1.5), (0.75, 1.25)):
                pp = params_for(name, delta_override=d, m_lo=lo, m_hi=hi)
                m = metrics(simulate(records, VARIANTS[name][0], pp), years)
                sens.append({"variant": name, "delta": d, "clip": f"{lo}-{hi}", "Sharpe": m["Sharpe"],
                             "Calmar": m["Calmar"], "CAGR%": m["CAGR%"], "MaxDD%": m["MaxDD%"],
                             "AvgExp%": m["AvgExp%"], "Turn%/wk": m["Turn%/wk"]})
    sens = pd.DataFrame(sens)
    print(sens.to_string(index=False))

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = os.path.join(SHARPE_ROOT, "backtest results",
                           f"run_{ts}_{'smoke_' if args.smoke else ''}rank_tilt_nseall")
    os.makedirs(run_dir, exist_ok=True)
    summary.to_csv(os.path.join(run_dir, "summary.csv"))
    sens.to_csv(os.path.join(run_dir, "sensitivity.csv"), index=False)
    if split is not None:
        split.to_csv(os.path.join(run_dir, "split_period.csv"))
    for n, df in sims.items():
        df.to_csv(os.path.join(run_dir, f"weekly_{n}.csv"), index=False)
    try:
        fig, ax = plt.subplots(2, 1, figsize=(12, 9), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
        for n in ("BASELINE", "EQUAL", "EQUAL_ENTRY", "TILT_A", "TILT_A_REV", "TILT_A_ENTRY"):
            eq = START_EQUITY * np.cumprod(1 + sims[n]["net_ret"].values)
            ax[0].plot(dates_, eq, label=f"{n} (Sharpe {summary.loc[n, 'Sharpe']:.2f})", linewidth=1.6)
            ax[1].plot(dates_, eq / np.maximum.accumulate(eq) - 1.0, linewidth=1.1)
        ax[0].set_yscale("log")
        ax[0].set_title("Rank-tilt sizing vs equal weight — NSEAll, identical portfolios")
        ax[0].legend()
        ax[0].grid(True, alpha=0.3)
        ax[1].set_ylabel("Drawdown")
        ax[1].grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(run_dir, "equity_curves.png"), dpi=200)
        plt.close()
    except Exception as e:
        print(f"Could not generate chart: {e}")
    try:
        shutil.copy2(__file__, os.path.join(run_dir, os.path.basename(__file__)))
    except Exception:
        pass
    print(f"\nSaved to {run_dir}")


if __name__ == "__main__":
    main()
