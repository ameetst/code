"""
sharpe_dashboard.py
===================
Sharpe Momentum Strategy — Streamlit Dashboard (v3 — Dynamic Regime Engine)
Run:  streamlit run sharpe_dashboard.py
"""
import sys, json, datetime
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np

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

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    st.divider()

    st.markdown("#### 📁 Data Source")
    default_files = sorted([f.name for f in SCRIPT_DIR.glob("*.xlsx")
                            if not f.name.startswith("~")
                            and "ranking" not in f.name.lower()])
    preferred = next((f for f in ["N750_updated.xlsx", "N750.xlsx"] if f in default_files), None)
    default_idx = default_files.index(preferred) if preferred else 0
    selected_file = st.selectbox("Input File", default_files, index=default_idx)
    input_path = str(SCRIPT_DIR / selected_file)

    # Derive universe name and ledger path
    universe = selected_file.replace("_updated.xlsx", "").replace(".xlsx", "")
    ledger_candidates = [
        SCRIPT_DIR / f"{universe}_positions_ledger.json",
        SCRIPT_DIR / "positions_ledger.json",
    ]
    LEDGER_FILE = next((str(p) for p in ledger_candidates if p.exists()),
                       str(ledger_candidates[0]))

    st.divider()
    st.markdown("#### 💰 Capital & Sizing")
    capital = st.number_input("Portfolio Capital (INR)", value=2_000_000,
                               step=100_000, format="%d")
    max_wt = st.slider("Max Position Weight", 0.03, 0.10, 0.05, 0.01,
                        format="%.0f%%")

    st.divider()
    st.markdown("#### 📋 Strategy Parameters")
    st.markdown(f"**RFR:** `7.0%`")
    st.markdown(f"**Windows:** `12M / 9M / 6M / 3M`")
    st.markdown(f"**Top N:** `Dynamic ({MIN_N}–{MAX_N})`")
    st.markdown(f"**Entry Gate:** `Regime score >= {NEW_ENTRY_THRESHOLD}`")
    st.markdown(f"**Hold Lock:** `28 days`")
    st.markdown(f"**52H Filter:** `>= -25%`")
    st.markdown(f"**Rank Buffer:** `40`")
    st.markdown(f"**Cash Yield:** `6% p.a.`")
    st.markdown(f"**Ledger:** `{Path(LEDGER_FILE).name}`")

    st.divider()
    st.caption("Sharpe Momentum Strategy v3.0 — Dynamic Regime")

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

dynamic_n = regime_detail["dynamic_n"]
allow_new = regime_detail["allow_new"]
weights, cash_wt = compute_weights(result, dynamic_n, capital, max_wt)
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
with c2: st.metric("Dynamic N", f"{dynamic_n} stocks")
with c3: st.metric("Entry Gate", entry_label)
with c4: st.metric("Eligible (52H)", f"{(result['PCT_FROM_52H'] >= -25).sum()}")
with c5: st.metric("Data Range", f"{dates[0].strftime('%d-%b-%y')} to {dates[-1].strftime('%d-%b-%y')}")

# Signal breakdown
with st.expander("📡 Regime Score Breakdown", expanded=False):
    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1: st.metric("EMA50 Distance (35%)",    f"{regime_detail['ema50_score']:.3f}")
    with sc2: st.metric("EMA Trend 50v200 (25%)",  f"{regime_detail['ema_trend_score']:.3f}")
    with sc3: st.metric("52H Breadth (25%)",       f"{regime_detail['breadth_score']:.3f}")
    with sc4: st.metric("Momentum Breadth (15%)",  f"{regime_detail['momentum_score']:.3f}")
    st.progress(regime_score, text=f"Composite Regime Score: {regime_score:.3f}")

st.divider()

# ── TABS ──────────────────────────────────────────────────────────────────────
tab_top, tab_exits, tab_calcs = st.tabs([
    f"📊 Top {dynamic_n} Portfolio",
    "🚨 Exit Monitor",
    "📋 Full Rankings"])

