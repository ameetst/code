"""
Re-run of etf_backtest_blend_methods.py with the LIQUID sector removed from the
investable pool ENTIRELY (dropped before any ranking, so liquid funds also stay out of
the Sharpe/Clenow Z-score reference pools). Everything else (data, rules, costs, regime,
exits) is identical to the original. EVALUATION ONLY.

Usage:  python etf_backtest_blend_methods_noliquid.py
Outputs: blend_methods_noliquid_*.csv / _equity_curve.png (originals are untouched).
"""
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))

import etf_backtest_clenow90_vs_live as m   # noqa: E402

EXCLUDE_SECTORS = {"LIQUID"}
_n_dropped = {}


def _drop_excluded(meta, hist):
    drop = [t for t in hist.columns if m.SECTOR_MAP.get(t, "OTHER") in EXCLUDE_SECTORS]
    _n_dropped["n"] = len(drop)
    if not drop:
        return meta, hist
    return meta[~meta["TICKER"].isin(drop)], hist.drop(columns=drop)


def _wrap(fn):
    def wrapper(meta, hist):
        meta_f, hist_f = _drop_excluded(meta, hist)
        return fn(meta_f, hist_f)
    return wrapper


# must patch BEFORE importing blend_methods (it binds m.ranking_* via cached() at import)
m.ranking_live = _wrap(m.ranking_live)
m.ranking_clenow = _wrap(m.ranking_clenow)

import etf_backtest_blend_methods as bm      # noqa: E402

bm.OUT = "blend_methods_noliquid"

if __name__ == "__main__":
    bm.main()
