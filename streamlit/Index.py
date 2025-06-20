import streamlit as st

st.set_page_config(
    page_title="Financial Planning Suite",
    page_icon="💰",
    layout="centered"
)

# Custom CSS for background and card style
st.markdown("""
    <style>
    .main {
        background: linear-gradient(135deg, #e0eafc 0%, #cfdef3 100%);
    }
    .card {
        background: white;
        border-radius: 1rem;
        box-shadow: 0 4px 24px 0 rgba(0,0,0,0.10);
        padding: 2rem;
        margin-bottom: 2rem;
    }
    .big-title {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1a237e;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        font-size: 1.2rem;
        color: #3949ab;
        margin-bottom: 2rem;
    }
    </style>
""", unsafe_allow_html=True)

st.markdown('<div class="big-title">💰 Financial Planning Suite</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Your one-stop solution for financial goal planning, retirement, and smart investing.</div>', unsafe_allow_html=True)

st.markdown("""
### 📊 Available Tools

- **Retirement Corpus Calculator**  
  _Calculate and visualize how long your retirement corpus will last._

- **Goal Planning**  
  _Find out the annual SIP needed to achieve your future financial goals._

- **Dual Momentum Strategy**  
  _Explore a classical dual momentum trading strategy with Indian ETFs._

---
""")

st.markdown('<span style="color: #616161;">Select a tool from the sidebar to get started!</span>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)