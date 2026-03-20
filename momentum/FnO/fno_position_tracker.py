"""
FNO Position Tracker
=====================
Tracks open options positions, computes live P&L, checks exit rules,
and logs each update to a CSV trade journal.

Usage
-----
  python fno_position_tracker.py             # shows all positions + prompts for update
  python fno_position_tracker.py --status    # quick status table, no update
  python fno_position_tracker.py --close ASHOKLEY  # mark a position closed

How to update prices
---------------------
Run the script, enter current LTP (option price) and current spot price
when prompted. The script computes everything else.

Files created/maintained
-------------------------
  positions.json     — live position state (auto-created on first run)
  trade_journal.csv  — append-only log of every update you make

Dependencies: none (stdlib only)
"""

import json
import csv
import os
import sys
import argparse
from datetime import datetime, date

# ── File paths ────────────────────────────────────────────────────────────────
POSITIONS_FILE = 'positions.json'
JOURNAL_FILE   = 'trade_journal.csv'

# ── Expiry date ───────────────────────────────────────────────────────────────
EXPIRY_DATE = date(2026, 3, 30)

# ── Default positions (loaded on first run if positions.json doesn't exist) ──
DEFAULT_POSITIONS = [
    {
        'id'              : 'ASHOKLEY_CE178_MAR30',
        'ticker'          : 'ASHOKLEY',
        'contract'        : 'ASHOKLEY CE 178 Mar30',
        'type'            : 'CE',
        'strike'          : 178.0,
        'expiry'          : '30-Mar-2026',
        'lots'            : 2,
        'lot_size'        : 3500,
        'entry_ltp'       : 5.05,
        'entry_spot'      : 177.50,
        'entry_date'      : '18-Mar-2026',
        'entry_dte'       : 12,
        'ema20_at_entry'  : 183.4,
        'ema50_at_entry'  : 172.0,
        'stop_ltp'        : 2.50,    # exit if LTP falls here  (~50% loss on premium)
        'target_spot'     : 183.0,   # EMA20 level = first target
        'stretch_target'  : 188.0,   # EMA20 + (EMA20-EMA50) = stretch target
        'conviction'      : 70.6,
        'r2_252'          : 0.4064,
        'r2_90'           : 0.1125,
        'adx'             : 7.8,
        'status'          : 'OPEN',  # OPEN | CLOSED | EXPIRED
        'current_ltp'     : None,
        'current_spot'    : None,
        'last_updated'    : None,
        'exit_ltp'        : None,
        'exit_date'       : None,
        'exit_reason'     : None,
        'notes'           : '',
    },
    {
        'id'              : 'BHARATFORG_CE1800_MAR30',
        'ticker'          : 'BHARATFORG',
        'contract'        : 'BHARATFORG CE 1800 Mar30',
        'type'            : 'CE',
        'strike'          : 1800.0,
        'expiry'          : '30-Mar-2026',
        'lots'            : 2,
        'lot_size'        : 500,
        'entry_ltp'       : 40.35,
        'entry_spot'      : 1800.0,
        'entry_date'      : '18-Mar-2026',
        'entry_dte'       : 12,
        'ema20_at_entry'  : 1705.0,
        'ema50_at_entry'  : 1660.0,
        'stop_ltp'        : 20.0,    # 50% of premium
        'target_spot'     : 1842.0,
        'stretch_target'  : 1880.0,
        'conviction'      : 64.4,
        'r2_252'          : 0.2189,
        'r2_90'           : 0.1088,
        'adx'             : 11.7,
        'status'          : 'OPEN',
        'current_ltp'     : None,
        'current_spot'    : None,
        'last_updated'    : None,
        'exit_ltp'        : None,
        'exit_date'       : None,
        'exit_reason'     : None,
        'notes'           : '',
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# FILE I/O
# ══════════════════════════════════════════════════════════════════════════════

def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        save_positions(DEFAULT_POSITIONS)
        print(f"[INFO] Created {POSITIONS_FILE} with default positions.\n")
        return DEFAULT_POSITIONS
    with open(POSITIONS_FILE, 'r') as f:
        return json.load(f)

def save_positions(positions):
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(positions, f, indent=2)

def append_journal(row: dict):
    file_exists = os.path.exists(JOURNAL_FILE)
    with open(JOURNAL_FILE, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ══════════════════════════════════════════════════════════════════════════════
# CALCULATIONS
# ══════════════════════════════════════════════════════════════════════════════

def total_qty(p):
    return p['lots'] * p['lot_size']

def premium_paid(p):
    return round(p['entry_ltp'] * total_qty(p), 2)

def current_value(p):
    if p['current_ltp'] is None:
        return None
    return round(p['current_ltp'] * total_qty(p), 2)

def pnl(p):
    cv = current_value(p)
    if cv is None:
        return None
    return round(cv - premium_paid(p), 2)

def pnl_pct(p):
    pp = premium_paid(p)
    pl = pnl(p)
    if pl is None or pp == 0:
        return None
    return round(pl / pp * 100, 1)

def dte_remaining():
    today = date.today()
    delta = (EXPIRY_DATE - today).days
    return max(0, delta)

def days_in_trade(p):
    try:
        entry = datetime.strptime(p['entry_date'], '%d-%b-%Y').date()
        return (date.today() - entry).days
    except:
        return None

def spot_move_pct(p):
    if p['current_spot'] is None:
        return None
    return round((p['current_spot'] - p['entry_spot']) / p['entry_spot'] * 100, 2)


# ══════════════════════════════════════════════════════════════════════════════
# EXIT RULE CHECKS
# ══════════════════════════════════════════════════════════════════════════════

def check_exit_rules(p):
    """
    Returns list of triggered rules as (rule_name, message, urgency) tuples.
    urgency: 'CRITICAL' | 'WARNING' | 'INFO'
    """
    triggers = []
    ltp = p['current_ltp']
    spot = p['current_spot']
    dte = dte_remaining()
    pl_pct = pnl_pct(p)
    days = days_in_trade(p)

    if ltp is None:
        return triggers

    # ── Hard stop: LTP at or below stop level ────────────────────────────────
    if ltp <= p['stop_ltp']:
        triggers.append((
            'STOP HIT',
            f"LTP ₹{ltp} ≤ stop ₹{p['stop_ltp']} — EXIT IMMEDIATELY",
            'CRITICAL'
        ))

    # ── Time-based stop: 40% loss after 5+ days ──────────────────────────────
    if days and days >= 5 and pl_pct is not None and pl_pct <= -40:
        triggers.append((
            'TIME-STOP',
            f"{days} days in trade, P&L = {pl_pct}% — exit, theta is winning",
            'CRITICAL'
        ))

    # ── DTE warning: < 5 days remaining ──────────────────────────────────────
    if dte <= 5:
        triggers.append((
            'LOW DTE',
            f"Only {dte} DTE left — review position, theta decay is severe",
            'CRITICAL'
        ))
    elif dte <= 8:
        triggers.append((
            'DTE WARNING',
            f"{dte} DTE — monitor closely, tighten stop",
            'WARNING'
        ))

    # ── Target hit: spot reached target ──────────────────────────────────────
    if spot and spot >= p['target_spot']:
        triggers.append((
            'TARGET HIT',
            f"Spot ₹{spot} ≥ target ₹{p['target_spot']} — consider booking profits or trailing stop",
            'WARNING'
        ))

    # ── Stretch target hit ────────────────────────────────────────────────────
    if spot and spot >= p['stretch_target']:
        triggers.append((
            'STRETCH TARGET',
            f"Spot ₹{spot} ≥ stretch target ₹{p['stretch_target']} — book full position",
            'WARNING'
        ))

    # ── Spot moving against trade ─────────────────────────────────────────────
    sp_move = spot_move_pct(p)
    if sp_move is not None and sp_move <= -3:
        triggers.append((
            'SPOT ADVERSE',
            f"Spot moved {sp_move}% against trade since entry — reassess",
            'WARNING'
        ))

    # ── Healthy profit: lock in partial ──────────────────────────────────────
    if pl_pct is not None and pl_pct >= 50 and dte > 5:
        triggers.append((
            'PROFIT ALERT',
            f"Up {pl_pct}% — consider booking 50% position or moving stop to breakeven",
            'INFO'
        ))

    return triggers


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

RESET  = '\033[0m'
BOLD   = '\033[1m'
RED    = '\033[91m'
GREEN  = '\033[92m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
WHITE  = '\033[97m'
DIM    = '\033[2m'

def color_pnl(val):
    if val is None: return DIM + '—' + RESET
    if val > 0:     return GREEN  + f'+₹{val:,.0f}' + RESET
    if val < 0:     return RED    + f'-₹{abs(val):,.0f}' + RESET
    return '₹0'

def color_pct(val):
    if val is None: return DIM + '—' + RESET
    if val > 0:     return GREEN  + f'+{val}%' + RESET
    if val < 0:     return RED    + f'{val}%' + RESET
    return '0%'

def urgency_color(u):
    if u == 'CRITICAL': return RED + BOLD
    if u == 'WARNING':  return YELLOW
    return CYAN

def print_header():
    dte = dte_remaining()
    now = datetime.now().strftime('%d-%b-%Y %H:%M')
    print()
    print(BOLD + '═' * 72 + RESET)
    print(BOLD + '  FNO POSITION TRACKER' + RESET +
          DIM + f'                    {now}' + RESET)
    print(BOLD + f'  Expiry: 30 Mar 2026  |  DTE: ' +
          (RED if dte <= 5 else YELLOW if dte <= 8 else WHITE) +
          f'{dte} days remaining' + RESET)
    print(BOLD + '═' * 72 + RESET)

def print_position(p, show_triggers=True):
    qty   = total_qty(p)
    pp    = premium_paid(p)
    cv    = current_value(p)
    pl    = pnl(p)
    pl_p  = pnl_pct(p)
    days  = days_in_trade(p)
    sp_mv = spot_move_pct(p)
    status_color = GREEN if p['status']=='OPEN' else DIM

    print()
    print(BOLD + f"  {p['contract']}" + RESET +
          f"  [{status_color}{p['status']}{RESET}]")
    print(f"  {'Entry':12s}: LTP ₹{p['entry_ltp']}  |  Spot ₹{p['entry_spot']}  |  "
          f"Date {p['entry_date']}  |  {p['lots']} lots × {p['lot_size']} = {qty:,} qty")
    print(f"  {'Premium paid':12s}: ₹{pp:,.0f}  |  Max loss = ₹{pp:,.0f}")
    print(f"  {'Stop / Target':12s}: Stop LTP ₹{p['stop_ltp']}  |  "
          f"Target spot ₹{p['target_spot']}  |  Stretch ₹{p['stretch_target']}")

    if p['status'] == 'OPEN':
        if p['current_ltp'] is not None:
            print(f"  {'Current':12s}: LTP ₹{p['current_ltp']}  |  "
                  f"Spot ₹{p['current_spot']}  |  Days in trade: {days}  |  "
                  f"Spot move: {color_pct(sp_mv)}")
            print(f"  {'P&L':12s}: {color_pnl(pl)}  ({color_pct(pl_p)})  |  "
                  f"Current value ₹{cv:,.0f}")
        else:
            print(f"  {'Current':12s}: " + DIM + 'Not yet updated — run script to enter prices' + RESET)

    elif p['status'] == 'CLOSED':
        exit_pl = round((p['exit_ltp'] - p['entry_ltp']) * qty, 2) if p['exit_ltp'] else None
        exit_pct = round((p['exit_ltp'] - p['entry_ltp']) / p['entry_ltp'] * 100, 1) if p['exit_ltp'] else None
        print(f"  {'Exit':12s}: LTP ₹{p['exit_ltp']}  |  Date {p['exit_date']}  |  Reason: {p['exit_reason']}")
        print(f"  {'Final P&L':12s}: {color_pnl(exit_pl)}  ({color_pct(exit_pct)})")

    elif p['status'] == 'EXPIRED':
        print(f"  {'Expired':12s}: ₹{pp:,.0f} lost (premium expired worthless)")

    if p.get('notes'):
        print(f"  {'Notes':12s}: {DIM}{p['notes']}{RESET}")

    if show_triggers and p['status'] == 'OPEN' and p['current_ltp'] is not None:
        triggers = check_exit_rules(p)
        if triggers:
            print()
            for rule, msg, urgency in triggers:
                uc = urgency_color(urgency)
                print(f"  {uc}⚠  [{rule}] {msg}{RESET}")

    print('  ' + DIM + '─' * 68 + RESET)

def print_portfolio_summary(positions):
    open_pos = [p for p in positions if p['status'] == 'OPEN']
    closed_pos = [p for p in positions if p['status'] == 'CLOSED']

    total_invested = sum(premium_paid(p) for p in open_pos)
    total_current  = sum(current_value(p) or premium_paid(p) for p in open_pos)
    total_pl       = sum(pnl(p) or 0 for p in open_pos if p['current_ltp'])

    closed_pl = 0
    for p in closed_pos:
        if p['exit_ltp']:
            qty = total_qty(p)
            closed_pl += round((p['exit_ltp'] - p['entry_ltp']) * qty, 2)

    print()
    print(BOLD + '  PORTFOLIO SUMMARY' + RESET)
    print(f"  Open positions   : {len(open_pos)}")
    print(f"  Closed positions : {len(closed_pos)}")
    print(f"  Total invested   : ₹{total_invested:,.0f}")
    if any(p['current_ltp'] for p in open_pos):
        print(f"  Open P&L         : {color_pnl(total_pl)}")
    if closed_pos:
        print(f"  Closed P&L       : {color_pnl(closed_pl)}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# UPDATE FLOW
# ══════════════════════════════════════════════════════════════════════════════

def update_position(positions):
    """Prompt user to update prices for one or all open positions."""
    open_pos = [p for p in positions if p['status'] == 'OPEN']
    if not open_pos:
        print("\n  No open positions to update.")
        return

    print(f"\n  Open positions:")
    for i, p in enumerate(open_pos):
        print(f"    {i+1}. {p['contract']}  (entry ₹{p['entry_ltp']})")

    print(f"    {len(open_pos)+1}. Update ALL")
    print(f"    0. Cancel")

    try:
        choice = int(input("\n  Select position to update: ").strip())
    except (ValueError, KeyboardInterrupt):
        print("  Cancelled.")
        return

    if choice == 0:
        return

    if choice == len(open_pos) + 1:
        to_update = open_pos
    elif 1 <= choice <= len(open_pos):
        to_update = [open_pos[choice - 1]]
    else:
        print("  Invalid choice.")
        return

    now_str = datetime.now().strftime('%d-%b-%Y %H:%M')

    for p in to_update:
        print(f"\n  Updating: {p['contract']}")
        try:
            ltp_str = input(f"    Current option LTP ₹ (entry was ₹{p['entry_ltp']}): ").strip()
            if not ltp_str:
                print("    Skipped.")
                continue
            ltp = float(ltp_str)

            spot_str = input(f"    Current spot price ₹ (entry was ₹{p['entry_spot']}): ").strip()
            spot = float(spot_str) if spot_str else p['current_spot'] or p['entry_spot']

            notes = input(f"    Notes (optional, press Enter to skip): ").strip()

        except (ValueError, KeyboardInterrupt):
            print("    Invalid input, skipped.")
            continue

        # Update position
        p['current_ltp']   = ltp
        p['current_spot']  = spot
        p['last_updated']  = now_str
        if notes:
            p['notes'] = notes

        # Auto-check triggers and suggest status change
        triggers = check_exit_rules(p)
        critical = [t for t in triggers if t[2] == 'CRITICAL']

        pl    = pnl(p)
        pl_p  = pnl_pct(p)
        print(f"\n    Updated P&L: {color_pnl(pl)} ({color_pct(pl_p)})")

        for rule, msg, urgency in triggers:
            uc = urgency_color(urgency)
            print(f"    {uc}⚠  [{rule}] {msg}{RESET}")

        if critical:
            print(RED + BOLD + f"\n    CRITICAL TRIGGER ACTIVE — do you want to mark this position as CLOSED?" + RESET)
            close_confirm = input("    Mark as CLOSED? (y/n): ").strip().lower()
            if close_confirm == 'y':
                exit_reason = critical[0][0]
                close_position(p, ltp, exit_reason)

        # Log to journal
        append_journal({
            'timestamp'    : now_str,
            'id'           : p['id'],
            'ticker'       : p['ticker'],
            'contract'     : p['contract'],
            'entry_ltp'    : p['entry_ltp'],
            'entry_spot'   : p['entry_spot'],
            'current_ltp'  : ltp,
            'current_spot' : spot,
            'pnl'          : pl,
            'pnl_pct'      : pl_p,
            'dte_remaining': dte_remaining(),
            'status'       : p['status'],
            'triggers'     : ' | '.join(t[0] for t in triggers) if triggers else '',
            'notes'        : notes,
        })

    save_positions(positions)
    print(f"\n  Positions saved. Journal updated → {JOURNAL_FILE}")


def close_position(p, exit_ltp, reason):
    """Mark a position as closed."""
    p['status']      = 'CLOSED'
    p['exit_ltp']    = exit_ltp
    p['exit_date']   = datetime.now().strftime('%d-%b-%Y')
    p['exit_reason'] = reason
    qty   = total_qty(p)
    pl    = round((exit_ltp - p['entry_ltp']) * qty, 2)
    pl_p  = round((exit_ltp - p['entry_ltp']) / p['entry_ltp'] * 100, 1)
    print(GREEN + f"\n  Position CLOSED — Final P&L: {'+' if pl>=0 else ''}₹{pl:,.0f} ({pl_p}%)" + RESET)


def close_by_ticker(positions, ticker):
    """CLI: mark a specific ticker's position as closed."""
    matches = [p for p in positions if p['ticker'].upper() == ticker.upper() and p['status'] == 'OPEN']
    if not matches:
        print(f"  No open position found for {ticker}.")
        return
    p = matches[0]
    print(f"\n  Closing: {p['contract']}")
    try:
        exit_ltp = float(input(f"    Exit LTP ₹ (entry was ₹{p['entry_ltp']}): ").strip())
        reason   = input(f"    Exit reason (STOP HIT / TARGET / MANUAL / EXPIRED): ").strip() or 'MANUAL'
    except (ValueError, KeyboardInterrupt):
        print("  Cancelled.")
        return
    close_position(p, exit_ltp, reason)
    append_journal({
        'timestamp'    : datetime.now().strftime('%d-%b-%Y %H:%M'),
        'id'           : p['id'],
        'ticker'       : p['ticker'],
        'contract'     : p['contract'],
        'entry_ltp'    : p['entry_ltp'],
        'entry_spot'   : p['entry_spot'],
        'current_ltp'  : exit_ltp,
        'current_spot' : p['current_spot'] or '',
        'pnl'          : round((exit_ltp - p['entry_ltp']) * total_qty(p), 2),
        'pnl_pct'      : round((exit_ltp - p['entry_ltp']) / p['entry_ltp'] * 100, 1),
        'dte_remaining': dte_remaining(),
        'status'       : 'CLOSED',
        'triggers'     : reason,
        'notes'        : '',
    })
    save_positions(positions)
    print(f"  Saved → {POSITIONS_FILE}  |  Journal updated → {JOURNAL_FILE}")


def add_position(positions):
    """Interactively add a new position."""
    print("\n  ADD NEW POSITION")
    try:
        ticker    = input("  Ticker (e.g. VEDL): ").strip().upper()
        contract  = input("  Contract description (e.g. VEDL CE 700 Mar30): ").strip()
        strike    = float(input("  Strike price: ").strip())
        expiry    = input("  Expiry (e.g. 30-Mar-2026): ").strip()
        lots      = int(input("  Number of lots: ").strip())
        lot_size  = int(input("  Lot size: ").strip())
        entry_ltp = float(input("  Entry LTP (premium paid): ").strip())
        entry_spot= float(input("  Entry spot price: ").strip())
        stop_ltp  = float(input(f"  Stop LTP (suggested {round(entry_ltp*0.5,2)}): ").strip() or entry_ltp*0.5)
        target    = float(input("  Target spot price: ").strip())
        stretch   = float(input("  Stretch target spot (optional, press Enter to skip): ").strip() or target*1.02)
    except (ValueError, KeyboardInterrupt):
        print("  Cancelled.")
        return

    new_pos = {
        'id'              : f"{ticker}_{int(strike)}_custom",
        'ticker'          : ticker,
        'contract'        : contract,
        'type'            : 'CE',
        'strike'          : strike,
        'expiry'          : expiry,
        'lots'            : lots,
        'lot_size'        : lot_size,
        'entry_ltp'       : entry_ltp,
        'entry_spot'      : entry_spot,
        'entry_date'      : datetime.now().strftime('%d-%b-%Y'),
        'entry_dte'       : dte_remaining(),
        'ema20_at_entry'  : None,
        'ema50_at_entry'  : None,
        'stop_ltp'        : stop_ltp,
        'target_spot'     : target,
        'stretch_target'  : stretch,
        'conviction'      : None,
        'r2_252'          : None,
        'r2_90'           : None,
        'adx'             : None,
        'status'          : 'OPEN',
        'current_ltp'     : None,
        'current_spot'    : None,
        'last_updated'    : None,
        'exit_ltp'        : None,
        'exit_date'       : None,
        'exit_reason'     : None,
        'notes'           : '',
    }
    positions.append(new_pos)
    save_positions(positions)
    qty = lots * lot_size
    print(f"\n  Added: {contract}")
    print(f"  Qty: {qty:,}  |  Premium paid: ₹{round(entry_ltp*qty):,}  |  Stop: ₹{stop_ltp}  |  Target spot: ₹{target}")


def print_journal():
    """Print trade journal summary."""
    if not os.path.exists(JOURNAL_FILE):
        print("\n  No journal entries yet.")
        return
    with open(JOURNAL_FILE, 'r') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("\n  Journal is empty.")
        return
    print(f"\n  TRADE JOURNAL — {len(rows)} entries")
    print(f"  {'Timestamp':<18} {'Ticker':<13} {'LTP':>7} {'Spot':>9} {'P&L':>10} {'%':>7}  Triggers")
    print('  ' + '─' * 80)
    for row in rows[-20:]:   # show last 20 entries
        pl_str = f"+₹{float(row['pnl']):,.0f}" if row['pnl'] and float(row['pnl'])>=0 else f"-₹{abs(float(row['pnl'])):,.0f}" if row['pnl'] else '—'
        print(f"  {row['timestamp']:<18} {row['ticker']:<13} {row.get('current_ltp','—'):>7} "
              f"{row.get('current_spot','—'):>9} {pl_str:>10} {row.get('pnl_pct','—'):>7}  {row.get('triggers','')}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='FNO Position Tracker')
    parser.add_argument('--status',  action='store_true', help='Show status only, no update prompt')
    parser.add_argument('--close',   metavar='TICKER',    help='Close a position by ticker name')
    parser.add_argument('--add',     action='store_true', help='Add a new position')
    parser.add_argument('--journal', action='store_true', help='Print trade journal')
    args = parser.parse_args()

    positions = load_positions()

    print_header()

    # ── Subcommands ───────────────────────────────────────────────────────────
    if args.close:
        close_by_ticker(positions, args.close)
        return

    if args.add:
        add_position(positions)
        return

    if args.journal:
        print_journal()
        return

    # ── Default: show all positions ───────────────────────────────────────────
    for p in positions:
        print_position(p)

    print_portfolio_summary(positions)

    # ── Prompt for update (unless --status) ───────────────────────────────────
    if args.status:
        return

    open_pos = [p for p in positions if p['status'] == 'OPEN']
    if open_pos:
        print()
        do_update = input("  Update prices now? (y/n): ").strip().lower()
        if do_update == 'y':
            update_position(positions)
            print()
            print_header()
            for p in positions:
                print_position(p)
            print_portfolio_summary(positions)


if __name__ == '__main__':
    main()
