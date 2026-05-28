"""For each of the 32 recovered Components, walk the full dossier and fill
the 11 Lukas component-card fields. Output a CSV.

11 fields per playbook Ch. 6 "master extraction template":
  1.  Date of first installation
  2.  Part total time at first installation (TSN/CSN_at_install)
  3.  Overhaul history (list of date / AC hrs / AC cyc per overhaul)
  4.  Repair history
  5.  Modification history (SB list / Mod Status)
  6.  Parts Certificate for last repair/overhaul/shop visit
  7.  Part total time at last repair/overhaul/shop visit
  8.  Accrued time since last overhaul
  9.  Current part total time
  10. Remaining time until next overhaul or scrap
  11. Part Identity (PN + alternates + SN + Description + Position + Category)
"""
import csv, json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from collections import defaultdict
csv.field_size_limit(50_000_000)

# ---- The 32 components recovered (from earlier analysis) ----
COMPONENTS = [
    # (group, pn, sn, description_hint)
    ("A", "500097", "112718038", "KIT, FIRST AID"),
    ("A", "016263-01", "0784", "LEVEL SENSOR PROBE"),
    ("A", "BDP3009-001-001", "000754", "PLAYER, BLU-RAY DISC"),
    ("A", "0059-0008-3", "286461", "VALVE, CHECK"),
    ("A", "123790-1-1", "1585", "VALVE, CHECK"),
    ("A", "3202076-1-1", "1418", "VALVE, CHECK"),
    ("A", "3202062-1-1", "2852", "VALVE, CHECK, PRESS BKLHD"),
    ("A", "3202062-1-1", "2858", "VALVE, CHECK, PRESS BKLHD"),
    ("B", "1015F9A-C4-1-150", "10EUL-82792", "Winslow life raft"),
    ("B", "031-614-0", "8065S00381", "TIRE 18X4.4/12/210"),
    ("B", "70721725-5", "18-156101-03787", "APU TURBINE ROTOR (LLP)"),
    ("B", "1756-3", "0905200213796", "MAIN NICAD BATTERY (Saft)"),
    ("B", "601R59041-3", "09052000A3208", "MAIN BATTERY (Bombardier-ref)"),
    ("B", "228-50162-565", "NSN", "BRACKET"),
    ("B", "601R92386-3", "SN1854", "L/H HSTA TRUNNION SUPPORT (LLP)"),
    ("B", "601R92386-3", "SN1841", "R/H HSTA TRUNNION SUPPORT (LLP)"),
    ("B", "228-56334-103", "2114592501", "DUCT ASSY"),
    ("B", "625692-2", "089C-1231", "FAN SENSOR"),
    ("B", "40962-1", "SO2011084", "LABEL (Bombardier batch)"),
    ("B", "600-59199-9", "DT6325N", "LATERAL ACCELEROMETER"),
    ("B", "A3372-1", "NSN", "STAY BRACE"),
    ("B", "54303-6C35", "M3833", "STALL PROTECTION COMPUTER"),
    ("B", "60-1321-1", "26558", "EMERGENCY LIGHT POWER SUPPLY"),
    ("C", "031-614-0", "8065S00381", "TIRE (dup of #10)"),
    ("C", "024657-000", "8905200213796", "BATTERY (MAIN)"),
    ("C", "024453-000", "090520021095E", "NICAD BATTERY (Saft) — variant 1"),
    ("C", "024453-000", "090520021095E", "NICAD BATTERY (Saft) — variant 2"),
    ("C", "1756-3", "0905200213796", "MAIN NICAD BATTERY (Saft) — variant 3"),
    ("C", "C16786MA01", "C16786026816", "INTEGRATED STANDBY INSTRUMENT (ISI)"),
    ("C", "MS20995F32", "3158", "LOCKWIRE [REVISED: consumable; not a Component]"),
    ("C", "600-59199-9", "DT6325N", "LATERAL ACCELEROMETER (dup of #20)"),
    ("C", "2100-2245-22", "001279641", "FLIGHT DATA RECORDER"),
]

# ---- Normalisers ----
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

# ---- Pass 1: stream the dossier once, build per-page index ----
print("Loading dossier CSV...", file=sys.stderr)
PAGES = []  # list of {file, page, doc_type, title, raw_pns, raw_sns, rows, dates, mod_status_texts, fields}

