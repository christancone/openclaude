"""Deep dive specific rows to extract structured fields."""
import csv, json, re
csv.field_size_limit(2**31 - 1)

TARGET_ROWS = {1747, 1763, 1766, 1783, 1818, 1820, 1823, 1825, 1833, 1835, 1859,
               1899, 1930, 1940, 1941, 1961, 1969, 1976, 1979, 1984, 1985, 1988,
               1991, 2041, 2042, 2056, 2443, 2452, 2465,
               16294, 16319, 16333, 16370, 16378, 16381, 16387}

CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"

def find_relevant_chunks(ej, terms):
    """Find table rows or text snippets containing any term."""
    results = []
    def walk(node, path="$"):
        if isinstance(node, dict):
            # if it has 'rows', check each row
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            up = node.upper()
            for t in terms:
                if t in up:
                    results.append((path, node[:600]))
                    break
    walk(ej)
    return results

TERMS = ["4FM4CT", "822-0809", "822 0809", "8220809", "4FMACT", "FCC", "FLIGHT CONTROL COMPUTER",
         "4CX4R4", "AUTO FLIGHT", "ATA 22", "BORN ON", "SERIALIZATION"]

with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    r = csv.DictReader(f)
    for i, row in enumerate(r):
        if i not in TARGET_ROWS:
            continue
        ej_raw = row.get("extracted_json","") or ""
        try:
            ej = json.loads(ej_raw)
        except Exception:
            ej = ej_raw
        print(f"\n{'='*100}\nROW {i} | {row.get('file_name','')} p.{row.get('page_index','')}")
        if isinstance(ej, dict):
            print(f"  doc_type: {ej.get('document_type')}  title: {ej.get('title')}")
            md = ej.get("metadata") or {}
            if md:
                # truncate big
                md_short = {k: (str(v)[:200] if not isinstance(v,(dict,list)) else json.dumps(v)[:300]) for k,v in md.items()}
                print(f"  metadata: {md_short}")
            ent = ej.get("entities")
            if ent:
                print(f"  entities: {json.dumps(ent, ensure_ascii=False)[:1500]}")
        hits = find_relevant_chunks(ej, TERMS)
        # dedupe by short text
        seen = set()
        for p, t in hits:
            key = (p, t[:80])
            if key in seen: continue
            seen.add(key)
            print(f"  [{p}] {t}")

print("\nDONE")
