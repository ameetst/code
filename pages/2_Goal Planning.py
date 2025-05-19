# goalPlanning.py

import streamlit as st
import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt
import numpy_financial as npf

st.markdown("## How To Use This Tool")

st.markdown("""
ABOUT - This page will calculate and provide the annual SIP amount to be invested towards a future goal.
             
INPUTS           
1) Goal Value Today - Value of financial goal as of today
2) Inflation Rate - Expected annual rate of inflation (default is 6%)
3) Years To Goal - # of Years Left to Start of Goal 
4) Annual Rate of Return on Investments 

OUTPUT
            
1) Corpus in INR required to achieve goal in the future
2) Annual investment needed to build corpus to achieve goal
3) Plot of glide path to build corpus
            
""")

goal_value_today = float(st.text_input("Value of financial goal in today's INR",value = 2400000))
inflation_rate = float(st.text_input("Rate of goal value inflation to be considered (%)", value = 10))
years_to_goal = float(st.text_input("Number of Years Left to build corpus", value = 16))
rate_inv_return = float(st.text_input("Expected annual rate of return on investments", value = 10))

if (goal_value_today and inflation_rate and years_to_goal):
    future_goal_value = round(goal_value_today*((1+inflation_rate/100)**(years_to_goal)),2)
    st.text("Corpus needed - INR " + str(future_goal_value))
    annual_sip = npf.pmt(rate_inv_return/100,years_to_goal,0,-future_goal_value, 0)
    st.text("Annual SIP to be invested to achieve goal - INR " + str(annual_sip))
else:
    future_goal_value = 0
    annual_sip = 0
