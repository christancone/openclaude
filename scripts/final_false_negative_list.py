"""Generate the FINAL clean list of all false-negative rejections.

For each of the 28 recoverable PN+SN tuples, show:
  - rejected (raw) PN/SN value
  - real PN/SN value as it appears on install evidence
  - char-by-char diff (for OCR substitution cases)
  - source document where the real evidence is
  - recovery method
"""
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
    if abs(len(a) - len(b)) > 3: return 99
    dp = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        prev, dp[0] = dp[0], i
        for j, cb in enumerate(b, 1):
            cur = dp[j]
            dp[j] = min(dp[j]+1, dp[j-1]+1, prev + (ca != cb))
            prev = cur
    return dp[-1]

def char_diff(misread: str, correct: str) -> list[tuple[int, str, str]]:
    """Return [(position, misread_char, correct_char)] for substitutions only."""
    diffs = []
    if not misread or not correct: return diffs
    L = min(len(misread), len(correct))
    for i in range(L):
        if misread[i] != correct[i]:
            diffs.append((i, misread[i], correct[i]))
    if len(misread) != len(correct):
        diffs.append((L, "<len-diff>", f"{len(misread)}vs{len(correct)}"))
    return diffs

INSTALL_TYPES = {
    "easa_form_one","faa_form_8130","tcca_form_one",
    "certificate_of_release_to_service","shipping_record",
    "life_limited_parts_status","work_order_contents_report",
    "mis_task_card","shop_visit_report","component_history_card",
    "modification_record","non_routine_card","routine_task_card",
}

