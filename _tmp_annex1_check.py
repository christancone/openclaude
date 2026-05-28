#!/usr/bin/env python3
"""Pull Annex 1 header_fields and find latest as-of date in dossier."""
import csv, json, re, sys
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"

EXCLUDE = "Pre-Purchase"
print("=== Annex 1 header_fields (page 0/1) ===")
with open(CSV_PATH, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        fn = row.get("file_name","") or ""
        if "Annex 1" not in fn:
            continue
        pidx = row.get("page_index","")
        if pidx not in ("0","1","2"):
            continue
        ej = row.get("extracted_json","") or ""
        try:
            obj = json.loads(ej)
        except Exception:
            continue
        hf = obj.get("header_fields") or {}
        print(f"\n[{fn} p{pidx}]")
        print(f"  document_type: {obj.get('document_type')}")
        print(f"  title: {obj.get('title')}")
        print(f"  header_fields: {json.dumps(hf, indent=2)[:1500]}")
        # find any 559 or 609
        text = json.dumps(obj)
        for pat in ["559:25","609:35","559.25","609.35","aircraft_hours","Aircraft Hours","Total Time","total_time"]:
            i = text.find(pat)
            if i >= 0:
                print(f"  match {pat!r}: ...{text[max(0,i-80):i+120]}...")

# also: find the latest as-of date / report date in dossier
print("\n\n=== Latest 'as of' dates and report dates ===")
date_pat = re.compile(r"(as[_\s]of(?:[_\s]date)?|report[_\s]date|effective[_\s]date|as[_\s]of[_\s]hours)\W{0,3}([0-9A-Za-z\-:/ ]{4,30})", re.I)
found = []
with open(CSV_PATH, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        fn = row.get("file_name","") or ""
        if EXCLUDE in fn: continue
        ej = row.get("extracted_json","") or ""
        if not ej: continue
        try:
            obj = json.loads(ej)
        except Exception:
            continue
        hf = obj.get("header_fields") or {}
        if isinstance(hf, dict):
            for k,v in hf.items():
                kl = str(k).lower()
                if "as of" in kl or "as_of" in kl or "report date" in kl or "report_date" in kl or "effective" in kl:
                    found.append((str(v), str(k), fn, row.get("page_index","")))

# normalize - filter to 2023+
import datetime
def parse(d):
    d = d.strip()
    for fmt in ("%d-%b-%Y","%d-%B-%Y","%Y-%m-%d","%d.%m.%Y","%d/%m/%Y","%d %B %Y","%d %b %Y"):
        try:
            return datetime.datetime.strptime(d, fmt)
        except Exception:
            pass
    return None

parsed = []
for v, k, fn, p in found:
    dt = parse(v)
    if dt:
        parsed.append((dt, v, k, fn, p))

parsed.sort(reverse=True)
seen = set()
for dt, v, k, fn, p in parsed[:30]:
    key=(v,fn,p)
    if key in seen: continue
    seen.add(key)
    print(f"  {dt.date()}  [{k}]={v}  ({fn} p{p})")
