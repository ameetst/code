"""Book-style 90-day Clenow score for every ETF in ETF_updated.xlsx.
Uses load_etf_data from etf_momentum_ranking.py (handles the ETF_updated.xlsx layout)
and clenow_score from etf_momentum_ranking_blend.py:
  score = (exp(OLS slope of ln px x 250) - 1) x 100 x R^2 over last 90 closes,
  NaN if any |1-day move| > 15% in the window or < 90 closes.
Joins screen result / Sharpe columns from etf_rankings.xlsx (Rankings sheet)."""
import numpy as np, pandas as pd
import etf_momentum_ranking as loader
import etf_momentum_ranking_blend as m

meta, prices = loader.load_etf_data("ETF_updated.xlsx")
rows = []
for t in dict.fromkeys(meta["TICKER"]):
    if t not in prices.columns: continue
    s = prices[t]
    if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]   # duplicate ticker column
    if s.dropna().empty: continue
    rows.append((t, float(s.dropna().iloc[-1]), m.clenow_score(s)))
df = pd.DataFrame(rows, columns=["Ticker", "Close", "Clenow_90D"])

rk = pd.read_excel("etf_rankings.xlsx", sheet_name="Rankings", header=2)
rk.columns = [str(c).replace(chr(10), " ") for c in rk.columns]
rk = rk[["Ticker", "ETF Name", "Sector", "Investable Rank", "Screen Result", "Wtd Sharpe Score"]]
out = df.merge(rk, on="Ticker", how="left")
out["Clenow_Rank_All"] = out["Clenow_90D"].rank(ascending=False, method="min")
inv = out["Screen Result"].eq("PASS")
out.loc[inv, "Clenow_Rank_Investable"] = out.loc[inv, "Clenow_90D"].rank(ascending=False, method="min")
out = out.sort_values("Clenow_90D", ascending=False)
out.to_excel("etf_clenow_scores.xlsx", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
print(len(out), "ETFs;", out["Clenow_90D"].isna().sum(), "NaN (gap>15% or <90 closes)")
print(out.head(20).round(2).to_string(index=False))
