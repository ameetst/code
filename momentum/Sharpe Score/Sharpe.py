"""
N500 Momentum Ranking
=====================
Ranks NSE N500 stocks by a multi-window momentum composite.

Scores computed (via momentum_lib):
  SHARPE_ALL  — equal-weighted Z-score of 12M/9M/6M/3M Sharpe ratios
  SHARPE_3    — equal-weighted Z-score of 12M/6M/3M Sharpe ratios
  RES_MOM     — equal-weighted Z-score of 12M/9M/6M/3M residual Sharpe

Eligibility filter : PCT_FROM_52H >= -25%
Ranking            : SHARPE_ALL (COMPOSITE)

Usage:  python Sharpe.py path/to/n500.xlsx
"""

import sys
import numpy as np
import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import momentum_lib as ml

# ── CONFIG ────────────────────────────────────────────────────────────────────
FILE         = "n500.xlsx" if len(sys.argv) < 2 else sys.argv[1]
OUTPUT_FILE  = "n500_rankings.xlsx"
RFR_ANNUAL   = 0.07
TRADING_DAYS = 252
TOP_N        = 20

WINDOWS        = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63, "1M": 21}

rfr_daily = RFR_ANNUAL / TRADING_DAYS

# ── LOAD ──────────────────────────────────────────────────────────────────────
print(f"Loading {FILE} ...")
prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

valid_days = sum(1 for d in prices_df.columns
                 if prices_df[d].notna().any() and (prices_df[d] != 0).any())
print(f"  {len(prices_df)} stocks  |  {len(dates)} date columns  "
      f"({valid_days} actual trading days)  "
      f"|  {dates[0].strftime('%d-%b-%Y')} -> {dates[-1].strftime('%d-%b-%Y')}\n")

# ── COMPUTE SCORES ────────────────────────────────────────────────────────────
sharpe_df, z_df       = ml.compute_sharpe(prices_df, stock_tickers,
                                           SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)



ret_df                = ml.compute_returns(prices_df, stock_tickers)
pct_52h               = ml.compute_pct_from_52h(prices_df, stock_tickers)

# ── COMBINE ───────────────────────────────────────────────────────────────────
result = z_df.join(sharpe_df.rename(columns={l: f"S_{l}" for l in SHARPE_WINDOWS}))

for col in ["COMPOSITE", "SHARPE_3"]:
    result[col] = result[col].map(ml.normalise_composite)
result["SHARPE_ALL"] = result["COMPOSITE"]

result["RANK"] = result["COMPOSITE"].rank(ascending=False, method="first",
                                           na_option="bottom")
result = result.sort_values("COMPOSITE", ascending=False)
result = result.join(ret_df)

# ── 52H FILTER + RE-RANK ──────────────────────────────────────────────────────
print("\nComputing 52-week high proximity ...")
result["PCT_FROM_52H"] = pct_52h

eligible = result["PCT_FROM_52H"] >= -25
result["RANK"] = np.nan
result.loc[eligible, "RANK"] = (
    result.loc[eligible, "COMPOSITE"]
    .rank(ascending=False, method="first", na_option="bottom")
)
result = result.sort_values(["RANK", "COMPOSITE"], ascending=[True, False])
print(f"  {eligible.sum()} / {len(result)} stocks eligible (PCT_FROM_52H >= -25%)")

# ── RESIDUAL MOMENTUM ─────────────────────────────────────────────────────────
resmom_df, rs_z_df = ml.compute_residual_momentum(prices_df, stock_tickers,
                                                    nifty_series, WINDOWS, TRADING_DAYS)
result = result.join(resmom_df)
result = result.join(rs_z_df)

# ── MARKET REGIME ─────────────────────────────────────────────────────────────
regime_flag, is_cash = ml.compute_market_regime(nifty_series)

# ── CONSOLE OUTPUT ────────────────────────────────────────────────────────────
SEP  = "-" * 82
HEAD = (f"{'RNK':>4}  {'TICKER':<12}  {'SHARPE_ALL':>10}  "
        f"{'RES_MOM':>9}  {'SHARPE_3':>9}  "
        f"{'1M%':>7}  {'3M%':>7}  {'12M%':>7}")

