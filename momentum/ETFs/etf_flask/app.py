"""
ETF Momentum Strategy -- Flask Dashboard
=========================================
Flask port of etf_dashboard.py (the Streamlit version is kept as a backup).
Run:  python etf_flask/app.py        (serves http://127.0.0.1:8502)

The page is server-rendered on every load (like a Streamlit rerun); actions
(log/edit/delete trades, save config, rebalance, refresh prices, upload) are
JSON POSTs under /api and the page reloads itself afterwards.

Single-user local tool: state that Streamlit kept in st.session_state /
st.cache_data lives in the module-level STATE dict, guarded by LOCK.
"""

import datetime
import logging
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path

import pandas as pd
import yfinance as yf
from flask import Flask, abort, jsonify, render_template, request, send_file

import core
from core import ROOT, ValidationError, emr
import etf_rotation_sleeve as rot      # separate paper-only monthly sleeve; core put ROOT on sys.path

# run_pipeline prints non-ASCII characters; don't let a cp1252 console kill it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024       # uploaded .xlsx cap
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0               # always serve fresh css/js
log = logging.getLogger("etf_flask")

LOCK = threading.RLock()
STATE = {
    "uploaded_path": None,     # custom NAV file chosen on the Configuration tab
    "uploaded_name": None,
    "live_prices":   {},       # yfinance prices from "Refresh Live Market Prices"
    "model":         None,     # cached (meta, prices, regime, ranking)
}
UPLOAD_DIR = ROOT / ".flask_tmp"


class ViewError(Exception):
    """Data could not be loaded / ranked -- shown in place of the dashboard."""


# =========================================================
# MODEL CACHE (replaces st.cache_data)
# =========================================================
def input_path() -> str:
    return STATE["uploaded_path"] or str(ROOT / emr.CONFIG.INPUT_FILE)


def invalidate_model():
    STATE["model"] = None


def get_model() -> dict:
    """meta / prices / regime / ranking for the current input file.
    Regime + ranking are pure functions of the data and CONFIG, so they are
    cached until the file changes or CONFIG is saved / a rebalance runs."""
    path = input_path()
    if not Path(path).exists():
        raise ViewError(f"Data file not found: {path}. Ensure {emr.CONFIG.INPUT_FILE} is in "
                        f"the script directory or upload a custom file in the Configuration tab.")
    key = (path, os.path.getmtime(path))
    model = STATE["model"]
    if model and model["key"] == key:
        return model
    try:
        meta, prices = emr.load_etf_data(path)
    except Exception as e:
        raise ViewError(f"Error loading data: {e}")
    try:
        regime  = emr.regime_status(prices, variant=emr.get_variant())
        ranking = emr.build_ranking(meta, prices)
    except Exception as e:
        raise ViewError(f"Error computing rankings: {e}")
    STATE["model"] = dict(key=key, meta=meta, prices=prices, regime=regime, ranking=ranking)
    return STATE["model"]


# =========================================================
# PAGE
# =========================================================
def _num(x):
    return None if pd.isna(x) else float(x)


def _score_mode() -> str:
    ws, wc = emr.CONFIG.BLEND_W_SHARPE, emr.CONFIG.BLEND_W_CLENOW
    if wc <= 0:
        return "Sharpe composite only"
    if ws <= 0:
        return "Clenow 90D only"
    return f"Blend: {ws:.0%} Sharpe / {wc:.0%} Clenow"


