#!/usr/bin/env python3
"""Search dossier CSV for snapshot patterns."""
import csv, json, re, sys
from collections import defaultdict

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"

# Patterns
PAT_559 = re.compile(r"559[:\.\s]*25")
PAT_609 = re.compile(r"609[:\.\s]*35")
PAT_231 = re.compile(r"\b231\b")
PAT_DATE = re.compile(r"\b(20[12]\d)[-/](\d{1,2})[-/](\d{1,2})\b|\b(\d{1,2})[-/\s](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s](20[12]\d)\b", re.I)
PAT_TAH = re.compile(r"(TAH|A/?F\s*(?:HRS|HOURS|TT|TIME|TAH)|AIRFRAME\s*HOURS?|TOTAL\s*TIME)\s*[:=]?\s*(\d{1,4}[:\.]\d{1,2})", re.I)
PAT_TAC = re.compile(r"(TAC|A/?F\s*(?:CYC|CYCLES?|TC|LDG|LANDINGS?)|AIRFRAME\s*CYCLES?|TOTAL\s*CYCLES?|AFL)\s*[:=]?\s*(\d{1,4})", re.I)

# 231 paired w/ cycles/AFL/LDG/TAC
PAT_231_CTX = re.compile(r"(231\s*(?:cy|cyc|cycles?|ldg|landings?|tac|afl)\b|\b(?:cy|cyc|cycles?|ldg|landings?|tac|afl)[:\s]+231\b)", re.I)

EXCLUDE_DIR = "Pre-Purchase Inspection Report"

hits_559 = []
hits_609 = []
hits_231_paired = []
date_snapshots = []  # (date_str, tah, tac, file_name, page_index)
all_files = defaultdict(int)

def extract_text_from_json(ej):
    """Pull all text-ish content out of extracted_json."""
    if not ej:
        return ""
    try:
        obj = json.loads(ej)
    except Exception:
        return ej
    parts = []
    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, list):
            for i in x: walk(i)
        elif isinstance(x, dict):
            for k, v in x.items():
                parts.append(str(k))
                walk(v)
    walk(obj)
    return "\n".join(parts), obj

with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        fn = row.get("file_name", "") or ""
        if EXCLUDE_DIR in fn or "Pre-Purchase" in fn:
            continue
        pidx = row.get("page_index", "")
        ej = row.get("extracted_json", "") or ""
        all_files[fn] += 1
        try:
            text, obj = extract_text_from_json(ej)
        except Exception:
            text, obj = ej, {}

        if PAT_559.search(text):
            # grab a snippet around match
            m = PAT_559.search(text)
            s = max(0, m.start()-120); e = min(len(text), m.end()+120)
            hits_559.append((fn, pidx, text[s:e].replace("\n", " | ")))
        if PAT_609.search(text):
            m = PAT_609.search(text)
            s = max(0, m.start()-100); e = min(len(text), m.end()+100)
            hits_609.append((fn, pidx, text[s:e].replace("\n", " | ")))
        for m in PAT_231_CTX.finditer(text):
            s = max(0, m.start()-100); e = min(len(text), m.end()+100)
            hits_231_paired.append((fn, pidx, text[s:e].replace("\n", " | ")))

        # extract TAH+TAC near dates (within same document)
        tahs = PAT_TAH.findall(text)
        tacs = PAT_TAC.findall(text)
        dates = PAT_DATE.findall(text)
        if (tahs or tacs) and dates:
            # take first of each
            date_str = ""
            for d in dates:
                if d[0]:
                    date_str = f"{d[0]}-{d[1].zfill(2)}-{d[2].zfill(2)}"
                else:
                    date_str = f"{d[3]}-{d[4]}-{d[5]}"
                break
            tah_val = tahs[0][1] if tahs else ""
            tac_val = tacs[0][1] if tacs else ""
            date_snapshots.append((date_str, tah_val, tac_val, fn, pidx))

print(f"=== HITS 559:25 ({len(hits_559)}) ===")
for fn, p, ctx in hits_559[:40]:
    print(f"\n[{fn} p{p}]")
    print(f"  ...{ctx}...")

print(f"\n\n=== HITS 609:35 ({len(hits_609)}) ===")
for fn, p, ctx in hits_609[:30]:
    print(f"\n[{fn} p{p}]")
    print(f"  ...{ctx}...")

print(f"\n\n=== HITS 231 PAIRED w/ cycles/AFL/LDG/TAC ({len(hits_231_paired)}) ===")
for fn, p, ctx in hits_231_paired[:30]:
    print(f"\n[{fn} p{p}]")
    print(f"  ...{ctx}...")

# Sort date snapshots
def sortkey(t):
    return t[0]
date_snapshots.sort(key=sortkey, reverse=True)
print(f"\n\n=== TOP 20 LATEST DATE+TAH/TAC SNAPSHOTS ===")
seen = set()
shown = 0
for d, tah, tac, fn, p in date_snapshots:
    k = (d, fn, p)
    if k in seen: continue
    seen.add(k)
    print(f"  {d}  TAH={tah:>10}  TAC={tac:>6}  [{fn} p{p}]")
    shown += 1
    if shown >= 30: break

print(f"\n\n=== TOTAL FILES ({len(all_files)}) ===")
for fn, n in sorted(all_files.items()):
    print(f"  {n:>4}  {fn}")
