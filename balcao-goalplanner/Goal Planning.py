# goalPlanning.py

import streamlit as st
import pandas as pd
import datetime as dt
import numpy_financial as npf
import plotly.graph_objects as go
import sqlalchemy
import traceback

engine = sqlalchemy.create_engine(st.secrets["DB_URL"])

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

def validate_inputs(goal_value_today, inflation_rate, years_to_goal, rate_inv_return, initial_corpus):
    """Validate input parameters"""
    if goal_value_today <= 0:
        st.error("Goal value must be positive")
        return False
    if inflation_rate < 0 or inflation_rate > 100:
        st.error("Inflation rate must be between 0% and 100%")
        return False
    if years_to_goal <= 0:
        st.error("Years to goal must be positive")
        return False
    if rate_inv_return <= 0 or rate_inv_return > 100:
        st.error("Rate of return must be between 0% and 100%")
        return False
    if initial_corpus < 0:
        st.error("Initial corpus cannot be negative")
        return False
    return True

with st.form("goal_form"):
    goal_value_today = safe_float(st.text_input("Value of financial goal in today's INR", value="2400000"), 2400000)
    inflation_rate = safe_float(st.text_input("Rate of goal value inflation to be considered (%)", value="6"), 6)
    years_to_goal = safe_int(st.text_input("Number of Years Left to build corpus", value="16"), 16)
    rate_inv_return = safe_float(st.text_input("Expected annual rate of return on investments", value="10"), 10)
    initial_corpus = safe_float(st.text_input("Initial Corpus Available (INR)", value="0"), 0)
    submitted = st.form_submit_button("Calculate Goal Plan")

if submitted:
    # Validate inputs
    if not validate_inputs(goal_value_today, inflation_rate, years_to_goal, rate_inv_return, initial_corpus):
        st.stop()
    
    # Calculate future value of goal
    future_goal_value = round(goal_value_today * ((1 + inflation_rate/100) ** years_to_goal), 2)
    st.success(f"Corpus needed - INR {future_goal_value:,.2f}")
    
    # Calculate annual SIP for remaining corpus
    future_value_of_initial = initial_corpus * ((1 + rate_inv_return/100) ** years_to_goal)
    remaining_corpus_needed = max(future_goal_value - future_value_of_initial, 0)
    
    if remaining_corpus_needed > 0:
        annual_sip = float(npf.pmt(rate_inv_return/100, years_to_goal, 0, -round(remaining_corpus_needed), when='end'))
        st.info(f"Annual SIP to be invested to achieve goal - INR {annual_sip:,.2f}")

        # Glide path DataFrame - corrected calculation
        corpus_values = [initial_corpus]
        progress_bar = st.progress(0)
        for i in range(1, years_to_goal + 1):
            prev = corpus_values[-1]
            # SIP is invested at the end of the year, so it doesn't earn interest in the current year
            new_val = prev * (1 + rate_inv_return/100) + annual_sip
            corpus_values.append(new_val)
            progress_bar.progress(i / years_to_goal)
        progress_bar.empty()
        
        df = pd.DataFrame({
            "YEAR": list(range(0, years_to_goal + 1)),
            "CORPUS VALUE": corpus_values
        })

        # Plotly chart for Glide Path
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["YEAR"],
            y=df["CORPUS VALUE"],
            mode="lines+markers",
            name="Corpus Growth",
            line=dict(color="blue"),
            marker=dict(symbol="circle")
        ))
        fig.add_trace(go.Scatter(
            x=[df["YEAR"].min(), df["YEAR"].max()],
            y=[future_goal_value, future_goal_value],
            mode="lines",
            name="Goal Corpus",
            line=dict(color="red", dash="dash")
        ))
        fig.update_layout(
            title="Glide Path to Goal Corpus",
            xaxis_title="Year",
            yaxis_title="Corpus Value (INR)",
            legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
            template="plotly_white",
            hovermode="x unified",
            margin=dict(l=40, r=40, t=60, b=40)
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        annual_sip = 0
        st.info("No additional investment needed! Your initial corpus is sufficient.")
        # Do not display the graph

    # Remove Save Plan functionality
    # (No Save Plan button, form, or DB logic)