print(f"\n{'':=<82}")
print(f"  N500 MOMENTUM - TOP {TOP_N}  .  Sharpe Z + Sharpe 3W + Residual")
print(f"  MARKET REGIME : {regime_flag}")
print(f"  Checks        : (1) EMA50 > EMA200  (2) price > EMA50  (3) EMA21 > EMA63   [NIFTY500]")
print(f"  Windows: 12M/9M/6M/3M  |  RFR={RFR_ANNUAL*100:.1f}%  |  Filter: PCT_FROM_52H >= -25%")
print(f"{'':=<82}")

if is_cash:
    print(f"\n  !! CASH REGIME ALERT !!")
    print(f"  Death Cross detected on NIFTY500 (EMA50 crossed below EMA200).")
    print(f"  RECOMMENDED ACTION : EXIT ALL EQUITY POSITIONS")
    print(f"  PARK PROCEEDS IN   : Liquid Funds (~2% p.a.)")
    print(f"  Rankings below are shown for reference only.\n")
print(HEAD); print(SEP)

def fs(v, w=7): return f"{v:>{w}.3f}" if pd.notna(v) else f"{'--':>{w}}"
def fp(v, w=7): return f"{v:>{w}.1f}" if pd.notna(v) else f"{'--':>{w}}"

for i, (ticker, row) in enumerate(result.head(TOP_N).iterrows(), 1):
    print(f"{i:>4}  {ticker:<12}  "
          f"{fs(row['COMPOSITE'],10)}  "
          f"{fs(row['RES_MOM'],9)}  {fs(row['SHARPE_3'],9)}  "
          f"{fp(row['1M%'])}  {fp(row['3M%'])}  {fp(row['12M%'])}")

print(SEP)
print(f"\n  SHARPE_ALL = mean(Z_12M..Z_3M)  |  SHARPE_3 = mean(Z_12M,Z_6M,Z_3M)  |  "
      f"RES_MOM = residual Sharpe composite\n")

# ── EXCEL OUTPUT ──────────────────────────────────────────────────────────────
print(f"Writing {OUTPUT_FILE} ...")
wb_out = openpyxl.Workbook()
wb_out.remove(wb_out.active)

def fill(hex_colour):
    return PatternFill("solid", fgColor=hex_colour)

def border_all():
    s = Side(style="thin", color="CCCCCC")
    return Border(left=s, right=s, top=s, bottom=s)

HDR_FILL   = fill("E8EEF2")
ALT_FILL   = fill("F8F9FA")
POS_FILL   = fill("E6F4EA")
NEG_FILL   = fill("FCE8E6")
GOLD_FONT  = Font(name="Calibri", color="1A365D", bold=True,  size=11)
CYAN_FONT  = Font(name="Calibri", color="0055CC", bold=True,  size=11)
TEXT_FONT  = Font(name="Calibri", color="111111",             size=11)
MUTED_FONT = Font(name="Calibri", color="707070",             size=11)
HDR_FONT   = Font(name="Calibri", color="1A365D", bold=True,  size=11)
GREEN_FONT = Font(name="Calibri", color="137333", bold=True,  size=11)
RED_FONT   = Font(name="Calibri", color="C5221F", bold=True,  size=11)

def set_hdr(cell, value):
    cell.value     = value
    cell.font      = HDR_FONT
    cell.fill      = HDR_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border    = border_all()

def set_cell(cell, value, font=None, bg=None, num_fmt=None, align="right"):
    cell.value     = value
    cell.font      = font or TEXT_FONT
    cell.fill      = bg or fill("FFFFFF")
    cell.alignment = Alignment(horizontal=align, vertical="center")
    cell.border    = border_all()
    if num_fmt:
        cell.number_format = num_fmt

def pnl_fnt(v): return GREEN_FONT if pd.notna(v) and v >= 0 else RED_FONT
def pnl_bg(v):  return POS_FILL   if pd.notna(v) and v >= 0 else NEG_FILL

