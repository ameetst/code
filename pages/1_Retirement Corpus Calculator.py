import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt

# constants initialisation
inflation_rate = 0.06
equity_returns = 0.12
debt_returns = 0.06
years_in_retirement = 30
annual_expense = 1200000
debt_pf_value = 10000000
equity_pf_value = 10000000

st.markdown("## How To Use This Tool")

st.markdown("""
Using this tool, you can calculate the required retirement corpus by providing a few inputs. 
These are as follows (default value mentioned in brackets):
            
1) Inflation Rate - Expected rate of inflation in the retirement years (6%)
2) Equity Rate of Return - Expected rate of returns for equity invesments (12%)
3) Debt Rate of Returns - Expexted rate of returns for debt investments in retirement (6%)
4) Years in Retirement - # of Years in Retirement (30)
5) Annual Expenses - Estimated annual expense estimated at time of retirement (INR 1.2m)
6) Debt Portfolio - Estimated value of debt portfolio at time of retirement (INR 10m)
7) Equity Portfolio - Estimated value of equity portfolio at time of retirement (INR 10m) 

Output
            
The ouput of the calculation consists of two components which are displayed in two tabs:
            
1) Chart - This is a plot of time (years in retirement, on X axis) against corpus (amount in INR, on Y axis). Reading from left to write, the corpus 
will get consumed over time and trend towards the X axis. 
            
2) Data - This is the calculated figures that are used to plot the graph, and available for download 

""")
            
def run_calculations():
    
    try:
        debt_withdrawal = np.round(float(debt_pf_value)/(float(equity_pf_value)+float(debt_pf_value)),2)
    except:
        debt_withdrawal = 0.4

    equity_withdrawal = 1 - debt_withdrawal

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
            "TOTAL CORPUS REMAINING" : float(debt_pf_value + equity_pf_value)
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

    # df2.to_csv("Template_"+str(dt.date.today())+".csv",index=False)
    return df2
# End of Calculation =====================================

if 'df2' not in st.session_state:
    st.session_state.df2 = run_calculations()
st.session_state.df2 = run_calculations()

with st.form("input_form"):
    inflation_rate = int(st.text_input("Inflation Rate in post retirement years (in %)", value = 6))
    equity_returns = int(st.text_input("Estimated rate of equity returns in retirement (in %)", value = 12))
    debt_returns = int(st.text_input("Estimated rate of debt returns in retirement (in %)", value = 6))
    years_in_retirement = int(st.text_input("Years in retirement",value = 50))
    annual_expense = st.text_input("Estimated annual expense estimated at time of retirement",value = annual_expense)
    debt_pf_value = int(st.text_input("Estimated value of debt portfolio at time of retirement",value = debt_pf_value))
    equity_pf_value = int(st.text_input("Estimated value of equity portfolio at time of retirement",value = equity_pf_value))

    st.form_submit_button("Update Chart")

# Start of Calculation =====================================

tab1, tab2 = st.tabs(["Chart", "Data"])

with tab1:
    st.line_chart(pd.DataFrame(st.session_state.df2["TOTAL CORPUS REMAINING"],st.session_state.df2["YEAR"]), height=500, width=1500)

with tab2:
    st.dataframe(st.session_state.df2,height=500, width=1500, hide_index=True)
