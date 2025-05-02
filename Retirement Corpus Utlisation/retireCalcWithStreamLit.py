import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import matplotlib.pyplot as plt

# constants
inflation_rate = 0.06
equity_returns = 0.12
debt_returns = 0.06

constants = []
constants.append(
        {
            "inflation_rate" : 0.06,
            "equity_returns" : 0.12,
            "debt_returns" : 0.06
        }
    )
constants = pd.DataFrame(constants)

num_rows = st.slider("Years in Retirement", 30, 50)
years_in_retirement = int(num_rows)

annual_expense = 1200000
debt_pf_value = 10000000
equity_pf_value = 10000000

if st.toggle("Update Constants"):
    edited_data = st.data_editor(constants, use_container_width=True)
else:
    st.dataframe(constants, use_container_width=True)

annual_expense = st.text_input("Estimated annual expense estimated at time of retirement",value = annual_expense)
debt_pf_value = int(st.text_input("Estimated value of debt portfolio at time of retirement",value = debt_pf_value))
equity_pf_value = int(st.text_input("Estimated value of equity portfolio at time of retirement",value = equity_pf_value))

try:
    debt_withdrawal = np.round(float(debt_pf_value)/(float(equity_pf_value)+float(debt_pf_value)),2)
except:
    debt_withdrawal = 0.4

equity_withdrawal = 1 - debt_withdrawal

np.random.seed(42)

# Start of Calculation =====================================

expenses = np.round(int(annual_expense) * ((1 + inflation_rate) ** np.arange(years_in_retirement)),2)

df2 = pd.DataFrame(
    {
        "YEAR" : list(range(0,years_in_retirement)),
        "ANNUAL EXPENSE" : expenses,
        "MONTHLY EXPENSE" : np.round(expenses / 12,2),
        "AMT TO WDRAW FROM DEBT" : 0.0,
        "AMT TO WDRAW FROM EQTY" : 0.0,
        "DEBT PF YEAR END" : float(debt_pf_value),
        "EQUITY PF YEAR END" : float(equity_pf_value),
        "TOTAL CORPUS REMAINING" : debt_pf_value + equity_pf_value  
    }
)

df2["AMT TO WDRAW FROM DEBT"] = np.round(debt_withdrawal * df2["ANNUAL EXPENSE"],2)
df2["AMT TO WDRAW FROM EQTY"] = np.round(equity_withdrawal * df2["ANNUAL EXPENSE"],2)

df2.loc[1,"DEBT PF YEAR END"] = np.round(debt_pf_value - df2.loc[1,"AMT TO WDRAW FROM DEBT"],2)
df2.loc[1,"EQUITY PF YEAR END"] = np.round(equity_pf_value - df2.loc[1,"AMT TO WDRAW FROM EQTY"],2)
df2.loc[1,"TOTAL CORPUS REMAINING"] = df2.loc[1,"DEBT PF YEAR END"] + df2.loc[1,"EQUITY PF YEAR END"]

for i in range(2,len(df2)) :
    df2.loc[i,"DEBT PF YEAR END"] = np.round(df2.loc[i-1,"DEBT PF YEAR END"] - df2.loc[i,"AMT TO WDRAW FROM DEBT"],2)
    df2.loc[i,"EQUITY PF YEAR END"] = np.round(df2.loc[i-1,"EQUITY PF YEAR END"] - df2.loc[i,"AMT TO WDRAW FROM EQTY"],2)
    df2.loc[i,"TOTAL CORPUS REMAINING"] = df2.loc[i,"DEBT PF YEAR END"] + df2.loc[i,"EQUITY PF YEAR END"]
    if(df2.loc[i,"TOTAL CORPUS REMAINING"] <= 0):
        df2.loc[i,"TOTAL CORPUS REMAINING"] = 0
        df2 = df2.iloc[:i+1]
        break

df2.to_csv("Template_"+str(dt.date.today())+".csv",index=False)

# End of Calculation =====================================

tab1, tab2 = st.tabs(["Chart", "Data"])
tab1.line_chart(pd.DataFrame(df2["TOTAL CORPUS REMAINING"],df2["YEAR"]), height=500)
tab2.dataframe(pd.DataFrame(df2["TOTAL CORPUS REMAINING"],df2["YEAR"]), height=500, use_container_width=True)

