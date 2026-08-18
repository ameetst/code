"""
ETF Momentum v2 -- Desktop Dashboard (Tkinter)
================================================
Desktop equivalent of etf_dashboard.py (the Streamlit web dashboard), wired
to etf_momentum_ranking_v2.py -- 3W-STRICT Sharpe + Monthly Full-Flush +
Inverse-Vol Sizing + No Sector Cap -- instead of the live weekly script.

Run:
  python etf_momentum_gui_v2.py

Tabs (mirrors etf_dashboard.py's feature set):
  1. Current Recommendation -- this month's allocation, Run/Force Rebalance,
     Open Rankings Excel. Row-click prefills the Tradelog tab's trade form.
  2. Full Rankings          -- filterable full ranking table (screen /
     sector / top-N filters). Row-click prefills the trade form.
  3. Configuration          -- editable strategy params, writes/reads
     strategy_config_v2.json, Save + Run-with-new-config buttons.
  4. Tradelog & MTM         -- FIFO avg-cost P&L engine (ported from
     etf_dashboard.py), active holdings with informational exit-rule
     display, live price refresh, log/edit/delete transactions.

Isolated state -- separate from etf_dashboard.py and the live script:
  ETF_tradelog_v2.json, ETF_positions_ledger_v2.json
  (etf_momentum_ranking_v2.py itself owns etf_rankings_v2.xlsx,
  holdings_log_v2.json, strategy_config_v2.json)
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import queue
import json
import shutil
import uuid
import datetime
import sys
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import etf_momentum_ranking_v2 as emr

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


# =========================================================
# ISOLATED TRADELOG STATE (separate file names from etf_dashboard.py)
# =========================================================
ETF_TRADELOG_FILE    = SCRIPT_DIR / "ETF_tradelog_v2.json"
ETF_POSITIONS_LEDGER = SCRIPT_DIR / "ETF_positions_ledger_v2.json"


def safe_write_json(path, data):
    """Atomic JSON write: write to .tmp, backup existing to .bak, rename .tmp -> target."""
    path = Path(path)
    tmp_path = path.with_suffix(".tmp")
    bak_path = path.with_suffix(".bak")
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        if path.exists():
            shutil.copy2(path, bak_path)
        shutil.move(str(tmp_path), str(path))
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise e


def load_tradelog() -> list:
    if not ETF_TRADELOG_FILE.exists():
        try:
            safe_write_json(ETF_TRADELOG_FILE, [])
        except Exception:
            pass
        return []
    try:
        with open(ETF_TRADELOG_FILE) as f:
            return json.load(f)
    except Exception:
        bak = ETF_TRADELOG_FILE.with_suffix(".bak")
        if bak.exists():
            try:
                with open(bak) as f:
                    return json.load(f)
            except Exception:
                pass
        return []


def save_tradelog(tradelog: list):
    safe_write_json(ETF_TRADELOG_FILE, tradelog)


def validate_tradelog_integrity(transactions: list):
    """Replay all transactions chronologically; check no ticker ever goes negative."""
    try:
        sorted_txs = sorted(transactions, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_txs = transactions
    holdings = {}
    for tx in sorted_txs:
        ticker  = tx["ticker"]
        action  = tx["action"].upper()
        qty     = float(tx["quantity"])
        current = holdings.get(ticker, 0.0)
        if action == "BUY":
            holdings[ticker] = current + qty
        elif action == "SELL":
            if qty > current + 1e-9:
                return False, (f"{ticker}: SELL {qty:.0f} exceeds holding "
                               f"{current:.0f} on {tx.get('date', '?')}")
            holdings[ticker] = current - qty
    return True, ""


def calculate_holdings_and_pnl(transactions: list, latest_prices: dict | None = None) -> dict:
    """FIFO avg-cost P&L engine -- ported verbatim from etf_dashboard.py."""
    try:
        sorted_txs = sorted(transactions, key=lambda x: (x.get("date", ""), x.get("timestamp", "")))
    except Exception:
        sorted_txs = transactions

    holdings: dict = {}
    realized_pnl = 0.0
    realized_pnl_by_ticker: dict = {}

    for tx in sorted_txs:
        ticker   = tx["ticker"]
        action   = tx["action"].upper()
        qty      = float(tx["quantity"])
        price    = float(tx["price"])
        tx_date  = tx.get("date", "")
        if isinstance(tx_date, str):
            try:
                tx_date = datetime.date.fromisoformat(tx_date)
            except Exception:
                tx_date = datetime.date.today()

        if ticker not in holdings:
            holdings[ticker] = {"qty": 0.0, "avg_price": 0.0,
                                "first_buy_date": None, "total_cost": 0.0}
        h      = holdings[ticker]
        t_pnl  = realized_pnl_by_ticker.get(ticker, 0.0)

        if action == "BUY":
            if h["qty"] == 0:
                h["first_buy_date"] = tx_date
            h["total_cost"] += qty * price
            h["qty"]        += qty
            h["avg_price"]   = h["total_cost"] / h["qty"]
        elif action == "SELL":
            if h["qty"] > 0:
                sell_qty = min(qty, h["qty"])
                pnl      = sell_qty * (price - h["avg_price"])
                realized_pnl += pnl
                t_pnl        += pnl
                h["qty"]     -= sell_qty
                h["total_cost"] = h["qty"] * h["avg_price"]
                if h["qty"] == 0:
                    h["avg_price"]      = 0.0
                    h["first_buy_date"] = None
        realized_pnl_by_ticker[ticker] = t_pnl

    active_holdings = {t: h for t, h in holdings.items() if h["qty"] > 0}
    unrealized_pnl  = 0.0
    holdings_metrics = []

    for ticker, h in active_holdings.items():
        curr_price = h["avg_price"]
        if latest_prices and ticker in latest_prices:
            curr_price = latest_prices[ticker]
        market_val  = h["qty"] * curr_price
        u_pnl       = market_val - h["total_cost"]
        unrealized_pnl += u_pnl
        u_pnl_pct   = (u_pnl / h["total_cost"] * 100) if h["total_cost"] > 0 else 0.0
        holdings_metrics.append({
            "Ticker":            ticker,
            "Qty":               h["qty"],
            "Avg Price":         h["avg_price"],
            "Current Price":     curr_price,
            "Cost Value":        h["total_cost"],
            "Market Value":      market_val,
            "Unrealized PnL":    u_pnl,
            "Unrealized PnL %":  u_pnl_pct,
            "First Buy Date":    h["first_buy_date"],
        })

    return {
        "active_holdings":        active_holdings,
        "holdings_metrics":       holdings_metrics,
        "realized_pnl":           realized_pnl,
        "realized_pnl_by_ticker": realized_pnl_by_ticker,
        "unrealized_pnl":         unrealized_pnl,
    }


def sync_to_positions_ledger(active_holdings: dict):
    """Write active ETF holdings to ETF_positions_ledger_v2.json."""
    serialisable = {}
    for ticker, h in active_holdings.items():
        if h["qty"] > 0:
            entry_date_str = h["first_buy_date"]
            if isinstance(entry_date_str, (datetime.date, datetime.datetime)):
                entry_date_str = entry_date_str.isoformat()
            serialisable[ticker] = {
                "entry_date":  entry_date_str,
                "entry_price": float(h["avg_price"]),
            }
    try:
        safe_write_json(ETF_POSITIONS_LEDGER, serialisable)
    except Exception:
        pass


def get_etf_latest_price(ticker: str, prices_df) -> float:
    if prices_df is not None and ticker in prices_df.columns:
        series = prices_df[ticker].dropna()
        if not series.empty:
            return float(series.iloc[-1])
    return 0.0


def fmt_money(x):
    try:
        return f"Rs {x:,.2f}"
    except Exception:
        return "-"


def fmt_pct(x):
    try:
        return f"{x:+.2f}%"
    except Exception:
        return "-"


# =========================================================
# THREAD-SAFE STDOUT REDIRECT (pipeline print() -> log panel)
# =========================================================
class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, msg):
        if msg:
            self.q.put(("log", msg))

    def flush(self):
        pass


# =========================================================
# MAIN APPLICATION
# =========================================================
class ETFMomentumV2App:
    def __init__(self, root):
        self.root = root
        self.root.title("ETF Momentum v2 — 3W-STRICT / Monthly / Inverse-Vol (Desktop)")
        self.root.geometry("1360x860")

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TButton", font=("Segoe UI", 9), padding=5)
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Bull.TLabel", background="#E8F5E9", foreground="#2E7D32", font=("Segoe UI", 10, "bold"))
        style.configure("Partial.TLabel", background="#FFF8E1", foreground="#F57F17", font=("Segoe UI", 10, "bold"))
        style.configure("Bear.TLabel", background="#FFEBEE", foreground="#C62828", font=("Segoe UI", 10, "bold"))

        # ── State ──────────────────────────────────────────
        self.meta = None
        self.prices = None
        self.regime = None
        self.ranking = pd.DataFrame()
        self.allocation = pd.DataFrame()
        self.tradelog = []
        self.holdings_metrics = []
        self.active_holdings = {}
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.live_price_overrides = {}
        self.etf_tickers = []
        self.selected_trade_ticker = tk.StringVar()

        self.msg_queue = queue.Queue()

        self._build_banner()
        self._build_tabs()
        self._build_statusbar()

        self.root.after(100, self._poll_queue)
        self._run_pipeline_async(force=False, initial=True)

    # =====================================================
    # BANNER (always visible)
    # =====================================================
    def _build_banner(self):
        title_frame = ttk.Frame(self.root, padding=(15, 10, 15, 4))
        title_frame.pack(fill=tk.X)
        ttk.Label(title_frame, text="ETF Momentum v2", font=("Segoe UI", 16, "bold"),
                  foreground="#1F4E79").pack(side=tk.LEFT)
        ttk.Label(title_frame, text="   Screen → Score (3W-STRICT) → Regime → Monthly Full-Flush (Inv-Vol)",
                  foreground="#6B7A8D").pack(side=tk.LEFT)

        self.banner_frame = ttk.Frame(self.root, padding=(15, 4, 15, 8))
        self.banner_frame.pack(fill=tk.X)
        self.banner_vars = {}
        labels = ["Regime", "Price", "EMA 50", "EMA 100", "Active Slots", "Trend Ticker", "Sizing", "Data Range"]
        for i, lab in enumerate(labels):
            cell = ttk.Frame(self.banner_frame, relief=tk.GROOVE, borderwidth=1, padding=8)
            cell.grid(row=0, column=i, sticky="nsew", padx=3)
            self.banner_frame.columnconfigure(i, weight=1)
            ttk.Label(cell, text=lab.upper(), font=("Segoe UI", 8, "bold"), foreground="#6B7A8D").pack(anchor="w")
            v = tk.StringVar(value="—")
            self.banner_vars[lab] = v
            lbl = ttk.Label(cell, textvariable=v, font=("Segoe UI", 11, "bold"))
            lbl.pack(anchor="w")
            if lab == "Regime":
                self.regime_label_widget = lbl
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=15)

    def _update_banner(self):
        r = self.regime or {}
        label = r.get("label", "—")
        self.banner_vars["Regime"].set(label)
        style_map = {"BULL": "Bull.TLabel", "PARTIAL": "Partial.TLabel", "BEAR": "Bear.TLabel"}
        self.regime_label_widget.configure(style=style_map.get(label, "TLabel"))
        self.banner_vars["Price"].set(f"{r.get('nifty_price', float('nan')):.2f}" if r else "—")
        self.banner_vars["EMA 50"].set(f"{r.get('nifty_ema_50', float('nan')):.2f}" if r else "—")
        self.banner_vars["EMA 100"].set(f"{r.get('nifty_ema_100', float('nan')):.2f}" if r else "—")
        self.banner_vars["Active Slots"].set(f"{r.get('active_slots', '-')} / {emr.CONFIG.TOP_N}")
        self.banner_vars["Trend Ticker"].set(r.get("trend_ticker", "N/A"))
        self.banner_vars["Sizing"].set(f"{emr.CONFIG.SIZING_MODE} | cap={emr.CONFIG.SECTOR_CAP}")
        if self.prices is not None and len(self.prices):
            self.banner_vars["Data Range"].set(
                f"{self.prices.index[0].strftime('%Y-%m-%d')} → {self.prices.index[-1].strftime('%Y-%m-%d')}"
            )

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Ready.")
        bar = ttk.Frame(self.root, relief=tk.SUNKEN)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(bar, textvariable=self.status_var, padding=(8, 3)).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=150)
        self.progress.pack(side=tk.RIGHT, padx=8, pady=2)

    # =====================================================
    # TABS
    # =====================================================
    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=15, pady=8)

        self.tab_reco = ttk.Frame(self.notebook)
        self.tab_rankings = ttk.Frame(self.notebook)
        self.tab_config = ttk.Frame(self.notebook)
        self.tab_tradelog = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_reco, text="📊 Current Recommendation")
        self.notebook.add(self.tab_rankings, text="📋 Full Rankings")
        self.notebook.add(self.tab_config, text="⚙️ Configuration")
        self.notebook.add(self.tab_tradelog, text="📝 Tradelog & MTM")

        self._build_tab_reco()
        self._build_tab_rankings()
        self._build_tab_config()
        self._build_tab_tradelog()

    # ---------------------------------------------------
    # TAB 1: CURRENT RECOMMENDATION
    # ---------------------------------------------------
    def _build_tab_reco(self):
        top = ttk.Frame(self.tab_reco, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="This Month's Allocation", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        self.reco_status_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.reco_status_var, foreground="#6B7A8D").pack(side=tk.LEFT, padx=12)

        cols = ("Slot", "Ticker", "ETF Name", "Sector", "Weight", "Inv Rank")
        self.reco_tree = ttk.Treeview(self.tab_reco, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (50, 100, 320, 140, 80, 80)):
            self.reco_tree.heading(c, text=c)
            self.reco_tree.column(c, width=w, anchor="w" if c in ("ETF Name",) else "center")
        self.reco_tree.pack(fill=tk.X, padx=10, pady=6)
        self.reco_tree.bind("<<TreeviewSelect>>", self._on_select_reco_row)
        self.reco_tree.tag_configure("cash", foreground="#9AA5B4")

        ttk.Label(self.tab_reco, text="💡 Click a row to pre-fill the trade form in Tradelog & MTM.",
                  foreground="#6B7A8D").pack(anchor="w", padx=10)

        act = ttk.Frame(self.tab_reco, padding=10)
        act.pack(fill=tk.X)
        ttk.Button(act, text="🔁 Run Monthly Rebalance", style="Action.TButton",
                   command=lambda: self._run_pipeline_async(force=False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(act, text="⚠️ Force Re-Flush This Month", style="Action.TButton",
                   command=self._on_force_rebalance).pack(side=tk.LEFT, padx=4)
        ttk.Button(act, text="📥 Open Rankings Excel", style="Action.TButton",
                   command=self._open_rankings_excel).pack(side=tk.LEFT, padx=4)
        ttk.Button(act, text="🔄 Refresh Data", style="Action.TButton",
                   command=lambda: self._run_pipeline_async(force=False)).pack(side=tk.LEFT, padx=4)

        ttk.Label(self.tab_reco,
                  text="Note: monthly full-flush is idempotent within a calendar month -- running\n"
                       "'Run Monthly Rebalance' again this month just re-displays the current picks. "
                       "'Force Re-Flush' recomputes and sells/rebuys immediately, any time.",
                  foreground="#6B7A8D", justify=tk.LEFT).pack(anchor="w", padx=10, pady=(0, 8))

        ttk.Separator(self.tab_reco, orient="horizontal").pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(self.tab_reco, text="Pipeline Log", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10)
        self.log_text = scrolledtext.ScrolledText(self.tab_reco, height=14, font=("Consolas", 9),
                                                    bg="#0F1117", fg="#D6E2F0")
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

    def _on_select_reco_row(self, event):
        sel = self.reco_tree.selection()
        if not sel:
            return
        vals = self.reco_tree.item(sel[0], "values")
        ticker = vals[1]
        if ticker and ticker != "CASH":
            self._prefill_trade_form(ticker)

    def _refresh_reco_tab(self):
        for row in self.reco_tree.get_children():
            self.reco_tree.delete(row)
        for _, a in self.allocation.iterrows():
            is_cash = a["TICKER"] == "CASH"
            tag = ("cash",) if is_cash else ()
            self.reco_tree.insert("", "end", values=(
                int(a["SLOT"]), a["TICKER"], a["ETF_NAME"], a["SECTOR"],
                f"{a['WEIGHT']:.1%}", a["INV_RANK"],
            ), tags=tag)

    # ---------------------------------------------------
    # TAB 2: FULL RANKINGS
    # ---------------------------------------------------
    def _build_tab_rankings(self):
        filt = ttk.Frame(self.tab_rankings, padding=10)
        filt.pack(fill=tk.X)

        ttk.Label(filt, text="Screen:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.rank_screen_var = tk.StringVar(value="All")
        ttk.Combobox(filt, textvariable=self.rank_screen_var, values=["All", "PASS only", "FAIL only"],
                     width=12, state="readonly").grid(row=0, column=1, padx=(0, 16))

        ttk.Label(filt, text="Sector:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        self.rank_sector_var = tk.StringVar(value="All")
        self.rank_sector_combo = ttk.Combobox(filt, textvariable=self.rank_sector_var, values=["All"],
                                               width=20, state="readonly")
        self.rank_sector_combo.grid(row=0, column=3, padx=(0, 16))

        ttk.Label(filt, text="Show top N:").grid(row=0, column=4, sticky="w", padx=(0, 4))
        self.rank_topn_var = tk.IntVar(value=50)
        ttk.Spinbox(filt, from_=5, to=300, increment=5, textvariable=self.rank_topn_var,
                    width=6).grid(row=0, column=5, padx=(0, 16))

        ttk.Button(filt, text="Apply Filters", command=self._apply_rankings_filter).grid(row=0, column=6)

        cols = ("Inv Rank", "Ticker", "ETF Name", "Sector", "Wtd Sharpe", "Sharpe 12M",
                "Sharpe 6M", "Sharpe 3M", "Screen")
        self.rank_tree = ttk.Treeview(self.tab_rankings, columns=cols, show="headings", height=28)
        widths = (65, 100, 300, 140, 85, 85, 80, 80, 60)
        for c, w in zip(cols, widths):
            self.rank_tree.heading(c, text=c)
            self.rank_tree.column(c, width=w, anchor="w" if c == "ETF Name" else "center")
        vsb = ttk.Scrollbar(self.tab_rankings, orient="vertical", command=self.rank_tree.yview)
        self.rank_tree.configure(yscrollcommand=vsb.set)
        self.rank_tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=(10, 0), pady=6)
        vsb.pack(fill=tk.Y, side=tk.LEFT, pady=6)
        self.rank_tree.bind("<<TreeviewSelect>>", self._on_select_ranking_row)
        self.rank_tree.tag_configure("fail", foreground="#B0B0B0")
        self.rank_tree.tag_configure("investable", background="#E8F5E9")

        self.rankings_caption_var = tk.StringVar(value="")
        ttk.Label(self.tab_rankings, textvariable=self.rankings_caption_var,
                  foreground="#6B7A8D").pack(anchor="w", padx=10, pady=4)

    def _on_select_ranking_row(self, event):
        sel = self.rank_tree.selection()
        if not sel:
            return
        vals = self.rank_tree.item(sel[0], "values")
        ticker = vals[1]
        if ticker and ticker != "CASH":
            self._prefill_trade_form(ticker)

    def _apply_rankings_filter(self):
        self._refresh_rankings_tab()

    def _refresh_rankings_tab(self):
        if self.ranking is None or self.ranking.empty:
            return
        df = self.ranking.copy()

        sectors = ["All"] + sorted(df["SECTOR"].dropna().unique().tolist())
        self.rank_sector_combo["values"] = sectors

        screen_choice = self.rank_screen_var.get()
        if screen_choice == "PASS only":
            df = df[df["SCREEN_PASS"] == True]
        elif screen_choice == "FAIL only":
            df = df[df["SCREEN_PASS"] == False]
        if self.rank_sector_var.get() != "All":
            df = df[df["SECTOR"] == self.rank_sector_var.get()]
        df = df.head(self.rank_topn_var.get())

        for row in self.rank_tree.get_children():
            self.rank_tree.delete(row)

        active_slots = (self.regime or {}).get("active_slots", 0)
        for _, r in df.iterrows():
            def f(v):
                return f"{v:.3f}" if pd.notna(v) else "—"
            inv_rk = str(int(r["RANK_INVESTABLE"])) if pd.notna(r.get("RANK_INVESTABLE")) else ""
            screen = "PASS" if r["SCREEN_PASS"] else "FAIL"
            tag = ()
            if not r["SCREEN_PASS"]:
                tag = ("fail",)
            else:
                try:
                    if int(inv_rk) <= active_slots:
                        tag = ("investable",)
                except (ValueError, TypeError):
                    pass
            self.rank_tree.insert("", "end", values=(
                inv_rk, r["TICKER"], r["ETF_NAME"], r["SECTOR"],
                f(r.get("WTD_SHARPE")), f(r.get("SHARPE_12M")), f(r.get("SHARPE_6M")), f(r.get("SHARPE_3M")),
                screen,
            ), tags=tag)

        inv_count = int(self.ranking["SCREEN_PASS"].sum())
        self.rankings_caption_var.set(
            f"Universe: {len(self.ranking)} ETFs  |  Investable: {inv_count}  |  "
            f"Screened out: {len(self.ranking) - inv_count}"
        )

    # ---------------------------------------------------
    # TAB 3: CONFIGURATION
    # ---------------------------------------------------
    def _build_tab_config(self):
        outer = ttk.Frame(self.tab_config, padding=15)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text="Strategy Configuration", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Edit parameters and click Save to persist to strategy_config_v2.json.",
                  foreground="#6B7A8D").pack(anchor="w", pady=(0, 10))

        form = ttk.Frame(outer)
        form.pack(fill=tk.X)
        self.cfg_vars = {}

        def add_row(r, label, key, width=12):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=4)
            var = tk.StringVar()
            ttk.Entry(form, textvariable=var, width=width).grid(row=r, column=1, sticky="w", pady=4)
            self.cfg_vars[key] = var

        add_row(0, "Top N (BULL):", "TOP_N")
        add_row(1, "Top N (PARTIAL):", "TOP_N_PARTIAL")
        add_row(2, "Sector Cap (>=TOP_N = no cap):", "SECTOR_CAP")
        add_row(3, "Sizing Mode (equal / invvol):", "SIZING_MODE", width=14)
        add_row(4, "Vol Window (days, for invvol):", "VOL_WINDOW")
        add_row(5, "12M Window (days):", "WINDOW_12M")
        add_row(6, "6M Window (days):", "WINDOW_6M")
        add_row(7, "3M Window (days):", "WINDOW_3M")
        add_row(8, "Max Drawdown from 52W High:", "MAX_DRAWDOWN_FROM_HIGH")
        add_row(9, "Index Ticker (Yahoo Finance):", "REGIME_INDEX_TICKER", width=14)
        add_row(10, "Regime Ticker (fallback):", "REGIME_TICKER", width=14)
        add_row(11, "Fast EMA Window:", "TREND_FAST_EMA_WINDOW")
        add_row(12, "Slow EMA Window:", "TREND_EMA_WINDOW")
        add_row(13, "Risk-Free Rate (annual):", "DAILY_RF_ANNUAL")

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, pady=14)
        ttk.Button(btn_row, text="💾 Save Configuration", style="Action.TButton",
                   command=self._on_save_config).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="🔁 Save & Run With New Config", style="Action.TButton",
                   command=self._on_run_with_new_config).pack(side=tk.LEFT, padx=4)

        ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, pady=8)
        ttk.Label(outer, text="Current strategy_config_v2.json", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.cfg_text = scrolledtext.ScrolledText(outer, height=12, font=("Consolas", 9))
        self.cfg_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self._load_config_into_form()

    def _load_config_into_form(self):
        cfg = emr.get_config_as_dict()
        mapping = {
            "TOP_N": cfg["TOP_N"], "TOP_N_PARTIAL": cfg["TOP_N_PARTIAL"],
            "SECTOR_CAP": cfg["SECTOR_CAP"], "SIZING_MODE": cfg["SIZING_MODE"],
            "VOL_WINDOW": cfg["VOL_WINDOW"], "WINDOW_12M": cfg["WINDOW_12M"],
            "WINDOW_6M": cfg["WINDOW_6M"], "WINDOW_3M": cfg["WINDOW_3M"],
            "MAX_DRAWDOWN_FROM_HIGH": cfg["MAX_DRAWDOWN_FROM_HIGH"],
            "REGIME_INDEX_TICKER": cfg["REGIME_INDEX_TICKER"], "REGIME_TICKER": cfg["REGIME_TICKER"],
            "TREND_FAST_EMA_WINDOW": cfg["TREND_FAST_EMA_WINDOW"],
            "TREND_EMA_WINDOW": cfg["TREND_EMA_WINDOW"],
            "DAILY_RF_ANNUAL": cfg["DAILY_RF_ANNUAL"],
        }
        for key, val in mapping.items():
            self.cfg_vars[key].set(str(val))

        cfg_path = SCRIPT_DIR / "strategy_config_v2.json"
        self.cfg_text.delete("1.0", tk.END)
        if cfg_path.exists():
            self.cfg_text.insert("1.0", cfg_path.read_text())
        else:
            self.cfg_text.insert("1.0", "(no strategy_config_v2.json yet -- using hardcoded defaults)")

    def _collect_config_from_form(self) -> dict:
        cfg = emr.get_config_as_dict()
        try:
            cfg["TOP_N"] = int(self.cfg_vars["TOP_N"].get())
            cfg["TOP_N_PARTIAL"] = int(self.cfg_vars["TOP_N_PARTIAL"].get())
            cfg["SECTOR_CAP"] = int(self.cfg_vars["SECTOR_CAP"].get())
            cfg["SIZING_MODE"] = self.cfg_vars["SIZING_MODE"].get().strip().lower()
            cfg["VOL_WINDOW"] = int(self.cfg_vars["VOL_WINDOW"].get())
            cfg["WINDOW_12M"] = int(self.cfg_vars["WINDOW_12M"].get())
            cfg["WINDOW_6M"] = int(self.cfg_vars["WINDOW_6M"].get())
            cfg["WINDOW_3M"] = int(self.cfg_vars["WINDOW_3M"].get())
            cfg["MAX_DRAWDOWN_FROM_HIGH"] = float(self.cfg_vars["MAX_DRAWDOWN_FROM_HIGH"].get())
            cfg["REGIME_INDEX_TICKER"] = self.cfg_vars["REGIME_INDEX_TICKER"].get().strip()
            cfg["REGIME_TICKER"] = self.cfg_vars["REGIME_TICKER"].get().strip()
            cfg["TREND_FAST_EMA_WINDOW"] = int(self.cfg_vars["TREND_FAST_EMA_WINDOW"].get())
            cfg["TREND_EMA_WINDOW"] = int(self.cfg_vars["TREND_EMA_WINDOW"].get())
            cfg["DAILY_RF_ANNUAL"] = float(self.cfg_vars["DAILY_RF_ANNUAL"].get())
        except ValueError as e:
            raise ValueError(f"Invalid config value: {e}")
        if cfg["SIZING_MODE"] not in ("equal", "invvol"):
            raise ValueError("Sizing Mode must be 'equal' or 'invvol'")
        return cfg

    def _on_save_config(self):
        try:
            cfg = self._collect_config_from_form()
            emr.save_config_to_json(cfg, SCRIPT_DIR)
            emr._apply_json_config(emr.CONFIG, cfg)
            self._load_config_into_form()
            messagebox.showinfo("Saved", "Configuration saved to strategy_config_v2.json")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_run_with_new_config(self):
        try:
            cfg = self._collect_config_from_form()
            emr.save_config_to_json(cfg, SCRIPT_DIR)
            emr._apply_json_config(emr.CONFIG, cfg)
            self._load_config_into_form()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self._run_pipeline_async(force=False)

    # ---------------------------------------------------
    # TAB 4: TRADELOG & MTM
    # ---------------------------------------------------
    def _build_tab_tradelog(self):
        outer = ttk.Frame(self.tab_tradelog, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Metric cards ──
        metrics = ttk.Frame(outer)
        metrics.pack(fill=tk.X, pady=(0, 8))
        self.metric_vars = {}
        for i, label in enumerate(["Total Invested", "Current Market Value", "Unrealized PnL (MTM)", "Realized PnL"]):
            cell = ttk.Frame(metrics, relief=tk.GROOVE, borderwidth=1, padding=10)
            cell.grid(row=0, column=i, sticky="nsew", padx=4)
            metrics.columnconfigure(i, weight=1)
            ttk.Label(cell, text=label, foreground="#6B7A8D", font=("Segoe UI", 8, "bold")).pack(anchor="w")
            v = tk.StringVar(value="Rs 0.00")
            self.metric_vars[label] = v
            ttk.Label(cell, textvariable=v, font=("Segoe UI", 13, "bold")).pack(anchor="w")

        # ── Active holdings ──
        hc = ttk.Frame(outer)
        hc.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(hc, text="💼 Active Holdings", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(hc, text="🔄 Refresh Live Market Prices",
                   command=self._on_refresh_live_prices).pack(side=tk.RIGHT)

        self.breach_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.breach_var, foreground="#C62828",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 4))

        cols = ("Ticker", "Qty", "Avg Price", "Current Price", "Cost Value", "Market Value",
                "Unrealized PnL", "Unrealized PnL %", "First Buy Date", "Exit Reason (info only)")
        self.holdings_tree = ttk.Treeview(outer, columns=cols, show="headings", height=8)
        widths = (90, 60, 90, 100, 100, 100, 100, 90, 100, 220)
        for c, w in zip(cols, widths):
            self.holdings_tree.heading(c, text=c)
            self.holdings_tree.column(c, width=w, anchor="center")
        self.holdings_tree.pack(fill=tk.X, pady=4)
        self.holdings_tree.bind("<<TreeviewSelect>>", self._on_select_holdings_row)
        self.holdings_tree.tag_configure("gain", background="#E8F5E9")
        self.holdings_tree.tag_configure("loss", background="#FFEBEE")
        self.holdings_tree.tag_configure("breach", background="#FFCDD2", foreground="#7F0000")

        ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, pady=8)

        # ── Log new transaction ──
        ttk.Label(outer, text="➕ Log New Transaction", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        form = ttk.Frame(outer, padding=(0, 6))
        form.pack(fill=tk.X)

        ttk.Label(form, text="Ticker:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.tl_ticker_combo = ttk.Combobox(form, textvariable=self.selected_trade_ticker,
                                             width=16, state="readonly")
        self.tl_ticker_combo.grid(row=0, column=1, padx=(0, 16))

        self.tl_action_var = tk.StringVar(value="BUY")
        ttk.Label(form, text="Action:").grid(row=0, column=2, sticky="w", padx=(0, 4))
        ttk.Radiobutton(form, text="BUY", variable=self.tl_action_var, value="BUY").grid(row=0, column=3)
        ttk.Radiobutton(form, text="SELL", variable=self.tl_action_var, value="SELL").grid(row=0, column=4, padx=(0, 16))

        ttk.Label(form, text="Date (YYYY-MM-DD):").grid(row=0, column=5, sticky="w", padx=(0, 4))
        self.tl_date_var = tk.StringVar(value=datetime.date.today().isoformat())
        ttk.Entry(form, textvariable=self.tl_date_var, width=12).grid(row=0, column=6, padx=(0, 16))

        ttk.Label(form, text="Quantity:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        self.tl_qty_var = tk.StringVar(value="10")
        ttk.Entry(form, textvariable=self.tl_qty_var, width=10).grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(form, text="Price/Unit (INR):").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(6, 0))
        self.tl_price_var = tk.StringVar(value="0.00")
        ttk.Entry(form, textvariable=self.tl_price_var, width=10).grid(row=1, column=3, sticky="w", pady=(6, 0))

        ttk.Button(form, text="💾 Record Transaction", style="Action.TButton",
                   command=self._on_submit_trade).grid(row=1, column=6, sticky="w", pady=(6, 0))

        ttk.Separator(outer, orient="horizontal").pack(fill=tk.X, pady=8)

        # ── Transaction history ──
        th = ttk.Frame(outer)
        th.pack(fill=tk.BOTH, expand=True)
        ttk.Label(th, text="🕒 Transaction History", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        tx_cols = ("Date", "Ticker", "Action", "Quantity", "Price", "Total Value", "id")
        self.tx_tree = ttk.Treeview(th, columns=tx_cols, show="headings", height=8, displaycolumns=tx_cols[:-1])
        for c, w in zip(tx_cols[:-1], (90, 90, 60, 80, 90, 100)):
            self.tx_tree.heading(c, text=c)
            self.tx_tree.column(c, width=w, anchor="center")
        self.tx_tree.pack(fill=tk.BOTH, expand=True, pady=4)
        self.tx_tree.bind("<<TreeviewSelect>>", self._on_select_tx_row)
        self.tx_tree.tag_configure("buy", background="#E8F5E9")
        self.tx_tree.tag_configure("sell", background="#FFEBEE")

        tx_btns = ttk.Frame(outer)
        tx_btns.pack(fill=tk.X, pady=4)
        ttk.Button(tx_btns, text="✏️ Save Edits to Selected Transaction",
                   command=self._on_save_tx_edit).pack(side=tk.LEFT, padx=4)
        ttk.Button(tx_btns, text="🗑️ Delete Selected Transaction",
                   command=self._on_delete_tx).pack(side=tk.LEFT, padx=4)

        self._editing_tx_id = None

    # -- selection prefill --
    def _prefill_trade_form(self, ticker):
        self.selected_trade_ticker.set(ticker)
        price = get_etf_latest_price(ticker, self.prices)
        if ticker in self.live_price_overrides:
            price = self.live_price_overrides[ticker]
        self.tl_price_var.set(f"{price:.2f}")
        self.notebook.select(self.tab_tradelog)

    def _on_select_holdings_row(self, event):
        sel = self.holdings_tree.selection()
        if not sel:
            return
        vals = self.holdings_tree.item(sel[0], "values")
        ticker = vals[0]
        self.selected_trade_ticker.set(ticker)
        self.tl_qty_var.set(str(int(float(vals[1]))))
        self.tl_price_var.set(vals[3].replace("Rs ", "").replace(",", ""))

    def _on_select_tx_row(self, event):
        sel = self.tx_tree.selection()
        if not sel:
            self._editing_tx_id = None
            return
        vals = self.tx_tree.item(sel[0], "values")
        tx_id = self.tx_tree.set(sel[0], "id")
        self._editing_tx_id = tx_id
        self.selected_trade_ticker.set(vals[1])
        self.tl_action_var.set(vals[2])
        self.tl_date_var.set(vals[0])
        self.tl_qty_var.set(str(int(float(vals[3]))))
        self.tl_price_var.set(vals[4].replace("Rs ", "").replace(",", ""))

    # -- trade CRUD --
    def _on_submit_trade(self):
        ticker = self.selected_trade_ticker.get()
        if not ticker:
            messagebox.showwarning("No ticker", "Select a ticker first.")
            return
        try:
            qty = int(self.tl_qty_var.get())
            price = float(self.tl_price_var.get())
            trade_date = datetime.date.fromisoformat(self.tl_date_var.get())
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e))
            return
        action = self.tl_action_var.get()

        curr_qty = self.active_holdings.get(ticker, {}).get("qty", 0.0)
        if action == "SELL" and qty > curr_qty:
            messagebox.showerror("Rejected",
                                  f"Cannot SELL {qty} units of {ticker} — you only hold {curr_qty:.0f} units.")
            return

        new_trade = {
            "id": str(uuid.uuid4()), "date": trade_date.isoformat(),
            "timestamp": datetime.datetime.now().isoformat(),
            "ticker": ticker, "action": action, "quantity": qty, "price": price,
        }
        updated = self.tradelog + [new_trade]
        ok, err = validate_tradelog_integrity(updated)
        if not ok:
            messagebox.showerror("Rejected — inconsistent state", err)
            return
        save_tradelog(updated)
        self.tradelog = updated
        self._recompute_tradelog_state()
        self._refresh_tradelog_tab()
        messagebox.showinfo("Recorded", f"{action} {qty} units of {ticker} @ Rs {price:.2f}")

    def _on_save_tx_edit(self):
        if not self._editing_tx_id:
            messagebox.showwarning("Nothing selected", "Select a transaction row in the history table first.")
            return
        try:
            qty = int(self.tl_qty_var.get())
            price = float(self.tl_price_var.get())
            trade_date = datetime.date.fromisoformat(self.tl_date_var.get())
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e))
            return
        ticker = self.selected_trade_ticker.get()
        action = self.tl_action_var.get()

        candidate = [tx.copy() for tx in self.tradelog]
        idx = next((i for i, tx in enumerate(candidate) if tx["id"] == self._editing_tx_id), None)
        if idx is None:
            return
        candidate[idx].update({
            "ticker": ticker, "action": action,
            "date": trade_date.isoformat(), "quantity": qty, "price": price,
        })
        ok, err = validate_tradelog_integrity(candidate)
        if not ok:
            messagebox.showerror("Rejected — inconsistent holdings", err)
            return
        save_tradelog(candidate)
        self.tradelog = candidate
        self._recompute_tradelog_state()
        self._refresh_tradelog_tab()
        messagebox.showinfo("Updated", "Transaction updated and positions ledger synced.")

    def _on_delete_tx(self):
        if not self._editing_tx_id:
            messagebox.showwarning("Nothing selected", "Select a transaction row in the history table first.")
            return
        if not messagebox.askyesno("Confirm delete", "Permanently delete the selected transaction?"):
            return
        candidate = [tx for tx in self.tradelog if tx["id"] != self._editing_tx_id]
        ok, err = validate_tradelog_integrity(candidate)
        if not ok:
            messagebox.showerror("Rejected", f"Would cause inconsistent holdings: {err}")
            return
        save_tradelog(candidate)
        self.tradelog = candidate
        self._editing_tx_id = None
        self._recompute_tradelog_state()
        self._refresh_tradelog_tab()

    def _recompute_tradelog_state(self):
        latest_prices = {t: get_etf_latest_price(t, self.prices) for t in self.etf_tickers}
        latest_prices.update(self.live_price_overrides)
        result = calculate_holdings_and_pnl(self.tradelog, latest_prices)
        self.active_holdings = result["active_holdings"]
        self.realized_pnl = result["realized_pnl"]
        self.unrealized_pnl = result["unrealized_pnl"]
        if self.ranking is not None and not self.ranking.empty and self.prices is not None:
            self.holdings_metrics = emr.evaluate_holdings_exit_rules(
                result["holdings_metrics"], self.ranking, self.prices)
        else:
            self.holdings_metrics = [dict(h, **{"Exit Reason": "—"}) for h in result["holdings_metrics"]]
        sync_to_positions_ledger(self.active_holdings)

    def _on_refresh_live_prices(self):
        if not self.holdings_metrics:
            messagebox.showinfo("Nothing to refresh", "No active holdings.")
            return
        if not _YF_AVAILABLE:
            messagebox.showwarning("yfinance unavailable", "yfinance is not installed.")
            return
        self.status_var.set("Fetching live NAVs from Yahoo Finance ...")
        self.progress.start(10)
        threading.Thread(target=self._worker_refresh_prices, daemon=True).start()

    def _worker_refresh_prices(self):
        tickers = [h["Ticker"] for h in self.holdings_metrics]

        def _fetch(tkr):
            try:
                return tkr, yf.Ticker(f"{tkr}.NS").fast_info.last_price
            except Exception:
                return tkr, None

        live = {}
        with ThreadPoolExecutor(max_workers=20) as exe:
            for tkr, px in exe.map(_fetch, tickers):
                if px:
                    live[tkr] = px
        self.msg_queue.put(("live_prices", live))

    def _refresh_tradelog_tab(self):
        self.tl_ticker_combo["values"] = self.etf_tickers
        if not self.selected_trade_ticker.get() and self.etf_tickers:
            self.selected_trade_ticker.set(self.etf_tickers[0])

        total_invested = sum(h["Cost Value"] for h in self.holdings_metrics)
        total_market = sum(h["Market Value"] for h in self.holdings_metrics)
        total_unrl = total_market - total_invested
        total_unrl_pct = (total_unrl / total_invested * 100) if total_invested > 0 else 0.0
        self.metric_vars["Total Invested"].set(fmt_money(total_invested))
        self.metric_vars["Current Market Value"].set(fmt_money(total_market))
        self.metric_vars["Unrealized PnL (MTM)"].set(f"{fmt_money(total_unrl)}  ({fmt_pct(total_unrl_pct)})")
        self.metric_vars["Realized PnL"].set(fmt_money(self.realized_pnl))

        breached = [h for h in self.holdings_metrics if h.get("Exit Reason", "OK") not in ("OK", "—")]
        if breached:
            self.breach_var.set(f"🚨 {len(breached)} holding(s) breaching exit criteria (informational only — "
                                 f"the monthly engine does NOT auto-sell): " +
                                 "; ".join(f"{h['Ticker']}: {h['Exit Reason']}" for h in breached))
        else:
            self.breach_var.set("✅ All active holdings within exit thresholds (informational only).")

        for row in self.holdings_tree.get_children():
            self.holdings_tree.delete(row)
        for h in sorted(self.holdings_metrics, key=lambda x: -x["Unrealized PnL"]):
            breached_row = h.get("Exit Reason", "OK") not in ("OK", "—")
            tag = "breach" if breached_row else ("gain" if h["Unrealized PnL"] > 0 else
                                                  ("loss" if h["Unrealized PnL"] < 0 else ""))
            fbd = h["First Buy Date"]
            fbd_str = fbd.isoformat() if hasattr(fbd, "isoformat") else str(fbd)
            self.holdings_tree.insert("", "end", values=(
                h["Ticker"], f"{h['Qty']:,.0f}", fmt_money(h["Avg Price"]), fmt_money(h["Current Price"]),
                fmt_money(h["Cost Value"]), fmt_money(h["Market Value"]),
                fmt_money(h["Unrealized PnL"]), fmt_pct(h["Unrealized PnL %"]), fbd_str,
                h.get("Exit Reason", "—"),
            ), tags=(tag,) if tag else ())

        for row in self.tx_tree.get_children():
            self.tx_tree.delete(row)
        for tx in reversed(self.tradelog):
            tag = "buy" if tx["action"] == "BUY" else "sell"
            item = self.tx_tree.insert("", "end", values=(
                tx["date"], tx["ticker"], tx["action"], f"{tx['quantity']:,}",
                fmt_money(tx["price"]), fmt_money(tx["quantity"] * tx["price"]), tx["id"],
            ), tags=(tag,))

    # =====================================================
    # PIPELINE EXECUTION (threaded)
    # =====================================================
    def _run_pipeline_async(self, force=False, initial=False):
        self.status_var.set("Loading data & running pipeline ..." if not initial else "Loading ...")
        self.progress.start(10)
        self.log_text.delete("1.0", tk.END)
        threading.Thread(target=self._worker_run_pipeline, args=(force,), daemon=True).start()

    def _worker_run_pipeline(self, force):
        old_stdout = sys.stdout
        sys.stdout = QueueWriter(self.msg_queue)
        try:
            input_path = str(SCRIPT_DIR / emr.CONFIG.INPUT_FILE)
            result = emr.run_pipeline(input_path, force=force)
            self.msg_queue.put(("pipeline_done", result))
        except Exception as e:
            self.msg_queue.put(("pipeline_error", str(e)))
        finally:
            sys.stdout = old_stdout

    def _on_force_rebalance(self):
        if not messagebox.askyesno(
                "Force Re-Flush",
                "This will immediately sell every current holding and buy this "
                "month's fresh Top-N picks, even if you've already rebalanced "
                "this calendar month. Continue?"):
            return
        self._run_pipeline_async(force=True)

    def _open_rankings_excel(self):
        out_path = SCRIPT_DIR / emr.CONFIG.OUTPUT_FILE
        if not out_path.exists():
            messagebox.showinfo("Not found", "Run the pipeline first to generate the Excel output.")
            return
        try:
            os.startfile(str(out_path))
        except Exception as e:
            messagebox.showerror("Error", f"Could not open file: {e}")

    # =====================================================
    # QUEUE POLLING (main-thread UI updates)
    # =====================================================
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log_text.insert(tk.END, payload)
                    self.log_text.see(tk.END)
                elif kind == "pipeline_done":
                    self.progress.stop()
                    self._apply_pipeline_result(payload)
                    self.status_var.set(
                        f"Ready. Last run: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        + ("  (NEW monthly flush)" if payload.get("is_new_month") else "  (existing month, no trades)")
                    )
                elif kind == "pipeline_error":
                    self.progress.stop()
                    self.status_var.set("Error.")
                    messagebox.showerror("Pipeline error", payload)
                elif kind == "live_prices":
                    self.progress.stop()
                    if payload:
                        self.live_price_overrides.update(payload)
                        self._recompute_tradelog_state()
                        self._refresh_tradelog_tab()
                        self.status_var.set(f"Refreshed {len(payload)} live price(s).")
                    else:
                        self.status_var.set("Could not fetch live prices.")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _apply_pipeline_result(self, result):
        self.regime = result["regime"]
        self.ranking = result["ranking"]
        self.allocation = result["allocation"]

        # meta/prices aren't returned by run_pipeline -- reload them once
        # (cheap: already-cached ETF.xlsx read) so the Tradelog tab has a
        # ticker list and latest prices.
        try:
            self.meta, self.prices = emr.load_etf_data(str(SCRIPT_DIR / emr.CONFIG.INPUT_FILE))
            self.etf_tickers = sorted(self.meta["TICKER"].tolist())
        except Exception:
            pass

        self.tradelog = load_tradelog()
        self._recompute_tradelog_state()

        self._update_banner()
        self._refresh_reco_tab()
        self._refresh_rankings_tab()
        self._refresh_tradelog_tab()

        self.reco_status_var.set(
            "This month's picks (already flushed — showing existing allocation)"
            if not result.get("is_new_month") else "NEW monthly flush just executed"
        )


def main():
    root = tk.Tk()
    app = ETFMomentumV2App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
