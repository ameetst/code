"""
turning_points_lib.py
======================
PROTOTYPE — standalone, does NOT touch live code (momentum_lib.py / Sharpe.py /
backtest_wired.py unchanged). Lives only in prototype_turning_points/.

Implements the market-cycle classifier from:
  Goulding, Harvey, Mazzoleni — "Momentum Turning Points"
  https://ssrn.com/abstract=3489539

SLOW signal  = sign of the trailing 12-month return, defined as the ARITHMETIC
               MEAN of the 12 trailing monthly returns (NOT a compounded/
               cumulative 12-month return — the paper is explicit about this;
               getting it wrong silently shifts the state boundaries).
FAST signal  = sign of the single most recent month's return.

State (paper's Figure 4):
    SLOW+ / FAST+  -> Bull        (agreement, uptrend)
    SLOW- / FAST-  -> Bear        (agreement, downtrend)
    SLOW+ / FAST-  -> Correction  (disagreement, was up — turning down)
    SLOW- / FAST+  -> Rebound     (disagreement, was down — turning up)

risk_on grouping used by the prototype backtest (user-selected): Bull and
Rebound are risk-on (both states the paper documents as having positive
expected forward returns — Rebound specifically "average returns and
skewness similar to Bull phases, but with higher volatility"); Bear and
Correction are risk-off (both documented as predicting negative/deteriorating
forward returns).
"""

import datetime
import numpy as np
import pandas as pd

STATES = ("Bull", "Bear", "Correction", "Rebound")
RISK_ON_STATES = ("Bull", "Rebound")


def build_monthly_series(nifty_series: pd.Series, dates: list) -> pd.Series:
    """
    Collapse a daily NIFTY500 series to month-end levels, point-in-time correct:
    each month's value is the last trading day ON OR BEFORE that calendar
    month-end (never a future date within the month).

    Parameters
    ----------
    nifty_series : pd.Series indexed by date (as returned by ml.load_prices)
    dates        : list[datetime.date], ascending, matching nifty_series.index

    Returns
    -------
    pd.Series indexed by pandas Timestamp (month-end), NIFTY500 level.
    """
    s = nifty_series.copy()
    s.index = pd.DatetimeIndex(pd.to_datetime(list(s.index)))
    s = s.sort_index()
    monthly = s.groupby(pd.Grouper(freq="ME")).last().dropna()
    return monthly


def compute_slow_fast(monthly: pd.Series) -> pd.DataFrame:
    """
    Given a month-end price series, compute monthly returns, then:
      r_fast(t) = monthly_return(t)
      r_slow(t) = arithmetic mean of monthly_return(t-11 .. t)   (12 months incl. t)

    Returns
    -------
    pd.DataFrame indexed like `monthly` (minus the first month, which has no
    return) with columns ['ret', 'r_fast', 'r_slow'], r_slow is NaN until 12
    monthly returns are available.
    """
    ret = monthly.pct_change().dropna()
    r_fast = ret.copy()
    r_slow = ret.rolling(window=12, min_periods=12).mean()
    df = pd.DataFrame({"ret": ret, "r_fast": r_fast, "r_slow": r_slow})
    return df


def classify_state(r_slow: float, r_fast: float) -> str:
    """Map (r_slow, r_fast) signs to one of the four paper states. NaN-safe."""
    if pd.isna(r_slow) or pd.isna(r_fast):
        return np.nan
    slow_up = r_slow >= 0
    fast_up = r_fast >= 0
    if slow_up and fast_up:
        return "Bull"
    if (not slow_up) and (not fast_up):
        return "Bear"
    if slow_up and (not fast_up):
        return "Correction"
    return "Rebound"


