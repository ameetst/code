"""
Volatility-based position sizing study on the live NSEAll strategy.

Why one script runs every variant
  Which names are held (entries, the 4 live exit triggers, the 28-day hold lock,
  regime-driven dynamic_n) does NOT depend on position weights. So the expensive
  point-in-time ranking pass runs ONCE (stage 1) and records, for every weekly
  rebalance, the held/new tickers plus their COMPOSITE, volatility and next-week
  return. Stage 2 then applies each sizing scheme to that identical portfolio —
  a like-for-like comparison, and a parameter sweep costs seconds.

Stage 1 mirrors backtest_live_baseline_nseall.py (same ranking call, same exits,
same entries) and does not import or modify any live code except read-only
momentum_lib.

Sizing variants (stage 2)
  BASELINE      live formula: COMPOSITE/mean_vol, normalised over the book, cap 1/MAX_N,
                full weekly reweight. Must reproduce LIVE_BASELINE.
  EQUAL         flat 1/MAX_N per name. What the live book effectively is, because the
                cap binds on nearly every name. Control for "does BASELINE do anything?"
  INVVOL        w = clip(BASE_WT * SIGMA_REF / sigma, W_MIN, W_MAX). Per-name risk budget:
                absolute, not normalised against the other names. Full weekly reweight.
  INVVOL_BAND   same targets, but sized at entry and only re-sized when the target moves
                more than BAND away from the held weight. Mirrors how live works.
  INVVOL_EWMA   INVVOL with a 60-day-halflife-style EWMA (lambda 0.94) sigma instead of the
                4-window mean.

Exposure is whatever the weights sum to; the remainder earns LIQUID_YIELD_PA, as in the
baseline. Compare Sharpe/Calmar as well as CAGR, since INVVOL changes average exposure.

Usage
  python backtest_vol_sizing_nseall.py                 # full run (resumes from cache)
  python backtest_vol_sizing_nseall.py --smoke 20      # last 20 weeks only, no cache
  python backtest_vol_sizing_nseall.py --refresh       # rebuild stage-1 cache
  python backtest_vol_sizing_nseall.py --use-partial   # analyse the weeks cached so far
  python backtest_vol_sizing_nseall.py --sigma-ref 0.5 --w-max 0.07 --w-min 0.02 --band 0.4
"""

import sys
import os
import argparse
import datetime
import warnings
import pickle
import shutil
from contextlib import contextmanager

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

SHARPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SHARPE_ROOT not in sys.path:
    sys.path.insert(0, SHARPE_ROOT)

import momentum_lib as ml

warnings.filterwarnings("ignore")

# ── CONFIG (mirrors backtest_live_baseline_nseall.py / dashboard_config.json) ─
FILE = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated_NSEAll.xlsx"
STOCKDB_CSV = os.path.join(SHARPE_ROOT, "STOCKDB.csv")

RFR_ANNUAL      = 0.07
TRADING_DAYS    = 252
FRICTION        = 0.002
LIQUID_YIELD_PA = 0.06
START_EQUITY    = 2000000.0

SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
VOL_WINDOWS    = [252, 189, 126, 63]
EWMA_LAMBDA    = 0.94

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
HOLD_RANK_BUFFER        = 75
MIN_HOLD_DAYS           = 28

BASE_WT = 1.0 / MAX_N          # 4% — the weight a SIGMA_REF-vol name gets
MAX_GROSS = 1.0                # no leverage: scale weights down pro-rata if they sum above this

# Sizing defaults; overridable from the CLI. SIGMA_REF=None -> calibrated from the data
# (median sigma of held names across the run). It only sets the overall exposure level.
DEF_W_MIN = 0.02
DEF_W_MAX = 0.065
DEF_BAND  = 0.40

CACHE_PATH = os.path.join(SHARPE_ROOT, "backtest results", ".stage1_vol_sizing_nseall.pkl")


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 1 — point-in-time ranking pass, run once
# ═════════════════════════════════════════════════════════════════════════════

