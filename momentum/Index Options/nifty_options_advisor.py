#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
  Nifty Options Advisor  v2.0
  Reads the NSE option chain CSV automatically — no manual
  premium lookup needed.

  HOW TO USE
  ──────────
  1. Go to  https://www.nseindia.com/option-chain
  2. Select  NIFTY  +  your target expiry date
  3. Click   Download (.csv)
  4. Rename the file to  nifty_chain.csv
  5. Place it in the SAME folder as this script
  6. Run:  python nifty_options_advisor.py

  REQUIREMENTS
  ────────────
  pip install colorama tabulate pandas
═══════════════════════════════════════════════════════════════
"""

import os
import sys
import math
import re
from datetime import date, timedelta
from colorama import init, Fore, Style
from tabulate import tabulate
import pandas as pd

init(autoreset=True)

# ── Your trading profile ─────────────────────────────────────
CAPITAL         = 150000    # ₹1.5 lakh
NIFTY_LOT_SIZE  = 75        # units — verify after SEBI revisions
BROKERAGE_RT    = 40        # ₹20 × 2 sides on Dhan
STRIKE_INTERVAL = 50        # Nifty strikes every 50 pts
MAX_PREMIUM_LOT = 21000     # ₹280 × 75 — your cost ceiling
MIN_DTE         = 6         # minimum days to expiry at entry
VIX_CAUTION     = 20.0
VIX_SKIP        = 22.0
CSV_FILENAME    = None   # auto-detected — see find_nse_csv()

# ── Colour helpers ───────────────────────────────────────────
def green(t):  return Fore.GREEN  + Style.BRIGHT + str(t) + Style.RESET_ALL
def red(t):    return Fore.RED    + Style.BRIGHT + str(t) + Style.RESET_ALL
def yellow(t): return Fore.YELLOW + Style.BRIGHT + str(t) + Style.RESET_ALL
def cyan(t):   return Fore.CYAN   + Style.BRIGHT + str(t) + Style.RESET_ALL
def bold(t):   return Style.BRIGHT + str(t) + Style.RESET_ALL
def dim(t):    return Style.DIM    + str(t) + Style.RESET_ALL

def tick(msg):  return green("  ✓  ") + msg
def cross(msg): return red("  ✗  ") + msg
def warn(msg):  return yellow("  ⚠  ") + msg
def info(msg):  return cyan("  ●  ") + msg

def section(title):
    print()
    print(cyan("─" * 60))
    print(cyan("  " + title.upper()))
    print(cyan("─" * 60))

# ── Input helpers ────────────────────────────────────────────
def ask_float(prompt, min_val=None, max_val=None):
    while True:
        try:
            val = float(input(prompt).strip().replace(",", ""))
            if min_val is not None and val < min_val:
                print(red(f"    Must be >= {min_val}"))
                continue
            if max_val is not None and val > max_val:
                print(red(f"    Must be <= {max_val}"))
                continue
            return val
        except ValueError:
            print(red("    Please enter a valid number"))

def ask_choice(prompt, choices):
    choices_lower = [c.lower() for c in choices]
    while True:
        val = input(prompt).strip().lower()
        if val in choices_lower:
            return val
        print(red(f"    Enter one of: {' / '.join(choices)}"))


# ── NSE CSV auto-detection ───────────────────────────────────
# Accepts any NSE option chain filename without renaming, e.g.:
#   OptionChain_NIFTY_19-03-2026.csv
#   option-chain-ED-NIFTY-24-Mar-2026.csv
# Picks the newest matching CSV in the script folder.
def find_nse_csv(script_dir):
    import glob
    found = []
    for f in glob.glob(os.path.join(script_dir, "*.csv")):
        name = os.path.basename(f).lower()
        if "nifty" in name or "option" in name:
            found.append(f)
    found = list(set(found))
    found.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return found[0] if found else None

# ── NSE CSV parser ───────────────────────────────────────────
# Handles the NSE option chain download format:
#   Row 0 : "CALLS,,PUTS"  (3-col merged header — ignored)
#   Row 1 : column names — 23 cols, col 0 blank
#            col  1 = Call OI      col 11 = STRIKE
#            col  4 = Call IV      col 17 = Put LTP
#            col  5 = Call LTP     col 18 = Put IV
#            col 21 = Put OI
#   Row 2+ : data rows (leading empty col, Indian number formatting)
#
# Indian number format ("2,67,014") — strip ALL commas before parsing.

def _clean(val):
    """Strip commas/spaces, return float or None."""
    if val is None:
        return None
    v = str(val).strip().replace(",", "").replace("\r", "")
    if v in ("", "-", "--", "nan"):
        return None
    try:
        return float(v)
    except ValueError:
        return None

def parse_nse_csv(filepath):
    import csv as _csv
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            rows = list(_csv.reader(f))
    except FileNotFoundError:
        return None
    except Exception as e:
        print(red(f"    Error reading CSV: {e}"))
        return None

    # Find header row — the one containing "STRIKE" or "Strike Price"
    header_idx = None
    for i, row in enumerate(rows):
        joined = ",".join(row).upper()
        if "STRIKE" in joined and ("LTP" in joined or "BID" in joined):
            header_idx = i
            break

    if header_idx is None:
        print(red("    Cannot find header row in CSV (expected STRIKE + LTP columns)."))
        return None

    headers = [h.strip().upper() for h in rows[header_idx]]

    # Locate column indices by name
    # There are duplicate names (OI, IV, LTP appear for both calls and puts).
    # Calls are LEFT of STRIKE, puts are RIGHT — find strike position first.
    try:
        strike_col = headers.index("STRIKE")
    except ValueError:
        # Fallback: try "STRIKE PRICE"
        strike_col = next((i for i,h in enumerate(headers) if "STRIKE" in h), None)
        if strike_col is None:
            print(red("    STRIKE column not found in CSV."))
            return None

    # All LTP/IV/OI occurrences
    ltp_cols = [i for i,h in enumerate(headers) if h == "LTP"]
    iv_cols  = [i for i,h in enumerate(headers) if h == "IV"]
    oi_cols  = [i for i,h in enumerate(headers) if h == "OI"]

    # Calls: first occurrence (left of STRIKE); Puts: first occurrence right of STRIKE
    call_ltp = ltp_cols[0]  if ltp_cols else None
    put_ltp  = next((c for c in ltp_cols if c > strike_col), None)
    call_iv  = iv_cols[0]   if iv_cols  else None
    put_iv   = next((c for c in iv_cols  if c > strike_col), None)
    call_oi  = oi_cols[0]   if oi_cols  else None
    put_oi   = next((c for c in oi_cols  if c > strike_col), None)

    if call_ltp is None or put_ltp is None:
        print(red("    Could not locate LTP columns in CSV."))
        return None

    # Parse data rows
    records = []
    for row in rows[header_idx + 1:]:
        if len(row) <= strike_col:
            continue
        strike = _clean(row[strike_col])
        if strike is None or strike <= 0:
            continue
        records.append({
            "strike"  : int(strike),
            "call_ltp": _clean(row[call_ltp] if call_ltp < len(row) else None),
            "put_ltp" : _clean(row[put_ltp]  if put_ltp  < len(row) else None),
            "call_iv" : _clean(row[call_iv]  if call_iv  and call_iv  < len(row) else None),
            "put_iv"  : _clean(row[put_iv]   if put_iv   and put_iv   < len(row) else None),
            "call_oi" : _clean(row[call_oi]  if call_oi  and call_oi  < len(row) else None),
            "put_oi"  : _clean(row[put_oi]   if put_oi   and put_oi   < len(row) else None),
        })

    if not records:
        print(red("    No valid data rows found in CSV."))
        return None

    df = pd.DataFrame(records).sort_values("strike").reset_index(drop=True)
    return df

# ── Strategy helpers ─────────────────────────────────────────
def atm_strike(spot):
    return int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)

def otm_strike(spot, direction):
    atm = atm_strike(spot)
    return atm + STRIKE_INTERVAL if direction == "bull" else atm - STRIKE_INTERVAL

def next_thursday(from_date):
    days = 3 - from_date.weekday()
    if days <= 0:
        days += 7
    return from_date + timedelta(days=days)

def expiry_rec(today, vix):
    cur = next_thursday(today)
    nxt = next_thursday(cur + timedelta(days=1))
    dte_cur = (cur - today).days
    dte_nxt = (nxt - today).days
    if today.weekday() == 3:
        return nxt, f"Today is Thursday — current expiry is today, using next week ({dte_nxt} DTE)"
    if dte_cur < MIN_DTE:
        return nxt, f"Current expiry only {dte_cur} DTE — using next week ({dte_nxt} DTE)"
    if vix >= VIX_CAUTION:
        return nxt, f"VIX {vix:.1f} elevated — next expiry for theta buffer ({dte_nxt} DTE)"
    return cur, f"Current expiry has {dte_cur} DTE — sufficient"

def check_st(flipped, direction):
    if not flipped:
        return False, "No fresh SuperTrend flip today — no signal"
    label = "BULLISH" if direction == "bull" else "BEARISH"
    return True, f"SuperTrend flipped {label} today"

def check_ema(spot, ema, direction):
    ok  = spot > ema if direction == "bull" else spot < ema
    sym = ">" if direction == "bull" else "<"
    act = ">" if spot > ema else "<"
    return ok, f"Nifty {spot:.0f} {act} EMA {ema:.0f}  (need {sym})"

def check_adx(adx):
    ok = adx >= 20
    s  = "strong" if adx >= 30 else "moderate" if adx >= 20 else "weak (< 20)"
    return ok, f"ADX {adx:.1f} — {s}"

def check_rsi(rsi, direction):
    if direction == "bull":
        ok = rsi >= 45
        return ok, f"RSI {rsi:.1f} {'>=45' if ok else '<45'}  (bull min 45)"
    else:
        ok = rsi <= 55
        return ok, f"RSI {rsi:.1f} {'<=55' if ok else '>55'}  (bear max 55)"

def check_vix(vix):
    if vix < VIX_CAUTION:
        return "green",  f"VIX {vix:.1f} — premiums cheap, safe to trade"
    elif vix <= VIX_SKIP:
        return "yellow", f"VIX {vix:.1f} — elevated, using next expiry for buffer"
    else:
        return "red",    f"VIX {vix:.1f} — too high, skip this trade"

def sl_tp_levels(spot, atr, direction):
    sl_pts = round(1.5 * atr)
    tp_pts = round(3.0 * atr)
    if direction == "bull":
        return {"sl": round(spot - sl_pts), "tp": round(spot + tp_pts),
                "sl_pts": sl_pts, "tp_pts": tp_pts, "rr": round(tp_pts / sl_pts, 1)}
    else:
        return {"sl": round(spot + sl_pts), "tp": round(spot - tp_pts),
                "sl_pts": sl_pts, "tp_pts": tp_pts, "rr": round(tp_pts / sl_pts, 1)}

def get_from_chain(chain, strike, col):
    row = chain[chain["strike"] == strike]
    if row.empty:
        return None
    val = row[col].values[0]
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(val)

# ════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════

# ── Extract expiry label from CSV filename ────────────────────
def csv_expiry_label(filepath):
    name = os.path.basename(filepath)
    # Matches: option-chain-ED-NIFTY-24-Mar-2026.csv  or  OptionChain_NIFTY_19-03-2026.csv
    m = re.search(r"(\d{2}[-_][A-Za-z]{3}[-_]\d{4}|\d{2}[-_]\d{2}[-_]\d{4})", name)
    if m:
        return m.group().replace("_", "-")
    return name  # fallback: just show filename

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print()
    print(bold("=" * 60))
    print(bold("  NIFTY OPTIONS ADVISOR  v2.0"))
    print(bold("  SuperTrend Daily Strategy · ATM Strikes · Dhan"))
    print(bold("=" * 60))
    print(dim("  Run after 3:30 PM — values from your TradingView chart"))

    # ── Load CSV ─────────────────────────────────────────────
    section("0. Loading Option Chain CSV")

    csv_path = find_nse_csv(script_dir)

    if csv_path is None:
        print(red("  No NSE option chain CSV found in:"))
        print(red(f"  {script_dir}"))
        print()
        print("  Steps:")
        print("  1. Go to  https://www.nseindia.com/option-chain")
        print("  2. Select NIFTY + your target expiry date")
        print("  3. Click  Download (.csv)")
        print(f"  4. Place the downloaded file (do NOT rename) in:")
        print(f"     {script_dir}")
        print()
        print(dim("  The script accepts any filename NSE gives — e.g.:"))
        print(dim("  OptionChain_NIFTY_19-03-2026.csv"))
        sys.exit(1)

    csv_name = os.path.basename(csv_path)
    print(tick(f"Found: {csv_name}"))

    chain = parse_nse_csv(csv_path)
    if chain is None or chain.empty:
        print(red("  CSV parse failed — see errors above."))
        sys.exit(1)

    exp_label = csv_expiry_label(csv_path)
    print(tick(f"Loaded {len(chain)} strikes  |  expiry: {exp_label}"))
    print(info(f"Strike range: {chain['strike'].min()} - {chain['strike'].max()}"))

    # ── Chart inputs ─────────────────────────────────────────
    section("1. Chart Inputs  (from TradingView after 3:30 PM)")

    today = date.today()
    print(info(f"Today: {today.strftime('%A, %d %b %Y')}"))
    print()

    spot    = ask_float("  Nifty closing price  : ", 10000, 30000)
    ema_200 = ask_float("  EMA 200 value        : ", 10000, 30000)
    atr     = ask_float("  ATR (14) value       : ", 1, 1000)
    adx     = ask_float("  ADX value            : ", 0, 100)
    rsi     = ask_float("  RSI (14) value       : ", 0, 100)
    vix     = ask_float("  India VIX            : ", 5, 60)
    print()
    flipped    = ask_choice("  ST flipped today?    (y/n)       : ", ["y","n"]) == "y"
    if flipped:
        direction = ask_choice("  Flipped to?          (bull/bear) : ", ["bull","bear"])
    else:
        direction = ask_choice("  Current ST dir?      (bull/bear) : ", ["bull","bear"])

    opt_type = "CE" if direction == "bull" else "PE"
    action   = "BUY CALL (CE)" if direction == "bull" else "BUY PUT  (PE)"

    # ── Filters ──────────────────────────────────────────────
    section("2. Signal & Filter Check")

    sig_ok,  sig_msg = check_st(flipped, direction)
    ema_ok,  ema_msg = check_ema(spot, ema_200, direction)
    adx_ok,  adx_msg = check_adx(adx)
    rsi_ok,  rsi_msg = check_rsi(rsi, direction)
    vix_lvl, vix_msg = check_vix(vix)

    print(tick(sig_msg)  if sig_ok  else cross(sig_msg))
    print(tick(ema_msg)  if ema_ok  else cross(ema_msg))
    print(tick(adx_msg)  if adx_ok  else cross(adx_msg))
    print(tick(rsi_msg)  if rsi_ok  else cross(rsi_msg))
    if   vix_lvl == "green":  print(tick(vix_msg))
    elif vix_lvl == "yellow": print(warn(vix_msg))
    else:                     print(cross(vix_msg))

    print()
    if not sig_ok:
        print(red("  == NO TRADE — No SuperTrend flip today =="))
        print(dim("  Wait for a daily candle that flips ST direction."))
        print(); sys.exit(0)

    if vix_lvl == "red":
        print(red("  == NO TRADE — VIX too high =="))
        print(dim("  Wait for VIX to drop below 22."))
        print(); sys.exit(0)

    filters_ok = ema_ok and adx_ok and rsi_ok
    if filters_ok:
        print(green("  == ALL FILTERS PASSED — HIGH CONFIDENCE =="))
    else:
        failed = [n for n,ok in [("EMA",ema_ok),("ADX",adx_ok),("RSI",rsi_ok)] if not ok]
        print(yellow(f"  == WEAK SIGNAL — {', '.join(failed)} filter(s) failed =="))

    # ── Strike & expiry ──────────────────────────────────────
    section("3. Strike & Expiry")

    atm = atm_strike(spot)
    otm = otm_strike(spot, direction)
    rec = otm if adx >= 30 else atm
    strike_note = (f"ADX {adx:.0f}>=30 (strong) — OTM gives better R:R"
                   if adx >= 30 else
                   f"ADX {adx:.0f}<30 (moderate) — ATM for reliable delta")

    expiry, exp_reason = expiry_rec(today, vix)
    dte           = (expiry - today).days
    exit_deadline = expiry - timedelta(days=1)

    print(f"  ATM : {atm}  |  OTM : {otm}")
    print(f"  Recommended strike : {bold(str(rec))} {opt_type}  ({strike_note})")
    print()
    print(f"  Expiry  : {bold(expiry.strftime('%d %b %Y (%A)'))}  ({dte} DTE)")
    print(f"  Reason  : {exp_reason}")
    print(f"  Exit by : {exit_deadline.strftime('%d %b %Y')} 3:00 PM")

    # ── Premiums from CSV ────────────────────────────────────
    section("4. Premium from Option Chain CSV")

    ltp_col = "call_ltp" if opt_type == "CE" else "put_ltp"
    iv_col  = "call_iv"  if opt_type == "CE" else "put_iv"
    oi_col  = "call_oi"  if opt_type == "CE" else "put_oi"

    # Print strikes within ±150 pts of ATM
    nearby = sorted(s for s in chain["strike"].tolist() if abs(s - atm) <= 150)
    print(f"\n  {'Strike':<10} {opt_type+' LTP':<12} {'IV%':<9} {'OI':<12} {'Note'}")
    print("  " + "-" * 54)
    for s in nearby:
        ltp = get_from_chain(chain, s, ltp_col)
        iv  = get_from_chain(chain, s, iv_col)
        oi  = get_from_chain(chain, s, oi_col)
        note = ""
        if s == rec and s == atm: note = "<-- recommended (ATM)"
        elif s == rec:            note = "<-- recommended (OTM)"
        elif s == atm:            note = "<-- ATM"
        print(f"  {s:<10} {'Rs.'+str(round(ltp,1)) if ltp else '-':<12} "
              f"{str(round(iv,1))+'%' if iv else '-':<9} "
              f"{str(int(oi)) if oi else '-':<12} {note}")

    # Get recommended LTP
    rec_ltp = get_from_chain(chain, rec, ltp_col)
    rec_iv  = get_from_chain(chain, rec, iv_col)
    rec_oi  = get_from_chain(chain, rec, oi_col)

    if rec_ltp is None:
        print()
        print(warn(f"Strike {rec} {opt_type} not in CSV — enter premium manually"))
        actual_premium = ask_float(f"  Premium for {rec} {opt_type} : Rs.", 1, 2000)
        actual_strike  = rec
    else:
        actual_premium = rec_ltp
        actual_strike  = rec
        print()
        print(tick(f"{rec} {opt_type} LTP : Rs.{actual_premium:.1f}  (read from CSV)"))
        if rec_iv:
            iv_note = "low — good time to buy" if rec_iv < 15 else "normal" if rec_iv < 22 else "high — expensive"
            print(info(f"IV  : {rec_iv:.1f}%  ({iv_note})"))
        if rec_oi:
            print(info(f"OI  : {int(rec_oi):,} contracts"))

    # Cost ceiling check — offer OTM fallback
    if actual_premium * NIFTY_LOT_SIZE > MAX_PREMIUM_LOT:
        print()
        print(warn(f"1 lot = Rs.{actual_premium*NIFTY_LOT_SIZE:,.0f} — above Rs.21,000 ceiling"))
        otm_ltp = get_from_chain(chain, otm, ltp_col)
        if otm_ltp and otm_ltp * NIFTY_LOT_SIZE <= MAX_PREMIUM_LOT:
            actual_strike  = otm
            actual_premium = otm_ltp
            print(tick(f"Switching to OTM {otm} {opt_type} @ Rs.{actual_premium:.1f}  (Rs.{actual_premium*NIFTY_LOT_SIZE:,.0f}/lot)"))
        else:
            print(warn("OTM also above limit. Proceed? (y/n)"))
            if ask_choice("  ", ["y","n"]) == "n":
                print(dim("  Trade skipped.")); print(); sys.exit(0)

    # ── Sizing ───────────────────────────────────────────────
    section("5. Position Sizing")

    total_cost = actual_premium * NIFTY_LOT_SIZE + BROKERAGE_RT
    pct_cap    = total_cost / CAPITAL * 100

    print()
    print(tabulate([
        ["Lots",             "1"],
        ["Quantity",         f"{NIFTY_LOT_SIZE} units"],
        ["Premium/unit",     f"Rs.{actual_premium:.1f}"],
        ["Total premium",    f"Rs.{actual_premium*NIFTY_LOT_SIZE:,.0f}"],
        ["Brokerage (Dhan)", f"Rs.{BROKERAGE_RT}"],
        ["Total outlay",     f"Rs.{total_cost:,.0f}"],
        ["% of capital",     f"{pct_cap:.1f}%"],
    ], tablefmt="simple", colalign=("left","right")))

    # ── SL / TP ──────────────────────────────────────────────
    section("6. Stop-Loss & Take-Profit  (Nifty chart levels)")

    lv = sl_tp_levels(spot, atr, direction)
    print(f"  Entry (Nifty) : {spot:.0f}")
    print(f"  Stop-Loss     : {red(str(lv['sl']))}   ({lv['sl_pts']:.0f} pts  1.5 x ATR {atr:.0f})")
    print(f"  Take-Profit   : {green(str(lv['tp']))}   ({lv['tp_pts']:.0f} pts  3.0 x ATR {atr:.0f})")
    print(f"  R:R ratio     : 1 : {lv['rr']}")
    print()
    print(dim("  Exit when NIFTY SPOT hits these levels — not option premium."))

    # ── Order instructions ───────────────────────────────────
    section("7. Order Instructions  (Dhan)")

    limit_px = round(actual_premium + 2)
    print(f"  1. Markets -> Option Chain -> NIFTY -> {expiry.strftime('%d-%b-%Y')}")
    print(f"  2. Strike  : {bold(str(actual_strike))}   Type : {bold(opt_type)}")
    print(f"  3. Product : {bold('CNC / Normal')}  (NOT MIS / intraday)")
    print(f"  4. Order   : {bold('Limit')} @ Rs.{limit_px}  (LTP + Rs.2 buffer)")
    print(f"  5. Qty     : {bold(str(NIFTY_LOT_SIZE))}  (1 lot)")
    print(f"  6. Time    : {bold('9:20 AM')} tomorrow")
    print()
    print(f"  Set alert on Dhan -> Nifty spot below {bold(str(lv['sl']))}")
    print(f"  When triggered -> check if ST flipped -> exit option")

    # ── Summary ──────────────────────────────────────────────
    section("8. Trade Summary")

    rows = [
        ["Signal",        action],
        ["Confidence",    "HIGH" if filters_ok else "WEAK"],
        ["Strike",        f"{actual_strike} {opt_type}"],
        ["Expiry",        expiry.strftime("%d %b %Y")],
        ["DTE",           f"{dte} days"],
        ["Premium",       f"Rs.{actual_premium:.1f}"],
        ["Total outlay",  f"Rs.{total_cost:,.0f}"],
        ["% of capital",  f"{pct_cap:.1f}%"],
        ["Nifty SL",      str(lv["sl"])],
        ["Nifty TP",      str(lv["tp"])],
        ["R:R",           f"1 : {lv['rr']}"],
        ["Exit deadline", exit_deadline.strftime("%d %b %Y") + " 3 PM"],
    ]
    if rec_iv:
        rows.append(["Impl. Vol.", f"{rec_iv:.1f}%"])

    print()
    print(tabulate(rows, headers=["Field","Value"],
                   tablefmt="rounded_outline", colalign=("left","left")))

    print()
    print(bold("=" * 60))
    if filters_ok:
        print(green(f"  RECOMMENDATION  ->  PLACE THE TRADE"))
        print(green(f"  {actual_strike} {opt_type}  |  {expiry.strftime('%d %b')}  |  Rs.{actual_premium:.0f} premium  |  1 lot"))
    else:
        failed = [n for n,ok in [("EMA",ema_ok),("ADX",adx_ok),("RSI",rsi_ok)] if not ok]
        print(yellow(f"  RECOMMENDATION  ->  PROCEED WITH CAUTION"))
        print(yellow(f"  Failed: {', '.join(failed)}  — consider waiting for cleaner setup"))
    print(bold("=" * 60))
    print()
    print(dim("  Decision-support tool only. Options trading involves risk"))
    print(dim("  of total loss of premium paid. Not financial advice."))
    print()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(); print(dim("  Exited.")); print()