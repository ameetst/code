"""
Write-path rehearsal for the webapp -- run it before using the webapp in write mode (and again
after any change to the write routes).

Copies the configured universe's data files to a temporary folder, starts the real Flask app
against that COPY with SHARPE_READ_ONLY=0, and exercises every write route: trade add / edit /
partial sell / oversell guard / delete, cash ledger add / edit / over-withdrawal warning /
delete, config save, and the daily regime record. After each step it checks the files that
changed (same keys as the Streamlit-written rows, positions ledger re-synced from the tradelog,
deletes restoring the baseline exactly) and finally that the LIVE files were never touched.

    python webapp/tools/rehearse_writes.py          (from momentum/Sharpe, any python with the webapp deps)
"""
import copy
import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_DIR))
LIVE = Path(os.environ.get("SHARPE_DATA_DIR", CODE_DIR)).resolve()

_cfg = json.loads((LIVE / "dashboard_config.json").read_text())
_universe = _cfg["file"].replace("_updated.xlsx", "").replace(".xlsx", "")
_needed = ["dashboard_config.json", _cfg["file"], "STOCKDB.csv", f"{_universe}_tradelog.json",
           f"{_universe}_positions_ledger.json", f"{_universe}_cash_ledger.json",
           f"{_universe}_regime_history.json", f"{_universe}_equity_history.json"]
DATA = Path(tempfile.mkdtemp(prefix="sharpe_rehearsal_"))
for name in _needed:
    if (LIVE / name).exists():
        shutil.copy2(LIVE / name, DATA / name)
# strip today's regime point from the copy so the "record today" path is actually exercised
_rp = DATA / f"{_universe}_regime_history.json"
if _rp.exists():
    _h = [e for e in json.loads(_rp.read_text()) if e["date"] != datetime.date.today().isoformat()]
    _rp.write_text(json.dumps(_h, indent=2))

os.environ["SHARPE_DATA_DIR"] = str(DATA)
os.environ["SHARPE_READ_ONLY"] = "0"
os.environ["LIVE_DIR"] = str(LIVE)
U = _universe

from webapp import settings
from webapp.app import create_app
from webapp.core import actions, cash_ledger, config_store, rankings, tradelog

assert settings.READ_ONLY is False and settings.DATA_DIR == DATA.resolve()

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))


def load(name):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else None


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


LIVE_FILES = [f"{U}_tradelog.json", f"{U}_positions_ledger.json", f"{U}_cash_ledger.json",
              f"{U}_regime_history.json", "dashboard_config.json"]
live_before = {f: sha(LIVE / f) for f in LIVE_FILES}

# Baseline snapshot BEFORE the app starts: in write mode its start-up warm-up already
# records today's regime point on its own, which must not leak into the baseline.
base = {n: load(n) for n in [f"{U}_tradelog.json", f"{U}_positions_ledger.json",
                             f"{U}_cash_ledger.json", "dashboard_config.json",
                             f"{U}_regime_history.json"]}
app = create_app()
app.config.update(TESTING=True)
c = app.test_client()

cfg = config_store.load_config()
universe = config_store.universe_name(config_store.resolve_file(cfg))
bundle = rankings.get_bundle(cfg)
today = datetime.date.today().isoformat()
held = set(base[f"{U}_positions_ledger.json"])
ticker = next(t for t in bundle.result.index[bundle.result["RANK"].notna()]
              if t not in held and not bundle.purchase_block.get(t, ""))
px = round(float(bundle.latest_prices[ticker]), 2)
print(f"rehearsal ticker {ticker} @ Rs {px} | universe {universe} | copy {DATA}\n")


def realized():
    return tradelog.calculate_holdings_and_pnl(tradelog.load_tradelog(universe))["realized_pnl"]


def ledger_matches_tradelog():
    active = tradelog.calculate_holdings_and_pnl(tradelog.load_tradelog(universe))["active_holdings"]
    led = load(f"{U}_positions_ledger.json")
    return (set(led) == set(active)
            and all(abs(led[t]["qty"] - active[t]["qty"]) < 1e-9 for t in active)
            and all(abs(led[t]["entry_price"] - active[t]["avg_price"]) < 1e-6 for t in active))


