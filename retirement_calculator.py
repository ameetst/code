import pandas as pd
import datetime as dt
import numpy as np
import matplotlib.pyplot as plt

# constants
inflation_rate = 0.06
equity_returns = 0.12
debt_returns = 0.06
debt_withdrawal = 0.4
equity_withdrawal = 1 - debt_withdrawal
user_inp = {}

def gather_inputs() :
    user_inp = {}
    #user_inp["annual_expense"] = int(input("Enter annual expense estimated at time of retirement - "))
    user_inp["annual_expense"] = 1200000
    user_inp["monthly_expense"] = user_inp["annual_expense"] / 12

    #user_inp["debt_pf_value"] = int(input("Enter value of debt portfolio at start of retirement - "))
    #user_inp["equity_pf_value"] = int(input("Enter value of equity portfolio at start of retirement - "))

    user_inp["debt_pf_value"] = 10000000
    user_inp["equity_pf_value"] = 25000000

    #user_inp["years_in_retirement"] = int(input("Enter number of years to be spent in retirement - "))
    user_inp["years_in_retirement"] = 50

    return user_inp

user_inp = gather_inputs()

# Calculating retirement

# annual expenses
expenses = np.round(user_inp["annual_expense"] * (1 + inflation_rate) ** np.arange(user_inp["years_in_retirement"]),2)

df2 = pd.DataFrame(
    {
        "YEAR" : list(range(1,user_inp["years_in_retirement"]+1)),
        "ANNUAL EXPENSE" : expenses,
        "MONTHLY EXPENSE" : np.round(expenses / 12,2),
        "AMT TO WDRAW FROM DEBT" : 0.0,
        "AMT TO WDRAW FROM EQTY" : 0.0,
        "DEBT PF YEAR END" : 0.0,
        "EQUITY PF YEAR END" : 0.0,
        "TOTAL CORPUS REMAINING" : 0.0  
    }
)
    
df2["AMT TO WDRAW FROM DEBT"] = np.round(debt_withdrawal * df2["ANNUAL EXPENSE"],2)
df2["AMT TO WDRAW FROM EQTY"] = np.round(equity_withdrawal * df2["ANNUAL EXPENSE"],2)

df2.loc[0,"DEBT PF YEAR END"] = np.round(user_inp["debt_pf_value"] - df2.loc[0,"AMT TO WDRAW FROM DEBT"],2)
df2.loc[0,"EQUITY PF YEAR END"] = np.round(user_inp["equity_pf_value"] - df2.loc[0,"AMT TO WDRAW FROM EQTY"],2)
df2.loc[0,"TOTAL CORPUS REMAINING"] = df2.loc[0,"DEBT PF YEAR END"] + df2.loc[0,"EQUITY PF YEAR END"]

for i in range(1,len(df2)) :
    df2.loc[i,"DEBT PF YEAR END"] = np.round(df2.loc[i-1,"DEBT PF YEAR END"] - df2.loc[i,"AMT TO WDRAW FROM DEBT"],2)
    df2.loc[i,"EQUITY PF YEAR END"] = np.round(df2.loc[i-1,"EQUITY PF YEAR END"] - df2.loc[i,"AMT TO WDRAW FROM EQTY"],2)
    df2.loc[i,"TOTAL CORPUS REMAINING"] = df2.loc[i,"DEBT PF YEAR END"] + df2.loc[i,"EQUITY PF YEAR END"]
    if(df2.loc[i,"TOTAL CORPUS REMAINING"] <= 0):
        df2.loc[i,"TOTAL CORPUS REMAINING"] = 0
        break

df2.to_csv("Template_"+str(dt.date.today())+".csv",index=False)

# Plot
plt.plot(df2['YEAR'], df2['TOTAL CORPUS REMAINING'])
plt.title('Corpus Burndown')
plt.xlabel('Retirement Years')
plt.ylabel('Corpus')
plt.grid(True)
plt.show()