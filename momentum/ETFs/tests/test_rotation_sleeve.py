"""Tests for etf_rotation_sleeve (paper-only monthly dual-momentum rotation).

Run:  python -m pytest ETFs/tests/test_rotation_sleeve.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ETFS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ETFS))
sys.path.insert(0, str(ETFS / "backtest"))

import etf_rotation_sleeve as rot  # noqa: E402

UNI = rot.DEFAULTS["UNIVERSE"]


def make_prices(rets_12m: dict, start="2025-01-01", n=330, glitch=None, drop=None, short=None):
    """Business-day prices; each ticker rises/falls smoothly so its trailing-252-row return equals
    rets_12m[ticker] exactly (constant daily growth)."""
    idx = pd.bdate_range(start, periods=n)
    cols = {}
    for t in UNI:
        if drop and t == drop:
            continue
        g = (1.0 + rets_12m.get(t, 0.0)) ** (1.0 / 252.0)
        s = 100.0 * g ** np.arange(n)
        cols[t] = pd.Series(s, index=idx)
    df = pd.DataFrame(cols)
    if glitch:
        df.loc[df.index[-100], glitch] *= 10.0
    if short:
        df.loc[df.index[: n - 100], short] = np.nan
    return df


CFG = dict(rot.DEFAULTS)
RETS = {"NIFTYBEES": 0.10, "JUNIORBEES": 0.30, "BANKBEES": -0.05, "MOM100": 0.20,
        "CPSEETF": 0.02, "HNGSNGBEES": 0.40, "GOLDBEES": 0.25}


def test_month_end_signal_date_is_last_row_of_previous_month():
    idx = pd.bdate_range("2026-08-24", "2026-10-07")
    assert rot.month_end_signal_date(idx) == pd.Timestamp("2026-09-30")
    assert rot.month_end_signal_date(pd.bdate_range("2026-10-01", "2026-10-20")) is None


def test_signal_picks_top3_that_beat_cash():
    px = make_prices(RETS)
    sd = px.index[-30]
    sig = rot.compute_signal(px, sd, CFG)
    # recompute expected 12M returns at sd
    exp = {t: px[t].loc[:sd].iloc[-1] / px[t].loc[:sd].iloc[-253] - 1 for t in UNI}
    order = sorted(exp, key=lambda t: -exp[t])
    assert [r["ticker"] for r in sig["rows"] if r["rank"]][:3] == order[:3]
    assert set(sig["targets"]) == {t for t in order[:3] if exp[t] > sig["hurdle"]}
    assert all(abs(w - 1 / 3) < 1e-12 for w in sig["targets"].values())
    assert abs(sig["cash_weight"] - (1 - len(sig["targets"]) / 3)) < 1e-12


def test_pick_below_cash_hurdle_goes_to_cash():
    rets = {t: -0.10 for t in UNI}
    rets["GOLDBEES"] = 0.20                      # only one ETF beats the 5.5% bar
    sig = rot.compute_signal(make_prices(rets), make_prices(rets).index[-30], CFG)
    assert list(sig["targets"]) == ["GOLDBEES"]
    assert abs(sig["cash_weight"] - 2 / 3) < 1e-12


def test_signal_ignores_data_after_signal_date():
    px = make_prices(RETS)
    sd = px.index[-30]
    a = rot.compute_signal(px, sd, CFG)
    px2 = px.copy()
    px2.loc[px2.index > sd] *= 0.5                # crash after the signal date must not matter
    b = rot.compute_signal(px2, sd, CFG)
    assert a["targets"] == b["targets"]


@pytest.mark.parametrize("kw,expect", [({"glitch": "MOM100"}, "GLITCH"), ({"drop": "MOM100"}, "NOT IN DATA"),
                                       ({"short": "MOM100"}, "SHORT HISTORY")])
def test_bad_data_is_flagged_not_ranked(kw, expect):
    px = make_prices(RETS, **kw)
    sig = rot.compute_signal(px, px.index[-5], CFG)
    row = next(r for r in sig["rows"] if r["ticker"] == "MOM100")
    assert expect in row["status"] and row["rank"] is None and "MOM100" not in sig["targets"]
    assert sig["issues"]


def test_first_update_buys_targets_and_charges_costs(tmp_path):
    px = make_prices(RETS)
    res = rot.run_update(tmp_path, px, CFG, force=True)
    assert res["status"] == "ok" and res["rebalanced"] and res["first_run"]
    log = rot.load_log(tmp_path)
    st = log["state"]
    sig = rot.current_signal(px, CFG)
    assert set(st["positions"]) == set(sig["targets"])
    eq = res["equity"]
    assert eq < CFG["START_CAPITAL"]                     # slippage + fees were charged
    assert eq > CFG["START_CAPITAL"] * 0.99               # ...but only a little
    invested = sum(p["shares"] * px[t].iloc[-1] for t, p in st["positions"].items())
    assert abs(invested / eq - sum(sig["targets"].values())) < 0.01


def test_update_is_idempotent_within_a_month(tmp_path):
    px = make_prices(RETS)
    rot.run_update(tmp_path, px, CFG, force=True)
    before = rot.load_log(tmp_path)
    res = rot.run_update(tmp_path, px, CFG, force=True)
    after = rot.load_log(tmp_path)
    assert res["rebalanced"] is False and res["n_trades"] == 0
    assert len(after["state"]["trades"]) == len(before["state"]["trades"])
    assert len(after["rebalances"]) == 1
    assert len(after["state"]["equity_history"]) == 1


def test_new_month_rotates_out_of_a_loser(tmp_path):
    px = make_prices(RETS, n=300)
    rot.run_update(tmp_path, px, CFG, force=True)
    held_before = set(rot.load_log(tmp_path)["state"]["positions"])
    # extend into the next month with HNGSNGBEES (a winner) collapsing, so it drops out of the top 3
    more = pd.bdate_range(px.index[-1] + pd.Timedelta(days=1), periods=45)
    ext = pd.DataFrame({t: px[t].iloc[-1] * (1.0 + 0.0003) ** np.arange(1, 46) for t in px.columns}, index=more)
    ext["HNGSNGBEES"] = px["HNGSNGBEES"].iloc[-1] * np.linspace(0.9, 0.35, 45)
    px2 = pd.concat([px, ext])
    res = rot.run_update(tmp_path, px2, CFG, force=True)
    assert res["rebalanced"]
    st = rot.load_log(tmp_path)["state"]
    assert "HNGSNGBEES" in held_before and "HNGSNGBEES" not in st["positions"]
    sells = [t for t in st["trades"] if t["type"] == "SELL" and t["ticker"] == "HNGSNGBEES"]
    assert sells and sells[0]["reason"].startswith("monthly rotation")


def test_stale_data_is_skipped_unless_forced(tmp_path):
    px = make_prices(RETS, start="2020-01-01")          # years old
    res = rot.run_update(tmp_path, px, CFG, force=False)
    assert res["status"] == "skipped" and not (tmp_path / rot.LOG_FILE).exists()
    assert rot.run_update(tmp_path, px, CFG, force=True)["status"] == "ok"


def test_preview_is_read_only_and_matches_the_update(tmp_path):
    px = make_prices(RETS)
    pv = rot.preview(tmp_path, px, CFG)
    assert pv["due"] and not (tmp_path / rot.LOG_FILE).exists() and pv["pending"]
    rot.run_update(tmp_path, px, CFG, force=True)
    trades_done = [t["ticker"] for t in rot.load_log(tmp_path)["state"]["trades"]]
    assert sorted(t["ticker"] for t in pv["pending"]) == sorted(trades_done)
    assert rot.preview(tmp_path, px, CFG)["due"] is False


def test_reset_archives_instead_of_deleting(tmp_path):
    px = make_prices(RETS)
    rot.run_update(tmp_path, px, CFG, force=True)
    bak = rot.reset_log(tmp_path)
    assert bak is not None and bak.exists() and not (tmp_path / rot.LOG_FILE).exists()


# ------------------------------------------------------------------ cross-check with the backtest
HIST = Path(r"C:\Users\ameet\Documents\Github\dhan_datahq\base files\History_ETFs.xlsx")


@pytest.mark.skipif(not HIST.exists(), reason="History_ETFs.xlsx not available")
def test_signal_matches_backtest_gem_schedule_on_real_history():
    """The paper rules must be the ones that were backtested: every month-end since 2017 gives the
    same holdings and weights as etf_backtest_dual_momentum.gem_schedule (top3, 12M, abs, 5.5% cash)."""
    import etf_backtest_dual_momentum as dm
    meta, raw = dm.bt.load_history_etfs(dm.bt.HISTORY_FILE)
    prices, _ = dm.detect_and_clean(raw)
    sched = dm.gem_schedule(prices, UNI, [252], 3, True, 0.055)
    assert len(sched) > 90
    mism = []
    for e_idx, w in sched.items():
        sd = prices.index[e_idx - 1]
        sig = rot.compute_signal(prices, sd, CFG)
        want = {UNI[j]: w[j] for j in np.where(w > 0)[0]}
        if set(sig["targets"]) != set(want) or any(abs(sig["targets"][t] - want[t]) > 1e-12 for t in want):
            mism.append((sd.date(), want, sig["targets"]))
    assert not mism, f"{len(mism)} of {len(sched)} month-ends differ, e.g. {mism[:2]}"
