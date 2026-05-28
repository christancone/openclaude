"""Dump full structured content of the OEM Serialization Listing rows that contain FCC."""
import csv, json
csv.field_size_limit(2**31 - 1)
CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"
TARGET = {15521, 9145, 9149, 9243}

with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
    r = csv.DictReader(f)
    for i, row in enumerate(r):
        if i not in TARGET: continue
        print(f"\n{'='*80}\nROW {i} | {row.get('file_name','')} p.{row.get('page_index','')}")
        ej = json.loads(row.get("extracted_json","") or "{}")
        # walk to find rows with 822-0809
        def find_table_rows(node, path="$"):
            results = []
            if isinstance(node, dict):
                if "rows" in node and isinstance(node["rows"], list):
                    for ri, rrow in enumerate(node["rows"]):
                        as_str = json.dumps(rrow, ensure_ascii=False)
                        if "822-0809" in as_str or "4FM4CT" in as_str or "FCC" in as_str.upper() or "FLIGHT CONTROL" in as_str.upper():
                            results.append((f"{path}.rows[{ri}]", rrow))
                for k, v in node.items():
                    results.extend(find_table_rows(v, f"{path}.{k}"))
            elif isinstance(node, list):
                for j, v in enumerate(node):
                    results.extend(find_table_rows(v, f"{path}[{j}]"))
            return results
        # also tables[i].name to identify
        if "tables" in ej and isinstance(ej["tables"], list):
            for ti, t in enumerate(ej["tables"]):
                print(f"\n-- table[{ti}] name={t.get('name')}")
                # print headers if any
                if "rows" in t and t["rows"]:
                    print(f"   first row (likely header): {t['rows'][0]}")
        hits = find_table_rows(ej)
        for p, content in hits:
            print(f"\n  [{p}]")
            print(f"     {content}")
        # Look for raw text
        if "sections" in ej:
            for si, sec in enumerate(ej["sections"] if isinstance(ej["sections"], list) else []):
                s = json.dumps(sec, ensure_ascii=False)
                if "822-0809" in s or "FCC" in s.upper() or "FLIGHT CONTROL" in s.upper() or "4FM4CT" in s:
                    print(f"\n  sections[{si}]:")
                    print(f"    {sec}")
