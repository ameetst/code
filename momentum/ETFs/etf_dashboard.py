"""
ETF Momentum Strategy — Streamlit Dashboard
=============================================
A clean white-themed web interface for the ETF momentum ranking engine.
Run:  streamlit run etf_dashboard.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import json
import sys
import os
from pathlib import Path
from datetime import datetime
import tempfile

# ── Ensure the script directory is importable ──────────────
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import etf_momentum_ranking as emr

# =========================================================
# PAGE CONFIG
# =========================================================
st.set_page_config(
    page_title="ETF Momentum Strategy",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS for white theme polish ──────────────────────
st.markdown("""
<style>
    /* Clean white background */
    .stApp { background-color: #FFFFFF; }
    
    /* Metric cards */
    div[data-testid="stMetric"] {
        background: #F5F7FA;
        border: 1px solid #E8ECF1;
        border-radius: 10px;
        padding: 14px 18px;
    }
    div[data-testid="stMetric"] label { color: #6B7A8D !important; font-size: 13px !important; }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] { color: #1A1A2E !important; }
    
    /* Table styling */
    .stDataFrame { border-radius: 8px; overflow: hidden; }
    thead tr th { background-color: #1F4E79 !important; color: #FFFFFF !important; }
    
    /* Buttons */
    .stButton > button {
        background: #1F4E79; color: white; border: none;
        border-radius: 8px; font-weight: 600; padding: 8px 24px;
    }
    .stButton > button:hover { background: #163D5E; }
    
    /* Section headers */
    h2 { color: #1F4E79 !important; border-bottom: 2px solid #E8ECF1; padding-bottom: 8px; }
    
    /* Regime badges */
    .regime-bull { background: #E8F5E9; color: #2E7D32; padding: 6px 16px; border-radius: 20px; font-weight: 700; }
    .regime-partial { background: #FFF8E1; color: #F57F17; padding: 6px 16px; border-radius: 20px; font-weight: 700; }
    .regime-bear { background: #FFEBEE; color: #C62828; padding: 6px 16px; border-radius: 20px; font-weight: 700; }
    
    /* Action badges */
    .badge-buy { background: #E8F5E9; color: #2E7D32; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
    .badge-sell { background: #FFEBEE; color: #C62828; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
    .badge-hold { background: #E3F2FD; color: #1565C0; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 12px; }
    
    /* Remove Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Card container */
    .card {
        background: #FFFFFF; border: 1px solid #E8ECF1; border-radius: 12px;
        padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
</style>
""", unsafe_allow_html=True)


# =========================================================
# SIDEBAR
# =========================================================
with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    st.divider()
    
    # Data source
    st.markdown("#### 📁 Data Source")
    data_source = st.radio(
        "NAV Input File",
        ["Default (ETF.xlsx)", "Upload custom file"],
        index=0,
        label_visibility="collapsed",
    )
    
    uploaded_file = None
    input_path = str(SCRIPT_DIR / emr.CONFIG.INPUT_FILE)
    
    if data_source == "Upload custom file":
        uploaded_file = st.file_uploader("Upload ETF data (.xlsx)", type=["xlsx"])
        if uploaded_file:
            # Save to temp and use that path
            tmp_dir = SCRIPT_DIR / ".streamlit_tmp"
            tmp_dir.mkdir(exist_ok=True)
            tmp_path = tmp_dir / "uploaded_etf.xlsx"
            with open(tmp_path, "wb") as f:
                f.write(uploaded_file.getvalue())
            input_path = str(tmp_path)
            st.success(f"✅ Using uploaded file")
    else:
        if Path(input_path).exists():
            st.info(f"📄 {emr.CONFIG.INPUT_FILE}")
        else:
            st.error(f"❌ {emr.CONFIG.INPUT_FILE} not found!")
    
    st.divider()
    
    # CONFIG display
    st.markdown("#### 📋 Strategy Parameters")
    params = {
        "Regime Ticker": getattr(emr.CONFIG, 'REGIME_TICKER', 'MONIFTY500'),
        "Top N (BULL)": emr.CONFIG.TOP_N,
        "Top N (PARTIAL)": emr.CONFIG.TOP_N_PARTIAL,
        "Sector Cap": emr.CONFIG.SECTOR_CAP,
        "TSL Threshold": f"{emr.CONFIG.TSL_THRESHOLD:.0%}",
        "52W High Screen": f"{emr.CONFIG.MAX_DRAWDOWN_FROM_HIGH:.0%}",
        "Sharpe 6M Weight": f"{emr.CONFIG.SHARPE_W6M:.0%}",
        "Sharpe 3M Weight": f"{emr.CONFIG.SHARPE_W3M:.0%}",
        "Risk-Free Rate": "7% p.a.",
    }
    for k, v in params.items():
        st.markdown(f"**{k}:** `{v}`")
    
    st.divider()
    st.markdown(
        "<div style='text-align:center; color:#9AA5B4; font-size:12px;'>"
        "ETF Momentum Strategy v1.0</div>",
        unsafe_allow_html=True,
    )


# =========================================================
# HELPER: Load data with caching
# =========================================================
@st.cache_data(show_spinner="Loading ETF data...")
def load_data(filepath):
    meta, prices = emr.load_etf_data(filepath)
    return meta, prices


@st.cache_data(show_spinner="Computing rankings...")
def compute_rankings(_meta, _prices):
    """Run the full ranking pipeline."""
    regime = emr.regime_status(_prices)
    ranking = emr.build_ranking(_meta, _prices)
    allocation = emr.build_allocation(ranking, regime)
    return regime, ranking, allocation


def load_log():
    """Load holdings log from disk."""
    return emr.load_holdings_log(SCRIPT_DIR)


def fetch_tsl_data(holdings):
    """Fetch live NAVs and compute TSL metrics for current holdings."""
    import yfinance as yf
    
    held = [s for s in holdings if s["ticker"] != "CASH"]
    if not held:
        return pd.DataFrame()
    
    tickers_nse = [s["ticker"] + ".NS" for s in held]
    live_prices = {}
    
    try:
        data = yf.download(tickers_nse, period="5d", auto_adjust=True, progress=False)
        close = data["Close"]
        if isinstance(close, pd.Series):
            close = close.to_frame(tickers_nse[0])
        for s in held:
            yf_t = s["ticker"] + ".NS"
            if yf_t in close.columns:
                vals = close[yf_t].dropna()
                if len(vals) > 0:
                    live_prices[s["ticker"]] = float(vals.iloc[-1])
    except Exception as e:
        st.error(f"Error fetching prices: {e}")
        return pd.DataFrame()
    
    threshold = emr.CONFIG.TSL_THRESHOLD
    rows = []
    for s in holdings:
        t = s["ticker"]
        if t == "CASH":
            rows.append({
                "Slot": s["slot"], "Ticker": "CASH", "ETF Name": "Cash / Money Market",
                "Entry": None, "Peak": None, "TSL NAV": None,
                "Current NAV": None, "DD%": None, "Status": "—",
            })
            continue
        
        entry_price = s.get("entry_price")
        peak = s.get("peak")
        nav = live_prices.get(t)
        
        if nav is None:
            rows.append({
                "Slot": s["slot"], "Ticker": t, "ETF Name": s.get("etf_name", ""),
                "Entry": entry_price, "Peak": peak, "TSL NAV": None,
                "Current NAV": None, "DD%": None, "Status": "FETCH FAILED",
            })
            continue
        
        if peak is None or nav > peak:
            peak = nav
        
        tsl_nav = peak * (1 - threshold)
        dd = (peak - nav) / peak if peak > 0 else 0.0
        status = "⚠️ BREACH" if dd >= threshold else "✅ OK"
        
        rows.append({
            "Slot": s["slot"], "Ticker": t, "ETF Name": s.get("etf_name", ""),
            "Entry": round(entry_price, 2) if entry_price else None,
            "Peak": round(peak, 2),
            "TSL NAV": round(tsl_nav, 2),
            "Current NAV": round(nav, 2),
            "DD%": round(dd * 100, 1),
            "Status": status,
        })
    
    return pd.DataFrame(rows)


# =========================================================
# MAIN CONTENT
# =========================================================

# Title
st.markdown(
    "<h1 style='color:#1F4E79; margin-bottom:0;'>📈 ETF Momentum Strategy</h1>"
    "<p style='color:#6B7A8D; margin-top:4px;'>Screen → Score → Regime → Allocate</p>",
    unsafe_allow_html=True,
)

# Check if data file exists
if not Path(input_path).exists():
    st.error(f"❌ Data file not found: `{input_path}`\n\nPlease ensure `ETF.xlsx` is in the script directory or upload a custom file.")
    st.stop()

# Load data
try:
    meta, prices = load_data(input_path)
except Exception as e:
    st.error(f"Error loading data: {e}")
    st.stop()

# Compute rankings
try:
    regime, ranking, allocation = compute_rankings(meta, prices)
except Exception as e:
    st.error(f"Error computing rankings: {e}")
    st.stop()

# ── Section: Regime Status ─────────────────────────────────
st.markdown("## 🎯 Regime Status")

regime_label = regime["label"]
if regime_label == "BULL":
    badge_class = "regime-bull"
    regime_emoji = "🟢"
elif regime_label == "PARTIAL":
    badge_class = "regime-partial"
    regime_emoji = "🟡"
else:
    badge_class = "regime-bear"
    regime_emoji = "🔴"

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown(
        f"<div style='text-align:center; padding:12px;'>"
        f"<span class='{badge_class}' style='font-size:18px;'>{regime_emoji} {regime_label}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
with col2:
    st.metric("Price", f"{regime['nifty_price']:.2f}")
with col3:
    st.metric("EMA 50", f"{regime['nifty_ema_50']:.2f}")
with col4:
    st.metric("EMA 100", f"{regime['nifty_ema_100']:.2f}")

col_a, col_b, col_c = st.columns(3)
with col_a:
    st.metric("Active Slots", f"{regime['active_slots']} / {emr.CONFIG.TOP_N}")
with col_b:
    st.metric("Trend Ticker", regime.get("trend_ticker", "N/A"))
with col_c:
    data_range = f"{prices.index[0].strftime('%Y-%m-%d')} → {prices.index[-1].strftime('%Y-%m-%d')}"
    st.metric("Data Range", data_range)

st.divider()

# ── Section: Current Allocation ────────────────────────────
st.markdown("## 📊 Current Allocation")

alloc_display = allocation[["SLOT", "TICKER", "ETF_NAME", "SECTOR", "WEIGHT", "INV_RANK"]].copy()
alloc_display["WEIGHT"] = alloc_display["WEIGHT"].apply(lambda x: f"{x:.0%}")
alloc_display.columns = ["Slot", "Ticker", "ETF Name", "Sector", "Weight", "Inv Rank"]

# Color the rows
def style_allocation(row):
    if row["Ticker"] == "CASH":
        return ["background-color: #F9FAFB; color: #9AA5B4;"] * len(row)
    return ["background-color: #FFFFFF;"] * len(row)

st.dataframe(
    alloc_display.style.apply(style_allocation, axis=1),
    use_container_width=True,
    hide_index=True,
    height=220,
)

st.divider()

# ── Section: TSL Monitor ──────────────────────────────────
st.markdown("## 🛡️ Trailing Stop Loss Monitor")

log = load_log()
month_key = datetime.today().strftime("%Y-%m")

if month_key in log:
    current_entry = log[month_key]
    holdings = current_entry.get("allocation", [])
    
    has_positions = any(s["ticker"] != "CASH" for s in holdings)
    
    if has_positions:
        if st.button("🔄 Check TSL (Fetch Live NAVs)", use_container_width=True):
            with st.spinner("Fetching live NAVs via yfinance..."):
                tsl_df = fetch_tsl_data(holdings)
            
            if not tsl_df.empty:
                # Check for breaches
                breaches = tsl_df[tsl_df["Status"] == "⚠️ BREACH"]
                
                if len(breaches) > 0:
                    st.error(f"🚨 **{len(breaches)} TSL BREACH(ES) DETECTED!**")
                    for _, b in breaches.iterrows():
                        st.markdown(
                            f"<div style='background:#FFEBEE; border-left:4px solid #C62828; "
                            f"padding:12px; border-radius:6px; margin:8px 0;'>"
                            f"<b>SELL {b['Ticker']}</b> ({b['ETF Name']}) — "
                            f"Drawdown {b['DD%']:.1f}% exceeds {emr.CONFIG.TSL_THRESHOLD:.0%} TSL"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                else:
                    st.success("✅ All positions within TSL threshold. No action needed.")
                
                # Display table
                st.dataframe(
                    tsl_df.style.apply(
                        lambda row: [
                            "background-color: #FFEBEE;" if row["Status"] == "⚠️ BREACH"
                            else "background-color: #F9FAFB;" if row["Ticker"] == "CASH"
                            else ""
                        ] * len(row),
                        axis=1,
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                
                st.caption(f"Last checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            st.info("Click the button above to fetch live NAVs and check trailing stop loss levels.")
    else:
        st.info("No active positions (all cash). Nothing to check.")
else:
    st.warning("No holdings found for current month. Run the monthly rebalance first.")

st.divider()

# ── Section: Rebalance Diff ────────────────────────────────
st.markdown("## 🔄 Rebalance Changes")

sorted_keys = sorted(log.keys())
prev_keys = [k for k in sorted_keys if k < month_key]

if prev_keys and month_key in log:
    prev_entry = log[prev_keys[-1]]
    curr_entry = log[month_key]
    changes = emr.diff_allocations(prev_entry, curr_entry)
    
    if changes:
        prev_month = prev_entry.get("run_date", "?")[:7]
        st.markdown(f"**Previous month:** {prev_month}  |  **Changes:** {len(changes)}")
        
        change_rows = []
        for ch in changes:
            action = ch["action"]
            if action == "BUY":
                badge = '<span class="badge-buy">BUY</span>'
            elif action == "SELL":
                badge = '<span class="badge-sell">SELL</span>'
            elif action == "HOLD":
                badge = '<span class="badge-hold">HOLD</span>'
            elif action == "REGIME":
                badge = '<span style="background:#F3E5F5;color:#7B1FA2;padding:3px 10px;border-radius:12px;font-weight:600;font-size:12px;">REGIME</span>'
            else:
                badge = f'<span class="badge-hold">{action}</span>'
            
            change_rows.append({
                "Action": action,
                "Ticker": ch["ticker"],
                "ETF Name": ch["etf_name"],
                "Prev Wt": f"{ch['prev_wt']:.0%}" if ch["prev_wt"] > 0 else "—",
                "Curr Wt": f"{ch['curr_wt']:.0%}" if ch["curr_wt"] > 0 else "—",
                "Note": ch["note"],
            })
        
        changes_df = pd.DataFrame(change_rows)
        
        def style_changes(row):
            if row["Action"] == "BUY":
                return ["background-color: #E8F5E9;"] * len(row)
            elif row["Action"] == "SELL":
                return ["background-color: #FFEBEE;"] * len(row)
            elif row["Action"] == "REGIME":
                return ["background-color: #F3E5F5;"] * len(row)
            return [""] * len(row)
        
        st.dataframe(
            changes_df.style.apply(style_changes, axis=1),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No changes from previous month.")
elif not prev_keys:
    st.info("First month — no previous holdings to compare.")
else:
    st.warning("Current month's allocation not found. Run the monthly rebalance.")

st.divider()

# ── Section: Full Rankings ─────────────────────────────────
st.markdown("## 📋 Full Rankings")

# Prepare display columns
display_cols = ["RANK_INVESTABLE", "RANK_UNIVERSE", "TICKER", "ETF_NAME", "SECTOR",
                "WTD_SHARPE", "SHARPE_6M", "SHARPE_3M", "SCREEN_PASS"]
available_cols = [c for c in display_cols if c in ranking.columns]
rank_display = ranking[available_cols].copy()

# Rename for readability
col_rename = {
    "RANK_INVESTABLE": "Inv Rank",
    "RANK_UNIVERSE": "Uni Rank",
    "TICKER": "Ticker",
    "ETF_NAME": "ETF Name",
    "SECTOR": "Sector",
    "WTD_SHARPE": "Wtd Sharpe",
    "SHARPE_6M": "Sharpe 6M",
    "SHARPE_3M": "Sharpe 3M",
    "SCREEN_PASS": "Screen",
}
rank_display = rank_display.rename(columns=col_rename)

# Filters
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    show_screen = st.selectbox("Screen Filter", ["All", "PASS only", "FAIL only"], index=0)
with col_f2:
    sectors = ["All"] + sorted(rank_display["Sector"].unique().tolist())
    selected_sector = st.selectbox("Sector", sectors, index=0)
with col_f3:
    top_n_filter = st.slider("Show top N", min_value=5, max_value=len(rank_display), value=min(50, len(rank_display)), step=5)

# Apply filters
filtered = rank_display.copy()
if show_screen == "PASS only":
    filtered = filtered[filtered["Screen"] == True]
elif show_screen == "FAIL only":
    filtered = filtered[filtered["Screen"] == False]
if selected_sector != "All":
    filtered = filtered[filtered["Sector"] == selected_sector]
filtered = filtered.head(top_n_filter)

# Format numeric columns
for col in ["Wtd Sharpe", "Sharpe 6M", "Sharpe 3M"]:
    if col in filtered.columns:
        filtered[col] = filtered[col].apply(lambda x: f"{x:.3f}" if pd.notna(x) else "—")

def style_rankings(row):
    if row.get("Screen") == False:
        return ["background-color: #FFF8F8; color: #B0B0B0;"] * len(row)
    try:
        rank = int(row.get("Inv Rank", 999))
        if rank <= regime["active_slots"]:
            return ["background-color: #E8F5E9;"] * len(row)
    except (ValueError, TypeError):
        pass
    return [""] * len(row)

st.dataframe(
    filtered.style.apply(style_rankings, axis=1),
    use_container_width=True,
    hide_index=True,
    height=500,
)

st.caption(f"Universe: {len(meta)} ETFs  |  Investable: {ranking['SCREEN_PASS'].sum()}  |  "
           f"Screened out: {(~ranking['SCREEN_PASS']).sum()}")

st.divider()

# ── Section: Run Actions ──────────────────────────────────
st.markdown("## ⚡ Actions")

col_act1, col_act2 = st.columns(2)

with col_act1:
    if st.button("🔁 Run Monthly Rebalance", use_container_width=True):
        with st.spinner("Running full pipeline..."):
            try:
                emr.run_pipeline(input_path)
                st.success("✅ Monthly rebalance complete! Refresh the page to see updated results.")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Error: {e}")

with col_act2:
    output_path = SCRIPT_DIR / emr.CONFIG.OUTPUT_FILE
    if output_path.exists():
        with open(output_path, "rb") as f:
            st.download_button(
                label="📥 Download Rankings Excel",
                data=f.read(),
                file_name=emr.CONFIG.OUTPUT_FILE,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    else:
        st.info("Run the monthly rebalance first to generate the Excel output.")
