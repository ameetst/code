"""
_patch_weekly.py  —  Apply all weekly-rebalance + selective-exit changes
to etf_momentum_ranking.py.  Run once:  python _patch_weekly.py
"""
import ast, re
from pathlib import Path

TARGET = Path("etf_momentum_ranking.py")
src = TARGET.read_text(encoding="utf-8")
NL = "\r\n" if "\r\n" in src else "\n"

def nl(text):
    """Normalize newlines to match file."""
    return text.replace("\n", NL)

assert_count = 0

def do_replace(label, old, new, count=1):
    global src, assert_count
    assert_count += 1
    old_n = nl(old)
    new_n = nl(new)
    assert old_n in src, f"PATCH {assert_count} FAILED ({label}): anchor not found"
    src = src.replace(old_n, new_n, count)
    print(f"  [{assert_count}] {label} — OK")


# ══════════════════════════════════════════════════════════════════
# PATCH 1: CONFIG class — change TSL default, add new exit params
# ══════════════════════════════════════════════════════════════════
do_replace("CONFIG: TSL + exit params",
    "    # Trailing Stop Loss — daily monitoring via --tsl flag\n"
    "    TSL_THRESHOLD = 0.10   # 10% drawdown from peak triggers alert",

    "    # Trailing Stop Loss — daily monitoring via --tsl flag\n"
    "    TSL_THRESHOLD = 0.05   # 5% drawdown from peak triggers alert\n"
    "\n"
    "    # Weekly rebalance: selective exit triggers\n"
    "    EXIT_MAX_DD_FROM_HIGH = 0.25   # exit if >25% from 52-week high\n"
    "    EXIT_MAX_RANK         = 20     # exit if investable rank > 20\n"
    "    HISTORY_PERIODS       = 52     # weeks of log history to retain in Excel"
)

# ══════════════════════════════════════════════════════════════════
# PATCH 2: _apply_json_config — add new keys
# ══════════════════════════════════════════════════════════════════
do_replace("_apply_json_config keys",
    '        "TSL_THRESHOLD",\n'
    '    ]',

    '        "TSL_THRESHOLD",\n'
    '        "EXIT_MAX_DD_FROM_HIGH", "EXIT_MAX_RANK",\n'
    '        "HISTORY_PERIODS",\n'
    '    ]'
)

# ══════════════════════════════════════════════════════════════════
# PATCH 3: get_config_as_dict — add new keys
# ══════════════════════════════════════════════════════════════════
do_replace("get_config_as_dict keys",
    '        "TSL_THRESHOLD": CONFIG.TSL_THRESHOLD,\n'
    '    }',

    '        "TSL_THRESHOLD": CONFIG.TSL_THRESHOLD,\n'
    '        "EXIT_MAX_DD_FROM_HIGH": CONFIG.EXIT_MAX_DD_FROM_HIGH,\n'
    '        "EXIT_MAX_RANK": CONFIG.EXIT_MAX_RANK,\n'
    '        "HISTORY_PERIODS": CONFIG.HISTORY_PERIODS,\n'
    '    }'
)

# ══════════════════════════════════════════════════════════════════
# PATCH 4: Add _week_key() helper + change HISTORY constant
# ══════════════════════════════════════════════════════════════════
do_replace("Add _week_key() + change HISTORY",
    'HOLDINGS_LOG_FILE = "holdings_log.json"\n'
    'HISTORY_MONTHS    = 12   # how many months of history to show in Excel',

    'HOLDINGS_LOG_FILE = "holdings_log.json"\n'
    '\n'
    '\n'
    'def _week_key(dt=None):\n'
    '    """Return ISO week key like \'2026-W30\' for the given date (default=today)."""\n'
    '    if dt is None:\n'
    '        dt = datetime.today()\n'
    '    iso = dt.isocalendar()\n'
    '    return f"{iso.year}-W{iso.week:02d}"'
)

