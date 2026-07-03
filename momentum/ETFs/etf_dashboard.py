"""
ETF Momentum Strategy — Streamlit Dashboard
=============================================
A clean white-themed web interface for the ETF momentum ranking engine.
Run:  streamlit run etf_dashboard.py

Layout:
  - Regime banner (always visible at top)
  - Tab 1: Current Allocation  (allocation + TSL + rebalance diff + actions)
  - Tab 2: Full Rankings        (filterable ranking table)
  - Tab 3: Configuration        (editable strategy params + data source)
"""

import streamlit as st
import pandas as pd
import numpy as np
import json
import sys
import os
from pathlib import Path
from datetime import datetime

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
    initial_sidebar_state="collapsed",
)

# ── Custom CSS for white theme polish ──────────────────────
st.markdown("""
<style>
    /* Clean white background */
    .stApp { background-color: #FFFFFF; }
    
    /* Hide sidebar toggle */
    [data-testid="collapsedControl"] { display: none; }
    
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
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 24px; font-weight: 600; border-radius: 8px 8px 0 0;
    }
</style>
""", unsafe_allow_html=True)


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


# =========================================================
# DATA LOADING — resolve input path from config
# =========================================================
input_path = str(SCRIPT_DIR / emr.CONFIG.INPUT_FILE)

# Check for uploaded file in session state (set from Configuration tab)
if "uploaded_input_path" in st.session_state and st.session_state.uploaded_input_path:
    input_path = st.session_state.uploaded_input_path


# =========================================================
# TITLE + REGIME BANNER (always visible)
# =========================================================
st.markdown(
    "<h1 style='color:#1F4E79; margin-bottom:0;'>📈 ETF Momentum Strategy</h1>"
    "<p style='color:#6B7A8D; margin-top:4px;'>Screen → Score → Regime → Allocate</p>",
    unsafe_allow_html=True,
)

# Load data + compute rankings
if not Path(input_path).exists():
    st.error(f"❌ Data file not found: `{input_path}`\n\nPlease ensure `{emr.CONFIG.INPUT_FILE}` is in the script directory or upload a custom file in the Configuration tab.")
    st.stop()

try:
    meta, prices = load_data(input_path)
except Exception as e:
    st.error(f"Error loading data: {e}")
    st.stop()

try:
    regime, ranking, allocation = compute_rankings(meta, prices)
except Exception as e:
    st.error(f"Error computing rankings: {e}")
    st.stop()

# ── Regime Banner ──────────────────────────────────────────
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


# =========================================================
# TABS
# =========================================================
tab_alloc, tab_rankings, tab_config = st.tabs([
    "📊 Current Allocation", "📋 Full Rankings", "⚙️ Configuration"
])


# =========================================================
# TAB 1: CURRENT ALLOCATION
# =========================================================
with tab_alloc:
    # ── Current Allocation Table ───────────────────────────
    st.markdown("## 📊 Current Allocation")

    alloc_display = allocation[["SLOT", "TICKER", "ETF_NAME", "SECTOR", "WEIGHT", "INV_RANK"]].copy()
    alloc_display["WEIGHT"] = alloc_display["WEIGHT"].apply(lambda x: f"{x:.0%}")
    alloc_display.columns = ["Slot", "Ticker", "ETF Name", "Sector", "Weight", "Inv Rank"]

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

    # ── TSL Monitor ────────────────────────────────────────
    st.markdown("## 🛡️ Trailing Stop Loss Monitor")

    log = load_log()
    month_key = datetime.today().strftime("%Y-%m")

    if month_key in log:
        current_entry = log[month_key]
        holdings = current_entry.get("allocation", [])

        has_positions = any(s["ticker"] != "CASH" for s in holdings)

        if has_positions:
            if st.button("🔄 Check TSL (Fetch Live NAVs)", use_container_width=True, key="tsl_btn"):
                with st.spinner("Fetching live NAVs via yfinance..."):
                    tsl_result = emr.check_tsl(SCRIPT_DIR)

                if tsl_result and tsl_result["rows"]:
                    tsl_df = pd.DataFrame(tsl_result["rows"])

                    # Check for breaches
                    if tsl_result["breaches"]:
                        n_breach = len(tsl_result["breaches"])
                        st.error(f"🚨 **{n_breach} TSL BREACH(ES) DETECTED!**")
                        for b in tsl_result["breaches"]:
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

                    st.caption(f"Last checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Peaks saved to holdings_log.json")
                elif tsl_result:
                    st.warning(tsl_result["message"])
            else:
                st.info("Click the button above to fetch live NAVs and check trailing stop loss levels.")
        else:
            st.info("No active positions (all cash). Nothing to check.")
    else:
        st.warning("No holdings found for current month. Run the monthly rebalance first.")

    st.divider()

    # ── Rebalance Diff ─────────────────────────────────────
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

    # ── Actions ────────────────────────────────────────────
    st.markdown("## ⚡ Actions")

    col_act1, col_act2 = st.columns(2)

    with col_act1:
        if st.button("🔁 Run Monthly Rebalance", use_container_width=True, key="rebal_btn"):
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


