"""
ETF Rotation Sleeve -- dual momentum on a few distinct asset-class ETFs (PAPER TRADING ONLY)
=====================================================================================
A small, separate, monthly strategy that sits next to (and never touches) the live weekly ETF
strategy in etf_momentum_ranking.py. It exists to be paper-traded, not to place orders.

Rules (frozen -- these are the ones that were backtested in backtest/etf_backtest_dual_momentum.py,
variant "top3, 12M, abs" on the 7 ETFs that were already listed in 2016):

  Universe   NIFTYBEES, JUNIORBEES, BANKBEES, MOM100, CPSEETF, HNGSNGBEES, GOLDBEES
  Signal     the last trading day of the most recent completed month
  Relative   rank the universe by 12-month (252 trading-day) price return; take the top 3
  Absolute   a pick is only held if its 12-month return beats cash (ASSUMED 5.5% a year);
             a pick that fails leaves its slot in cash
  Weights    equal, 1/3 per slot (so 0, 1/3, 2/3 or 100% invested)
  Rebalance  once per month, trading to target; fills at the latest close with slippage + fee
  Cash       earns the assumed 5.5% a year (a liquid-fund proxy -- there is no usable cash
             history in the data, so this is an assumption, not a measurement)

State lives in rotation_paper_log.json (own file). This module never reads or writes the
tradelog, the positions ledger, holdings_log.json or paper_trade_log.json.

CLI:
  python etf_rotation_sleeve.py              show the current signal and pending actions
  python etf_rotation_sleeve.py --update     apply this month's rebalance to the paper portfolio
  python etf_rotation_sleeve.py --update --force   ... even if price data is stale
  python etf_rotation_sleeve.py --reset      archive the paper history and restart from cash
"""

from __future__ import annotations

import copy
import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
LOG_FILE = "rotation_paper_log.json"
CONFIG_FILE = "rotation_sleeve_config.json"

DEFAULTS = {
    "UNIVERSE": ["NIFTYBEES", "JUNIORBEES", "BANKBEES", "MOM100", "CPSEETF", "HNGSNGBEES", "GOLDBEES"],
    "LOOKBACK_DAYS": 252,          # 12 months of trading days
    "TOP_N": 3,
    "CASH_PA": 0.055,              # ASSUMED liquid-fund yield; also the absolute-momentum hurdle
    "SLIPPAGE_BPS": 20.0,          # one-way, on every fill
    "FEE_PER_LEG": 20.0,           # Rs per trade leg (same convention as the backtests)
    "START_CAPITAL": 1_000_000.0,
    "MAX_DATA_AGE_DAYS": 7,        # refuse to trade on price data older than this
    "LATE_WARN_DAYS": 7,           # flag a rebalance that runs this long after the signal date
    "MIN_TRADE_FRAC": 0.005,       # ignore drift trims/adds smaller than this share of equity
    "BAD_PRINT_LOGRET": 1.2,       # |1-day log return| above this in the window = data glitch
    "AUTO_UPDATE": True,           # the dashboard updates the sleeve when Run Weekly Rebalance runs
}


# --------------------------------------------------------------------------- config / files
def load_config(root: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = (root or ROOT) / CONFIG_FILE
    if p.exists():
        try:
            with open(p) as f:
                over = json.load(f)
            cfg.update({k: v for k, v in over.items() if k in DEFAULTS})
        except Exception:
            pass
    return cfg


def save_config(root: Path | None, values: dict) -> dict:
    """Merge `values` into rotation_sleeve_config.json (only known keys)."""
    root = root or ROOT
    p = root / CONFIG_FILE
    cur = {}
    if p.exists():
        try:
            with open(p) as f:
                cur = json.load(f)
        except Exception:
            cur = {}
    cur.update({k: v for k, v in values.items() if k in DEFAULTS})
    _atomic_write(p, cur)
    return load_config(root)


def _json_safe(o):
    if isinstance(o, (np.floating,)):
        return None if math.isnan(float(o)) else float(o)
    if isinstance(o, float):
        return None if math.isnan(o) else o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.strftime("%Y-%m-%d")
    raise TypeError(f"not JSON serialisable: {type(o)}")


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=_json_safe)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".bak"))
    tmp.replace(path)