# ══════════════════════════════════════════════════════════════════
# PATCH 5: update_log — monthly key → weekly key
# ══════════════════════════════════════════════════════════════════
do_replace("update_log: month_key -> week_key",
    "    log      = load_holdings_log(script_dir)\n"
    '    month_key = datetime.today().strftime("%Y-%m")\n'
    '    run_date  = datetime.today().strftime("%Y-%m-%d %H:%M")',

    "    log      = load_holdings_log(script_dir)\n"
    '    month_key = _week_key()   # weekly key, e.g. "2026-W30"\n'
    '    run_date  = datetime.today().strftime("%Y-%m-%d %H:%M")'
)

do_replace("update_log: comment monthly->weekly",
    "    # Save current month (overwrites if same month run again — latest wins)",
    "    # Save current week (overwrites if same week run again — latest wins)"
)

# ══════════════════════════════════════════════════════════════════
# PATCH 6: check_tsl — monthly key → weekly key
# ══════════════════════════════════════════════════════════════════
# First occurrence in check_tsl:
do_replace("check_tsl: month_key -> week_key",
    '    log = load_holdings_log(script_dir)\n'
    '    month_key = datetime.today().strftime("%Y-%m")\n'
    '\n'
    '    if month_key not in log:\n'
    '        msg = "[tsl] No allocation found for current month."\n'
    '        print(msg)\n'
    '        print("      Run the monthly rebalance first: python etf_momentum_ranking.py")',

    '    log = load_holdings_log(script_dir)\n'
    '    month_key = _week_key()   # weekly key\n'
    '\n'
    '    if month_key not in log:\n'
    '        msg = "[tsl] No allocation found for current week."\n'
    '        print(msg)\n'
    '        print("      Run the weekly rebalance first: python etf_momentum_ranking.py")'
)

# TSL: "Move proceeds to cash until next monthly rebalance"
do_replace("check_tsl: monthly -> weekly in message",
    '        print(f"  -> Move proceeds to cash until next monthly rebalance.\\n")',
    '        print(f"  -> Move proceeds to cash until next weekly rebalance.\\n")'
)

# ══════════════════════════════════════════════════════════════════
# PATCH 7: Add should_exit() function — before build_allocation
# ══════════════════════════════════════════════════════════════════
do_replace("Add should_exit() before build_allocation",
    'def build_allocation(df: pd.DataFrame, regime: dict) -> pd.DataFrame:',

    'def should_exit(ticker: str, ranking_df: pd.DataFrame, peak: float,\n'
    '                current_price: float) -> tuple[bool, str]:\n'
    '    """\n'
    '    Check whether a currently-held position should be exited.\n'
    '    Returns (should_exit: bool, reason: str).\n'
    '\n'
    '    Exit triggers (ANY fires → exit):\n'
    '      1. >25% away from 52-week high  (EXIT_MAX_DD_FROM_HIGH)\n'
    '      2. Investable rank > 20          (EXIT_MAX_RANK)\n'
    '      3. Drawdown from peak > 5%       (TSL_THRESHOLD)\n'
    '    """\n'
    '    reasons = []\n'
    '    row = ranking_df[ranking_df["TICKER"] == ticker]\n'
    '    if row.empty:\n'
    '        return True, "Ticker no longer in ranking universe"\n'
    '\n'
    '    row = row.iloc[0]\n'
    '\n'
    '    # 1. 52-week high drawdown\n'
    '    pct_from_high = row.get("PCT_FROM_HIGH", 0)\n'
    '    if pd.notna(pct_from_high) and abs(pct_from_high) > CONFIG.EXIT_MAX_DD_FROM_HIGH * 100:\n'
    '        reasons.append(f"52wk high DD {pct_from_high:.1f}% > {CONFIG.EXIT_MAX_DD_FROM_HIGH*100:.0f}%")\n'
    '\n'
    '    # 2. Rank degradation\n'
    '    inv_rank = row.get("RANK_INVESTABLE", float("inf"))\n'
    '    if pd.notna(inv_rank) and inv_rank > CONFIG.EXIT_MAX_RANK:\n'
    '        reasons.append(f"Rank {int(inv_rank)} > {CONFIG.EXIT_MAX_RANK}")\n'
    '\n'
    '    # 3. TSL breach (drawdown from stored peak)\n'
    '    if peak and peak > 0 and current_price and current_price > 0:\n'
    '        dd_from_peak = (peak - current_price) / peak\n'
    '        if dd_from_peak >= CONFIG.TSL_THRESHOLD:\n'
    '            reasons.append(f"TSL {dd_from_peak*100:.1f}% >= {CONFIG.TSL_THRESHOLD*100:.0f}%")\n'
    '\n'
    '    if reasons:\n'
    '        return True, " | ".join(reasons)\n'
    '    return False, ""\n'
    '\n'
    '\n'
    'def build_allocation(df: pd.DataFrame, regime: dict,\n'
    '                     prev_allocation: list | None = None,\n'
    '                     prices: pd.DataFrame | None = None) -> pd.DataFrame:'
)

