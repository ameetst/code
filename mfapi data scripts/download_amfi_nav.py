"""
download_amfi_nav.py
====================
Downloads 1-year NAV history for all ETFs listed in 'AMFI ETF Codes.csv'
using the mfapi.in free API.

Output files (same directory as this script):
  - AMFI_NAV_History.xlsx  : Wide-format Excel — rows=dates, cols=scheme names
                              (mirrors ETF.xlsx structure for easy comparison)
  - AMFI_NAV_Long.csv      : Long-format CSV — Date, Code, Scheme Name, NAV
  - AMFI_NAV_download_log.csv : Per-ETF download status and record counts

Usage:
  python download_amfi_nav.py
"""

import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

SCRIPT_DIR   = Path(__file__).resolve().parent
CODES_FILE   = SCRIPT_DIR / "AMFI ETF Codes.csv"
OUT_WIDE     = SCRIPT_DIR / "AMFI_NAV_History.xlsx"
OUT_LONG     = SCRIPT_DIR / "AMFI_NAV_Long.csv"
OUT_LOG      = SCRIPT_DIR / "AMFI_NAV_download_log.csv"

API_BASE     = "https://api.mfapi.in/mf/{code}"
DELAY_S      = 0.25          # seconds between requests — be polite to free API
LOOKBACK_DAYS = 365           # 1 year of history
TIMEOUT_S    = 15


def fetch_nav_history(code: int) -> tuple[list[dict], str]:
    """
    Fetch full NAV history for a scheme code from mfapi.in.
    Returns (records, error_msg). records is list of {date, nav} dicts.
    """
    try:
        url  = API_BASE.format(code=code)
        resp = requests.get(url, timeout=TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []), ""
    except requests.exceptions.Timeout:
        return [], "TIMEOUT"
    except requests.exceptions.ConnectionError:
        return [], "CONNECTION_ERROR"
    except Exception as e:
        return [], str(e)[:80]


def filter_one_year(records: list[dict]) -> list[dict]:
    """Keep only records within the last 365 calendar days."""
    cutoff = datetime.today() - timedelta(days=LOOKBACK_DAYS)
    kept   = []
    for r in records:
        try:
            d = datetime.strptime(r["date"], "%d-%m-%Y")
            if d >= cutoff:
                kept.append({"date": d.date(), "nav": float(r["nav"])})
        except Exception:
            pass
    return kept


def main():
    print(f"\n{'='*60}")
    print("  AMFI NAV History Downloader")
    print(f"  Lookback  : {LOOKBACK_DAYS} days (1 year)")
    print(f"  API       : mfapi.in (free, no auth)")
    print(f"  Input     : {CODES_FILE.name}")
    print(f"{'='*60}\n")

    # Load codes
    codes_df = pd.read_csv(CODES_FILE)
    codes_df.columns = [c.strip() for c in codes_df.columns]
    print(f"Loaded {len(codes_df)} ETFs from {CODES_FILE.name}\n")

    all_records = []   # for long-format output
    log_rows    = []   # per-ETF status
    wide_data   = {}   # scheme_name -> {date: nav}

    total = len(codes_df)
    for i, row in codes_df.iterrows():
        code        = int(row["Code"])
        scheme_name = str(row["Scheme Name"]).strip()
        nav_name    = str(row["Scheme NAV Name"]).strip()

        print(f"[{i+1:3d}/{total}] {code}  {scheme_name[:55]:<55}", end="  ", flush=True)

        raw_records, err = fetch_nav_history(code)

        if err:
            print(f"FAILED — {err}")
            log_rows.append({
                "Code": code, "Scheme Name": scheme_name,
                "Status": "FAILED", "Error": err, "Records": 0,
                "DateFrom": None, "DateTo": None,
            })
            time.sleep(DELAY_S)
            continue

        filtered = filter_one_year(raw_records)

        if not filtered:
            print(f"NO DATA  (total in API: {len(raw_records)})")
            log_rows.append({
                "Code": code, "Scheme Name": scheme_name,
                "Status": "NO_DATA_IN_RANGE", "Error": "",
                "Records": 0, "DateFrom": None, "DateTo": None,
            })
            time.sleep(DELAY_S)
            continue

        # Sort ascending
        filtered.sort(key=lambda x: x["date"])
        date_from = filtered[0]["date"]
        date_to   = filtered[-1]["date"]
        print(f"OK  {len(filtered):3d} pts  ({date_from} -> {date_to})")

        log_rows.append({
            "Code": code, "Scheme Name": scheme_name,
            "Status": "OK", "Error": "",
            "Records": len(filtered),
            "DateFrom": str(date_from), "DateTo": str(date_to),
        })

        # Accumulate for long format
        for rec in filtered:
            all_records.append({
                "Date"       : rec["date"],
                "Code"       : code,
                "Scheme Name": scheme_name,
                "NAV Name"   : nav_name,
                "NAV"        : rec["nav"],
            })

        # Accumulate for wide format (keyed by scheme_name)
        wide_data[scheme_name] = {rec["date"]: rec["nav"] for rec in filtered}

        time.sleep(DELAY_S)

    # ── Save long format CSV ───────────────────────────────────────
    print(f"\nSaving long-format CSV  ->  {OUT_LONG.name}")
    long_df = pd.DataFrame(all_records)
    long_df.to_csv(OUT_LONG, index=False)
    print(f"  {len(long_df):,} total rows written.")

    # ── Save wide-format Excel ─────────────────────────────────────
    print(f"Building wide-format pivot  ->  {OUT_WIDE.name}")
    if wide_data:
        all_dates = sorted(set(d for nav_map in wide_data.values() for d in nav_map))
        wide_rows = []
        for d in all_dates:
            r = {"Date": d}
            for sname, nav_map in wide_data.items():
                r[sname] = nav_map.get(d, None)
            wide_rows.append(r)
        wide_df = pd.DataFrame(wide_rows).set_index("Date")
        wide_df.sort_index(inplace=True)

        with pd.ExcelWriter(OUT_WIDE, engine="openpyxl") as writer:
            wide_df.to_excel(writer, sheet_name="NAV History")
            # Summary sheet
            log_df = pd.DataFrame(log_rows)
            log_df.to_excel(writer, sheet_name="Download Log", index=False)

        print(f"  {len(wide_df)} dates  ×  {len(wide_df.columns)} ETFs")
    else:
        print("  No data to write.")

    # ── Save log CSV ───────────────────────────────────────────────
    log_df = pd.DataFrame(log_rows)
    log_df.to_csv(OUT_LOG, index=False)

    # ── Summary ────────────────────────────────────────────────────
    ok_count   = (log_df["Status"] == "OK").sum()
    fail_count = (log_df["Status"] != "OK").sum()
    print(f"\n{'='*60}")
    print(f"  Download complete")
    print(f"  Successful : {ok_count} / {total}")
    print(f"  Failed     : {fail_count} / {total}")
    print(f"  Output     : {OUT_WIDE}")
    print(f"  Long CSV   : {OUT_LONG}")
    print(f"  Log        : {OUT_LOG}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