def load_log(root: Path | None = None) -> dict:
    p = (root or ROOT) / LOG_FILE
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def reset_log(root: Path | None = None) -> Path | None:
    """Archive the paper history (never deletes it) and let the next update start from cash."""
    p = (root or ROOT) / LOG_FILE
    if not p.exists():
        return None
    dest = p.with_name(f"rotation_paper_log.reset_{datetime.now():%Y%m%d_%H%M%S}.json")
    p.replace(dest)
    return dest


# --------------------------------------------------------------------------- signal
def month_end_signal_date(index: pd.DatetimeIndex):
    """Last trading row of the most recent COMPLETED month in the data: the last row whose
    month differs from the month of the final row. None if the data spans a single month."""
    if len(index) == 0:
        return None
    last = index[-1]
    prev = index[(index.month != last.month) | (index.year != last.year)]
    return prev[-1] if len(prev) else None


def hurdle(cfg: dict) -> float:
    """Cash return over the lookback window -- the absolute-momentum bar."""
    return (1.0 + cfg["CASH_PA"]) ** (cfg["LOOKBACK_DAYS"] / 252.0) - 1.0


def compute_signal(prices: pd.DataFrame, signal_date, cfg: dict) -> dict:
    """Score the universe using ONLY data up to and including `signal_date`.

    Returns {"signal_date", "rows", "targets", "cash_weight", "hurdle", "issues"}.
    rows: one per universe ETF, ranked: ticker, status, ret (12M), rank, abs_ok, selected, weight.
    status is OK or the reason the ETF cannot be ranked (it is then treated as unavailable --
    never silently dropped).
    """
    L, N = int(cfg["LOOKBACK_DAYS"]), int(cfg["TOP_N"])
    hist = prices.loc[:signal_date]
    bar = hurdle(cfg)
    rows, issues = [], []
    for t in cfg["UNIVERSE"]:
        row = dict(ticker=t, status="OK", ret=None, rank=None, abs_ok=False, selected=False, weight=0.0)
        if t not in hist.columns:
            row["status"] = "NOT IN DATA"
        else:
            win = hist[t].iloc[-(L + 1):]
            if len(win) < L + 1:
                row["status"] = f"SHORT HISTORY ({len(win)} of {L + 1} rows)"
            elif win.isna().any():
                first_ok = win.first_valid_index()
                leading_only = first_ok is not None and not win.loc[first_ok:].isna().any()
                row["status"] = (f"SHORT HISTORY ({int(win.notna().sum())} of {L + 1} rows)"
                                 if leading_only else "GAP IN WINDOW")
            elif (win <= 0).any():
                row["status"] = "BAD PRICE"
            elif np.log(win).diff().abs().max() > cfg["BAD_PRINT_LOGRET"]:
                row["status"] = "DATA GLITCH (extreme 1-day move)"
            else:
                row["ret"] = float(win.iloc[-1] / win.iloc[0] - 1.0)
        if row["status"] != "OK":
            issues.append(f"{t}: {row['status']}")
        rows.append(row)

    ok = sorted([r for r in rows if r["status"] == "OK"], key=lambda r: -r["ret"])
    for i, r in enumerate(ok, start=1):
        r["rank"] = i
        r["abs_ok"] = bool(r["ret"] > bar)
    targets = {}
    for r in ok[:N]:
        r["selected"] = True
        if r["abs_ok"]:
            r["weight"] = 1.0 / N
            targets[r["ticker"]] = r["weight"]
    order = {r["ticker"]: i for i, r in enumerate(ok)}
    rows.sort(key=lambda r: order.get(r["ticker"], 10_000 + cfg["UNIVERSE"].index(r["ticker"])))
    return dict(signal_date=pd.Timestamp(signal_date).strftime("%Y-%m-%d"), rows=rows, targets=targets,
                cash_weight=1.0 - sum(targets.values()), hurdle=bar, issues=issues)


# --------------------------------------------------------------------------- paper portfolio
def new_state(cfg: dict) -> dict:
    return dict(start_capital=float(cfg["START_CAPITAL"]), cash=float(cfg["START_CAPITAL"]), positions={},
                equity_history=[], trades=[], inception=None, last_signal=None, last_mtm=None)


