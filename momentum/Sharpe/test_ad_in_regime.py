import pandas as pd
import json
import momentum_lib as ml
import numpy as np
import warnings
warnings.filterwarnings('ignore')

# Load data
prices_df, nifty_series, stock_tickers, dates = ml.load_prices('N750_updated.xlsx')

# --- Current 4 signals ---
# Signal 1: EMA50 Breadth
ema50_all  = prices_df.ewm(span=50,  adjust=False, axis=1).mean()
ema200_all = prices_df.ewm(span=200, adjust=False, axis=1).mean()
last_px    = prices_df.iloc[:, -1]
last_ema50 = ema50_all.iloc[:, -1]
last_ema200 = ema200_all.iloc[:, -1]
valid      = last_px.notna() & last_ema200.notna()
n_valid    = int(valid.sum())

s1_ema50_breadth = float((last_px[valid] > last_ema50[valid]).sum()) / n_valid
s2_ema_trend     = float((last_ema50[valid] > last_ema200[valid]).sum()) / n_valid

# Signal 3: 52H Breadth (from history)
with open('N750_regime_history.json', 'r') as f:
    latest = json.load(f)[-1]
s3_breadth = latest['Breadth']
s4_momentum = latest['Momentum']

# A/D Ratio
last_two = prices_df.iloc[:, -2:]
rets = last_two.iloc[:, 1] - last_two.iloc[:, 0]
adv = (rets > 0).sum()
dec = (rets < 0).sum()
ad_ratio = adv / dec if dec > 0 else float('inf')
ad_score = adv / (adv + dec) if (adv + dec) > 0 else 0.5

# Current composite (no A/D)
current = s1_ema50_breadth * 0.35 + s2_ema_trend * 0.25 + s3_breadth * 0.25 + s4_momentum * 0.15

print("=" * 70)
print("CURRENT SIGNAL VALUES")
print("=" * 70)
print(f"  S1  EMA50 Breadth : {s1_ema50_breadth:.3f}")
print(f"  S2  EMA Trend Brdth: {s2_ema_trend:.3f}")
print(f"  S3  52H Breadth   : {s3_breadth:.3f}")
print(f"  S4  Momentum Brdth: {s4_momentum:.3f}")
print(f"  --  A/D Score     : {ad_score:.3f}  (Ratio: {ad_ratio:.2f}, {adv}A/{dec}D)")
print(f"\n  Current Composite : {current:.3f}")

# --- Option A: Add as 5th signal (10%), reduce others proportionally ---
# Scale existing 100% down to 90%, give A/D 10%
factor = 0.90
opt_a = (s1_ema50_breadth * 0.35 * factor +
         s2_ema_trend     * 0.25 * factor +
         s3_breadth       * 0.25 * factor +
         s4_momentum      * 0.15 * factor +
         ad_score         * 0.10)

# --- Option B: Replace Momentum Breadth (15%) with A/D ---
opt_b = (s1_ema50_breadth * 0.35 +
         s2_ema_trend     * 0.25 +
         s3_breadth       * 0.25 +
         ad_score         * 0.15)

# --- Option C: Split EMA50 Breadth weight (25% EMA50 + 10% A/D) ---
opt_c = (s1_ema50_breadth * 0.25 +
         s2_ema_trend     * 0.25 +
         s3_breadth       * 0.25 +
         s4_momentum      * 0.15 +
         ad_score         * 0.10)

# --- Option D: Equal weight all 5 (20% each) ---
opt_d = (s1_ema50_breadth * 0.20 +
         s2_ema_trend     * 0.20 +
         s3_breadth       * 0.20 +
         s4_momentum      * 0.20 +
         ad_score         * 0.20)

MIN_N = 5
MAX_N = 25
THRESHOLD = 0.40

def summary(label, score):
    dyn_n = int(MIN_N + score * (MAX_N - MIN_N))
    status = "BUY" if score >= THRESHOLD else "BLOCKED"
    print(f"  {label:45s} -> {score:.3f}  (N={dyn_n}, {status})")

print("\n" + "=" * 70)
print("WHAT-IF SCENARIOS")
print("=" * 70)
summary("Current (no A/D)", current)
summary("A: Add 5th signal (10%), scale others to 90%", opt_a)
summary("B: Replace Momentum Breadth with A/D (15%)", opt_b)
summary("C: Split EMA50 Breadth (25%+10% A/D)", opt_c)
summary("D: Equal weight all 5 (20% each)", opt_d)

# Correlation analysis - how similar is A/D to existing signals?
print("\n" + "=" * 70)
print("SIGNAL SIMILARITY ANALYSIS")
print("=" * 70)
print(f"  A/D Score ({ad_score:.3f}) vs EMA50 Breadth ({s1_ema50_breadth:.3f}): ", end="")
diff1 = abs(ad_score - s1_ema50_breadth)
print(f"diff = {diff1:.3f} {'(SIMILAR)' if diff1 < 0.15 else '(DIFFERENT)'}")

print(f"  A/D Score ({ad_score:.3f}) vs EMA Trend    ({s2_ema_trend:.3f}): ", end="")
diff2 = abs(ad_score - s2_ema_trend)
print(f"diff = {diff2:.3f} {'(SIMILAR)' if diff2 < 0.15 else '(DIFFERENT)'}")

print(f"  A/D Score ({ad_score:.3f}) vs 52H Breadth  ({s3_breadth:.3f}): ", end="")
diff3 = abs(ad_score - s3_breadth)
print(f"diff = {diff3:.3f} {'(SIMILAR)' if diff3 < 0.15 else '(DIFFERENT)'}")

print(f"  A/D Score ({ad_score:.3f}) vs Momentum     ({s4_momentum:.3f}): ", end="")
diff4 = abs(ad_score - s4_momentum)
print(f"diff = {diff4:.3f} {'(SIMILAR)' if diff4 < 0.15 else '(DIFFERENT)'}")
