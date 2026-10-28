"""
ETF Backtest -- Keller "MAA" (Modern Asset Allocation) allocator vs the live ETF strategy
=====================================================================================
EVALUATION ONLY. Nothing here is wired into etf_momentum_ranking.py.

Implements the long-only single-index max-Sharpe allocator from Keller (2014),
"Momentum, Markowitz, and Smart Beta" (SSRN 2450017), on the same data and cleaning
the live-strategy backtests use (History_ETFs.xlsx + detect_and_clean):

  per rebalance, over a lookback of L trading days (default 84 ~ 4 months):
    r_i   = rate of change of price            (expected return, momentum)
    v_i   = daily volatility
    m_t   = equal-weight mean return of the candidate pool (the "market")
    c_i   = correlation with m_t ;  b_i = v_i * c_i / v_m  (beta)
    shrink r_i -> pool mean (WR), v_i -> mean (WV), c_i -> mean (WC), market variance
    s -> WM * s                          (default weights 50% each, as in the paper)
    se_i  = v_i^2 - s * b_i^2            (idiosyncratic variance)
    w_i  ~ (r_i - T*b_i) / se_i  for assets with r_i - T*b_i > 0, else 0
    T     = s*sum_P(r_j b_j/se_j) / (1 + s*sum_P b_j^2/se_j)   (iterated to a fixed point)
  This is the paper's  w_i ~ (1 - T/t_i) r_i / se_i  with t_i = r_i/b_i (Treynor ratio),
  written so that assets with beta <= 0 are handled too. Weights are normalised to 100%;
  if no asset has positive momentum the book is 100% cash.

Timing: signal at the last close of each month, executed at the next trading day's close
(as in the paper). Cash earns 2% p.a. (same as the live backtest engine).

Variants (headline = paper defaults):
  EW-pool          : equal weight over the candidate pool, monthly (what weighting alone gives)
  MAA-broad        : MAA on every eligible ETF (paper-faithful, no screens)
  MAA-sector       : MAA on one ETF per sector (best lookback ROC) after the live 52wk-high screen
  MAA-sector-cap25 : same, max 25% in any ETF
plus sensitivity runs (lookback, shrinkage) on MAA-sector-cap25, and a cost ladder.

Usage:  python etf_backtest_keller_maa.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_BT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BT_DIR))

import etf_backtest_current_vs_nifty500 as bt                       # noqa: E402
from etf_backtest_current_vs_nifty500_clean import detect_and_clean  # noqa: E402

emr = bt.emr
START = 1_000_000.0
CASH_PA = bt.CASH_INTEREST_PA          # 2% p.a., accrued per trading row as /365 (engine convention)
FLAT_FEE = bt.TRADE_COST_FIXED         # Rs 20 per trade leg
MIN_HISTORY_IDX = bt.MIN_HISTORY_DAYS  # 260 rows warm-up, same as the live-strategy backtests
MAX_ZERO_RET_FRAC = 0.25               # stale / non-trading ETFs are excluded (zero-variance trap)
SCREEN_DD = 0.25                       # live 52-week-high screen


# ---------------------------------------------------------------- allocator
def maa_weights(R: np.ndarray, roc: np.ndarray, p: dict) -> np.ndarray:
    """R: (n_assets x L) daily simple returns; roc: (n_assets,) price rate of change.
    Returns weights (n_assets,) summing to <= 1 (the rest is cash)."""
    n, L = R.shape
    if n == 0:
        return np.zeros(0)
    if n == 1:
        return np.array([1.0 if roc[0] > 0 else 0.0])
    mkt = R.mean(axis=0)
    v_m = mkt.std(ddof=1)
    r = roc / L                                  # per-day expected return (unit-consistent with daily var)
    v = R.std(axis=1, ddof=1)
    Rc = R - R.mean(axis=1, keepdims=True)
    mc = mkt - mkt.mean()
    c = (Rc @ mc) / ((L - 1) * v * v_m + 1e-18)
    c = np.clip(c, -0.999, 0.999)

    r_s = p["WR"] * r + (1 - p["WR"]) * r.mean()
    v_s = p["WV"] * v + (1 - p["WV"]) * v.mean()
    c_s = np.clip(p["WC"] * c + (1 - p["WC"]) * c.mean(), -0.999, 0.999)
    s = p["WM"] * v_m ** 2
    b = v_s * c_s / v_m
    se = np.maximum(v_s ** 2 - s * b ** 2, 1e-12)

    P = r_s > 0
    z = np.zeros(n)
    for _ in range(200):
        if not P.any():
            return np.zeros(n)
        T = s * np.sum(r_s[P] * b[P] / se[P]) / (1 + s * np.sum(b[P] ** 2 / se[P]))
        z = (r_s - T * b) / se
        Pn = z > 0
        if (Pn == P).all():
            break
        P = Pn
    w = np.where(P, np.maximum(z, 0.0), 0.0)
    tot = w.sum()
    return w / tot if tot > 0 else np.zeros(n)


def cap_weights(w: np.ndarray, cap: float) -> np.ndarray:
    """Cap each weight; redistribute the excess pro-rata to uncapped holdings; any excess
    that cannot be placed stays in cash."""
    w = w.copy()
    for _ in range(30):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        room = (~over) & (w > 0)
        if not room.any():
            break
        w[room] += excess * w[room] / w[room].sum()
    return np.minimum(w, cap)


# ---------------------------------------------------------------- schedule
def month_end_signal_idx(dates: pd.DatetimeIndex) -> list:
    out = []
    for i in range(len(dates) - 1):
        if dates[i].month != dates[i + 1].month:
            out.append(i)
    return out


def build_schedule(prices: pd.DataFrame, sector: dict, excluded: set, p: dict):
    """dict: exec row index -> weight vector over prices.columns."""
    px = prices.values
    n_all = px.shape[1]
    cols = list(prices.columns)
    L = p["L"]
    sched, diag = {}, []
    ok_sector = np.array([sector[t] not in excluded for t in cols])
    for s_idx in month_end_signal_idx(prices.index):
        e_idx = s_idx + 1
        if e_idx < MIN_HISTORY_IDX or s_idx < L:
            continue
        win = px[s_idx - L: s_idx + 1, :]                     # L+1 prices through signal close
        full = ~np.isnan(win).any(axis=0) & ok_sector
        rets = win[1:] / win[:-1] - 1.0
        zero_frac = (np.abs(rets) < 1e-12).mean(axis=0)
        elig = full & (zero_frac <= MAX_ZERO_RET_FRAC)
        if p.get("screen"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)   # ETFs not yet listed: all-NaN column
                hi = np.nanmax(px[max(0, s_idx - 251): s_idx + 1, :], axis=0)
            cur = px[s_idx, :]
            with np.errstate(invalid="ignore", divide="ignore"):
                dd = (hi - cur) / hi
            elig &= np.where(np.isnan(dd), True, dd <= SCREEN_DD)
        idx = np.where(elig)[0]
        if len(idx) == 0:
            sched[e_idx] = np.zeros(n_all)
            continue
        roc = win[-1, idx] / win[0, idx] - 1.0
        if p.get("one_per_sector"):
            best = {}
            for k, j in enumerate(idx):
                sec = sector[cols[j]]
                if sec not in best or roc[k] > roc[best[sec]]:
                    best[sec] = k
            keep = np.array(sorted(best.values()))
            idx, roc = idx[keep], roc[keep]
        R = rets[:, idx].T
        if p["mode"] == "ew":
            w_sub = np.full(len(idx), 1.0 / len(idx))
        else:
            w_sub = maa_weights(R, roc, p)
            if p.get("cap"):
                w_sub = cap_weights(w_sub, p["cap"])
        w = np.zeros(n_all)
        w[idx] = w_sub
        sched[e_idx] = w
        diag.append((prices.index[e_idx], len(idx), int((w_sub > 0).sum()), float(w_sub.max()) if len(w_sub) else 0.0))
    return sched, pd.DataFrame(diag, columns=["date", "pool", "held", "max_w"]).set_index("date")


# ---------------------------------------------------------------- simulation
def simulate(prices: pd.DataFrame, sched: dict, bps: float):
    rets = prices.pct_change().fillna(0.0).values
    T, N = rets.shape
    hold = np.zeros(N)
    cash = START
    eq = np.empty(T)
    turn = []
    for i in range(T):
        hold *= (1.0 + rets[i])
        cash *= (1.0 + CASH_PA / 365.0)
        if i in sched:
            w = sched[i]
            equity = hold.sum() + cash
            target = w * equity
            delta = target - hold
            notional = np.abs(delta).sum()
            legs = int((np.abs(delta) > 1.0).sum())
            cost = notional * bps / 1e4 + legs * FLAT_FEE
            scale = (equity - cost) / equity
            hold = target * scale
            cash = (1.0 - w.sum()) * equity * scale
            turn.append((prices.index[i], notional / equity))
        eq[i] = hold.sum() + cash
    return pd.Series(eq, index=prices.index), pd.DataFrame(turn, columns=["date", "turnover"]).set_index("date")


def metrics(eq: pd.Series) -> dict:
    m = bt.compute_metrics(eq)
    m["sharpe_rf6"] = (m["cagr"] - 0.06) / m["vol"] if m["vol"] > 0 else 0.0
    return m


def rebase(s: pd.Series, start) -> pd.Series:
    s = s[s.index >= start]
    return s / s.iloc[0] * START


# ---------------------------------------------------------------- main
def main():
    meta, raw = bt.load_history_etfs(bt.HISTORY_FILE)
    prices, _ = detect_and_clean(raw)
    cols = list(prices.columns)
    sector = {t: emr.classify_sector(t, t) for t in cols}
    excluded = set(emr.CONFIG.EXCLUDE_SECTORS)
    print(f"[data] {len(cols)} ETFs | excluded sectors {sorted(excluded)}: "
          f"{sum(sector[t] in excluded for t in cols)} ETFs | {len(set(sector.values()))} sector labels")

    base = dict(mode="maa", L=84, WR=0.5, WV=0.5, WM=0.5, WC=0.5)
    variants = {
        "EW-pool (sector1+screen)": dict(base, mode="ew", screen=True, one_per_sector=True),
        "MAA-broad":                dict(base),
        "MAA-sector":               dict(base, screen=True, one_per_sector=True),
        "MAA-sector-cap25":         dict(base, screen=True, one_per_sector=True, cap=0.25),
    }
    sens = {
        "L=63 (3M)":   dict(base, L=63,  screen=True, one_per_sector=True, cap=0.25),
        "L=126 (6M)":  dict(base, L=126, screen=True, one_per_sector=True, cap=0.25),
        "WR=25%":      dict(base, WR=0.25, screen=True, one_per_sector=True, cap=0.25),
        "WR=100%":     dict(base, WR=1.0,  screen=True, one_per_sector=True, cap=0.25),
        "WV=10%":      dict(base, WV=0.10, screen=True, one_per_sector=True, cap=0.25),
        "cap 15%":     dict(base, screen=True, one_per_sector=True, cap=0.15),
        "cap 35%":     dict(base, screen=True, one_per_sector=True, cap=0.35),
    }

    allv = {**variants, **sens}
    scheds, diags = {}, {}
    for name, p in allv.items():
        scheds[name], diags[name] = build_schedule(prices, sector, excluded, p)
    first_exec = min(min(s) for s in scheds.values())
    start = prices.index[first_exec]
    print(f"[window] first rebalance executes {start.date()} -> {prices.index[-1].date()}")

    # reference curves from the existing backtest outputs (same data/period)
    live = pd.read_csv(_BT_DIR / "noentry_live___52wk_filter__current_equity.csv",
                       parse_dates=["date"]).set_index("date")["equity"]
    bench = pd.read_csv(_BT_DIR / "benchmark_equity_clean.csv", index_col=0, parse_dates=True).iloc[:, 0]
    refs = {"Live strategy (Sharpe+Clenow)": rebase(live, start), "Nifty 500 buy&hold": rebase(bench, start)}

    def row(name, eq, extra=None):
        m = metrics(eq)
        d = {"CAGR": m["cagr"], "Vol": m["vol"], "MaxDD": m["max_dd"], "CAGR/vol": m["sharpe"],
             "(CAGR-6%)/vol": m["sharpe_rf6"]}
        d.update(extra or {})
        return pd.Series(d, name=name)

    print("\n" + "=" * 100)
    print(f"HEADLINE  (window {start.date()} -> {prices.index[-1].date()}, 20 bps one-way + Rs20/leg)")
    print("=" * 100)
    rows, curves = [], {}
    for name in variants:
        eq, turn = simulate(prices, scheds[name], bps=20)
        eq = rebase(eq, start)
        curves[name] = eq
        d = diags[name]
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        rows.append(row(name, eq, {"AnnTurnover": turn["turnover"].sum() / yrs,
                                   "AvgHeld": d["held"].mean(), "AvgMaxW": d["max_w"].mean()}))
    for name, eq in refs.items():
        rows.append(row(name, eq))
        curves[name] = eq
    tbl = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(tbl.to_string(float_format=lambda x: f"{x:,.3f}"))
    tbl.to_csv(_BT_DIR / "keller_maa_headline.csv")

    print("\n" + "=" * 100)
    print("COST LADDER (CAGR / MaxDD) -- one-way bps on traded notional, plus Rs20 per leg")
    print("=" * 100)
    ladder = {}
    for name in variants:
        out = {}
        for bps in (0, 10, 20, 30):
            eq, _ = simulate(prices, scheds[name], bps=bps)
            m = metrics(rebase(eq, start))
            out[f"{bps}bps"] = f"{m['cagr']*100:5.1f}% / {m['max_dd']*100:6.1f}%"
        ladder[name] = out
    print(pd.DataFrame(ladder).T.to_string())
    for name, eq in refs.items():
        m = metrics(eq)
        print(f"{name:<32} {m['cagr']*100:5.1f}% / {m['max_dd']*100:6.1f}%   (live strategy cost: Rs20/leg only)")

    print("\n" + "=" * 100)
    print("SENSITIVITY on MAA-sector-cap25 (20 bps one-way)")
    print("=" * 100)
    srows = []
    eq, turn = simulate(prices, scheds["MAA-sector-cap25"], 20)
    srows.append(row("MAA-sector-cap25 (default)", rebase(eq, start)))
    for name in sens:
        eq, _ = simulate(prices, scheds[name], 20)
        srows.append(row(name, rebase(eq, start), {"AvgHeld": diags[name]["held"].mean()}))
    print(pd.DataFrame(srows).to_string(float_format=lambda x: f"{x:,.3f}"))
    pd.DataFrame(srows).to_csv(_BT_DIR / "keller_maa_sensitivity.csv")

    print("\n" + "=" * 100)
    print("CALENDAR-YEAR RETURNS (%), 20 bps")
    print("=" * 100)
    yr = pd.DataFrame({k: v.resample("YE").last().pct_change() * 100 for k, v in curves.items()})
    for k, v in curves.items():   # first (partial) year is measured from the window start
        y0 = v.index[0].year
        yr.loc[pd.Timestamp(f"{y0}-12-31"), k] = (v[v.index.year == y0].iloc[-1] / v.iloc[0] - 1) * 100
    print(yr.round(1).to_string())

    print("\n" + "=" * 100)
    print("DIVERSIFICATION vs LIVE STRATEGY (monthly returns; blends rebalanced monthly)")
    print("=" * 100)
    live_eq = curves["Live strategy (Sharpe+Clenow)"]
    live_m = live_eq.resample("ME").last().pct_change().dropna()
    drows = []
    for name in variants:
        v_m_ = curves[name].resample("ME").last().pct_change().dropna()
        j = pd.concat([live_m, v_m_], axis=1, keys=["live", "alt"]).dropna()
        corr = j.corr().iloc[0, 1]
        for wl in (1.0, 0.8, 0.5):
            blend = wl * j["live"] + (1 - wl) * j["alt"]
            eqb = (1 + blend).cumprod()
            yrs = len(blend) / 12
            cagr = eqb.iloc[-1] ** (1 / yrs) - 1
            vol = blend.std() * np.sqrt(12)
            dd = (eqb / eqb.cummax() - 1).min()
            drows.append({"alt": name, "corr": corr, "live weight": wl, "CAGR": cagr, "Vol": vol,
                          "MaxDD(monthly)": dd, "CAGR/vol": cagr / vol})
    print(pd.DataFrame(drows).to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # what does the allocator actually hold? (MAA-sector-cap25 default)
    print("\nAVERAGE ALLOCATION BY SECTOR -- MAA-sector-cap25 (share of book, avg over rebalances)")
    w_by_sector = {}
    for e_idx, w in scheds["MAA-sector-cap25"].items():
        for j in np.where(w > 0)[0]:
            w_by_sector.setdefault(sector[cols[j]], []).append((e_idx, w[j]))
    n_reb = len(scheds["MAA-sector-cap25"])
    avg = {s: sum(x for _, x in v) / n_reb for s, v in w_by_sector.items()}
    for s_, a in sorted(avg.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {s_:<22} {a*100:5.1f}%")
    cash_share = np.mean([1 - w.sum() for w in scheds["MAA-sector-cap25"].values()])
    print(f"  {'CASH':<22} {cash_share*100:5.1f}%")

    csv = pd.DataFrame(curves)
    csv.to_csv(_BT_DIR / "keller_maa_equity.csv")
    fig, ax = plt.subplots(figsize=(11, 6))
    for k, v in curves.items():
        ax.plot(v.index, v.values, label=k, lw=2 if k.startswith("MAA-sector-cap") or k.startswith("Live") else 1.2)
    ax.set_yscale("log")
    ax.set_title("Keller MAA allocator vs live ETF strategy vs Nifty 500 (20 bps one-way costs for MAA)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(_BT_DIR / "keller_maa_equity_curve.png", dpi=110)
    print(f"\nChart -> {_BT_DIR / 'keller_maa_equity_curve.png'}")


if __name__ == "__main__":
    main()
