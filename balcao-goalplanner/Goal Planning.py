# goalPlanning.py

import streamlit as st
import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt
import numpy_financial as npf
import numpy as np
import io
import plotly.graph_objects as go
import sqlalchemy  # Make sure 'sqlalchemy' is in requirements.txt
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

    # Inline Save Plan input method
    if 'show_save_inline' not in st.session_state:
        st.session_state['show_save_inline'] = False

    if st.button("Save Plan"):
        st.session_state['show_save_inline'] = True

    if st.session_state.get('show_save_inline', False):
        with st.form("save_plan_form"):
            now = dt.datetime.now()
            default_plan_name = f"Plan Name_{now.strftime('%d%m%y')}_{now.strftime('%H%M%S')}"
            plan_name = st.text_input("Enter a name for your plan:", value=default_plan_name, key="plan_name_inline")
            save_submitted = st.form_submit_button("Save To DB")
            if save_submitted:
                try:
                    with engine.begin() as conn:
                        conn.execute(
                            sqlalchemy.text(
                                '''
                                INSERT INTO goal_plans
                                (plan_name, goal_value_today, inflation_rate, years_to_goal, rate_inv_return, initial_corpus, future_goal_value, annual_sip)
                                VALUES (:plan_name, :goal_value_today, :inflation_rate, :years_to_goal, :rate_inv_return, :initial_corpus, :future_goal_value, :annual_sip)
                                '''
                            ),
                            {
                                "plan_name": plan_name,
                                "goal_value_today": goal_value_today,
                                "inflation_rate": inflation_rate,
                                "years_to_goal": years_to_goal,
                                "rate_inv_return": rate_inv_return,
                                "initial_corpus": initial_corpus,
                                "future_goal_value": future_goal_value,
                                "annual_sip": annual_sip,
                            }
                        )
                    st.success(f"Plan '{plan_name}' saved to database!")
                    st.session_state['show_save_inline'] = False
                except Exception as e:
                    st.error(f"Database insert failed: {e}")
                    st.text(traceback.format_exc())