def build_view() -> dict:
    model = get_model()
    meta, prices, regime, ranking = (model[k] for k in ("meta", "prices", "regime", "ranking"))

    etf_tickers = sorted(meta["TICKER"].tolist()) if "TICKER" in meta.columns else []
    latest = {t: core.get_etf_latest_price(t, prices) for t in etf_tickers}
    for tk, px in STATE["live_prices"].items():
        if tk in latest:
            latest[tk] = px

    tradelog, notices = core.load_tradelog()
    tl = core.calculate_holdings_and_pnl(tradelog, latest)
    active_holdings  = tl["active_holdings"]
    holdings_metrics = emr.evaluate_holdings_exit_rules(tl["holdings_metrics"], ranking, prices,
                                                        variant=emr.get_variant())
    core.sync_to_positions_ledger(active_holdings)

    allocation = core.build_live_allocation(ranking, regime, active_holdings, prices)
    held = set(allocation.loc[allocation["TICKER"] != "CASH", "TICKER"])

    # ── Regime banner ──
    is_bull = regime["label"] == "BULL"
    data_range = f"{prices.index[0]:%Y-%m-%d} → {prices.index[-1]:%Y-%m-%d}"
    price_date = f"{prices.index[-1].day} {prices.index[-1]:%b}"      # top-bar status pill
    banner = [
        ("Regime",           regime['label'], "bull" if is_bull else "bear"),
        ("Price",            f"{regime['nifty_price']:.2f}", None),
        (regime.get("regime_ma_label", "50 EMA (trend)"),
                             f"{regime.get('regime_ma', regime['nifty_ema_50']):.2f}", None),
        ("New-Buy Slots",    f"{regime['active_slots']} / {emr.CONFIG.TOP_N}", None),
        ("Current Holdings", f"{len(held)} / {emr.CONFIG.TOP_N}", None),
        ("Trend Ticker",     regime.get("trend_ticker", "N/A"), None),
        ("Data Range",       data_range, None),
        ("Ranking Score",    _score_mode(), None),
    ]

    # ── Tab 1: allocation ──
    alloc_rows = [dict(slot=r["SLOT"], ticker=r["TICKER"], name=r["ETF_NAME"], sector=r["SECTOR"],
                       weight=f"{r['WEIGHT']:.0%}", inv_rank=str(r["INV_RANK"]),
                       is_cash=r["TICKER"] == "CASH")
                  for r in allocation.to_dict("records")]

    # ── Tab 2: full rankings ──
    rank_rows = [dict(rank=None if pd.isna(r["RANK_INVESTABLE"]) else int(r["RANK_INVESTABLE"]),
                      ticker=r["TICKER"], name=r["ETF_NAME"], sector=r["SECTOR"],
                      blend=_num(r["WTD_BLEND"]), wtd=_num(r["WTD_SHARPE"]),
                      s6=_num(r["SHARPE_6M"]), s3=_num(r["SHARPE_3M"]), cl=_num(r["CLENOW_90D"]),
                      screen=bool(r["SCREEN_PASS"]), held=r["TICKER"] in held)
                 for r in ranking.to_dict("records")]

    # ── Tab 3: config ──
    cfg = emr.get_config_as_dict()
    cfg_path = ROOT / "strategy_config.json"
    default_input = cfg.get("INPUT_FILE", "ETF_updated.xlsx")

    # ── Tab 4: tradelog & MTM ──
    holdings = sorted(holdings_metrics, key=lambda h: h["Unrealized PnL"], reverse=True)
    for h in holdings:
        d = h["First Buy Date"]
        h["First Buy Date"] = d.isoformat() if hasattr(d, "isoformat") else str(d)
    invested = sum(h["Cost Value"] for h in holdings)
    market   = sum(h["Market Value"] for h in holdings)
    unreal   = market - invested

    try:
        paper = build_paper_view(model, held)
    except Exception as e:                    # a paper-tab problem must never take the whole page down
        log.exception("paper tab failed")
        paper = dict(error=f"{type(e).__name__}: {e}")

    try:
        rotation = build_rotation_view(model)
    except Exception as e:                    # same rule: a sleeve problem must never take the whole page down
        log.exception("rotation tab failed")
        rotation = dict(error=f"{type(e).__name__}: {e}")

    return dict(
        fatal=None, notices=notices, price_date=price_date,
        banner=banner, paper=paper, rotation=rotation,
        alloc_rows=alloc_rows,
        rank_rows=rank_rows,
        sectors=sorted({r["sector"] for r in rank_rows}),
        universe=len(meta),
        n_investable=int(ranking["SCREEN_PASS"].sum()),
        n_screened_out=int((~ranking["SCREEN_PASS"]).sum()),
        output_name=emr.CONFIG.OUTPUT_FILE,
        output_exists=(ROOT / emr.CONFIG.OUTPUT_FILE).exists(),
        cfg=cfg, num_fields=core.NUM_FIELDS,
        cfg_fallbacks=", ".join(cfg["REGIME_FALLBACKS"]),
        cfg_exclude=", ".join(cfg.get("EXCLUDE_SECTORS", [])),
        n_excluded=int(ranking["SECTOR"].isin(emr.CONFIG.EXCLUDE_SECTORS).sum()),
        score_mode=_score_mode(),
        cfg_file_text=cfg_path.read_text() if cfg_path.exists() else None,
        default_input=default_input,
        default_input_exists=(ROOT / default_input).exists(),
        uploaded_name=STATE["uploaded_name"],
        holdings=holdings,
        breached=[h for h in holdings if h["Exit Reason"] != "OK"],
        totals=dict(invested=invested, market=market, unreal=unreal,
                    unreal_pct=(unreal / invested * 100) if invested > 0 else 0.0,
                    realized=tl["realized_pnl"]),
        transactions=[dict(tx, total=tx["quantity"] * tx["price"]) for tx in reversed(tradelog)],
        etf_tickers=etf_tickers,
        today=datetime.date.today().isoformat(),
        boot=dict(prices=latest, tickers=etf_tickers, topn_max=len(rank_rows),
                  paper_chart=paper.get("chart"),
                  paper_equity=(dict(dates=paper["perf"]["dates"], values=paper["perf"]["equity_series"])
                                if paper.get("perf") else None),
                  rotation_equity=(dict(dates=rotation["perf"]["dates"], values=rotation["perf"]["equity_series"])
                                   if rotation.get("perf") else None)),
    )



