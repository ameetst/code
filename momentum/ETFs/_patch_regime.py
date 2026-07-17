"""
_patch_regime.py
================
Applies the Nifty 500 live-index regime changes to etf_momentum_ranking.py.
Run once from the ETFs directory:  python _patch_regime.py
"""
from pathlib import Path

TARGET = Path("etf_momentum_ranking.py")
src = TARGET.read_text(encoding="utf-8")

# ── Patch 1: add datetime + yfinance imports ──────────────────────────────────
OLD_IMPORTS = (
    "from __future__ import annotations\r\n"
    "import sys\r\n"
    "import json\r\n"
    "import numpy as np\r\n"
)
NEW_IMPORTS = (
    "from __future__ import annotations\r\n"
    "import sys\r\n"
    "import json\r\n"
    "import datetime\r\n"
    "import numpy as np\r\n"
)
assert OLD_IMPORTS in src, "PATCH 1 FAILED: import anchor not found"
src = src.replace(OLD_IMPORTS, NEW_IMPORTS, 1)

YF_BLOCK = (
    "from openpyxl.utils import get_column_letter\r\n"
    "\r\n"
    "\r\n"
    "# =========================================================\r\n"
    "# CONFIG"
)
YF_BLOCK_NEW = (
    "from openpyxl.utils import get_column_letter\r\n"
    "\r\n"
    "try:\r\n"
    "    import yfinance as yf\r\n"
    "    _YF_AVAILABLE = True\r\n"
    "except ImportError:\r\n"
    "    _YF_AVAILABLE = False\r\n"
    "\r\n"
    "\r\n"
    "# =========================================================\r\n"
    "# CONFIG"
)
assert YF_BLOCK in src, "PATCH 1b FAILED: openpyxl anchor not found"
src = src.replace(YF_BLOCK, YF_BLOCK_NEW, 1)

# ── Patch 2: add REGIME_INDEX_TICKER to _apply_json_config _KEYS ─────────────
OLD_KEYS = (
    '        "SHARPE_W6M", "SHARPE_W3M",\r\n'
    '        "REGIME_TICKER", "REGIME_FALLBACKS",\r\n'
)
NEW_KEYS = (
    '        "SHARPE_W6M", "SHARPE_W3M",\r\n'
    '        "REGIME_INDEX_TICKER",\r\n'
    '        "REGIME_TICKER", "REGIME_FALLBACKS",\r\n'
)
assert OLD_KEYS in src, "PATCH 2 FAILED: _KEYS anchor not found"
src = src.replace(OLD_KEYS, NEW_KEYS, 1)

# ── Patch 3: add REGIME_INDEX_TICKER to get_config_as_dict ───────────────────
OLD_GET = (
    '        "SHARPE_W3M": CONFIG.SHARPE_W3M,\r\n'
    '        "REGIME_TICKER": CONFIG.REGIME_TICKER,\r\n'
)
NEW_GET = (
    '        "SHARPE_W3M": CONFIG.SHARPE_W3M,\r\n'
    '        "REGIME_INDEX_TICKER": CONFIG.REGIME_INDEX_TICKER,\r\n'
    '        "REGIME_TICKER": CONFIG.REGIME_TICKER,\r\n'
)
assert OLD_GET in src, "PATCH 3 FAILED: get_config_as_dict anchor not found"
src = src.replace(OLD_GET, NEW_GET, 1)

# ── Patch 4: add REGIME_INDEX_TICKER attribute to CONFIG class ────────────────
OLD_CFG = (
    "    # Regime filter\r\n"
    "    # Nifty 500 used (not Nifty 50) \u2014 broader coverage matches full ETF universe\r\n"
    "    # (large + mid + small cap); mid/small roll over before large caps in India\r\n"
    "    REGIME_TICKER      = \"MONIFTY500\"\r\n"
    "    REGIME_FALLBACKS   = [\"BSE500IETF\", \"HDFCBSE500\", \"NIFTYBEES\"]\r\n"
    "    TREND_FAST_EMA_WINDOW = 50     # Layer 1: fast EMA\r\n"
    "    TREND_EMA_WINDOW   = 100       # Layer 1: slow EMA\r\n"
)
NEW_CFG = (
    "    # Regime filter\r\n"
    "    # Primary: Nifty 500 index fetched live from Yahoo Finance (^CRSLDX)\r\n"
    "    # Fallback: REGIME_TICKER column in ETF.xlsx price data\r\n"
    "    REGIME_INDEX_TICKER   = \"^CRSLDX\"        # Yahoo Finance symbol for Nifty 500\r\n"
    "    REGIME_TICKER         = \"MONIFTY500\"     # ETF proxy fallback (must be in ETF.xlsx)\r\n"
    "    REGIME_FALLBACKS      = [\"BSE500IETF\", \"HDFCBSE500\", \"NIFTYBEES\"]\r\n"
    "    TREND_FAST_EMA_WINDOW = 50               # fast EMA window (days)\r\n"
    "    TREND_EMA_WINDOW      = 100              # slow EMA window (days)\r\n"
)
assert OLD_CFG in src, "PATCH 4 FAILED: CONFIG class anchor not found"
src = src.replace(OLD_CFG, NEW_CFG, 1)

