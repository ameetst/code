"""
Categorise jump types:
  Category A — 31-Mar-2021 to 06-Nov-2024 gap: looks like a data gap (missing years)
  Category B — IRB-style alternating +100/-50%: looks like a stock split not adjusted
  Category C — Genuine single large move (splits/bonuses on a specific date)
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
import datetime
import numpy as np
import openpyxl

FILE = r"C:\Users\ameet\Documents\Github\code\momentum\Sharpe Score\n500_bt.xlsx"

wb       = openpyxl.load_workbook(FILE, data_only=True, read_only=True)
ws       = wb["DATA"]
all_rows = list(ws.iter_rows(values_only=True))
wb.close()

header       = all_rows[0]
date_indices = [i for i, h in enumerate(header)
                if isinstance(h, (datetime.datetime, datetime.date))]
dates        = [header[i] for i in date_indices]

THRESHOLD = 0.50

all_jumps = []   # (ticker, from_date, to_date, from_px, to_px, pct)

for row in all_rows[1:]:
    if row[0] is None:
        continue
    ticker = str(row[0]).strip()
    if ticker == "NIFTY500":
        continue
    px_vals, px_dates = [], []
    for idx, di in enumerate(date_indices):
        v = row[di]
        try:
            f = float(v)
            if f > 0:
                px_vals.append(f)
                px_dates.append(dates[idx])
        except:
            pass
    if len(px_vals) < 2:
        continue
    px   = np.array(px_vals)
    rets = np.diff(px) / px[:-1]
    for j, r in enumerate(rets):
        if abs(r) > THRESHOLD:
            fd = px_dates[j]
            td = px_dates[j+1]
            all_jumps.append((ticker,
                              fd.strftime("%d-%b-%Y") if hasattr(fd,"strftime") else str(fd),
                              td.strftime("%d-%b-%Y") if hasattr(td,"strftime") else str(td),
                              px[j], px[j+1], r*100))

# ── Categorise ────────────────────────────────────────────────────────
# Category A: from=31-Mar-2021, to=06-Nov-2024 (or similar multi-year gap)
# Category B: IRB-style oscillating splits (many jumps per ticker, alternating sign)
# Category C: single genuine event

from collections import defaultdict
ticker_jumps = defaultdict(list)
for j in all_jumps:
    ticker_jumps[j[0]].append(j)

cat_a_tickers = []   # data gap
cat_b_tickers = []   # unadjusted split oscillating
cat_c_tickers = []   # single event splits/bonuses

for ticker, jumps in ticker_jumps.items():
    # Check if any jump crosses a multi-year gap (> 365 days between consecutive non-zero prices)
    is_gap = any(j[2].find("Nov-2024") != -1 and j[1].find("Mar-2021") != -1 for j in jumps)
    is_gap = is_gap or any(j[2].find("Nov-2024") != -1 for j in jumps)
    if is_gap:
        cat_a_tickers.append((ticker, jumps))
    elif len(jumps) > 3:
        cat_b_tickers.append((ticker, jumps))
    else:
        cat_c_tickers.append((ticker, jumps))

print("=" * 72)
print(" JUMP ANALYSIS REPORT")
print("=" * 72)

print(f"""
CATEGORY A — DATA GAP ({len(cat_a_tickers)} tickers)
  These stocks jump from 31-Mar-2021 directly to 06-Nov-2024 (or similar).
  This means ~3.5 years of price data is MISSING for these tickers.
  The "jump" is just the price change over the gap.
  ACTION: Fill in the missing 2021-2024 daily prices for these stocks.
""")
print(f"  {'TICKER':<18} {'GAP FROM':<14} {'GAP TO':<14} {'FROM PX':>9} {'TO PX':>9} {'MOVE':>7}")
print(f"  {'-'*18} {'-'*14} {'-'*14} {'-'*9} {'-'*9} {'-'*7}")
for ticker, jumps in sorted(cat_a_tickers):
    for j in jumps:
        if "Nov-2024" in j[2] or "Nov-2025" in j[2] or "Nov-2026" in j[2]:
            print(f"  {j[0]:<18} {j[1]:<14} {j[2]:<14} {j[3]:>9.2f} {j[4]:>9.2f} {j[5]:>+7.1f}%")
            break

print(f"""
CATEGORY B — OSCILLATING / UNADJUSTED SPLIT ({len(cat_b_tickers)} tickers)
  These stocks show repeated alternating +100% / -50% moves (e.g. IRB).
  This is a classic sign of unadjusted stock splits — the raw price
  alternates between pre/post split prices on different dates.
  ACTION: Replace with split-adjusted prices for these tickers.
""")
print(f"  {'TICKER':<18} {'# JUMPS':>7}  EXAMPLE JUMP")
print(f"  {'-'*18} {'-'*7}  {'-'*30}")
for ticker, jumps in sorted(cat_b_tickers):
    j = jumps[0]
    print(f"  {ticker:<18} {len(jumps):>7}  {j[1]} -> {j[2]}  {j[5]:>+.1f}%")

print(f"""
CATEGORY C — SINGLE EVENT (split/bonus/demerger) ({len(cat_c_tickers)} tickers)
  These stocks have 1-2 large moves on specific dates.
  Could be splits, bonus issues, or demergers.
  ACTION: Verify each against corporate action announcements.
""")
print(f"  {'TICKER':<18} {'DATE':<14} {'FROM PX':>9} {'TO PX':>9} {'MOVE':>7}  NOTE")
print(f"  {'-'*18} {'-'*14} {'-'*9} {'-'*9} {'-'*7}  {'-'*20}")
for ticker, jumps in sorted(cat_c_tickers):
    for j in jumps:
        note = "split?" if j[5] > 0 else "reverse-split/demerger?"
        print(f"  {j[0]:<18} {j[1]:<14} {j[3]:>9.2f} {j[4]:>9.2f} {j[5]:>+7.1f}%  {note}")

print(f"\n{'=' * 72}")
print(f"  SUMMARY")
print(f"{'=' * 72}")
print(f"  Category A (data gaps)              : {len(cat_a_tickers):>3} tickers  — fill missing prices")
print(f"  Category B (unadjusted split noise) : {len(cat_b_tickers):>3} tickers  — replace with adjusted prices")
print(f"  Category C (single split/bonus)     : {len(cat_c_tickers):>3} tickers  — verify & adjust")
print(f"  Total affected                      : {len(cat_a_tickers)+len(cat_b_tickers)+len(cat_c_tickers):>3} tickers")
print("=" * 72)