# =========================================================
# PAPER TRADE (v2) TAB -- simulated portfolio, no real orders
# =========================================================
def _isnum(v) -> bool:
    """True only for a real, non-NaN number (None / NaN / Jinja Undefined / strings -> False)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def build_paper_view(model: dict, held: set) -> dict:
    """Everything the Paper Trade tab shows. Read-only: nothing is written here."""
    cfg = emr.CONFIG
    prices, ranking, regime = model["prices"], model["ranking"], model["regime"]
    variant = emr.get_variant(cfg.PAPER_VARIANT)
    n_ma = int(variant.get("regime_window") or 200)

    plog = emr.load_paper_log(ROOT)
    last = plog[sorted(plog)[-1]] if plog else None
    asof = prices.index[-1]
    age = int((pd.Timestamp.today().normalize() - asof.normalize()).days)

    pv = emr.paper_preview(ROOT, ranking, prices)
    preg, palloc, pexits, pstate = pv["regime"], pv["allocation"], pv["exits"], pv["state"]

    since = preg.get("state_since")
    live_ma = regime.get("regime_ma", regime["nifty_ema_50"])
    reg = dict(
        paper=dict(label=preg["label"], risk_on=bool(preg["risk_on"]), price=_num(preg["nifty_price"]),
                   ma=_num(preg.get("regime_ma", float("nan"))), dist=_num(preg.get("dist_pct", float("nan"))),
                   since=pd.Timestamp(since).strftime("%d %b %Y") if since is not None else "n/a",
                   days=int(preg.get("state_days") or 0), source=preg.get("trend_ticker", "N/A"),
                   short_history=bool(preg.get("insufficient_history"))),
        live=dict(label=regime["label"], price=_num(regime["nifty_price"]), ma=_num(live_ma),
                  dist=(regime["nifty_price"] / live_ma - 1) * 100 if live_ma else None,
                  ma_label=regime.get("regime_ma_label", "50-day EMA")),
    )

    # holdings the paper variant recommends right now
    rows = []
    for r in palloc.to_dict("records"):
        t = r["TICKER"]
        if t == "CASH":
            continue
        pos = pstate["positions"].get(t)
        px = emr._last_close(prices, t)
        if pos:
            peak = max(pos.get("peak") or 0, px or 0)
            rows.append(dict(slot=int(r["SLOT"]), ticker=t, name=r["ETF_NAME"], sector=r["SECTOR"], rank=str(r["INV_RANK"]),
                             status="HOLD", entry_date=pos["entry_date"], entry_px=pos["entry_price"], last_px=px,
                             pnl=(px / pos["entry_price"] - 1) * 100 if px else None,
                             from_peak=(px / peak - 1) * 100 if px and peak else None))
        else:
            rows.append(dict(slot=int(r["SLOT"]), ticker=t, name=r["ETF_NAME"], sector=r["SECTOR"], rank=str(r["INV_RANK"]),
                             status="NEW BUY", entry_date="next update", entry_px=px, last_px=px, pnl=None, from_peak=None))
    n_cash = int((palloc["TICKER"] == "CASH").sum())
    cash_reason = palloc.loc[palloc["TICKER"] == "CASH", "REASON"].iloc[0] if n_cash else ""

    pending = [dict(action="SELL", ticker=e["ticker"], name=pstate["positions"][e["ticker"]]["name"], reason=e["reason"])
               for e in pexits]
    pending += [dict(action="BUY", ticker=r["ticker"], name=r["name"], reason=f"Rank {r['rank']} in {r['sector']}")
                for r in rows if r["status"] == "NEW BUY"]

    # last executed update + performance
    last_view, perf = None, None
    if last:
        lr = last["regime"]
        last_view = dict(week=last["week"], run_date=last["run_date"], data_asof=last["data_asof"], regime=lr.get("label"),
                         price=lr.get("nifty_price"), ma=lr.get("regime_ma"), ma_window=lr.get("regime_ma_window"),
                         equity=last["equity"], actions=last["actions"])
        stt = last["state"]
        eq_now = emr.paper_equity(stt, prices)
        hist = {}
        for p in stt["equity_history"]:
            hist[p["date"]] = p["equity"]
        hist[str(asof.date())] = eq_now
        dates, vals = list(hist), list(hist.values())
        peak, dd = 0.0, 0.0
        for v in vals:
            peak = max(peak, v)
            dd = min(dd, v / peak - 1)
        closed = [t for t in stt["trades"] if t["type"] == "SELL"]
        wins = [t for t in closed if (t.get("pnl_pct") or 0) > 0]
        perf = dict(equity=eq_now, ret=(eq_now / stt["start_capital"] - 1) * 100, max_dd=dd * 100,
                    n_closed=len(closed), win_rate=(len(wins) / len(closed) * 100) if closed else None,
                    cash_pct=stt["cash"] / eq_now * 100 if eq_now else 0.0, inception=stt["inception"],
                    dates=dates, equity_series=vals, trades=stt["trades"][::-1])

    paper_set = {r["ticker"] for r in rows}
    top = ranking[ranking["SCREEN_PASS"] & ranking["RANK_INVESTABLE"].notna()].sort_values("RANK_INVESTABLE").head(15)
    top_rows = [dict(rank=int(r["RANK_INVESTABLE"]), ticker=r["TICKER"], name=r["ETF_NAME"], sector=r["SECTOR"],
                     s6=_num(r["SHARPE_6M"]), s3=_num(r["SHARPE_3M"]), cl=_num(r["CLENOW_90D"]),
                     pfh=_num(r["PCT_FROM_HIGH"]), held=r["TICKER"] in pstate["positions"])
                for r in top.to_dict("records")]

    tf = emr.trend_frame(prices, ROOT, variant, tail=300)
    chart = None
    if len(tf):
        chart = dict(dates=[d.strftime("%Y-%m-%d") for d in tf.index],
                     series=[dict(name=f"{n_ma}-day SMA (paper signal)", color="#4F46E5", data=[_num(v) for v in tf["ma"]]),
                             dict(name="50-day EMA (live signal)", color="#98A1AE", data=[_num(v) for v in tf["ema50"]]),
                             dict(name="Nifty 500", color="#B42318", data=[_num(v) for v in tf["price"]])])

    return dict(
        variant_name=variant["name"], live_variant_name=emr.get_variant()["name"], n_ma=n_ma,
        exit_rank_v2=variant.get("exit_max_rank"), exit_rank_live=cfg.EXIT_MAX_RANK,
        asof=str(asof.date()), age=age, stale=age > cfg.PAPER_MAX_DATA_AGE_DAYS, max_age=cfg.PAPER_MAX_DATA_AGE_DAYS,
        enabled=bool(cfg.PAPER_TRADE_ENABLED), start_capital=cfg.PAPER_START_CAPITAL, slip_bps=cfg.PAPER_SLIPPAGE_BPS,
        tsl=cfg.TSL_THRESHOLD, top_n=cfg.TOP_N, last=last_view, perf=perf, reg=reg, rows=rows, n_cash=n_cash,
        cash_reason=cash_reason, pending=pending, has_history=bool(plog), top_rows=top_rows,
        live_only=sorted(held - paper_set), paper_only=sorted(paper_set - held), both=sorted(held & paper_set),
        flat_no_positions=(not preg["risk_on"]) and not pstate["positions"],
        chart=chart,
    )


# =========================================================
# ROTATION SLEEVE TAB -- monthly dual momentum, paper only
# =========================================================
def build_rotation_view(model: dict) -> dict:
    """Everything the Rotation Sleeve tab shows. Read-only: nothing is written here."""
    prices, meta = model["prices"], model["meta"]
    names = dict(zip(meta["TICKER"], meta["ETF_NAME"])) if "ETF_NAME" in meta.columns else {}
    nm = lambda t: names.get(t) or t                                   # noqa: E731
    pv = rot.preview(ROOT, prices)
    cfg, sig, perf = pv["cfg"], pv["signal"], pv["perf"]

    rows = [dict(rank=r["rank"], ticker=r["ticker"], name=nm(r["ticker"]),
                 ret=None if r["ret"] is None else r["ret"] * 100.0, abs_ok=r["abs_ok"], selected=r["selected"],
                 weight=r["weight"] * 100.0, status=r["status"])
            for r in (sig["rows"] if sig else [])]
    pending = [dict(action=t["type"], ticker=t["ticker"], name=nm(t["ticker"]), shares=t["shares"], price=t["price"],
                    value=t["value"], reason=t["reason"]) for t in pv["pending"]]
    positions = [dict(p, name=nm(p["ticker"])) for p in pv["positions"]]
    if perf:
        perf = dict(perf, trades=[dict(t, name=nm(t["ticker"])) for t in perf["trades"]])
    history = [dict(signal_date=r["signal_date"], exec_date=r["exec_date"], lag=r["lag_days"], late=r.get("late"),
                    holdings=", ".join(r["targets"]) or "100% cash", cash=r["cash_weight"] * 100.0,
                    equity=r["equity"]) for r in pv["rebalances"][:12]]
    n_ok = sum(1 for r in rows if r["status"] == "OK")
    return dict(
        cfg=cfg, asof=pv["asof"], age=pv["age"], stale=pv["stale"], signal=sig, rows=rows, issues=sig["issues"] if sig else [],
        hurdle=(sig["hurdle"] * 100.0) if sig else None, cash_target=(sig["cash_weight"] * 100.0) if sig else None,
        n_ok=n_ok, due=pv["due"], lag=pv["lag_days"], late=pv["late"], pending=pending, positions=positions,
        cash=pv["cash"], perf=perf, has_history=pv["has_history"], history=history, next_signal=pv["next_signal"],
        last_signal=pv["last_signal"], enabled=bool(cfg["AUTO_UPDATE"]), lookback_m=round(cfg["LOOKBACK_DAYS"] / 21),
    )


@app.template_filter("inr")
def _inr(v):
    return f"Rs {v:,.2f}"


@app.template_filter("fmt")
def _fmt(v, spec="{:,.2f}"):
    """Format a number with `spec`, or an em dash for None/NaN."""
    return spec.format(v) if _isnum(v) else "—"


@app.template_filter("f3")
def _f3(v):
    return "—" if v is None else f"{v:.3f}"


@app.get("/")
def index():
    with LOCK:
        try:
            return render_template("dashboard.html", **build_view())
        except ViewError as e:
            return render_template("dashboard.html", fatal=str(e), notices=[]), 500


@app.get("/favicon.ico")
def favicon():
    return "", 204          # the page ships an inline emoji icon; stop browsers logging a 404


@app.get("/download/rankings")
def download_rankings():
    path = ROOT / emr.CONFIG.OUTPUT_FILE
    if not path.exists():
        abort(404, "Run the weekly rebalance first to generate the Excel output.")
    return send_file(path, as_attachment=True, download_name=emr.CONFIG.OUTPUT_FILE)


# =========================================================
# API
# =========================================================
@app.before_request
def _csrf_guard():
    # Browsers can't add a custom header to a cross-site form post, so this
    # keeps other web pages from driving the local API.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "fetch":
        abort(403)


def api(fn):
    """JSON in / JSON out; ValidationError -> 400, anything else -> 500."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return jsonify(ok=True, **(fn(*args, **kwargs) or {}))
        except ValidationError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            log.exception("API error in %s", fn.__name__)
            return jsonify(ok=False, error=f"{type(e).__name__}: {e}"), 500
    return wrapper


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("Expected a JSON object body.")
    return body


