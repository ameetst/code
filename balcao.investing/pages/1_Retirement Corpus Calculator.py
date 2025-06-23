import streamlit as st
import pandas as pd
import numpy as np
import datetime as dt
import matplotlib.pyplot as plt

def run_calculations(inflation_rate, equity_returns, debt_returns, years_in_retirement, annual_expense, debt_pf_value, equity_pf_value):
    df2 = []
    # Add initial corpus at year 0 (before any withdrawal)
    df2.append({
        "YEAR": 0,
        "ANNUAL EXPENSE": 0,
        "MONTHLY EXPENSE": 0,
        "AMT TO WDRAW FROM DEBT": 0,
        "AMT TO WDRAW FROM EQTY": 0,
        "DEBT PF YEAR END": debt_pf_value,
        "EQUITY PF YEAR END": equity_pf_value,
        "TOTAL CORPUS REMAINING": debt_pf_value + equity_pf_value
    })
    for year in range(1, int(years_in_retirement) + 1):
        debt = df2[-1]['DEBT PF YEAR END']
        equity = df2[-1]['EQUITY PF YEAR END']
        total = debt + equity
        if total <= 0:
            break
        expense = annual_expense * ((1 + inflation_rate) ** (year - 1))
        debt_withdrawal = round(debt / total, 2) if total > 0 else 0.4
        equity_withdrawal = 1 - debt_withdrawal
        amt_debt = round(debt_withdrawal * expense, 2)
        amt_equity = round(equity_withdrawal * expense, 2)
        debt_end = max(0, (debt - amt_debt) * (1 + debt_returns))
        equity_end = max(0, (equity - amt_equity) * (1 + equity_returns))
        total_end = debt_end + equity_end
        df2.append({
            "YEAR": year,
            "ANNUAL EXPENSE": round(expense, 2),
            "MONTHLY EXPENSE": round(expense / 12, 2),
            "AMT TO WDRAW FROM DEBT": amt_debt,
            "AMT TO WDRAW FROM EQTY": amt_equity,
            "DEBT PF YEAR END": round(debt_end, 2),
            "EQUITY PF YEAR END": round(equity_end, 2),
            "TOTAL CORPUS REMAINING": round(total_end, 2)
        })
        if total_end <= 0:
            break
    return pd.DataFrame(df2)

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

# Default values
DEFAULTS = {
    "inflation_rate": 6.0,
    "equity_returns": 12.0,
    "debt_returns": 6.0,
    "years_in_retirement": 30,
    "annual_expense": 1200000,
    "debt_pf_value": 10000000,
    "equity_pf_value": 10000000
}

st.markdown("## Retirement Corpus Calculator")
st.markdown("""
**How To Use This Tool**

- **Inflation Rate (%):** Expected annual inflation during retirement.
- **Equity Returns (%):** Expected annual return on equity corpus during retirement.
- **Debt Returns (%):** Expected annual return on debt corpus during retirement.
- **Years in Retirement:** Number of years you expect to be in retirement.
- **Annual Expenses (INR):** Estimated annual expenses at the start of retirement.
- **Debt Portfolio (INR):** Starting corpus in debt investments at retirement.
- **Equity Portfolio (INR):** Starting corpus in equity investments at retirement.

**Outputs:**
- **Chart:** Corpus value over time (years in retirement).
- **Data:** Table of yearly corpus, expenses, and withdrawals. Downloadable as CSV.
- **Summary:** How many years your corpus lasts.
""")

with st.form("input_form"):
    inflation_rate = st.slider("Inflation Rate during retirement (%)", min_value=0.0, max_value=20.0, value=DEFAULTS["inflation_rate"], step=0.1) / 100
    equity_returns = st.slider("Expected equity returns during retirement (%)", min_value=0.0, max_value=20.0, value=DEFAULTS["equity_returns"], step=0.1) / 100
    debt_returns = st.slider("Expected debt returns during retirement (%)", min_value=0.0, max_value=20.0, value=DEFAULTS["debt_returns"], step=0.1) / 100
    years_in_retirement = st.slider("Years in retirement", min_value=0, max_value=50, value=DEFAULTS["years_in_retirement"], step=1)
    annual_expense = safe_float(st.text_input("Annual expenses at start of retirement (INR)", value=str(DEFAULTS["annual_expense"])), DEFAULTS["annual_expense"])
    debt_pf_value = safe_float(st.text_input("Debt portfolio at start of retirement (INR)", value=str(DEFAULTS["debt_pf_value"])), DEFAULTS["debt_pf_value"])
    equity_pf_value = safe_float(st.text_input("Equity portfolio at start of retirement (INR)", value=str(DEFAULTS["equity_pf_value"])), DEFAULTS["equity_pf_value"])
    submitted = st.form_submit_button("Update Chart")

if "df2" not in st.session_state:
    st.session_state.df2 = run_calculations(
        inflation_rate, equity_returns, debt_returns, years_in_retirement, annual_expense, debt_pf_value, equity_pf_value
    )

if submitted:
    st.session_state.df2 = run_calculations(
        inflation_rate, equity_returns, debt_returns, years_in_retirement, annual_expense, debt_pf_value, equity_pf_value
    )

df2 = st.session_state.df2

tab3, tab1, tab2 = st.tabs(["Summary", "Chart", "Data"])

with tab1:
    if not df2.empty:
        df_plot = df2.set_index("YEAR")
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(df_plot.index, df_plot["TOTAL CORPUS REMAINING"], marker='o', color='tab:blue', label='Corpus Remaining')
        ax.set_xlabel("Year in Retirement")
        ax.set_ylabel("Corpus Remaining (INR)")
        ax.set_title("Retirement Corpus Depletion Over Time")
        ax.grid(True, linestyle='--', alpha=0.6)
        # Highlight depletion point
        depleted = df_plot["TOTAL CORPUS REMAINING"] == 0
        if depleted.any():
            first_depleted = df_plot[depleted].index.tolist()[0]
            ax.axvline(first_depleted, color='red', linestyle=':', label='Corpus Depleted')
        ax.legend()
        st.pyplot(fig)
    else:
        st.warning("No data to plot. Please check your inputs.")

with tab2:
    if not df2.empty:
        st.dataframe(df2.iloc[1:], height=500, width=1500, hide_index=True)
        csv = df2.iloc[1:].to_csv(index=False).encode('utf-8')
        st.download_button("Download Data as CSV", data=csv, file_name="retirement_corpus_projection.csv", mime="text/csv")

with tab3:
    if not df2.empty:
        years_lasted = df2["YEAR"].iloc[-1] + 1
        st.markdown(f"**Your corpus lasts for:** {years_lasted} year(s)")
        if df2["TOTAL CORPUS REMAINING"].iloc[-1] > 0:
            st.success("Your corpus lasts the entire planned retirement period!")
        else:
            st.error("Your corpus is depleted before the end of the planned retirement period.")
    else:
        st.warning("No summary available. Please check your inputs.")
