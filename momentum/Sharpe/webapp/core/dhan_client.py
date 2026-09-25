"""
Dhan integration for live quotes: the India VIX index and per-ticker LTP for
held positions. Ported from sharpe_dashboard_dhan.py's own DHAN INTEGRATION
block, get_live_vix(), and the Tradelog tab's "Refresh Live Market Prices"
button handler.

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


# ── Live LTP for held positions ("Refresh Live Market Prices" button) ────────
# No TTL here (unlike VIX) -- the dashboard's version only ever refreshes on an
# explicit button click, never on a timer, so this cache holds whatever was last
# fetched until the button is clicked again. Global rather than per-session:
# this is a single-user local app, same simplification already used for the
# VIX and Yahoo cap-tier caches.
_live_price_cache: dict = {}


def refresh_live_prices(tickers: list[str]) -> dict:
    """Fetches live LTP for `tickers`: Dhan's batched /marketfeed/ltp first,
    per-ticker yfinance thread pool fallback. Returns
    {"prices": {ticker: float}, "source": "Dhan"|"Yahoo Finance"|None, "unmatched": [str]}.
    Caches the result (if any prices came back) for get_cached_live_prices()."""
    prices, source, unmatched = {}, None, []

    if DHAN_AVAILABLE:
        try:
            prices, unmatched = dh.resolve_and_get_ltp(tickers)
            if prices:
                source = "Dhan"
        except Exception:
            prices = {}

    if not prices:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor

        def fetch_price(tkr):
            try:
                return tkr, yf.Ticker(f"{tkr}.NS").fast_info.last_price
            except Exception:
                try:
                    return tkr, yf.Ticker(f"{tkr}.BO").fast_info.last_price
                except Exception:
                    return tkr, None

        with ThreadPoolExecutor(max_workers=20) as exe:
            for tkr, price in exe.map(fetch_price, tickers):
                if price:
                    prices[tkr] = price
        if prices:
            source = "Yahoo Finance"

    result = {"prices": prices, "source": source, "unmatched": unmatched}
    if prices:
        _live_price_cache["value"] = result
    return result


def get_cached_live_prices() -> dict | None:
    return _live_price_cache.get("value")


def apply_cached_prices(prices: dict) -> dict:
    """Overrides `prices` with whatever refresh_live_prices() last cached, for any
    ticker present in both -- the same global-override semantics the dashboard got
    from st.session_state.live_prices (set once by Tradelog's "Refresh Live Market
    Prices" button, visible everywhere that reads latest_prices, including the
    Top-N Rankings LTP column). `prices` itself is left untouched; a new dict is
    returned."""
    merged = dict(prices)
    cached = get_cached_live_prices()
    if cached:
        for ticker, price in cached["prices"].items():
            if ticker in merged:
                merged[ticker] = price
    return merged