def _ticker_universe() -> set:
    return set(get_model()["meta"]["TICKER"])


def _commit_tradelog(candidate: list):
    core.save_tradelog(candidate)
    core.sync_to_positions_ledger(core.calculate_holdings_and_pnl(candidate)["active_holdings"])


@app.post("/api/trades")
@api
def add_trade():
    body = _json_body()
    with LOCK:
        tradelog, _ = core.load_tradelog()
        t = core.parse_trade(body, _ticker_universe())
        if t["action"] == "SELL":
            held = core.calculate_holdings_and_pnl(tradelog)["active_holdings"]
            curr_qty = held.get(t["ticker"], {}).get("qty", 0.0)
            if t["quantity"] > curr_qty:
                raise ValidationError(f"Cannot SELL {t['quantity']} units of {t['ticker']} -- "
                                      f"you only hold {curr_qty:.0f} units. Trade not recorded.")
        new_trade = {"id": str(uuid.uuid4()), "timestamp": datetime.datetime.now().isoformat(), **t}
        candidate = tradelog + [new_trade]
        core.check_integrity(candidate, "Trade")
        _commit_tradelog(candidate)
    return {"message": f"Recorded {t['action']} {t['quantity']} units of {t['ticker']} @ Rs {t['price']:.2f}"}