def mean_vol_4w(px):
    """Same estimator as the live weight-sizing engine: mean of 4 windowed annualised vols."""
    if len(px) <= 10:
        return None
    vols = []
    for w in VOL_WINDOWS:
        pw = px.iloc[-w:] if len(px) >= w else px
        lr = np.diff(np.log(pw.values))
        if len(lr) > 5:
            vols.append(np.std(lr, ddof=1) * np.sqrt(252))
    return float(np.mean(vols)) if vols else None


def ewma_vol(px, lam=EWMA_LAMBDA, window=252):
    if len(px) <= 10:
        return None
    lr = np.diff(np.log(px.iloc[-window:].values))
    lr = lr[np.isfinite(lr)]
    if len(lr) <= 5:
        return None
    wts = (1 - lam) * lam ** np.arange(len(lr))[::-1]      # most recent obs weighted highest
    return float(np.sqrt(np.sum(wts * lr ** 2) / np.sum(wts) * 252))


def run_stage1(smoke_weeks=None, refresh=False, use_partial=False):
    file_mtime = os.path.getmtime(FILE)

    if use_partial and os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "rb") as f:
            cache = pickle.load(f)
        print(f"Using stage-1 cache as-is ({len(cache['records'])} weeks, complete={cache.get('complete')}).")
        return cache

    if smoke_weeks is None and not refresh and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "rb") as f:
                cache = pickle.load(f)
            if cache.get("complete") and cache.get("file_mtime") == file_mtime:
                print(f"Stage-1 cache is complete ({len(cache['records'])} weeks) — skipping ranking pass.")
                return cache
        except Exception as e:
            print(f"[cache] could not load ({e}) — rebuilding.")

    print(f"Loading {FILE} ...")
    prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)
    volume_df = ml.load_volume(FILE)
    print(f"Volume data: {'loaded' if volume_df is not None else 'NOT FOUND — MDTV filter passes everything'}")

    dt_idx = pd.DatetimeIndex(dates)
    eow_dates = [dates[i] for i in range(len(dt_idx) - 1)
                 if dt_idx[i].isocalendar().week != dt_idx[i + 1].isocalendar().week]
    eow_dates.append(dates[-1])

    start_idx = 252
    valid_dates = [d for d in eow_dates if dates.index(d) >= start_idx]
    if smoke_weeks:
        valid_dates = valid_dates[-(smoke_weeks + 1):]
    print(f"Universe: {len(stock_tickers)} tickers | rebalance points: {len(valid_dates)}")
    if len(valid_dates) < 2:
        print("[!] Insufficient data for backtesting.")
        sys.exit(0)

    prices_ffill = prices_df.ffill(axis=1)
    nifty_ffill = nifty_series.ffill()

    records = []
    current_portfolio = {}      # ticker -> {'entry_date'}
    start_i = 0

    if smoke_weeks is None and not refresh and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "rb") as f:
                part = pickle.load(f)
            if part.get("file_mtime") == file_mtime and not part.get("complete"):
                records = part["records"]
                current_portfolio = part["current_portfolio"]
                start_i = part["i"] + 1
                print(f"[CHECKPOINT] resuming from rebalance {start_i}/{len(valid_dates) - 1}")
        except Exception as e:
            print(f"[CHECKPOINT] could not load ({e}) — starting fresh.")

    t0 = datetime.datetime.now()
    for i in range(start_i, len(valid_dates) - 1):
        t_date, next_date = valid_dates[i], valid_dates[i + 1]
        idx, next_idx = dates.index(t_date), dates.index(next_date)

        sliced_prices = prices_df.iloc[:, :idx + 1]
        sliced_nifty = nifty_series.iloc[:idx + 1]
        sliced_volume = volume_df.iloc[:, :idx + 1] if volume_df is not None else None

        with suppress_stdout():
            result, regime_score, regime_detail = ml.compute_universe_rankings(
                sliced_prices, sliced_nifty, stock_tickers,
                volume_df=sliced_volume,
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

        dynamic_n = regime_detail["dynamic_n"]
        allow_new = regime_detail["allow_new"]

        # EXIT EVALUATION — identical to backtest_live_baseline_nseall.py
        kept = []
        exit_counts = {"52H": 0, "FILTER": 0, "REL_DD": 0, "RANK": 0}
        for ticker, state in current_portfolio.items():
            held_days = (t_date - state['entry_date']).days
            rank_val = result.loc[ticker, "RANK"] if ticker in result.index else np.nan
            pct52h = result.loc[ticker, "PCT_FROM_52H"] if ticker in result.index else np.nan
            reldd = result.loc[ticker, "REL_52H_DD"] if ticker in result.index else np.nan

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

        # ENTRY — top dynamic_n by RANK, no backfill
        new = []
        if allow_new:
            held_set = set(kept)
            new = [t for t in result.head(dynamic_n).index.tolist() if t not in held_set]
        tickers = kept + new

        comp, vol4, volE, ret = {}, {}, {}, {}
        for t in tickers:
            comp[t] = float(result.loc[t, "COMPOSITE"]) if t in result.index else 0.0
            px = prices_df.loc[t].iloc[:idx + 1].dropna()
            vol4[t] = mean_vol_4w(px)
            volE[t] = ewma_vol(px)
            sp = prices_ffill.loc[t].iloc[idx]
            ep = prices_ffill.loc[t].iloc[next_idx]
            ret[t] = float(ep / sp - 1.0) if sp and np.isfinite(sp) and np.isfinite(ep) else np.nan

        records.append({
            "date": t_date, "next_date": next_date,
            "regime_score": float(regime_score), "dynamic_n": int(dynamic_n),
            "allow_new": bool(allow_new), "exit_counts": exit_counts,
            "tickers": tickers, "new": set(new),
            "comp": comp, "vol4": vol4, "volE": volE, "ret": ret,
            "nifty_ret": float(nifty_ffill.iloc[next_idx] / nifty_ffill.iloc[idx] - 1.0),
        })

        current_portfolio = {t: {'entry_date': (current_portfolio[t]['entry_date'] if t in current_portfolio else t_date)}
                             for t in tickers}

        elapsed = (datetime.datetime.now() - t0).total_seconds()
        sys.stdout.write(f"\r  [{i + 1}/{len(valid_dates) - 1}] {t_date.strftime('%b %Y')} | "
                         f"RS {regime_score:.2f} N={dynamic_n:2d} held={len(tickers):2d} | elapsed {elapsed:6.0f}s")
        sys.stdout.flush()

        if smoke_weeks is None:
            tmp = CACHE_PATH + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump({"records": records, "current_portfolio": current_portfolio, "i": i,
                             "file_mtime": file_mtime, "complete": False}, f)
            os.replace(tmp, CACHE_PATH)

    print("\nStage 1 complete.")
    cache = {"records": records, "file_mtime": file_mtime, "complete": True,
             "n_universe": len(stock_tickers)}
    if smoke_weeks is None:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(cache, f)
        os.replace(tmp, CACHE_PATH)
    return cache


# ═════════════════════════════════════════════════════════════════════════════
# STAGE 2 — apply each sizing scheme to the identical portfolio
# ═════════════════════════════════════════════════════════════════════════════

def w_baseline(rec, prev, p):
    raw = {}
    for t in rec["tickers"]:
        v = rec["vol4"][t]
        raw[t] = rec["comp"][t] / v if v and v > 0 else rec["comp"][t]
    total = sum(raw.values())
    n = len(rec["tickers"])
    return {t: min(BASE_WT, (raw[t] / total) if total > 0 else 1.0 / n) for t in rec["tickers"]}


def w_equal(rec, prev, p):
    return {t: BASE_WT for t in rec["tickers"]}


def _invvol_targets(rec, key, p):
    out = {}
    for t in rec["tickers"]:
        v = rec[key][t]
        out[t] = float(np.clip(BASE_WT * p["sigma_ref"] / v, p["w_min"], p["w_max"])) if v and v > 0 else BASE_WT
    return out


def w_invvol(rec, prev, p):
    return _invvol_targets(rec, "vol4", p)


def w_invvol_ewma(rec, prev, p):
    return _invvol_targets(rec, "volE", p)


def w_invvol_band(rec, prev, p):
    target = _invvol_targets(rec, "vol4", p)
    out = {}
    for t in rec["tickers"]:
        old = prev.get(t)
        if old is None or old <= 0 or abs(target[t] / old - 1.0) > p["band"]:
            out[t] = target[t]       # new entry, or drifted outside the band -> re-size
        else:
            out[t] = old             # inside band -> leave alone
    return out


VARIANTS = {
    "BASELINE":    w_baseline,
    "EQUAL":       w_equal,
    "INVVOL":      w_invvol,
    "INVVOL_BAND": w_invvol_band,
    "INVVOL_EWMA": w_invvol_ewma,
}


def simulate(records, fn, p):
    equity = START_EQUITY
    cash_wk = (1.0 + LIQUID_YIELD_PA) ** (1 / 52) - 1.0
    prev = {}
    rows = []
    for rec in records:
        w = fn(rec, prev, p) if rec["tickers"] else {}
        eq_w = sum(w.values())
        if eq_w > MAX_GROSS:    # the book can hold >MAX_N names (hold buffer); live scales pro-rata to cash
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
                     "turnover": abs_chg / 2.0, "n_held": len(w),
                     "max_w": max(w.values()) if w else 0.0})
        prev = w
    return pd.DataFrame(rows)


