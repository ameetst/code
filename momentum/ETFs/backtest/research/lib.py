"""
Research harness: fast parameterised re-implementation of the live weekly hold-and-replace
engine (same rules as etf_backtest_rank_cutoff_fixed_vs_pct.run_strategy_rank_cutoff), driven by
the cached live feature tables. Backtest-only; touches no production file.
"""
import pickle
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
START_CAPITAL = 1_000_000.0
FIXED_COST = 20.0
MIN_HISTORY_DAYS = 260


@dataclass
class Params:
    n_slots: int = 5
    exit_dd: float | None = 0.25        # exit if > this far below 52wk high
    exit_rank: int | None = 40          # exit if investable rank worse than this
    tsl: float | None = 0.05            # exit on this drawdown from peak-since-entry
    sector_cap: int = 1
    regime: str = "ema50"               # key into Ctx.regime_risk_on
    riskoff_liquidate: bool = False     # sell everything when regime flips to risk-off
    cash_rate: float = 0.02             # annual, accrued /365 per trading day (matches earlier runs)
    slip: float = 0.0                   # one-way slippage+spread as a fraction of price
    every: int = 1                      # act every k-th weekly rebalance
    name: str = "baseline"


class Ctx:
    def __init__(self, cache_path=HERE / "cache.pkl"):
        with open(cache_path, "rb") as f:
            c = pickle.load(f)
        self.meta, self.prices, self.regime, self.feats = c["meta"], c["prices"], c["regime"], c["feats"]
        self.cfg = c["cfg"]
        self.dates = self.prices.index
        self.A = self.prices.values.astype(float)
        self.cols = {t: j for j, t in enumerate(self.prices.columns)}
        self.week_dates = sorted(self.feats)
        self.first_trade = self.week_dates[0]
        self.regime_risk_on = {}
        r = self.regime
        for n in (20, 30, 50, 75, 100, 150, 200, 250, 300):
            ema = r.ewm(span=n, adjust=False).mean()
            self.regime_risk_on[f"ema{n}"] = (r > ema)
        for n in (100, 150, 200, 250):
            self.regime_risk_on[f"sma{n}"] = (r > r.rolling(n).mean())
        # dual: price above 50EMA AND 50EMA above 100EMA
        e50, e100 = r.ewm(span=50, adjust=False).mean(), r.ewm(span=100, adjust=False).mean()
        self.regime_risk_on["ema50_and_trend"] = (r > e50) & (e50 > e100)
        self.regime_risk_on["always"] = pd.Series(True, index=r.index)

        def hyst(ma, band):
            st, out = True, []
            for px, m in zip(r.values, ma.values):
                if not (np.isnan(px) or np.isnan(m)):
                    if px > m * (1 + band): st = True
                    elif px < m * (1 - band): st = False
                out.append(st)
            return pd.Series(out, index=r.index)
        for b in (0.01, 0.02, 0.03):
            self.regime_risk_on[f"sma200_h{int(b*100)}"] = hyst(r.rolling(200).mean(), b)
            self.regime_risk_on[f"ema200_h{int(b*100)}"] = hyst(r.ewm(span=200, adjust=False).mean(), b)

    # -- cheap price-based features at as-of index i-1 (no look-ahead) --
    def ret(self, i, n):
        a = self.A
        with np.errstate(all="ignore"):
            return a[i - 1] / a[i - 1 - n] - 1

    def vol(self, i, n):
        a = self.A[i - 1 - n:i]
        with np.errstate(all="ignore"):
            lr = np.diff(np.log(a), axis=0)
        return np.nanstd(lr, axis=0, ddof=1) * np.sqrt(252)


def zs(x: pd.Series) -> pd.Series:
    mu, sd = x.mean(), x.std()
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=x.index)
    return (x - mu) / sd


