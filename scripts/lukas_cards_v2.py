"""Lukas component cards v2 — adds Component Name and Is_Installed columns."""
import csv, json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from collections import defaultdict
csv.field_size_limit(50_000_000)

# Each row: (group, pn, sn, name, install_status_override)
# install_status_override: "Installed" / "Removed" / "Unknown" / "N/A — Consumable"
# Set to None to auto-derive from evidence.
COMPONENTS = [
    ("A","500097","112718038","KIT, FIRST AID","Installed"),
    ("A","016263-01","0784","LEVEL SENSOR PROBE","Installed"),
    ("A","BDP3009-001-001","000754","PLAYER, BLU-RAY DISC","Installed"),
    ("A","0059-0008-3","286461","VALVE, CHECK","Installed"),
    ("A","123790-1-1","1585","VALVE, CHECK","Installed"),
    ("A","3202076-1-1","1418","VALVE, CHECK","Installed"),
    ("A","3202062-1-1","2852","VALVE, CHECK, PRESS BKLHD","Installed"),
    ("A","3202062-1-1","2858","VALVE, CHECK, PRESS BKLHD","Installed"),
    ("B","1015F9A-C4-1-150","10EUL-82792","Winslow life raft","Removed"),
    ("B","031-614-0","8065S00381","TIRE 18X4.4/12/210","Installed"),
    ("B","70721725-5","18-156101-03787","APU TURBINE ROTOR (LLP)","Installed"),
    ("B","1756-3","0905200213796","MAIN NICAD BATTERY (Saft)","Removed"),
    ("B","601R59041-3","09052000A3208","MAIN BATTERY (Bombardier-ref)","Installed"),
    ("B","228-50162-565","NSN","BRACKET","Installed"),
    ("B","601R92386-3","SN1854","L/H HSTA TRUNNION SUPPORT (LLP)","Installed"),
    ("B","601R92386-3","SN1841","R/H HSTA TRUNNION SUPPORT (LLP)","Installed"),
    ("B","228-56334-103","2114592501","DUCT ASSY","Installed"),
    ("B","625692-2","089C-1231","FAN SENSOR","Installed"),
    ("B","40962-1","SO2011084","LABEL (Bombardier batch)","Installed"),
    ("B","600-59199-9","DT6325N","LATERAL ACCELEROMETER","Installed"),
    ("B","A3372-1","NSN","STAY BRACE","Installed"),
    ("B","54303-6C35","M3833","STALL PROTECTION COMPUTER","Installed"),
    ("B","60-1321-1","26558","EMERGENCY LIGHT POWER SUPPLY","Installed"),
    ("C","031-614-0","8065S00381","TIRE 18X4.4/12/210 (dup of #10)","Installed"),
    ("C","024657-000","8905200213796","BATTERY (MAIN) — different unit, ed=2","Unknown"),
    ("C","024453-000","090520021095E","NICAD BATTERY (Saft)","Installed"),
    ("C","024453-000","090520021095E","NICAD BATTERY (Saft) — OCR variant 2","Installed"),
    ("C","1756-3","0905200213796","MAIN NICAD BATTERY (Saft) — OCR variant 3","Removed"),
    ("C","C16786MA01","C16786026816","INTEGRATED STANDBY INSTRUMENT (ISI)","Installed"),
    ("C","MS20995F32","3158","LOCKWIRE (consumable)","N/A — Consumable"),
    ("C","600-59199-9","DT6325N","LATERAL ACCELEROMETER (dup of #20)","Installed"),
    ("C","2100-2245-22","001279641","FLIGHT DATA RECORDER","Installed"),
]

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
                 "modification_record","non_routine_card","routine_task_card",
                 "airframe_logbook","engine_logbook"}

DATE_RE_ISO    = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
HOURS_RE       = re.compile(r"\b(\d{1,5}):(\d{2})\b")
CYCLES_RE      = re.compile(r"\b(\d{1,5})\s*(?:LDG|CYC|CYCLES|landings?)\b", re.IGNORECASE)