# ── SHEET 1 — TOP20 ───────────────────────────────────────────────────────────
ws1 = wb_out.create_sheet("TOP20")
ws1.sheet_view.showGridLines = False
ws1.freeze_panes = "C3"

ws1.merge_cells("A1:H1")
tc           = ws1["A1"]
tc.value     = (f"N500 MOMENTUM  .  Top {TOP_N} by SHARPE_ALL  .  Filter: PCT_FROM_52H >= -25%  .  "
                f"RFR={RFR_ANNUAL*100:.1f}%  .  "
                f"{dates[0].strftime('%d-%b-%Y')} -> {dates[-1].strftime('%d-%b-%Y')}  .  "
                f"Regime: {regime_flag}")
tc.font      = Font(name="Calibri", color="FF2222" if is_cash else "1A365D", bold=True, size=11)
tc.fill      = fill("2A0000") if is_cash else fill("F0F4F8")
tc.alignment = Alignment(horizontal="center", vertical="center")
ws1.row_dimensions[1].height = 22

top20_cols = [
    ("RNK",       5), ("TICKER",   12), ("SHARPE_ALL", 10),
    ("RES_MOM",  10), ("SHARPE_3", 10), ("1M%",         8),
    ("3M%",       8), ("12M%",      8),
]
for c, (col_name, col_w) in enumerate(top20_cols, 1):
    set_hdr(ws1.cell(row=2, column=c), col_name)
    ws1.column_dimensions[get_column_letter(c)].width = col_w
ws1.row_dimensions[2].height = 18

for i, (ticker, row) in enumerate(result.head(TOP_N).iterrows(), 3):
    bg     = ALT_FILL if i % 2 == 0 else fill("FFFFFF")
    rank_v = int(row["RANK"]) if pd.notna(row["RANK"]) else None
    values = [
        (rank_v,              GOLD_FONT,            bg,                   None),
        (ticker,              GOLD_FONT,            bg,                   None),
        (row["COMPOSITE"],    CYAN_FONT,            bg,                   "0.000"),
        (row["RES_MOM"],      TEXT_FONT,            bg,                   "0.000"),
        (row["SHARPE_3"],     CYAN_FONT,            bg,                   "0.000"),
        (row["1M%"],          pnl_fnt(row["1M%"]),  pnl_bg(row["1M%"]),  "0.0"),
        (row["3M%"],          pnl_fnt(row["3M%"]),  pnl_bg(row["3M%"]),  "0.0"),
        (row["12M%"],         pnl_fnt(row["12M%"]), pnl_bg(row["12M%"]), "0.0"),
    ]
    for c, (val, fnt, bg_c, nfmt) in enumerate(values, 1):
        v = None if (isinstance(val, float) and np.isnan(val)) else val
        set_cell(ws1.cell(row=i, column=c), v, fnt, bg_c, nfmt,
                 align="left" if c == 2 else "right")
    ws1.row_dimensions[i].height = 16

# ── SHEET 2 — CALCS ───────────────────────────────────────────────────────────
ws2 = wb_out.create_sheet("CALCS")
ws2.sheet_view.showGridLines = False
ws2.freeze_panes = "C3"

ws2.merge_cells("A1:AD1")
t2           = ws2["A1"]
t2.value     = (f"N500  .  Full Calculations  .  All {len(stock_tickers)} stocks  .  "
                f"{dates[0].strftime('%d-%b-%Y')} -> {dates[-1].strftime('%d-%b-%Y')}  .  "
                f"Regime: {regime_flag}")
t2.font      = Font(name="Calibri", color="FF2222" if is_cash else "1A365D", bold=True, size=11)
t2.fill      = fill("2A0000") if is_cash else fill("F0F4F8")
t2.alignment = Alignment(horizontal="center", vertical="center")
ws2.row_dimensions[1].height = 22

