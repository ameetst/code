import pandas as pd
import datetime as dt
import numpy as np
import matplotlib.pyplot as plt

# constants
inflation_rate = 0.06
equity_returns = 0.12
debt_returns = 0.06
debt_allocation = 0.4
equity_allocation = 1 - debt_allocation
user_inp = {}

def gather_inputs() :
    user_inp = {}
    #user_inp["annual_expense"] = int(input("Enter annual expense estimated at time of retirement"))
    user_inp["annual_expense"] = 1200000
    user_inp["monthly_expense"] = user_inp["annual_expense"] / 12

    #user_inp["debt_pf_value"] = int(input("Enter value of debt portfolio at start of retirement"))
    #user_inp["equity_pf_value"] = int(input("Enter value of equity portfolio at start of retirement"))

    user_inp["debt_pf_value"] = 4000000
    user_inp["equity_pf_value"] = 6000000

    #user_inp["years_in_retirement"] = int(input("Enter number of years to be spent in retirement"))
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
        "DEBT PF YR START" : 0.0, 
        "EQUITY PF YR START" : 0.0,
        "DEBT PF YEAR END" : 0.0,
        "EQUITY PF YEAR END" : 0.0
    }
)
    
debt_pf_value_upd = user_inp["debt_pf_value"]
equity_pf_value_upd = user_inp["equity_pf_value"]

for index, row in df2.iterrows():    
    df2.loc[index,"DEBT PF YR START"] = debt_pf_value_upd
    df2.loc[index,"EQUITY PF YR START"] = equity_pf_value_upd

    df2.loc[index,"DEBT PF YEAR END"] = np.round((df2["DEBT PF YR START"].iloc[index]-(debt_allocation*df2["ANNUAL EXPENSE"].iloc[index]))*(1+debt_returns),2)
    df2.loc[index,"EQUITY PF YEAR END"] = np.round((df2["EQUITY PF YR START"].iloc[index]-(equity_allocation*df2["ANNUAL EXPENSE"].iloc[index]))*(1+equity_returns),2)

    debt_pf_value_upd = df2.loc[index,"DEBT PF YEAR END"]
    equity_pf_value_upd = df2.loc[index,"EQUITY PF YEAR END"]
     
df2.to_csv("Template_"+str(dt.date.today())+".csv",index=False)

# Plot
plt.plot(df2['YEAR'], df2['EQUITY PF YEAR END'])
plt.title('Utilisation of Retirement Corpus')
plt.xlabel('Retirement Years')
plt.ylabel('Corpus')
plt.grid(True)
#plt.show()