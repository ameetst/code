"""
Dhan integration for the live India VIX quote. Ported from sharpe_dashboard_dhan.py's
own DHAN INTEGRATION block and get_live_vix().

dhan_datahq lives as a sibling checkout under the same Github root:
    Github/code/momentum/Sharpe/webapp/core/dhan_client.py   (this file)
    Github/dhan_datahq/dhandata/                              (shared library)
Falls back gracefully (DHAN_AVAILABLE = False) if dhandata isn't importable or its
token cache isn't set up -- every call site checks this flag and degrades to the
yfinance path rather than failing.
"""
import sys
import time

from webapp.settings import CODE_DIR

DHAN_DIR = CODE_DIR.parent.parent.parent / "dhan_datahq"
if str(DHAN_DIR) not in sys.path:
    sys.path.insert(0, str(DHAN_DIR))

try:
    import dhandata as dh
    DHAN_AVAILABLE = True
except Exception:
    dh = None
    DHAN_AVAILABLE = False

_VIX_TTL = 3600  # matches @st.cache_data(ttl=3600) on the dashboard's get_live_vix
_vix_cache: dict = {}


def get_live_vix() -> tuple[float | None, str | None]:
    """(value, source) -- source is "Dhan" / "Yahoo Finance" / None (both failed).
    Cached in-process for _VIX_TTL seconds, same cadence as the dashboard."""
    now = time.time()
    cached = _vix_cache.get("value")
    if cached and now - cached[2] < _VIX_TTL:
        return cached[0], cached[1]

    if DHAN_AVAILABLE:
        try:
            vix = dh.get_index_ltp("INDIA VIX")
            if vix is not None:
                result = (float(vix), "Dhan")
                _vix_cache["value"] = (*result, now)
                return result
        except Exception:
            pass  # fall through to yfinance below

    try:
        import yfinance as yf
        data = yf.Ticker("^INDIAVIX").history(period="1d")
        if not data.empty:
            result = (float(data["Close"].iloc[-1]), "Yahoo Finance")
            _vix_cache["value"] = (*result, now)
            return result
    except Exception:
        pass

    return None, None
