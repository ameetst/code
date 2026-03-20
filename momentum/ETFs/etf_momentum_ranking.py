"""
ETF Dual Momentum + Clenow + Weighted Sharpe + Tiered Regime Filter
====================================================================
Scoring pipeline (in correct order):
  Step 1 - SCREEN  : Apply absolute momentum filter first.
                     Only ETFs with 6M return > HURDLE qualify as investable.
  Step 2 - SCORE   : Compute Clenow (6M+3M), Weighted Sharpe, Composite
                     on ALL 224 ETFs for reference.
                     Investable rank is computed on the screened subset only.
  Step 3 - REGIME  : Two-layer tiered filter determines how many slots to fill.
  Step 4 - ALLOCATE: Select Top-N from the investable (screened) ranked list.

Regime Filter - Tiered (three states):
  BULL   (both pass) : all TOP_N slots active
  PARTIAL (one fails): TOP_N_PARTIAL slots active, rest = cash
  BEAR   (both fail) : full cash

  Layer 1 - Trend  : MONIFTY500 above its 100-day SMA
  Layer 2 - Breadth: >= 50% of ETFs above their own 50-day SMA

All parameters in CONFIG below.
"""

from __future__ import annotations
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


# =========================================================
# CONFIG  <- edit these freely
# =========================================================
class CONFIG:
    INPUT_FILE  = "ETF.xlsx"
    OUTPUT_FILE = "etf_rankings.xlsx"

    # Momentum windows (trading days)
    WINDOW_6M   = 126
    WINDOW_3M   = 63
    ANNUALIZE   = 252

    # Portfolio allocation
    TOP_N         = 5    # slots when regime = BULL (both layers pass)
    TOP_N_PARTIAL = 3    # slots when regime = PARTIAL (one layer fails)
                         # remaining slots go to cash as a buffer

    # Absolute momentum hurdle — applied BEFORE ranking
    # Only ETFs exceeding this 6M return are considered investable
    HURDLE_6M   = 0.035  # 3.5% over 6M ~ 7% annualised (repo rate proxy)

    # 52-week high proximity filter — applied BEFORE ranking alongside abs hurdle
    # ETF must be trading within MAX_DRAWDOWN_FROM_HIGH of its 52-week high.
    # Removes deep-drawdown ETFs that are bouncing off a bottom rather than
    # exhibiting genuine momentum. 0.25 = must be >= 75% of 52wk high.
    MAX_DRAWDOWN_FROM_HIGH = 0.25

    # Weighted Sharpe blending (6M trend + 3M recency tilt)
    # Composite ranking is based entirely on Weighted Sharpe
    SHARPE_W6M  = 0.60
    SHARPE_W3M  = 0.40

    # Regime filter
    # Nifty 500 used (not Nifty 50) — broader coverage matches full ETF universe
    # (large + mid + small cap); mid/small roll over before large caps in India
    REGIME_TICKER      = "MONIFTY500"
    REGIME_FALLBACKS   = ["BSE500IETF", "HDFCBSE500", "NIFTYBEES"]
    TREND_SMA_WINDOW   = 100   # Layer 1: index must be above N-day SMA
    BREADTH_SMA_WINDOW = 50    # Layer 2: % of ETFs above their N-day SMA
    BREADTH_THRESHOLD  = 0.50  # Layer 2: minimum fraction required

    # Sector cap — max ETFs per sector in final allocation
    # Prevents concentration in duplicates tracking the same index
    SECTOR_CAP = 2

    # Daily risk-free rate for Sharpe (7% annual / 252)
    DAILY_RF = 0.07 / 252



# =========================================================
# SECTOR CLASSIFICATION
# Auto-derived from ETF name keywords. Rules are ordered
# most-specific first — first match wins.
# =========================================================
_SECTOR_RULES = [
    ("PSU_BANK",         ["psu bank","psubnk","psubank","bse psu bank"]),
    ("PRIVATE_BANK",     ["private bank","pvt bank","pvtban","nifty pb "]),
    ("BANKING_BROAD",    ["nifty bank","bse bank"," bank ","banketf","bankbees","banknifty","nifban"]),
    ("IT_TECH",          ["nifty it","bse it"," it etf","itbees","itietf","nifit","tech etf"]),
    ("HEALTHCARE",       ["healthcare","pharma","health "," hc "," hc\\"]),
    ("METAL",            ["metal"]),
    ("ENERGY",           ["energy","oil & gas","o&g","oilietf","power etf","bse power"]),
    ("INFRA",            ["infra"]),
    ("CONSUMPTION",      ["consumption","consump","fmcg","consumer"]),
    ("REALTY",           ["realty","real estate"]),
    ("DEFENCE",          ["defence","dfnc"]),
    ("PSE",              ["pse etf","cpse","nifty pse","bharat 22","cpseetf"]),
    ("AUTO",             ["auto"]),
    ("CHEMICALS",        ["chemical"]),
    ("FIN_SERVICES",     ["fin serv","financial serv","finietf","bfsi","capital mkt",
                          "captl mkt","capital market","cptmkt","capital mrkts"]),
    ("COMMODITIES",      ["commodity","commo"]),
    ("MANUFACTURING",    ["manufactur","manu"]),
    ("EV_MOBILITY",      ["ev&new","ev new","nifty ev"]),
    ("DIGITAL_INTERNET", ["internet","digital"]),
    ("RAILWAY",          ["railway"]),
    ("TOURISM",          ["trsm","tourism"]),
    ("MNC",              ["mnc"]),
    ("GOLD",             ["gold"]),
    ("SILVER",           ["silver"]),
    ("GOVT_BONDS",       ["g-sec","gsec","gilt","bond etf","bharat bond","ebbetf"]),
    ("DIVIDEND",         ["dividend","div opp"]),
    ("IPO",              ["ipo"]),
    ("ESG",              ["esg"]),
    ("FACTOR_MOMENTUM",  ["momentum","mmt","mmntm"]),
    ("FACTOR_VALUE",     ["value 20","value 30","value 50","enhanced val","enhval"]),
    ("FACTOR_QUALITY",   ["quality","qlty"," ql "," ql30","qual30"]),
    ("FACTOR_LOW_VOL",   ["low vol","lowvol","lw- vol"]),
    ("FACTOR_ALPHA",     ["alpha"]),
    ("FACTOR_EQUAL_WT",  ["equal weight","eq weight","eqwt","eqlwgt","eqlwght","equal wt"]),
    ("INTERNATIONAL",    ["nasdaq","s&p 500","hang seng","hangseng","hngsng","msci","fang+"]),
    ("MIDCAP",           ["midcap","mid cap","mdsmc","midsmall"]),
    ("SMALLCAP",         ["smallcap","small cap","sml100","smcp"]),
    ("NEXT_50",          ["next 50","next50","juniorbees","jr bees"]),
    ("BROAD_MARKET",     ["nifty 50","nifty50","sensex","nifty 100","nifty100",
                          "nifty 200","nifty 500","nifty500","total market",
                          "total mrkt","bse 500","bse500","multicap","mltcp",
                          "lgmdcp","gth sectors","flexicap","flexi"]),
    ("SERVICES",         ["services","svcs"]),
]

