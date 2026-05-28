"""Aggressive OCR-fold + edit-distance recovery pass on the 585 truly-unrecoverable
rejected (PN, SN) tuples. Checks if a 1–2 char OCR typo would have matched a real
install record (Form 1 / LLP / WPSS / Form 1 / OEM serialisation listing) elsewhere
in the dossier.

Confusable folds: O↔0, I↔1, L↔1, S↔5, B↔8, Z↔2, G↔6.
"""

import csv, json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from collections import Counter, defaultdict
csv.field_size_limit(50_000_000)

def norm_basic(s):
    if not s: return None
    s = str(s).strip().upper()
    s = re.sub(r"\.0+$", "", s)
    s = re.sub(r"[^A-Z0-9]", "", s)
    s = s.lstrip("0") or "0"
    return s or None

def norm_aggressive(s):
    s = norm_basic(s)
    if not s: return None
    table = str.maketrans({"O":"0","I":"1","L":"1","S":"5","B":"8","Z":"2","G":"6"})
    return s.translate(table)

def edit_distance(a, b):
    if not a or not b: return 99
    if a == b: return 0
    if abs(len(a) - len(b)) > 3: return 99
    dp = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, cb in enumerate(b, 1):
            cur = dp[j]
            dp[j] = min(dp[j]+1, dp[j-1]+1, prev + (ca != cb))
            prev = cur
    return dp[-1]

INSTALL_TYPES = {
    "easa_form_one","faa_form_8130","tcca_form_one",
    "certificate_of_release_to_service","shipping_record",
    "life_limited_parts_status","work_order_contents_report",
    "mis_task_card","shop_visit_report","component_history_card",
    "modification_record","non_routine_card","routine_task_card",
}