print("Loading dossier...", file=sys.stderr)
PAGES = []
with open("D:/work/openclaude/csvs/Full challenger dossier.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        try: ext = json.loads(row.get("extracted_json") or "{}")
        except: continue
        dt = ext.get("document_type"); title = ext.get("title") or ""
        is_oem = "SERIALIZATION LISTING" in title.upper() or "SERIALISATION LISTING" in title.upper()
        if not (is_oem or dt in INSTALL_TYPES):
            continue
        raw_pns, raw_sns = set(), set()
        rows = []
        for t in (ext.get("tables") or []):
            headers = [str(h).strip().lower() if h else "" for h in (t.get("headers") or [])]
            idx = {}
            for i,h in enumerate(headers):
                if "part number" in h or "part no" in h or "p/n" in h: idx.setdefault("pn", i)
                if "serial" in h or "s/n" in h: idx.setdefault("sn", i)
                if "descr" in h: idx.setdefault("desc", i)
                if "qty" in h or "quantity" in h: idx.setdefault("qty", i)
                if "ata" in h: idx.setdefault("ata", i)
                if "location" in h or "loc id" in h: idx.setdefault("loc", i)
                if "mod" in h and "status" in h: idx.setdefault("mod_status", i)
                if "status" in h or "work" in h: idx.setdefault("status", i)
                if "manufacturing" in h and "date" in h: idx.setdefault("mfg_date", i)
            for r in (t.get("rows") or []):
                if not isinstance(r, list): continue
                cell = lambda k: (str(r[idx[k]]).strip() if idx.get(k) is not None and idx[k] < len(r) and r[idx[k]] not in (None,"") else None)
                pn = cell("pn"); sn = cell("sn")
                if pn: raw_pns.add(pn)
                if sn: raw_sns.add(sn)
                if pn or sn:
                    rows.append({"pn":pn,"sn":sn,"desc":cell("desc"),"qty":cell("qty"),
                                 "ata":cell("ata"),"loc":cell("loc"),
                                 "mod_status":cell("mod_status"),"status":cell("status"),
                                 "mfg_date":cell("mfg_date"),"full":r})
        hf = ext.get("header_fields") or {}
        block11 = tracking_no = signer = cert_no = release_date = None
        block8_pn = block10_sn = None
        for k, v in (hf.items() if isinstance(hf, dict) else []):
            if not isinstance(v, str): continue
            kl = k.lower()
            if "11" in kl and "status" in kl: block11 = v
            if "3" in kl and ("tracking" in kl or "form" in kl): tracking_no = v
            if "13d" in kl or "14d" in kl: signer = signer or v
            if "13c" in kl or "14c" in kl: cert_no = cert_no or v
            if ("13e" in kl or "14e" in kl) and "date" in kl: release_date = release_date or v
            if "8. part" in kl or "8.part" in kl: block8_pn = v
            if "10. serial" in kl or "10.serial" in kl or "10. ser" in kl: block10_sn = v
        if block8_pn:
            for p in re.split(r"[\n,;]+", block8_pn):
                p = p.strip()
                if p: raw_pns.add(p)
        if block10_sn:
            for s in re.split(r"[\n,;]+", block10_sn):
                s = s.strip()
                if s: raw_sns.add(s)
        PAGES.append({
            "file": row.get("file_name"), "page": int(row.get("page_index") or 0),
            "doc_type": dt, "title": title, "is_oem": is_oem,
            "raw_pns": raw_pns, "raw_sns": raw_sns, "rows": rows,
            "block_11": block11, "tracking_no": tracking_no,
            "signer": signer, "cert_no": cert_no, "release_date": release_date,
        })
print(f"  {len(PAGES)} install/OEM pages indexed", file=sys.stderr)

mfg_date = "2019-02-20"  # CL650-6134 delivery per Lukas playbook (HB-JTZ, Bombardier)
current_ac_hours = "559:25"   # per Lukas's removal-anchor reading
current_ac_cycles = "231"

def matches(raw, target_basic, target_agg, tier="aggressive"):
    if not raw: return False
    n = norm_basic(raw)
    if n == target_basic: return True
    a = norm_aggressive(raw)
    if not a: return False
    if a == target_agg: return True
    if tier == "aggressive" and edit_distance(a, target_agg) <= 1: return True
    return False

def find_hits(pn, sn):
    npn = norm_basic(pn); nsn = norm_basic(sn)
    apn = norm_aggressive(pn); asn = norm_aggressive(sn)
    hits = []
    for p in PAGES:
        if not any(matches(x, npn, apn) for x in p["raw_pns"]):
            continue
        for r in p["rows"]:
            rpn = r.get("pn"); rsn = r.get("sn")
            if not rpn: continue
            if matches(rpn, npn, apn) and (
                (rsn and matches(rsn, nsn, asn)) or
                (rsn is None and len(p["rows"]) == 1)
            ):
                hits.append({"page": p, "row": r})
    return hits

# Helpers for Lukas's 11 fields
def compute_card(pn, sn, name_hint):
    hits = find_hits(pn, sn)
    born_on = False
    location = None; ata = None; real_desc = name_hint; mod_status_seen = []
    alt_pns = set()
    form1_certs = []  # by date order
    llp_limit_h = None; llp_remaining_h = None
    work_events = []  # install / removal traces

    for h in hits:
        p = h["page"]; r = h["row"]
        if p["is_oem"]:
            born_on = True
            if r.get("loc"): location = r["loc"]
            if r.get("ata"): ata = ata or r["ata"]
            if r.get("desc"): real_desc = r["desc"]
            if r.get("mod_status"): mod_status_seen.append(r["mod_status"])
            if r.get("pn") and norm_basic(r["pn"]) != norm_basic(pn):
                alt_pns.add(r["pn"])
        if p["doc_type"] == "life_limited_parts_status":
            # parse limits from row
            full = r.get("full") or []
            for i, c in enumerate(full):
                if isinstance(c, str) and c.upper() in ("HRS","HOURS"):
                    if i+2 < len(full) and full[i+2]:
                        try: llp_limit_h = int(str(full[i+2]).replace(",",""))
                        except: pass
                    if i+7 < len(full) and full[i+7]:
                        try: llp_remaining_h = int(str(full[i+7]).replace(",",""))
                        except: pass
            if r.get("desc"): real_desc = r["desc"]
        if p["doc_type"] in ("easa_form_one","faa_form_8130","tcca_form_one"):
            cert = {
                "date": p.get("release_date") or "",
                "tracking_no": p.get("tracking_no") or "",
                "signer": p.get("signer") or "",
                "status": (r.get("status") or p.get("block_11") or "").upper().strip(),
                "doc_type": p["doc_type"], "file": p["file"], "page": p["page"],
            }
            form1_certs.append(cert)
        if p["doc_type"] in ("work_order_contents_report","mis_task_card","certificate_of_release_to_service","routine_task_card"):
            work_events.append({"file": p["file"], "page": p["page"], "doc_type": p["doc_type"]})

    overhauls = sorted([c for c in form1_certs if "OVERHAUL" in c["status"]], key=lambda c: c.get("date") or "")
    repairs   = sorted([c for c in form1_certs if "REPAIR"   in c["status"]], key=lambda c: c.get("date") or "")
    inspects  = sorted([c for c in form1_certs if any(t in c["status"] for t in ("INSPECT","TEST","SERVICEABLE"))], key=lambda c: c.get("date") or "")
    news      = [c for c in form1_certs if "NEW" in c["status"]]
    last_visit = (overhauls + repairs + inspects + news)[-1] if (overhauls or repairs or inspects or news) else None

    # Field assembly
    if born_on:
        f1 = mfg_date
        f2h = "0:00"; f2c = "0"
        f6 = "Birth Form 1 — OEM serialisation listing"
    elif news:
        c = news[0]
        f1 = c["date"] or "Unknown"
        f2h = "Unknown"; f2c = "Unknown"
        f6 = f"Tracking {c['tracking_no']} — {c['doc_type']} — {c['date']}"
    elif inspects or repairs or overhauls:
        c = (inspects + repairs + overhauls)[0]
        f1 = c.get("date") or "Unknown"
        f2h = "Unknown"; f2c = "Unknown"
        f6 = f"Tracking {last_visit['tracking_no']} — {last_visit['doc_type']} — {last_visit['date']}"
    else:
        f1 = "GAP_IN_DOSSIER"
        f2h = "GAP"; f2c = "GAP"
        f6 = "GAP_IN_DOSSIER"

    f3 = "; ".join(f"{c.get('date') or '?'} (cert {c['tracking_no'] or '?'})" for c in overhauls) if overhauls else "NIL"
    f4 = "; ".join(f"{c.get('date') or '?'} (cert {c['tracking_no'] or '?'})" for c in repairs)   if repairs   else "NIL"
    f5 = ", ".join(sorted(set(mod_status_seen))) if mod_status_seen else "NIL"
    f7 = "Read from techlog at cert date" if last_visit and not born_on else "N/A — never been to shop" if born_on else "GAP"
    f8 = "N/A — never overhauled" if not overhauls and (born_on or news) else "Compute from techlog (current AC TT − overhaul AC TT)" if overhauls else "N/A"
    f9 = current_ac_hours if born_on and not (overhauls or repairs) else "Compute (sum of install→removal arcs)"
    if llp_limit_h:
        f10 = f"Limit {llp_limit_h}H; remaining {llp_remaining_h if llp_remaining_h else '?'}H"
    else:
        f10 = "N/A (non-LLP / on-condition)"
    alt_str = ", ".join(sorted(alt_pns)) if alt_pns else ""
    category = "LLP" if llp_limit_h or "(LLP)" in (real_desc or "") else \
               "Consumable" if "lockwire" in (real_desc or "").lower() else \
               "Non-serialised" if sn == "NSN" else "Unknown"
    f11 = f"PN: {pn}" + (f" (alt: {alt_str})" if alt_str else "") + \
          f" | SN: {sn} | Desc: {real_desc} | Pos: {location or 'Unknown'} | Category: {category}"
    return {
        "real_desc": real_desc, "born_on": born_on,
        "f1": f1, "f2h": f2h, "f2c": f2c, "f3": f3, "f4": f4, "f5": f5,
        "f6": f6, "f7": f7, "f8": f8, "f9": f9, "f10": f10, "f11": f11,
        "n_form1": len(form1_certs), "n_work_events": len(work_events),
        "files": sorted({h["page"]["file"] for h in hits if h["page"]["file"]})[:5],
    }

# Build output
out_csv = "D:/work/openclaude/dumps/lukas_component_cards_v2.csv"
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow([
        "#","Group","Component_Name","PN","SN","Is_Installed","Born_on_Aircraft",
        "1_Date_of_First_Installation",
        "2_Hours_at_First_Install",
        "2_Cycles_at_First_Install",
        "3_Overhaul_History",
        "4_Repair_History",
        "5_Modification_Status_SBs",
        "6_Last_Parts_Certificate",
        "7_TSN_at_Last_Shop_Visit",
        "8_Accrued_Since_Last_Overhaul",
        "9_Current_Part_Total_Time",
        "10_Remaining_to_Overhaul_or_Scrap",
        "11_Part_Identity",
        "n_Form1s_found",
        "n_Work_Events_found",
        "Evidence_Source_Files",
    ])
    for i, (grp, pn, sn, name, status) in enumerate(COMPONENTS, 1):
        c = compute_card(pn, sn, name)
        w.writerow([
            i, grp, name, pn, sn, status,
            "Yes" if c["born_on"] else "No",
            c["f1"], c["f2h"], c["f2c"], c["f3"], c["f4"], c["f5"],
            c["f6"], c["f7"], c["f8"], c["f9"], c["f10"], c["f11"],
            c["n_form1"], c["n_work_events"], ", ".join(c["files"]),
        ])

print(f"Wrote {out_csv}")
print(f"Total components: {len(COMPONENTS)}")

# Summary by Is_Installed
from collections import Counter
status_counter = Counter(s for *_, s in COMPONENTS)
for s, n in status_counter.most_common():
    print(f"  Is_Installed = '{s}': {n}")
