import pandas as pd
import requests
import io

# Specify the categories to filter (comma-separated, e.g., 'midcap, flexicap, largecap')
categories_input = 'smallcap'  # Change this to the desired categories

# Split and clean the categories
categories = [cat.strip().lower() for cat in categories_input.split(',')]

url = "https://www.amfiindia.com/spages/NAVAll.txt"
response = requests.get(url)

# Read the semicolon-separated text file
df = pd.read_csv(io.StringIO(response.text), sep=";")

# Drop section headers and empty rows to isolate the actual fund data
df_clean = df.dropna(subset=['ISIN Div Payout/ ISIN Growth'])

# Filter by categories and direct plans (case insensitive search in Scheme Name)
if categories:
    pattern = '|'.join(categories)
    df_filtered = df_clean[df_clean['Scheme Name'].str.lower().str.contains(pattern, na=False) & 
                           df_clean['Scheme Name'].str.lower().str.contains('direct', na=False)]
else:
    df_filtered = df_clean[df_clean['Scheme Name'].str.lower().str.contains('direct', na=False)]

# Extract just the Fund Names and ISINs
isin_list = df_filtered[['Scheme Name', 'ISIN Div Payout/ ISIN Growth', 'ISIN Div Reinvestment']]

# Create a list to hold MF Name and ISIN pairs
mf_isin_pairs = []

for index, row in isin_list.iterrows():
    mf_name = row['Scheme Name']
    isin_growth = row['ISIN Div Payout/ ISIN Growth']
    isin_reinvest = row['ISIN Div Reinvestment']
    
    # Add ISIN Growth if not NaN, not empty, and not '-'
    if pd.notna(isin_growth) and isin_growth.strip() and isin_growth.strip() != '-':
        mf_isin_pairs.append([mf_name, isin_growth.strip()])
    
    # Add ISIN Reinvestment if not NaN, not empty, not '-', and different from growth
    if pd.notna(isin_reinvest) and isin_reinvest.strip() and isin_reinvest.strip() != '-' and isin_reinvest != isin_growth:
        mf_isin_pairs.append([mf_name, isin_reinvest.strip()])

# Create a DataFrame from the pairs
output_df = pd.DataFrame(mf_isin_pairs, columns=['MF NAME', 'ISIN CODE'])

# Write to CSV without header, using semicolon separator
output_filename = f"{'_'.join(categories)}_isin_codes.csv"
output_df.to_csv(output_filename, sep=';', index=False, header=False)

print(f"ISIN codes for {', '.join(categories)} direct funds have been written to {output_filename}")