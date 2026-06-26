import yfinance as yf
import pandas as pd
import datetime

def get_tickers_from_csv(file_path="tickers.csv"):
    df = pd.read_csv(file_path)
    if 'TICKER' in df.columns:
        tickers = df['TICKER'].dropna().astype(str).tolist()
        return [f"{t.strip()}.NS" for t in tickers if t.strip()]
    return []

tickers = get_tickers_from_csv()
print(f"Total tickers in CSV: {len(tickers)}")
if "AEGISLOG.NS" in tickers:
    print("AEGISLOG.NS is in tickers list.")

end_date = datetime.date.today()
start_date = end_date - datetime.timedelta(days=1095) 

data = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', threads=True, progress=False)

if "AEGISLOG.NS" in data.columns.levels[0]:
    print("AEGISLOG.NS successfully downloaded.")
    df = data["AEGISLOG.NS"].copy()
    df.dropna(subset=['Close', 'High'], inplace=True)
    print(f"Rows after dropna: {len(df)}")
else:
    print("AEGISLOG.NS WAS DROPPED BY YFINANCE!")
