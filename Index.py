import streamlit as st

st.set_page_config(
    page_title="Hello",
    page_icon="👋",
)

st.write("# Welcome! 👋")

st.markdown(
    """
    Here's a brief description for each of the pages available in the sidebar

    1. Index - The starting point
    2. Retirement Corpus Calculator - Calculates and shows how far into retirement does your corpus last.
    3. Goal Planning - Shows amount of annual SIP needed to achieve a future financial goal
    4. Dual Momentum Strategy - An implementation of the classical dual mommentum trading strategy, with Nifty 50, Nifty Next50, and Gold ETFs. 
"""
)