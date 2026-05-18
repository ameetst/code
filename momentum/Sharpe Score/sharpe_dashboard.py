"""
Sharpe Momentum Strategy — Streamlit Dashboard
================================================
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
st.set_page_config(page_title="Sharpe Momentum", page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

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
.regime-buy { background: #E8F5E9; color: #2E7D32; padding: 6px 16px;
              border-radius: 20px; font-weight: 700; display: inline-block; }
.regime-not { background: #FFEBEE; color: #C62828; padding: 6px 16px;
              border-radius: 20px; font-weight: 700; display: inline-block; }
#MainMenu {visibility: hidden;} footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    st.divider()

    st.markdown("#### 📁 Data Source")
    default_files = [f.name for f in SCRIPT_DIR.glob("*.xlsx")
                     if not f.name.startswith("~") and "ranking" not in f.name.lower()]
    selected_file = st.selectbox("Input File", default_files,
                                 index=default_files.index("N750.xlsx") if "N750.xlsx" in default_files else 0)
    input_path = str(SCRIPT_DIR / selected_file)

    st.divider()
    st.markdown("#### 💰 Capital & Sizing")
    capital = st.number_input("Portfolio Capital (INR)", value=2_000_000, step=100_000, format="%d")
    max_wt = st.slider("Max Position Weight", 0.03, 0.10, 0.05, 0.01, format="%.0f%%",
                        help="Maximum allocation per stock")

    st.divider()
    st.markdown("#### 📋 Strategy Parameters")
    params = {"RFR": "7.0%", "Windows": "12M / 9M / 6M / 3M",
              "Top N": "20", "Hold Lock": "28 days",
              "52H Filter": "≥ -25%", "Rank Buffer": "40",
              "Cash Yield": "6% p.a."}
    for k, v in params.items():
        st.markdown(f"**{k}:** `{v}`")

    st.divider()
    st.caption("Sharpe Momentum Strategy v2.0")

# ── CONFIG ────────────────────────────────────────────────────────────────────
RFR_ANNUAL = 0.07; TRADING_DAYS = 252; TOP_N = 20
WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
rfr_daily = RFR_ANNUAL / TRADING_DAYS
LEDGER_FILE = str(SCRIPT_DIR / "positions_ledger.json")
TODAY = datetime.date.today()

# ── LOAD & COMPUTE (cached) ──────────────────────────────────────────────────
@st.cache_data(show_spinner="Loading price data...")
def load_data(filepath):
    return ml.load_prices(filepath)

@st.cache_data(show_spinner="Computing Sharpe rankings...")
def compute_all(_prices_df, _nifty_series, _stock_tickers):
    sharpe_df, z_df = ml.compute_sharpe(_prices_df, _stock_tickers, WINDOWS, rfr_daily, TRADING_DAYS)
    ret_df = ml.compute_returns(_prices_df, _stock_tickers)
    pct_52h = ml.compute_pct_from_52h(_prices_df, _stock_tickers)
    resmom_df, rs_z_df = ml.compute_residual_momentum(_prices_df, _stock_tickers, _nifty_series, WINDOWS, TRADING_DAYS)
    regime = ml.compute_market_regime(_nifty_series)

    result = z_df.join(sharpe_df.rename(columns={l: f"S_{l}" for l in WINDOWS}))
    for col in ["COMPOSITE", "SHARPE_3"]:
        result[col] = result[col].map(ml.normalise_composite)
    result["SHARPE_ALL"] = result["COMPOSITE"]
    result["PCT_FROM_52H"] = pct_52h
    eligible = result["PCT_FROM_52H"] >= -25
    result["RANK"] = np.nan
    result.loc[eligible, "RANK"] = result.loc[eligible, "COMPOSITE"].rank(
        ascending=False, method="first", na_option="bottom")
    result = result.sort_values(["RANK", "COMPOSITE"], ascending=[True, False])
    result = result.join(ret_df)
    result = result.join(resmom_df)
    result = result.join(rs_z_df)
    return result, regime

def compute_weights(result, capital_val, max_weight):
    top_tickers = result.head(TOP_N).index.tolist()
    raw_w = {}
    for t in top_tickers:
        comp = result.loc[t, "COMPOSITE"]
        px = prices_df.loc[t].dropna()
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
    eq_wt = sum(weights.values())
    cash_wt = max(0.0, 1.0 - eq_wt)
    return weights, cash_wt

def load_ledger():
    p = Path(LEDGER_FILE)
    if not p.exists(): return {}
    with open(p) as f: raw = json.load(f)
    ledger = {}
    for t, rec in raw.items():
        try:
            ledger[t] = {"entry_date": datetime.date.fromisoformat(rec["entry_date"]),
                         "entry_price": float(rec["entry_price"])}
        except (KeyError, ValueError): pass
    return ledger

# ── TITLE ─────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='color:#1F4E79; margin-bottom:0;'>📊 Sharpe Momentum Strategy</h1>"
    "<p style='color:#6B7A8D; margin-top:4px;'>Rank → Filter → Size → Allocate</p>",
    unsafe_allow_html=True)

# ── LOAD DATA ─────────────────────────────────────────────────────────────────
if not Path(input_path).exists():
    st.error(f"❌ Data file not found: `{input_path}`")
    st.stop()

try:
    prices_df, nifty_series, stock_tickers, dates = load_data(input_path)
except Exception as e:
    st.error(f"Error loading data: {e}"); st.stop()

try:
    result, regime = compute_all(prices_df, nifty_series, stock_tickers)
except Exception as e:
    st.error(f"Error computing rankings: {e}"); st.stop()

weights, cash_wt = compute_weights(result, capital, max_wt)
ledger = load_ledger()

# ── REGIME HEADER ─────────────────────────────────────────────────────────────
is_buy = regime.startswith("BUY")
badge = "regime-buy" if is_buy else "regime-not"
emoji = "🟢" if is_buy else "🔴"
label = "BUY" if is_buy else "NOT BUY"

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"<div style='text-align:center; padding:12px;'>"
                f"<span class='{badge}' style='font-size:18px;'>{emoji} {label}</span></div>",
                unsafe_allow_html=True)
with c2: st.metric("Data Range", f"{dates[0].strftime('%d-%b-%y')} → {dates[-1].strftime('%d-%b-%y')}")
with c3: st.metric("Universe", f"{len(stock_tickers)} stocks")
with c4: st.metric("Eligible", f"{(result['PCT_FROM_52H'] >= -25).sum()} stocks")

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# TABS — mirror the Excel sheets
# ══════════════════════════════════════════════════════════════════════════════
tab_top20, tab_exits, tab_calcs = st.tabs(["📊 Top 20 Portfolio", "🚨 Exit Monitor", "📋 Full Rankings"])

# ── TAB 1: TOP 20 ────────────────────────────────────────────────────────────
with tab_top20:
    st.markdown("## 📊 Top 20 Portfolio Allocation")

    rows = []
    for i, (ticker, row) in enumerate(result.head(TOP_N).iterrows(), 1):
        wt = weights.get(ticker, 0.0)
        alloc = wt * capital
        status = ""
        if ticker in ledger:
            held = (TODAY - ledger[ticker]["entry_date"]).days
            rank = row["RANK"]
            if pd.isna(rank) or row["PCT_FROM_52H"] < -25:
                status = "🔴 EXIT-52H"
            elif rank > 40 and held >= 28:
                status = "🟠 EXIT-RANK"
            else:
                status = f"🔵 HOLD ({held}d)"
        elif is_buy:
            status = "🟢 NEW BUY"

        rows.append({
            "Rank": int(row["RANK"]) if pd.notna(row["RANK"]) else None,
            "Ticker": ticker,
            "Status": status,
            "Weight": wt,
            "Alloc (₹)": round(alloc),
            "SHARPE_ALL": round(row["COMPOSITE"], 3) if pd.notna(row["COMPOSITE"]) else None,
            "RES_MOM": round(row["RES_MOM"], 3) if pd.notna(row.get("RES_MOM")) else None,
            "SHARPE_3": round(row["SHARPE_3"], 3) if pd.notna(row.get("SHARPE_3")) else None,
            "52H%": round(row["PCT_FROM_52H"], 1) if pd.notna(row["PCT_FROM_52H"]) else None,
        })

    # Cash row
    rows.append({
        "Rank": None, "Ticker": "CASH (LIQUID)", "Status": "—",
        "Weight": cash_wt, "Alloc (₹)": round(cash_wt * capital),
        "SHARPE_ALL": None, "RES_MOM": None, "SHARPE_3": None, "52H%": None,
    })

    top_df = pd.DataFrame(rows)

    def style_top20(row):
        if row["Ticker"] == "CASH (LIQUID)":
            return ["background-color: #F5F7FA; color: #6B7A8D; font-weight: bold;"] * len(row)
        if "EXIT" in str(row["Status"]):
            return ["background-color: #FFF3E0;"] * len(row)
        if "NEW BUY" in str(row["Status"]):
            return ["background-color: #E8F5E9;"] * len(row)
        return [""] * len(row)

    st.dataframe(
        top_df.style.apply(style_top20, axis=1).format({
            "Weight": "{:.1%}", "Alloc (₹)": "₹{:,.0f}",
            "SHARPE_ALL": "{:.3f}", "RES_MOM": "{:.3f}",
            "SHARPE_3": "{:.3f}", "52H%": "{:.1f}",
        }, na_rep="—"),
        use_container_width=True, hide_index=True, height=780,
    )

    # Summary metrics
    eq_invested = sum(weights.values())
    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1: st.metric("Equity Deployed", f"₹{eq_invested * capital:,.0f}")
    with mc2: st.metric("Equity Weight", f"{eq_invested:.1%}")
    with mc3: st.metric("Cash Reserve", f"₹{cash_wt * capital:,.0f}")
    with mc4: st.metric("Cash Weight", f"{cash_wt:.1%}")

# ── TAB 2: EXIT MONITOR ──────────────────────────────────────────────────────
with tab_exits:
    st.markdown("## 🚨 Exit Evaluation")

    if not ledger:
        st.info("No open positions in ledger — nothing to evaluate. "
                f"Ledger path: `{LEDGER_FILE}`")
    else:
        exit_rows = []
        for ticker, rec in ledger.items():
            held = (TODAY - rec["entry_date"]).days
            rank_val = result.loc[ticker, "RANK"] if ticker in result.index else np.nan
            pct52 = result.loc[ticker, "PCT_FROM_52H"] if ticker in result.index else np.nan

            if pd.isna(rank_val) or (pd.notna(pct52) and pct52 < -25):
                trigger = "52H_BREACH"
                action = "⚠️ SELL IMMEDIATELY"
            elif pd.notna(rank_val) and rank_val > 40 and held >= 28:
                trigger = "RANK_EXIT"
                action = "🔻 SELL (rank dropped)"
            elif pd.notna(rank_val) and rank_val > 40 and held < 28:
                trigger = "HOLD_LOCK"
                action = f"🔒 Locked ({held}/{28}d)"
            else:
                trigger = "HEALTHY"
                action = "✅ HOLD"

            exit_rows.append({
                "Ticker": ticker,
                "Action": action,
                "Trigger": trigger,
                "Rank": int(rank_val) if pd.notna(rank_val) else None,
                "52H%": round(pct52, 1) if pd.notna(pct52) else None,
                "Days Held": held,
                "Entry Date": rec["entry_date"].isoformat(),
                "Entry Price": round(rec["entry_price"], 2),
            })

        exit_df = pd.DataFrame(exit_rows)

        def style_exits(row):
            if "SELL IMMEDIATELY" in str(row["Action"]):
                return ["background-color: #FFEBEE; font-weight: bold;"] * len(row)
            if "SELL" in str(row["Action"]):
                return ["background-color: #FFF3E0;"] * len(row)
            if "Locked" in str(row["Action"]):
                return ["background-color: #FFF8E1;"] * len(row)
            return ["background-color: #E8F5E9;"] * len(row)

        breaches = exit_df[exit_df["Trigger"].isin(["52H_BREACH", "RANK_EXIT"])]
        if len(breaches) > 0:
            st.error(f"🚨 **{len(breaches)} EXIT SIGNAL(S) — Action Required!**")
        else:
            st.success(f"✅ All {len(ledger)} positions healthy. No exits triggered.")

        st.dataframe(
            exit_df.style.apply(style_exits, axis=1),
            use_container_width=True, hide_index=True,
        )

        # Summary
        st.markdown(f"**Positions:** {len(ledger)}  |  "
                    f"**52H Exits:** {len(exit_df[exit_df['Trigger']=='52H_BREACH'])}  |  "
                    f"**Rank Exits:** {len(exit_df[exit_df['Trigger']=='RANK_EXIT'])}  |  "
                    f"**Hold-Locked:** {len(exit_df[exit_df['Trigger']=='HOLD_LOCK'])}  |  "
                    f"**Healthy:** {len(exit_df[exit_df['Trigger']=='HEALTHY'])}")

# ── TAB 3: FULL RANKINGS ─────────────────────────────────────────────────────
with tab_calcs:
    st.markdown("## 📋 Full Universe Rankings")

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        filter_elig = st.selectbox("Eligibility", ["All", "Eligible only", "Disqualified only"])
    with fc2:
        top_n_show = st.slider("Show top N", 10, len(result), min(100, len(result)), 10)
    with fc3:
        sort_col = st.selectbox("Sort by", ["RANK", "COMPOSITE", "RES_MOM", "PCT_FROM_52H"])

    display_cols = ["RANK", "COMPOSITE", "SHARPE_3"]
    for lbl in WINDOWS:
        s_col, z_col = f"S_{lbl}", f"Z_{lbl}"
        if s_col in result.columns: display_cols.append(s_col)
        if z_col in result.columns: display_cols.append(z_col)
    if "RES_MOM" in result.columns: display_cols.append("RES_MOM")
    for c in ["1M%", "3M%", "12M%", "PCT_FROM_52H"]:
        if c in result.columns: display_cols.append(c)

    available = [c for c in display_cols if c in result.columns]
    calcs_df = result[available].copy()
    calcs_df.index.name = "TICKER"
    calcs_df = calcs_df.reset_index()

    if filter_elig == "Eligible only":
        calcs_df = calcs_df[calcs_df["PCT_FROM_52H"] >= -25]
    elif filter_elig == "Disqualified only":
        calcs_df = calcs_df[calcs_df["PCT_FROM_52H"] < -25]

    asc = True if sort_col == "RANK" else False
    calcs_df = calcs_df.sort_values(sort_col, ascending=asc, na_position="last").head(top_n_show)

    fmt = {c: "{:.3f}" for c in calcs_df.columns if c not in ["RANK", "TICKER"]}
    fmt["PCT_FROM_52H"] = "{:.1f}"
    for c in ["1M%", "3M%", "12M%"]: 
        if c in fmt: fmt[c] = "{:.1f}"

    def style_calcs(row):
        if pd.notna(row.get("PCT_FROM_52H")) and row["PCT_FROM_52H"] < -25:
            return ["background-color: #FFF8F8; color: #B0B0B0;"] * len(row)
        try:
            if pd.notna(row.get("RANK")) and int(row["RANK"]) <= TOP_N:
                return ["background-color: #E8F5E9;"] * len(row)
        except (ValueError, TypeError): pass
        return [""] * len(row)

    st.dataframe(
        calcs_df.style.apply(style_calcs, axis=1).format(fmt, na_rep="—"),
        use_container_width=True, hide_index=True, height=600,
    )
    st.caption(f"Universe: {len(stock_tickers)}  |  "
               f"Eligible: {(result['PCT_FROM_52H'] >= -25).sum()}  |  "
               f"Disqualified: {(result['PCT_FROM_52H'] < -25).sum()}")

# ── FOOTER: ACTIONS ───────────────────────────────────────────────────────────
st.divider()
st.markdown("## ⚡ Actions")
ac1, ac2 = st.columns(2)
with ac1:
    if st.button("🔁 Refresh Rankings", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with ac2:
    output_path = SCRIPT_DIR / "N750_rankings.xlsx"
    if output_path.exists():
        with open(output_path, "rb") as f:
            st.download_button("📥 Download Rankings Excel", f.read(),
                               file_name="N750_rankings.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
    else:
        st.info("Run `1_Sharpe.py` first to generate the Excel output.")
