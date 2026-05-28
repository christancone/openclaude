#!/usr/bin/env python3
"""Exhaustive search for MAY18-4688 / 5012373 wheel evidence."""
import csv, json, re, sys

CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"

# Build normalization helpers
def norm(s):
    if not isinstance(s, str): return ""
    return re.sub(r"[\s\-_/.\\]+", "", s).upper()

SN_VARIANTS = ["MAY184688"]  # normalised
PN_VARIANTS = ["5012373"]
WHEEL_KEYWORDS = ["MAIN WHEEL", "MLG WHEEL", "WHEEL ASSY", "WHEEL ASSEMBLY", "MAINWHEEL"]

csv.field_size_limit(2**31 - 1)

hits = []
total_rows = 0
total_with_sn = 0
total_with_pn = 0

with open(CSV_PATH, "r", encoding="utf-8", errors="replace", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        total_rows += 1
        ej_raw = row.get("extracted_json") or ""
        if not ej_raw:
            continue
        # Quick screen on raw string (normalised)
        ej_norm = norm(ej_raw)
        has_sn = any(v in ej_norm for v in SN_VARIANTS)
        has_pn = "5012373" in ej_norm  # PN already digit-only
        if not (has_sn or has_pn):
            continue
        if has_sn: total_with_sn += 1
        if has_pn: total_with_pn += 1
        try:
            ej = json.loads(ej_raw)
        except Exception:
            ej = None
        rec = {
            "row_id": row.get("id"),
            "document_id": row.get("document_id"),
            "file_name": row.get("file_name"),
            "page_index": row.get("page_index"),
            "has_sn": has_sn,
            "has_pn": has_pn,
            "document_type": None,
            "title": None,
            "excerpts": [],
            "tables_hits": [],
            "entities_hits": [],
            "header_hits": {},
            "section_hits": [],
            "text_hits": [],
        }
        if isinstance(ej, dict):
            rec["document_type"] = ej.get("document_type")
            rec["title"] = ej.get("title")
            # header_fields
            hf = ej.get("header_fields") or {}
            if isinstance(hf, dict):
                for k, v in hf.items():
                    sv = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                    if "5012373" in norm(sv) or any(x in norm(sv) for x in SN_VARIANTS):
                        rec["header_hits"][k] = v
            # tables
            tables = ej.get("tables") or []
            if isinstance(tables, list):
                for ti, t in enumerate(tables):
                    if not isinstance(t, dict):
                        continue
                    t_title = t.get("title") or t.get("name") or ""
                    rows_ = t.get("rows") or t.get("data") or []
                    headers = t.get("headers") or t.get("columns") or []
                    if isinstance(rows_, list):
                        for ri, rrow in enumerate(rows_):
                            sblob = json.dumps(rrow, ensure_ascii=False)
                            sn_ok = any(v in norm(sblob) for v in SN_VARIANTS)
                            pn_ok = "5012373" in norm(sblob)
                            if sn_ok or pn_ok:
                                rec["tables_hits"].append({
                                    "table_idx": ti,
                                    "table_title": t_title,
                                    "headers": headers,
                                    "row_idx": ri,
                                    "row": rrow,
                                    "sn": sn_ok,
                                    "pn": pn_ok,
                                })
            # entities
            ents = ej.get("entities") or []
            if isinstance(ents, list):
                for e in ents:
                    sblob = json.dumps(e, ensure_ascii=False)
                    if any(v in norm(sblob) for v in SN_VARIANTS) or "5012373" in norm(sblob):
                        rec["entities_hits"].append(e)
            # sections
            secs = ej.get("sections") or []
            if isinstance(secs, list):
                for s in secs:
                    sblob = json.dumps(s, ensure_ascii=False) if not isinstance(s, str) else s
                    if any(v in norm(sblob) for v in SN_VARIANTS) or "5012373" in norm(sblob):
                        # Trim
                        if isinstance(s, dict):
                            rec["section_hits"].append({k: (v[:400] if isinstance(v, str) else v) for k, v in s.items()})
                        else:
                            rec["section_hits"].append(s[:400])
            # text
            text = ej.get("text") or ej.get("raw_text") or ""
            if isinstance(text, str) and text:
                ntext = norm(text)
                if any(v in ntext for v in SN_VARIANTS) or "5012373" in ntext:
                    # Find offsets in original
                    for pat in ["MAY18", "5012373"]:
                        for m in re.finditer(re.escape(pat), text, re.IGNORECASE):
                            start = max(0, m.start() - 200)
                            end = min(len(text), m.end() + 200)
                            rec["text_hits"].append(text[start:end])
            # metadata
            md = ej.get("metadata") or {}
            if isinstance(md, dict):
                for k, v in md.items():
                    sv = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                    if any(x in norm(sv) for x in SN_VARIANTS) or "5012373" in norm(sv):
                        rec["header_hits"][f"meta::{k}"] = v
        hits.append(rec)

print(f"TOTAL ROWS SCANNED: {total_rows}")
print(f"ROWS WITH SN HIT: {total_with_sn}")
print(f"ROWS WITH PN HIT: {total_with_pn}")
print(f"UNIQUE HIT ROWS: {len(hits)}")
print("=" * 100)

# Save full hits to JSON
with open(r"D:/work/openclaude/_tmp_wheel_hits.json", "w", encoding="utf-8") as f:
    json.dump(hits, f, ensure_ascii=False, indent=2)

# Print compact summary
for i, h in enumerate(hits):
    print(f"\n[{i+1}] file={h['file_name']!r} p.{h['page_index']} doc_type={h['document_type']!r} title={h['title']!r} SN={h['has_sn']} PN={h['has_pn']}")
    if h["header_hits"]:
        print(f"   header_hits: {json.dumps(h['header_hits'], ensure_ascii=False)[:600]}")
    for th in h["tables_hits"]:
        rr = th["row"]
        rr_s = json.dumps(rr, ensure_ascii=False)
        print(f"   TABLE[{th['table_idx']}/{th['table_title']}] row[{th['row_idx']}] sn={th['sn']} pn={th['pn']}: {rr_s[:500]}")
        if th["headers"]:
            print(f"      headers: {json.dumps(th['headers'], ensure_ascii=False)[:300]}")
    for eh in h["entities_hits"][:5]:
        print(f"   ENTITY: {json.dumps(eh, ensure_ascii=False)[:400]}")
    for sh in h["section_hits"][:3]:
        print(f"   SECTION: {json.dumps(sh, ensure_ascii=False)[:400]}")
    for txt in h["text_hits"][:3]:
        print(f"   TEXT: ...{txt}...")