# ── Tradelog: add BUY ─────────────────────────────────────────────────────────
r0 = realized()
r = c.post("/tradelog/add", data={"ticker": ticker, "action": "BUY", "date": today, "quantity": "10", "price": str(px)})
html = r.data.decode()
tl = load(f"{U}_tradelog.json")
check("add BUY: 200 + success banner", r.status_code == 200 and f"Recorded BUY 10 shares of {ticker}" in html)
check("add BUY: tradelog gained exactly one row", len(tl) == len(base[f"{U}_tradelog.json"]) + 1)
new_tx = tl[-1]
check("add BUY: new row has the same keys as existing rows",
      set(new_tx) == set(base[f"{U}_tradelog.json"][0]), f"{sorted(new_tx)} vs {sorted(base[f'{U}_tradelog.json'][0])}")
check("add BUY: row values correct", new_tx["ticker"] == ticker and new_tx["action"] == "BUY"
      and new_tx["quantity"] == 10 and new_tx["price"] == px and new_tx["date"] == today)
check("add BUY: positions ledger synced from tradelog", ledger_matches_tradelog() and ticker in load(f"{U}_positions_ledger.json"))
check("add BUY: .bak holds the previous tradelog", json.loads((DATA / f"{U}_tradelog.bak").read_text()) == base[f"{U}_tradelog.json"])
check("add BUY: realized PnL unchanged by a buy", abs(realized() - r0) < 1e-9)

# ── edit it ───────────────────────────────────────────────────────────────────
r = c.post(f"/tradelog/edit/{new_tx['id']}", data={"ticker": ticker, "action": "BUY", "date": today, "quantity": "12", "price": str(px)})
tl = load(f"{U}_tradelog.json")
check("edit: success + row updated", "Transaction updated" in r.data.decode() and tl[-1]["quantity"] == 12 and len(tl) == len(base[f"{U}_tradelog.json"]) + 1)
check("edit: positions ledger re-synced (qty 12)", ledger_matches_tradelog() and load(f"{U}_positions_ledger.json")[ticker]["qty"] == 12)

# ── partial SELL changes realized PnL; cash ledger box shows the same number ─
sell_px = round(px * 1.10, 2)
r = c.post("/tradelog/add", data={"ticker": ticker, "action": "SELL", "date": today, "quantity": "5", "price": str(sell_px)})
sell_tx = load(f"{U}_tradelog.json")[-1]
expected = r0 + 5 * (sell_px - px)
check("partial SELL: recorded", f"Recorded SELL 5 shares of {ticker}" in r.data.decode())
check("partial SELL: realized PnL moved by 5*(sell-avg)", abs(realized() - expected) < 0.01, f"{realized()} vs {expected}")
check("partial SELL: tradelog page shows the new realized PnL", f"Rs {realized():,.2f}" in c.get("/tradelog").data.decode())
cash_html = c.get("/cash").data.decode()
dep = sum(e["amount"] for e in load(f"{U}_cash_ledger.json") if e["type"] == "Deposit")
check("cash ledger shows same realized PnL and Net = Deposits + Realized",
      f"Rs {realized():,.2f}" in cash_html and f"Rs {dep + realized():,.2f}" in cash_html)
check("partial SELL: ledger qty now 7", ledger_matches_tradelog() and load(f"{U}_positions_ledger.json")[ticker]["qty"] == 7)

# ── guard: oversell is rejected and changes nothing ───────────────────────────
before = load(f"{U}_tradelog.json")
r = c.post("/tradelog/add", data={"ticker": ticker, "action": "SELL", "date": today, "quantity": "99999", "price": str(px)})
check("oversell rejected, tradelog untouched", "Cannot SELL" in r.data.decode() and load(f"{U}_tradelog.json") == before)

# ── delete both → back to baseline exactly ────────────────────────────────────
r = c.post("/tradelog/delete", data={"tx_id": [new_tx["id"], sell_tx["id"]]})
check("delete: success", "Deleted 2 transaction" in r.data.decode())
check("delete: tradelog identical to baseline", load(f"{U}_tradelog.json") == base[f"{U}_tradelog.json"])
check("delete: positions ledger identical to baseline", load(f"{U}_positions_ledger.json") == base[f"{U}_positions_ledger.json"])
check("delete: realized PnL back to baseline", abs(realized() - r0) < 1e-9)

