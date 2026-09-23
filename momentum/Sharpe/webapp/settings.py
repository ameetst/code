"""
Runtime settings for the webapp.

CODE_DIR  – where momentum_lib.py / Sharpe.py live (imported, never modified).
DATA_DIR  – where the xlsx / json / csv data files live. Defaults to CODE_DIR;
            point it at a copy (SHARPE_DATA_DIR) to develop without touching live files.
READ_ONLY – write routes are rejected while True (default). Flip with
            SHARPE_READ_ONLY=0 once a write tab has reached parity with the
            Streamlit dashboard.
"""
import os
import sys
from pathlib import Path

WEBAPP_DIR = Path(__file__).resolve().parent
CODE_DIR = WEBAPP_DIR.parent
DATA_DIR = Path(os.environ.get("SHARPE_DATA_DIR", CODE_DIR)).resolve()
READ_ONLY = os.environ.get("SHARPE_READ_ONLY", "1") != "0"

# Makes `import momentum_lib` resolve to the live library next to the Streamlit dashboards.
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))