# ══════════════════════════════════════════════════════════════════
# PATCH 8: Rewrite build_allocation body — WRH with selective exits
# ══════════════════════════════════════════════════════════════════
# Replace the docstring + full body through the final `return pd.DataFrame(slots)`
OLD_ALLOC_BODY = (
    '    """\n'
    '    Select top ETFs from the INVESTABLE (abs-pass) subset only.\n'
    '    Number of active slots determined by tiered regime state:\n'
    '      BULL    -> TOP_N slots\n'
    '      PARTIAL -> TOP_N_PARTIAL slots (remainder = cash buffer)\n'
    '      BEAR    -> 0 slots (full cash)\n'
    '    """\n'
    '    active = regime["active_slots"]\n'
    '    total  = CONFIG.TOP_N\n'
    '    w      = 1.0 / total\n'
    '\n'
    '    # Full cash — regime is BEAR\n'
    '    if active == 0:\n'
    '        return pd.DataFrame([{\n'
    '            "SLOT"        : i + 1,\n'
    '            "TICKER"      : "CASH",\n'
    '            "ETF_NAME"    : "Cash / Money Market",\n'
    '            "SECTOR"      : "CASH",\n'
    '            "WEIGHT"      : w,\n'
    '            "INV_RANK"    : "-",\n'
    '            "REASON"      : f"Regime = {regime[\'label\']} -> full cash"\n'
    '        } for i in range(total)])\n'
    '\n'
    '    # Investable ETFs sorted by investable rank (composite score)\n'
    '    investable = df[df["SCREEN_PASS"] & (df["RANK_INVESTABLE"] > 0)].copy()\n'
    '    investable = investable.sort_values("RANK_INVESTABLE").reset_index(drop=True)\n'
    '\n'
    '    is_partial = (active == CONFIG.TOP_N_PARTIAL)\n'
    '\n'
    '    # All regimes use the same WTD_SHARPE-based RANK_INVESTABLE ordering.\n'
    '    # In PARTIAL, the top-N_PARTIAL by composite rank are kept; the rest\n'
    '    # go to cash. This aligns with the backtested Run 1 configuration.\n'
    '    sort_label = "Investable composite rank (WTD_SHARPE)"\n'
    '\n'
    '    slots = []\n'
    '    sector_count: dict[str, int] = {}   # track how many slots each sector has filled\n'
    '\n'
    '    # Walk the (possibly re-sorted) investable list; apply sector cap\n'
    '    candidate_idx: int = 0\n'
    '    slot_num: int = 1\n'
    '    while slot_num <= active:\n'
    '        # Find next candidate that doesn\'t breach sector cap\n'
    '        filled = False\n'
    '        while candidate_idx < len(investable):\n'
    '            row     = investable.iloc[candidate_idx]\n'
    '            sector  = row.get("SECTOR", "OTHER")\n'
    '            current = sector_count.get(sector, 0)\n'
    '            candidate_idx += 1  # pyre-ignore[58]\n'
    '\n'
    '            if current < CONFIG.SECTOR_CAP:\n'
    '                sector_count[sector] = current + 1\n'
    '                slots.append({\n'
    '                    "SLOT"    : slot_num,\n'
    '                    "TICKER"  : row["TICKER"],\n'
    '                    "ETF_NAME": row["ETF_NAME"],\n'
    '                    "SECTOR"  : sector,\n'
    '                    "WEIGHT"  : w,\n'
    '                    "INV_RANK": int(row["RANK_INVESTABLE"]),\n'
    '                    "REASON"  : (f"Sort: {sort_label}  |  "\n'
    '                                 f"Sector={sector} ({current+1}/{CONFIG.SECTOR_CAP})  |  "\n'
    '                                 f"3M Sharpe={row[\'SHARPE_3M\']:.3f}"),\n'
    '                })\n'
    '                slot_num += 1\n'
    '                filled = True\n'
    '                break\n'
    '            # else: sector cap hit — skip this ETF, try next\n'
    '\n'
    '        if not filled:\n'
    '            # Exhausted all candidates — fill remaining with cash\n'
    '            slots.append({\n'
    '                "SLOT"    : slot_num,\n'
    '                "TICKER"  : "CASH",\n'
    '                "ETF_NAME": "Cash (sector cap / investable universe exhausted)",\n'
    '                "SECTOR"  : "CASH",\n'
    '                "WEIGHT"  : w,\n'
    '                "INV_RANK": "-",\n'
    '                "REASON"  : (f"Sector cap={CONFIG.SECTOR_CAP} per sector  |  "\n'
    '                             f"No remaining qualifying ETF after cap"),\n'
    '            })\n'
    '            slot_num += 1\n'
    '\n'
    '    # Remaining slots: cash buffer for PARTIAL regime\n'
    '    for slot_num in range(active + 1, total + 1):\n'
    '        slots.append({\n'
    '            "SLOT"    : slot_num,\n'
    '            "TICKER"  : "CASH",\n'
    '            "ETF_NAME": "Cash / Money Market",\n'
    '            "SECTOR"  : "CASH",\n'
    '            "WEIGHT"  : w,\n'
    '            "INV_RANK": "-",\n'
    '            "REASON"  : (f"Regime buffer: {regime[\'label\']} -> "\n'
    '                         f"weakest 3M Clenow ETFs dropped  |  "\n'
    '                         f"only {active} of {total} slots active"\n'
    '                         if is_partial else "Universe exhausted")\n'
    '        })\n'
    '\n'
    '    return pd.DataFrame(slots)'
)

