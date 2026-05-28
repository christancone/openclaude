"""Search the serialization-listing PDFs and Annex 1 LLP report for FCC mentions."""
import csv, json, re
csv.field_size_limit(2**31 - 1)
CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"

# names of interest
NAMES_OF_INTEREST = ["PART NUMBERS", "190220", "Aircraft Serialization", "Annex 1",
                     "Life Limited", "Component", "Inventory", "Master Inventory"]
EXCLUDE = "5. Pre-Purchase Inspection Report"

def walk_collect(obj, out):
    if obj is None: return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values(): walk_collect(v, out)
    elif isinstance(obj, list):
        for v in obj: walk_collect(v, out)

with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    r = csv.DictReader(f)
    matched_files = {}
    for i, row in enumerate(r):
        path = row.get("original_path", "") or ""
        fname = row.get("file_name","") or ""
        if EXCLUDE in path:
            continue
        if not any(n.lower() in fname.lower() or n.lower() in path.lower() for n in NAMES_OF_INTEREST):
            continue
        ej_raw = row.get("extracted_json","") or ""
        try:
            ej = json.loads(ej_raw)
        except Exception:
            ej = None
        leaves = []
        if ej is not None:
            walk_collect(ej, leaves)
        text = "\n".join(leaves)
        upp = text.upper()
        # Look for FCC keywords AND 822-0809
        has_fcc = "FLIGHT CONTROL COMPUTER" in upp or " FCC" in upp or "FCC-" in upp or "AUTO FLIGHT" in upp
        has_pn = "822-0809" in upp or "8220809" in upp.replace("-","").replace(" ","")
        has_sn = "4FM4CT" in upp.replace(" ","").replace("-","")
        if has_fcc or has_pn or has_sn:
            matched_files.setdefault(fname, []).append({
                "row": i, "page": row.get("page_index",""),
                "doc_type": (ej or {}).get("document_type") if isinstance(ej,dict) else None,
                "title": (ej or {}).get("title") if isinstance(ej,dict) else None,
                "has_fcc": has_fcc, "has_pn": has_pn, "has_sn": has_sn,
                "excerpt": next((l for l in leaves if ("FLIGHT CONTROL" in l.upper() or "822-0809" in l.upper() or "4FM4CT" in l.upper())), text[:400])[:600]
            })

    for fname, entries in sorted(matched_files.items()):
        print(f"\n### {fname} ({len(entries)} pages)")
        for e in entries[:15]:
            print(f"  row {e['row']} p.{e['page']} | doc={e['doc_type']} title={e['title']}")
            print(f"    fcc={e['has_fcc']} pn={e['has_pn']} sn={e['has_sn']}")
            print(f"    excerpt: {e['excerpt']!r}")

# Also list the files themselves
print("\n=== all files in dossier matching name filter (no content filter) ===")
with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    r = csv.DictReader(f)
    seen = set()
    for row in r:
        path = row.get("original_path", "") or ""
        fname = row.get("file_name","") or ""
        if EXCLUDE in path: continue
        if any(n.lower() in fname.lower() or n.lower() in path.lower() for n in NAMES_OF_INTEREST):
            if fname not in seen:
                seen.add(fname)
                print(f"  {fname}  ::  {path}")