@app.put("/api/trades/<tx_id>")
@api
def edit_trade(tx_id):
    body = _json_body()
    with LOCK:
        tradelog, _ = core.load_tradelog()
        idx = next((i for i, tx in enumerate(tradelog) if tx.get("id") == tx_id), None)
        if idx is None:
            raise ValidationError("Transaction not found.")
        # A legacy row may reference a ticker outside the current universe; allow it to stay.
        t = core.parse_trade(body, _ticker_universe() | {tradelog[idx]["ticker"]})
        candidate = [tx.copy() for tx in tradelog]
        candidate[idx].update(t)
        core.check_integrity(candidate, "Edit")
        _commit_tradelog(candidate)
    return {"message": "Transaction updated and positions ledger synced."}


@app.post("/api/trades/delete")
@api
def delete_trades():
    ids = set(_json_body().get("ids") or [])
    with LOCK:
        tradelog, _ = core.load_tradelog()
        candidate = [tx for tx in tradelog if tx.get("id") not in ids]
        n = len(tradelog) - len(candidate)
        if n == 0:
            raise ValidationError("No matching transactions to delete.")
        core.check_integrity(candidate, "Deletion")
        _commit_tradelog(candidate)
    return {"message": f"Deleted {n} transaction(s) and synced positions ledger."}


