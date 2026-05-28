"""Exhaustive search for FCC 4FM4CT / PN 822-0809-810 in the Challenger dossier."""
import csv, json, re, sys
from pathlib import Path

CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"
EXCLUDE = "5. Pre-Purchase Inspection Report"
csv.field_size_limit(2**31 - 1)

SN_VARIANTS = {"4FM4CT", "4FMACT", "4FM4C1", "4FM4CI", "4FM4CL", "4FM4O7", "4FMAOT"}
PN_CORE = "8220809"   # all separators stripped
PN_FAMILY = "8220809"  # core without dash
PN_FULL_DIGITS = "8220809810"
PN_DASH_PATTERNS = [re.compile(r"822\W*0809\W*(\d{3})", re.I)]
SIB_SN = "4CX4R4"

def norm(s):
    if s is None: return ""
    s = str(s).upper()
    return re.sub(r"[^A-Z0-9]", "", s)

def ocr_fold(s):
    if s is None: return ""
    s = str(s).upper()
    table = str.maketrans({"O":"0","I":"1","L":"1","S":"5","B":"8","Z":"2","G":"6"})
    return re.sub(r"[^A-Z0-9]", "", s.translate(table))

def walk_collect_text(obj, out):
    """Recursively collect all string leaves."""
    if obj is None: return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values(): walk_collect_text(v, out)
    elif isinstance(obj, list):
        for v in obj: walk_collect_text(v, out)

def main():
    hits = []
    sib_hits = []
    pn_family_hits = []  # any 822-0809-XXX
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for i, row in enumerate(r):
            path = row.get("original_path", "") or ""
            if EXCLUDE in path:
                continue
            ej_raw = row.get("extracted_json", "") or ""
            if not ej_raw:
                continue
            try:
                ej = json.loads(ej_raw)
            except Exception:
                ej = None
            # gather all text leaves
            leaves = []
            if ej is not None:
                walk_collect_text(ej, leaves)
            else:
                leaves.append(ej_raw)
            all_text = "\n".join(leaves)
            n = norm(all_text)
            nf = ocr_fold(all_text)

            sn_hit = any(v in n for v in SN_VARIANTS) or any(v in nf for v in SN_VARIANTS)
            pn_hit = PN_FULL_DIGITS in n or PN_FULL_DIGITS in nf
            family_hit = False
            family_dashes = set()
            for m in PN_DASH_PATTERNS[0].finditer(all_text):
                family_hit = True
                family_dashes.add(m.group(1))
            sib_hit = SIB_SN in n

            if sn_hit or pn_hit or family_hit:
                hits.append({
                    "row": i,
                    "file_name": row.get("file_name",""),
                    "page_index": row.get("page_index",""),
                    "original_path": path,
                    "sn_hit": sn_hit,
                    "pn_hit": pn_hit,
                    "family_hit": family_hit,
                    "family_dashes": sorted(family_dashes),
                    "doc_type": (ej or {}).get("document_type") if isinstance(ej, dict) else None,
                    "title": (ej or {}).get("title") if isinstance(ej, dict) else None,
                    "text_preview": all_text[:400],
                    "all_text_len": len(all_text),
                })
            if sib_hit:
                sib_hits.append({
                    "row": i,
                    "file_name": row.get("file_name",""),
                    "page_index": row.get("page_index",""),
                })
            if family_hit and not pn_hit:
                pn_family_hits.append({
                    "row": i,
                    "file_name": row.get("file_name",""),
                    "page_index": row.get("page_index",""),
                    "dashes": sorted(family_dashes),
                })

    print(f"=== TOTAL HITS: {len(hits)} ===")
    for h in hits:
        print(f"\n--- row {h['row']} | {h['file_name']} p.{h['page_index']} ---")
        print(f"  doc_type: {h['doc_type']}  title: {h['title']}")
        print(f"  sn={h['sn_hit']} pn={h['pn_hit']} family={h['family_hit']} dashes={h['family_dashes']}")
        print(f"  path: {h['original_path']}")

    print(f"\n=== SIBLING 4CX4R4 hits: {len(sib_hits)} ===")
    for h in sib_hits[:20]:
        print(f"  row {h['row']} | {h['file_name']} p.{h['page_index']}")

    # Save full
    Path("D:/work/openclaude/_tmp_fcc_hits.json").write_text(
        json.dumps({"hits": hits, "sib_hits": sib_hits, "family_other_hits": pn_family_hits}, indent=2),
        encoding="utf-8")
    print(f"\nWrote D:/work/openclaude/_tmp_fcc_hits.json")

if __name__ == "__main__":
    main()