print("=== Loading dossier pages ===")
oem_set, install_set = set(), set()
all_pages = []
with open("D:/work/openclaude/csvs/Full challenger dossier.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        try: ext = json.loads(row.get("extracted_json") or "{}")
        except: continue
        dt = ext.get("document_type")
        title = ext.get("title") or ""
        title_u = title.upper()
        is_oem = "SERIALIZATION LISTING" in title_u or "SERIALISATION LISTING" in title_u
        is_install = dt in INSTALL_TYPES
        if not (is_oem or is_install): continue

        raw_pns = set(); raw_sns = set()
        for t in (ext.get("tables") or []):
            headers = [str(h).strip().lower() if h else "" for h in (t.get("headers") or [])]
            pn_idx = sn_idx = None
            for i, h in enumerate(headers):
                if "part number" in h or "part no" in h or "p/n" in h: pn_idx = i
                if "serial" in h or "s/n" in h: sn_idx = i
            for r in (t.get("rows") or []):
                if not isinstance(r, list): continue
                if pn_idx is not None and pn_idx < len(r) and r[pn_idx] not in (None,""):
                    raw_pns.add(str(r[pn_idx]))
                if sn_idx is not None and sn_idx < len(r) and r[sn_idx] not in (None,""):
                    raw_sns.add(str(r[sn_idx]))
        for e in (ext.get("entities") or []):
            if isinstance(e, dict):
                v = e.get("value")
                et = (e.get("entity_type") or "").lower()
                if v and "part_number" in et: raw_pns.add(str(v))
                if v and ("serial" in et or et == "sn"): raw_sns.add(str(v))
        for v in (ext.get("metadata") or {}).get("part_numbers") or []:
            raw_pns.add(str(v))
        for v in (ext.get("metadata") or {}).get("serial_numbers") or []:
            raw_sns.add(str(v))

        # Also grow OEM/install exact sets for the earlier-confirmed direct match
        for p_raw in raw_pns:
            for s_raw in raw_sns:
                np = norm_basic(p_raw); ns = norm_basic(s_raw)
                if np and ns:
                    if is_oem: oem_set.add((np, ns))
                    if is_install: install_set.add((np, ns))

        all_pages.append({
            "file": row.get("file_name"), "page": row.get("page_index"),
            "doc_type": dt, "title": title, "is_oem": is_oem, "is_install": is_install,
            "agg_pns": {norm_aggressive(x) for x in raw_pns if norm_aggressive(x)},
            "agg_sns": {norm_aggressive(x) for x in raw_sns if norm_aggressive(x)},
            "raw_sns": raw_sns,
        })

print(f"  install/OEM pages: {len(all_pages)}")

print("\n=== Loading 607 rejected tuples ===")
rejected = []
with open("C:/Users/rajak/Downloads/inventory_rejected.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        if row["category"] == "no_asset_register_evidence":
            rejected.append((row["raw_pn"].strip(), row["raw_sn"].strip()))
print(f"  {len(rejected)} rejected tuples")

unrec = []
for pn, sn in rejected:
    npn = norm_basic(pn); nsn = norm_basic(sn)
    if (npn, nsn) in oem_set or (npn, nsn) in install_set:
        continue
    unrec.append((pn, sn, npn, nsn))
print(f"  Truly unrecoverable after exact-normalised match: {len(unrec)}")

print("\n=== Aggressive recovery: char-fold + SN edit-distance <=2 on install/OEM pages ===")
recovered = []
for raw_pn, raw_sn, npn, nsn in unrec:
    agg_pn = norm_aggressive(raw_pn)
    agg_sn = norm_aggressive(raw_sn)
    if not agg_pn or not agg_sn: continue
    for p in all_pages:
        # PN match: aggressive exact OR edit distance 1
        pn_hit = agg_pn in p["agg_pns"]
        if not pn_hit:
            for ax in p["agg_pns"]:
                if ax and edit_distance(agg_pn, ax) <= 1:
                    pn_hit = True; break
        if not pn_hit: continue
        # SN match: aggressive exact OR edit distance <=2
        ed = 99
        nearest_sn_agg = None
        if agg_sn in p["agg_sns"]:
            ed = 0; nearest_sn_agg = agg_sn
        else:
            for ax in p["agg_sns"]:
                if not ax: continue
                d = edit_distance(agg_sn, ax)
                if d < ed: ed = d; nearest_sn_agg = ax
                if ed == 0: break
        if ed <= 2:
            # Find the raw SN that produced the nearest match for reporting
            nearest_raw = None
            for x in p["raw_sns"]:
                if norm_aggressive(x) == nearest_sn_agg:
                    nearest_raw = x; break
            recovered.append({
                "raw_pn": raw_pn, "raw_sn": raw_sn,
                "page": p, "edit_distance": ed,
                "agg_pn": agg_pn, "agg_sn": agg_sn,
                "nearest_sn_on_page_raw": nearest_raw,
                "nearest_sn_on_page_agg": nearest_sn_agg,
            })
            break

print(f"\n=== RECOVERED via aggressive pass: {len(recovered)} ===\n")

# Group by edit distance
ed_counter = Counter(r["edit_distance"] for r in recovered)
print("Recovery by edit distance:")
for ed, n in sorted(ed_counter.items()):
    print(f"  edit_distance={ed}: {n}")
print()

# Show samples
print("=== Sample aggressive recoveries (first 30) ===")
seen = set()
for r in recovered:
    key = (r["raw_pn"], r["raw_sn"])
    if key in seen: continue
    seen.add(key)
    if len(seen) > 30: break
    p = r["page"]
    flag = "OEM" if p["is_oem"] else "INSTALL"
    print(f"  [{flag} {p['doc_type']:30}] {p['file'][:50]} p.{p['page']}")
    print(f"    rejected:    PN={r['raw_pn']!r:25}  SN={r['raw_sn']!r:25}  (agg_sn={r['agg_sn']})")
    print(f"    page has:                                       SN={r['nearest_sn_on_page_raw']!r:25}  (agg_sn={r['nearest_sn_on_page_agg']})  ed={r['edit_distance']}")
    print()

# Final summary of all 607
total_recovered = 7 + 15 + len(recovered)  # 7 OEM exact, 15 install exact, X aggressive
print("\n" + "="*60)
print(f"FINAL TALLY across all 607 rejected `no_asset_register_evidence` tuples")
print("="*60)
print(f"  Recovered: OEM serialisation listing (exact after Excel-fix):  7")
print(f"  Recovered: install-evidence doc (exact after norm):           15")
print(f"  Recovered: install/OEM (aggressive: OCR confusables + ed<=2): {len(recovered)}")
print(f"  TOTAL false negatives recoverable:                            {7 + 15 + len(recovered)}")
print(f"  Truly correct rejections (AD-fleetwide / catalogue / no install evidence anywhere): {607 - 7 - 15 - len(recovered)}")