calcs_cols = [
    ("RANK",       6), ("TICKER",    12),
    ("S_12M",      9), ("S_9M",       9), ("S_6M",     9), ("S_3M",    9), ("S_1M",   9),
    ("Z_12M",      9), ("Z_9M",       9), ("Z_6M",     9), ("Z_3M",    9), ("Z_1M",   9),
    ("SHARPE_ALL",10), ("SHARPE_3",  10),
    ("RS_12M",     9), ("RS_9M",      9), ("RS_6M",    9), ("RS_3M",   9),
    ("RZ_12M",     9), ("RZ_9M",      9), ("RZ_6M",    9), ("RZ_3M",   9),
    ("RES_MOM",   10),
    ("1M%",        8), ("3M%",        8), ("12M%",     8),
    ("52H%",      10),
]

for c, (col_name, col_w) in enumerate(calcs_cols, 1):
    set_hdr(ws2.cell(row=2, column=c), col_name)
    ws2.column_dimensions[get_column_letter(c)].width = col_w
ws2.row_dimensions[2].height = 18

for i, (ticker, row) in enumerate(result.iterrows(), 3):
    bg         = ALT_FILL if i % 2 == 0 else fill("FFFFFF")
    rank_v     = int(row["RANK"]) if pd.notna(row["RANK"]) else None
    pct52h     = row["PCT_FROM_52H"]
    pct52h_ok  = pd.notna(pct52h) and pct52h >= -25
    pct52h_fnt = GREEN_FONT if pct52h_ok else MUTED_FONT
    pct52h_bg  = fill("E6F4EA") if pct52h_ok else fill("F0F0F0")

    values = [
        (rank_v,             GOLD_FONT,  bg,        None),
        (ticker,             GOLD_FONT,  bg,        None),
        (row["S_12M"],       TEXT_FONT,  bg,        "0.000"),
        (row["S_9M"],        TEXT_FONT,  bg,        "0.000"),
        (row["S_6M"],        TEXT_FONT,  bg,        "0.000"),
        (row["S_3M"],        TEXT_FONT,  bg,        "0.000"),
        (row["S_1M"],        TEXT_FONT,  bg,        "0.000"),
        (row["Z_12M"],       TEXT_FONT,  bg,        "0.000"),
        (row["Z_9M"],        TEXT_FONT,  bg,        "0.000"),
        (row["Z_6M"],        TEXT_FONT,  bg,        "0.000"),
        (row["Z_3M"],        TEXT_FONT,  bg,        "0.000"),
        (row["Z_1M"],        TEXT_FONT,  bg,        "0.000"),
        (row["COMPOSITE"],   CYAN_FONT,  bg,        "0.000"),
        (row["SHARPE_3"],    TEXT_FONT,  bg,        "0.000"),

        (row["RS_12M"],      MUTED_FONT, bg,        "0.000"),
        (row["RS_9M"],       MUTED_FONT, bg,        "0.000"),
        (row["RS_6M"],       MUTED_FONT, bg,        "0.000"),
        (row["RS_3M"],       MUTED_FONT, bg,        "0.000"),
        (row["RZ_12M"],      MUTED_FONT, bg,        "0.000"),
        (row["RZ_9M"],       MUTED_FONT, bg,        "0.000"),
        (row["RZ_6M"],       MUTED_FONT, bg,        "0.000"),
        (row["RZ_3M"],       MUTED_FONT, bg,        "0.000"),
        (row["RES_MOM"],     CYAN_FONT,  bg,        "0.000"),
        (row["1M%"],         TEXT_FONT,  bg,        "0.0"),
        (row["3M%"],         TEXT_FONT,  bg,        "0.0"),
        (row["12M%"],        TEXT_FONT,  bg,        "0.0"),
        (pct52h,             pct52h_fnt, pct52h_bg, "0.0"),
    ]
    for c, (val, fnt, bg_c, nfmt) in enumerate(values, 1):
        v = None if (isinstance(val, float) and np.isnan(val)) else val
        set_cell(ws2.cell(row=i, column=c), v, fnt, bg_c, nfmt,
                 align="left" if c == 2 else "right")
    ws2.row_dimensions[i].height = 15

wb_out.save(OUTPUT_FILE)
print(f"  +  Saved -> {OUTPUT_FILE}")
print(f"     Sheet 'TOP20' : top {TOP_N} stocks")
print(f"     Sheet 'CALCS' : all {len(stock_tickers)} stocks, 28 columns")