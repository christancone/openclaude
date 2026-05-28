"""Check Master Inventory and Annex 1 in dossier for FCC mentions."""
import csv, json
csv.field_size_limit(2**31 - 1)
CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"

def walk_collect(obj, out):
    if isinstance(obj, str): out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values(): walk_collect(v, out)
    elif isinstance(obj, list):
        for v in obj: walk_collect(v, out)

target_substrings = ["Master Inventory", "Annex 1", "Life Limited", "LIFE ITEMS",
                     "CALENDAR.pdf", "COMPONENTS TRACKING", "Component Status"]

# Look for any FCC/Auto Flight mention in those files
with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    r = csv.DictReader(f)
    for i, row in enumerate(r):
        path = row.get("original_path", "") or ""
        fname = row.get("file_name","") or ""
        if "5. Pre-Purchase" in path: continue
        if not any(t.lower() in fname.lower() or t.lower() in path.lower() for t in target_substrings):
            continue
        ej_raw = row.get("extracted_json","") or ""
        try:
            ej = json.loads(ej_raw)
        except Exception:
            continue
        leaves = []
        walk_collect(ej, leaves)
        text = "\n".join(leaves)
        upp = text.upper()
        # only show pages mentioning FCC/auto-flight/822-0809/4FM4CT
        if not ("FLIGHT CONTROL COMPUTER" in upp or "822-0809" in upp or "4FM4CT" in upp or "AUTO FLIGHT" in upp.replace("-"," ").replace("/"," ")):
            continue
        print(f"\n--- row {i} | {fname} p.{row.get('page_index','')} (doc_type={ej.get('document_type')})")
        for l in leaves:
            up = l.upper()
            if "FLIGHT CONTROL COMPUTER" in up or "822-0809" in up or "4FM4CT" in up or "FCC" in up:
                print(f"   {l[:300]}")