@app.post("/api/refresh-prices")
@api
def refresh_prices():
    with LOCK:
        tradelog, _ = core.load_tradelog()
        tickers = list(core.calculate_holdings_and_pnl(tradelog)["active_holdings"])
    if not tickers:
        raise ValidationError("No active holdings to refresh.")

    def fetch(tkr):
        try:
            return tkr, yf.Ticker(f"{tkr}.NS").fast_info.last_price
        except Exception:
            return tkr, None

    with ThreadPoolExecutor(max_workers=20) as exe:
        live = {tkr: px for tkr, px in exe.map(fetch, tickers) if px}
    if not live:
        raise RuntimeError("Could not fetch any live prices. Check NSE tickers.")
    with LOCK:
        STATE["live_prices"] = live
    return {"message": f"Live prices updated for {len(live)} of {len(tickers)} holdings."}


def _save_config(values: dict):
    cfg = core.parse_config(values, emr.get_config_as_dict())
    emr.save_config_to_json(cfg, ROOT)
    emr._apply_json_config(emr.CONFIG, cfg)
    invalidate_model()


@app.post("/api/config")
@api
def save_config():
    with LOCK:
        _save_config(_json_body())
    return {"message": "Configuration saved to strategy_config.json"}


@app.post("/api/rebalance")
@api
def rebalance():
    """Optionally saves a config first ({"config": {...}}), then runs the full pipeline."""
    body = request.get_json(silent=True) or {}
    with LOCK:
        if body.get("config"):
            _save_config(body["config"])
        emr.run_pipeline(input_path())
        invalidate_model()
        note = ""
        if rot.load_config(ROOT)["AUTO_UPDATE"]:     # monthly paper sleeve; a failure here must not fail the rebalance
            try:
                res = rot.run_update(ROOT, get_model()["prices"])
                note = (f" Rotation sleeve: {'rebalanced' if res.get('rebalanced') else 'marked to market'}."
                        if res["status"] == "ok" else f" Rotation sleeve skipped: {res['reason']}")
            except Exception as e:
                log.exception("rotation sleeve auto-update failed")
                note = f" Rotation sleeve update failed ({type(e).__name__}); see the Rotation tab."
    return {"message": "Weekly rebalance complete." + note}