# ── Patch 5: replace old regime_status with new fetch + helper + regime ───────
OLD_REGIME = (
    "# =========================================================\r\n"
    "# 3. REGIME FILTER\r\n"
    "# =========================================================\r\n"
    "def regime_status(prices: pd.DataFrame) -> dict:\r\n"
    "    \"\"\"\r\n"
    "    Returns tiered regime state:\r\n"
    "      BULL    - both layers pass  -> invest TOP_N slots\r\n"
    "      PARTIAL - one layer fails   -> invest TOP_N_PARTIAL slots, rest = cash\r\n"
    "      BEAR    - both layers fail  -> full cash\r\n"
    "    \"\"\"\r\n"
    "    # Trend\r\n"
    "    trend_ticker = next(\r\n"
    "        (t for t in [CONFIG.REGIME_TICKER] + CONFIG.REGIME_FALLBACKS\r\n"
    "         if t in prices.columns), None\r\n"
    "    )\r\n"
    "\r\n"
    "    if trend_ticker is None:\r\n"
    "        print(\"  [warn] No Nifty 500 proxy found; trend layer defaulting to PASS\")\r\n"
    "        trend_ok = True\r\n"
    "        nifty_price = np.nan\r\n"
    "        nifty_ema_50 = np.nan\r\n"
    "        nifty_ema_100 = np.nan\r\n"
    "        label = \"BULL\"\r\n"
    "        active_slots = CONFIG.TOP_N\r\n"
    "    else:\r\n"
    "        s = prices[trend_ticker].dropna()\r\n"
    "        if len(s) >= CONFIG.TREND_EMA_WINDOW:\r\n"
    "            nifty_price = float(s.iloc[-1])\r\n"
    "            nifty_ema_50  = float(s.ewm(span=CONFIG.TREND_FAST_EMA_WINDOW, adjust=False).mean().iloc[-1])\r\n"
    "            nifty_ema_100 = float(s.ewm(span=CONFIG.TREND_EMA_WINDOW, adjust=False).mean().iloc[-1])\r\n"
    "            \r\n"
    "            # Regime (Run 1 - best performing config):\r\n"
    "            #   BULL    : EMA50 > EMA100  AND  Price > EMA50  -> TOP_N slots\r\n"
    "            #   PARTIAL : Price > EMA100  (but not BULL)       -> TOP_N_PARTIAL slots\r\n"
    "            #   BEAR    : Price <= EMA100                       -> 0 slots, full cash\r\n"
    "            if nifty_ema_50 > nifty_ema_100 and nifty_price > nifty_ema_50:\r\n"
    "                label        = \"BULL\"\r\n"
    "                active_slots = CONFIG.TOP_N\r\n"
    "            elif nifty_price > nifty_ema_100:\r\n"
    "                label        = \"PARTIAL\"\r\n"
    "                active_slots = CONFIG.TOP_N_PARTIAL\r\n"
    "            else:\r\n"
    "                label        = \"BEAR\"\r\n"
    "                active_slots = 0\r\n"
    "            \r\n"
    "            trend_ok = active_slots > 0\r\n"
    "        else:\r\n"
    "            trend_ok    = True\r\n"
    "            nifty_price = float(s.iloc[-1]) if len(s) else np.nan\r\n"
    "            nifty_ema_50 = np.nan\r\n"
    "            nifty_ema_100 = np.nan\r\n"
    "            label = \"BULL\"\r\n"
    "            active_slots = CONFIG.TOP_N\r\n"
    "\r\n"
    "    return {\r\n"
    "        \"regime_ok\"   : active_slots == CONFIG.TOP_N,\r\n"
    "        \"label\"       : label,\r\n"
    "        \"active_slots\": active_slots,\r\n"
    "        \"trend_ok\"    : trend_ok,\r\n"
    "        \"nifty_price\" : nifty_price,\r\n"
    "        \"nifty_ema_50\": nifty_ema_50,\r\n"
    "        \"nifty_ema_100\": nifty_ema_100,\r\n"
    "        \"trend_ticker\": trend_ticker or \"N/A\",\r\n"
    "    }\r\n"
)
NEW_REGIME = (
    "# =========================================================\r\n"
    "# 3. REGIME FILTER  (Nifty 500 index via yfinance, with ETF fallback)\r\n"
    "# =========================================================\r\n"
    "_NIFTY500_CACHE_FILE = _SCRIPT_DIR / \"nifty500_cache.csv\"\r\n"
    "\r\n"
    "\r\n"
    "def fetch_nifty500_index(n_days: int = 600):\r\n"
    "    \"\"\"\r\n"
    "    Fetch the Nifty 500 index (CONFIG.REGIME_INDEX_TICKER, default ^CRSLDX)\r\n"
    "    from Yahoo Finance. Results are cached to nifty500_cache.csv and reused\r\n"
    "    for the rest of the calendar day (or up to 3 days on weekends/holidays).\r\n"
    "    Returns a pd.Series of Close prices indexed by date, or None on failure.\r\n"
    "    \"\"\"\r\n"
    "    if not _YF_AVAILABLE:\r\n"
    "        print(\"  [warn] yfinance not installed; skipping live Nifty 500 fetch\")\r\n"
    "        return None\r\n"
    "\r\n"
    "    ticker    = CONFIG.REGIME_INDEX_TICKER\r\n"
    "    today_str = datetime.date.today().isoformat()\r\n"
    "\r\n"
    "    # ── Try cache first ──────────────────────────────────────\r\n"
    "    if _NIFTY500_CACHE_FILE.exists():\r\n"
    "        try:\r\n"
    "            cached = pd.read_csv(_NIFTY500_CACHE_FILE, index_col=0, parse_dates=True)\r\n"
    "            if not cached.empty:\r\n"
    "                cache_date = str(cached.index[-1].date())\r\n"
    "                age_days   = (datetime.date.today() - cached.index[-1].date()).days\r\n"
    "                if cache_date == today_str or age_days <= 3:\r\n"
    "                    s = cached.iloc[:, 0].dropna()\r\n"
    "                    s.name = ticker\r\n"
    "                    print(f\"  [regime] Using cached Nifty 500 ({len(s)} rows, \"\r\n"
    "                          f\"last: {cache_date})\")\r\n"
    "                    return s\r\n"
    "        except Exception as e:\r\n"
    "            print(f\"  [warn] Cache read failed ({e}); will re-fetch\")\r\n"
    "\r\n"
    "    # ── Fetch from Yahoo Finance ─────────────────────────────\r\n"
    "    try:\r\n"
    "        print(f\"  [regime] Fetching Nifty 500 ({ticker}) from Yahoo Finance...\")\r\n"
    "        raw = yf.download(ticker, period=f\"{n_days}d\", progress=False, auto_adjust=True)\r\n"
    "        if raw.empty:\r\n"
    "            print(f\"  [warn] yfinance returned empty data for {ticker}\")\r\n"
    "            return None\r\n"
    "        close = (raw[\"Close\"].iloc[:, 0]\r\n"
    "                 if isinstance(raw.columns, pd.MultiIndex)\r\n"
    "                 else raw[\"Close\"])\r\n"
    "        close = close.dropna()\r\n"
    "        close.name  = ticker\r\n"
    "        close.index = pd.to_datetime(close.index).tz_localize(None)\r\n"
    "        try:\r\n"
    "            close.to_frame(name=\"Close\").to_csv(_NIFTY500_CACHE_FILE)\r\n"
    "            print(f\"  [regime] Cached {len(close)} rows → {_NIFTY500_CACHE_FILE.name}\")\r\n"
    "        except Exception as e:\r\n"
    "            print(f\"  [warn] Could not write cache ({e})\")\r\n"
    "        return close\r\n"
    "    except Exception as e:\r\n"
    "        print(f\"  [warn] yfinance fetch failed for {ticker}: {e}\")\r\n"
    "        return None\r\n"
    "\r\n"
    "\r\n"
    "def _compute_regime_from_series(s: pd.Series, trend_ticker: str) -> dict:\r\n"
    "    \"\"\"Shared regime computation from a price series + display label.\"\"\"\r\n"
    "    if len(s) >= CONFIG.TREND_EMA_WINDOW:\r\n"
    "        nifty_price   = float(s.iloc[-1])\r\n"
    "        nifty_ema_50  = float(s.ewm(span=CONFIG.TREND_FAST_EMA_WINDOW, adjust=False).mean().iloc[-1])\r\n"
    "        nifty_ema_100 = float(s.ewm(span=CONFIG.TREND_EMA_WINDOW,      adjust=False).mean().iloc[-1])\r\n"
    "        if nifty_ema_50 > nifty_ema_100 and nifty_price > nifty_ema_50:\r\n"
    "            label = \"BULL\";  active_slots = CONFIG.TOP_N\r\n"
    "        elif nifty_price > nifty_ema_100:\r\n"
    "            label = \"PARTIAL\"; active_slots = CONFIG.TOP_N_PARTIAL\r\n"
    "        else:\r\n"
    "            label = \"BEAR\";  active_slots = 0\r\n"
    "    else:\r\n"
    "        nifty_price = float(s.iloc[-1]) if len(s) else np.nan\r\n"
    "        nifty_ema_50 = nifty_ema_100 = np.nan\r\n"
    "        label = \"BULL\"; active_slots = CONFIG.TOP_N\r\n"
    "    return {\r\n"
    "        \"regime_ok\"    : active_slots == CONFIG.TOP_N,\r\n"
    "        \"label\"        : label,\r\n"
    "        \"active_slots\" : active_slots,\r\n"
    "        \"trend_ok\"     : active_slots > 0,\r\n"
    "        \"nifty_price\"  : nifty_price,\r\n"
    "        \"nifty_ema_50\" : nifty_ema_50,\r\n"
    "        \"nifty_ema_100\": nifty_ema_100,\r\n"
    "        \"trend_ticker\" : trend_ticker,\r\n"
    "    }\r\n"
    "\r\n"
    "\r\n"
    "def regime_status(prices: pd.DataFrame) -> dict:\r\n"
    "    \"\"\"\r\n"
    "    Returns tiered regime state driven by the Nifty 500 index.\r\n"
    "\r\n"
    "    Priority order:\r\n"
    "      1. Live Nifty 500 index  (^CRSLDX via yfinance, daily CSV cache)\r\n"
    "      2. REGIME_TICKER column in ETF.xlsx (MONIFTY500 ETF proxy)\r\n"
    "      3. REGIME_FALLBACKS list  (other broad-market ETFs in ETF.xlsx)\r\n"
    "      4. Default BULL if nothing is available\r\n"
    "\r\n"
    "    States:\r\n"
    "      BULL    - EMA50 > EMA100 AND Price > EMA50  -> TOP_N slots\r\n"
    "      PARTIAL - Price > EMA100 (but not BULL)     -> TOP_N_PARTIAL slots\r\n"
    "      BEAR    - Price <= EMA100                   -> 0 slots (full cash)\r\n"
    "    \"\"\"\r\n"
    "    # 1. Try live Nifty 500 index\r\n"
    "    n_cal_days   = max(600, int(len(prices.index) * 1.4))\r\n"
    "    index_series = fetch_nifty500_index(n_days=n_cal_days)\r\n"
    "    if index_series is not None and len(index_series) >= CONFIG.TREND_EMA_WINDOW:\r\n"
    "        lbl = f\"NIFTY500_IDX ({CONFIG.REGIME_INDEX_TICKER})\"\r\n"
    "        print(f\"  [regime] Using live Nifty 500 index ({len(index_series)} rows)\")\r\n"
    "        return _compute_regime_from_series(index_series, lbl)\r\n"
    "\r\n"
    "    # 2. Fall back to ETF proxy in ETF.xlsx\r\n"
    "    print(\"  [warn] Live index unavailable; falling back to ETF proxy in ETF.xlsx\")\r\n"
    "    etf_ticker = next(\r\n"
    "        (t for t in [CONFIG.REGIME_TICKER] + CONFIG.REGIME_FALLBACKS\r\n"
    "         if t in prices.columns), None\r\n"
    "    )\r\n"
    "    if etf_ticker is not None:\r\n"
    "        s = prices[etf_ticker].dropna()\r\n"
    "        print(f\"  [regime] Using ETF proxy: {etf_ticker} ({len(s)} rows)\")\r\n"
    "        return _compute_regime_from_series(s, etf_ticker)\r\n"
    "\r\n"
    "    # 3. Nothing available — default BULL\r\n"
    "    print(\"  [warn] No regime data source; defaulting to BULL\")\r\n"
    "    return {\r\n"
    "        \"regime_ok\"    : True,\r\n"
    "        \"label\"        : \"BULL\",\r\n"
    "        \"active_slots\" : CONFIG.TOP_N,\r\n"
    "        \"trend_ok\"     : True,\r\n"
    "        \"nifty_price\"  : np.nan,\r\n"
    "        \"nifty_ema_50\" : np.nan,\r\n"
    "        \"nifty_ema_100\": np.nan,\r\n"
    "        \"trend_ticker\" : \"N/A (default)\",\r\n"
    "    }\r\n"
)
assert OLD_REGIME in src, "PATCH 5 FAILED: old regime_status anchor not found"
src = src.replace(OLD_REGIME, NEW_REGIME, 1)

# ── Write result ──────────────────────────────────────────────────────────────
TARGET.write_text(src, encoding="utf-8")
print("All patches applied successfully.")

# Quick syntax check
import ast
ast.parse(src)
print("Syntax OK")