# ── Cash ledger: add / edit / over-withdrawal warning / delete ────────────────
r = c.post("/cash/add", data={"type": "Deposit", "date": today, "amount": "1000", "note": "rehearsal"})
ce = load(f"{U}_cash_ledger.json")
check("cash add: recorded, one new row", "Recorded Deposit" in r.data.decode() and len(ce) == len(base[f"{U}_cash_ledger.json"]) + 1)
dep_id = ce[-1]["id"]
check("cash add: row keys match existing rows", set(ce[-1]) == set(base[f"{U}_cash_ledger.json"][0]))
r = c.post(f"/cash/edit/{dep_id}", data={"type": "Deposit", "date": today, "amount": "2000", "note": "rehearsal edit"})
check("cash edit: amount updated", load(f"{U}_cash_ledger.json")[-1]["amount"] == 2000)
r = c.post("/cash/add", data={"type": "Withdrawal", "date": today, "amount": "99999999999", "note": "rehearsal"})
html = r.data.decode()
wd_id = load(f"{U}_cash_ledger.json")[-1]["id"]
check("cash over-withdrawal: warning shown but still recorded", "exceeds the current net cash" in html and load(f"{U}_cash_ledger.json")[-1]["type"] == "Withdrawal")
r = c.post("/cash/delete", data={"entry_id": [dep_id, wd_id]})
check("cash delete: ledger identical to baseline", load(f"{U}_cash_ledger.json") == base[f"{U}_cash_ledger.json"])

# ── Config save: only the changed key moves; the file is shared with Sharpe.py ─
form = {"file": cfg["file"], "capital": str(int(cfg["capital"])), "min_n": str(cfg["min_n"]), "max_n": str(cfg["max_n"]),
        "min_turnover": str(cfg["min_turnover"]), "min_cmp": "55", "min_market_cap": str(int(cfg["min_market_cap"])),
        "circuit_threshold": str(cfg["circuit_threshold"]), "rel_dd_breach_threshold": str(cfg["rel_dd_breach_threshold"]),
        "hold_rank_buffer": str(cfg["hold_rank_buffer"])}
for k in ("eq_series_filter", "circuit_filter_enabled"):
    if cfg[k]:
        form[k] = "on"
r = c.post("/config-page/save", data=form)
new_cfg = load("dashboard_config.json")
diff = {k for k in new_cfg if new_cfg[k] != base["dashboard_config.json"].get(k)}
check("config save: success banner", "Configuration saved" in r.data.decode())
check("config save: only min_cmp changed (100 -> 55)", diff == {"min_cmp"} and new_cfg["min_cmp"] == 55, str(diff))
check("config save: same key set as before (Sharpe.py/Streamlit compatible)", set(new_cfg) == set(base["dashboard_config.json"]))
form["min_cmp"] = str(int(cfg["min_cmp"]))
c.post("/config-page/save", data=form)
check("config restore: identical to baseline", load("dashboard_config.json") == base["dashboard_config.json"])

# ── Regime history: recorded on first load, idempotent after ──────────────────
c.get("/")
rh = load(f"{U}_regime_history.json")
check("regime: today's point recorded on Top Rankings load (+1 entry)", rh[-1]["date"] == today and len(rh) == len(base[f"{U}_regime_history.json"]) + 1)
m = (DATA / f"{U}_regime_history.json").stat().st_mtime_ns
c.get("/")
check("regime: second load does not rewrite the file", (DATA / f"{U}_regime_history.json").stat().st_mtime_ns == m)

# ── The live files were never touched by this rehearsal ───────────────────────
live_after = {f: sha(LIVE / f) for f in LIVE_FILES}
check("LIVE data files untouched by the rehearsal", live_before == live_after,
      str({f for f in LIVE_FILES if live_before[f] != live_after[f]}) + " (changed on their own -- your live session?)")

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if failed else 0)