def classify_sector(etf_name: str, ticker: str) -> str:
    n = etf_name.lower()
    t = ticker.lower()
    for sector, keywords in _SECTOR_RULES:
        for kw in keywords:
            if kw in n or kw in t:
                return sector
    return "OTHER"


# =========================================================
# 1. DATA LOADING
# =========================================================
def load_etf_data(filepath: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_excel(filepath, sheet_name="DATA", header=None)

    header = raw.iloc[0]
    date_cols = []
    for c in range(4, raw.shape[1] - 1):
        val = header.iloc[c]
        if pd.isna(val):
            continue
        try:
            pd.Timestamp(val)
            date_cols.append(c)
        except Exception:
            pass

    dates    = pd.to_datetime([header.iloc[c] for c in date_cols])
    etf_rows = raw.iloc[1:].reset_index(drop=True)

    meta = pd.DataFrame({
        "ETF_NAME" : etf_rows.iloc[:, 0].fillna("").astype(str).str.strip(),
        "TICKER"   : etf_rows.iloc[:, 1].astype(str).str.strip(),
        "CLOSE"    : pd.to_numeric(etf_rows.iloc[:, 2], errors="coerce"),
        "52WK_HIGH": pd.to_numeric(etf_rows.iloc[:, 3], errors="coerce"),
    })

    price_raw = etf_rows.iloc[:, date_cols].copy()
    price_raw.columns = dates
    price_raw.index   = meta["TICKER"].values
    price_raw = price_raw.apply(pd.to_numeric, errors="coerce").replace(0, np.nan)

    prices = price_raw.T.sort_index().ffill()
    return meta.reset_index(drop=True), prices


# =========================================================
# 2. SCORING
# =========================================================
def sharpe_score(series: pd.Series, window: int) -> float:
    """Annualised Sharpe over lookback window, excess of daily RF"""
    clean = series.dropna()
    if len(clean) < window + 1:
        return np.nan
    log_ret = np.log(clean.iloc[-window - 1:] / clean.iloc[-window - 1:].shift(1)).dropna()
    excess  = log_ret - CONFIG.DAILY_RF
    if excess.std() == 0:
        return np.nan
    return (excess.mean() / excess.std()) * np.sqrt(CONFIG.ANNUALIZE)


def momentum_return(series: pd.Series, window: int) -> float:
    """Total % return over lookback window"""
    clean = series.dropna()
    if len(clean) < window:
        return np.nan
    return (clean.iloc[-1] / clean.iloc[-window] - 1) * 100


# =========================================================
# 3. REGIME FILTER
# =========================================================
def regime_status(prices: pd.DataFrame) -> dict:
    """
    Returns tiered regime state:
      BULL    - both layers pass  -> invest TOP_N slots
      PARTIAL - one layer fails   -> invest TOP_N_PARTIAL slots, rest = cash
      BEAR    - both layers fail  -> full cash
    """
    # Layer 1: Trend
    trend_ticker = next(
        (t for t in [CONFIG.REGIME_TICKER] + CONFIG.REGIME_FALLBACKS
         if t in prices.columns), None
    )

    if trend_ticker is None:
        print("  [warn] No Nifty 500 proxy found; trend layer defaulting to PASS")
        trend_ok    = True
        nifty_price = np.nan
        nifty_sma   = np.nan
    else:
        s = prices[trend_ticker].dropna()
        if len(s) >= CONFIG.TREND_SMA_WINDOW:
            nifty_price = s.iloc[-1]
            nifty_sma   = s.iloc[-CONFIG.TREND_SMA_WINDOW:].mean()
            trend_ok    = bool(nifty_price > nifty_sma)
        else:
            trend_ok    = True
            nifty_price = s.iloc[-1] if len(s) else np.nan
            nifty_sma   = np.nan

    # Layer 2: Breadth
    above, eligible = 0, 0
    bw = CONFIG.BREADTH_SMA_WINDOW
    for col in prices.columns:
        s = prices[col].dropna()
        if len(s) >= bw:
            eligible += 1
            if s.iloc[-1] > s.iloc[-bw:].mean():
                above += 1

    breadth_pct = above / eligible if eligible > 0 else np.nan
    breadth_ok  = bool(breadth_pct >= CONFIG.BREADTH_THRESHOLD) if not np.isnan(breadth_pct) else True

    # Tiered label
    both_pass = trend_ok and breadth_ok
    both_fail = (not trend_ok) and (not breadth_ok)

    if both_pass:
        label        = "BULL"
        active_slots = CONFIG.TOP_N
    elif both_fail:
        label        = "BEAR"
        active_slots = 0
    else:
        # One layer failing = PARTIAL
        failing_layer = "TREND" if not trend_ok else "BREADTH"
        label        = f"PARTIAL (weak {failing_layer})"
        active_slots = CONFIG.TOP_N_PARTIAL

    return {
        "regime_ok"   : both_pass,        # True only if BULL
        "label"       : label,
        "active_slots": active_slots,
        "trend_ok"    : trend_ok,
        "breadth_ok"  : breadth_ok,
        "nifty_price" : nifty_price,
        "nifty_sma"   : nifty_sma,
        "breadth_pct" : breadth_pct,
        "trend_ticker": trend_ticker or "N/A",
    }


# =========================================================
# 4. SCORING + RANKING
#    Abs momentum screen applied FIRST to determine investable universe,
#    then composite ranking done on that screened subset.
# =========================================================
def build_ranking(meta: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    records = []

    for _, row in meta.iterrows():
        ticker = row["TICKER"]
        if ticker not in prices.columns:
            continue
        s = prices[ticker]

        sh6 = sharpe_score(s, CONFIG.WINDOW_6M)
        sh3 = sharpe_score(s, CONFIG.WINDOW_3M)
        dm6 = momentum_return(s, CONFIG.WINDOW_6M)
        dm3 = momentum_return(s, CONFIG.WINDOW_3M)

        # Weighted Sharpe (composite ranking metric)
        if not np.isnan(sh6) and not np.isnan(sh3):
            wtd_sharpe = CONFIG.SHARPE_W6M * sh6 + CONFIG.SHARPE_W3M * sh3
        elif not np.isnan(sh6):
            wtd_sharpe = sh6
        elif not np.isnan(sh3):
            wtd_sharpe = sh3
        else:
            wtd_sharpe = np.nan

        # --- Screen Step 1a: Absolute momentum hurdle ---
        hurdle_pct = CONFIG.HURDLE_6M * 100
        if not np.isnan(dm6):
            abs_pass = dm6 > hurdle_pct
        elif not np.isnan(dm3):
            abs_pass = dm3 > hurdle_pct / 2
        else:
            abs_pass = False

        # --- Screen Step 1b: 52-week high proximity filter ---
        close    = row["CLOSE"]
        high_52w = row["52WK_HIGH"]
        if pd.notna(close) and pd.notna(high_52w) and high_52w > 0:
            pct_from_high = (high_52w - close) / high_52w   # 0 = at high, 1 = at zero
            high_pass     = pct_from_high <= CONFIG.MAX_DRAWDOWN_FROM_HIGH
        else:
            pct_from_high = np.nan
            high_pass     = True   # no data -> don't penalise

        # Both screens must pass to be investable
        screen_pass = abs_pass and high_pass

        records.append({
            "TICKER"        : ticker,
            "ETF_NAME"      : row["ETF_NAME"],
            "SECTOR"        : classify_sector(row["ETF_NAME"], ticker),
            "CLOSE"         : close,
            "52WK_HIGH"     : high_52w,
            "PCT_FROM_HIGH" : pct_from_high * 100 if not np.isnan(pct_from_high) else np.nan,
            "SHARPE_6M"     : sh6,
            "SHARPE_3M"     : sh3,
            "WTD_SHARPE"    : wtd_sharpe,
            "DM_RET_6M_PCT" : dm6,
            "DM_RET_3M_PCT" : dm3,
            "ABS_PASS"      : abs_pass,
            "HIGH_PASS"     : high_pass,
            "SCREEN_PASS"   : screen_pass,
        })

    df = pd.DataFrame(records)

    def minmax(col):
        mn, mx = col.min(), col.max()
        return (col - mn) / (mx - mn) if mx > mn else col * 0 + 0.5

    # --- Universe rank — based entirely on Weighted Sharpe ---
    df["RANK_UNIVERSE"] = df["WTD_SHARPE"].rank(ascending=False, na_option="bottom").astype(int)
    df["RANK_SHARPE"]   = df["RANK_UNIVERSE"]   # same — kept for column compatibility
    df["RANK_DM_6M"]    = df["DM_RET_6M_PCT"].rank(ascending=False, na_option="bottom").astype(int)

    # --- Investable rank — Weighted Sharpe among screen-pass ETFs only ---
    inv = df[df["SCREEN_PASS"]].copy()
    if len(inv) > 0:
        inv["RANK_INVESTABLE"] = inv["WTD_SHARPE"].rank(ascending=False, na_option="bottom").astype(int)
        df = df.merge(inv[["TICKER", "RANK_INVESTABLE"]], on="TICKER", how="left")
    else:
        df["RANK_INVESTABLE"] = np.nan

    df["RANK_INVESTABLE"] = df["RANK_INVESTABLE"].fillna(0).astype(int)

    # Sort by investable rank first (passing ETFs at top), then universe rank
    df["_sort"] = df["RANK_INVESTABLE"].replace(0, 9999)
    df = df.sort_values(["_sort", "RANK_UNIVERSE"]).drop(columns="_sort").reset_index(drop=True)

    return df


# =========================================================
# 5. PORTFOLIO ALLOCATION
# =========================================================
def build_allocation(df: pd.DataFrame, regime: dict) -> pd.DataFrame:
    """
    Select top ETFs from the INVESTABLE (abs-pass) subset only.
    Number of active slots determined by tiered regime state:
      BULL    -> TOP_N slots
      PARTIAL -> TOP_N_PARTIAL slots (remainder = cash buffer)
      BEAR    -> 0 slots (full cash)
    """
    active = regime["active_slots"]
    total  = CONFIG.TOP_N
    w      = 1.0 / total

    # Full cash — regime is BEAR
    if active == 0:
        return pd.DataFrame([{
            "SLOT"        : i + 1,
            "TICKER"      : "CASH",
            "ETF_NAME"    : "Cash / Money Market",
            "SECTOR"      : "CASH",
            "WEIGHT"      : w,
            "INV_RANK"    : "-",
            "REASON"      : f"Regime = {regime['label']} -> full cash"
        } for i in range(total)])

    # Investable ETFs sorted by investable rank (composite score)
    investable = df[df["SCREEN_PASS"] & (df["RANK_INVESTABLE"] > 0)].copy()
    investable = investable.sort_values("RANK_INVESTABLE").reset_index(drop=True)

    is_partial = (active == CONFIG.TOP_N_PARTIAL)

    if is_partial:
        # PARTIAL REGIME: re-sort the investable pool by 3M Clenow score
        # descending before trimming to TOP_N_PARTIAL slots.
        # Rationale: when regime is weakening, 3M is the leading signal of
        # deterioration. The two ETFs with the weakest recent momentum are
        # dropped first, regardless of their 6M composite rank.
        # NaN 3M scores sorted last so missing-data ETFs drop off first.
        investable = investable.sort_values(
            "SHARPE_3M", ascending=False, na_position="last"
        ).reset_index(drop=True)
        sort_label = "3M Sharpe (partial regime sort)"
    else:
        sort_label = "Investable composite rank (bull regime sort)"

    slots = []
    sector_count: dict[str, int] = {}   # track how many slots each sector has filled

    # Walk the (possibly re-sorted) investable list; apply sector cap
    candidate_idx = 0
    slot_num = 1
    while slot_num <= active:
        # Find next candidate that doesn't breach sector cap
        filled = False
        while candidate_idx < len(investable):
            row     = investable.iloc[candidate_idx]
            sector  = row.get("SECTOR", "OTHER")
            current = sector_count.get(sector, 0)
            candidate_idx += 1

            if current < CONFIG.SECTOR_CAP:
                sector_count[sector] = current + 1
                slots.append({
                    "SLOT"    : slot_num,
                    "TICKER"  : row["TICKER"],
                    "ETF_NAME": row["ETF_NAME"],
                    "SECTOR"  : sector,
                    "WEIGHT"  : w,
                    "INV_RANK": int(row["RANK_INVESTABLE"]),
                    "REASON"  : (f"Sort: {sort_label}  |  "
                                 f"Sector={sector} ({current+1}/{CONFIG.SECTOR_CAP})  |  "
                                 f"3M Sharpe={row['SHARPE_3M']:.3f}  |  "
                                 f"6M={row['DM_RET_6M_PCT']:.1f}%"),
                })
                slot_num += 1
                filled = True
                break
            # else: sector cap hit — skip this ETF, try next

        if not filled:
            # Exhausted all candidates — fill remaining with cash
            slots.append({
                "SLOT"    : slot_num,
                "TICKER"  : "CASH",
                "ETF_NAME": "Cash (sector cap / investable universe exhausted)",
                "SECTOR"  : "CASH",
                "WEIGHT"  : w,
                "INV_RANK": "-",
                "REASON"  : (f"Sector cap={CONFIG.SECTOR_CAP} per sector  |  "
                             f"No remaining qualifying ETF after cap"),
            })
            slot_num += 1

    # Remaining slots: cash buffer for PARTIAL regime
    for slot_num in range(active + 1, total + 1):
        slots.append({
            "SLOT"    : slot_num,
            "TICKER"  : "CASH",
            "ETF_NAME": "Cash / Money Market",
            "SECTOR"  : "CASH",
            "WEIGHT"  : w,
            "INV_RANK": "-",
            "REASON"  : (f"Regime buffer: {regime['label']} -> "
                         f"weakest 3M Clenow ETFs dropped  |  "
                         f"only {active} of {total} slots active"
                         if is_partial else "Universe exhausted")
        })

    return pd.DataFrame(slots)


# =========================================================
# 6. CONSOLE SUMMARY
# =========================================================
def print_summary(df, regime, allocation):
    W = 110
    print("\n" + "=" * W)
    print("ETF MOMENTUM RANKING  |  Screen -> Score -> Regime -> Allocate")
    print("=" * W)

    r = regime
    print(f"\n  REGIME: {r['label']:30s}"
          f"  Active slots: {r['active_slots']} / {CONFIG.TOP_N}")
    print(f"  Layer 1 Trend    ({r['trend_ticker']} vs {CONFIG.TREND_SMA_WINDOW}d SMA): "
          f"{'PASS' if r['trend_ok'] else 'FAIL'}  "
          f"({r['nifty_price']:.2f} vs SMA {r['nifty_sma']:.2f})")
    print(f"  Layer 2 Breadth  (>={CONFIG.BREADTH_THRESHOLD:.0%} above {CONFIG.BREADTH_SMA_WINDOW}d SMA): "
          f"{'PASS' if r['breadth_ok'] else 'FAIL'}  "
          f"({r['breadth_pct']:.1%} of ETFs above SMA)")

    print(f"\n  ALLOCATION  (hurdle={CONFIG.HURDLE_6M*100:.1f}%  |  "
          f"BULL={CONFIG.TOP_N} slots  PARTIAL={CONFIG.TOP_N_PARTIAL} slots  BEAR=0 slots)")
    print("  " + "-" * 75)
    for _, a in allocation.iterrows():
        is_cash = a["TICKER"] == "CASH"
        marker  = "  [CASH]" if is_cash else "  [HOLD]"
        print(f"  Slot {int(a['SLOT'])}: {a['TICKER']:<14} {a['WEIGHT']:5.1%}{marker}  {a['ETF_NAME'][:48]}")

    inv_count = df["SCREEN_PASS"].sum()
    print(f"\n  RANKING  (investable rank = scored among {inv_count} ETFs passing abs filter)")
    print(f"  {'InvRk':>5} {'UniRk':>5} {'Ticker':<14} {'ETF Name':<36} "
          f"{'WtdSharpe':>10} {'Sharpe6M':>9} {'Sharpe3M':>9} "
          f"{'DM6M%':>7} {'DM3M%':>7} {'Screen':>7}")
    print("  " + "-" * 105)

    for _, r2 in df.head(30).iterrows():
        def f(v, d=3): return f"{v:.{d}f}" if pd.notna(v) and v != 0 else "N/A"
        inv_rk = str(int(r2["RANK_INVESTABLE"])) if r2["ABS_PASS"] else "-"
        screen = "PASS" if r2["SCREEN_PASS"] else ("AbsOK/HighFAIL" if r2["ABS_PASS"] else "FAIL")
        print(f"  {inv_rk:>5} {int(r2['RANK_UNIVERSE']):>5} {r2['TICKER']:<14} "
              f"{str(r2['ETF_NAME'])[:35]:<36} "
              f"{f(r2['WTD_SHARPE']):>10} {f(r2['SHARPE_6M']):>9} {f(r2['SHARPE_3M']):>9} "
              f"{f(r2['DM_RET_6M_PCT'],1):>7} "
              f"{f(r2['DM_RET_3M_PCT'],1):>7} {screen:>7}")

    print(f"\n  Universe={len(df)}  Investable (both screens pass)={inv_count}  "
          f"Screened out={len(df)-inv_count}  Valid Wtd Sharpe={df['WTD_SHARPE'].notna().sum()}")
    print("=" * W)


# =========================================================
# 7b. HOLDINGS LOG & REBALANCE TRACKER
# =========================================================
import json
from datetime import datetime

HOLDINGS_LOG_FILE = "holdings_log.json"
HISTORY_MONTHS    = 12   # how many months of history to show in Excel

def _log_path(script_dir: Path) -> Path:
    return script_dir / HOLDINGS_LOG_FILE


def load_holdings_log(script_dir: Path) -> dict:
    """Load existing log; return empty dict if none exists yet."""
    p = _log_path(script_dir)
    if p.exists():
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_holdings_log(script_dir: Path, log: dict):
    """Persist the updated log to disk."""
    with open(_log_path(script_dir), "w") as f:
        json.dump(log, f, indent=2, default=str)


def record_to_log(allocation: pd.DataFrame, regime: dict, run_date: str) -> dict:
    """Serialise current allocation + regime into a log entry."""
    slots = []
    for _, row in allocation.iterrows():
        slots.append({
            "slot"    : int(row["SLOT"]),
            "ticker"  : str(row["TICKER"]),
            "etf_name": str(row["ETF_NAME"]),
            "sector"  : str(row.get("SECTOR", "")),
            "weight"  : float(row["WEIGHT"]),
            "inv_rank": str(row["INV_RANK"]),
        })
    return {
        "run_date"    : run_date,
        "regime"      : regime["label"],
        "active_slots": int(regime["active_slots"]),
        "allocation"  : slots,
    }


def diff_allocations(prev: dict, curr: dict) -> list[dict]:
    """
    Compare previous and current allocation dicts.
    Returns a list of change records, one per affected ticker.
    """
    prev_alloc = {s["ticker"]: s for s in prev.get("allocation", [])}
    curr_alloc = {s["ticker"]: s for s in curr.get("allocation", [])}

    prev_holds = {t for t, s in prev_alloc.items() if t != "CASH"}
    curr_holds = {t for t, s in curr_alloc.items() if t != "CASH"}

    changes = []

    # BUY — new entrants
    for t in sorted(curr_holds - prev_holds):
        s = curr_alloc[t]
        changes.append({
            "action"  : "BUY",
            "ticker"  : t,
            "etf_name": s["etf_name"],
            "sector"  : s["sector"],
            "prev_wt" : 0.0,
            "curr_wt" : s["weight"],
            "prev_rk" : "-",
            "curr_rk" : s["inv_rank"],
            "note"    : "New entry",
        })

    # SELL — exits
    for t in sorted(prev_holds - curr_holds):
        s = prev_alloc[t]
        # Check if it moved to cash or just dropped off
        in_curr_as_cash = any(
            sl["ticker"] == "CASH" for sl in curr["allocation"]
        )
        changes.append({
            "action"  : "SELL",
            "ticker"  : t,
            "etf_name": s["etf_name"],
            "sector"  : s["sector"],
            "prev_wt" : s["weight"],
            "curr_wt" : 0.0,
            "prev_rk" : s["inv_rank"],
            "curr_rk" : "-",
            "note"    : "Exited",
        })

    # HOLD / ADD / TRIM — existing positions
    for t in sorted(prev_holds & curr_holds):
        ps = prev_alloc[t]
        cs = curr_alloc[t]
        pw = ps["weight"]
        cw = cs["weight"]
        pr = ps["inv_rank"]
        cr = cs["inv_rank"]

        if abs(cw - pw) < 0.001:
            action = "HOLD"
            note   = "No change"
        elif cw > pw:
            action = "ADD"
            note   = f"Weight increased {pw:.1%} -> {cw:.1%}"
        else:
            action = "TRIM"
            note   = f"Weight reduced {pw:.1%} -> {cw:.1%}"

        # Flag rank drift even on holds
        try:
            rk_drift = int(pr) - int(cr)
            if abs(rk_drift) >= 3:
                note += f"  |  Rank: {pr} -> {cr} ({'+' if rk_drift>0 else ''}{rk_drift})"
        except (ValueError, TypeError):
            pass

        changes.append({
            "action"  : action,
            "ticker"  : t,
            "etf_name": cs["etf_name"],
            "sector"  : cs["sector"],
            "prev_wt" : pw,
            "curr_wt" : cw,
            "prev_rk" : pr,
            "curr_rk" : cr,
            "note"    : note,
        })

    # REGIME CHANGE note
    prev_regime = prev.get("regime", "")
    curr_regime = curr.get("regime", "")
    if prev_regime != curr_regime:
        changes.insert(0, {
            "action"  : "REGIME",
            "ticker"  : "—",
            "etf_name": f"Regime changed: {prev_regime} -> {curr_regime}",
            "sector"  : "—",
            "prev_wt" : 0.0,
            "curr_wt" : 0.0,
            "prev_rk" : "-",
            "curr_rk" : "-",
            "note"    : f"Active slots: {prev.get('active_slots','?')} -> {curr.get('active_slots','?')}",
        })

    # Sort: REGIME first, then SELL, BUY, ADD, TRIM, HOLD
    order = {"REGIME":0,"SELL":1,"BUY":2,"ADD":3,"TRIM":4,"HOLD":5}
    changes.sort(key=lambda x: order.get(x["action"], 9))
    return changes


def update_log(script_dir: Path, allocation: pd.DataFrame,
               regime: dict) -> tuple[dict, list[dict]]:
    """
    Load log, diff vs previous month, save updated log.
    Returns (prev_entry_or_None, list_of_changes).
    """
    log      = load_holdings_log(script_dir)
    month_key = datetime.today().strftime("%Y-%m")
    run_date  = datetime.today().strftime("%Y-%m-%d %H:%M")

    curr_entry = record_to_log(allocation, regime, run_date)

    # Find most recent previous month entry
    sorted_keys = sorted(log.keys())
    prev_keys   = [k for k in sorted_keys if k < month_key]
    prev_entry  = log[prev_keys[-1]] if prev_keys else None

    # Compute diff
    changes = diff_allocations(prev_entry, curr_entry) if prev_entry else []

    # Save current month (overwrites if same month run again — latest wins)
    log[month_key] = curr_entry
    save_holdings_log(script_dir, log)

    return prev_entry, changes, log



# =========================================================
# 7. EXCEL OUTPUT
# =========================================================
def _brd():
    s = Side(style="thin", color="BFBFBF")
    return Border(left=s, right=s, top=s, bottom=s)

def _h(ws, row, col, val, bg="1F4E79", fg="FFFFFF", sz=10):
    c = ws.cell(row=row, column=col, value=val)
    c.font      = Font(name="Arial", bold=True, size=sz, color=fg)
    c.fill      = PatternFill("solid", fgColor=bg)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border    = _brd()
    return c

def _d(ws, row, col, val, bg="FFFFFF", fmt=None, bold=False):
    if isinstance(val, float) and np.isnan(val):
        val = None
    c = ws.cell(row=row, column=col, value=val)
    c.font      = Font(name="Arial", size=9, bold=bold)
    c.fill      = PatternFill("solid", fgColor=bg)
    c.alignment = Alignment(horizontal="center", vertical="center")
    c.border    = _brd()
    if fmt:
        c.number_format = fmt
    return c


def _write_rebalance_sheet(wb, prev_entry, changes, log,
                            NAVY, GREEN, DKGREEN, ORANGE, YELLOW, GREY):
    """Write the Rebalance sheet with 3 sections:
       1. Current allocation
       2. Changes vs last month
       3. Last 12 months history
    """
    ACTION_COLORS = {
        "BUY"   : "C6EFCE",   # green
        "SELL"  : "FFC7CE",   # red
        "ADD"   : "DAEEF3",   # light blue
        "TRIM"  : "FFEB9C",   # amber
        "HOLD"  : "F2F2F2",   # grey
        "REGIME": "D9D9D9",   # dark grey
    }
    NAVY2  = "1F4E79"

    wb_sheets = [s.title for s in wb.worksheets]
    if "Rebalance" in wb_sheets:
        del wb["Rebalance"]
    wb.create_sheet("Rebalance", 1)   # insert as second sheet
    wr = wb["Rebalance"]

    row = 1

    def title_row(ws, r, text, cols, bg=NAVY2):
        ws.merge_cells(f"A{r}:{get_column_letter(cols)}{r}")
        c = ws.cell(row=r, column=1, value=text)
        c.font      = Font(name="Arial", bold=True, size=12, color="FFFFFF")
        c.fill      = PatternFill("solid", fgColor=bg)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[r].height = 20

    def hdr_row(ws, r, hdrs, widths, bg=NAVY2):
        for ci, (h, w) in enumerate(zip(hdrs, widths), 1):
            c = ws.cell(row=r, column=ci, value=h)
            c.font      = Font(name="Arial", bold=True, size=9, color="FFFFFF")
            c.fill      = PatternFill("solid", fgColor=bg)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border    = _brd()
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.row_dimensions[r].height = 28

    def data_row(ws, r, vals, fmts, bg="F2F2F2", bold=False):
        for ci, (v, f) in enumerate(zip(vals, fmts), 1):
            if isinstance(v, float) and np.isnan(v): v = None
            c = ws.cell(row=r, column=ci, value=v)
            c.font      = Font(name="Arial", size=9, bold=bold)
            c.fill      = PatternFill("solid", fgColor=bg)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border    = _brd()
            if f and f != "@": c.number_format = f
        ws.row_dimensions[r].height = 15

    # ── Section 1: Current Allocation ─────────────────────────────
    title_row(wr, row, "SECTION 1 — CURRENT ALLOCATION", 7)
    row += 1
    hdr_row(wr, row,
            ["Slot","Inv Rank","Ticker","ETF Name","Sector","Weight","Action"],
            [7, 9, 14, 40, 18, 9, 9])
    row += 1

    curr_tickers = set()
    prev_tickers = set(
        s["ticker"] for s in (prev_entry or {}).get("allocation", [])
        if s["ticker"] != "CASH"
    ) if prev_entry else set()

    # Build quick lookup from changes
    change_map = {c["ticker"]: c["action"] for c in (changes or [])}

    from datetime import datetime
    curr_month = datetime.today().strftime("%Y-%m")
    curr_entry = log.get(curr_month, {})
    for sl in curr_entry.get("allocation", []):
        t      = sl["ticker"]
        action = change_map.get(t, "HOLD") if t != "CASH" else "CASH"
        bg     = ACTION_COLORS.get(action, GREY)
        if t != "CASH": curr_tickers.add(t)
        data_row(wr, row,
                 [sl["slot"], sl["inv_rank"], t,
                  sl["etf_name"], sl["sector"],
                  sl["weight"], action],
                 ["0","@","@","@","@","0%","@"],
                 bg=bg, bold=(t != "CASH"))
        row += 1

    row += 1  # spacer

    # ── Section 2: Changes vs Previous Month ──────────────────────
    prev_month_label = prev_entry.get("run_date","N/A")[:7] if prev_entry else "N/A"
    title_row(wr, row,
              f"SECTION 2 — CHANGES vs PREVIOUS ({prev_month_label})", 7,
              bg="375623" if prev_entry else "7F6000")
    row += 1

    if not changes:
        wr.merge_cells(f"A{row}:G{row}")
        c = wr.cell(row=row, column=1,
                    value="No previous month data — this is the first recorded rebalance.")
        c.font      = Font(name="Arial", italic=True, size=9)
        c.alignment = Alignment(horizontal="left", vertical="center")
        row += 1
    else:
        hdr_row(wr, row,
                ["Action","Ticker","ETF Name","Sector",
                 "Prev Wt","Curr Wt","Note"],
                [9, 14, 38, 18, 9, 9, 48])
        row += 1
        for ch in changes:
            bg   = ACTION_COLORS.get(ch["action"], GREY)
            bold = ch["action"] in ("BUY","SELL","REGIME")
            data_row(wr, row,
                     [ch["action"], ch["ticker"], ch["etf_name"],
                      ch["sector"], ch["prev_wt"] or None,
                      ch["curr_wt"] or None, ch["note"]],
                     ["@","@","@","@","0%","0%","@"],
                     bg=bg, bold=bold)
            row += 1

    row += 1  # spacer

    # ── Section 3: 12-Month History ───────────────────────────────
    title_row(wr, row, f"SECTION 3 — LAST {HISTORY_MONTHS} MONTHS HISTORY", 7,
              bg="203864")
    row += 1

    sorted_months = sorted(log.keys())[-HISTORY_MONTHS:]

    # Collect all unique tickers ever held (excluding CASH)
    all_tickers: list[str] = []
    seen: set[str] = set()
    for mk in reversed(sorted_months):
        for sl in log[mk].get("allocation", []):
            t = sl["ticker"]
            if t != "CASH" and t not in seen:
                all_tickers.append(t)
                seen.add(t)

    # Header: Month | Regime | Ticker1 | Ticker2 | ...
    hdrs   = ["Month", "Regime"] + all_tickers
    widths = [12, 24] + [12] * len(all_tickers)
    hdr_row(wr, row, hdrs, widths)
    row += 1

    for mk in sorted_months:
        entry    = log[mk]
        regime_l = entry.get("regime", "")
        held     = {s["ticker"]: s["weight"]
                    for s in entry.get("allocation", [])
                    if s["ticker"] != "CASH"}

        regime_bg = ("E2EFDA" if "BULL" in regime_l else
                     "FCE4D6" if "BEAR" in regime_l else "FFF2CC")

        vals = [mk, regime_l]
        fmts = ["@", "@"]
        for t in all_tickers:
            w = held.get(t)
            vals.append(w)
            fmts.append("0%" if w is not None else "@")

        # Write row cell by cell for per-cell colouring
        for ci, (v, f) in enumerate(zip(vals, fmts), 1):
            if isinstance(v, float) and np.isnan(v): v = None
            c = wr.cell(row=row, column=ci, value=v)
            c.font      = Font(name="Arial", size=9)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border    = _brd()
            if ci <= 2:
                c.fill = PatternFill("solid", fgColor=regime_bg)
                c.font = Font(name="Arial", size=9, bold=(ci==1))
            elif v is not None:
                c.fill = PatternFill("solid", fgColor=DKGREEN)
                c.font = Font(name="Arial", size=9, bold=True)
            else:
                c.fill = PatternFill("solid", fgColor="F2F2F2")
            if f and f != "@": c.number_format = f
        wr.row_dimensions[row].height = 15
        row += 1

    wr.freeze_panes = "A2"


def save_excel(df, regime, allocation, out_path, prev_entry=None, changes=None, log=None):
    wb = Workbook()
    NAVY    = "1F4E79"
    GREEN   = "E2EFDA"
    DKGREEN = "C6EFCE"
    ORANGE  = "FCE4D6"
    YELLOW  = "FFF2CC"
    GREY    = "F2F2F2"
    BULL_C  = "375623"
    BEAR_C  = "C00000"
    PART_C  = "7F6000"

    regime_color = (BULL_C if regime["label"] == "BULL" else
                    BEAR_C if "BEAR" in regime["label"] else PART_C)

    # ── Sheet 1: Rankings ──────────────────────────────────────────
    ws = wb.active
    ws.title = "Rankings"

    # Title
    ws.merge_cells("A1:R1")
    c = ws["A1"]
    c.value     = ("ETF Momentum Ranking  |  "
                   "Step 1: Screen (abs filter)  ->  "
                   "Step 2: Score (Weighted Sharpe)  ->  "
                   "Step 3: Regime  ->  Step 4: Allocate")
    c.font      = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    c.fill      = PatternFill("solid", fgColor=NAVY)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 22

    # Regime row
    ws.merge_cells("A2:R2")
    r = regime
    rtext = (f"REGIME: {r['label']}  |  Active slots: {r['active_slots']}/{CONFIG.TOP_N}  |  "
             f"Layer 1 Trend ({r['trend_ticker']} vs {CONFIG.TREND_SMA_WINDOW}d SMA): "
             f"{'PASS' if r['trend_ok'] else 'FAIL'} ({r['nifty_price']:.2f} vs {r['nifty_sma']:.2f})  |  "
             f"Layer 2 Breadth (>={CONFIG.BREADTH_THRESHOLD:.0%} above {CONFIG.BREADTH_SMA_WINDOW}d SMA): "
             f"{'PASS' if r['breadth_ok'] else 'FAIL'} ({r['breadth_pct']:.1%})")
    rc = ws["A2"]
    rc.value     = rtext
    rc.font      = Font(name="Arial", bold=True, size=10, color="FFFFFF")
    rc.fill      = PatternFill("solid", fgColor=regime_color)
    rc.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 18

    COLS = [
        ("Investable\nRank",    10, "0"),
        ("Universe\nRank",      10, "0"),
        ("Sharpe\nRank",         9, "0"),
        ("DM 6M\nRank",          9, "0"),
        ("Ticker",              14, "@"),
        ("ETF Name",            40, "@"),
        ("Sector",              18, "@"),
        ("Close",               10, "0.00"),
        ("52Wk\nHigh",          10, "0.00"),
        ("% From\n52Wk High",   12, "0.00"),
        ("Wtd Sharpe\nScore",   14, "0.000"),
        ("Sharpe\n6M",          13, "0.000"),
        ("Sharpe\n3M",          13, "0.000"),
        ("DM Ret\n6M (%)",      13, "0.00"),
        ("DM Ret\n3M (%)",      13, "0.00"),
        ("Abs Momo\nFilter",    11, "@"),
        ("52Wk High\nFilter",   11, "@"),
        ("Screen\nResult",      11, "@"),
    ]
    KEYS = [
        "RANK_INVESTABLE", "RANK_UNIVERSE", "RANK_SHARPE", "RANK_DM_6M",
        "TICKER", "ETF_NAME", "SECTOR", "CLOSE", "52WK_HIGH", "PCT_FROM_HIGH",
        "WTD_SHARPE", "SHARPE_6M", "SHARPE_3M",
        "DM_RET_6M_PCT", "DM_RET_3M_PCT", "ABS_PASS", "HIGH_PASS", "SCREEN_PASS",
    ]

    HDR = 3
    for ci, (hdr, width, _) in enumerate(COLS, 1):
        _h(ws, HDR, ci, hdr)
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[HDR].height = 32

    for ri, (_, row) in enumerate(df.iterrows(), start=HDR + 1):
        passed  = row["SCREEN_PASS"]
        inv_rk  = row["RANK_INVESTABLE"]
        in_alloc = passed and (inv_rk > 0) and (inv_rk <= regime["active_slots"])
        bg = (DKGREEN if in_alloc else
              GREEN   if passed else
              ORANGE)

        for ci, (key, (_, _, fmt)) in enumerate(zip(KEYS, COLS), 1):
            val = row[key]
            if key in ("ABS_PASS", "HIGH_PASS", "SCREEN_PASS"):
                val = "PASS" if val else "FAIL"
            elif key == "RANK_INVESTABLE" and (not passed or val == 0):
                val = "-"
            _d(ws, ri, ci, val, bg=bg,
               fmt=fmt if fmt != "@" else None, bold=in_alloc)
        ws.row_dimensions[ri].height = 14

    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(COLS))}{HDR + len(df)}"

    # ── Sheet 2: Allocation ────────────────────────────────────────
    wa = wb.create_sheet("Allocation")
    wa.merge_cells("A1:F1")
    c = wa["A1"]
    c.value     = (f"Top-{CONFIG.TOP_N} Allocation  |  Hurdle={CONFIG.HURDLE_6M*100:.1f}%  |  "
                   f"Regime={regime['label']}  |  Active slots={regime['active_slots']}")
    c.font      = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    c.fill      = PatternFill("solid", fgColor=regime_color)
    c.alignment = Alignment(horizontal="center", vertical="center")
    wa.row_dimensions[1].height = 22

    for ci, (hdr, w) in enumerate(
            zip(["Slot", "Inv Rank", "Sector", "Ticker", "ETF Name", "Weight", "Detail"],
                [8, 10, 18, 14, 40, 10, 55]), 1):
        _h(wa, 2, ci, hdr)
        wa.column_dimensions[get_column_letter(ci)].width = w
    wa.row_dimensions[2].height = 22
    wa.merge_cells("A1:G1")

    for ri, (_, row) in enumerate(allocation.iterrows(), start=3):
        is_cash = row["TICKER"] == "CASH"
        is_buf  = is_cash and "buffer" in str(row["REASON"])
        bg = YELLOW if is_buf else (ORANGE if is_cash else DKGREEN)
        for ci, (v, f) in enumerate(
                zip([row["SLOT"], row["INV_RANK"], row.get("SECTOR",""),
                     row["TICKER"], row["ETF_NAME"], row["WEIGHT"], row["REASON"]],
                    ["0", "@", "@", "@", "@", "0.0%", "@"]), 1):
            _d(wa, ri, ci, v, bg=bg, fmt=f if f != "@" else None, bold=True)
        wa.row_dimensions[ri].height = 18

    # ── Sheet 3: Regime Detail ─────────────────────────────────────
    wr = wb.create_sheet("Regime")
    wr.column_dimensions["A"].width = 40
    wr.column_dimensions["B"].width = 25
    _h(wr, 1, 1, "Regime Parameter", bg=NAVY)
    _h(wr, 1, 2, "Value / Status", bg=NAVY)

    regime_rows = [
        ("Regime Label",                                  r["label"]),
        ("Active slots",                                  f"{r['active_slots']} of {CONFIG.TOP_N}"),
        ("--- TIERED LOGIC ---",                          ""),
        ("BULL  (both pass)  -> slots active",            str(CONFIG.TOP_N)),
        ("PARTIAL (one fail) -> slots active",            str(CONFIG.TOP_N_PARTIAL)),
        ("BEAR  (both fail)  -> slots active",            "0  (full cash)"),
        ("--- LAYER 1: TREND ---",                        ""),
        ("Index used",                                    r["trend_ticker"]),
        ("Current price",                                 f"{r['nifty_price']:.2f}"),
        (f"{CONFIG.TREND_SMA_WINDOW}-day SMA",            f"{r['nifty_sma']:.2f}"),
        ("Price above SMA",                               str(r["trend_ok"])),
        ("--- LAYER 2: BREADTH ---",                      ""),
        (f"SMA window",                                   f"{CONFIG.BREADTH_SMA_WINDOW} days"),
        ("% ETFs above their SMA",                        f"{r['breadth_pct']:.1%}"),
        ("Required threshold",                            f">= {CONFIG.BREADTH_THRESHOLD:.0%}"),
        ("Breadth threshold met",                         str(r["breadth_ok"])),
    ]
    for ri2, (lbl, val) in enumerate(regime_rows, start=2):
        is_section = lbl.startswith("---")
        ok_bg = (NAVY   if is_section else
                 GREEN  if "True"  in str(val) or "BULL" in str(val) else
                 ORANGE if "False" in str(val) or "BEAR" in str(val) else
                 GREY)
        fg = "FFFFFF" if is_section else "000000"
        c1 = _d(wr, ri2, 1, lbl, bg=ok_bg if not is_section else NAVY)
        c2 = _d(wr, ri2, 2, val, bg=ok_bg if not is_section else NAVY)
        if is_section:
            c1.font = Font(name="Arial", bold=True, size=9, color="FFFFFF")
            c2.font = Font(name="Arial", bold=True, size=9, color="FFFFFF")

    # ── Sheet 4: Rebalance Tracker ───────────────────────────
    if changes is not None:
        _write_rebalance_sheet(wb, prev_entry, changes, log,
                               NAVY, GREEN, DKGREEN, ORANGE, YELLOW, GREY)

    wb.save(out_path)
    print(f"\n[saved] -> {Path(out_path).resolve()}")


