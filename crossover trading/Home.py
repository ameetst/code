import streamlit as st

st.set_page_config(page_title="Algorithmic Trading Dashboard", layout="wide", page_icon="📈")

st.title("📈 Algorithmic Trading Dashboard")
st.markdown("### Welcome to your unified Quantitative Trading System!")

st.write("---")

col1, col2 = st.columns(2)

with col1:
    st.info("### 🔄 EMA Crossover System")
    st.write("A medium-to-long term trend following strategy that identifies major multi-month trends using the 50/200 EMA Golden Cross.")
    st.markdown("- **Lookback Window:** Scans for exact crossover dates")
    st.markdown("- **Proximity Filter:** Ensures price is near 52-week highs")
    st.markdown("- **Trade Management:** Integrated Portfolio Journal")
    st.write("")
    if st.button("Launch Crossover System ➡️", use_container_width=True):
        st.switch_page("pages/1_EMA_Crossover.py")

with col2:
    st.success("### 🚀 Momentum Breakout Strategy")
    st.write("An aggressive, momentum-based breakout scanner that hunts for stocks breaking 3-month highs with massive volume surges.")
    st.markdown("- **Regime Filter:** Only runs during Nifty 500 Bull Markets")
    st.markdown("- **Volume Check:** Requires 1.5x volume expansion")
    st.markdown("- **Signal Ranking:** Ranks by 3-Month Relative Strength")
    st.write("")
    if st.button("Launch Breakout Strategy ➡️", use_container_width=True):
        st.switch_page("pages/2_Breakout_Strategy.py")

st.write("---")
st.caption("👈 **You can also use the sidebar on the left to navigate seamlessly between strategies.**")
