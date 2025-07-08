# goalPlanning.py

import streamlit as st
import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt
import numpy_financial as npf
import numpy as np
import io

st.markdown("## Goal Planning Tool")

st.markdown("""
ABOUT - This page will calculate and provide the annual SIP amount to be invested towards a future goal.

**INPUTS**           
1) Goal Value Today - Value of financial goal as of today
2) Inflation Rate - Expected annual rate of inflation (default is 6%)
3) Years To Goal - # of Years Left to Start of Goal 
4) Annual Rate of Return on Investments 

**OUTPUT**
1) Corpus in INR required to achieve goal in the future
2) Annual investment needed to build corpus to achieve goal
3) Plot of glide path to build corpus
""")

def safe_float(val, default):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def safe_int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

with st.form("goal_form"):
    goal_value_today = safe_float(st.text_input("Value of financial goal in today's INR", value="2400000"), 2400000)
    inflation_rate = safe_float(st.text_input("Rate of goal value inflation to be considered (%)", value="10"), 10)
    years_to_goal = safe_int(st.text_input("Number of Years Left to build corpus", value="16"), 16)
    rate_inv_return = safe_float(st.text_input("Expected annual rate of return on investments", value="10"), 10)
    initial_corpus = safe_float(st.text_input("Initial Corpus Available (INR)", value="0"), 0)
    submitted = st.form_submit_button("Calculate Goal Plan")

if submitted:
    # Calculate future value of goal
    future_goal_value = round(goal_value_today * ((1 + inflation_rate/100) ** years_to_goal), 2)
    st.success(f"Corpus needed - INR {future_goal_value:,.2f}")
    # Calculate annual SIP for remaining corpus
    remaining_corpus_needed = max(future_goal_value - initial_corpus * ((1 + rate_inv_return/100) ** years_to_goal), 0)
    annual_sip = float(npf.pmt(rate_inv_return/100, years_to_goal, 0, -int(remaining_corpus_needed), when='end'))
    st.info(f"Annual SIP to be invested to achieve goal - INR {annual_sip:,.2f}")

    # Glide path DataFrame
    corpus_values = [initial_corpus]
    for i in range(1, years_to_goal + 1):
        prev = corpus_values[-1]
        new_val = float(prev * (1 + rate_inv_return/100) + annual_sip)
        corpus_values.append(new_val)
    df = pd.DataFrame({
        "YEAR": list(range(0, years_to_goal + 1)),
        "CORPUS VALUE": corpus_values
    })

    # Matplotlib chart
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(df["YEAR"], df["CORPUS VALUE"], label="Corpus Growth", color="blue", marker='o')
    ax.axhline(future_goal_value, color='red', linestyle='--', label='Goal Corpus')
    ax.set_title("Glide Path to Goal Corpus")
    ax.set_xlabel("Year")
    ax.set_ylabel("Corpus Value (INR)")
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.6)
    st.pyplot(fig)

    # Download chart as PNG
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    st.download_button(
        label="Download Chart as PNG",
        data=buf.getvalue(),
        file_name="goal_glide_path.png",
        mime="image/png"
    )

    # Download data as CSV
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Download Data as CSV",
        data=csv,
        file_name="goal_glide_path.csv",
        mime="text/csv"
    )
