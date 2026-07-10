"""Step-by-step SHARPE_ALL computation for PIRAMALFIN."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import momentum_lib as ml

FILE           = "n500.xlsx"
TICKER         = "PIRAMALFIN"
RFR_ANNUAL     = 0.07
TRADING_DAYS   = 252
rfr_daily      = RFR_ANNUAL / TRADING_DAYS
SHARPE_WINDOWS = {"12M": 252, "9M": 189, "6M": 126, "3M": 63, "1M": 21}

prices_df, nifty_series, stock_tickers, dates = ml.load_prices(FILE)

if TICKER not in prices_df.index:
    matches = [t for t in stock_tickers if "PIRAM" in t.upper()]
    print(f"'{TICKER}' not found. Similar tickers: {matches}")
    sys.exit(1)

px = prices_df.loc[TICKER].dropna()
print("=" * 68)
print(f"  SHARPE_ALL step-by-step: {TICKER}")
print("=" * 68)
print(f"Data range   : {dates[0].strftime('%d-%b-%Y')} to {dates[-1].strftime('%d-%b-%Y')}")
print(f"Valid prices : {len(px)}  |  Latest: {px.iloc[-1]:.2f}")

# ── STEP 1: Raw Sharpe ────────────────────────────────────────────────
def raw_sharpe(series, window):
    p = series.dropna()
    if len(p) < window * 0.90:
        return np.nan
    pw   = p if len(p) < window + 1 else p.iloc[-(window + 1):]
    lr   = np.diff(np.log(pw.values))
    ex   = lr - rfr_daily
    sd   = ex.std(ddof=1)
    if sd < 1e-12:
        return np.nan
    return (ex.mean() / sd) * np.sqrt(TRADING_DAYS)

print()
print("STEP 1 - Raw annualised Sharpe per window")
print(f"  Formula: mean(daily_excess_log_ret) / std * sqrt({TRADING_DAYS})")
print(f"  daily RFR = {RFR_ANNUAL} / {TRADING_DAYS} = {rfr_daily:.8f}")
raws = {}
for lbl, w in SHARPE_WINDOWS.items():
    v = raw_sharpe(prices_df.loc[TICKER], w)
    raws[lbl] = v
    print(f"  {lbl:>3} ({w:>3}d): {v:>8.4f}")

# ── STEP 2: Cross-sectional Z-scores ─────────────────────────────────
print()
print("STEP 2 - Cross-sectional Z-scores across all stocks in universe")
print("  (computing Sharpe for all stocks - this takes a moment...)")
sharpe_df, z_df = ml.compute_sharpe(prices_df, stock_tickers,
                                    SHARPE_WINDOWS, rfr_daily, TRADING_DAYS)
row = z_df.loc[TICKER]

print()
print(f"  {'Window':<6} {'Raw Sharpe':>10} {'Universe mu':>12} "
      f"{'Universe sd':>12} {'Z-score':>9}")
print(f"  {'-'*6} {'-'*10} {'-'*12} {'-'*12} {'-'*9}")
for lbl in SHARPE_WINDOWS:
    col     = sharpe_df[lbl]
    mu, sd  = col.mean(), col.std(ddof=1)
    v       = raws[lbl]
    z       = (v - mu) / sd if (not np.isnan(v) and sd > 0) else np.nan
    zstr    = f"{z:.4f}" if not np.isnan(z) else "NaN"
    print(f"  {lbl:<6} {v:>10.4f} {mu:>12.4f} {sd:>12.4f} {zstr:>9}")

# ── STEP 3: COMPOSITE ────────────────────────────────────────────────
print()
print("STEP 3 - COMPOSITE = equal-weighted mean of 4 core Z-scores (1M excluded)")
core   = ["12M", "9M", "6M", "3M"]
zv     = [row[f"Z_{l}"] for l in core]
comp_r = np.mean(zv)
terms  = "  +  ".join(f"Z_{l}({z:.4f})" for l, z in zip(core, zv))
print(f"  = [ {terms} ] / 4")
print(f"  = {comp_r:.4f}")

# ── STEP 4: Normalise ────────────────────────────────────────────────
print()
print("STEP 4 - normalise_composite(v)")
v = comp_r
if np.isnan(v):
    norm, rule = np.nan, "NaN"
elif v > 1:
    norm = v + 1
    rule = f"v > 1   -->  v + 1 = {norm:.4f}"
elif v < 0:
    norm = 1.0 / (1.0 - v)
    rule = f"v < 0   -->  1/(1-v) = 1/(1-({v:.4f})) = {norm:.4f}"
else:
    norm = v
    rule = f"0 <= v <= 1  -->  unchanged = {norm:.4f}"
print(f"  v = {v:.4f}")
print(f"  {rule}")
print(f"  SHARPE_ALL (final) = {norm:.4f}")

# ── STEP 5: SHARPE_3 ─────────────────────────────────────────────────
print()
print("STEP 5 - SHARPE_3 = mean(Z_12M, Z_6M, Z_3M)  [9M excluded]")
s3r = np.mean([row["Z_12M"], row["Z_6M"], row["Z_3M"]])
s3n = ml.normalise_composite(s3r)
print(f"  raw = ({row['Z_12M']:.4f} + {row['Z_6M']:.4f} + {row['Z_3M']:.4f}) / 3 = {s3r:.4f}")
print(f"  SHARPE_3 (normalised) = {s3n:.4f}")

# ── STEP 6: MOM_ACCEL ────────────────────────────────────────────────
print()
print("STEP 6 - MOM_ACCEL = Z(SHARPE_ST - SHARPE_LT) across universe")
print(f"  SHARPE_ST = mean(Z_1M, Z_3M, Z_6M)   = {row['SHARPE_ST']:.4f}")
print(f"  SHARPE_LT = mean(Z_9M, Z_12M)         = {row['SHARPE_LT']:.4f}")
print(f"  accel_raw = {row['SHARPE_ST']:.4f} - {row['SHARPE_LT']:.4f} = {row['SHARPE_ST']-row['SHARPE_LT']:.4f}")
print(f"  (then Z-scored cross-sectionally vs all stocks)")
print(f"  MOM_ACCEL = {row['MOM_ACCEL']:.4f}")

# ── Rank ─────────────────────────────────────────────────────────────
tmp              = z_df[["COMPOSITE"]].copy()
tmp["COMPOSITE"] = tmp["COMPOSITE"].map(ml.normalise_composite)
tmp["RANK"]      = tmp["COMPOSITE"].rank(ascending=False, method="first",
                                         na_option="bottom")
rnk = int(tmp.loc[TICKER, "RANK"])
total = len(tmp.dropna(subset=["COMPOSITE"]))

print()
print("=" * 68)
print(f"  SUMMARY for {TICKER}")
print("=" * 68)
for lbl in SHARPE_WINDOWS:
    print(f"  S_{lbl:<3}      = {raws[lbl]:>8.4f}   raw Sharpe")
print()
for lbl in SHARPE_WINDOWS:
    print(f"  Z_{lbl:<3}      = {row[f'Z_{lbl}']:>8.4f}   cross-sectional Z-score")
print()
print(f"  COMPOSITE   = {comp_r:.4f}  (pre-normalisation)")
print(f"  SHARPE_ALL  = {norm:.4f}  (post-normalisation)  <-- ranking score")
print(f"  SHARPE_3    = {s3n:.4f}")
print(f"  MOM_ACCEL   = {row['MOM_ACCEL']:.4f}")
print(f"  SHARPE_ST   = {row['SHARPE_ST']:.4f}")
print(f"  SHARPE_LT   = {row['SHARPE_LT']:.4f}")
print()
print(f"  Pre-52H filter rank: {rnk} / {len(tmp)}")
print("=" * 68)
