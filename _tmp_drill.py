#!/usr/bin/env python3
"""Drill into specific pages for full Form 1 / CRS / Serialization Listing detail."""
import csv, json, re

CSV_PATH = r"D:/work/openclaude/csvs/Full challenger dossier.csv"
csv.field_size_limit(2**31 - 1)

TARGETS = [
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "208"),
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "209"),
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "210"),
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "211"),
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "4"),
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "5"),
    ("CL650-6134_bo2_bi7_CL65HBJTZ015MX_6134-21-13_16-Jul-2021.pdf", "15"),
    ("CL650-6134_boA_bib_Technical Log_Airframe_29-Dec-2022.pdf", "87"),
    ("190220_HB-JTZ_PART_NUMBERS.pdf", "9"),
    ("PART NUMBERS.pdf", "12"),
    ("Annex 1 � Life Limited & Overhaul Components Status Report.pdf", "11"),
    ("PPI Report_CL650_SN 6134_25.10.2023.pdf", "22"),
]

with open(CSV_PATH, "r", encoding="utf-8", errors="replace", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        for fn, pg in TARGETS:
            if fn in (row.get("file_name") or "") and str(row.get("page_index")) == pg:
                print(f"\n{'#'*80}\n# FILE: {row.get('file_name')}  p.{pg}\n{'#'*80}")
                ej_raw = row.get("extracted_json") or ""
                try:
                    ej = json.loads(ej_raw)
                except Exception:
                    print("(json parse failed; raw excerpt):")
                    print(ej_raw[:3000])
                    continue
                # Print everything except text (keep it bounded)
                hf = ej.get("header_fields") or {}
                if hf:
                    print("HEADER_FIELDS:")
                    print(json.dumps(hf, ensure_ascii=False, indent=2)[:4000])
                md = ej.get("metadata") or {}
                if md:
                    print("\nMETADATA:")
                    print(json.dumps(md, ensure_ascii=False, indent=2)[:4000])
                tables = ej.get("tables") or []
                for ti, t in enumerate(tables):
                    print(f"\nTABLE[{ti}] title={t.get('title')!r}")
                    print(json.dumps(t, ensure_ascii=False, indent=2)[:3000])
                secs = ej.get("sections") or []
                for s in secs:
                    print("\nSECTION:")
                    print(json.dumps(s, ensure_ascii=False, indent=2)[:2000])
                txt = ej.get("text") or ej.get("raw_text") or ""
                if txt:
                    print("\nTEXT (first 2500 chars):")
                    print(txt[:2500])
