"""
sharpe_dashboard.py
===================
Sharpe Momentum Strategy — Streamlit Dashboard (v3 — Dynamic Regime Engine)
Run:  streamlit run sharpe_dashboard.py
"""
import sys, json, datetime, uuid, shutil, tempfile
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import momentum_lib as ml

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Sharpe Momentum", page_icon="📊",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.stApp { background-color: #FFFFFF; }
div[data-testid="stMetric"] {
    background: #F5F7FA; border: 1px solid #E8ECF1;
    border-radius: 10px; padding: 14px 18px;
}
div[data-testid="stMetric"] label { color: #6B7A8D !important; font-size: 13px !important; }
div[data-testid="stMetric"] div[data-testid="stMetricValue"] { color: #1A1A2E !important; }
thead tr th { background-color: #1F4E79 !important; color: #FFFFFF !important; }
.stButton > button {
    background: #1F4E79; color: white; border: none;
    border-radius: 8px; font-weight: 600; padding: 8px 24px;
}
.stButton > button:hover { background: #163D5E; }
h2 { color: #1F4E79 !important; border-bottom: 2px solid #E8ECF1; padding-bottom: 8px; }
.score-high  { background:#E8F5E9; color:#2E7D32; padding:6px 16px; border-radius:20px; font-weight:700; display:inline-block; }
.score-mid   { background:#FFF8E1; color:#F57F17; padding:6px 16px; border-radius:20px; font-weight:700; display:inline-block; }
.score-low   { background:#FFEBEE; color:#C62828; padding:6px 16px; border-radius:20px; font-weight:700; display:inline-block; }
#MainMenu {visibility:hidden;} footer {visibility:hidden;}
</style>
""", unsafe_allow_html=True)

# ── TRADELOG & MTM HELPERS ───────────────────────────────────────────────────
def safe_write_json(path, data):
    """Atomic JSON write: write to .tmp, backup existing to .bak, rename .tmp → target."""
    path = Path(path)
    tmp_path = path.with_suffix(".tmp")
    bak_path = path.with_suffix(".bak")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        if path.exists():
            shutil.copy2(path, bak_path)
        shutil.move(str(tmp_path), str(path))
    except Exception as e:
        # Clean up tmp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise e

@st.cache_data(ttl=3600)
def get_live_vix():
    """Fetch live India VIX value, cached for 1 hour."""
    try:
        data = yf.Ticker('^INDIAVIX').history(period='1d')
        if not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400)
def compute_cap_tier_momentum(prices_df, stock_tickers):
    """Calculate the % of stocks with positive 63-day momentum per cap tier. Cached 24h."""
    mom_63 = {}
    for t in stock_tickers:
        px = prices_df.loc[t].dropna()
        if len(px) >= 63:
            ret = (px.iloc[-1] / px.iloc[-63]) - 1.0
            mom_63[t] = ret
        else:
            mom_63[t] = np.nan
            
    def fetch_mcap(ticker):
        try:
            info = yf.Ticker(f"{ticker}.NS").fast_info
            return ticker, info.market_cap
        except:
            try:
                info = yf.Ticker(f"{ticker}.BO").fast_info
                return ticker, info.market_cap
            except:
                return ticker, 0
                
    market_caps = {}
    with ThreadPoolExecutor(max_workers=30) as exe:
        results = exe.map(fetch_mcap, stock_tickers)
        for t, mcap in results:
            market_caps[t] = mcap
            
    df = pd.DataFrame({"MOM_63": pd.Series(mom_63), "MCAP": pd.Series(market_caps)})
    df = df[df["MCAP"] > 0]
    df = df.sort_values("MCAP", ascending=False)
    df["Rank"] = range(1, len(df) + 1)
    
    def get_tier(rank):
        if rank <= 100: return "Large Cap (1-100)"
        if rank <= 250: return "Mid Cap (101-250)"
        if rank <= 500: return "Small Cap (251-500)"
        return "Micro Cap (501+)"
        
    df["Cap Tier"] = df["Rank"].map(get_tier)
    df["Is_Positive"] = df["MOM_63"] > 0
    
    summary = df.groupby("Cap Tier")["Is_Positive"].agg(["count", "sum"])
    summary["% Positive"] = (summary["sum"] / summary["count"] * 100).round(1)
    summary = summary.rename(columns={"count": "Total Stocks", "sum": "Positive Mom Stocks"})
    
    order = ["Large Cap (1-100)", "Mid Cap (101-250)", "Small Cap (251-500)", "Micro Cap (501+)"]
    summary = summary.reindex(order).dropna()
    return summary

def validate_tradelog_integrity(transactions):
    """Replay all transactions chronologically and check no ticker ever goes negative.
    Returns (is_valid: bool, error_message: str)."""
    try:
        sorted_txs = sorted(transactions, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_txs = transactions
    holdings = {}
    for tx in sorted_txs:
        ticker = tx["ticker"]
        action = tx["action"].upper()
        qty = float(tx["quantity"])
        current = holdings.get(ticker, 0.0)
        if action == "BUY":
            holdings[ticker] = current + qty
        elif action == "SELL":
            if qty > current + 1e-9:  # small epsilon for float tolerance
                return False, (f"{ticker}: SELL of {qty:.0f} shares exceeds "
                               f"holding of {current:.0f} shares on {tx.get('date', '?')}")
            holdings[ticker] = current - qty
    return True, ""

def load_tradelog(universe_name):
    path = SCRIPT_DIR / f"{universe_name}_tradelog.json"
    if not path.exists():
        try:
            safe_write_json(path, [])
        except Exception as e:
            st.error(f"Error initializing empty tradelog: {e}")
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading tradelog: {e}")
        # Attempt recovery from .bak
        bak_path = path.with_suffix(".bak")
        if bak_path.exists():
            st.warning("Attempting recovery from backup file...")
            try:
                with open(bak_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

def save_tradelog(universe_name, tradelog):
    path = SCRIPT_DIR / f"{universe_name}_tradelog.json"
    try:
        safe_write_json(path, tradelog)
    except Exception as e:
        st.error(f"Error saving tradelog: {e}")

def append_regime_history(universe_name, score, detail):
    """Record today's regime score to a per-universe JSON file.
    One entry per calendar day — if run multiple times, the latest overwrites."""
    path = SCRIPT_DIR / f"{universe_name}_regime_history.json"
    try:
        history = json.load(open(path)) if path.exists() else []
    except Exception:
        history = []

    today_str = datetime.date.today().isoformat()
    entry = {
        "date":      today_str,
        "Composite": round(score, 4),
        "Breadth":   round(detail.get("breadth_score",  0.0), 4),
        "Momentum":  round(detail.get("momentum_score", 0.0), 4),
        "Dynamic N": detail.get("dynamic_n", 0),
    }

    # Update today's entry if it exists, otherwise append
    for i, h in enumerate(history):
        if h.get("date") == today_str:
            history[i] = entry
            break
    else:
        history.append(entry)

    try:
        safe_write_json(path, history)
    except Exception:
        pass  # Non-critical — never crash the dashboard over history writes

    return history

def get_latest_price(ticker, prices_df):
    if ticker in prices_df.index:
        series = prices_df.loc[ticker].dropna()
        if not series.empty:
            return float(series.iloc[-1])
    return 0.0

def calculate_holdings_and_pnl(transactions, latest_prices=None):
    try:
        sorted_txs = sorted(transactions, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_txs = transactions

    holdings = {}
    realized_pnl = 0.0
    realized_pnl_by_ticker = {}

    for tx in sorted_txs:
        ticker = tx["ticker"]
        action = tx["action"].upper()
        qty = float(tx["quantity"])
        price = float(tx["price"])
        tx_date = tx.get("date", "")
        if isinstance(tx_date, str):
            try:
                tx_date = datetime.date.fromisoformat(tx_date)
            except Exception:
                tx_date = datetime.date.today()

        if ticker not in holdings:
            holdings[ticker] = {
                "qty": 0.0,
                "avg_price": 0.0,
                "first_buy_date": None,
                "total_cost": 0.0
            }

        h = holdings[ticker]
        t_pnl = realized_pnl_by_ticker.get(ticker, 0.0)

        if action == "BUY":
            if h["qty"] == 0:
                h["first_buy_date"] = tx_date
            h["total_cost"] += qty * price
            h["qty"] += qty
            h["avg_price"] = h["total_cost"] / h["qty"]
        elif action == "SELL":
            if h["qty"] > 0:
                sell_qty = min(qty, h["qty"])
                pnl = sell_qty * (price - h["avg_price"])
                realized_pnl += pnl
                t_pnl += pnl
                h["qty"] -= sell_qty
                h["total_cost"] = h["qty"] * h["avg_price"]
                if h["qty"] == 0:
                    h["avg_price"] = 0.0
                    h["first_buy_date"] = None
            else:
                pass
        
        realized_pnl_by_ticker[ticker] = t_pnl

    active_holdings = {
        ticker: h for ticker, h in holdings.items() if h["qty"] > 0
    }

    unrealized_pnl = 0.0
    holdings_metrics = []
    
    for ticker, h in active_holdings.items():
        curr_price = h["avg_price"]
        if latest_prices is not None and ticker in latest_prices:
            curr_price = latest_prices[ticker]
        
        market_val = h["qty"] * curr_price
        u_pnl = market_val - h["total_cost"]
        unrealized_pnl += u_pnl
        
        u_pnl_pct = (u_pnl / h["total_cost"] * 100) if h["total_cost"] > 0 else 0.0
        
        holdings_metrics.append({
            "Ticker": ticker,
            "Qty": h["qty"],
            "Avg Price": h["avg_price"],
            "Current Price": curr_price,
            "Cost Value": h["total_cost"],
            "Market Value": market_val,
            "Unrealized PnL": u_pnl,
            "Unrealized PnL %": u_pnl_pct,
            "First Buy Date": h["first_buy_date"]
        })

    return {
        "active_holdings": active_holdings,
        "holdings_metrics": holdings_metrics,
        "realized_pnl": realized_pnl,
        "realized_pnl_by_ticker": realized_pnl_by_ticker,
        "unrealized_pnl": unrealized_pnl
    }

def sync_to_positions_ledger(ledger_path, active_holdings):
    serialisable = {}
    for ticker, h in active_holdings.items():
        if h["qty"] > 0:
            entry_date_str = h["first_buy_date"]
            if isinstance(entry_date_str, (datetime.date, datetime.datetime)):
                entry_date_str = entry_date_str.isoformat()
            
            serialisable[ticker] = {
                "entry_date": entry_date_str,
                "entry_price": float(h["avg_price"])
            }
    
    try:
        safe_write_json(ledger_path, serialisable)
    except Exception as e:
        st.error(f"Error syncing to positions ledger: {e}")


# ── REGIME ENGINE (mirrors Sharpe.py exactly) ─────────────────────────────────
MIN_N               = 5
MAX_N               = 25
NEW_ENTRY_THRESHOLD = 0.40
EMA50_BAND          = 0.10
EMA_TREND_BAND      = 0.05
SIGNAL_WEIGHTS      = {"ema50": 0.35, "ema_trend": 0.25,
                       "breadth": 0.25, "momentum": 0.15}

def compute_regime_score(nifty_s, eligible_mask, composite_series):
    px = nifty_s.dropna()
    if len(px) < 200:
        return 0.5, {"regime_score": 0.5, "dynamic_n": 15, "allow_new": True,
                     "ema50_score": 0.5, "ema_trend_score": 0.5,
                     "breadth_score": 0.5, "momentum_score": 0.5}
    price  = px.iloc[-1]
    ema50  = px.ewm(span=50,  adjust=False).mean().iloc[-1]
    ema200 = px.ewm(span=200, adjust=False).mean().iloc[-1]
    ema50_score     = float(np.clip((price / ema50  - 1.0) / EMA50_BAND     + 0.5, 0.0, 1.0))
    ema_trend_score = float(np.clip((ema50  / ema200 - 1.0) / EMA_TREND_BAND + 0.5, 0.0, 1.0))
    total          = len(eligible_mask)
    elig           = int(eligible_mask.sum())
    breadth_score  = elig / total if total > 0 else 0.5
    pos_mom        = int((composite_series[eligible_mask] > 1.5).sum())
    momentum_score = pos_mom / max(1, elig)
    score = (ema50_score     * SIGNAL_WEIGHTS["ema50"]     +
             ema_trend_score * SIGNAL_WEIGHTS["ema_trend"] +
             breadth_score   * SIGNAL_WEIGHTS["breadth"]   +
             momentum_score  * SIGNAL_WEIGHTS["momentum"])
    dyn_n = int(MIN_N + score * (MAX_N - MIN_N))
    return score, {"regime_score": round(score, 3),
                   "dynamic_n": dyn_n,
                   "allow_new": score >= NEW_ENTRY_THRESHOLD,
                   "ema50_score": round(ema50_score, 3),
                   "ema_trend_score": round(ema_trend_score, 3),
                   "breadth_score": round(breadth_score, 3),
                   "momentum_score": round(momentum_score, 3)}

# ── CONFIGURATION (session-state driven, rendered in Config tab) ──────────────
# Scan available xlsx files first (needed to set the default)
_cfg_files = sorted([f.name for f in SCRIPT_DIR.glob("*.xlsx")
                     if not f.name.startswith("~")
                     and "ranking" not in f.name.lower()])
_cfg_preferred = next((f for f in ["N750_updated.xlsx", "N750.xlsx"] if f in _cfg_files), None)

# Initialise session-state defaults on first run
if "cfg_file" not in st.session_state:
    st.session_state.cfg_file = _cfg_preferred if _cfg_preferred else (_cfg_files[0] if _cfg_files else "")
if "cfg_capital" not in st.session_state:
    st.session_state.cfg_capital = 1_500_000
if "cfg_max_wt_pct" not in st.session_state:
    st.session_state.cfg_max_wt_pct = 5

# Derive runtime values from session state
selected_file = st.session_state.cfg_file
input_path    = str(SCRIPT_DIR / selected_file)
universe      = selected_file.replace("_updated.xlsx", "").replace(".xlsx", "")
ledger_candidates = [
    SCRIPT_DIR / f"{universe}_positions_ledger.json",
    SCRIPT_DIR / "positions_ledger.json",
]
LEDGER_FILE = next((str(p) for p in ledger_candidates if p.exists()),
                   str(ledger_candidates[0]))
capital    = st.session_state.cfg_capital
max_wt_pct = st.session_state.cfg_max_wt_pct
max_wt     = max_wt_pct / 100.0

# ── CONFIG ────────────────────────────────────────────────────────────────────
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
WINDOWS      = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily    = RFR_ANNUAL / TRADING_DAYS
TODAY        = datetime.date.today()

# ── CACHED LOAD & COMPUTE ─────────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading price data...")
def load_data(filepath):
    return ml.load_prices(filepath)

@st.cache_data(show_spinner="Computing Sharpe rankings...")
def compute_all(_prices_df, _nifty_series, _stock_tickers):
    sharpe_df, z_df = ml.compute_sharpe(
        _prices_df, _stock_tickers, WINDOWS, rfr_daily, TRADING_DAYS)
    ret_df           = ml.compute_returns(_prices_df, _stock_tickers)
    pct_52h          = ml.compute_pct_from_52h(_prices_df, _stock_tickers)
    resmom_df, rs_z  = ml.compute_residual_momentum(
        _prices_df, _stock_tickers, _nifty_series, WINDOWS, TRADING_DAYS)

    result = z_df.join(sharpe_df.rename(columns={l: f"S_{l}" for l in WINDOWS}))
    for col in ["COMPOSITE", "SHARPE_3"]:
        result[col] = result[col].map(ml.normalise_composite)
    result["SHARPE_ALL"] = result["COMPOSITE"]
    result["PCT_FROM_52H"] = pct_52h

    eligible = result["PCT_FROM_52H"] >= -25
    result["RANK"] = np.nan
    result.loc[eligible, "RANK"] = (
        result.loc[eligible, "COMPOSITE"]
        .rank(ascending=False, method="first", na_option="bottom"))
    result = result.sort_values(["RANK", "COMPOSITE"], ascending=[True, False])
    result = result.join(ret_df).join(resmom_df).join(rs_z)

    # Dynamic Regime Score
    score, detail = compute_regime_score(
        _nifty_series, eligible, result["COMPOSITE"])
    return result, score, detail

def compute_weights(result, dynamic_n, capital_val, max_weight):
    top_tickers = result.head(dynamic_n).index.tolist()
    raw_w = {}
    for t in top_tickers:
        comp = result.loc[t, "COMPOSITE"]
        px   = prices_df.loc[t].dropna()
        if len(px) > 10:
            vols = []
            for w in [252, 189, 126, 63]:
                pw = px.iloc[-w:] if len(px) >= w else px
                lr = np.diff(np.log(pw.values))
                if len(lr) > 5: vols.append(np.std(lr, ddof=1) * np.sqrt(252))
            raw_w[t] = comp / np.mean(vols) if vols and np.mean(vols) > 0 else comp
        else:
            raw_w[t] = comp
    total = sum(raw_w.values())
    weights = {}
    for t in top_tickers:
        nw = raw_w[t] / total if total > 0 else 1.0 / len(top_tickers)
        weights[t] = min(max_weight, nw)
    cash_wt = max(0.0, 1.0 - sum(weights.values()))
    return weights, cash_wt

def load_ledger(path):
    p = Path(path)
    if not p.exists(): return {}
    with open(p) as f: raw = json.load(f)
    ledger = {}
    for t, rec in raw.items():
        try:
            ledger[t] = {"entry_date":  datetime.date.fromisoformat(rec["entry_date"]),
                         "entry_price": float(rec["entry_price"])}
        except (KeyError, ValueError): pass
    return ledger

# ── TITLE ─────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#1F4E79; margin-bottom:0;'>📊 Sharpe Momentum Strategy</h1>"
    "<p style='color:#6B7A8D; margin-top:4px;'>Rank &rarr; Filter &rarr; Size &rarr; Allocate"
    " &nbsp;|&nbsp; Dynamic Regime Engine</p>",
    unsafe_allow_html=True)

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
if not Path(input_path).exists():
    st.error(f"Data file not found: `{input_path}`"); st.stop()

try:
    prices_df, nifty_series, stock_tickers, dates = load_data(input_path)
except Exception as e:
    st.error(f"Error loading data: {e}"); st.stop()

try:
    result, regime_score, regime_detail = compute_all(prices_df, nifty_series, stock_tickers)
except Exception as e:
    st.error(f"Error computing rankings: {e}"); st.stop()

# Record today's regime score and load full history for trend chart
regime_history = append_regime_history(universe, regime_score, regime_detail)

dynamic_n = regime_detail["dynamic_n"]
allow_new = regime_detail["allow_new"]
weights, cash_wt = compute_weights(result, dynamic_n, capital, max_wt)

# Load trade log and synchronize the positions ledger
latest_prices = {ticker: get_latest_price(ticker, prices_df) for ticker in stock_tickers}
if "live_prices" in st.session_state:
    for tk, price in st.session_state.live_prices.items():
        if tk in latest_prices:
            latest_prices[tk] = price

tradelog = load_tradelog(universe)
tradelog_result = calculate_holdings_and_pnl(tradelog, latest_prices)
active_holdings = tradelog_result["active_holdings"]
holdings_metrics = tradelog_result["holdings_metrics"]
realized_pnl = tradelog_result["realized_pnl"]
unrealized_pnl = tradelog_result["unrealized_pnl"]

# Sync tradelog to positions ledger on startup to ensure consistency
sync_to_positions_ledger(LEDGER_FILE, active_holdings)
ledger = load_ledger(LEDGER_FILE)

# ── REGIME HEADER ─────────────────────────────────────────────────────────────
if regime_score >= 0.65:
    badge_cls, emoji = "score-high", "🟢"
elif regime_score >= NEW_ENTRY_THRESHOLD:
    badge_cls, emoji = "score-mid",  "🟡"
else:
    badge_cls, emoji = "score-low",  "🔴"

entry_label = "NEW BUYS ALLOWED" if allow_new else f"NO NEW BUYS (< {NEW_ENTRY_THRESHOLD})"

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.markdown(
        f"<div style='text-align:center; padding:10px;'>"
        f"<span class='{badge_cls}' style='font-size:17px;'>"
        f"{emoji} Score: {regime_score:.2f}</span></div>",
        unsafe_allow_html=True)
with c2:
    st.markdown(
        f"<div style='background-color:#F0F2F6; border-radius:8px; padding:14px 16px;'>"
        f"<p style='font-size:14px; color:#6B7A8D; margin:0 0 4px 0;'>Dynamic N</p>"
        f"<p style='font-size:20px; font-weight:700; margin:0;'>{dynamic_n} stocks</p>"
        f"</div>",
        unsafe_allow_html=True)
with c3:
    st.markdown(
        f"<div style='background-color:#F0F2F6; border-radius:8px; padding:14px 16px;'>"
        f"<p style='font-size:14px; color:#6B7A8D; margin:0 0 4px 0;'>Entry Gate</p>"
        f"<p style='font-size:20px; font-weight:700; margin:0;'>{entry_label}</p>"
        f"</div>",
        unsafe_allow_html=True)
with c4:
    eligible_count = (result['PCT_FROM_52H'] >= -25).sum()
    st.markdown(
        f"<div style='background-color:#F0F2F6; border-radius:8px; padding:14px 16px;'>"
        f"<p style='font-size:14px; color:#6B7A8D; margin:0 0 4px 0;'>Eligible (52H)</p>"
        f"<p style='font-size:20px; font-weight:700; margin:0;'>{eligible_count}</p>"
        f"</div>",
        unsafe_allow_html=True)
with c5:
    st.markdown(
        f"<div style='background-color:#F0F2F6; border-radius:8px; padding:14px 16px;'>"
        f"<p style='font-size:14px; color:#6B7A8D; margin:0 0 4px 0;'>Data Range</p>"
        f"<p style='font-size:20px; font-weight:700; margin:0;'>{dates[0].strftime('%d-%b-%y')} to {dates[-1].strftime('%d-%b-%y')}</p>"
        f"</div>",
        unsafe_allow_html=True)

# Signal breakdown + trend
with st.expander("📡 Regime Score Breakdown", expanded=False):
    live_vix = get_live_vix()
    vix_str = f"{live_vix:.2f}" if live_vix else "N/A"
    
    sc1, sc2, sc3, sc4, sc5 = st.columns(5)
    with sc1: st.metric("EMA50 Distance (35%)",    f"{regime_detail['ema50_score']:.3f}")
    with sc2: st.metric("EMA Trend 50v200 (25%)",  f"{regime_detail['ema_trend_score']:.3f}")
    with sc3: st.metric("52H Breadth (25%)",       f"{regime_detail['breadth_score']:.3f}")
    with sc4: st.metric("Momentum Breadth (15%)",  f"{regime_detail['momentum_score']:.3f}")
    with sc5: st.metric("Live India VIX",          vix_str)
    st.progress(regime_score, text=f"Composite Regime Score: {regime_score:.3f}")

    st.markdown("---")
    st.markdown("**📈 Regime Score Trend**")
    if len(regime_history) >= 2:
        hist_df = pd.DataFrame(regime_history)
        hist_df["date"] = pd.to_datetime(hist_df["date"])
        hist_df = hist_df.set_index("date").sort_index()
        chart_df = hist_df[["Composite", "Breadth", "Momentum"]].copy()
        chart_df["Entry Threshold"] = NEW_ENTRY_THRESHOLD
        st.line_chart(chart_df, height=260)
        st.caption(
            f"📊 {len(regime_history)} day(s) recorded  |  "
            f"Latest: {hist_df.index[-1].strftime('%d-%b-%Y')}  |  "
            f"Entry Threshold line = {NEW_ENTRY_THRESHOLD}")
    else:
        st.caption("📅 Trend chart appears after 2+ days of recorded data. Come back tomorrow!")

# Cap Tier breakdown
with st.expander("📊 Market Cap Momentum Breakdown", expanded=False):
    st.markdown("Percentage of stocks with a positive 63-day return across each Cap Tier. *(Cached daily)*")
    try:
        with st.spinner("Crunching Market Caps from Yahoo Finance (takes ~15 seconds on first run)..."):
            tier_summary = compute_cap_tier_momentum(prices_df, stock_tickers)
        st.dataframe(tier_summary, use_container_width=True)
    except Exception as e:
        st.error(f"Could not load Market Cap data: {e}")

st.divider()

# ── TABS ──────────────────────────────────────────────────────────────────────
tab_top, tab_exits, tab_tradelog, tab_calcs, tab_config = st.tabs([
    "📊 Top 25 Rankings",
    "🚨 Exit Monitor",
    "📝 Tradelog & MTM",
    "📋 Full Rankings",
    "⚙️ Configuration"])

# ── TAB 1: TOP 25 RANKINGS ────────────────────────────────────────────────────
with tab_top:
    st.markdown(
        f"## 📊 Top 25 Rankings  —  "
        f"Regime Score: **{regime_score:.2f}**  |  "
        f"Dynamic N (Strategy): **{dynamic_n}**")

    DISPLAY_N = 25
    held_tickers = set(active_holdings.keys())
    rows = []

    for ticker, row in result.head(DISPLAY_N).iterrows():
        ltp = latest_prices.get(ticker, 0.0)

        # Compute annualised volatility — mean across 4 windows (same as weight-sizing engine)
        mean_vol = None
        if ticker in prices_df.index:
            px = prices_df.loc[ticker].dropna()
            if len(px) > 10:
                vols = []
                for w in [252, 189, 126, 63]:
                    pw = px.iloc[-w:] if len(px) >= w else px
                    lr = np.diff(np.log(pw.values))
                    if len(lr) > 5:
                        vols.append(np.std(lr, ddof=1) * np.sqrt(252))
                if vols:
                    mean_vol = float(np.mean(vols))

        comp = row["COMPOSITE"] if pd.notna(row["COMPOSITE"]) else None
        vol_adj = round(comp / mean_vol, 3) if (comp is not None and mean_vol and mean_vol > 0) else None

        rows.append({
            "Rank":          int(row["RANK"]) if pd.notna(row["RANK"]) else None,
            "Ticker":        ticker,
            "Composite":     round(comp, 3) if comp is not None else None,
            "Res Mom":       round(row["RES_MOM"], 3) if pd.notna(row.get("RES_MOM")) else None,
            "Volatility %":  round(mean_vol * 100, 1) if mean_vol is not None else None,
            "Vol-Adj Score": vol_adj,
            "LTP":           round(ltp, 2) if ltp > 0 else None,
        })

    top25_df = pd.DataFrame(rows)

    def style_top25(row):
        if row["Ticker"] in held_tickers:
            return ["background-color:#FFFDE7;"] * len(row)   # light yellow — currently held
        return ["background-color:#FFFFFF;"] * len(row)        # white

    st.dataframe(
        top25_df.style.apply(style_top25, axis=1).format(
            {"Rank":          "{:.0f}",
             "Composite":     "{:.3f}",
             "Res Mom":       "{:.3f}",
             "Volatility %":  "{:.1f}%",
             "Vol-Adj Score": "{:.3f}",
             "LTP":           "Rs{:,.2f}"}, na_rep="—"),
        use_container_width=True, hide_index=True,
        height=min(950, (DISPLAY_N + 2) * 36))

    st.caption("🟡 Yellow rows = currently held positions  |  White rows = not yet in portfolio")

    st.divider()
    
    # Calculate exact portfolio valuation based on active holdings
    actual_cost_basis = sum(h["Cost Value"] for h in holdings_metrics)
    
    # Cash = Starting Capital - What we spent
    actual_cash_liquid = capital - actual_cost_basis
    actual_cash_liquid = max(0.0, actual_cash_liquid) # Prevent negative cash display
    
    # Weights strictly based on original configuration capital
    actual_equity_wt = (actual_cost_basis / capital) if capital > 0 else 0.0
    actual_cash_wt = (actual_cash_liquid / capital) if capital > 0 else 0.0

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1: st.metric("Equity Deployed",  f"Rs {actual_cost_basis:,.0f}")
    with mc2: st.metric("Equity Weight",    f"{actual_equity_wt:.1%}")
    with mc3: st.metric("Cash (Liquid)",    f"Rs {actual_cash_liquid:,.0f}")
    with mc4: st.metric("Cash Weight",      f"{actual_cash_wt:.1%}")


# ── TAB 2: EXIT MONITOR ───────────────────────────────────────────────────────
with tab_exits:
    st.markdown("## 🚨 Exit Evaluation")

    if not ledger:
        st.info(f"No open positions found in ledger `{Path(LEDGER_FILE).name}`. "
                "Nothing to evaluate.")
    else:
        exit_rows = []
        for ticker, rec in ledger.items():
            held     = (TODAY - rec["entry_date"]).days
            rank_val = result.loc[ticker, "RANK"]       if ticker in result.index else np.nan
            pct52    = result.loc[ticker, "PCT_FROM_52H"] if ticker in result.index else np.nan

            if pd.isna(rank_val) or (pd.notna(pct52) and pct52 < -25):
                trigger = "52H_BREACH";  action = "⚠️ SELL IMMEDIATELY"
            elif pd.notna(rank_val) and rank_val > 40 and held >= 28:
                trigger = "RANK_EXIT";   action = "🔻 SELL (rank dropped)"
            elif pd.notna(rank_val) and rank_val > 40 and held < 28:
                trigger = "HOLD_LOCK";   action = f"🔒 Locked ({held}/28d)"
            else:
                trigger = "HEALTHY";     action = "✅ HOLD"

            exit_rows.append({
                "Ticker":     ticker,
                "Action":     action,
                "Trigger":    trigger,
                "Rank":       int(rank_val) if pd.notna(rank_val) else None,
                "52H%":       round(pct52, 1) if pd.notna(pct52) else None,
                "Days Held":  held,
                "Entry Date": rec["entry_date"].isoformat(),
                "Entry Price":round(rec["entry_price"], 2),
            })

        exit_df = pd.DataFrame(exit_rows)
        breaches = exit_df[exit_df["Trigger"].isin(["52H_BREACH", "RANK_EXIT"])]

        if len(breaches) > 0:
            st.error(f"🚨 **{len(breaches)} EXIT SIGNAL(S) — Action Required!**")
        else:
            st.success(f"✅ All {len(ledger)} positions healthy. No exits triggered.")

        def style_exits(row):
            if "SELL IMMEDIATELY" in str(row["Action"]): return ["background-color:#FFEBEE; font-weight:bold;"] * len(row)
            if "SELL" in str(row["Action"]):             return ["background-color:#FFF3E0;"] * len(row)
            if "Locked"  in str(row["Action"]):          return ["background-color:#FFF8E1;"] * len(row)
            return ["background-color:#E8F5E9;"] * len(row)

        st.dataframe(exit_df.style.apply(style_exits, axis=1).format(
                         {"Rank": "{:.0f}"}, na_rep="—"),
                     use_container_width=True, hide_index=True)

        st.markdown(
            f"**Positions:** {len(ledger)}  |  "
            f"**52H Exits:** {len(exit_df[exit_df['Trigger']=='52H_BREACH'])}  |  "
            f"**Rank Exits:** {len(exit_df[exit_df['Trigger']=='RANK_EXIT'])}  |  "
            f"**Hold-Locked:** {len(exit_df[exit_df['Trigger']=='HOLD_LOCK'])}  |  "
            f"**Healthy:** {len(exit_df[exit_df['Trigger']=='HEALTHY'])}")

# ── TAB 3: TRADELOG & MTM ─────────────────────────────────────────────────────
with tab_tradelog:
    st.markdown("## 📝 Tradelog & Real-time MTM")

    # 1. Metric Cards
    total_invested_val = sum(h["Cost Value"] for h in holdings_metrics)
    total_market_val = sum(h["Market Value"] for h in holdings_metrics)
    total_unrealized_pnl = total_market_val - total_invested_val
    total_unrealized_pnl_pct = (total_unrealized_pnl / total_invested_val * 100) if total_invested_val > 0 else 0.0

    tc1, tc2, tc3, tc4 = st.columns(4)
    with tc1:
        st.metric("Total Invested (Rs)", f"Rs {total_invested_val:,.2f}")
    with tc2:
        st.metric("Current Market Value (Rs)", f"Rs {total_market_val:,.2f}")
    with tc3:
        st.metric("Unrealized PnL (MTM)", f"Rs {total_unrealized_pnl:,.2f}", delta=f"{total_unrealized_pnl_pct:+.2f}%")
    with tc4:
        st.metric("Realized PnL (Rs)", f"Rs {realized_pnl:,.2f}")

    st.divider()

    # 2. Active Holdings Table
    hc1, hc2 = st.columns([0.7, 0.3])
    with hc1: 
        st.markdown("### 💼 Active Holdings")
    with hc2:
        if st.button("🔄 Refresh Live Market Prices", use_container_width=True):
            live_prices = {}
            if holdings_metrics:
                with st.spinner("Fetching latest prices from Yahoo Finance..."):
                    def fetch_price(tkr):
                        try:
                            return tkr, yf.Ticker(f"{tkr}.NS").fast_info.last_price
                        except:
                            try:
                                return tkr, yf.Ticker(f"{tkr}.BO").fast_info.last_price
                            except:
                                return tkr, None
                    
                    with ThreadPoolExecutor(max_workers=20) as exe:
                        results = exe.map(fetch_price, [h["Ticker"] for h in holdings_metrics])
                        for tkr, price in results:
                            if price: live_prices[tkr] = price
            
            if live_prices:
                st.session_state.live_prices = live_prices
                st.rerun()

    # Initialize state keys for tracking row selection and dropdown state
    if "last_selected_row" not in st.session_state:
        st.session_state.last_selected_row = None
    if "last_seen_ticker" not in st.session_state:
        st.session_state.last_seen_ticker = None

    if not holdings_metrics:
        st.info("No active holdings found. Log a BUY trade below to open a position.")
    else:
        holdings_df = pd.DataFrame(holdings_metrics)
        cols_order = ["Ticker", "Qty", "Avg Price", "Current Price", "Cost Value", "Market Value", "Unrealized PnL", "Unrealized PnL %", "First Buy Date"]
        holdings_df = holdings_df[cols_order].sort_values(by="Unrealized PnL", ascending=False)

        def style_holdings(row):
            pnl = row["Unrealized PnL"]
            if pnl > 0:
                return ["background-color:#E8F5E9;"] * len(row)
            elif pnl < 0:
                return ["background-color:#FFEBEE;"] * len(row)
            return [""] * len(row)

        event = st.dataframe(
            holdings_df.style.apply(style_holdings, axis=1).format(
                {"Qty": "{:,.0f}", "Avg Price": "Rs {:,.2f}", "Current Price": "Rs {:,.2f}",
                 "Cost Value": "Rs {:,.2f}", "Market Value": "Rs {:,.2f}",
                 "Unrealized PnL": "Rs {:,.2f}", "Unrealized PnL %": "{:+.2f}%",
                 "First Buy Date": lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x)}, na_rep="—"
            ),
            use_container_width=True, hide_index=True,
            on_select="rerun",
            selection_mode="single-row"
        )

        # Extract selected row details
        rows = []
        if event and hasattr(event, "selection"):
            if hasattr(event.selection, "rows"):
                rows = event.selection.rows
            elif isinstance(event.selection, dict):
                rows = event.selection.get("rows", [])

        if rows:
            selected_row_idx = rows[0]
            if st.session_state.last_selected_row != selected_row_idx:
                selected_row = holdings_df.iloc[selected_row_idx]
                st.session_state.tradelog_select_ticker = selected_row["Ticker"]
                st.session_state.tradelog_qty = int(selected_row["Qty"])
                st.session_state.tradelog_price = float(selected_row["Current Price"])
                st.session_state.last_selected_row = selected_row_idx
                st.session_state.last_seen_ticker = selected_row["Ticker"]
        else:
            st.session_state.last_selected_row = None

    st.divider()

    # 3. Log Trade Form & Transaction History
    st.markdown("### ➕ Log New Transaction")
    
    # Deferred reset: apply pending resets BEFORE widgets are instantiated
    if st.session_state.get("_pending_trade_reset"):
        st.session_state.tradelog_qty = 10
        if "tradelog_select_ticker" in st.session_state:
            st.session_state.tradelog_price = float(
                get_latest_price(st.session_state.tradelog_select_ticker, prices_df))
        st.session_state.last_selected_row = None
        del st.session_state["_pending_trade_reset"]

    # Initialize inputs session state if not set
    if "tradelog_select_ticker" not in st.session_state:
        st.session_state.tradelog_select_ticker = stock_tickers[0]
    if "tradelog_qty" not in st.session_state:
        st.session_state.tradelog_qty = 10
    if "tradelog_price" not in st.session_state:
        selected_ticker = st.session_state.tradelog_select_ticker
        st.session_state.tradelog_price = float(get_latest_price(selected_ticker, prices_df))
    
    with st.form(key="add_trade_form", clear_on_submit=True):
        col_ticker, col_act, col_dt = st.columns([2, 1, 1])
        
        with col_ticker:
            selected_ticker = st.selectbox(
                "Select Ticker", 
                options=stock_tickers, 
                index=stock_tickers.index(st.session_state.tradelog_select_ticker)
                    if st.session_state.tradelog_select_ticker in stock_tickers else 0,
                help="Select stock from the universe to trade"
            )
        with col_act:
            trade_action = st.radio("Action", ["BUY", "SELL"], horizontal=True)
        with col_dt:
            trade_date = st.date_input("Transaction Date", datetime.date.today())
        
        col_q, col_p, col_spacer = st.columns([1, 1, 2])
        with col_q:
            trade_qty = st.number_input("Quantity", min_value=1, step=1, value=st.session_state.tradelog_qty)
        with col_p:
            trade_price = st.number_input("Price per Share (INR)", min_value=0.01, step=0.01, value=float(st.session_state.tradelog_price))
            
        submit_trade = st.form_submit_button("💾 Record Transaction", use_container_width=True)
        
        if submit_trade:
            # Update the ticker in session state from the form selection
            st.session_state.tradelog_select_ticker = selected_ticker
            
            # Strict validation: block SELL exceeding holdings
            curr_qty = active_holdings.get(selected_ticker, {}).get("qty", 0.0)
            if trade_action == "SELL" and trade_qty > curr_qty:
                st.error(
                    f"❌ Cannot SELL {trade_qty} shares of {selected_ticker} — "
                    f"you only hold {curr_qty:.0f} shares. Trade not recorded."
                )
            else:
                # Build candidate tradelog and validate full integrity
                new_trade = {
                    "id": str(uuid.uuid4()),
                    "date": trade_date.isoformat(),
                    "timestamp": datetime.datetime.now().isoformat(),
                    "ticker": selected_ticker,
                    "action": trade_action,
                    "quantity": int(trade_qty),
                    "price": float(trade_price)
                }
                
                updated_tradelog = tradelog + [new_trade]
                is_valid, err_msg = validate_tradelog_integrity(updated_tradelog)
                
                if not is_valid:
                    st.error(f"❌ Trade rejected — would cause inconsistent state: {err_msg}")
                else:
                    save_tradelog(universe, updated_tradelog)
                    
                    # Recalculate holdings & sync positions ledger
                    new_calc = calculate_holdings_and_pnl(updated_tradelog, latest_prices)
                    sync_to_positions_ledger(LEDGER_FILE, new_calc["active_holdings"])
                    
                    # Set deferred reset flag — will be applied on next rerun BEFORE widgets
                    st.session_state._pending_trade_reset = True
                    
                    st.success(f"Successfully recorded {trade_action} {trade_qty} shares of {selected_ticker} @ Rs {trade_price:.2f}!")
                    st.rerun()

    st.divider()

    # 4. Chronological Transaction Table & Deletion
    st.markdown("### 🕒 Transaction History & Management")
    if not tradelog:
        st.info("No transactions logged yet.")
    else:
        display_tx = []
        for tx in reversed(tradelog):
            display_tx.append({
                "Date": tx["date"],
                "Ticker": tx["ticker"],
                "Action": tx["action"],
                "Quantity": tx["quantity"],
                "Price": tx["price"],
                "Total Value": tx["quantity"] * tx["price"]
            })
            
        display_df = pd.DataFrame(display_tx)
        
        def style_txs(row):
            if row["Action"] == "BUY":
                return ["background-color:#E8F5E9;"] * len(row)
            elif row["Action"] == "SELL":
                return ["background-color:#FFEBEE;"] * len(row)
            return [""] * len(row)
            
        st.dataframe(
            display_df.style.apply(style_txs, axis=1).format(
                {"Quantity": "{:,.0f}", "Price": "Rs {:,.2f}", "Total Value": "Rs {:,.2f}"}
            ),
            use_container_width=True, hide_index=True
        )
        st.markdown("#### ✏️ Edit Existing Transaction")
        tx_options_edit = [
            f"{tx['date']} | {tx['action']} {tx['quantity']} {tx['ticker']} @ Rs{tx['price']} (ID: {tx['id']})"
            for tx in reversed(tradelog)
        ]
        selected_choice = st.selectbox(
            "Select transaction to edit (useful for adjusting entry prices/quantities)",
            options=["-- Select Transaction to Edit --"] + tx_options_edit,
            key="edit_tx_selectbox"
        )
        
        if selected_choice != "-- Select Transaction to Edit --":
            parts = selected_choice.split("(ID: ")
            edit_id = parts[1].rstrip(")") if len(parts) > 1 else None
            target_tx = next((tx for tx in tradelog if tx["id"] == edit_id), None)
            
            if target_tx:
                st.info(f"Editing transaction ID: {target_tx['id']}")
                with st.form(key="edit_tx_form", clear_on_submit=False):
                    col_edit_ticker, col_edit_act, col_edit_dt, col_edit_qty, col_edit_pr = st.columns(5)
                    
                    with col_edit_ticker:
                        try:
                            ticker_idx = stock_tickers.index(target_tx["ticker"])
                        except ValueError:
                            ticker_idx = 0
                        edit_ticker = st.selectbox("Ticker", options=stock_tickers, index=ticker_idx)
                    with col_edit_act:
                        edit_action = st.radio("Action", ["BUY", "SELL"], index=0 if target_tx["action"].upper() == "BUY" else 1, horizontal=True)
                    with col_edit_dt:
                        try:
                            dt_val = datetime.date.fromisoformat(target_tx["date"])
                        except ValueError:
                            dt_val = datetime.date.today()
                        edit_date = st.date_input("Date", dt_val)
                    with col_edit_qty:
                        edit_qty = st.number_input("Quantity", min_value=1, step=1, value=int(target_tx["quantity"]))
                    with col_edit_pr:
                        edit_price = st.number_input("Price (INR)", min_value=0.01, step=0.01, value=float(target_tx["price"]))
                        
                    submit_edit = st.form_submit_button("💾 Save Changes", use_container_width=True)
                    
                    if submit_edit:
                        idx_to_update = next((i for i, tx in enumerate(tradelog) if tx["id"] == edit_id), None)
                        if idx_to_update is not None:
                            # Build candidate tradelog with the edit applied
                            candidate_tradelog = [tx.copy() for tx in tradelog]
                            candidate_tradelog[idx_to_update]["ticker"] = edit_ticker
                            candidate_tradelog[idx_to_update]["action"] = edit_action
                            candidate_tradelog[idx_to_update]["date"] = edit_date.isoformat()
                            candidate_tradelog[idx_to_update]["quantity"] = int(edit_qty)
                            candidate_tradelog[idx_to_update]["price"] = float(edit_price)
                            
                            # Validate integrity of the resulting tradelog
                            is_valid, err_msg = validate_tradelog_integrity(candidate_tradelog)
                            if not is_valid:
                                st.error(
                                    f"❌ Edit rejected — would cause inconsistent holdings: {err_msg}. "
                                    f"The original transaction has NOT been modified."
                                )
                            else:
                                save_tradelog(universe, candidate_tradelog)
                                
                                new_calc = calculate_holdings_and_pnl(candidate_tradelog, latest_prices)
                                sync_to_positions_ledger(LEDGER_FILE, new_calc["active_holdings"])
                                
                                st.success("Successfully updated transaction and synced positions ledger!")
                                st.rerun()

        st.divider()
        
        st.markdown("#### 🗑️ Delete Transactions")
        tx_options = [
            f"{tx['date']} | {tx['action']} {tx['quantity']} {tx['ticker']} @ Rs{tx['price']} (ID: {tx['id']})"
            for tx in reversed(tradelog)
        ]
        selected_to_delete = st.multiselect(
            "Select transactions to delete (useful for fixing entries)",
            options=tx_options,
            help="Select one or more transactions to permanently delete"
        )
        
        if selected_to_delete:
            if st.button("🗑️ Delete Selected", type="secondary", use_container_width=True):
                ids_to_delete = []
                for choice in selected_to_delete:
                    parts = choice.split("(ID: ")
                    if len(parts) > 1:
                        ids_to_delete.append(parts[1].rstrip(")"))
                
                candidate_tradelog = [tx for tx in tradelog if tx["id"] not in ids_to_delete]
                
                # Validate integrity of the resulting tradelog
                is_valid, err_msg = validate_tradelog_integrity(candidate_tradelog)
                if not is_valid:
                    st.error(
                        f"❌ Deletion rejected — removing these transaction(s) would cause "
                        f"inconsistent holdings: {err_msg}. No transactions were deleted."
                    )
                else:
                    save_tradelog(universe, candidate_tradelog)
                    
                    new_calc = calculate_holdings_and_pnl(candidate_tradelog, latest_prices)
                    sync_to_positions_ledger(LEDGER_FILE, new_calc["active_holdings"])
                    
                    st.success(f"Deleted {len(ids_to_delete)} transaction(s) and synchronized positions ledger!")
                    st.rerun()

# ── TAB 4: FULL RANKINGS ──────────────────────────────────────────────────────
with tab_calcs:
    st.markdown("## 📋 Full Universe Rankings")

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        filter_elig = st.selectbox("Eligibility",
                                    ["All", "Eligible only", "Disqualified only"])
    with fc2:
        top_n_show = st.slider("Show top N", 10, len(result),
                                min(100, len(result)), 10)
    with fc3:
        sort_col = st.selectbox("Sort by",
                                 ["RANK", "COMPOSITE", "RES_MOM", "PCT_FROM_52H"])

    display_cols = ["RANK", "COMPOSITE", "SHARPE_3"]
    for lbl in WINDOWS:
        for pfx in ["S_", "Z_"]:
            c = f"{pfx}{lbl}"
            if c in result.columns: display_cols.append(c)
    for c in ["RES_MOM", "1M%", "3M%", "12M%", "PCT_FROM_52H"]:
        if c in result.columns: display_cols.append(c)

    calcs_df = result[[c for c in display_cols if c in result.columns]].copy()
    calcs_df.index.name = "TICKER"
    calcs_df = calcs_df.reset_index()

    if filter_elig == "Eligible only":
        calcs_df = calcs_df[calcs_df["PCT_FROM_52H"] >= -25]
    elif filter_elig == "Disqualified only":
        calcs_df = calcs_df[calcs_df["PCT_FROM_52H"] < -25]

    calcs_df = calcs_df.sort_values(
        sort_col, ascending=(sort_col == "RANK"),
        na_position="last").head(top_n_show)

    fmt = {c: "{:.3f}" for c in calcs_df.columns
           if c not in ["RANK", "TICKER"]}
    fmt["PCT_FROM_52H"] = "{:.1f}"
    for c in ["1M%", "3M%", "12M%"]:
        if c in fmt: fmt[c] = "{:.1f}"

    def style_calcs(row):
        if pd.notna(row.get("PCT_FROM_52H")) and row["PCT_FROM_52H"] < -25:
            return ["background-color:#FFF8F8; color:#B0B0B0;"] * len(row)
        try:
            if pd.notna(row.get("RANK")) and int(row["RANK"]) <= dynamic_n:
                return ["background-color:#E8F5E9;"] * len(row)
        except (ValueError, TypeError): pass
        return [""] * len(row)

    st.dataframe(
        calcs_df.style.apply(style_calcs, axis=1).format(fmt, na_rep="—"),
        use_container_width=True, hide_index=True, height=600)

    st.caption(
        f"Universe: {len(stock_tickers)}  |  "
        f"Eligible: {(result['PCT_FROM_52H'] >= -25).sum()}  |  "
        f"Disqualified: {(result['PCT_FROM_52H'] < -25).sum()}  |  "
        f"Regime N (green rows): {dynamic_n}")

# ── TAB 5: CONFIGURATION ─────────────────────────────────────────────────────
with tab_config:
    st.markdown("## ⚙️ Configuration")

    st.markdown("#### 📁 Data Source")
    st.selectbox("Input File", _cfg_files, key="cfg_file")

    st.divider()
    st.markdown("#### 💰 Capital & Sizing")
    st.number_input("Portfolio Capital (INR)", min_value=100_000,
                    step=100_000, format="%d", key="cfg_capital")
    st.slider("Max Position Weight (%)", min_value=3, max_value=10,
              step=1, format="%d%%", key="cfg_max_wt_pct")

    st.divider()
    st.markdown("#### 📋 Strategy Parameters (Read-only)")
    params = {
        "RFR":          "7.0%",
        "Windows":      "12M / 9M / 6M / 3M",
        "Top N":        f"Dynamic ({MIN_N}–{MAX_N})",
        "Entry Gate":   f"Regime score >= {NEW_ENTRY_THRESHOLD}",
        "Hold Lock":    "28 days",
        "52H Filter":   ">= -25%",
        "Rank Buffer":  "40",
        "Cash Yield":   "6% p.a.",
        "Ledger File":  Path(LEDGER_FILE).name,
    }
    for k, v in params.items():
        st.markdown(f"**{k}:** `{v}`")

    st.divider()
    st.caption("Sharpe Momentum Strategy v3.0 — Dynamic Regime")
    st.info("Changes to Data Source, Capital, or Max Weight take effect immediately on the next rerun.")

# ── ACTIONS ───────────────────────────────────────────────────────────────────
st.divider()
st.markdown("## ⚡ Actions")
ac1, ac2 = st.columns(2)
with ac1:
    if st.button("🔁 Refresh Rankings", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with ac2:
    out_candidates = [
        SCRIPT_DIR / f"{universe}_rankings.xlsx",
        SCRIPT_DIR / "N750_rankings.xlsx",
        SCRIPT_DIR / "NSEAll_rankings.xlsx",
    ]
    out_path = next((p for p in out_candidates if p.exists()), None)
    if out_path:
        with open(out_path, "rb") as f:
            st.download_button(
                f"📥 Download Rankings Excel ({out_path.name})", f.read(),
                file_name=out_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
    else:
        st.info("Run `Sharpe.py` to generate the Excel output first.")