NEW_ALLOC_BODY = (
    '    """\n'
    '    Weekly Hold-and-Replace (WRH) allocation.\n'
    '\n'
    '    If prev_allocation is provided (list of slot dicts from last week):\n'
    '      1. Check each held position against exit triggers (should_exit)\n'
    '      2. HOLD positions that pass all checks\n'
    '      3. Fill vacated + new slots from investable ranking (skip already held)\n'
    '\n'
    '    If prev_allocation is None (first run): behaves like fresh top-N pick.\n'
    '\n'
    '    Number of active slots determined by tiered regime state:\n'
    '      BULL    -> TOP_N slots\n'
    '      PARTIAL -> TOP_N_PARTIAL slots (remainder = cash buffer)\n'
    '      BEAR    -> 0 slots (full cash)\n'
    '    """\n'
    '    active = regime["active_slots"]\n'
    '    total  = CONFIG.TOP_N\n'
    '    w      = 1.0 / total\n'
    '\n'
    '    # Full cash — regime is BEAR\n'
    '    if active == 0:\n'
    '        return pd.DataFrame([{\n'
    '            "SLOT"        : i + 1,\n'
    '            "TICKER"      : "CASH",\n'
    '            "ETF_NAME"    : "Cash / Money Market",\n'
    '            "SECTOR"      : "CASH",\n'
    '            "WEIGHT"      : w,\n'
    '            "INV_RANK"    : "-",\n'
    '            "REASON"      : f"Regime = {regime[\'label\']} -> full cash"\n'
    '        } for i in range(total)])\n'
    '\n'
    '    # Investable ETFs sorted by investable rank (composite score)\n'
    '    investable = df[df["SCREEN_PASS"] & (df["RANK_INVESTABLE"] > 0)].copy()\n'
    '    investable = investable.sort_values("RANK_INVESTABLE").reset_index(drop=True)\n'
    '    is_partial = (active == CONFIG.TOP_N_PARTIAL)\n'
    '\n'
    '    slots = []\n'
    '    held_tickers = set()      # tickers retained from previous week\n'
    '    sector_count: dict[str, int] = {}\n'
    '\n'
    '    # ── Phase 1: evaluate holds from previous allocation ─────────────\n'
    '    if prev_allocation:\n'
    '        for prev_slot in prev_allocation:\n'
    '            t = prev_slot.get("ticker", "")\n'
    '            if t == "CASH" or not t:\n'
    '                continue\n'
    '            peak          = prev_slot.get("peak", 0) or 0\n'
    '            current_price = 0.0\n'
    '            if prices is not None and t in prices.columns:\n'
    '                s = prices[t].dropna()\n'
    '                if len(s) > 0:\n'
    '                    current_price = float(s.iloc[-1])\n'
    '\n'
    '            exit_flag, exit_reason = should_exit(t, df, peak, current_price)\n'
    '\n'
    '            if not exit_flag and len(held_tickers) < active:\n'
    '                sector = prev_slot.get("sector", "OTHER")\n'
    '                sc     = sector_count.get(sector, 0)\n'
    '                # Look up current rank\n'
    '                rk_row = df[df["TICKER"] == t]\n'
    '                inv_rk = int(rk_row.iloc[0]["RANK_INVESTABLE"]) if not rk_row.empty and pd.notna(rk_row.iloc[0].get("RANK_INVESTABLE")) else "-"\n'
    '                sector_count[sector] = sc + 1\n'
    '                held_tickers.add(t)\n'
    '                slots.append({\n'
    '                    "SLOT"    : len(slots) + 1,\n'
    '                    "TICKER"  : t,\n'
    '                    "ETF_NAME": prev_slot.get("etf_name", t),\n'
    '                    "SECTOR"  : sector,\n'
    '                    "WEIGHT"  : w,\n'
    '                    "INV_RANK": inv_rk,\n'
    '                    "REASON"  : "HOLD — no exit trigger",\n'
    '                })\n'
    '            else:\n'
    '                if exit_flag:\n'
    '                    print(f"    EXIT {t}: {exit_reason}")\n'
    '\n'
    '    # ── Phase 2: fill remaining active slots from ranking ────────────\n'
    '    open_slots   = active - len(slots)\n'
    '    candidate_idx = 0\n'
    '    filled_new    = 0\n'
    '    while filled_new < open_slots and candidate_idx < len(investable):\n'
    '        row    = investable.iloc[candidate_idx]\n'
    '        ticker = row["TICKER"]\n'
    '        sector = row.get("SECTOR", "OTHER")\n'
    '        candidate_idx += 1\n'
    '\n'
    '        if ticker in held_tickers:\n'
    '            continue   # already held from Phase 1\n'
    '        sc = sector_count.get(sector, 0)\n'
    '        if sc >= CONFIG.SECTOR_CAP:\n'
    '            continue   # sector cap hit\n'
    '\n'
    '        sector_count[sector] = sc + 1\n'
    '        held_tickers.add(ticker)\n'
    '        slots.append({\n'
    '            "SLOT"    : len(slots) + 1,\n'
    '            "TICKER"  : ticker,\n'
    '            "ETF_NAME": row["ETF_NAME"],\n'
    '            "SECTOR"  : sector,\n'
    '            "WEIGHT"  : w,\n'
    '            "INV_RANK": int(row["RANK_INVESTABLE"]),\n'
    '            "REASON"  : (f"NEW BUY — Rank {int(row[\'RANK_INVESTABLE\'])}  |  "\n'
    '                         f"Sector={sector} ({sc+1}/{CONFIG.SECTOR_CAP})"),\n'
    '        })\n'
    '        filled_new += 1\n'
    '\n'
    '    # Fill any remaining active slots with CASH (universe exhausted)\n'
    '    while len(slots) < active:\n'
    '        slots.append({\n'
    '            "SLOT"    : len(slots) + 1,\n'
    '            "TICKER"  : "CASH",\n'
    '            "ETF_NAME": "Cash (sector cap / investable universe exhausted)",\n'
    '            "SECTOR"  : "CASH",\n'
    '            "WEIGHT"  : w,\n'
    '            "INV_RANK": "-",\n'
    '            "REASON"  : "No remaining qualifying ETF after cap",\n'
    '        })\n'
    '\n'
    '    # Remaining slots: cash buffer for PARTIAL regime\n'
    '    for _ in range(len(slots), total):\n'
    '        slots.append({\n'
    '            "SLOT"    : len(slots) + 1,\n'
    '            "TICKER"  : "CASH",\n'
    '            "ETF_NAME": "Cash / Money Market",\n'
    '            "SECTOR"  : "CASH",\n'
    '            "WEIGHT"  : w,\n'
    '            "INV_RANK": "-",\n'
    '            "REASON"  : (f"Regime buffer: {regime[\'label\']} -> "\n'
    '                         f"only {active} of {total} slots active"\n'
    '                         if is_partial else "Universe exhausted")\n'
    '        })\n'
    '\n'
    '    return pd.DataFrame(slots)'
)

