"""Generate the comprehensive 31-row false-negative table with all metadata."""
import csv, json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from collections import defaultdict
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
    return s.translate(str.maketrans({"O":"0","I":"1","L":"1","S":"5","B":"8","Z":"2","G":"6"}))

def edit_distance(a, b):
    if not a or not b: return 99
    if a == b: return 0
    if abs(len(a)-len(b)) > 3: return 99
    dp = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, cb in enumerate(b, 1):
            cur = dp[j]; dp[j] = min(dp[j]+1, dp[j-1]+1, prev+(ca!=cb)); prev = cur
    return dp[-1]

INSTALL_TYPES = {"easa_form_one","faa_form_8130","tcca_form_one",
                 "certificate_of_release_to_service","shipping_record",
                 "life_limited_parts_status","work_order_contents_report",
                 "mis_task_card","shop_visit_report","component_history_card",
                 "modification_record","non_routine_card","routine_task_card"}

# Pull each page's tables and the row-context (description, qty, ata, etc.)
class PageInfo:
    __slots__ = ("file","page","doc_type","title","is_oem","is_install","rows","agg_pns","agg_sns")
    def __init__(self, file, page, doc_type, title, is_oem, is_install):
        self.file, self.page, self.doc_type, self.title = file, page, doc_type, title
        self.is_oem, self.is_install = is_oem, is_install
        self.rows = []   # list of {pn, sn, desc, qty, ata, location, full_row}
        self.agg_pns = set()
        self.agg_sns = set()