def last_close(prices: pd.DataFrame, ticker: str):
    if ticker not in prices.columns:
        return None
    s = prices[ticker].dropna()
    return float(s.iloc[-1]) if len(s) else None


def equity(state: dict, prices: pd.DataFrame) -> float:
    tot = state["cash"]
    for t, p in state["positions"].items():
        px = last_close(prices, t) or p["entry_price"]
        tot += p["shares"] * px
    return float(tot)


def accrue_cash(state: dict, asof) -> None:
    """Compound idle cash at CASH_PA on calendar days since the last mark. (The backtests accrued
    per trading row, which is slightly more conservative.)"""
    if state.get("last_mtm"):
        days = (pd.Timestamp(asof) - pd.Timestamp(state["last_mtm"])).days
        if days > 0:
            state["cash"] *= (1.0 + state["_cash_pa"]) ** (days / 365.0)


def rebalance(state: dict, targets: dict, prices: pd.DataFrame, cfg: dict, asof, reason: str) -> list:
    """Trade the paper portfolio to `targets` (ticker -> weight of equity) at the latest close.
    Sells fill at close*(1-slip), buys at close*(1+slip); Rs FEE_PER_LEG per leg. Returns the trades."""
    slip = cfg["SLIPPAGE_BPS"] / 1e4
    fee = cfg["FEE_PER_LEG"]
    E = equity(state, prices)
    min_trade = cfg["MIN_TRADE_FRAC"] * E
    d = pd.Timestamp(asof).strftime("%Y-%m-%d")
    trades = []

    # --- sells first (full exits, then trims) so buys are funded from proceeds
    for t in list(state["positions"]):
        pos = state["positions"][t]
        px = last_close(prices, t) or pos["entry_price"]
        cur_val = pos["shares"] * px
        tgt_val = targets.get(t, 0.0) * E
        if t not in targets:
            sell_sh = pos["shares"]
        elif cur_val - tgt_val > min_trade:
            sell_sh = (cur_val - tgt_val) / px
        else:
            continue
        fill = px * (1.0 - slip)
        proceeds = sell_sh * fill - fee
        state["cash"] += proceeds
        trades.append(dict(date=d, type="SELL", ticker=t, price=fill, shares=sell_sh, value=sell_sh * fill,
                           pnl_pct=(fill / pos["entry_price"] - 1.0) * 100.0,
                           reason=reason if t not in targets else "trim to target weight"))
        pos["shares"] -= sell_sh
        if pos["shares"] <= 1e-9:
            del state["positions"][t]

    # --- buys, scaled down if cash would not cover the fees and slippage
    wants = []
    for t, w in targets.items():
        px = last_close(prices, t)
        if not px:
            continue
        cur_val = state["positions"].get(t, {}).get("shares", 0.0) * px
        add_val = w * E - cur_val
        if add_val > min_trade:
            wants.append((t, px, add_val))
    need = sum(v + fee for _, _, v in wants)
    scale = min(1.0, state["cash"] / need) if need > 0 else 1.0
    for t, px, add_val in wants:
        spend = add_val * scale
        fill = px * (1.0 + slip)
        shares = max(spend - fee, 0.0) / fill
        if shares <= 0:
            continue
        state["cash"] -= shares * fill + fee
        pos = state["positions"].get(t)
        if pos:
            tot = pos["shares"] + shares
            pos["entry_price"] = (pos["entry_price"] * pos["shares"] + fill * shares) / tot
            pos["shares"] = tot
        else:
            state["positions"][t] = dict(shares=shares, entry_price=fill, entry_date=d)
        trades.append(dict(date=d, type="BUY", ticker=t, price=fill, shares=shares, value=shares * fill,
                           pnl_pct=None, reason=reason))
    return trades


def _prepare(state: dict, cfg: dict) -> None:
    state["_cash_pa"] = cfg["CASH_PA"]


def _strip(state: dict) -> dict:
    s = dict(state)
    s.pop("_cash_pa", None)
    return s


# --------------------------------------------------------------------------- update / preview
def current_signal(prices: pd.DataFrame, cfg: dict):
    sd = month_end_signal_date(prices.index)
    if sd is None:
        return None
    return compute_signal(prices, sd, cfg)


