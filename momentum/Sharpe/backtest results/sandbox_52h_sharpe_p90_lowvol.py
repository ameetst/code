"""
SANDBOX -- "Quality Highs" momentum strategy. Does not touch live code.

Signals (all from daily closes, no volume needed):
  1. NEAR_52H : close / rolling 252d high           (1.0 = at a new high)
  2. SHARPE_1Y: annualised excess-return Sharpe, last 252d
  3. P90_FRAC : % of the last 252 days on which the close was ABOVE the
                90th percentile of the PREVIOUS 252 closes (threshold
                recomputed each day, ending the day before -> no look-ahead
                and not circular). Measures how persistently the stock has
                been breaking out, not just where it sits today.
  Filters    : 1Y Sharpe > NIFTY500 1Y Sharpe (default on); % below all-time high < ATH_DD (default 25%); optional 1Y vol cap VOL_MAX (default off)

Score  = mean of cross-sectional z-scores of the three signals, among stocks
         passing the vol filter.
Portfolio: top N by score, equal weight, weekly rebalance at the week-end
         close, hold-buffer (an existing holding stays while rank <= BUFFER),
         friction per side on turnover. Signal at close t, return t -> next
         rebalance close (no look-ahead).

Usage:  python sandbox_52h_sharpe_p90_lowvol.py [file.xlsx] [N] [VOL_MAX] [ATH_DD] [BENCH_SHARPE_FILTER 1|0]
Needs 504 trading days of history before the first signal.
NOTE: N750 file = current constituents only -> survivorship bias flatters
      absolute returns; compare variants, not the headline CAGR.
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
SHARPE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SHARPE_ROOT)
import momentum_lib as ml

FILE = (sys.argv[1] if len(sys.argv) > 1 else
        r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated_N750.xlsx")
N        = int(sys.argv[2]) if len(sys.argv) > 2 else 20
VOL_MAX  = float(sys.argv[3]) if len(sys.argv) > 3 else 9.0   # 9 = no vol cap
ATH_DD   = float(sys.argv[4]) if len(sys.argv) > 4 else 0.25  # max % below all-time high
BENCH_SHARPE_FILTER = (sys.argv[5] if len(sys.argv) > 5 else "1") == "1"  # stock 1Y Sharpe > NIFTY500 1Y Sharpe
BUFFER   = int(sys.argv[6]) if len(sys.argv) > 6 else 60      # exit a holding when rank > BUFFER
REGIME   = (sys.argv[7] if len(sys.argv) > 7 else "1") == "1"  # NIFTY500 < 100 EMA -> no new entries
NEAR52_DD = float(sys.argv[8]) if len(sys.argv) > 8 else 0.25  # must be within this % of 52W high
EMA_SPAN = 100
CASH_RATE = 0.06          # annual return on uninvested cash
MIN_TURNOVER = float(sys.argv[9]) if len(sys.argv) > 9 else 1e7   # Rs 1 crore median daily traded value (252d)
SCREEN_ONLY = os.environ.get("SCREEN_ONLY") == "1"
NSEALL_FILE = os.path.join(SHARPE_ROOT, "NSEAll_updated.xlsx")  # extends N750 history to the latest date


def _frame(path):
    """price / volume / benchmark frames (date x ticker), holiday columns dropped."""
    p, n, _, d = ml.load_prices(path)
    v = ml.load_volume(path)
    idx = pd.DatetimeIndex(d)
    px = p.T
    px.index = idx
    bench = pd.Series(n.values, index=idx)
    vol = v.T
    vol.index = pd.DatetimeIndex(v.columns)
    vol = vol.reindex(index=px.index, columns=px.columns)
    live = px.notna().sum(axis=1) > 0.5 * px.shape[1]
    return px[live], bench[live].ffill(), vol[live]


def load_data():
    """N750 history, extended to the latest date with NSEAll prices (restricted to
    N750 tickers). The NSEAll segment is rescaled per ticker at the join date so
    split-adjustment differences between the two files don't create a fake jump.
    Returns px, bench, daily traded value (price x volume, unscaled)."""
    px, bench, vol = _frame(FILE)
    tov = vol          # N750 'VOLUME' sheet is already traded VALUE in Rs (NSEAll's is share count)
    if os.path.exists(NSEALL_FILE):
        bpx, bbench, bvol = _frame(NSEALL_FILE)
        cut = px.index[-1]
        join = bpx.index[bpx.index <= cut][-1] if (bpx.index <= cut).any() else None
        if join is not None and bpx.index[-1] > cut:
            scale = (px.loc[join] / bpx.loc[join].reindex(px.columns)).where(lambda s: s.between(0.05, 20))
            new_idx = bpx.index[bpx.index > cut]
            add = bpx.loc[new_idx].reindex(columns=px.columns) * scale
            px = pd.concat([px, add])
            bench = pd.concat([bench, bbench.loc[new_idx] * (bench.loc[join] / bbench.loc[join])])
            tov = pd.concat([tov, (bpx * bvol).loc[new_idx].reindex(columns=px.columns)])
            print(f"Spliced NSEAll: {len(new_idx)} extra days ({new_idx[0].date()} -> {new_idx[-1].date()}), "
                  f"joined on {join.date()}")
    return px, bench, tov
WIN      = 252
RFR      = 0.07
FRICTION = 0.002          # per side
MIN_OBS  = 240            # min valid closes in the Sharpe/vol window


FILTER_SHARPE = os.environ.get("FILTER_SHARPE", "1y")   # "1y" or "6m": which Sharpe must beat NIFTY500's
SCORE_MODE = os.environ.get("SCORE_MODE", "z")  # "z" = avg of 3 z-scores; "clenow" = rank by 90d Clenow score


def rolling_clenow(px: pd.DataFrame, w: int = 90, ann: int = 250) -> pd.DataFrame:
    """Clenow momentum score per date: annualised slope of a 90d log-price
    regression, (exp(slope*250)-1)*100, times R^2. Same formula as
    Clenow Score/Clenow.py::clenow_score, vectorised over rolling windows.
    NaN if any close in the window is missing."""
    from numpy.lib.stride_tricks import sliding_window_view
    ly = np.log(px.where(px > 0)).values
    T, C = ly.shape
    x = np.arange(w) - (w - 1) / 2
    sxx = float((x ** 2).sum())
    out = np.full((T, C), np.nan)
    for j in range(C):
        win = sliding_window_view(ly[:, j], w)
        yc = win - win.mean(axis=1, keepdims=True)
        sxy = yc @ x
        syy = (yc ** 2).sum(axis=1)
        with np.errstate(all="ignore"):
            out[w - 1:, j] = (np.exp(sxy / sxx * ann) - 1) * 100 * (sxy ** 2 / (sxx * syy))
    return pd.DataFrame(out, index=px.index, columns=px.columns)


def build_signals(px: pd.DataFrame, bench: pd.Series, tov: pd.DataFrame):
    """px: DataFrame index=date, columns=ticker. Returns dict of date x ticker frames."""
    ret = px.pct_change(fill_method=None)
    ex = ret - RFR / WIN
    cnt = ret.rolling(WIN, min_periods=MIN_OBS).count()
    sd = ret.rolling(WIN, min_periods=MIN_OBS).std()
    sharpe = ex.rolling(WIN, min_periods=MIN_OBS).mean() / sd * np.sqrt(WIN)
    vol = sd * np.sqrt(WIN)
    # 6-month Sharpe (126d, annualised) -> used in the SCORE; the 1Y Sharpe above stays a filter only
    sd6 = ret.rolling(126, min_periods=120).std()
    sharpe6 = ex.rolling(126, min_periods=120).mean() / sd6 * np.sqrt(WIN)
    near52 = px / px.rolling(WIN, min_periods=MIN_OBS).max()
    q90 = px.rolling(WIN, min_periods=MIN_OBS).quantile(0.90).shift(1)
    above = (px > q90).astype(float).where(q90.notna())
    p90 = above.rolling(WIN, min_periods=MIN_OBS).mean()
    # ATH = running max over the whole file (history starts 2016), not true listing ATH
    ath = px / px.cummax()
    tov_med = tov.rolling(WIN, min_periods=120).median()   # median daily traded value (Rs)
    bret = bench.pct_change(fill_method=None)
    bsd = bret.rolling(WIN, min_periods=MIN_OBS).std()
    # benchmark Sharpe for the stock-vs-NIFTY500 filter; FILTER_SHARPE=6m uses 126d, default 1y uses 252d
    if FILTER_SHARPE == "6m":
        bsd6 = bret.rolling(126, min_periods=120).std()
        bsharpe = (bret - RFR / WIN).rolling(126, min_periods=120).mean() / bsd6 * np.sqrt(WIN)
    else:
        bsharpe = (bret - RFR / WIN).rolling(WIN, min_periods=MIN_OBS).mean() / bsd * np.sqrt(WIN)
    clenow = rolling_clenow(px) if SCORE_MODE == "clenow" else px * np.nan
    return dict(clenow=clenow, near52=near52, sharpe=sharpe, sharpe6=sharpe6, p90=p90, vol=vol, ath=ath, tov=tov_med), bsharpe


def z(s: pd.Series) -> pd.Series:
    sd = s.std()
    return (s - s.mean()) / sd if sd and np.isfinite(sd) else s * 0


def score_on(sig: dict, bsharpe: pd.Series, d, vol_max: float) -> pd.Series:
    df = pd.DataFrame({k: v.loc[d] for k, v in sig.items()})
    if SCORE_MODE != "clenow":
        df = df.drop(columns="clenow")
    df = df.dropna()
    df = df[(df["vol"] < vol_max) & (df["ath"] >= 1 - ATH_DD) & (df["near52"] >= 1 - NEAR52_DD) \
            & (df["tov"] >= MIN_TURNOVER)]
    if BENCH_SHARPE_FILTER:
        df = df[df["sharpe6" if FILTER_SHARPE == "6m" else "sharpe"] > bsharpe.loc[d]]
    if df.empty:
        return pd.Series(dtype=float)
    if SCORE_MODE == "clenow":
        return df["clenow"]           # rank by raw 90d Clenow score; all other signals are filters/info only
    if SCORE_MODE == "sharpe6":
        return df["sharpe6"]          # rank by raw 6-month Sharpe
    return (z(df["near52"]) + z(df["sharpe6"]) + z(df["p90"])) / 3   # 1Y Sharpe is a filter only


def main():
    px, bench, tov = load_data()
    sig, bsharpe = build_signals(px, bench, tov)
    ema = bench.ewm(span=EMA_SPAN, adjust=False).mean()
    bull = bench >= ema            # regime: True = new entries allowed

    def show_current():
        last = px.index[-1]
        cur = score_on(sig, bsharpe, last, VOL_MAX).sort_values(ascending=False).head(N)
        tbl = pd.DataFrame({k: sig[k].loc[last, cur.index] for k in ("near52", "sharpe6", "sharpe", "p90", "vol", "tov")})
        if SCORE_MODE == "clenow":
            tbl.insert(0, "clenow", sig["clenow"].loc[last, cur.index])
        tbl["tov"] = tbl["tov"] / 1e7           # Rs crore
        tbl = tbl.rename(columns={"tov": "med_tov_cr"})
        tbl.insert(0, "score", cur)
        print(f"\nNIFTY500 {FILTER_SHARPE.upper()} Sharpe {bsharpe.loc[last]:.2f}; close {bench.loc[last]:.0f} vs "
              f"{EMA_SPAN}EMA {ema.loc[last]:.0f} -> new entries {'ON' if bull.loc[last] else 'OFF'}")
        print(f"Current top {N} as of {last.date()}  (eligible: {len(score_on(sig, bsharpe, last, VOL_MAX))}):")
        print(tbl.round(3).to_string())

    if SCREEN_ONLY:
        show_current()
        return

    idx = px.index
    wk = pd.Series(idx, index=idx).groupby([idx.isocalendar().year.values,
                                            idx.isocalendar().week.values]).last()
    reb = [pd.Timestamp(d) for d in wk.values if idx.get_loc(pd.Timestamp(d)) >= 2 * WIN]
    print(f"{len(reb)} weekly rebalances  {reb[0].date()} -> {reb[-1].date()}  "
          f"N={N} buffer={BUFFER} vol<{VOL_MAX:.0%} ATH-dd<{ATH_DD:.0%} sharpe>bench={BENCH_SHARPE_FILTER} 52H-dd<{NEAR52_DD:.0%} regime={REGIME}")

    held, eq, beq, rows = {}, 1.0, 1.0, []
    for a, b in zip(reb[:-1], reb[1:]):
        sc = score_on(sig, bsharpe, a, VOL_MAX).sort_values(ascending=False)
        rank = {t: i + 1 for i, t in enumerate(sc.index)}
        keep = [t for t in held if rank.get(t, 10**9) <= BUFFER]
        allow_new = bool(bull.loc[a]) or not REGIME
        fill = [t for t in sc.index if t not in keep][: max(0, N - len(keep))] if allow_new else []
        new = (keep + fill)[:N]
        if not new:                       # nothing held -> all cash
            turn = sum(held.values())
            held, r = {}, 0.0
        else:
            w = {t: 1 / N for t in new}   # fixed 1/N slots; empty slots stay in cash (0% return)
            turn = sum(abs(w.get(t, 0) - held.get(t, 0)) for t in set(w) | set(held))
            pr = (px.loc[b, new] / px.loc[a, new] - 1).fillna(0.0)
            r = float((pr * pd.Series(w)).sum())
            held = w
        cash_w = 1 - len(new) / N                      # idle slots earn the cash rate
        r += cash_w * ((1 + CASH_RATE) ** ((b - a).days / 365) - 1)
        r -= turn * FRICTION
        br = bench.loc[b] / bench.loc[a] - 1
        eq *= 1 + r
        beq *= 1 + br
        rows.append((b, r, br, eq, beq, turn, len(new), allow_new))

    res = pd.DataFrame(rows, columns=["date", "ret", "bench", "equity", "bench_eq",
                                      "turnover", "n", "entries_on"]).set_index("date")
    yrs = (res.index[-1] - res.index[0]).days / 365.25
    def stats(e, r):
        cagr = e.iloc[-1] ** (1 / yrs) - 1
        mdd = (e / e.cummax() - 1).min()
        sh = (r.mean() - RFR / 52) / r.std() * np.sqrt(52)
        return cagr, mdd, sh
    for name, e, r in [("Strategy", res.equity, res.ret), ("NIFTY500", res.bench_eq, res.bench)]:
        c, m, s = stats(e, r)
        print(f"{name:9s} CAGR {c:6.1%}   MaxDD {m:6.1%}   Sharpe(wk) {s:4.2f}")
    print(f"Avg weekly turnover {res.turnover.mean():.1%}   avg names {res.n.mean():.1f}   "
          f"weeks with entries blocked {(~res.entries_on.astype(bool)).mean():.0%}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_52h_sharpe_p90_lowvol_equity.csv")
    res.to_csv(out)

    show_current()


if __name__ == "__main__":
    main()