# =========================================================
# TAB 2: FULL RANKINGS
# =========================================================
with tab_rankings:
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
        show_screen = st.selectbox("Screen Filter", ["All", "PASS only", "FAIL only"], index=0, key="screen_filter")
    with col_f2:
        sectors = ["All"] + sorted(rank_display["Sector"].unique().tolist())
        selected_sector = st.selectbox("Sector", sectors, index=0, key="sector_filter")
    with col_f3:
        top_n_filter = st.slider("Show top N", min_value=5, max_value=len(rank_display),
                                  value=min(50, len(rank_display)), step=5, key="topn_slider")

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


# =========================================================
# TAB 3: CONFIGURATION
# =========================================================
with tab_config:
    st.markdown("## ⚙️ Strategy Configuration")
    st.markdown("Edit parameters below and click **Save** to persist to `strategy_config.json`. "
                "Changes take effect after clicking **Run Rebalance**.")

    # Load current config
    current_cfg = emr.get_config_as_dict()

    st.divider()

    # ── Data Source ─────────────────────────────────────────
    st.markdown("### 📁 Data Source")
    data_source = st.radio(
        "NAV Input File",
        ["Default (ETF.xlsx)", "Upload custom file"],
        index=0,
        label_visibility="collapsed",
        key="cfg_data_source",
    )

    if data_source == "Upload custom file":
        uploaded_file = st.file_uploader("Upload ETF data (.xlsx)", type=["xlsx"], key="cfg_uploader")
        if uploaded_file:
            tmp_dir = SCRIPT_DIR / ".streamlit_tmp"
            tmp_dir.mkdir(exist_ok=True)
            tmp_path = tmp_dir / "uploaded_etf.xlsx"
            with open(tmp_path, "wb") as f:
                f.write(uploaded_file.getvalue())
            st.session_state.uploaded_input_path = str(tmp_path)
            st.success("✅ Uploaded file saved. Click **Run Rebalance** to apply.")
    else:
        default_path = SCRIPT_DIR / current_cfg.get("INPUT_FILE", "ETF.xlsx")
        if default_path.exists():
            st.info(f"📄 {current_cfg.get('INPUT_FILE', 'ETF.xlsx')}")
        else:
            st.error(f"❌ {current_cfg.get('INPUT_FILE', 'ETF.xlsx')} not found!")
        # Clear any uploaded path
        st.session_state.uploaded_input_path = None

    st.divider()

    # ── Portfolio Parameters ───────────────────────────────
    st.markdown("### 📊 Portfolio Allocation")
    cfg_col1, cfg_col2, cfg_col3 = st.columns(3)
    with cfg_col1:
        cfg_top_n = st.number_input("Top N (BULL)", min_value=1, max_value=20,
                                     value=current_cfg["TOP_N"], step=1, key="cfg_top_n")
    with cfg_col2:
        cfg_top_n_partial = st.number_input("Top N (PARTIAL)", min_value=1, max_value=20,
                                             value=current_cfg["TOP_N_PARTIAL"], step=1, key="cfg_top_n_partial")
    with cfg_col3:
        cfg_sector_cap = st.number_input("Sector Cap", min_value=1, max_value=10,
                                          value=current_cfg["SECTOR_CAP"], step=1, key="cfg_sector_cap")

    st.divider()

    # ── Momentum Parameters ────────────────────────────────
    st.markdown("### 📈 Momentum Scoring")
    cfg_col4, cfg_col5 = st.columns(2)
    with cfg_col4:
        cfg_window_6m = st.number_input("6M Window (days)", min_value=20, max_value=252,
                                         value=current_cfg["WINDOW_6M"], step=1, key="cfg_window_6m")
        cfg_sharpe_w6m = st.number_input("Sharpe 6M Weight", min_value=0.0, max_value=1.0,
                                          value=float(current_cfg["SHARPE_W6M"]), step=0.1,
                                          format="%.1f", key="cfg_sharpe_w6m")
    with cfg_col5:
        cfg_window_3m = st.number_input("3M Window (days)", min_value=10, max_value=126,
                                         value=current_cfg["WINDOW_3M"], step=1, key="cfg_window_3m")
        cfg_sharpe_w3m = st.number_input("Sharpe 3M Weight", min_value=0.0, max_value=1.0,
                                          value=float(current_cfg["SHARPE_W3M"]), step=0.1,
                                          format="%.1f", key="cfg_sharpe_w3m")

    st.divider()

    # ── Screening ──────────────────────────────────────────
    st.markdown("### 🔍 Screening")
    cfg_max_dd = st.number_input("Max Drawdown from 52W High", min_value=0.05, max_value=0.50,
                                  value=float(current_cfg["MAX_DRAWDOWN_FROM_HIGH"]), step=0.05,
                                  format="%.2f", key="cfg_max_dd",
                                  help="ETFs must be within this % of their 52-week high (0.25 = must be ≥ 75%)")

    st.divider()

    # ── Regime Parameters ──────────────────────────────────
    st.markdown("### 🎯 Regime Filter")
    cfg_col6, cfg_col7, cfg_col8 = st.columns(3)
    with cfg_col6:
        cfg_regime_ticker = st.text_input("Regime Ticker", value=current_cfg["REGIME_TICKER"], key="cfg_regime_ticker")
    with cfg_col7:
        cfg_fast_ema = st.number_input("Fast EMA Window", min_value=10, max_value=200,
                                        value=current_cfg["TREND_FAST_EMA_WINDOW"], step=5, key="cfg_fast_ema")
    with cfg_col8:
        cfg_slow_ema = st.number_input("Slow EMA Window", min_value=20, max_value=400,
                                        value=current_cfg["TREND_EMA_WINDOW"], step=10, key="cfg_slow_ema")

    cfg_fallbacks = st.text_input("Regime Fallbacks (comma-separated)",
                                   value=", ".join(current_cfg["REGIME_FALLBACKS"]),
                                   key="cfg_fallbacks",
                                   help="Fallback tickers if regime ticker is missing from data")

    st.divider()

    # ── Risk Parameters ────────────────────────────────────
    st.markdown("### ⚠️ Risk Management")
    cfg_col9, cfg_col10 = st.columns(2)
    with cfg_col9:
        cfg_rf_annual = st.number_input("Risk-Free Rate (annual)", min_value=0.0, max_value=0.20,
                                         value=float(current_cfg["DAILY_RF_ANNUAL"]), step=0.01,
                                         format="%.2f", key="cfg_rf_annual",
                                         help="Annual risk-free rate used for Sharpe calculation")
    with cfg_col10:
        cfg_tsl = st.number_input("TSL Threshold", min_value=0.01, max_value=0.30,
                                   value=float(current_cfg["TSL_THRESHOLD"]), step=0.01,
                                   format="%.2f", key="cfg_tsl",
                                   help="Trailing stop loss drawdown trigger (0.10 = 10%)")

    st.divider()

    # ── Save / Run Buttons ─────────────────────────────────
    col_save, col_run = st.columns(2)

    with col_save:
        if st.button("💾 Save Configuration", use_container_width=True, key="cfg_save_btn"):
            new_cfg = {
                "INPUT_FILE": current_cfg["INPUT_FILE"],
                "OUTPUT_FILE": current_cfg["OUTPUT_FILE"],
                "WINDOW_6M": cfg_window_6m,
                "WINDOW_3M": cfg_window_3m,
                "ANNUALIZE": current_cfg["ANNUALIZE"],
                "TOP_N": cfg_top_n,
                "TOP_N_PARTIAL": cfg_top_n_partial,
                "MAX_DRAWDOWN_FROM_HIGH": cfg_max_dd,
                "SHARPE_W6M": cfg_sharpe_w6m,
                "SHARPE_W3M": cfg_sharpe_w3m,
                "R2_W6M": current_cfg["R2_W6M"],
                "R2_W3M": current_cfg["R2_W3M"],
                "REGIME_TICKER": cfg_regime_ticker.strip(),
                "REGIME_FALLBACKS": [t.strip() for t in cfg_fallbacks.split(",") if t.strip()],
                "TREND_FAST_EMA_WINDOW": cfg_fast_ema,
                "TREND_EMA_WINDOW": cfg_slow_ema,
                "SECTOR_CAP": cfg_sector_cap,
                "DAILY_RF_ANNUAL": cfg_rf_annual,
                "TSL_THRESHOLD": cfg_tsl,
            }
            try:
                emr.save_config_to_json(new_cfg, SCRIPT_DIR)
                # Apply to running CONFIG
                emr._apply_json_config(emr.CONFIG, new_cfg)
                st.success("✅ Configuration saved to `strategy_config.json`")
            except Exception as e:
                st.error(f"Error saving config: {e}")

    with col_run:
        if st.button("🔁 Run Rebalance with New Config", use_container_width=True, key="cfg_run_btn"):
            with st.spinner("Saving config and running full pipeline..."):
                try:
                    # Save current form values first
                    new_cfg = {
                        "INPUT_FILE": current_cfg["INPUT_FILE"],
                        "OUTPUT_FILE": current_cfg["OUTPUT_FILE"],
                        "WINDOW_6M": cfg_window_6m,
                        "WINDOW_3M": cfg_window_3m,
                        "ANNUALIZE": current_cfg["ANNUALIZE"],
                        "TOP_N": cfg_top_n,
                        "TOP_N_PARTIAL": cfg_top_n_partial,
                        "MAX_DRAWDOWN_FROM_HIGH": cfg_max_dd,
                        "SHARPE_W6M": cfg_sharpe_w6m,
                        "SHARPE_W3M": cfg_sharpe_w3m,
                        "R2_W6M": current_cfg["R2_W6M"],
                        "R2_W3M": current_cfg["R2_W3M"],
                        "REGIME_TICKER": cfg_regime_ticker.strip(),
                        "REGIME_FALLBACKS": [t.strip() for t in cfg_fallbacks.split(",") if t.strip()],
                        "TREND_FAST_EMA_WINDOW": cfg_fast_ema,
                        "TREND_EMA_WINDOW": cfg_slow_ema,
                        "SECTOR_CAP": cfg_sector_cap,
                        "DAILY_RF_ANNUAL": cfg_rf_annual,
                        "TSL_THRESHOLD": cfg_tsl,
                    }
                    emr.save_config_to_json(new_cfg, SCRIPT_DIR)
                    emr._apply_json_config(emr.CONFIG, new_cfg)

                    # Run pipeline
                    run_path = st.session_state.get("uploaded_input_path") or str(SCRIPT_DIR / emr.CONFIG.INPUT_FILE)
                    emr.run_pipeline(run_path)
                    st.success("✅ Rebalance complete! Refresh the page to see updated results.")
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()

    # ── Current config summary ─────────────────────────────
    st.markdown("### 📄 Current Config File")
    cfg_path = SCRIPT_DIR / "strategy_config.json"
    if cfg_path.exists():
        with open(cfg_path) as f:
            st.code(f.read(), language="json")
    else:
        st.info("No `strategy_config.json` found — using hardcoded defaults.")