def build_state_series(nifty_series: pd.Series, dates: list) -> pd.DataFrame:
    """
    Full pipeline: daily NIFTY series -> monthly -> SLOW/FAST -> state.

    Returns
    -------
    pd.DataFrame indexed by month-end Timestamp, columns:
      ['level', 'ret', 'r_fast', 'r_slow', 'state', 'risk_on']
    Rows before the 12th monthly return have state = NaN / risk_on = NaN.
    """
    monthly = build_monthly_series(nifty_series, dates)
    sf = compute_slow_fast(monthly)
    out = sf.copy()
    out["level"] = monthly.reindex(out.index)
    out["state"] = [classify_state(rs, rf) for rs, rf in zip(out["r_slow"], out["r_fast"])]
    out["risk_on"] = out["state"].map(
        lambda s: (s in RISK_ON_STATES) if isinstance(s, str) else np.nan
    )
    return out[["level", "ret", "r_fast", "r_slow", "state", "risk_on"]]


def is_risk_on(state, risk_on_states=RISK_ON_STATES) -> bool:
    """
    True if `state` is in `risk_on_states`, False otherwise (incl. unknown/NaN).
    Defaults to the original Bull/Rebound grouping — pass an override (e.g.
    `risk_on_states=("Bull", "Bear", "Rebound")`) to test a different split
    without touching the default used by backtest_turning_points.py.
    """
    if not isinstance(state, str):
        return False
    return state in risk_on_states


def classify_state_asof(nifty_series: pd.Series, dates_asof: list):
    """
    Point-in-time classification: given a NIFTY series already sliced to data
    available as of the current backtest date (no lookahead — caller is
    responsible for the slice), return the CURRENT month's state using only
    data available up to that point.

    Used by backtest_turning_points.py inside the weekly rebalance loop, where
    `nifty_series`/`dates_asof` are already `.iloc[:idx+1]` sliced.

    Returns
    -------
    str state name, or "Unknown" if fewer than 13 monthly observations exist
    yet (12 trailing months + the return that defines them).
    """
    state_df = build_state_series(nifty_series, dates_asof)
    valid = state_df.dropna(subset=["state"])
    if valid.empty:
        return "Unknown"
    return valid["state"].iloc[-1]


# ── LEVEL C: dynamic Sharpe-optimal exposure dial (paper Proposition 9) ────────
#
# Bull and Bear are unambiguous (SLOW and FAST agree), so they stay simple:
# Bull -> full exposure, Bear -> the floor below. Only Correction and Rebound
# (the "disagreement" turning-point states) get a data-driven dial between 0
# and 1, estimated ONLY from state/forward-return pairs that are FULLY
# resolved strictly before the current point (no lookahead).
BEAR_EXPOSURE_FLOOR = 0.15   # long-only can't short Bear; partial floor, not 0 —
                             # flagged as a judgment call, easy to override
MIN_OBS_FOR_DIAL     = 5     # below this many prior occurrences of a state,
                             # fall back to neutral (0.5) outright — too thin
N_FULL_CONFIDENCE    = 12    # occurrences at which the shrinkage weight reaches 1.0
                             # (i.e. the raw Prop-9 estimate is trusted in full)


def _state_moments(hist: pd.DataFrame, state_name: str):
    """mean forward return, mean squared forward return, and count for one state."""
    sub = hist.loc[hist["state"] == state_name, "fwd_ret"]
    n = len(sub)
    if n == 0:
        return 0.0, 0.0, 0
    return float(sub.mean()), float((sub ** 2).mean()), n


