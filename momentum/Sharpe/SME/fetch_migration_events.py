"""
fetch_migration_events.py
=========================
For each NSE Emerge -> Main Board migrated symbol (SME/nse_migrated_list.csv),
pull NSE corporate announcements in the ~18 months before migration, scan the
attached PDFs for migration language, and classify the events:

    BOARD / MENTION : first public mention of migrating to the main board
    INPRINCIPLE     : in-principle approval from NSE
    FINAL           : final listing approval on the main board (T-1..T-3)

Everything is cached under SME/migration_events_cache/ (announcement JSON per
symbol and one JSON of PDF findings), so the script is resumable.
Output: SME/analysis/migration_events.csv
"""
import json, os, re, sys, time, io, datetime as dt
from pathlib import Path
import pandas as pd
import requests
import pypdf

HERE = Path(__file__).resolve().parent
CACHE = HERE / "migration_events_cache"
CACHE.mkdir(exist_ok=True)
(HERE / "analysis").mkdir(exist_ok=True)
PDF_DB = CACHE / "pdf_findings.json"
pdf_db = json.loads(PDF_DB.read_text()) if PDF_DB.exists() else {}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
      "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"}
sess = requests.Session(); sess.headers.update(UA)

SKIP_DESC = re.compile(r"trading window|certificate|compliance|shareholding|financial|result|dividend|appointment|resignation|"
                       r"record date|sast|takeover|insider|depositor|newspaper|loss of share|credit rating|analyst|investor meet|"
                       r"change in director|esop|duplicate|reconciliation|statement of deviation|annual report|agm|related party|auditor", re.I)
KEEP_DESC = re.compile(r"board meeting|outcome|shareholder|postal|ballot|egm|updates?$|general update|migrat|listing|approval|"
                       r"intimation|clarification|other upsi|material event|announcement", re.I)
MIG = re.compile(r"migrat|main\s*board|mainboard|main\s*platform|capital\s*market\s*segment|nse\s*main", re.I)
INPR = re.compile(r"in[\s-]*principle", re.I)
FINAL = re.compile(r"(final|listing)\s+approval|approval\s+(received|granted).{0,80}listing|admitted to dealings|"
                   r"commence.{0,40}trading|trading.{0,40}(main\s*board|capital market segment)|permitted to trade|list(ed|ing) on the (main\s*board|capital market)", re.I)