def metrics(df, years):
    r = df["net_ret"].values
    eq = df["equity"]
    cagr = ((eq.iloc[-1] / START_EQUITY) ** (1 / years) - 1) * 100
    ann_vol = np.std(r, ddof=1) * np.sqrt(52) * 100
    sharpe = (np.mean(r) - RFR_ANNUAL / 52) / np.std(r, ddof=1) * np.sqrt(52) if np.std(r, ddof=1) > 0 else np.nan
    dd = (eq / eq.cummax() - 1.0)
    mdd = dd.min() * 100
    return {
        "CAGR%": cagr, "AnnVol%": ann_vol, "Sharpe": sharpe, "MaxDD%": mdd,
        "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "AvgExposure%": df["eq_weight"].mean() * 100,
        "AvgTurnover%/wk": df["turnover"].mean() * 100,
        "AvgMaxWt%": df["max_w"].mean() * 100,
        "FinalEquity": eq.iloc[-1],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", type=int, default=None, help="run only the last N weekly rebalances, no cache")
    ap.add_argument("--refresh", action="store_true", help="ignore/rebuild the stage-1 cache")
    ap.add_argument("--use-partial", action="store_true",
                    help="analyse whatever weeks the stage-1 cache holds so far (no ranking pass)")
    ap.add_argument("--sigma-ref", type=float, default=None, help="vol that earns BASE_WT (default: calibrated)")
    ap.add_argument("--w-min", type=float, default=DEF_W_MIN)
    ap.add_argument("--w-max", type=float, default=DEF_W_MAX)
    ap.add_argument("--band", type=float, default=DEF_BAND)
    args = ap.parse_args()

    cache = run_stage1(smoke_weeks=args.smoke, refresh=args.refresh, use_partial=args.use_partial)
    records = cache["records"]

    all_sig = [rec["vol4"][t] for rec in records for t in rec["tickers"] if rec["vol4"][t]]
    calibrated = float(np.median(all_sig))
    sigma_ref = args.sigma_ref if args.sigma_ref else round(calibrated, 2)
    p = {"sigma_ref": sigma_ref, "w_min": args.w_min, "w_max": args.w_max, "band": args.band}
    print(f"\nSigma of held names: median {calibrated:.3f}, "
          f"p10 {np.percentile(all_sig, 10):.3f}, p90 {np.percentile(all_sig, 90):.3f}")
    print(f"INVVOL params: sigma_ref={sigma_ref}  base_wt={BASE_WT:.3f}  "
          f"w_min={p['w_min']}  w_max={p['w_max']}  band={p['band']}")

    first, last = records[0]["date"], records[-1]["next_date"]
    years = max((last - first).days / 365.25, 1e-9)

    sims = {name: simulate(records, fn, p) for name, fn in VARIANTS.items()}
    nifty_eq = START_EQUITY * np.cumprod([1 + r["nifty_ret"] for r in records])

    summary = pd.DataFrame({name: metrics(df, years) for name, df in sims.items()}).T
    nifty_df = pd.DataFrame({"net_ret": [r["nifty_ret"] for r in records],
                             "equity": nifty_eq, "eq_weight": 1.0, "turnover": 0.0, "max_w": 1.0})
    summary.loc["NIFTY500"] = metrics(nifty_df, years)

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
    print(f"\n=== VOL-SIZING COMPARISON ({first:%b %Y} to {last:%b %Y}, {years:.2f}y, {len(records)} weeks) ===")
    print(summary.drop(columns=["FinalEquity"]).to_string())

    # Sensitivity of INVVOL to the reference vol and the max weight
    print("\n=== INVVOL SENSITIVITY (sigma_ref x w_max) — CAGR% / Sharpe / MaxDD% / Exposure% ===")
    sens_rows = []
    for sr in [round(sigma_ref * m, 2) for m in (0.8, 0.9, 1.0, 1.1, 1.25)]:
        for wm in (0.05, 0.065, 0.08):
            pp = dict(p, sigma_ref=sr, w_max=wm)
            m = metrics(simulate(records, w_invvol, pp), years)
            sens_rows.append({"sigma_ref": sr, "w_max": wm, **{k: m[k] for k in
                              ("CAGR%", "Sharpe", "MaxDD%", "Calmar", "AvgExposure%", "AvgTurnover%/wk")}})
    sens = pd.DataFrame(sens_rows)
    print(sens.to_string(index=False))

    # Sanity: baseline vs live-baseline cap-binding diagnostics
    b = sims["BASELINE"]
    capped_share = np.mean([
        np.mean([abs(wt - BASE_WT) < 1e-9 for wt in w_baseline(rec, {}, p).values()]) for rec in records if rec["tickers"]
    ]) * 100
    print(f"\nBASELINE weights sitting exactly at the {BASE_WT:.0%} cap: {capped_share:.0f}% of position-weeks "
          f"(vol-weighting is inert wherever this is high)")

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tag = "smoke_" if args.smoke else ""
    run_dir = os.path.join(SHARPE_ROOT, "backtest results", f"run_{timestamp}_{tag}vol_sizing_nseall")
    os.makedirs(run_dir, exist_ok=True)
    summary.to_csv(os.path.join(run_dir, "summary.csv"))
    sens.to_csv(os.path.join(run_dir, "invvol_sensitivity.csv"), index=False)
    for name, df in sims.items():
        df.to_csv(os.path.join(run_dir, f"weekly_{name}.csv"), index=False)

    try:
        fig, ax = plt.subplots(2, 1, figsize=(12, 9), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
        dates_ = [r["date"] for r in records]
        for name, df in sims.items():
            cg = summary.loc[name, "CAGR%"]
            ax[0].plot(dates_, df["equity"], label=f"{name} (CAGR {cg:.1f}%)", linewidth=1.8)
        ax[0].plot(dates_, nifty_eq, label=f"NIFTY500 (CAGR {summary.loc['NIFTY500', 'CAGR%']:.1f}%)",
                   color="#555", linestyle="--", linewidth=1.5)
        ax[0].set_yscale("log")
        ax[0].set_title("Volatility-based sizing vs live baseline — NSEAll (same portfolio selection)")
        ax[0].legend()
        ax[0].grid(True, alpha=0.3)
        for name, df in sims.items():
            ax[1].plot(dates_, df["equity"] / df["equity"].cummax() - 1.0, label=name, linewidth=1.2)
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
