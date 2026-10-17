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
        regime  = emr.regime_status(prices)
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
    holdings_metrics = emr.evaluate_holdings_exit_rules(tl["holdings_metrics"], ranking, prices)
    core.sync_to_positions_ledger(active_holdings)

    allocation = core.build_live_allocation(ranking, regime, active_holdings, prices)
    held = set(allocation.loc[allocation["TICKER"] != "CASH", "TICKER"])

    # ── Regime banner ──
    is_bull = regime["label"] == "BULL"
    data_range = f"{prices.index[0]:%Y-%m-%d} → {prices.index[-1]:%Y-%m-%d}"
    banner = [
        ("Regime",           f"{'🟢' if is_bull else '🔴'} {regime['label']}", "bull" if is_bull else "bear"),
        ("Price",            f"{regime['nifty_price']:.2f}", None),
        ("50 EMA (trend)",   f"{regime['nifty_ema_50']:.2f}", None),
        ("New-Buy Slots",    f"{regime['active_slots']} / {emr.CONFIG.TOP_N}", None),
        ("Current Holdings", f"{len(held)} / {emr.CONFIG.TOP_N}", None),
        ("Trend Ticker",     regime.get("trend_ticker", "N/A"), None),
        ("Data Range",       data_range, None),
    ]

    # ── Tab 1: allocation ──
    alloc_rows = [dict(slot=r["SLOT"], ticker=r["TICKER"], name=r["ETF_NAME"], sector=r["SECTOR"],
                       weight=f"{r['WEIGHT']:.0%}", inv_rank=str(r["INV_RANK"]),
                       is_cash=r["TICKER"] == "CASH")
                  for r in allocation.to_dict("records")]

    # ── Tab 2: full rankings ──
    rank_rows = [dict(rank=None if pd.isna(r["RANK_INVESTABLE"]) else int(r["RANK_INVESTABLE"]),
                      ticker=r["TICKER"], name=r["ETF_NAME"], sector=r["SECTOR"],
                      wtd=_num(r["WTD_SHARPE"]), s6=_num(r["SHARPE_6M"]), s3=_num(r["SHARPE_3M"]),
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

    return dict(
        fatal=None, notices=notices,
        banner=banner,
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
        boot=dict(prices=latest, tickers=etf_tickers, topn_max=len(rank_rows)),
    )


@app.template_filter("inr")
def _inr(v):
    return f"Rs {v:,.2f}"


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
    return {"message": "Weekly rebalance complete."}


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
