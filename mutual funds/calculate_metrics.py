import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import os

# Input file
input_csv = 'historical_nav_10_years.csv'

# Output files
output_csv = 'fund_metrics_summary.csv'
plots_dir = 'fund_plots'
os.makedirs(plots_dir, exist_ok=True)

# Risk-free rate
RFR = 0.065  # 6.5%

# Read data
df = pd.read_csv(input_csv)
df['Date'] = pd.to_datetime(df['Date'])
df = df.sort_values(['MF NAME', 'Date'])

# Function to calculate CAGR
def calculate_cagr(initial_nav, final_nav, years):
    if years > 0 and initial_nav > 0:
        return (final_nav / initial_nav) ** (1 / years) - 1
    return np.nan

# Function to calculate max drawdown
def calculate_max_drawdown(nav_series):
    peak = nav_series.expanding().max()
    drawdown = (nav_series - peak) / peak
    return drawdown.min()

# Function to calculate rolling CAGR (1-year)
def calculate_rolling_cagr(nav_series, window=252):
    returns = nav_series.pct_change()
    rolling_cagr = (1 + returns).rolling(window=window).apply(lambda x: (x.prod()) ** (252 / len(x)) - 1, raw=False)
    return rolling_cagr

# Function to calculate rolling CAGR (3-year)
def calculate_rolling_3yr_cagr(nav_series, window=756):  # ~3 years (252 * 3)
    returns = nav_series.pct_change()
    rolling_cagr = (1 + returns).rolling(window=window).apply(lambda x: (x.prod()) ** (252 / len(x)) - 1, raw=False)
    return rolling_cagr

# Function to calculate rolling CAGR (5-year)
def calculate_rolling_5yr_cagr(nav_series, window=1260):  # ~5 years (252 * 5)
    returns = nav_series.pct_change()
    rolling_cagr = (1 + returns).rolling(window=window).apply(lambda x: (x.prod()) ** (252 / len(x)) - 1, raw=False)
    return rolling_cagr

# Dictionary to store rolling CAGR data for top 5 funds
rolling_cagr_data = {}

# Group by MF NAME
results = []
for name, group in df.groupby('MF NAME'):
    nav = group.set_index('Date')['nav']
    if len(nav) < 252:  # Less than 1 year
        continue
    
    start_date = nav.index.min()
    end_date = nav.index.max()
    total_years = (end_date - start_date).days / 365.25
    
    # Overall metrics
    initial_nav = nav.iloc[0]
    final_nav = nav.iloc[-1]
    overall_cagr = calculate_cagr(initial_nav, final_nav, total_years)
    
    daily_returns = nav.pct_change().dropna()
    volatility = daily_returns.std() * np.sqrt(252)
    sharpe = (overall_cagr - RFR) / volatility if volatility > 0 else np.nan
    max_dd = calculate_max_drawdown(nav)
    
    # Period CAGRs
    cagr_1yr = np.nan
    cagr_3yr = np.nan
    cagr_5yr = np.nan
    
    end_dt = nav.index[-1]
    if len(nav) >= 252:
        nav_1yr = nav[nav.index >= end_dt - pd.DateOffset(days=365)]
        if len(nav_1yr) > 1:
            cagr_1yr = calculate_cagr(nav_1yr.iloc[0], nav_1yr.iloc[-1], 1)
    
    if len(nav) >= 252*3:
        nav_3yr = nav[nav.index >= end_dt - pd.DateOffset(days=1095)]
        if len(nav_3yr) > 1:
            cagr_3yr = calculate_cagr(nav_3yr.iloc[0], nav_3yr.iloc[-1], 3)
    
    if len(nav) >= 252*5:
        nav_5yr = nav[nav.index >= end_dt - pd.DateOffset(days=1825)]
        if len(nav_5yr) > 1:
            cagr_5yr = calculate_cagr(nav_5yr.iloc[0], nav_5yr.iloc[-1], 5)
    
    # Rolling CAGR calculations
    rolling_1yr = calculate_rolling_cagr(nav)
    rolling_3yr = calculate_rolling_3yr_cagr(nav)
    rolling_5yr = calculate_rolling_5yr_cagr(nav)
    
    # Store rolling data for later use in top 5 graphs
    rolling_cagr_data[name] = {
        'rolling_1yr': rolling_1yr,
        'rolling_3yr': rolling_3yr,
        'rolling_5yr': rolling_5yr,
        'nav_index': nav.index
    }
    
    results.append({
        'MF NAME': name,
        'Start Date': start_date.strftime('%Y-%m-%d'),
        'End Date': end_date.strftime('%Y-%m-%d'),
        'Total Years': round(total_years, 2),
        'Initial NAV': initial_nav,
        'Final NAV': final_nav,
        'Overall CAGR (%)': round(overall_cagr * 100, 2) if not np.isnan(overall_cagr) else np.nan,
        'Volatility (%)': round(volatility * 100, 2),
        'Sharpe Ratio': round(sharpe, 2) if not np.isnan(sharpe) else np.nan,
        'Max Drawdown (%)': round(max_dd * 100, 2),
        '1Y CAGR (%)': round(cagr_1yr * 100, 2) if not np.isnan(cagr_1yr) else np.nan,
        '3Y CAGR (%)': round(cagr_3yr * 100, 2) if not np.isnan(cagr_3yr) else np.nan,
        '5Y CAGR (%)': round(cagr_5yr * 100, 2) if not np.isnan(cagr_5yr) else np.nan
    })
    
    print(f"Processed: {name}")  # Progress indicator
    
    # Generate plots
    # Removed individual plots as per request