# ── Paper trade (simulated; own state file, never touches the live logs) ──
@app.post("/api/paper/update")
@api
def paper_update():
    force = bool(_json_body().get("force"))
    with LOCK:
        m = get_model()
        res = emr.run_paper_trade(ROOT, m["ranking"], m["prices"], force=force)
    if res["status"] != "ok":
        raise ValidationError(res["reason"])
    e = res["entry"]
    return {"message": f"Paper portfolio updated (week {e['week']}, data as of {e['data_asof']})."}


@app.post("/api/paper/reset")
@api
def paper_reset():
    with LOCK:
        bak = emr.reset_paper_log(ROOT)
    return {"message": f"Paper history archived to {bak.name}." if bak else "Nothing to reset."}


@app.post("/api/paper/toggle")
@api
def paper_toggle():
    enabled = bool(_json_body().get("enabled"))
    with LOCK:
        emr.save_config_to_json({"PAPER_TRADE_ENABLED": enabled}, ROOT)
        emr.CONFIG.PAPER_TRADE_ENABLED = enabled
    return {"message": f"Paper auto-update {'enabled' if enabled else 'disabled'}."}



# ── Rotation sleeve (paper only; own state file rotation_paper_log.json) ──
@app.post("/api/rotation/update")
@api
def rotation_update():
    force = bool(_json_body().get("force"))
    with LOCK:
        res = rot.run_update(ROOT, get_model()["prices"], force=force)
    if res["status"] != "ok":
        raise ValidationError(res["reason"])
    what = (f"rebalanced ({res['n_trades']} paper trade(s), signal {res['signal_date']})"
            if res["rebalanced"] else "marked to market (this month's rebalance was already applied)")
    return {"message": f"Rotation sleeve {what}."}


@app.post("/api/rotation/reset")
@api
def rotation_reset():
    with LOCK:
        bak = rot.reset_log(ROOT)
    return {"message": f"Rotation history archived to {bak.name}." if bak else "Nothing to reset."}


@app.post("/api/rotation/toggle")
@api
def rotation_toggle():
    enabled = bool(_json_body().get("enabled"))
    with LOCK:
        rot.save_config(ROOT, {"AUTO_UPDATE": enabled})
    return {"message": f"Rotation sleeve auto-update {'enabled' if enabled else 'disabled'}."}


@app.post("/api/upload")
@api
def upload():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".xlsx"):
        raise ValidationError("Choose an .xlsx ETF data file.")
    UPLOAD_DIR.mkdir(exist_ok=True)
    staged, final = UPLOAD_DIR / "uploaded_etf.new.xlsx", UPLOAD_DIR / "uploaded_etf.xlsx"
    f.save(staged)
    try:
        emr.load_etf_data(str(staged))
    except Exception as e:
        staged.unlink(missing_ok=True)
        raise ValidationError(f"Could not read that file as ETF data: {e}")
    with LOCK:
        os.replace(staged, final)
        STATE["uploaded_path"], STATE["uploaded_name"] = str(final), Path(f.filename).name
        invalidate_model()
    return {"message": "Uploaded file saved. Click Run Rebalance to apply."}


@app.post("/api/use-default")
@api
def use_default():
    with LOCK:
        STATE["uploaded_path"] = STATE["uploaded_name"] = None
        invalidate_model()
    return {"message": f"Using default file {emr.CONFIG.INPUT_FILE}."}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8502)), debug=False, threaded=True)