def run(ctx: Ctx, p: Params, strat=None) -> dict:
    """strat(df, i, ctx) -> df with RANK_INVESTABLE & SCREEN_PASS; None = live ranking as cached."""
    A, dates = ctx.A, ctx.dates
    risk = ctx.regime_risk_on[p.regime]
    week_set = {d: k for k, d in enumerate(ctx.week_dates)}
    cash = START_CAPITAL
    slots = {}   # col -> dict
    eq, trades = [], []
    r_cash = p.cash_rate / 365.0
    invested_frac = []
    risk_on = True

    for i, d in enumerate(dates):
        cash *= (1 + r_cash)
        if d in week_set and week_set[d] % p.every == 0:
            prev = dates[i - 1]
            df = ctx.feats[d]
            if strat is not None:
                df = strat(df, i, ctx)
            rv = risk.get(prev, np.nan)
            risk_on = True if pd.isna(rv) else bool(rv)

            px_now = A[i]
            tot = cash + sum(s["sh"] * px_now[c] for c, s in slots.items() if not np.isnan(px_now[c]))
            slot_val = tot / p.n_slots
            ix = df.set_index("TICKER")
            held_secs = {}
            for c in list(slots):
                s = slots[c]
                t = ctx.prices.columns[c]
                pdec = A[i - 1, c] if not np.isnan(A[i - 1, c]) else s["entry"]
                s["peak"] = max(s["peak"], pdec)
                reasons = []
                if t not in ix.index:
                    reasons.append("not in universe")
                else:
                    row = ix.loc[t]
                    pfh = row["PCT_FROM_HIGH"]
                    if p.exit_dd is not None and pd.notna(pfh) and abs(pfh) > p.exit_dd * 100:
                        reasons.append("52wk-DD")
                    rk = row["RANK_INVESTABLE"]
                    if p.exit_rank is not None and pd.notna(rk) and rk > p.exit_rank:
                        reasons.append("rank")
                if p.tsl is not None and s["peak"] > 0 and pdec > 0 and (s["peak"] - pdec) / s["peak"] >= p.tsl:
                    reasons.append("TSL")
                if p.riskoff_liquidate and not risk_on:
                    reasons.append("risk-off")
                if reasons:
                    pe = px_now[c] if not np.isnan(px_now[c]) else pdec
                    proceeds = s["sh"] * pe * (1 - p.slip) - FIXED_COST
                    cash += proceeds
                    trades.append({"TYPE": "SELL", "TICKER": t, "ENTRY": s["date"], "EXIT": d,
                                   "REASON": "|".join(reasons), "PNL": proceeds - s["sh"] * s["entry"]})
                    del slots[c]
                else:
                    held_secs[s["sector"]] = held_secs.get(s["sector"], 0) + 1

            if risk_on:
                open_n = p.n_slots - len(slots)
                if open_n > 0:
                    cand = df[df["SCREEN_PASS"] & (df["RANK_INVESTABLE"] > 0)].sort_values("RANK_INVESTABLE")
                    filled = 0
                    for _, row in cand.iterrows():
                        if filled >= open_n:
                            break
                        t = row["TICKER"]
                        c = ctx.cols.get(t)
                        if c is None or c in slots:
                            continue
                        sec = row.get("SECTOR", "OTHER")
                        if held_secs.get(sec, 0) >= p.sector_cap:
                            continue
                        pe = A[i, c]
                        if np.isnan(pe) or pe <= 0:
                            continue
                        sv = slot_val
                        sh = (sv - FIXED_COST) / (pe * (1 + p.slip))
                        cash -= sv
                        slots[c] = {"sh": sh, "entry": pe, "date": d, "peak": pe, "sector": sec}
                        trades.append({"TYPE": "BUY", "TICKER": t, "ENTRY": d, "EXIT": None,
                                       "REASON": f"rank {int(row['RANK_INVESTABLE'])}", "PNL": np.nan})
                        held_secs[sec] = held_secs.get(sec, 0) + 1
                        filled += 1
        pv = sum(s["sh"] * A[i, c] for c, s in slots.items() if not np.isnan(A[i, c]))
        eq.append(cash + pv)
        invested_frac.append(pv / (cash + pv) if cash + pv else 0)
    eq = pd.Series(eq, index=dates, name=p.name)
    return {"equity": eq, "trades": pd.DataFrame(trades), "invested": pd.Series(invested_frac, index=dates)}


# ------------------------------------------------------------------ metrics
def metrics(eq: pd.Series, start=None, end=None) -> dict:
    e = eq.loc[start:end] if (start or end) else eq
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = e / e.cummax() - 1
    r = e.pct_change().dropna()
    vol = r.std() * np.sqrt(252)
    return {"CAGR": cagr, "Vol": vol, "MaxDD": dd.min(), "Sharpe": cagr / vol if vol else np.nan,
            "Calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
            "Final": e.iloc[-1] / e.iloc[0]}


def buy_hold(ctx: Ctx, start):
    r = ctx.regime.loc[start:]
    return r / r.iloc[0] * START_CAPITAL


def block_bootstrap_diff(eq_a: pd.Series, eq_b: pd.Series, n=2000, block=8, seed=7):
    """Paired stationary-style block bootstrap on weekly returns: distribution of
    (Sharpe_a - Sharpe_b) and (CAGR_a - CAGR_b). a = candidate, b = baseline."""
    wa = eq_a.resample("W-FRI").last().pct_change().dropna()
    wb = eq_b.resample("W-FRI").last().pct_change().dropna()
    idx = wa.index.intersection(wb.index)
    ra, rb = wa.loc[idx].values, wb.loc[idx].values
    T = len(idx)
    rng = np.random.default_rng(seed)
    out_s, out_c = [], []
    nb = int(np.ceil(T / block))
    for _ in range(n):
        starts = rng.integers(0, T, nb)
        sel = np.concatenate([(np.arange(block) + s) % T for s in starts])[:T]
        a, b = ra[sel], rb[sel]
        out_s.append((a.mean() / a.std() - b.mean() / b.std()) * np.sqrt(52))
        out_c.append((np.prod(1 + a) ** (52 / T) - 1) - (np.prod(1 + b) ** (52 / T) - 1))
    out_s, out_c = np.array(out_s), np.array(out_c)
    return {"dSharpe_mean": out_s.mean(), "dSharpe_lo": np.percentile(out_s, 2.5), "dSharpe_hi": np.percentile(out_s, 97.5),
            "P(dSharpe>0)": (out_s > 0).mean(),
            "dCAGR_mean": out_c.mean(), "dCAGR_lo": np.percentile(out_c, 2.5), "dCAGR_hi": np.percentile(out_c, 97.5),
            "P(dCAGR>0)": (out_c > 0).mean()}