# Save results
results_df = pd.DataFrame(results)
results_df = results_df.sort_values('Overall CAGR (%)', ascending=False)
results_df.to_csv(output_csv, index=False)

# Top 5 funds
top5_df = results_df.head(5)
top5_csv = 'top5_funds.csv'
top5_df.to_csv(top5_csv, index=False)

print(f"Metrics calculated and saved to {output_csv}")
print(f"Top 5 funds saved to {top5_csv}")
print(f"Plots saved in {plots_dir}")
print("Top 5 funds by Overall CAGR:")
print(top5_df[['MF NAME', 'Overall CAGR (%)']])

# Comparative graphs for top 5
if not top5_df.empty:
    # Read full NAV data for top 5
    top5_names = top5_df['MF NAME'].tolist()
    nav_top5 = df[df['MF NAME'].isin(top5_names)].copy()
    
    # Normalize NAV to start at 100 for comparison
    nav_top5['Normalized NAV'] = nav_top5.groupby('MF NAME')['nav'].transform(lambda x: x / x.iloc[0] * 100)
    
    # Plot normalized NAV
    plt.figure(figsize=(12, 6))
    for name in top5_names:
        fund_data = nav_top5[nav_top5['MF NAME'] == name]
        plt.plot(fund_data['Date'], fund_data['Normalized NAV'], label=name)
    plt.title('Top 5 Funds - Normalized NAV Comparison')
    plt.xlabel('Date')
    plt.ylabel('Normalized NAV (Base 100)')
    plt.legend()
    plt.savefig(f'{plots_dir}/top5_nav_comparison.png')
    plt.close()
    
    # Bar chart of CAGRs
    plt.figure(figsize=(10, 6))
    plt.bar(top5_df['MF NAME'], top5_df['Overall CAGR (%)'])
    plt.title('Top 5 Funds - Overall CAGR (%)')
    plt.xlabel('Fund Name')
    plt.ylabel('CAGR (%)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(f'{plots_dir}/top5_cagr_comparison.png')
    plt.close()
    
    # Bar chart of Max Drawdown
    plt.figure(figsize=(10, 6))
    plt.bar(top5_df['MF NAME'], top5_df['Max Drawdown (%)'])
    plt.title('Top 5 Funds - Max Drawdown (%)')
    plt.xlabel('Fund Name')
    plt.ylabel('Max Drawdown (%)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(f'{plots_dir}/top5_drawdown_comparison.png')
    plt.close()
    
    # Bar chart of Volatility
    plt.figure(figsize=(10, 6))
    plt.bar(top5_df['MF NAME'], top5_df['Volatility (%)'])
    plt.title('Top 5 Funds - Volatility (%)')
    plt.xlabel('Fund Name')
    plt.ylabel('Volatility (%)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(f'{plots_dir}/top5_volatility_comparison.png')
    plt.close()
    
    # Rolling 1-Year CAGR
    plt.figure(figsize=(12, 6))
    for name in top5_names:
        if name in rolling_cagr_data:
            rolling_data = rolling_cagr_data[name]
            plt.plot(rolling_data['nav_index'], rolling_data['rolling_1yr'] * 100, label=name, alpha=0.7)
    plt.title('Top 5 Funds - Rolling 1-Year CAGR (%)')
    plt.xlabel('Date')
    plt.ylabel('Rolling 1Y CAGR (%)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f'{plots_dir}/top5_rolling_1yr_cagr.png')
    plt.close()
    
    # Rolling 3-Year CAGR
    plt.figure(figsize=(12, 6))
    for name in top5_names:
        if name in rolling_cagr_data:
            rolling_data = rolling_cagr_data[name]
            plt.plot(rolling_data['nav_index'], rolling_data['rolling_3yr'] * 100, label=name, alpha=0.7)
    plt.title('Top 5 Funds - Rolling 3-Year CAGR (%)')
    plt.xlabel('Date')
    plt.ylabel('Rolling 3Y CAGR (%)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f'{plots_dir}/top5_rolling_3yr_cagr.png')
    plt.close()
    
    # Rolling 5-Year CAGR
    plt.figure(figsize=(12, 6))
    for name in top5_names:
        if name in rolling_cagr_data:
            rolling_data = rolling_cagr_data[name]
            plt.plot(rolling_data['nav_index'], rolling_data['rolling_5yr'] * 100, label=name, alpha=0.7)
    plt.title('Top 5 Funds - Rolling 5-Year CAGR (%)')
    plt.xlabel('Date')
    plt.ylabel('Rolling 5Y CAGR (%)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f'{plots_dir}/top5_rolling_5yr_cagr.png')
    plt.close()
    
    print("Comparative plots saved: top5_nav_comparison.png, top5_cagr_comparison.png, top5_drawdown_comparison.png, top5_volatility_comparison.png, top5_rolling_1yr_cagr.png, top5_rolling_3yr_cagr.png, top5_rolling_5yr_cagr.png")