do_replace("build_allocation body -> WRH", OLD_ALLOC_BODY, NEW_ALLOC_BODY)


# ══════════════════════════════════════════════════════════════════
# PATCH 9: run_pipeline — pass prev allocation to build_allocation
# ══════════════════════════════════════════════════════════════════
do_replace("run_pipeline: pass prev_alloc",
    '    print("[alloc]  Building allocation ...")\n'
    '    allocation = build_allocation(ranking, regime)',

    '    # Load previous week\'s allocation for hold-and-replace\n'
    '    prev_log   = load_holdings_log(SCRIPT_DIR)\n'
    '    prev_wk    = _week_key()\n'
    '    sorted_wks = sorted(prev_log.keys())\n'
    '    prev_wks   = [k for k in sorted_wks if k < prev_wk]\n'
    '    prev_alloc = prev_log[prev_wks[-1]].get("allocation", []) if prev_wks else None\n'
    '\n'
    '    print("[alloc]  Building allocation (WRH — hold & replace) ...")\n'
    '    allocation = build_allocation(ranking, regime,\n'
    '                                  prev_allocation=prev_alloc,\n'
    '                                  prices=prices)'
)

# Also update the diff message from "Previous month" to "Previous period"
do_replace("run_pipeline: month -> period",
    '        prev_month = prev_entry.get("run_date","?")[:7]\n'
    '        print(f"         Previous month: {prev_month}  |  Changes: {len(changes)}")',

    '        prev_period = prev_entry.get("run_date","?")[:10]\n'
    '        print(f"         Previous: {prev_period}  |  Changes: {len(changes)}")'
)

