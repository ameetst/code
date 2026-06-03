"""
download_amfi_nav.py  (copy placed here for use with etf_momentum_ranking_amfi.py)
"""
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

SCRIPT_DIR    = Path(__file__).resolve().parent
CODES_FILE    = SCRIPT_DIR / "AMFI ETF Codes.csv"
OUT_WIDE      = SCRIPT_DIR / "AMFI_NAV_History.xlsx"
OUT_LONG      = SCRIPT_DIR / "AMFI_NAV_Long.csv"
OUT_LOG       = SCRIPT_DIR / "AMFI_NAV_download_log.csv"
API_BASE      = "https://api.mfapi.in/mf/{code}"
DELAY_S       = 0.25
LOOKBACK_DAYS = 365
TIMEOUT_S     = 15


def fetch_nav_history(code):
    try:
        resp = requests.get(API_BASE.format(code=code), timeout=TIMEOUT_S)
        resp.raise_for_status()
        return resp.json().get("data", []), ""
    except requests.exceptions.Timeout:
        return [], "TIMEOUT"
    except requests.exceptions.ConnectionError:
        return [], "CONNECTION_ERROR"
    except Exception as e:
        return [], str(e)[:80]


def filter_one_year(records):
    cutoff = datetime.today() - timedelta(days=LOOKBACK_DAYS)
    kept = []
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
    print(f"  Input     : {CODES_FILE.name}")
    print(f"{'='*60}\n")

    codes_df = pd.read_csv(CODES_FILE)
    codes_df.columns = [c.strip() for c in codes_df.columns]
    print(f"Loaded {len(codes_df)} ETFs\n")

    all_records, log_rows, wide_data = [], [], {}
    total = len(codes_df)

    for i, row in codes_df.iterrows():
        code        = int(row["Code"])
        scheme_name = str(row["Scheme Name"]).strip()
        nav_name    = str(row["Scheme NAV Name"]).strip()
        print(f"[{i+1:3d}/{total}] {code}  {scheme_name[:55]:<55}", end="  ", flush=True)

        raw, err = fetch_nav_history(code)
        if err:
            print(f"FAILED - {err}")
            log_rows.append({"Code": code, "Scheme Name": scheme_name,
                             "Status": "FAILED", "Error": err, "Records": 0,
                             "DateFrom": None, "DateTo": None})
            time.sleep(DELAY_S)
            continue

        filtered = filter_one_year(raw)
        if not filtered:
            print(f"NO DATA  (total in API: {len(raw)})")
            log_rows.append({"Code": code, "Scheme Name": scheme_name,
                             "Status": "NO_DATA_IN_RANGE", "Error": "",
                             "Records": 0, "DateFrom": None, "DateTo": None})
            time.sleep(DELAY_S)
            continue

        filtered.sort(key=lambda x: x["date"])
        date_from, date_to = filtered[0]["date"], filtered[-1]["date"]
        print(f"OK  {len(filtered):3d} pts  ({date_from} -> {date_to})")
        log_rows.append({"Code": code, "Scheme Name": scheme_name,
                         "Status": "OK", "Error": "", "Records": len(filtered),
                         "DateFrom": str(date_from), "DateTo": str(date_to)})

        for rec in filtered:
            all_records.append({"Date": rec["date"], "Code": code,
                                 "Scheme Name": scheme_name, "NAV Name": nav_name,
                                 "NAV": rec["nav"]})
        wide_data[scheme_name] = {rec["date"]: rec["nav"] for rec in filtered}
        time.sleep(DELAY_S)

    print(f"\nSaving long-format CSV -> {OUT_LONG.name}")
    long_df = pd.DataFrame(all_records)
    long_df.to_csv(OUT_LONG, index=False)
    print(f"  {len(long_df):,} total rows written.")

    print(f"Building wide-format pivot -> {OUT_WIDE.name}")
    if wide_data:
        all_dates = sorted(set(d for nm in wide_data.values() for d in nm))
        wide_rows = [{"Date": d, **{sn: nv.get(d) for sn, nv in wide_data.items()}}
                     for d in all_dates]
        wide_df   = pd.DataFrame(wide_rows).set_index("Date").sort_index()
        with pd.ExcelWriter(OUT_WIDE, engine="openpyxl") as writer:
            wide_df.to_excel(writer, sheet_name="NAV History")
            pd.DataFrame(log_rows).to_excel(writer, sheet_name="Download Log", index=False)
        print(f"  {len(wide_df)} dates x {len(wide_df.columns)} ETFs")

    log_df = pd.DataFrame(log_rows)
    log_df.to_csv(OUT_LOG, index=False)
    ok = (log_df["Status"] == "OK").sum()
    print(f"\n{'='*60}")
    print(f"  Successful : {ok} / {total}")
    print(f"  Output     : {OUT_WIDE}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
