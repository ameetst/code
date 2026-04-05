import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext
import sys
import threading
import os
from pathlib import Path
import queue

# Try to import win32com for background Excel refresh
try:
    import win32com.client
    import pythoncom
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

# Import the main script pipeline
try:
    import etf_momentum_ranking as emr
except ImportError as e:
    print(f"Error importing etf_momentum_ranking.py: {e}")
    emr = None

class PrintLogger:
    """Redirects stdout to a tkinter text widget via a queue for thread safety."""
    def __init__(self, text_widget, update_queue):
        self.text_widget = text_widget
        self.update_queue = update_queue

    def write(self, msg):
        if msg:
            self.update_queue.put(msg)

    def flush(self):
        pass


class ETFMomentumGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ETF Momentum Ranking — Control Panel")
        self.root.geometry("900x700")
        
        # UI Styling options
        style = ttk.Style()
        try:
            # Try to use a native windows style if available
            style.theme_use("vista")
        except tk.TclError:
            pass
        
        style.configure("TButton", font=("Arial", 10), padding=5)
        style.configure("Action.TButton", font=("Arial", 10, "bold"), padding=10)
        
        # Main Layout
        self.top_frame = ttk.Frame(self.root, padding="15")
        self.top_frame.pack(fill=tk.X, side=tk.TOP)
        
        self.bottom_frame = ttk.Frame(self.root, padding="15")
        self.bottom_frame.pack(fill=tk.BOTH, expand=True, side=tk.BOTTOM)
        
        # --- Control Panel ---
        # Header text
        ttk.Label(
            self.top_frame, 
            text="ETF Momentum System Controller", 
            font=("Arial", 16, "bold")
        ).pack(side=tk.TOP, pady=(0, 15))
        
        # Button container
        self.btn_frame = ttk.Frame(self.top_frame)
        self.btn_frame.pack(side=tk.TOP, fill=tk.X)
        self.btn_frame.columnconfigure(0, weight=1)
        self.btn_frame.columnconfigure(1, weight=1)
        self.btn_frame.columnconfigure(2, weight=1)

        # Buttons
        self.btn_refresh = ttk.Button(
            self.btn_frame, 
            text="♻ Refresh Excel Data", 
            command=self.run_refresh_only,
            style="TButton"
        )
        self.btn_refresh.grid(row=0, column=0, padx=5, sticky="ew")

        self.btn_run = ttk.Button(
            self.btn_frame, 
            text="⚙ Run Ranking Pipeline", 
            command=self.run_pipeline_only,
            style="TButton"
        )
        self.btn_run.grid(row=0, column=1, padx=5, sticky="ew")

        self.btn_both = ttk.Button(
            self.btn_frame, 
            text="⚡ Refresh Data & Run Pipeline", 
            command=self.run_both,
            style="Action.TButton"
        )
        self.btn_both.grid(row=0, column=2, padx=5, sticky="ew")

        # Status Label
        self.status_var = tk.StringVar()
        self.status_var.set("Ready.")
        self.status_label = ttk.Label(self.top_frame, textvariable=self.status_var, foreground="blue", font=("Arial", 10, "italic"))
        self.status_label.pack(side=tk.TOP, pady=(10, 0))

        # --- Console Output ---
        ttk.Label(self.bottom_frame, text="Execution Console:", font=("Arial", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))
        
        self.console = scrolledtext.ScrolledText(
            self.bottom_frame, 
            wrap=tk.WORD, 
            bg="#1e1e1e", 
            fg="#cccccc", 
            font=("Consolas", 10)
        )
        self.console.pack(fill=tk.BOTH, expand=True)
        
        # System status check
        if not WIN32_AVAILABLE:
            self.console.insert(tk.END, "[WARNING] pywin32 is not installed. Excel Refresh feature will not work.\n")
            self.btn_refresh.config(state="disabled")
            self.btn_both.config(state="disabled")

        if emr is None:
            self.console.insert(tk.END, "[WARNING] etf_momentum_ranking.py module not found. Run pipeline will fail.\n")
            self.btn_run.config(state="disabled")
            self.btn_both.config(state="disabled")
            
        # Queue for thread-safe text widget updates
        self.msg_queue = queue.Queue()
        
        # Redirect stdout and stderr
        self.logger = PrintLogger(self.console, self.msg_queue)
        sys.stdout = self.logger
        sys.stderr = self.logger

        # Start listening to the queue
        self.root.after(100, self.process_queue)
        
        # Setup paths
        self.script_dir = Path(__file__).resolve().parent
        if emr is not None:
            self.excel_data_file = self.script_dir / emr.CONFIG.INPUT_FILE
        else:
            self.excel_data_file = self.script_dir / "ETF.xlsx"

    def process_queue(self):
        """Pulls messages from the queue and inserts them into the graphical text box."""
        while not self.msg_queue.empty():
            msg = self.msg_queue.get(0)
            self.console.insert(tk.END, msg)
            self.console.see(tk.END) # Auto-scroll
        self.root.after(100, self.process_queue)

    def lock_buttons(self):
        self.btn_refresh.config(state="disabled")
        self.btn_run.config(state="disabled")
        self.btn_both.config(state="disabled")
        self.status_var.set("Working... Please wait.")
        self.status_label.config(foreground="orange")

    def unlock_buttons(self):
        if WIN32_AVAILABLE:
            self.btn_refresh.config(state="normal")
            self.btn_both.config(state="normal")
        if emr is not None:
            self.btn_run.config(state="normal")
        self.status_var.set("Ready.")
        self.status_label.config(foreground="green")

    # --- Worker Functions ---
    def _refresh_excel_worker(self):
        # Must initialize COM in a new background thread!
        if WIN32_AVAILABLE:
            pythoncom.CoInitialize()
            
        print(f"\n[refresh] Attempting to background refresh: {self.excel_data_file.name} ...")
        abs_path = str(self.excel_data_file)
        
        if not os.path.exists(abs_path):
            print(f"[error] Cannot find {abs_path}")
            if WIN32_AVAILABLE:
                pythoncom.CoUninitialize()
            return False

        try:
            xl = win32com.client.DispatchEx("Excel.Application")
            xl.DisplayAlerts = False
            xl.Visible = False
            
            print("          Opening workbook (silently)...")
            wb = xl.Workbooks.Open(abs_path)
            
            print("          Executing RefreshAll()...")
            wb.RefreshAll()
            
            # Wait for any background queries to finish
            print("          Waiting for queries to complete...")
            xl.CalculateUntilAsyncQueriesDone()
            
            print("          Saving and closing workbook...")
            wb.Save()
            wb.Close()
            xl.Quit()
            
            print("[success] Excel data fully refreshed!\n")
            return True
            
        except Exception as e:
            import traceback
            print(f"\n[error] Excel refresh failed: {e}")
            print(traceback.format_exc())
            try:
                # Attempt to quit Excel if it's trapped
                xl.Quit()
            except:
                pass
            return False
        finally:
            if WIN32_AVAILABLE:
                pythoncom.CoUninitialize()

    def _run_pipeline_worker(self):
        print("\n" + "="*80)
        print("STARTING ETF MOMENTUM PIPELINE")
        print("="*80)
        try:
            emr.run_pipeline()
            print("\n[success] Pipeline execution completed!")
        except Exception as e:
            import traceback
            print(f"\n[error] Pipeline failed: {e}")
            print(traceback.format_exc())

    def _threaded_refresh(self):
        self._refresh_excel_worker()
        self.root.after(0, self.unlock_buttons)

    def _threaded_run(self):
        self._run_pipeline_worker()
        self.root.after(0, self.unlock_buttons)

    def _threaded_both(self):
        success = self._refresh_excel_worker()
        if success:
            self._run_pipeline_worker()
        else:
            print("\n[abort] Skipping pipeline run due to Excel refresh failure.")
        self.root.after(0, self.unlock_buttons)

    # --- Button Callbacks ---
    def run_refresh_only(self):
        self.lock_buttons()
        self.console.insert(tk.END, "\n--- Starting Data Refresh ---\n")
        threading.Thread(target=self._threaded_refresh, daemon=True).start()

    def run_pipeline_only(self):
        self.lock_buttons()
        self.console.insert(tk.END, "\n--- Starting Ranking Pipeline ---\n")
        threading.Thread(target=self._threaded_run, daemon=True).start()

    def run_both(self):
        self.lock_buttons()
        self.console.insert(tk.END, "\n--- Starting Full Operation (Refresh -> Run) ---\n")
        threading.Thread(target=self._threaded_both, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = ETFMomentumGUI(root)
    # Redirect print outputs on application close to standard output
    def on_closing():
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