# =========================================================
# 8. MAIN
# =========================================================
if __name__ == "__main__":
    SCRIPT_DIR = Path(__file__).resolve().parent
    fp  = sys.argv[1] if len(sys.argv) > 1 else str(SCRIPT_DIR / CONFIG.INPUT_FILE)
    out = sys.argv[2] if len(sys.argv) > 2 else str(SCRIPT_DIR / CONFIG.OUTPUT_FILE)

    print(f"[load]   {fp}")
    meta, prices = load_etf_data(fp)
    print(f"         {len(meta)} ETFs | {len(prices)} days "
          f"({prices.index[0].date()} -> {prices.index[-1].date()})")

    print("[regime] Computing tiered regime filter ...")
    regime = regime_status(prices)

    print("[scores] Screening + scoring all ETFs ...")
    ranking = build_ranking(meta, prices)

    print("[alloc]  Building allocation ...")
    allocation = build_allocation(ranking, regime)

    print_summary(ranking, regime, allocation)

    print("[log]    Updating holdings log ...")
    SCRIPT_DIR = Path(__file__).resolve().parent
    prev_entry, changes, log = update_log(SCRIPT_DIR, allocation, regime)

    if prev_entry:
        prev_month = prev_entry.get("run_date","?")[:7]
        print(f"         Previous month: {prev_month}  |  Changes: {len(changes)}")
        for ch in changes:
            arrow = {"BUY":"+ BUY","SELL":"- SELL","ADD":"↑ ADD",
                     "TRIM":"↓ TRIM","HOLD":"= HOLD","REGIME":"⚑ REGIME"}.get(ch["action"],"  ")
            print(f"           {arrow:8s} {ch['ticker']:<14} {ch['note']}")
    else:
        print("         First run — no previous holdings to compare.")

    save_excel(ranking, regime, allocation, out,
               prev_entry=prev_entry, changes=changes, log=log)