def dynamic_blend_a_asof(nifty_series: pd.Series, dates_asof: list) -> dict:
    """
    Estimate today's Correction/Rebound exposure dial (paper Proposition 9),
    using only monthly (state, next-month-return) pairs that are already
    fully realized as of `dates_asof` — the last month in the series never
    has a resolved forward return, so it's automatically excluded, which is
    what makes this safe to call point-in-time inside the backtest loop.

    Returns a dict:
      {"a_correction": float, "a_rebound": float, "diagnostics": {...}}
    `a_correction`/`a_rebound` are exposure multipliers in [0, 1] — NOT the
    paper's raw "a" (which is a SLOW/FAST blend weight for a long/short
    strategy); here they're already reinterpreted directly as how much of
    the normal position size to hold in that state.
    """
    state_df = build_state_series(nifty_series, dates_asof)
    state_df["fwd_ret"] = state_df["ret"].shift(-1)
    hist = state_df.dropna(subset=["state", "fwd_ret"])  # strictly-resolved history only

    if hist.empty:
        return {"a_correction": 0.5, "a_rebound": 0.5, "diagnostics": {"note": "no history yet"}}

    e_bull, _, n_bull = _state_moments(hist, "Bull")
    e_bear, _, n_bear = _state_moments(hist, "Bear")
    p_bull = (hist["state"] == "Bull").mean()
    p_bear = (hist["state"] == "Bear").mean()

    # K > 0 means "the trend signal is working" (Bull months pull their weight
    # more than Bear months drag) — the same quantity the paper calls
    # E[r|Bull]*P[Bull] - E[r|Bear]*P[Bear].
    K = e_bull * p_bull - e_bear * p_bear

    def solve(state_name: str, sign: int):
        e_r, e_r2, n = _state_moments(hist, state_name)
        if n < MIN_OBS_FOR_DIAL or e_r2 <= 1e-8:
            a_raw = 0.5
        else:
            a_raw = 0.5 * (1.0 + sign * K * e_r / e_r2)
            a_raw = min(1.0, max(0.0, a_raw))
        w = min(1.0, n / N_FULL_CONFIDENCE)     # shrinkage weight: trust grows with sample size
        a_final = w * a_raw + (1.0 - w) * 0.5
        return a_final, {"n": n, "mean_fwd_ret": round(e_r, 5),
                          "mean_sq_fwd_ret": round(e_r2, 5),
                          "a_raw": round(a_raw, 3), "shrink_w": round(w, 3)}

    a_co, diag_co = solve("Correction", sign=-1)
    a_re, diag_re = solve("Rebound", sign=+1)

    return {
        "a_correction": a_co,
        "a_rebound": a_re,
        "diagnostics": {
            "K": round(K, 5), "n_bull": n_bull, "n_bear": n_bear,
            "Correction": diag_co, "Rebound": diag_re,
        },
    }


def state_exposure_dynamic(state: str, dial: dict) -> float:
    """
    Map a state + this-point-in-time dial (from dynamic_blend_a_asof) to a
    position-size multiplier in [0, 1].
    """
    if state == "Bull":
        return 1.0
    if state == "Bear":
        return BEAR_EXPOSURE_FLOOR
    if state == "Correction":
        return dial["a_correction"]
    if state == "Rebound":
        return dial["a_rebound"]
    return BEAR_EXPOSURE_FLOOR  # "Unknown" -> defensive default


if __name__ == "__main__":
    # Self-check: load the same data file the backtest will use and print the
    # state history, so known events can be eyeballed before trusting this
    # module inside the backtest loop.
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import momentum_lib as ml

    FILE = r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_updated.xlsx"
    print(f"Loading {FILE} ...")
    prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

    state_df = build_state_series(nifty_series, dates)

    pd.set_option("display.max_rows", 200)
    pd.set_option("display.width", 140)
    print("\nFull monthly state history:")
    print(state_df.round(4))

    print("\nState frequency:")
    print(state_df["state"].value_counts())

    print("\n--- Known-event sanity checks ---")
    checks = {
        "2020-03": "COVID crash month — expect Bear or Correction",
        "2020-04": "COVID rebound start — expect Rebound or Bear",
        "2021-01": "Post-COVID uptrend — expect Bull",
        "2022-06": "2022 correction — expect Correction or Bear",
    }
    for ym, note in checks.items():
        matches = state_df.loc[state_df.index.strftime("%Y-%m") == ym]
        if not matches.empty:
            row = matches.iloc[0]
            print(f"  {ym}: state={row['state']!s:<12} risk_on={row['risk_on']!s:<6} | {note}")
        else:
            print(f"  {ym}: no data | {note}")