INSTALL_TYPES = {"easa_form_one","faa_form_8130","tcca_form_one",
                 "certificate_of_release_to_service","shipping_record",
                 "life_limited_parts_status","work_order_contents_report",
                 "mis_task_card","shop_visit_report","component_history_card",
                 "modification_record","non_routine_card","routine_task_card",
                 "airframe_logbook","engine_logbook","engineering_order",
                 "service_bulletin","service_bulletin_compliance",
                 "airworthiness_directive_compliance","ad_compliance",
                 "technical_journey_log","sb_status_report",
                 "modification_record"}

DATE_RE_ISO    = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_RE_DMMMY  = re.compile(r"\b(\d{1,2})[\-\/\s](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[\-\/\s](\d{4})\b", re.IGNORECASE)
HOURS_RE       = re.compile(r"\b(\d{1,5}):(\d{2})\b")
CYCLES_RE      = re.compile(r"\b(\d{1,5})\s*(?:LDG|CYC|CYCLES|landings?)\b", re.IGNORECASE)
STATUS_RE      = re.compile(r"\b(OVERHAULED|REPAIRED|INSPECTED|TESTED|NEW|SERVICEABLE|MODIFIED|AS[-\s]?REMOVED)\b", re.IGNORECASE)
SB_LIST_RE     = re.compile(r"(?:Mod\s*Status|SB\s*Config|Outgoing\s*SB|SBs?\s*Complied):\s*([\d,\s/]+)", re.IGNORECASE)

MONTH = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
         "jul":"07","aug":"08","sep":"09","sept":"09","oct":"10","nov":"11","dec":"12"}

def parse_dates(text):
    if not text: return []
    out = []
    for m in DATE_RE_ISO.finditer(text):
        out.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    for m in DATE_RE_DMMMY.finditer(text):
        mm = MONTH.get(m.group(2).lower()[:3])
        if mm:
            out.append(f"{m.group(3)}-{mm}-{int(m.group(1)):02d}")
    return out

with open("D:/work/openclaude/csvs/Full challenger dossier.csv", "r", encoding="utf-8", errors="replace") as f:
    for row in csv.DictReader(f):
        try: ext = json.loads(row.get("extracted_json") or "{}")
        except: continue
        dt = ext.get("document_type")
        title = ext.get("title") or ""
        is_oem = "SERIALIZATION LISTING" in title.upper() or "SERIALISATION LISTING" in title.upper()
        is_install = dt in INSTALL_TYPES

        page_text = json.dumps({
            "title": title,
            "tables": ext.get("tables"),
            "header_fields": ext.get("header_fields"),
            "metadata": ext.get("metadata"),
            "sections": ext.get("sections"),
            "events": ext.get("events"),
        })

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
                if "inspection" in h and "date" in h: idx.setdefault("insp_date", i)
                if "expiration" in h or "expir" in h: idx.setdefault("exp_date", i)
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
                                 "mfg_date":cell("mfg_date"),"insp_date":cell("insp_date"),
                                 "exp_date":cell("exp_date"),"full":r})
        # also harvest from header_fields and entities
        hf = ext.get("header_fields") or {}
        block11 = None; tracking_no = None; signer = None; cert_no = None; release_date = None
        for k, v in hf.items() if isinstance(hf, dict) else []:
            if not isinstance(v, str): continue
            kl = k.lower()
            if "11" in kl and "status" in kl: block11 = v
            if "3" in kl and ("tracking" in kl or "form" in kl): tracking_no = v
            if "13d" in kl or "14d" in kl: signer = signer or v
            if "13c" in kl or "14c" in kl: cert_no = cert_no or v
            if ("13e" in kl or "14e" in kl) and "date" in kl: release_date = release_date or v
            if "8. part" in kl:
                for p in re.split(r"[\n,;]+", v):
                    p = p.strip()
                    if p: raw_pns.add(p)
            if "10. serial" in kl or "10.serial" in kl:
                for s in re.split(r"[\n,;]+", v):
                    s = s.strip()
                    if s: raw_sns.add(s)

        PAGES.append({
            "file": row.get("file_name"), "page": int(row.get("page_index") or 0),
            "doc_type": dt, "title": title, "is_oem": is_oem, "is_install": is_install,
            "raw_pns": raw_pns, "raw_sns": raw_sns, "rows": rows,
            "page_text": page_text,
            "block_11": block11, "tracking_no": tracking_no,
            "signer": signer, "cert_no": cert_no, "release_date": release_date,
            "events": ext.get("events") or [],
        })

