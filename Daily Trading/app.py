import streamlit as st
import pandas as pd
from data_engine import update_data, load_local_data
from strategy import check_regime, build_universe_table, run_screener
import datetime
import os

st.set_page_config(page_title="Daily Breakout Screener", layout="wide")

st.title("📈 Daily Breakout Trading Screener")

# ── Sidebar ──────────────────────────────────────────────────────────
st.sidebar.header("⚙️ Settings")
r2_threshold   = st.sidebar.slider("R-Squared Threshold", min_value=0.0, max_value=1.0, value=0.60, step=0.05)
vchk_threshold = st.sidebar.slider("VCHK Threshold",      min_value=0.5, max_value=5.0, value=1.5,  step=0.1)
rr6m_threshold = st.sidebar.slider("6M R/R Threshold",    min_value=0.5, max_value=5.0, value=1.5,  step=0.1)
p52h_threshold = st.sidebar.slider("P/52H Threshold",     min_value=0.5, max_value=1.0, value=0.85, step=0.01,
                                   help="Green when Close ≥ this % of 52-week high. 0.85 = within 15% of high.")
force_refresh  = st.sidebar.button("🔄 Run On-Demand Scan (Refresh Data)")

# Path logic
CSV_PATH = "ind_niftytotalmarket_list (3).csv"
DATA_PATH = "market_data.pkl"
INDEX_PATH = "index_data.pkl"

# ── Data Loading ─────────────────────────────────────────────────────
if force_refresh or not os.path.exists(DATA_PATH):
    with st.spinner("Downloading latest market data via yfinance... (This may take a few minutes)"):
        update_data(csv_path=CSV_PATH, lookback_years=1, save_path=DATA_PATH)
        market_data, index_data = load_local_data(DATA_PATH, INDEX_PATH)
        st.success("Data refreshed successfully!")
else:
    market_data, index_data = load_local_data(DATA_PATH, INDEX_PATH)

if market_data is None or index_data is None:
    st.error("Failed to load market data. Please run an On-Demand Scan.")
    st.stop()

# ── Data date (used throughout sidebar and staleness check) ──────────
data_date = index_data.index[-1].date()
today     = datetime.datetime.now().date()

st.sidebar.divider()
st.sidebar.markdown("📅 **Data as of**")
st.sidebar.markdown(f"### {data_date.strftime('%d %b %Y')}")
if today > data_date and datetime.datetime.now().hour > 16:
    st.sidebar.warning("⚠️ Market has closed. Refresh for today's data.")

# ── Market Regime ────────────────────────────────────────────────────
regime_bullish, nifty_close, nifty_ema = check_regime(index_data)

col1, col2, col3 = st.columns(3)
with col1:
    if regime_bullish:
        st.success("**Market Regime: BULLISH** ✅")
    else:
        st.error("**Market Regime: BEARISH** 🛑  New entries blocked.")
with col2:
    st.metric("Nifty 500 Close", f"{nifty_close:,.2f}")
with col3:
    st.metric("Nifty 500 50EMA", f"{nifty_ema:,.2f}")

st.divider()

# ── Build Universe Table ─────────────────────────────────────────────
with st.spinner("Calculating indicators for all stocks..."):
    universe_df = build_universe_table(market_data, index_data=index_data)

# ── Tabs ─────────────────────────────────────────────────────────────
tab_signals, tab_data = st.tabs(["🎯 Buy Signals", "📊 All Stocks (DATA)"])

# ── Tab 1: Filtered Buy Signals ─────────────────────────────────────
with tab_signals:
    if regime_bullish:
        with st.spinner("Running screener filters..."):
            signals_df = run_screener(
                universe_df,
                r2_threshold=r2_threshold,
                regime_bullish=regime_bullish,
            )

        st.subheader(f"Stocks passing all criteria: {len(signals_df)}")

        if not signals_df.empty:
            display_sig = signals_df.copy()
            display_sig['Price'] = display_sig['Price'].round(2)
            display_sig['50EMA'] = display_sig['50EMA'].round(2)
            display_sig['200EMA'] = display_sig['200EMA'].round(2)
            display_sig['LOSS'] = display_sig['LOSS'].round(2)
            display_sig['P/52H'] = display_sig['P/52H'].map(lambda x: f"{x:.1%}")
            display_sig['3M BO'] = (display_sig['3M BO'] * 100).round(1).astype(str) + "%"
            display_sig['6M BO'] = (display_sig['6M BO'] * 100).round(1).astype(str) + "%"
            display_sig['VCHK'] = display_sig['VCHK'].round(2)
            display_sig['R-Squared'] = display_sig['R-Squared'].round(3)
            if 'INR_VOL' in display_sig.columns:
                display_sig['INR_VOL'] = (display_sig['INR_VOL'] / 1e7).round(2).astype(str) + " Cr"
            st.dataframe(display_sig, use_container_width=True, hide_index=True)
        else:
            st.info("No stocks met all entry criteria today.")
    else:
        st.warning("Market Regime is Bearish. Screening for new entries is skipped.")