# ══════════════════════════════════════════════════════════════════
# PATCH 10: process_historical — change month_key reference if present
# ══════════════════════════════════════════════════════════════════
# Check if there's a curr_month reference
if '    curr_month = datetime.today().strftime("%Y-%m")' in nl(src):
    do_replace("process_historical: month -> week",
        '    curr_month = datetime.today().strftime("%Y-%m")',
        '    curr_month = _week_key()'
    )

# ══════════════════════════════════════════════════════════════════
# PATCH 11: Update module docstring
# ══════════════════════════════════════════════════════════════════
do_replace("Module docstring: monthly -> weekly",
    "  Layer 1 - Trend  : MONIFTY500 above its 100-day SMA",
    "  Layer 1 - Trend  : MONIFTY500 above its 100-day EMA\n"
    "\n"
    "  Rebalance: Weekly (Monday / first trading day).\n"
    "  Exit logic: Hold positions unless 52wk-high DD > 25%, rank > 20, or TSL > 5%."
)

# ══════════════════════════════════════════════════════════════════
# WRITE + VERIFY
# ══════════════════════════════════════════════════════════════════
TARGET.write_text(src, encoding="utf-8")
print(f"\nAll {assert_count} patches applied to {TARGET.name}")

ast.parse(src)
print("Syntax OK ✓")