def preview(root: Path | None, prices: pd.DataFrame, cfg: dict | None = None) -> dict:
    """Everything the dashboard shows. Read-only: nothing is written."""
    root = root or ROOT
    cfg = cfg or load_config(root)
    log = load_log(root)
    asof = prices.index[-1]
    age = int((pd.Timestamp.today().normalize() - asof.normalize()).days)
    sig = current_signal(prices, cfg)
    state = copy.deepcopy(log["state"]) if log else new_state(cfg)
    _prepare(state, cfg)
    accrue_cash(state, asof)            # display-only copy: cash marked up to the latest data date

    due = bool(sig) and (log.get("state", {}).get("last_signal") if log else None) != (sig or {}).get("signal_date")
    pending = []
    if due:
        sim = copy.deepcopy(state)
        pending = rebalance(sim, sig["targets"], prices, cfg, asof,
                            f"{'monthly rotation' if log else 'initial allocation'} ({sig['signal_date']} signal)")
    lag = int((asof - pd.Timestamp(sig["signal_date"])).days) if sig else None

    positions = []
    for t, p in state["positions"].items():
        px = last_close(prices, t)
        positions.append(dict(ticker=t, shares=p["shares"], entry_date=p["entry_date"], entry_px=p["entry_price"],
                              last_px=px, value=(px or p["entry_price"]) * p["shares"],
                              pnl=((px / p["entry_price"] - 1.0) * 100.0) if px else None))
    eq_now = equity(state, prices)
    hist = {p["date"]: p["equity"] for p in state["equity_history"]}
    if log:
        hist[str(asof.date())] = eq_now
    dates, vals = list(hist), list(hist.values())
    peak, dd = 0.0, 0.0
    for v in vals:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1.0) if peak else dd
    closed = [t for t in state["trades"] if t["type"] == "SELL"]
    wins = [t for t in closed if (t.get("pnl_pct") or 0) > 0]
    perf = None
    if log:
        perf = dict(equity=eq_now, ret=(eq_now / state["start_capital"] - 1.0) * 100.0, max_dd=dd * 100.0,
                    n_closed=len(closed), win_rate=(len(wins) / len(closed) * 100.0) if closed else None,
                    cash_pct=(state["cash"] / eq_now * 100.0) if eq_now else 0.0,
                    inception=state.get("inception"), dates=dates, equity_series=vals,
                    trades=list(reversed(state["trades"])))
    nxt = (asof + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
    return dict(cfg=cfg, asof=str(asof.date()), age=age, stale=age > cfg["MAX_DATA_AGE_DAYS"],
                signal=sig, due=due, lag_days=lag, late=bool(lag is not None and lag > cfg["LATE_WARN_DAYS"]),
                pending=pending, positions=positions, cash=state["cash"], perf=perf, has_history=bool(log),
                rebalances=list(reversed(log.get("rebalances", []))) if log else [], next_signal=nxt,
                last_signal=state.get("last_signal"))


def run_update(root: Path | None, prices: pd.DataFrame, cfg: dict | None = None, force: bool = False,
               today=None) -> dict:
    """Mark the paper portfolio to market and, if a new month-end signal exists that has not been
    applied yet, rebalance. Idempotent: running twice in the same month only refreshes the mark."""
    root = root or ROOT
    cfg = cfg or load_config(root)
    asof = prices.index[-1]
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.today()
    age = int((now.normalize() - asof.normalize()).days)
    if not force and age > cfg["MAX_DATA_AGE_DAYS"]:
        return dict(status="skipped", reason=f"price data is {age} days old (as of {asof.date()}; limit "
                                             f"{cfg['MAX_DATA_AGE_DAYS']}) -- rotation sleeve not updated")
    sig = current_signal(prices, cfg)
    if sig is None:
        return dict(status="skipped", reason="price data spans less than one completed month")
    bad = [r for r in sig["rows"] if r["status"] != "OK"]
    if len(bad) == len(sig["rows"]):
        return dict(status="skipped", reason="no universe ETF has enough clean history for the signal: "
                                             + "; ".join(sig["issues"]))

    log = load_log(root)
    state = log["state"] if log else new_state(cfg)
    _prepare(state, cfg)
    first_run = not log
    if first_run:
        state["inception"] = str(asof.date())
        state["last_mtm"] = str(asof.date())
    accrue_cash(state, asof)

    actions, rebal_entry = [], None
    if state.get("last_signal") != sig["signal_date"]:
        lag = int((asof - pd.Timestamp(sig["signal_date"])).days)
        tag = "initial allocation" if first_run else "monthly rotation"
        actions = rebalance(state, sig["targets"], prices, cfg, asof, f"{tag} ({sig['signal_date']} signal)")
        state["trades"].extend(actions)
        state["last_signal"] = sig["signal_date"]
        rebal_entry = dict(signal_date=sig["signal_date"], exec_date=str(asof.date()), lag_days=lag,
                           late=lag > cfg["LATE_WARN_DAYS"], targets=sig["targets"], cash_weight=sig["cash_weight"],
                           ranking=[dict(ticker=r["ticker"], status=r["status"], ret=r["ret"], rank=r["rank"],
                                         abs_ok=r["abs_ok"], selected=r["selected"]) for r in sig["rows"]],
                           trades=actions, equity=equity(state, prices))
    state["last_mtm"] = str(asof.date())
    eq_now = equity(state, prices)
    hist = [p for p in state["equity_history"] if p["date"] != str(asof.date())]
    hist.append(dict(date=str(asof.date()), equity=eq_now))
    state["equity_history"] = hist

    out = dict(state=_strip(state), config=cfg, rebalances=(log.get("rebalances", []) if log else []))
    if rebal_entry:
        out["rebalances"].append(rebal_entry)
    out["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _atomic_write(root / LOG_FILE, out)
    return dict(status="ok", rebalanced=bool(rebal_entry), n_trades=len(actions), signal_date=sig["signal_date"],
                data_asof=str(asof.date()), equity=eq_now, first_run=first_run)


# --------------------------------------------------------------------------- CLI
def _print_preview(pv: dict) -> None:
    sig = pv["signal"]
    print(f"\nROTATION SLEEVE (paper)  data as of {pv['asof']} ({pv['age']}d old)")
    if not sig:
        print("  Not enough data for a month-end signal.")
        return
    print(f"  Signal date {sig['signal_date']}  |  12M return must beat cash {sig['hurdle']*100:.1f}%  |  "
          f"top {pv['cfg']['TOP_N']} of {len(sig['rows'])}")
    print(f"  {'Rank':>4}  {'Ticker':<12}{'12M ret':>9}  {'Beats cash':<11}{'Selected':<9}{'Weight':>7}  Status")
    for r in sig["rows"]:
        rk = "-" if r["rank"] is None else str(r["rank"])
        ret = "   n/a" if r["ret"] is None else f"{r['ret']*100:7.1f}%"
        print(f"  {rk:>4}  {r['ticker']:<12}{ret:>9}  {('yes' if r['abs_ok'] else 'no'):<11}"
              f"{('yes' if r['selected'] else ''):<9}{r['weight']*100:6.0f}%  {r['status']}")
    print(f"  Target cash: {sig['cash_weight']*100:.0f}%")
    if pv["due"]:
        print(f"  This month's rebalance is DUE ({len(pv['pending'])} paper trade(s)):")
        for t in pv["pending"]:
            print(f"    {t['type']:<4} {t['ticker']:<12} {t['shares']:,.2f} @ {t['price']:,.2f}")
    else:
        print("  This month's rebalance is already applied (or nothing to do).")


def main(argv: list) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if "--reset" in argv:
        bak = reset_log(ROOT)
        print(f"Archived to {bak.name}" if bak else "Nothing to reset.")
        return 0
    import etf_momentum_ranking as emr          # read-only use: only its price loader
    cfg = load_config(ROOT)
    path = ROOT / emr.CONFIG.INPUT_FILE
    _, prices = emr.load_etf_data(str(path))
    if "--update" in argv:
        res = run_update(ROOT, prices, cfg, force="--force" in argv)
        print(res)
        return 0 if res["status"] == "ok" else 1
    _print_preview(preview(ROOT, prices, cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