# ── TAB 1: PORTFOLIO ──────────────────────────────────────────────────────────
with tab_top:
    st.markdown(f"## 📊 Top {dynamic_n} Portfolio  —  Regime Score {regime_score:.2f}")

    rows = []
    for i, (ticker, row) in enumerate(result.head(dynamic_n).iterrows(), 1):
        wt    = weights.get(ticker, 0.0)
        alloc = wt * capital
        if ticker in ledger:
            held = (TODAY - ledger[ticker]["entry_date"]).days
            rank = row["RANK"]
            if pd.isna(rank) or row["PCT_FROM_52H"] < -25:
                status = "🔴 EXIT-52H"
            elif rank > 40 and held >= 28:
                status = "🟠 EXIT-RANK"
            else:
                status = f"🔵 HOLD ({held}d)"
        elif allow_new:
            status = "🟢 NEW BUY"
        else:
            status = "⚪ WATCH"

        rows.append({
            "Rank":       int(row["RANK"]) if pd.notna(row["RANK"]) else None,
            "Ticker":     ticker,
            "Status":     status,
            "Weight":     wt,
            "Alloc (Rs)": round(alloc),
            "SHARPE_ALL": round(row["COMPOSITE"], 3) if pd.notna(row["COMPOSITE"]) else None,
            "RES_MOM":    round(row["RES_MOM"], 3)   if pd.notna(row.get("RES_MOM")) else None,
            "SHARPE_3":   round(row["SHARPE_3"], 3)  if pd.notna(row.get("SHARPE_3")) else None,
            "52H%":       round(row["PCT_FROM_52H"], 1) if pd.notna(row["PCT_FROM_52H"]) else None,
        })

    rows.append({
        "Rank": None, "Ticker": "CASH (LIQUID)", "Status": "—",
        "Weight": cash_wt, "Alloc (Rs)": round(cash_wt * capital),
        "SHARPE_ALL": None, "RES_MOM": None, "SHARPE_3": None, "52H%": None,
    })

    top_df = pd.DataFrame(rows)

    def style_top(row):
        if row["Ticker"] == "CASH (LIQUID)":
            return ["background-color:#F5F7FA; color:#6B7A8D; font-weight:bold;"] * len(row)
        if "EXIT" in str(row["Status"]):
            return ["background-color:#FFF3E0;"] * len(row)
        if "NEW BUY" in str(row["Status"]):
            return ["background-color:#E8F5E9;"] * len(row)
        return [""] * len(row)

    st.dataframe(
        top_df.style.apply(style_top, axis=1).format(
            {"Weight": "{:.1%}", "Alloc (Rs)": "Rs{:,.0f}",
             "SHARPE_ALL": "{:.3f}", "RES_MOM": "{:.3f}",
             "SHARPE_3": "{:.3f}", "52H%": "{:.1f}"}, na_rep="—"),
        use_container_width=True, hide_index=True, height=min(780, (dynamic_n + 3) * 36))

    eq_invested = sum(weights.values())
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1: st.metric("Equity Deployed",  f"Rs{eq_invested * capital:,.0f}")
    with mc2: st.metric("Equity Weight",    f"{eq_invested:.1%}")
    with mc3: st.metric("Cash (Liquid)",    f"Rs{cash_wt * capital:,.0f}")
    with mc4: st.metric("Cash Weight",      f"{cash_wt:.1%}")

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

        st.dataframe(exit_df.style.apply(style_exits, axis=1),
                     use_container_width=True, hide_index=True)

        st.markdown(
            f"**Positions:** {len(ledger)}  |  "
            f"**52H Exits:** {len(exit_df[exit_df['Trigger']=='52H_BREACH'])}  |  "
            f"**Rank Exits:** {len(exit_df[exit_df['Trigger']=='RANK_EXIT'])}  |  "
            f"**Hold-Locked:** {len(exit_df[exit_df['Trigger']=='HOLD_LOCK'])}  |  "
            f"**Healthy:** {len(exit_df[exit_df['Trigger']=='HEALTHY'])}")

# ── TAB 3: FULL RANKINGS ──────────────────────────────────────────────────────
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