print(f"Loaded {len(PAGES)} pages", file=sys.stderr)

# Find aircraft current AC hours/cycles from the latest technical_journey_log page
latest_tjl_hours = None
latest_tjl_cycles = None
latest_tjl_date = None
for p in PAGES:
    if p["doc_type"] not in ("technical_journey_log","airframe_logbook"): continue
    # Look for the last numerical AC hours/cycles + date triple
    for r in p["rows"]:
        for cell in (r["full"] or []):
            if not isinstance(cell, str): continue
            mh = HOURS_RE.search(cell)
            mc = CYCLES_RE.search(cell)
            if mh:
                try:
                    h = int(mh.group(1)) + int(mh.group(2))/60
                    if not latest_tjl_hours or h > latest_tjl_hours:
                        latest_tjl_hours = h
                        latest_tjl_hours_str = mh.group(0)
                except: pass
    # also try a heuristic from page_text
    text = p["page_text"]
    for mh in HOURS_RE.finditer(text):
        try:
            h = int(mh.group(1)) + int(mh.group(2))/60
            if not latest_tjl_hours or h > latest_tjl_hours:
                latest_tjl_hours = h
        except: pass

# fallback: known from playbook
if not latest_tjl_hours:
    latest_tjl_hours = 559 + 25/60
print(f"Current AC hours (estimate): {int(latest_tjl_hours)}:{int(round((latest_tjl_hours-int(latest_tjl_hours))*60)):02d}", file=sys.stderr)

# Aircraft manufacture date — from delivery_acceptance_certificate or first technical_journey_log
mfg_date = None
for p in PAGES:
    if p["doc_type"] == "delivery_acceptance_certificate":
        dates = parse_dates(p["page_text"])
        if dates:
            mfg_date = min(dates)
            break
if not mfg_date:
    mfg_date = "2019-02-20"  # CL650-6134 delivery per playbook
print(f"Aircraft manufacture/delivery date (estimate): {mfg_date}", file=sys.stderr)

# ---- Pass 2: per Component, gather hits ----
print("\nGathering evidence per component...", file=sys.stderr)

def matches(raw, target_norm, target_agg, tier="basic"):
    """Return True if raw matches target via normalisation tier."""
    if not raw: return False
    n = norm_basic(raw)
    if n == target_norm: return True
    if tier == "aggressive":
        a = norm_aggressive(raw)
        if a == target_agg: return True
        if a and edit_distance(a, target_agg) <= 1: return True
    return False

def gather_hits(target_pn, target_sn):
    npn = norm_basic(target_pn); nsn = norm_basic(target_sn)
    apn = norm_aggressive(target_pn); asn = norm_aggressive(target_sn)
    hits = []
    for p in PAGES:
        # Quick reject: must mention PN aggressively
        if not any(norm_aggressive(x) == apn or (norm_aggressive(x) and edit_distance(norm_aggressive(x), apn) <= 1)
                   for x in p["raw_pns"]):
            continue
        # Find matching row
        for r in p["rows"]:
            rpn = r.get("pn"); rsn = r.get("sn")
            if not rpn: continue
            if not (matches(rpn, npn, apn, "aggressive") and (
                    (rsn and matches(rsn, nsn, asn, "aggressive")) or rsn is None)):
                continue
            hits.append({"page": p, "row": r})
    return hits