all_pages = []
with open("D:/work/openclaude/csvs/Full challenger dossier.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        try: ext = json.loads(row.get("extracted_json") or "{}")
        except: continue
        dt = ext.get("document_type"); title = ext.get("title") or ""
        is_oem = "SERIALIZATION LISTING" in title.upper() or "SERIALISATION LISTING" in title.upper()
        is_install = dt in INSTALL_TYPES
        if not (is_oem or is_install): continue
        pi = PageInfo(row.get("file_name"), row.get("page_index"), dt, title, is_oem, is_install)
        for t in (ext.get("tables") or []):
            headers = [str(h).strip().lower() if h else "" for h in (t.get("headers") or [])]
            idx = {}
            for i, h in enumerate(headers):
                if "part number" in h or "part no" in h or "p/n" in h: idx.setdefault("pn", i)
                if "serial" in h or "s/n" in h: idx.setdefault("sn", i)
                if "descr" in h: idx.setdefault("desc", i)
                if "qty" in h or "quantity" in h: idx.setdefault("qty", i)
                if "ata" in h: idx.setdefault("ata", i)
                if "location" in h: idx.setdefault("loc", i)
            for r in (t.get("rows") or []):
                if not isinstance(r, list): continue
                cell = lambda k: (str(r[idx[k]]).strip() if idx.get(k) is not None and idx[k] < len(r) and r[idx[k]] not in (None,"") else None)
                pn = cell("pn"); sn = cell("sn")
                if not (pn and sn): continue
                pi.rows.append({"pn": pn, "sn": sn, "desc": cell("desc"),
                                 "qty": cell("qty"), "ata": cell("ata"),
                                 "loc": cell("loc"), "full_row": r})
                pi.agg_pns.add(norm_aggressive(pn))
                pi.agg_sns.add(norm_aggressive(sn))
        all_pages.append(pi)

# Load rejected
rejected = []
with open("C:/Users/rajak/Downloads/inventory_rejected.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        if row["category"] == "no_asset_register_evidence":
            rejected.append((row["raw_pn"].strip(), row["raw_sn"].strip()))

# Find recovery for each rejected tuple
def lookup_in_pages(raw_pn, raw_sn):
    npn, nsn = norm_basic(raw_pn), norm_basic(raw_sn)
    apn, asn = norm_aggressive(raw_pn), norm_aggressive(raw_sn)
    # 1) basic exact match (OEM listing preferred, then install)
    best_oem = None; best_install = None
    for p in all_pages:
        for r in p.rows:
            rpn = norm_basic(r["pn"]); rsn = norm_basic(r["sn"])
            if rpn == npn and rsn == nsn:
                target = {"page": p, "row": r, "method": "basic_exact",
                          "real_pn": r["pn"], "real_sn": r["sn"], "ed": 0}
                if p.is_oem and not best_oem: best_oem = target
                if p.is_install and not best_install: best_install = target
    if best_oem: return ("A", best_oem)
    if best_install: return ("B", best_install)
    # 2) aggressive (OCR-fold + edit distance <=2)
    if not (apn and asn): return (None, None)
    best = None
    for p in all_pages:
        # PN agg match or ed=1
        pn_hit = apn in p.agg_pns
        if not pn_hit:
            for x in p.agg_pns:
                if x and edit_distance(apn, x) <= 1: pn_hit = True; break
        if not pn_hit: continue
        for r in p.rows:
            r_apn = norm_aggressive(r["pn"]); r_asn = norm_aggressive(r["sn"])
            if not (r_apn and r_asn): continue
            # PN check
            pned = edit_distance(apn, r_apn)
            if pned > 1: continue
            # SN check
            sned = edit_distance(asn, r_asn)
            if sned <= 2:
                cand = {"page": p, "row": r, "method": "aggressive",
                        "real_pn": r["pn"], "real_sn": r["sn"],
                        "ed": sned, "pn_ed": pned}
                if best is None or cand["ed"] < best["ed"]:
                    best = cand
                if cand["ed"] == 0: break
        if best and best["ed"] == 0: break
    if best: return ("C", best)
    return (None, None)

# Collect recovered list, dedupe on (raw_pn, raw_sn)
recovered = []
seen = set()
for raw_pn, raw_sn in rejected:
    key = (raw_pn, raw_sn)
    if key in seen: continue
    group, info = lookup_in_pages(raw_pn, raw_sn)
    if not info: continue
    seen.add(key)
    recovered.append({"raw_pn": raw_pn, "raw_sn": raw_sn, "group": group, **info})

# Sort: A then B then C, then by part description
recovered.sort(key=lambda r: (r["group"], (r["row"]["desc"] or "")))

# Emit table — CSV + Markdown
out_csv = "D:/work/openclaude/dumps/false_negatives_31.csv"
out_md  = "D:/work/openclaude/dumps/false_negatives_31.md"

with open(out_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["#","Group","PN","SN","Description","Qty","ATA","Location",
                "Source Doc","Source Type","Page","Recovery Method","Edit Dist",
                "Rejected PN (original OCR)","Rejected SN (original OCR)",
                "Audit Notes"])
    for i, r in enumerate(recovered, 1):
        p = r["page"]; row = r["row"]
        method_label = {
            "basic_exact": "Exact match after norm",
            "aggressive": f"OCR fold + edit distance {r.get('ed','?')}"
        }[r["method"]]
        notes = ""
        # Manual annotations for known special cases
        rpn_n = norm_basic(r["real_pn"]); rsn_n = norm_basic(r["real_sn"])
        if r["real_pn"] == "MS20995F32":
            notes = "ACTUALLY CORRECTLY REJECTED — LOCKWIRE consumable (qty in metres). Don't promote to Component."
        elif row.get("desc") and "LIFE LIMIT" in (row.get("desc") or "").upper():
            notes = "LIFE-LIMITED PART — Level-1 finding if missed by Phase 7."
        elif r["real_sn"] == "NSN":
            notes = "Non-serialised (NSN = Not Subject to Numbering). Batch-cert pattern."
        elif r["group"] == "C" and r.get("ed", 99) == 2:
            notes = "Medium confidence (ed=2). Needs human review — may be different physical unit."
        elif r["group"] == "C" and r.get("ed", 0) <= 1:
            notes = f"OCR substitution: norm_rejected={norm_aggressive(r['raw_sn'])} norm_real={norm_aggressive(r['real_sn'])}"
        w.writerow([i, r["group"], r["real_pn"], r["real_sn"],
                    row.get("desc") or "", row.get("qty") or "",
                    row.get("ata") or "", row.get("loc") or "",
                    p.file, p.doc_type, p.page, method_label,
                    r.get("ed",""), r["raw_pn"], r["raw_sn"], notes])

with open(out_md, "w", encoding="utf-8") as f:
    f.write("# Comprehensive False-Negative List (31 candidate Components)\n\n")
    f.write(f"Generated 2026-05-22.  Source: 607 `no_asset_register_evidence` rejections in inventory_rejected.csv\n\n")
    f.write("| # | Group | PN | SN | Description | Qty | ATA | Location | Source Doc | Doc Type | Page | Recovery | Rejected (OCR) PN | Rejected (OCR) SN | Notes |\n")
    f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    for i, r in enumerate(recovered, 1):
        p = r["page"]; row = r["row"]
        method_label = "Exact" if r["method"]=="basic_exact" else f"Fold+ed{r.get('ed','?')}"
        notes = ""
        if r["real_pn"] == "MS20995F32":
            notes = "⚠ REVISED: lockwire consumable — not a Component (qty in metres)"
        elif row.get("desc") and "LIFE LIMIT" in (row.get("desc") or "").upper():
            notes = "🛑 LIFE-LIMITED PART"
        elif r["real_sn"] == "NSN":
            notes = "Non-serialised (NSN)"
        elif r["group"] == "C" and r.get("ed", 99) == 2:
            notes = f"⚠ ed=2 — needs human review"
        elif r["group"] == "C" and r.get("ed", 0) <= 1:
            notes = f"OCR substitution"
        file_short = (p.file or "")[:55]
        desc = (row.get("desc") or "")[:50]
        f.write(f"| {i} | {r['group']} | `{r['real_pn']}` | `{r['real_sn']}` | {desc} | {row.get('qty') or ''} | {row.get('ata') or ''} | {row.get('loc') or ''} | {file_short} | {p.doc_type} | {p.page} | {method_label} | `{r['raw_pn']}` | `{r['raw_sn']}` | {notes} |\n")
    f.write(f"\nTotal: {len(recovered)} false negatives recovered\n")

print(f"Wrote {out_csv} and {out_md}")
print(f"Total recovered: {len(recovered)}")
# Print compact summary to stdout
print("\nGroup counts:")
from collections import Counter
gc = Counter(r["group"] for r in recovered)
for g, n in sorted(gc.items()): print(f"  Group {g}: {n}")
