"""
milt_gui.py
============
Desktop dashboard (Tkinter, stdlib only) for the MILT strategy. This is a
thin UI wrapper -- it runs milt_strategy.py as a subprocess and reads its
JSON state files to display status; no strategy logic is duplicated here.

Run:
    python milt_gui.py

What it does
------------
- Shows current cash / open-position count / mark-to-market equity / return
  vs starting capital, read from MILT_positions_ledger.json +
  MILT_portfolio_state.json, marked to market against the latest prices in
  MILT_N750_updated.xlsx.
- Open Positions tab: entry date/price, shares, current price, P&L%.
  Double-click the Entry ₹ cell to correct it (e.g. your actual Monday fill
  differed from the theoretical open milt_strategy.py recorded) -- writes
  straight back to MILT_positions_ledger.json.
- Trade History tab: closed trades from MILT_tradelog.json.
- Configuration tab: edit Cash, Max Positions, BB Period, BB Std Dev (Sigma),
  Stop Loss %, ATR Period, ATR Multiplier -- saved to MILT_config.json.
  Every script that reads these (milt_strategy.py itself, the backtest/
  suite) picks up the new values on its next run -- no restart needed,
  since each is a fresh subprocess/import that reloads the config file.
- Buttons:
    Refresh          -- re-read the local files and re-mark positions
                        (no subprocess, no data fetch -- instant)
    Preview (Friday)  -- python milt_strategy.py --update --dry-run
                        (refreshes prices, shows signals, saves nothing)
    Update & Commit (Monday) -- python milt_strategy.py --update
                        (refreshes prices, commits any triggered
                        entries/exits to the ledger -- asks for
                        confirmation first, since it changes saved state)
- A live log pane streams the subprocess's stdout as it runs.

This does not place real broker orders -- see the Friday-preview /
Monday-commit workflow described in milt_strategy.py's own docstring.
"""

