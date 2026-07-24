"""
_patch_dashboard_weekly.py  —  Apply weekly-rebalance changes to etf_dashboard.py
Run once:  python _patch_dashboard_weekly.py
"""
import ast
from pathlib import Path

TARGET = Path("etf_dashboard.py")
src = TARGET.read_text(encoding="utf-8")
NL = "\r\n" if "\r\n" in src else "\n"

def nl(text):
    return text.replace("\n", NL)

n = 0

def do_replace(label, old, new, count=1):
    global src, n
    n += 1
    old_n = nl(old)
    new_n = nl(new)
    assert old_n in src, f"PATCH {n} FAILED ({label}): anchor not found"
    src = src.replace(old_n, new_n, count)
    print(f"  [{n}] {label}")


# ── 1. TSL Monitor: month_key → week_key ──────────────────────────────────
do_replace("TSL: month_key -> week_key",
    '    month_key = datetime.datetime.today().strftime("%Y-%m")',
    '    _today = datetime.datetime.today()\n'
    '    _iso = _today.isocalendar()\n'
    '    month_key = f"{_iso.year}-W{_iso.week:02d}"   # weekly key'
)

do_replace("TSL: no-holdings warning",
    '        st.warning("No holdings found for current month. Run the monthly rebalance first.")',
    '        st.warning("No holdings found for current week. Run the weekly rebalance first.")'
)

# ── 2. Rebalance Diff: labels ────────────────────────────────────────────
do_replace("Diff: Previous month label",
    '            prev_month = prev_entry.get("run_date", "?")[:7]\n'
    '            st.markdown(f"**Previous month:** {prev_month}  |  **Changes:** {len(changes)}")',

    '            prev_period = prev_entry.get("run_date", "?")[:10]\n'
    '            st.markdown(f"**Previous:** {prev_period}  |  **Changes:** {len(changes)}")'
)

do_replace("Diff: no changes label",
    '            st.info("No changes from previous month.")',
    '            st.info("No changes from previous period.")'
)

do_replace("Diff: first period label",
    '        st.info("First month \u2014 no previous holdings to compare.")',
    '        st.info("First week \u2014 no previous holdings to compare.")'
)

do_replace("Diff: allocation not found",
    '        st.warning("Current month\'s allocation not found. Run the monthly rebalance.")',
    '        st.warning("Current week\'s allocation not found. Run the weekly rebalance.")'
)

# ── 3. Button labels: Monthly → Weekly ───────────────────────────────────
do_replace("Button: Run Monthly Rebalance",
    '"🔁 Run Monthly Rebalance"',
    '"🔁 Run Weekly Rebalance"'
)

do_replace("Button: Monthly rebalance complete",
    '"✅ Monthly rebalance complete! Refresh the page to see updated results."',
    '"✅ Weekly rebalance complete! Refresh the page to see updated results."'
)

do_replace("Info: Run monthly first",
    '"Run the monthly rebalance first to generate the Excel output."',
    '"Run the weekly rebalance first to generate the Excel output."'
)

# ── 4. Add exit-parameter widgets in Risk Management section ─────────────
do_replace("Config: add exit params after TSL",
    '        cfg_tsl = st.number_input("TSL Threshold", min_value=0.01, max_value=0.30,\n'
    '                                   value=float(current_cfg["TSL_THRESHOLD"]), step=0.01,\n'
    '                                   format="%.2f", key="cfg_tsl",\n'
    '                                   help="Trailing stop loss drawdown trigger (0.10 = 10%)")\n'
    '\n'
    '    st.divider()\n'
    '\n'
    '    # \u2500\u2500 Save / Run Buttons',

    '        cfg_tsl = st.number_input("TSL Threshold", min_value=0.01, max_value=0.30,\n'
    '                                   value=float(current_cfg["TSL_THRESHOLD"]), step=0.01,\n'
    '                                   format="%.2f", key="cfg_tsl",\n'
    '                                   help="Trailing stop loss drawdown trigger (0.05 = 5%)")\n'
    '\n'
    '    st.divider()\n'
    '\n'
    '    # \u2500\u2500 Weekly Exit Triggers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n'
    '    st.markdown("### \U0001f6aa Weekly Exit Triggers")\n'
    '    cfg_col11, cfg_col12 = st.columns(2)\n'
    '    with cfg_col11:\n'
    '        cfg_exit_dd = st.number_input("Exit: Max DD from 52W High", min_value=0.05, max_value=0.50,\n'
    '                                       value=float(current_cfg.get("EXIT_MAX_DD_FROM_HIGH", 0.25)), step=0.05,\n'
    '                                       format="%.2f", key="cfg_exit_dd",\n'
    '                                       help="Exit position if >X% away from 52-week high (0.25 = 25%)")\n'
    '    with cfg_col12:\n'
    '        cfg_exit_rank = st.number_input("Exit: Max Investable Rank", min_value=5, max_value=100,\n'
    '                                         value=int(current_cfg.get("EXIT_MAX_RANK", 20)), step=1,\n'
    '                                         key="cfg_exit_rank",\n'
    '                                         help="Exit position if investable rank exceeds this value")\n'
    '\n'
    '    st.divider()\n'
    '\n'
    '    # \u2500\u2500 Save / Run Buttons'
)


# ── 5. Add new keys to BOTH save config dicts ────────────────────────────
# There are two identical `new_cfg = {...}` blocks. We replace both.
OLD_CFG_TAIL = (
    '                "DAILY_RF_ANNUAL": cfg_rf_annual,\n'
    '                "TSL_THRESHOLD": cfg_tsl,\n'
    '            }'
)
NEW_CFG_TAIL = (
    '                "DAILY_RF_ANNUAL": cfg_rf_annual,\n'
    '                "TSL_THRESHOLD": cfg_tsl,\n'
    '                "EXIT_MAX_DD_FROM_HIGH": cfg_exit_dd,\n'
    '                "EXIT_MAX_RANK": cfg_exit_rank,\n'
    '            }'
)
# Replace ALL occurrences (there are 2 — one in Save, one in Run)
do_replace("Config dict: add exit keys (all occurrences)",
    OLD_CFG_TAIL, NEW_CFG_TAIL, count=2)


# ── Write + verify ───────────────────────────────────────────────────────
TARGET.write_text(src, encoding="utf-8")
print(f"\nAll {n} patches applied to {TARGET.name}")

ast.parse(src)
print("Syntax OK")