# Load dossier and build provenance maps
oem_pn_sn_to_origin = {}
install_pn_sn_to_origin = {}
all_install_oem_pages = []
with open("D:/work/openclaude/csvs/Full challenger dossier.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        try: ext = json.loads(row.get("extracted_json") or "{}")
        except: continue
        dt = ext.get("document_type")
        title = ext.get("title") or ""
        is_oem = "SERIALIZATION LISTING" in title.upper() or "SERIALISATION LISTING" in title.upper()
        is_install = dt in INSTALL_TYPES
        if not (is_oem or is_install): continue
        raw_pns, raw_sns = set(), set()
        for t in (ext.get("tables") or []):
            headers = [str(h).strip().lower() if h else "" for h in (t.get("headers") or [])]
            pn_idx = sn_idx = None
            for i,h in enumerate(headers):
                if "part number" in h or "part no" in h or "p/n" in h: pn_idx = i
                if "serial" in h or "s/n" in h: sn_idx = i
            if pn_idx is None or sn_idx is None: continue
            for r in (t.get("rows") or []):
                if not isinstance(r,list) or pn_idx>=len(r) or sn_idx>=len(r): continue
                if r[pn_idx] in (None,"") or r[sn_idx] in (None,""): continue
                pn_raw = str(r[pn_idx]); sn_raw = str(r[sn_idx])
                raw_pns.add(pn_raw); raw_sns.add(sn_raw)
                np = norm_basic(pn_raw); ns = norm_basic(sn_raw)
                if np and ns:
                    origin = {"file": row.get("file_name"), "page": row.get("page_index"),
                              "doc_type": dt, "title": title,
                              "real_pn": pn_raw, "real_sn": sn_raw, "row": r}
                    if is_oem: oem_pn_sn_to_origin.setdefault((np, ns), origin)
                    if is_install: install_pn_sn_to_origin.setdefault((np, ns), origin)
        all_install_oem_pages.append({
            "file": row.get("file_name"), "page": row.get("page_index"),
            "doc_type": dt, "title": title, "is_oem": is_oem, "is_install": is_install,
            "raw_pns": raw_pns, "raw_sns": raw_sns,
            "agg_pns": {norm_aggressive(x) for x in raw_pns if norm_aggressive(x)},
            "agg_sns": {norm_aggressive(x) for x in raw_sns if norm_aggressive(x)},
        })

# Load rejected
rejected = []
with open("C:/Users/rajak/Downloads/inventory_rejected.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        if row["category"] == "no_asset_register_evidence":
            rejected.append((row["raw_pn"].strip(), row["raw_sn"].strip()))

# Bucket each rejected tuple
oem_recoveries = []
install_recoveries = []
aggressive_recoveries = []

for raw_pn, raw_sn in rejected:
    npn, nsn = norm_basic(raw_pn), norm_basic(raw_sn)
    # 1) Exact match in OEM listing?
    if (npn, nsn) in oem_pn_sn_to_origin:
        oem_recoveries.append({"raw_pn": raw_pn, "raw_sn": raw_sn,
                               "origin": oem_pn_sn_to_origin[(npn, nsn)]})
        continue
    # 2) Exact match in install-evidence doc?
    if (npn, nsn) in install_pn_sn_to_origin:
        install_recoveries.append({"raw_pn": raw_pn, "raw_sn": raw_sn,
                                   "origin": install_pn_sn_to_origin[(npn, nsn)]})
        continue
    # 3) Aggressive (OCR fold + edit distance) — search install/OEM pages
    apn, asn = norm_aggressive(raw_pn), norm_aggressive(raw_sn)
    if not apn or not asn: continue
    for p in all_install_oem_pages:
        pn_hit = apn in p["agg_pns"]
        if not pn_hit:
            for ax in p["agg_pns"]:
                if ax and edit_distance(apn, ax) <= 1:
                    pn_hit = True; break
        if not pn_hit: continue
        ed = 99; nearest_agg = None
        if asn in p["agg_sns"]:
            ed = 0; nearest_agg = asn
        else:
            for ax in p["agg_sns"]:
                if not ax: continue
                d = edit_distance(asn, ax)
                if d < ed: ed = d; nearest_agg = ax
                if ed == 0: break
        if ed <= 2:
            # Locate the raw SN that produced the nearest agg
            real_sn_raw = None
            for x in p["raw_sns"]:
                if norm_aggressive(x) == nearest_agg:
                    real_sn_raw = x; break
            # Locate the real PN raw on page (first match)
            real_pn_raw = None
            for x in p["raw_pns"]:
                if norm_aggressive(x) == apn:
                    real_pn_raw = x; break
            if not real_pn_raw:
                for x in p["raw_pns"]:
                    ax = norm_aggressive(x)
                    if ax and edit_distance(apn, ax) <= 1:
                        real_pn_raw = x; break
            aggressive_recoveries.append({
                "raw_pn": raw_pn, "raw_sn": raw_sn,
                "real_pn": real_pn_raw, "real_sn": real_sn_raw,
                "edit_distance": ed,
                "origin": {"file": p["file"], "page": p["page"], "doc_type": p["doc_type"], "title": p["title"]},
            })
            break

# Dedupe within each bucket on (raw_pn, raw_sn)
def dedupe(lst, key):
    seen = set(); out = []
    for r in lst:
        k = key(r)
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

oem_recoveries = dedupe(oem_recoveries, lambda r: (r["raw_pn"], r["raw_sn"]))
install_recoveries = dedupe(install_recoveries, lambda r: (r["raw_pn"], r["raw_sn"]))
aggressive_recoveries = dedupe(aggressive_recoveries, lambda r: (r["raw_pn"], r["raw_sn"]))

print("\n" + "="*100)
print("FALSE NEGATIVE LIST — installed parts that were wrongly rejected")
print("="*100)

print(f"\n--- Group A: Excel `.0` artifact (matches OEM serialisation listing exactly after `.0` strip) [{len(oem_recoveries)} tuples] ---\n")
for i, r in enumerate(oem_recoveries, 1):
    o = r["origin"]
    print(f"  {i:2}. PN: rejected={r['raw_pn']!r:25}    real(OEM)={o['real_pn']!r}")
    print(f"      SN: rejected={r['raw_sn']!r:25}    real(OEM)={o['real_sn']!r}")
    print(f"      source: {o['file']} p.{o['page']}")
    print()

print(f"\n--- Group B: Install-evidence doctype not in original whitelist (recovered via exact-norm match) [{len(install_recoveries)} tuples] ---\n")
for i, r in enumerate(install_recoveries, 1):
    o = r["origin"]
    print(f"  {i:2}. PN: rejected={r['raw_pn']!r:25}    real={o['real_pn']!r}")
    print(f"      SN: rejected={r['raw_sn']!r:25}    real={o['real_sn']!r}")
    print(f"      source: [{o['doc_type']}] {o['file']} p.{o['page']}")
    desc = ""
    if o.get("row"):
        for c in o["row"]:
            if isinstance(c, str) and any(c2.isalpha() for c2 in c) and len(c) > 8 and "AMM" not in c:
                desc = c; break
        if desc: print(f"      part description: {desc[:90]}")
    print()

print(f"\n--- Group C: OCR character substitutions (aggressive normalisation, edit distance <=2) [{len(aggressive_recoveries)} tuples] ---\n")
for i, r in enumerate(aggressive_recoveries, 1):
    o = r["origin"]
    print(f"  {i:2}. PN: rejected={r['raw_pn']!r:25}    real={r.get('real_pn')!r}")
    print(f"      SN: rejected={r['raw_sn']!r:25}    real={r.get('real_sn')!r}    edit_distance={r['edit_distance']}")
    # Char-by-char diff
    rejected_sn_norm = norm_aggressive(r["raw_sn"]) or ""
    real_sn_norm = norm_aggressive(r.get("real_sn") or "") or ""
    print(f"      norm: rejected_sn={rejected_sn_norm}   real_sn={real_sn_norm}")
    diffs = char_diff(rejected_sn_norm, real_sn_norm)
    if diffs:
        ds = ", ".join(f"pos {p}: misread '{m}' should be '{c}'" for (p, m, c) in diffs)
        print(f"      char diff: {ds}")
    print(f"      source: [{o['doc_type']}] {o['file']} p.{o['page']}")
    print()

total = len(oem_recoveries) + len(install_recoveries) + len(aggressive_recoveries)
print("\n" + "="*100)
print(f"TOTAL false negatives recovered: {total} (of 607 rejected `no_asset_register_evidence` tuples)")
print(f"  Group A (Excel .0):           {len(oem_recoveries)}")
print(f"  Group B (install doctype):    {len(install_recoveries)}")
print(f"  Group C (OCR substitutions):  {len(aggressive_recoveries)}")
print(f"True correct rejections: {607 - total}")
print("="*100)