def get(url, tries=4, **kw):
    for k in range(tries):
        try:
            r = sess.get(url, timeout=40, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (401, 403):
                sess.get("https://www.nseindia.com/", timeout=25)
        except Exception:
            pass
        time.sleep(1.5 * (k + 1))
    return None


def announcements(sym, d0, d1):
    f = CACHE / f"{sym}.json"
    if f.exists():
        return json.loads(f.read_text())
    out, seen = [], set()
    start = d0
    while start <= d1:
        end = min(start + dt.timedelta(days=120), d1)
        for idx in ("equities", "sme"):
            r = get(f"https://www.nseindia.com/api/corporate-announcements?index={idx}&symbol={sym}"
                    f"&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}")
            if r is not None:
                try:
                    for x in r.json():
                        k = (x.get("an_dt"), x.get("attchmntFile"), x.get("desc"))
                        if k not in seen:
                            seen.add(k); out.append(x)
                    break
                except Exception:
                    pass
        time.sleep(0.2)
        start = end + dt.timedelta(days=1)
    f.write_text(json.dumps(out))
    return out


def scan_pdf(url):
    if url in pdf_db:
        return pdf_db[url]
    r = get(url)
    res = {"ok": False}
    if r is not None and r.content[:4] == b"%PDF":
        try:
            rd = pypdf.PdfReader(io.BytesIO(r.content))
            t = " ".join((p.extract_text() or "") for p in rd.pages[:6])
            t = re.sub(r"\s+", " ", t)
            res = {"ok": True, "chars": len(t)}
            m = MIG.search(t)
            if m:
                res["mig"] = True
                res["snip"] = t[max(0, m.start() - 120): m.end() + 220]
                res["inpr"] = bool(INPR.search(t))
                res["final"] = bool(FINAL.search(t))
        except Exception as e:
            res = {"ok": False, "err": str(e)[:80]}
    pdf_db[url] = res
    if len(pdf_db) % 25 == 0:
        PDF_DB.write_text(json.dumps(pdf_db))
    time.sleep(0.15)
    return res


def main():
    from concurrent.futures import ThreadPoolExecutor
    lst = pd.read_csv(HERE / "nse_migrated_list.csv", parse_dates=["mig_date"])
    rows = []
    t0 = time.time()
    # stage 1: announcement lists, parallel
    def _ann(r):
        D = r.mig_date.date()
        return announcements(r.symbol.strip(), D - dt.timedelta(days=300), D + dt.timedelta(days=2))
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(_ann, lst.itertuples()))
    print(f"stage1 done {time.time()-t0:.0f}s", flush=True)
    # stage 2: PDF candidates, parallel prefetch (fills pdf_db)
    urls = []
    for r in lst.itertuples():
        for a in json.loads((CACHE / f"{r.symbol.strip()}.json").read_text()):
            desc = a.get("desc") or ""; txt = desc + " " + (a.get("attchmntText") or ""); f = a.get("attchmntFile")
            if f and (MIG.search(txt) or (KEEP_DESC.search(desc) and not SKIP_DESC.search(desc))):
                urls.append(f)
    urls = [u for u in dict.fromkeys(urls) if u not in pdf_db]
    print(f"{len(urls)} PDFs to scan", flush=True)
    done = [0]
    def _scan(u):
        scan_pdf(u); done[0] += 1
        if done[0] % 100 == 0:
            print(f"  pdfs {done[0]}/{len(urls)} ({time.time()-t0:.0f}s)", flush=True)
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(_scan, urls))
    PDF_DB.write_text(json.dumps(pdf_db))
    for i, r in enumerate(lst.itertuples(), 1):
        sym, D = r.symbol.strip(), r.mig_date.date()
        ann = announcements(sym, D - dt.timedelta(days=300), D + dt.timedelta(days=2))
        n_pdf = 0
        for a in ann:
            desc = (a.get("desc") or "")
            txt = desc + " " + (a.get("attchmntText") or "")
            try:
                adt = pd.to_datetime(a.get("an_dt"), format="%d-%b-%Y %H:%M:%S")
            except Exception:
                continue
            ev = None
            text_hit = bool(MIG.search(txt))
            f = a.get("attchmntFile")
            pdf_res = None
            if f and (text_hit or (KEEP_DESC.search(desc) and not SKIP_DESC.search(desc))):
                pdf_res = scan_pdf(f); n_pdf += 1
            if text_hit or (pdf_res and pdf_res.get("mig")):
                kind = "MENTION"
                blob = txt + " " + (pdf_res.get("snip", "") if pdf_res else "")
                if (pdf_res and pdf_res.get("inpr")) or INPR.search(blob):
                    kind = "INPRINCIPLE"
                if (pdf_res and pdf_res.get("final")) or FINAL.search(txt):
                    kind = "FINAL" if kind != "INPRINCIPLE" else "INPRINCIPLE"
                rows.append({"symbol": sym, "mig_date": D, "an_dt": adt, "kind": kind, "desc": desc,
                             "snippet": ((pdf_res.get("snip") if pdf_res else None) or txt or "")[:300], "file": f})
        print(f"[{i}/{len(lst)}] {sym}: {len(ann)} ann, {n_pdf} pdf scanned, events so far {len(rows)} ({time.time()-t0:.0f}s)", flush=True)
        PDF_DB.write_text(json.dumps(pdf_db))
        pd.DataFrame(rows).to_csv(HERE / "analysis" / "migration_events.csv", index=False)
    print("DONE")


if __name__ == "__main__":
    main()