components_data = []
for grp, pn, sn, desc in COMPONENTS:
    hits = gather_hits(pn, sn)
    # Aggregate evidence
    events_by_status = defaultdict(list)
    install_events = []
    location = None
    category = None
    ata = None
    mod_status_seen = []
    real_desc = desc
    alt_pns = set()
    form1_certs = []  # list of {date, tracking_no, signer, status, source}
    llp_limit_hours = None; llp_limit_cycles = None; llp_remaining_hours = None; llp_remaining_cycles = None
    next_due_hours = None; next_due_cycles = None
    born_on_aircraft = False

    for h in hits:
        p = h["page"]; r = h["row"]
        # If on OEM serialisation listing → born on aircraft
        if p["is_oem"]:
            born_on_aircraft = True
            if r.get("loc"): location = r["loc"]
            if r.get("ata"): ata = ata or r["ata"]
            if r.get("desc"): real_desc = r["desc"]
            if r.get("mod_status"): mod_status_seen.append(r["mod_status"])
            # Different OEM doc may have different PN form (e.g. Bombardier ref vs Saft ref)
            if r.get("pn") and norm_basic(r["pn"]) != norm_basic(pn): alt_pns.add(r["pn"])
        # If on LLP status page → harvest limits
        if p["doc_type"] == "life_limited_parts_status":
            full = r.get("full") or []
            # Look for HRS/CYC limits — typically [task,...,"HRS","0","20000",None,"20000",None,"19751","Service",...]
            for i, c in enumerate(full):
                if c in ("HRS","Hrs","Hours"):
                    # next two cells: start_value, limit
                    try:
                        limit = int(str(full[i+2]).replace(",","")) if i+2 < len(full) and full[i+2] else None
                        if limit: llp_limit_hours = limit
                    except: pass
                    try:
                        rem = int(str(full[i+7]).replace(",","")) if i+7 < len(full) and full[i+7] else None
                        if rem: llp_remaining_hours = rem
                    except: pass
                if c in ("APUS","APU Hrs"):
                    try:
                        limit = int(str(full[i+2]).replace(",","")) if i+2 < len(full) and full[i+2] else None
                        if limit: llp_limit_cycles = limit
                    except: pass
            if r.get("desc"): real_desc = r["desc"]
            category = "LLP"
        # If Form 1 (any flavor) → collect cert
        if p["doc_type"] in ("easa_form_one","faa_form_8130","tcca_form_one"):
            cert = {
                "date": p.get("release_date") or "",
                "tracking_no": p.get("tracking_no") or "",
                "signer": p.get("signer") or "",
                "status": (r.get("status") or p.get("block_11") or "").upper(),
                "file": p["file"], "page": p["page"], "doc_type": p["doc_type"],
            }
            form1_certs.append(cert)
            if cert["status"]:
                events_by_status[cert["status"]].append(cert)
        # Job cards / WPSS / CRS — install/removal events
        if p["doc_type"] in ("work_order_contents_report","mis_task_card","certificate_of_release_to_service","routine_task_card"):
            # Try to find OFF/ON pattern in the row or page text
            ftext = json.dumps(r.get("full") or [])
            text = p["page_text"]
            # Try to extract AC hours and date from the page
            hrs = None
            for mh in HOURS_RE.finditer(text):
                try:
                    hh = int(mh.group(1)) + int(mh.group(2))/60
                    if hrs is None or hh > hrs:
                        hrs = hh
                except: pass
            cycles = None
            for mc in CYCLES_RE.finditer(text):
                try:
                    cc = int(mc.group(1))
                    if cycles is None or cc > cycles:
                        cycles = cc
                except: pass
            dates = parse_dates(text)
            d = max(dates) if dates else ""
            install_events.append({"file": p["file"], "page": p["page"], "doc_type": p["doc_type"],
                                    "hours": f"{int(hrs)}:{int(round((hrs-int(hrs))*60)):02d}" if hrs else "",
                                    "cycles": cycles or "",
                                    "date": d})

    # Compute the 11 fields
    install_date = None; install_hours = None; install_cycles = None
    if born_on_aircraft:
        install_date = mfg_date
        install_hours = "0:00"
        install_cycles = "0"
    elif install_events:
        # earliest install event
        earliest = sorted(install_events, key=lambda e: e["date"] or "9999")[0]
        install_date = earliest["date"] or "Unknown"
        install_hours = earliest["hours"] or "Unknown"
        install_cycles = earliest["cycles"] or "Unknown"
    else:
        install_date = "GAP_IN_DOSSIER"
        install_hours = "GAP"
        install_cycles = "GAP"

    overhauls = sorted([c for c in form1_certs if "OVERHAUL" in c["status"]], key=lambda c: c.get("date") or "")
    repairs = sorted([c for c in form1_certs if "REPAIR" in c["status"]], key=lambda c: c.get("date") or "")
    inspections = sorted([c for c in form1_certs if "INSPECT" in c["status"] or "TEST" in c["status"]], key=lambda c: c.get("date") or "")
    last_shop_visit = (overhauls + repairs + inspections)[-1] if (overhauls or repairs or inspections) else None

    field_3_overhaul = "; ".join(f"{c['date']} (cert {c['tracking_no']})" for c in overhauls) if overhauls else "NIL"
    field_4_repair   = "; ".join(f"{c['date']} (cert {c['tracking_no']})" for c in repairs) if repairs else "NIL"
    field_5_mod      = ", ".join(set(mod_status_seen)) if mod_status_seen else "NIL"
    field_6_cert     = f"{last_shop_visit['tracking_no']} ({last_shop_visit['date']}, {last_shop_visit['doc_type']})" if last_shop_visit else ("Birth Form 1 — OEM serialisation listing" if born_on_aircraft else "GAP_IN_DOSSIER")
    field_7_tsn_at_last = "Unknown (read from cert / techlog at shop visit date)" if last_shop_visit else ("N/A — never been to shop" if born_on_aircraft else "GAP")
    cur_hours_int = int(latest_tjl_hours) if latest_tjl_hours else 0
    cur_min = int(round((latest_tjl_hours - cur_hours_int)*60))
    cur_hours_str = f"{cur_hours_int}:{cur_min:02d}"
    field_8_acc_since_overhaul = (
        "N/A — never overhauled" if not overhauls and born_on_aircraft else
        ("From last overhaul date to now: compute from techlog" if overhauls else "N/A")
    )
    field_9_current_tpt = cur_hours_str if born_on_aircraft and not overhauls else "Unknown — sum of install→removal arcs needed"
    field_10_remaining = "N/A" if not (llp_limit_hours or next_due_hours) else f"{llp_remaining_hours}H / {llp_remaining_cycles or 'N/A'}C remaining of {llp_limit_hours}H limit"
    alt_pns_str = ", ".join(sorted(alt_pns)) if alt_pns else ""
    field_11_identity = f"PN: {pn}" + (f" (alt: {alt_pns_str})" if alt_pns_str else "") + f" | SN: {sn} | Desc: {real_desc} | Pos: {location or 'Unknown'} | Cat: {category or ('LLP' if 'LLP' in real_desc.upper() else 'Unknown')}"

    components_data.append({
        "grp": grp,
        "pn": pn,
        "sn": sn,
        "1_install_date": install_date,
        "2_install_tsn_hours": install_hours,
        "2_install_tsn_cycles": install_cycles,
        "3_overhaul_history": field_3_overhaul,
        "4_repair_history": field_4_repair,
        "5_mod_status": field_5_mod,
        "6_last_cert": field_6_cert,
        "7_tsn_at_last_shop_visit": field_7_tsn_at_last,
        "8_acc_since_overhaul": field_8_acc_since_overhaul,
        "9_current_total_time": field_9_current_tpt,
        "10_remaining_to_overhaul": field_10_remaining,
        "11_identity": field_11_identity,
        "born_on_aircraft": "Yes" if born_on_aircraft else "No",
        "n_form1s_found": len(form1_certs),
        "n_install_events": len(install_events),
        "evidence_files": ", ".join(sorted({h["page"]["file"] for h in hits if h["page"]["file"]})[:5]),
    })

# Write CSV
out_csv = "D:/work/openclaude/dumps/lukas_component_cards.csv"
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow([
        "#","Group","PN","SN","Born_on_Aircraft",
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
        "n_Form1s_found_in_dossier",
        "n_install_events_in_dossier",
        "evidence_source_files",
    ])
    for i, c in enumerate(components_data, 1):
        w.writerow([
            i, c["grp"], c["pn"], c["sn"], c["born_on_aircraft"],
            c["1_install_date"], c["2_install_tsn_hours"], c["2_install_tsn_cycles"],
            c["3_overhaul_history"], c["4_repair_history"], c["5_mod_status"],
            c["6_last_cert"], c["7_tsn_at_last_shop_visit"],
            c["8_acc_since_overhaul"], c["9_current_total_time"],
            c["10_remaining_to_overhaul"], c["11_identity"],
            c["n_form1s_found"], c["n_install_events"], c["evidence_files"],
        ])

print(f"\nWrote {out_csv}")
print(f"Total components processed: {len(components_data)}")
