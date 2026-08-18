"""
Compare TODAY's live ranking (etf_momentum_ranking.py's CURRENT composite:
Z(Sharpe_6M)*0.5 + Z(Sharpe_3M)*0.5) against the two backtest-winning
composites from v9 (etf_backtest_v9_multiwindow_sharpe.py):
  4W-STRICT : mean of Z(Sharpe_12M/9M/6M/3M), ETF must have ALL FOUR valid
  3W-STRICT : mean of Z(Sharpe_12M/6M/3M),    ETF must have ALL THREE valid

EVALUATION ONLY -- imports etf_momentum_ranking.py read-only (its own
build_ranking(), classify_sector(), sharpe_score(), CONFIG are reused
unmodified) and does not write etf_rankings.xlsx, holdings_log.json, or any
other live state file. Uses today's ETF.xlsx snapshot, same file the live
pipeline just ran against.

Usage:
  python compare_live_rankings_4w3w_strict.py
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

_BASE = Path(__file__).resolve().parent
_ETFS_DIR = _BASE.parent
sys.path.insert(0, str(_ETFS_DIR))

import etf_momentum_ranking as live  # noqa: E402  (read-only import)

WINDOWS_4W = {"12M": 252, "9M": 189, "6M": 126, "3M": 63}
WINDOWS_3W = {"12M": 252, "6M": 126, "3M": 63}


def _zscore(series: pd.Series) -> pd.Series:
    mu = series.mean()
    sig = series.std()
    if sig == 0 or np.isnan(sig):
        return pd.Series(0.0, index=series.index)
    return (series - mu) / sig


def build_strict_ranking(meta: pd.DataFrame, prices: pd.DataFrame, windows: dict) -> pd.DataFrame:
    """Same screen (52wk-high) and same live CONFIG as etf_momentum_ranking.py,
    but composite = equal-weight mean of Z-scored Sharpe across `windows`,
    ONLY if every window is valid for that ETF (STRICT missing-window policy,
    per user's chosen backtest winner)."""
    records = []
    for _, row in meta.iterrows():
        ticker = row["TICKER"]
        if ticker not in prices.columns:
            continue
        s = prices[ticker]
        close = float(s.iloc[-1]) if len(s) > 0 else np.nan
        high_52w = float(s.tail(252).max()) if len(s) > 0 else np.nan

        sharpes = {label: live.sharpe_score(s, days) for label, days in windows.items()}

        if pd.notna(close) and pd.notna(high_52w) and high_52w > 0:
            pct_from_high = (high_52w - close) / high_52w
            high_pass = pct_from_high <= live.CONFIG.MAX_DRAWDOWN_FROM_HIGH
        else:
            pct_from_high = np.nan
            high_pass = True

        rec = {
            "TICKER": ticker,
            "ETF_NAME": row["ETF_NAME"],
            "SECTOR": live.classify_sector(row["ETF_NAME"], ticker),
            "CLOSE": close,
            "PCT_FROM_HIGH": pct_from_high * 100 if pd.notna(pct_from_high) else np.nan,
            "SCREEN_PASS": high_pass,
        }
        for label, val in sharpes.items():
            rec[f"SHARPE_{label}"] = val
        records.append(rec)

    df = pd.DataFrame(records)
    inv_mask = df["SCREEN_PASS"]
    labels = list(windows.keys())

    z_cols = {}
    for label in labels:
        z = pd.Series(np.nan, index=df.index)
        if inv_mask.sum() > 0:
            z.loc[inv_mask] = _zscore(df.loc[inv_mask, f"SHARPE_{label}"])
        z_cols[label] = z
    zdf = pd.DataFrame(z_cols)

    valid_all = zdf.notna().all(axis=1)
    composite = zdf.mean(axis=1, skipna=True)
    composite[~valid_all] = np.nan

    df["COMPOSITE"] = np.nan
    df.loc[inv_mask, "COMPOSITE"] = composite[inv_mask]

    inv = df[df["SCREEN_PASS"]].copy()
    if len(inv) > 0:
        inv["RANK_INVESTABLE"] = inv["COMPOSITE"].rank(ascending=False, na_option="bottom").astype(int)
        df = df.merge(inv[["TICKER", "RANK_INVESTABLE"]], on="TICKER", how="left")
    else:
        df["RANK_INVESTABLE"] = np.nan

    df["_sort"] = df["RANK_INVESTABLE"].fillna(9999)
    df = df.sort_values(["_sort", "COMPOSITE"], ascending=[True, False]).drop(columns="_sort").reset_index(drop=True)
    return df


def main():
    meta, prices = live.load_etf_data(str(_ETFS_DIR / live.CONFIG.INPUT_FILE))
    regime = live.regime_status(prices, script_dir=_ETFS_DIR)

    print(f"\nRegime today: {regime['label']}  (active_slots={regime['active_slots']}, "
          f"trend source={regime['trend_ticker']})")

    current_df = live.build_ranking(meta, prices)
    df_4w = build_strict_ranking(meta, prices, WINDOWS_4W)
    df_3w = build_strict_ranking(meta, prices, WINDOWS_3W)

    def top_n(df, n=10):
        inv = df[df["SCREEN_PASS"] & df["RANK_INVESTABLE"].notna()].sort_values("RANK_INVESTABLE")
        return inv.head(n)[["RANK_INVESTABLE", "TICKER", "ETF_NAME", "SECTOR"]]

    print(f"\n{'='*100}\n  TOP 10 -- CURRENT (Sharpe 6M/3M)\n{'='*100}")
    print(top_n(current_df).to_string(index=False))

    print(f"\n{'='*100}\n  TOP 10 -- 4W-STRICT (Sharpe 12M/9M/6M/3M, all required)\n{'='*100}")
    print(top_n(df_4w).to_string(index=False))

    print(f"\n{'='*100}\n  TOP 10 -- 3W-STRICT (Sharpe 12M/6M/3M, all required)\n{'='*100}")
    print(top_n(df_3w).to_string(index=False))

    # ---- side-by-side rank table for the union of each scheme's top 10 ----
    rank_cur = current_df.set_index("TICKER")["RANK_INVESTABLE"]
    rank_4w = df_4w.set_index("TICKER")["RANK_INVESTABLE"]
    rank_3w = df_3w.set_index("TICKER")["RANK_INVESTABLE"]
    name_lookup = current_df.set_index("TICKER")["ETF_NAME"]
    sector_lookup = current_df.set_index("TICKER")["SECTOR"]

    union_tickers = sorted(
        set(top_n(current_df, 10)["TICKER"]) | set(top_n(df_4w, 10)["TICKER"]) | set(top_n(df_3w, 10)["TICKER"]),
        key=lambda t: rank_cur.get(t, 9999)
    )
    print(f"\n{'='*100}\n  SIDE-BY-SIDE: union of each scheme's Top 10 (sorted by CURRENT rank)\n{'='*100}")
    print(f"  {'TICKER':<14}{'SECTOR':<16}{'RANK_CURRENT':>14}{'RANK_4W_STRICT':>16}{'RANK_3W_STRICT':>16}")
    for t in union_tickers:
        print(f"  {t:<14}{sector_lookup.get(t,'?'):<16}"
              f"{int(rank_cur.get(t, -1)) if pd.notna(rank_cur.get(t)) else '-':>14}"
              f"{int(rank_4w.get(t, -1)) if pd.notna(rank_4w.get(t)) else '-':>16}"
              f"{int(rank_3w.get(t, -1)) if pd.notna(rank_3w.get(t)) else '-':>16}")

    # ---- current live holdings vs new-formula ranks ----
    log = live.load_holdings_log(_ETFS_DIR)
    if log:
        latest_week = sorted(log.keys())[-1]
        held = log[latest_week]["allocation"]
        print(f"\n{'='*100}\n  CURRENT LIVE HOLDINGS ({latest_week}) -- rank under each formula\n{'='*100}")
        print(f"  {'TICKER':<14}{'SECTOR':<16}{'RANK_CURRENT':>14}{'RANK_4W_STRICT':>16}{'RANK_3W_STRICT':>16}  STATUS")
        for h in held:
            t = h["ticker"]
            rc = rank_cur.get(t, np.nan)
            r4 = rank_4w.get(t, np.nan)
            r3 = rank_3w.get(t, np.nan)
            status_4w = "EXIT (rank>20)" if pd.notna(r4) and r4 > live.CONFIG.EXIT_MAX_RANK else "hold-ok"
            status_3w = "EXIT (rank>20)" if pd.notna(r3) and r3 > live.CONFIG.EXIT_MAX_RANK else "hold-ok"
            print(f"  {t:<14}{h.get('sector','?'):<16}"
                  f"{int(rc) if pd.notna(rc) else '-':>14}"
                  f"{int(r4) if pd.notna(r4) else '-':>16}"
                  f"{int(r3) if pd.notna(r3) else '-':>16}  4W:{status_4w} | 3W:{status_3w}")

    # save full rankings for reference
    current_df.to_csv(_BASE / "live_compare_CURRENT_ranking.csv", index=False)
    df_4w.to_csv(_BASE / "live_compare_4W_STRICT_ranking.csv", index=False)
    df_3w.to_csv(_BASE / "live_compare_3W_STRICT_ranking.csv", index=False)
    print(f"\nFull rankings saved to {_BASE}\\live_compare_*.csv")


if __name__ == "__main__":
    main()