import datetime
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import momentum_lib as ml
from milt_strategy import (
    DEFAULT_FILE, LEDGER_FILE, STATE_FILE, TRADELOG_FILE, EQUITY_HISTORY_FILE,
    CONFIG_DEFAULTS, load_milt_config, save_milt_config,
    load_ledger, save_ledger,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable
STRATEGY_SCRIPT = SCRIPT_DIR / "milt_strategy.py"


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


class MiltDashboard:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("MILT Strategy Dashboard")
        root.geometry("1080x720")
        root.minsize(860, 560)

        self.log_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._running = False

        self._setup_styles()
        self._build_ui()
        self.refresh()
        self.root.after(150, self._poll_log_queue)

    # ── styling ("jazzy" buttons) ────────────────────────────────────────────

    def _setup_styles(self):
        # The "vista"/"xpnative" ttk themes on Windows draw native widgets and
        # silently ignore custom button colors -- "clam" is a fully skinnable
        # theme that actually renders them, cross-platform.
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        base_font = ("Segoe UI", 10, "bold")

        def make_button_style(name, bg, active_bg, fg="white"):
            style.configure(
                f"{name}.TButton", background=bg, foreground=fg,
                font=base_font, padding=(14, 9), borderwidth=0, relief="flat",
            )
            style.map(
                f"{name}.TButton",
                background=[("active", active_bg), ("disabled", "#4a4f5c")],
                foreground=[("disabled", "#9aa0ab")],
            )

        make_button_style("Refresh",  "#17a2b8", "#12828f")   # teal
        make_button_style("Preview",  "#0d6efd", "#0b5ed7")   # blue
        make_button_style("Commit",   "#fd7e14", "#dc6803")   # orange
        make_button_style("Save",     "#198754", "#146c43")   # green
        make_button_style("Reset",    "#6c757d", "#565e64")   # gray

        style.configure("TNotebook.Tab", font=("Segoe UI", 9, "bold"), padding=(12, 6))
        style.configure("TLabelframe.Label", font=("Segoe UI", 9, "bold"))

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # -- status bar --------------------------------------------------------
        status_frame = ttk.LabelFrame(self.root, text="Portfolio Status")
        status_frame.pack(fill="x", **pad)

        self.status_vars = {
            k: tk.StringVar(value="—") for k in
            ["cash", "positions", "positions_value", "equity", "return", "asof"]
        }
        grid = ttk.Frame(status_frame)
        grid.pack(fill="x", padx=10, pady=8)
        labels = [
            ("Cash", "cash"), ("Open Positions", "positions"),
            ("Positions (MTM)", "positions_value"), ("Total Equity", "equity"),
            ("Return vs Start", "return"), ("Prices as of", "asof"),
        ]
        for i, (label, key) in enumerate(labels):
            col = i % 3
            row = i // 3
            ttk.Label(grid, text=f"{label}:", font=("Segoe UI", 9, "bold")).grid(
                row=row, column=col * 2, sticky="e", padx=(12 if col else 0, 4), pady=3)
            ttk.Label(grid, textvariable=self.status_vars[key]).grid(
                row=row, column=col * 2 + 1, sticky="w", pady=3)

        # -- buttons -------------------------------------------------------------
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)

        ttk.Button(btn_frame, text="🔄 Refresh", style="Refresh.TButton",
                   command=self.refresh).pack(side="left", padx=4)
        self.preview_btn = ttk.Button(btn_frame, text="👀 Preview (Friday) — dry run",
                                       style="Preview.TButton",
                                       command=lambda: self.run_strategy(dry_run=True))
        self.preview_btn.pack(side="left", padx=4)
        self.commit_btn = ttk.Button(btn_frame, text="🚀 Update & Commit (Monday)",
                                      style="Commit.TButton",
                                      command=lambda: self.run_strategy(dry_run=False))
        self.commit_btn.pack(side="left", padx=4)

        self.run_status_var = tk.StringVar(value="Idle")
        ttk.Label(btn_frame, textvariable=self.run_status_var,
                  foreground="#555").pack(side="right", padx=8)

        # -- notebook: positions / trade history / configuration -----------------
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, **pad)

        pos_tab = ttk.Frame(nb)
        nb.add(pos_tab, text="Open Positions")
        ttk.Label(pos_tab, text="Double-click Entry ₹ to correct the fill price "
                                "(e.g. actual Monday fill differed from the theoretical open).",
                  foreground="#666").pack(anchor="w", padx=6, pady=(6, 0))
        pos_cols = ("ticker", "entry_date", "entry_price", "shares",
                    "current_price", "value", "pnl_pct", "entry_week")
        self.pos_tree = ttk.Treeview(pos_tab, columns=pos_cols, show="headings", height=8)
        headers = {"ticker": "Ticker", "entry_date": "Entry Date", "entry_price": "Entry ₹ (dbl-click)",
                   "shares": "Shares", "current_price": "Current ₹", "value": "Value ₹",
                   "pnl_pct": "P&L %", "entry_week": "Entry Week"}
        for c in pos_cols:
            self.pos_tree.heading(c, text=headers[c])
            self.pos_tree.column(c, width=110, anchor="center")
        self.pos_tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.pos_tree.bind("<Double-1>", self._on_pos_tree_double_click)
        self._edit_entry_widget = None

        trade_tab = ttk.Frame(nb)
        nb.add(trade_tab, text="Trade History")
        tr_cols = ("ticker", "entry_date", "entry_price", "exit_date",
                   "exit_price", "shares", "pnl_pct", "reason")
        self.trade_tree = ttk.Treeview(trade_tab, columns=tr_cols, show="headings", height=8)
        tr_headers = {"ticker": "Ticker", "entry_date": "Entry Date", "entry_price": "Entry ₹",
                      "exit_date": "Exit Date", "exit_price": "Exit ₹", "shares": "Shares",
                      "pnl_pct": "P&L %", "reason": "Exit Reason"}
        for c in tr_cols:
            self.trade_tree.heading(c, text=tr_headers[c])
            self.trade_tree.column(c, width=110, anchor="center")
        self.trade_tree.pack(fill="both", expand=True, padx=4, pady=4)

        cfg_tab = ttk.Frame(nb)
        nb.add(cfg_tab, text="Configuration")
        self._build_config_tab(cfg_tab)

        # -- log pane --------------------------------------------------------------
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=False, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    # ── configuration tab ────────────────────────────────────────────────────

    # (label, config key, is_percent) -- is_percent fields are shown/edited as
    # a plain percentage number (e.g. 20) but stored as a fraction (0.20)
    CONFIG_FIELDS = [
        ("Starting Capital (₹)",   "capital",       False),
        ("Max Positions",          "max_positions", False),
        ("BB Period (weeks)",      "bb_window",      False),
        ("BB Std Dev (Sigma)",     "bb_std",         False),
        ("Stop Loss % (TSL)",      "stop_loss_pct",  True),
        ("ATR Period (weeks)",     "atr_period",     False),
        ("ATR Multiplier",         "atr_mult",       False),
    ]

    def _build_config_tab(self, parent):
        info = ttk.Label(
            parent,
            text="Changes take effect on the next Preview/Commit run or backtest "
                 "-- each is a fresh process that reloads MILT_config.json.",
            foreground="#666", wraplength=680, justify="left",
        )
        info.pack(anchor="w", padx=12, pady=(12, 4))

        form = ttk.Frame(parent)
        form.pack(anchor="w", padx=12, pady=8)

        self.config_vars: dict[str, tk.StringVar] = {}
        for row, (label, key, is_percent) in enumerate(self.CONFIG_FIELDS):
            ttk.Label(form, text=f"{label}:", font=("Segoe UI", 9, "bold")).grid(
                row=row, column=0, sticky="e", padx=(0, 8), pady=5)
            var = tk.StringVar()
            self.config_vars[key] = var
            entry = ttk.Entry(form, textvariable=var, width=16, font=("Segoe UI", 10))
            entry.grid(row=row, column=1, sticky="w", pady=5)

        btn_row = ttk.Frame(parent)
        btn_row.pack(anchor="w", padx=12, pady=(8, 4))
        ttk.Button(btn_row, text="💾 Save Configuration", style="Save.TButton",
                   command=self._save_config).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="↩ Reset to Defaults", style="Reset.TButton",
                   command=self._reset_config).pack(side="left")

        self.config_status_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.config_status_var,
                  foreground="#198754", font=("Segoe UI", 9, "bold")).pack(
            anchor="w", padx=12, pady=(4, 12))

        self._load_config_into_form()

    def _load_config_into_form(self, cfg: dict = None):
        cfg = cfg or load_milt_config()
        for label, key, is_percent in self.CONFIG_FIELDS:
            val = cfg.get(key, CONFIG_DEFAULTS[key])
            if is_percent:
                val = val * 100
            self.config_vars[key].set(str(val))

    def _reset_config(self):
        self._load_config_into_form(dict(CONFIG_DEFAULTS))
        self.config_status_var.set("Form reset to defaults -- click Save to persist.")

    def _save_config(self):
        cfg = {}
        try:
            for label, key, is_percent in self.CONFIG_FIELDS:
                raw = self.config_vars[key].get().strip()
                value = float(raw)
                if is_percent:
                    value = value / 100.0
                if key in ("max_positions", "bb_window", "atr_period"):
                    value = int(value)
                if value <= 0:
                    raise ValueError(f"{label} must be greater than 0 (got {raw!r}).")
                cfg[key] = value
        except ValueError as e:
            messagebox.showerror("Invalid configuration", str(e) if "must be" in str(e)
                                 else f"'{label}' is not a valid number: {raw!r}")
            return

        save_milt_config(cfg)
        self.config_status_var.set(
            f"Saved to MILT_config.json at {datetime.datetime.now().strftime('%H:%M:%S')}.")
        self.refresh()

    # ── editable Entry ₹ cell in the Open Positions tab ─────────────────────────

    def _on_pos_tree_double_click(self, event):
        if self._edit_entry_widget is not None:
            return  # an edit is already in progress
        row_id = self.pos_tree.identify_row(event.y)
        col_id = self.pos_tree.identify_column(event.x)  # e.g. "#3"
        if not row_id or not col_id:
            return
        col_index = int(col_id.replace("#", "")) - 1
        pos_cols = self.pos_tree["columns"]
        if pos_cols[col_index] != "entry_price":
            return  # only the Entry ₹ column is editable

        ticker = self.pos_tree.set(row_id, "ticker")
        current_value = self.pos_tree.set(row_id, "entry_price")
        x, y, width, height = self.pos_tree.bbox(row_id, col_id)

        edit_var = tk.StringVar(value=current_value)
        entry = ttk.Entry(self.pos_tree, textvariable=edit_var, justify="center",
                          font=("Segoe UI", 10))
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()
        entry.select_range(0, tk.END)
        self._edit_entry_widget = entry

        def commit(_event=None):
            new_val = edit_var.get().strip()
            self._close_price_editor()
            self._apply_entry_price_edit(ticker, new_val)

        def cancel(_event=None):
            self._close_price_editor()

        entry.bind("<Return>", commit)
        entry.bind("<KP_Enter>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", commit)

    def _close_price_editor(self):
        if self._edit_entry_widget is not None:
            self._edit_entry_widget.destroy()
            self._edit_entry_widget = None

    def _apply_entry_price_edit(self, ticker: str, new_val: str):
        try:
            new_price = float(new_val)
            if new_price <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid price",
                                 f"'{new_val}' is not a valid positive price for {ticker}.")
            return

        ledger_path = SCRIPT_DIR / LEDGER_FILE
        ledger = load_ledger(str(ledger_path))
        if ticker not in ledger:
            messagebox.showerror("Not found", f"{ticker} is no longer in the open ledger "
                                              "(it may have just been closed) -- not saved.")
            self.refresh()
            return

        pos = ledger[ticker]
        old_price = pos["entry_price"]
        # If no weekly re-evaluation has touched peak_close yet (still equal
        # to the old entry price), rebase it to the corrected entry too --
        # otherwise leave it alone, since it already reflects real
        # subsequent price action that shouldn't be overwritten.
        if pos["peak_close"] == old_price:
            pos["peak_close"] = new_price
        pos["entry_price"] = new_price

        save_ledger(ledger, str(ledger_path))
        self.run_status_var.set(
            f"{ticker} entry price corrected: {old_price:.2f} -> {new_price:.2f}")
        self.refresh()

    # ── data refresh (no subprocess -- just reads local files) ─────────────────

    def refresh(self):
        cfg = load_milt_config()
        capital = cfg["capital"]
        max_positions = cfg["max_positions"]

        ledger = _read_json(SCRIPT_DIR / LEDGER_FILE, {})
        state = _read_json(SCRIPT_DIR / STATE_FILE, {"cash": capital})
        cash = float(state.get("cash", capital))

        prices_df, asof = None, None
        data_file = SCRIPT_DIR / DEFAULT_FILE
        if data_file.exists():
            try:
                prices_df, _, _, dates = ml.load_prices(str(data_file))
                asof = max(dates).isoformat() if dates else None
            except Exception as e:
                self._log(f"Warning: could not load {DEFAULT_FILE} for mark-to-market: {e}\n")

        self.pos_tree.delete(*self.pos_tree.get_children())
        positions_value = 0.0
        for ticker, pos in ledger.items():
            entry_price = float(pos.get("entry_price", 0))
            shares = int(pos.get("shares", 0))
            current_price = entry_price
            if prices_df is not None and ticker in prices_df.index:
                px = prices_df.loc[ticker].dropna()
                if len(px):
                    current_price = float(px.iloc[-1])
            value = shares * current_price
            positions_value += value
            pnl_pct = (current_price / entry_price - 1) * 100 if entry_price else 0.0
            self.pos_tree.insert("", "end", values=(
                ticker, pos.get("entry_date", ""), f"{entry_price:.2f}", shares,
                f"{current_price:.2f}", f"{value:,.0f}", f"{pnl_pct:+.2f}%",
                pos.get("entry_week", ""),
            ))

        equity = cash + positions_value
        ret_pct = (equity / capital - 1) * 100

        self.status_vars["cash"].set(f"₹ {cash:,.0f}")
        self.status_vars["positions"].set(f"{len(ledger)} / {max_positions}")
        self.status_vars["positions_value"].set(f"₹ {positions_value:,.0f}")
        self.status_vars["equity"].set(f"₹ {equity:,.0f}")
        self.status_vars["return"].set(f"{ret_pct:+.2f}%")
        self.status_vars["asof"].set(asof or "no data file yet")

        self.trade_tree.delete(*self.trade_tree.get_children())
        trades = _read_json(SCRIPT_DIR / TRADELOG_FILE, [])
        for t in reversed(trades[-100:]):
            self.trade_tree.insert("", "end", values=(
                t.get("ticker", ""), t.get("entry_date", ""), f"{t.get('entry_price', 0):.2f}",
                t.get("exit_date", ""), f"{t.get('exit_price', 0):.2f}", t.get("shares", ""),
                f"{t.get('pnl_pct', 0):+.2f}%", t.get("reason", ""),
            ))

    # ── run milt_strategy.py as a subprocess, streaming output live ────────────

    def run_strategy(self, dry_run: bool):
        if self._running:
            messagebox.showinfo("Already running", "A run is already in progress.")
            return
        if not dry_run:
            ok = messagebox.askyesno(
                "Confirm commit",
                "This will refresh prices and COMMIT any triggered entries/exits "
                f"to {LEDGER_FILE} / {STATE_FILE}.\n\n"
                "This is still paper-tracking only (no real broker order is placed) "
                "-- but it changes your saved portfolio state. Continue?",
            )
            if not ok:
                return

        self._running = True
        self.preview_btn.config(state="disabled")
        self.commit_btn.config(state="disabled")
        self.log_text.delete("1.0", tk.END)
        threading.Thread(target=self._run_subprocess, args=(dry_run,), daemon=True).start()

    def _run_subprocess(self, dry_run: bool):
        args = [PYTHON, str(STRATEGY_SCRIPT), "--update"]
        if dry_run:
            args.append("--dry-run")
        self.log_queue.put(("status", "Running... (refreshing prices can take a few minutes)"))
        self.log_queue.put(("line", f"$ {' '.join(args)}\n\n"))
        try:
            proc = subprocess.Popen(
                args, cwd=str(SCRIPT_DIR), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            for line in proc.stdout:
                self.log_queue.put(("line", line))
            proc.wait()
            self.log_queue.put(("done", proc.returncode))
        except Exception as e:
            self.log_queue.put(("line", f"\nERROR launching subprocess: {e}\n"))
            self.log_queue.put(("done", -1))

    def _poll_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "line":
                    self.log_text.insert(tk.END, payload)
                    self.log_text.see(tk.END)
                elif kind == "status":
                    self.run_status_var.set(payload)
                elif kind == "done":
                    ok = payload == 0
                    self.run_status_var.set(
                        f"Finished {'OK' if ok else f'(exit code {payload})'} "
                        f"at {datetime.datetime.now().strftime('%H:%M:%S')}")
                    self._running = False
                    self.preview_btn.config(state="normal")
                    self.commit_btn.config(state="normal")
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log_queue)

    def _log(self, text: str):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)


def main():
    root = tk.Tk()
    # Theme selection ("clam", for skinnable buttons) happens inside
    # MiltDashboard._setup_styles().
    MiltDashboard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