# ── Tab 2: Full Universe Data (matches Excel DATA sheet) ─────────────
with tab_data:
    st.subheader(f"All Stocks with Indicators ({len(universe_df)} stocks)")

    if not universe_df.empty:
        display_all = universe_df.copy()

        # ── INR_VOL: convert to crores before column rename ───────────
        if 'INR_VOL' in display_all.columns:
            display_all['INR_VOL (Cr)'] = display_all['INR_VOL'] / 1e7
            display_all = display_all.drop(columns=['INR_VOL'])

        # ── Column order ──────────────────────────────────────────────
        base_cols = ['Symbol', 'Price', 'P/52H', '50EMA', '200EMA',
                     '3M BO', '6M BO', 'VCHK', '3M R/R', '6M R/R',
                     '6M HIGH', 'LOSS', 'R-Squared', 'RS_3M']
        extra_cols = [c for c in ['INR_VOL (Cr)'] if c in display_all.columns]
        display_all = display_all[[c for c in base_cols + extra_cols if c in display_all.columns]]

        # ── Green condition masks ──────────────────────────────────────
        def build_green_masks(df, vchk_thr, rr6m_thr):
            masks = {}
            if 'Price' in df.columns and '50EMA' in df.columns and '200EMA' in df.columns:
                masks['Price']  = (df['Price'] > df['50EMA']) & (df['50EMA'] > df['200EMA']) & (df['200EMA'] > 0)
            if '50EMA' in df.columns and '200EMA' in df.columns:
                masks['50EMA']  = (df['50EMA'] > df['200EMA']) & (df['200EMA'] > 0)
            if 'P/52H' in df.columns:
                masks['P/52H']  = df['P/52H'] >= p52h_threshold
            if '3M BO' in df.columns:
                masks['3M BO']  = df['3M BO'] > 0
            if 'VCHK' in df.columns:
                masks['VCHK']   = df['VCHK'] > vchk_thr
            if '6M R/R' in df.columns:
                masks['6M R/R'] = df['6M R/R'] > rr6m_thr
            if 'R-Squared' in df.columns:
                masks['R-Squared'] = df['R-Squared'] > r2_threshold
            if 'RS_3M' in df.columns:
                masks['RS_3M'] = df['RS_3M'] > 0
            if 'INR_VOL (Cr)' in df.columns:
                masks['INR_VOL (Cr)'] = df['INR_VOL (Cr)'] >= 1.0
            return masks

        # ── Orange condition masks ─────────────────────────────────────
        # 6M BO < 0 = still below 6M high = room to run (ideal entry context)
        def build_orange_masks(df):
            masks = {}
            if '6M BO' in df.columns:
                masks['6M BO'] = df['6M BO'] < 0
            return masks

        green_masks  = build_green_masks(display_all, vchk_threshold, rr6m_threshold)
        orange_masks = build_orange_masks(display_all)

        # ── Column filter UI ──────────────────────────────────────────
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            st.caption("🟢 **Must be green:**")
            selected_green = st.multiselect(
                label="Must be green:",
                options=list(green_masks.keys()),
                default=[],
                key="data_tab_green_filter",
                label_visibility="collapsed",
                placeholder="Select columns…",
            )
        with fcol2:
            st.caption("🟡 **Must be orange:**")
            selected_orange = st.multiselect(
                label="Must be orange:",
                options=list(orange_masks.keys()),
                default=[],
                key="data_tab_orange_filter",
                label_visibility="collapsed",
                placeholder="Select columns…",
            )

        # Apply filters: rows must satisfy ALL selected green AND orange conditions
        filtered_df = display_all.copy()
        if selected_green or selected_orange:
            combined_mask = pd.Series(True, index=filtered_df.index)
            for col in selected_green:
                if col in green_masks:
                    combined_mask &= green_masks[col].reindex(filtered_df.index, fill_value=False)
            for col in selected_orange:
                if col in orange_masks:
                    combined_mask &= orange_masks[col].reindex(filtered_df.index, fill_value=False)
            filtered_df = filtered_df[combined_mask]

        st.caption(f"Showing **{len(filtered_df)}** of {len(display_all)} stocks")

        # ── Colour constants ──────────────────────────────────────────
        GREEN_BG  = 'background-color: #d4edda; color: #155724;'
        GREEN_BLD = 'background-color: #d4edda; color: #155724; font-weight: bold;'
        ORANGE_BG = 'background-color: #fff3cd; color: #856404;'

        # ── Conditional formatting ────────────────────────────────────
        def style_cells(row):
            """
            Green highlights — Strategy.md §4B:
              Price       — Rule 1:  Close > 50EMA > 200EMA
              50EMA       — Rule 1:  50EMA > 200EMA
              P/52H       — Ranking: >= 0.85 (within 15% of 52W high), bold
              3M BO       — Rule 2a: > 0 (broken out of 3M range)
              VCHK        — Rule 4:  > vchk_threshold (configurable)
              6M R/R      — Target:  > rr6m_threshold (configurable)
              R-Squared   — Rule 6:  > r2_threshold (configurable)
              INR_VOL (Cr)— Rule 5:  >= 1 Cr liquid
            Orange highlights:
              6M BO       — < 0: below 6M high = room to run
            """
            styles = {col: '' for col in row.index}
            try:
                price  = row.get('Price',        pd.NA)
                ema50  = row.get('50EMA',         pd.NA)
                ema200 = row.get('200EMA',        pd.NA)
                p52h   = row.get('P/52H',         pd.NA)
                bo3m   = row.get('3M BO',         pd.NA)
                bo6m   = row.get('6M BO',         pd.NA)
                vchk   = row.get('VCHK',          pd.NA)
                rr6m   = row.get('6M R/R',        pd.NA)
                r2     = row.get('R-Squared',     pd.NA)
                rs3m   = row.get('RS_3M',          pd.NA)
                inrvol = row.get('INR_VOL (Cr)',  pd.NA)

                if pd.notna(price) and pd.notna(ema50) and pd.notna(ema200):
                    if price > ema50 > ema200 > 0:
                        styles['Price'] = GREEN_BG

                if pd.notna(ema50) and pd.notna(ema200) and ema200 > 0:
                    if ema50 > ema200:
                        styles['50EMA'] = GREEN_BG

                if pd.notna(p52h) and p52h >= p52h_threshold:
                    styles['P/52H'] = GREEN_BLD

                if pd.notna(bo3m) and bo3m > 0:
                    styles['3M BO'] = GREEN_BG

                if pd.notna(bo6m) and bo6m < 0:
                    styles['6M BO'] = ORANGE_BG

                if pd.notna(vchk) and vchk > vchk_threshold:
                    styles['VCHK'] = GREEN_BG

                if pd.notna(rr6m) and rr6m > rr6m_threshold:
                    styles['6M R/R'] = GREEN_BG

                if pd.notna(r2) and r2 > r2_threshold:
                    styles['R-Squared'] = GREEN_BG

                if pd.notna(rs3m) and rs3m > 0:
                    styles['RS_3M'] = GREEN_BG

                if 'INR_VOL (Cr)' in row.index and pd.notna(inrvol) and inrvol >= 1.0:
                    styles['INR_VOL (Cr)'] = GREEN_BG

            except Exception:
                pass

            return pd.Series(styles)

        # ── Number format dict ────────────────────────────────────────
        fmt = {
            'Price':        '{:.2f}',
            'P/52H':        '{:.1%}',
            '50EMA':        '{:.2f}',
            '200EMA':       '{:.2f}',
            '3M BO':        '{:.1%}',
            '6M BO':        '{:.1%}',
            'VCHK':         '{:.2f}',
            '3M R/R':       '{:.2f}',
            '6M R/R':       '{:.2f}',
            '6M HIGH':      '{:.2f}',
            'LOSS':         '{:.2f}',
            'R-Squared':    '{:.3f}',
            'RS_3M':        '{:.1%}',
            'INR_VOL (Cr)': '{:.2f}',
        }
        fmt = {k: v for k, v in fmt.items() if k in filtered_df.columns}

        styled = filtered_df.style.format(fmt).apply(style_cells, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True, height=700)
    else:
        st.info("No stock data available. Run an On-Demand Scan.")
