"""migrate_form1_part5_from_csv.py

CSV-driven re-derivation of Form 1 structural edges for CL650-6134.

The earlier PowerShell migration parts 1-4 worked on the Neo4j graph alone
and could only see the OCR-derived MENTIONS_PN / MENTIONS_SN flat lists.
That introduced 403 false-positive RELEASES edges (NiCad battery case
linked to 13 components when the cert releases 1).

This script goes back to the source: it streams the raw CSV, parses each
Form 1 page's `header_fields` (block 3 Form Tracking No, block 4 issuer,
block 13/14 signer) and `tables[]` (block 7 description, block 8 part no,
block 10 serial no, block 11 status). It writes authoritative edges:

  :Form1.block_3_form_tracking_no   (the unique cert identifier)
  :Form1.block_8_pn_list            (the PNs actually released by this cert)
  :Form1.block_10_sn_list            (the SNs actually released)
  :Form1.block_11_status             (overhauled / repaired / new / inspected)
  :Form1.block_13b_name              (signer name)
  :Form1.block_4_issuer_text         (issuing organisation)

Then for each item row in the cert table:
  :Form1-[:RELEASES_PN {source:'csv_table_row', confidence:'high'}]->:PartNumber
  :Form1-[:RELEASES_SN {source:'csv_table_row', confidence:'high'}]->:SerialNumber
  :Form1-[:RELEASES   {source:'csv_structural', disposition:..., confidence:'high'}]->:Component
    (only when both PN AND SN match an existing Component, i.e. the cert's
     literal binding intersects the dossier's Component nodes)

And the v7 false positives get tagged:
  :Form1-[:RELEASES {phase:'v7'}].pn_verified = 'csv_structural_mismatch'

Sections:
  1.  Stream CSV, identify Form 1 pages, parse structured fields
  2.  Group by Form Tracking No (detect merge cases)
  3.  Build Cypher batch:
      3a. Update Form1 properties (block fields)
      3b. Write authoritative RELEASES_PN / RELEASES_SN per table row
      3c. Write authoritative RELEASES per matching (PN, SN) component
      3d. Tag v7 RELEASES that don't match the structural binding
  4.  Execute via cypher-shell, batched
  5.  Verification: NiCad case should show exactly 1 structural Component;
      Bombardier case should show 3
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(50_000_000)

# Add graph_dal to sys.path for normalize helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sparengine-export"))
try:
    from graph_dal._normalize import normalize_date as _norm_date
except Exception:
    _MONTHS = {
        "jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
        "jul":"07","aug":"08","sep":"09","sept":"09","oct":"10","nov":"11","dec":"12",
    }
    def _norm_date(value):
        if not value: return None
        s = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s
        m = re.match(r"^\s*(\d{1,2})[/\-\s](\w{3,9})[/\-\s](\d{4})\s*$", s)
        if m:
            dd, mmm, yyyy = m.group(1), m.group(2), m.group(3)
            mm = _MONTHS.get(mmm.lower())
            if mm:
                return f"{yyyy}-{mm}-{int(dd):02d}"
        return None

CSV_PATH = Path("D:/work/openclaude/csvs/Full challenger dossier.csv")
NEO4J_CONTAINER = "sparengine-neo4j"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005"
ASSET_ID = "CL650-6134"
PHASE_TAG = "migration_form1_csv_2026_05_19"

FORM1_DOC_TYPES = {
    "tcca_form_one", "tcca_form_1",
    "easa_form_one", "easa_form_1",
    "faa_form_8130", "faa_8130_3", "faa_form_8130-3",
    "dual_release_certificate",
    "authorised_release_certificate", "authorized_release_certificate",
}

# Block-3 Form Tracking No can appear under various header_fields keys.
FORM_TRACKING_KEYS = [
    "3. Form Tracking No.",
    "3. Form Tracking Number",
    "3. Tracking No.",
    "3. Form Tracking",
    "3.",
]
# Block 4 issuer.
ISSUER_KEYS = [
    "4. Approved organization name and address",
    "4. Approved Organization Name and Address",
    "4. Organization name and address",
    "4.",
]
# Block 11 status (also sometimes in entities).
STATUS_KEYS = ["11.Status/Work", "11. Status/Work", "11. Status", "11.Status", "11."]
# Block 13 signer (EASA / FAA convention).
SIGNER_KEYS_13 = ["13d. Name", "13.d Name", "13d Name", "13d", "13.d"]
SIGNER_KEYS_14 = ["14d. Name", "14.d Name", "14d Name", "14d", "14.d"]
DATE_KEYS_13 = ["13e. Date (dd/mmm/yyyy)", "13e. Date", "13e Date", "13e", "13.e"]
DATE_KEYS_14 = ["14e. Date (dd/mmm/yyyy)", "14e. Date", "14e Date", "14e", "14.e"]
CERT_NO_KEYS = [
    "13c. Approval/Authorisation Number",
    "13c. Approval/Authorization Number",
    "13c. Certificate/Approval Ref. No.",
    "13c. Certificate/Approval Ref No.",
    "13c. Authorisation Number",
    "13c.",
    "14c. Approval/Authorisation Number",
    "14c. Authorization Number",
    "14c. Certificate/Approval Ref. No.",
    "14c.",
]

# Single-item header_fields style (EASA Form 1 variant: item data is at root
# of header_fields instead of inside tables[]).
H_ITEM_NO_KEYS    = ["6.Item", "6. Item", "6 Item", "6."]
H_DESCRIPTION_KEYS = ["7.Description", "7. Description", "7 Description"]
H_PART_NO_KEYS    = ["8.Part No", "8. Part No", "8. Part No.", "8.Part No.", "8 Part No"]
H_QTY_KEYS        = ["9.Qty", "9. Qty", "9. Qty.", "9.Qty.", "9 Qty"]
H_SERIAL_KEYS     = ["10.Serial/Batch No", "10. Serial/Batch No", "10.Serial/Batch No.", "10. Serial/Batch No.", "10. Serial Number", "10 Serial"]
H_STATUS_KEYS     = ["11.Status/Work", "11. Status/Work", "11.Status", "11. Status", "11.Status/Work."]


def _first(d: dict, keys: list[str]) -> str | None:
    """Return the first non-empty value among ``keys`` in ``d``."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v not in (None, "", "None"):
            return str(v).strip() if isinstance(v, str) else v
    return None


def _parse_header_single_item(header: dict) -> list[dict]:
    """Parse a single-item Form 1 where item fields live directly in header_fields.

    Common in EASA Form 1 OCR variant — block 6/7/8/9/10/11 are at the root
    of header_fields, with no tables[].rows for the item table. Returns
    [{pns, sns, qty, status, description, item}] or empty list. `pns` is
    a list (a cell may carry vendor+OEM PNs separated by newline).
    """
    if not isinstance(header, dict):
        return []
    pn_field = _first(header, H_PART_NO_KEYS)
    sn_field = _first(header, H_SERIAL_KEYS)
    if not pn_field and not sn_field:
        return []
    return [{
        "item":        _first(header, H_ITEM_NO_KEYS),
        "description": _first(header, H_DESCRIPTION_KEYS),
        "pns":         _split_pns(pn_field),
        "qty":         _first(header, H_QTY_KEYS),
        "sns":         _split_sns(sn_field),
        "status":      _first(header, H_STATUS_KEYS),
    }]


def _parse_table_rows(tables: list) -> list[dict]:
    """Parse the cert item table(s) into structured rows.

    Returns a list of {pn, sns, qty, status, description} per item line.
    `sns` is a list because a single item line can list multiple SNs
    (batch cert pattern from the GG468 / Bombardier window-shade cases).
    """
    items = []
    if not isinstance(tables, list):
        return items
    for t in tables:
        if not isinstance(t, dict):
            continue
        headers_raw = t.get("headers") or []
        rows = t.get("rows") or t.get("data") or []
        # Map header → column index
        idx = {}
        for i, h in enumerate(headers_raw):
            if not isinstance(h, str):
                continue
            hl = h.lower().strip()
            # Order matters: more-specific patterns first, fall through to generic.
            if "part" in hl and ("number" in hl or "no." in hl or "no " in hl or hl.endswith("no") or hl.endswith("part")):
                idx["pn"] = i
            elif "serial" in hl or "s/n" in hl:
                idx["sn"] = i
            elif "batch" in hl:
                idx["sn"] = i  # batch column can carry SNs too
            elif "descr" in hl:
                idx["description"] = i
            elif "qty" in hl or "quantity" in hl:
                idx["qty"] = i
            elif "status" in hl or "work" in hl:
                idx["status"] = i
            elif "item" in hl:
                idx["item"] = i
        for row in rows:
            if not isinstance(row, list):
                continue
            def cell(name):
                i = idx.get(name)
                if i is None or i >= len(row):
                    return None
                v = row[i]
                return None if v in (None, "") else (str(v).strip() if isinstance(v, str) else v)
            pn_field = cell("pn")
            sn_field = cell("sn")
            if not pn_field and not sn_field:
                continue
            # Split PN field — can be "AV16B2177-3\n601-62900-7" (vendor+OEM)
            pns = _split_pns(pn_field)
            # Split SN field — can be "S/N 4005\nS/N 4006\nS/N 4007" or
            # "4005, 4006, 4007" or "Refer to block 12" or a single "090520021095E"
            sns = _split_sns(sn_field)
            items.append({
                "item":        cell("item"),
                "description": cell("description"),
                "pns":         pns,
                "qty":         cell("qty"),
                "sns":         sns,
                "status":      cell("status"),
            })
    return items


_SN_SPLIT_RE = re.compile(r"(?:S/N|SN|Serial(?:\s+No\.?)?)\s*[:#]?\s*", re.IGNORECASE)
_PN_PREFIX_RE = re.compile(r"(?:P/N|PN|Part(?:\s+No\.?)?)\s*[:#]?\s*", re.IGNORECASE)


def _split_pns(value) -> list[str]:
    """Split a Part Number cell that may carry multiple PNs.

    OEMs commonly list vendor+OEM PNs in one cell separated by newline
    (e.g. FAA 8130-3 for an ITT valve: 'AV16B2177-3\\n601-62900-7' = the
    vendor part number AND the Bombardier catalog number for the same
    physical valve). Either is a valid PN to bind the cert to; downstream
    alias resolution links them.
    """
    if not value:
        return []
    s = str(value).strip()
    if not s:
        return []
    sl = s.lower()
    if "refer to" in sl or "see block" in sl or "see remarks" in sl:
        return []
    s = _PN_PREFIX_RE.sub("", s)
    parts = re.split(r"[,;\n\r]+", s)
    out = []
    for p in parts:
        p = _PN_PREFIX_RE.sub("", p).strip()
        if not p or len(p) < 2:
            continue
        if p.lower() in ("na", "n/a", "none", "tbd", "tba"):
            continue
        out.append(p)
    return out


def _split_sns(value) -> list[str]:
    """Split a Serial/Batch column value into discrete SNs.

    Handles:
      - "090520021095E"                   → ["090520021095E"]
      - "S/N 4005\nS/N 4006\nS/N 4007"    → ["4005","4006","4007"]
      - "4005, 4006, 4007"                → ["4005","4006","4007"]
      - "Refer to block 12"               → []   (cannot extract)
      - "S/N S: 3983, 3984, ..., 4000"    → ["3983","3984",...,"4000"]
    """
    if not value:
        return []
    s = str(value).strip()
    if not s:
        return []
    sl = s.lower()
    if "refer to" in sl or "see block" in sl or "see remarks" in sl:
        return []
    # Strip leading "S/N" / "Serial" prefixes, then split on newlines/commas/semicolons
    s = _SN_SPLIT_RE.sub("", s)
    parts = re.split(r"[,;\n\r]+", s)
    out = []
    for p in parts:
        p = _SN_SPLIT_RE.sub("", p).strip()
        if not p:
            continue
        # Drop obvious noise tokens
        if len(p) < 2:
            continue
        if p.lower() in ("na", "n/a", "none", "tbd", "tba", "see block 12"):
            continue
        out.append(p)
    return out


def _normalize_status(value: str | None) -> str | None:
    """Map block-11 status text to the closed disposition enum."""
    if not value:
        return None
    s = value.strip().lower()
    mapping = {
        "new": "new",
        "serviceable": "new",
        "overhauled": "overhauled", "overhaul": "overhauled",
        "repaired": "repaired", "repair": "repaired",
        "inspected": "inspected", "inspected/tested": "inspected", "tested": "inspected",
        "as-removed": "as_removed", "as removed": "as_removed",
        "modified": "modified", "modification": "modified",
    }
    if s in mapping:
        return mapping[s]
    for key, val in mapping.items():
        if key in s:
            return val
    return "unknown"


# =============================================================================
#  Phase 1: stream CSV
# =============================================================================

def stream_form1_pages():
    """Yield (page_uid, file_name, page_index, form1_data) per Form 1 row."""
    with CSV_PATH.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ext = json.loads(row.get("extracted_json") or "{}")
            except Exception:
                continue
            # The CSV puts everything at the root level (not under content).
            content = ext.get("content") or ext
            doc_type = content.get("document_type") or ext.get("document_type")
            if not doc_type or doc_type not in FORM1_DOC_TYPES:
                continue
            header = content.get("header_fields") or ext.get("header_fields") or {}
            tables = content.get("tables") or ext.get("tables") or []
            metadata = content.get("metadata") or ext.get("metadata") or {}
            stamps = content.get("stamps_and_signatures") or ext.get("stamps_and_signatures") or []
            events = content.get("events") or ext.get("events") or []

            tracking_no = _first(header, FORM_TRACKING_KEYS)
            issuer_text = _first(header, ISSUER_KEYS)
            status_block11 = _first(header, STATUS_KEYS)
            signer_13 = _first(header, SIGNER_KEYS_13)
            signer_14 = _first(header, SIGNER_KEYS_14)
            cert_no = _first(header, CERT_NO_KEYS)
            date_13_hdr = _first(header, DATE_KEYS_13)
            date_14_hdr = _first(header, DATE_KEYS_14)

            # Date precedence: block 14e (FAA Return-to-Service date) wins,
            # then block 13e (EASA conformity date). These are the structural
            # release dates from the cert. Only fall back to events.date /
            # metadata.dates if both are absent — those can pull from block 12
            # text (e.g. DWG dates like 'Sep/11/1995') and produce nonsense.
            date_iso = _norm_date(date_14_hdr) or _norm_date(date_13_hdr)
            if not date_iso:
                for ev in events:
                    if isinstance(ev, dict) and ev.get("event_type") == "release_to_service" and ev.get("date"):
                        date_iso = ev["date"]
                        break
            if not date_iso:
                for ev in events:
                    if isinstance(ev, dict) and ev.get("date"):
                        date_iso = ev["date"]
                        break
            if not date_iso:
                dates_meta = metadata.get("dates") or []
                if dates_meta:
                    date_iso = dates_meta[0]

            # Try tables[] first (multi-item table form); fall back to
            # header_fields single-item layout (EASA variant).
            items = _parse_table_rows(tables)
            if not items:
                items = _parse_header_single_item(header)
            else:
                # Backfill from header_fields when the table is missing
                # a column. Common in FAA 8130-3: table has PN+Description
                # but SN is at the header level only. (Single-item cert.)
                hdr_pn   = _first(header, H_PART_NO_KEYS)
                hdr_sn   = _first(header, H_SERIAL_KEYS)
                hdr_stat = _first(header, H_STATUS_KEYS)
                hdr_qty  = _first(header, H_QTY_KEYS)
                hdr_desc = _first(header, H_DESCRIPTION_KEYS)
                if len(items) == 1:
                    it = items[0]
                    if not it["pns"] and hdr_pn:
                        it["pns"] = _split_pns(hdr_pn)
                    if not it["sns"] and hdr_sn:
                        it["sns"] = _split_sns(hdr_sn)
                    if not it.get("status") and hdr_stat:
                        it["status"] = hdr_stat
                    if not it.get("qty") and hdr_qty:
                        it["qty"] = hdr_qty
                    if not it.get("description") and hdr_desc:
                        it["description"] = hdr_desc

            # Pull mod-status from block 12 sections if available
            sections = content.get("sections") or ext.get("sections") or []
            block_12_text = None
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                heading = (sec.get("heading") or "").lower()
                if "12" in heading or "remark" in heading:
                    block_12_text = sec.get("data") or sec.get("text")
                    break

            # Signer / cert fallback: walk stamps_and_signatures for a stamp
            # whose location_context mentions block 13b/14b/13d/14d.
            signer = signer_13 or signer_14
            signer_block = "13b" if signer_13 else ("14b" if signer_14 else None)
            cert_no_resolved = cert_no
            date_resolved = date_iso
            if not signer or not cert_no_resolved or not date_resolved:
                for s in stamps:
                    if not isinstance(s, dict):
                        continue
                    loc = (s.get("location_context") or "").lower()
                    is_release_block = (
                        "14b" in loc or "14 b" in loc or "14.b" in loc or
                        "13b" in loc or "13 b" in loc or "13.b" in loc or
                        "block 14" in loc or "block 13" in loc or
                        "authorised signature" in loc or "authorized signature" in loc or
                        "release" in loc
                    )
                    if not is_release_block:
                        continue
                    if not signer and s.get("person_name"):
                        signer = s["person_name"]
                        signer_block = "14b" if "14" in loc else "13b"
                    if not cert_no_resolved and s.get("certificate_number"):
                        cert_no_resolved = s["certificate_number"]
                    if not date_resolved and s.get("date"):
                        date_resolved = s["date"]
                    if signer and cert_no_resolved and date_resolved:
                        break
                # Final fallback: if there's a single stamp on the page with
                # a person_name, take it.
                if not signer:
                    named_stamps = [s for s in stamps if isinstance(s, dict) and s.get("person_name")]
                    if len(named_stamps) == 1:
                        signer = named_stamps[0]["person_name"]
                        signer_block = "fallback_single_stamp"
                        cert_no_resolved = cert_no_resolved or named_stamps[0].get("certificate_number")
                        date_resolved = date_resolved or named_stamps[0].get("date")

            yield {
                "page_uid": row["id"],
                "file_name": row.get("file_name"),
                "page_index": int(row.get("page_index") or 0),
                "doc_type": doc_type,
                "tracking_no": tracking_no,
                "issuer_text": issuer_text,
                "status_block11": status_block11,
                "signer": signer,
                "signer_block": signer_block,
                "cert_no": cert_no_resolved,
                "date_iso": date_resolved,
                "block_12_text": block_12_text,
                "items": items,
                "stamps": stamps,
            }


# =============================================================================
#  Phase 3: Cypher batch construction
# =============================================================================

def _cypher_str(value) -> str:
    """Safely embed a string in Cypher (we use parameter passing via files)."""
    if value is None:
        return "null"
    if isinstance(value, (int, float, bool)):
        return str(value).lower() if isinstance(value, bool) else str(value)
    s = str(value).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", " ")
    return f"'{s}'"


def build_cypher(records: list[dict]) -> str:
    """Build a single multi-statement Cypher script.

    Strategy: write a per-page UNWIND batch of structural data, then run
    deterministic MERGE/MATCH passes against it.
    """
    payload = []
    for rec in records:
        for i, item in enumerate(rec["items"]):
            for sn in (item["sns"] or [None]):
                payload.append({
                    "page_uid": rec["page_uid"],
                    "tracking_no": rec["tracking_no"],
                    "issuer_text": rec["issuer_text"],
                    "signer": rec["signer"],
                    "cert_no": rec["cert_no"],
                    "date_iso": rec["date_iso"],
                    "doc_type": rec["doc_type"],
                    "block_12_text": rec["block_12_text"],
                    "item_idx": i,
                    "pn": item["pn"],
                    "sn": sn,
                    "qty": item["qty"],
                    "description": item["description"],
                    "status": item["status"] or rec["status_block11"],
                })
    return payload


def execute_cypher(query: str, params: dict | None = None) -> str:
    """Run cypher-shell with params via stdin."""
    if params:
        param_str = json.dumps(params)
        # Use cypher-shell -P "{json}" syntax via env-passing; simpler: embed params
        # as a single $payload parameter.
        cmd = [
            "docker", "exec", "-i", NEO4J_CONTAINER,
            "cypher-shell", "-u", NEO4J_USER, "-p", NEO4J_PASSWORD,
            "--format", "plain",
            "--param", f"payload => {json.dumps(params)}",
            query,
        ]
    else:
        cmd = [
            "docker", "exec", "-i", NEO4J_CONTAINER,
            "cypher-shell", "-u", NEO4J_USER, "-p", NEO4J_PASSWORD,
            "--format", "plain",
            query,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"!! Cypher error:\n{result.stderr}", file=sys.stderr)
    return result.stdout


def execute_with_payload(query: str, payload: list[dict]) -> str:
    """Run a cypher query with a list-of-maps payload parameter named $payload.

    cypher-shell --param doesn't reliably accept arbitrary JSON via shell
    quoting on Windows, so we write a temp .cypher file with the payload
    inlined as a Cypher literal and run that via -f.
    """
    # Inline payload as Cypher list of maps (keep tokenisation simple)
    lines = ["WITH ["]
    parts = []
    for r in payload:
        item = "{" + ", ".join(
            f"{k}: {_cypher_lit(v)}" for k, v in r.items()
        ) + "}"
        parts.append(item)
    lines.append(", ".join(parts))
    lines.append("] AS payload")
    lines.append(query)
    cypher = "\n".join(lines) + ";\n"

    tmp = Path("D:/work/openclaude/dumps/_migrate_form1_payload.cypher")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(cypher, encoding="utf-8")

    cmd = [
        "docker", "cp", str(tmp), f"{NEO4J_CONTAINER}:/tmp/payload.cypher",
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    cmd = [
        "docker", "exec", NEO4J_CONTAINER,
        "bash", "-c",
        f"cypher-shell -u {NEO4J_USER} -p {NEO4J_PASSWORD} --format plain -f /tmp/payload.cypher",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"!! Cypher error:\n{result.stderr}", file=sys.stderr)
    return result.stdout


def _cypher_lit(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    s = s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", " ")
    return f"'{s}'"


# =============================================================================
#  Main
# =============================================================================

def main():
    print("==== migrate_form1_part5_from_csv.py ====")
    print(f"     CSV: {CSV_PATH}")
    print(f"     Asset: {ASSET_ID}   Neo4j: {NEO4J_CONTAINER}   tag: {PHASE_TAG}")
    print()

    # 1. Stream CSV
    print("---- 1. Streaming CSV for Form 1 pages ----")
    records = list(stream_form1_pages())
    print(f"   Form 1 pages found: {len(records)}")

    # Per-Form-Tracking-No grouping
    by_tracking = defaultdict(list)
    for r in records:
        if r["tracking_no"]:
            by_tracking[r["tracking_no"]].append(r)
    print(f"   Unique Form Tracking Nos with extracted block 3: {len(by_tracking)}")
    print(f"   Form 1 pages without a Form Tracking No: {sum(1 for r in records if not r['tracking_no'])}")

    # Items per record
    total_items = sum(len(r["items"]) for r in records)
    total_pn_sn = sum(
        1 for r in records for item in r["items"]
        for _pn in (item.get("pns") or [])
        for _sn in (item.get("sns") or [None])
    )
    print(f"   Total item rows extracted: {total_items}")
    print(f"   Total (PN, SN) pairs to bind: {total_pn_sn}")
    print()

    # 2. Update Form1 properties (block fields) from page-keyed lookup
    print("---- 2. Updating Form1 block-level properties (per carrier page) ----")
    page_payload = []
    for r in records:
        page_payload.append({
            "page_uid": r["page_uid"],
            "tracking_no": r["tracking_no"],
            "issuer_text": r["issuer_text"],
            "signer": r["signer"],
            "signer_block": r["signer_block"],
            "cert_no": r["cert_no"],
            "date_iso": _norm_date(r["date_iso"]) if r["date_iso"] else None,
            "status": _normalize_status(r["status_block11"]),
            "block_12_text": r["block_12_text"],
        })
    out = execute_with_payload(payload=page_payload, query="""
        UNWIND payload AS p
        MATCH (page:Page {value: p.page_uid})-[:CARRIES]->(f:Form1)
        SET f.block_3_form_tracking_no = coalesce(p.tracking_no, f.block_3_form_tracking_no),
            f.block_4_issuer_text      = coalesce(p.issuer_text, f.block_4_issuer_text),
            f.signer_name              = coalesce(p.signer, f.signer_name),
            f.signer_block             = coalesce(p.signer_block, f.signer_block),
            f.cert_number              = coalesce(p.cert_no, f.cert_number),
            f.release_date_iso         = coalesce(p.date_iso, f.release_date_iso),
            f.block_11_status_norm     = coalesce(p.status, f.block_11_status_norm),
            f.block_12_text_csv        = coalesce(p.block_12_text, f.block_12_text_csv),
            f.csv_migration_phase      = '""" + PHASE_TAG + """'
        RETURN count(DISTINCT f) AS form1s_updated
    """)
    print(out)

    # 3. Write authoritative RELEASES_PN / RELEASES_SN per item row
    print("---- 3a. Writing structural RELEASES_PN edges (csv_table_row) ----")
    pn_payload = []
    for r in records:
        for item in r["items"]:
            for pn in (item.get("pns") or []):
                if not pn:
                    continue
                pn_payload.append({
                    "page_uid": r["page_uid"],
                    "pn": pn,
                    "tracking_no": r["tracking_no"],
                    "status": _normalize_status(item["status"] or r["status_block11"]),
                })
    out = execute_with_payload(payload=pn_payload, query="""
        UNWIND payload AS p
        MATCH (page:Page {value: p.page_uid})-[:CARRIES]->(f:Form1)
        MERGE (pn:PartNumber {asset_id: f.asset_id, value: p.pn})
          ON CREATE SET pn.csv_migration_phase = '""" + PHASE_TAG + """'
        MERGE (f)-[r:RELEASES_PN_STRUCT]->(pn)
          ON CREATE SET r.source           = 'csv_table_row',
                        r.confidence       = 'high',
                        r.block             = '8',
                        r.form_tracking_no = p.tracking_no,
                        r.disposition       = p.status,
                        r.phase             = '""" + PHASE_TAG + """'
        RETURN count(DISTINCT r) AS releases_pn_struct
    """)
    print(out)

    print("---- 3b. Writing structural RELEASES_SN edges (csv_table_row) ----")
    sn_payload = []
    for r in records:
        for item in r["items"]:
            for sn in item["sns"]:
                if not sn:
                    continue
                sn_payload.append({
                    "page_uid": r["page_uid"],
                    "sn": sn,
                    "tracking_no": r["tracking_no"],
                    "status": _normalize_status(item["status"] or r["status_block11"]),
                })
    out = execute_with_payload(payload=sn_payload, query="""
        UNWIND payload AS p
        MATCH (page:Page {value: p.page_uid})-[:CARRIES]->(f:Form1)
        MERGE (sn:SerialNumber {asset_id: f.asset_id, value: p.sn})
          ON CREATE SET sn.csv_migration_phase = '""" + PHASE_TAG + """'
        MERGE (f)-[r:RELEASES_SN_STRUCT]->(sn)
          ON CREATE SET r.source           = 'csv_table_row',
                        r.confidence       = 'high',
                        r.block             = '10',
                        r.form_tracking_no = p.tracking_no,
                        r.disposition       = p.status,
                        r.phase             = '""" + PHASE_TAG + """'
        RETURN count(DISTINCT r) AS releases_sn_struct
    """)
    print(out)

    # 4. Authoritative Form1 -> Component for (PN, SN) pairs that exist
    print("---- 4. Writing structural RELEASES -> Component edges (PN AND SN match) ----")
    component_payload = []
    for r in records:
        for item in r["items"]:
            for pn in (item.get("pns") or []):
                if not pn:
                    continue
                for sn in (item.get("sns") or []):
                    if not sn:
                        continue
                    component_payload.append({
                        "page_uid": r["page_uid"],
                        "pn": pn,
                        "sn": sn,
                        "tracking_no": r["tracking_no"],
                        "status": _normalize_status(item["status"] or r["status_block11"]),
                    })
    out = execute_with_payload(payload=component_payload, query="""
        UNWIND payload AS p
        MATCH (page:Page {value: p.page_uid})-[:CARRIES]->(f:Form1)
        OPTIONAL MATCH (c:Component {asset_id: f.asset_id})-[:HAS_SN]->(sn:SerialNumber {value: p.sn})
        WITH p, f, c
        WHERE c IS NOT NULL
        OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn_c:PartNumber)
        WITH p, f, c, pn_c
        WHERE pn_c IS NOT NULL
          AND (pn_c.value = p.pn
            OR pn_c.normalized = replace(replace(replace(replace(toUpper(p.pn),' ',''),'-',''),'_',''),'/','')
            OR EXISTS {
              MATCH (pn_paper:PartNumber {asset_id: f.asset_id, value: p.pn})-[a:ALIAS_OF]->(pn_c)
              WHERE a.kind IN ['ocr_variant','incomplete_pn']
            }
            OR EXISTS {
              MATCH (pn_paper:PartNumber {asset_id: f.asset_id, value: p.pn})<-[a:ALIAS_OF]-(pn_c)
              WHERE a.kind IN ['ocr_variant','incomplete_pn']
            })
        MERGE (f)-[r:RELEASES_STRUCT]->(c)
          ON CREATE SET r.source            = 'csv_structural',
                        r.confidence        = 'high',
                        r.disposition       = p.status,
                        r.form_tracking_no  = p.tracking_no,
                        r.block             = '8+10',
                        r.phase             = '""" + PHASE_TAG + """'
        RETURN count(DISTINCT r) AS releases_struct
    """)
    print(out)

    # 5. Tag v7 RELEASES that don't have a structural counterpart
    print("---- 5. Tag v7 RELEASES as csv_structural_mismatch where no structural edge exists ----")
    out = execute_cypher("""
        MATCH (f:Form1)-[r:RELEASES {phase:'v7'}]->(c:Component)
        WHERE NOT EXISTS { (f)-[:RELEASES_STRUCT]->(c) }
        SET r.pn_verified_csv = 'csv_structural_mismatch',
            r.pn_verified_csv_phase = '""" + PHASE_TAG + """'
        RETURN count(r) AS tagged_mismatch;

        MATCH (f:Form1)-[r:RELEASES {phase:'v7'}]->(c:Component)
        WHERE EXISTS { (f)-[:RELEASES_STRUCT]->(c) }
        SET r.pn_verified_csv = 'csv_structural_match',
            r.pn_verified_csv_phase = '""" + PHASE_TAG + """'
        RETURN count(r) AS tagged_match;
    """)
    print(out)

    # 6. Verification
    print()
    print("==== Verification ====")
    print("-- NiCad battery Form 1 (form1::c608f9ce-aade-468c-9e46-1a5a5686899f) --")
    out = execute_cypher("""
        MATCH (f:Form1 {value:'form1::c608f9ce-aade-468c-9e46-1a5a5686899f'})
        OPTIONAL MATCH (f)-[r1:RELEASES_PN_STRUCT]->(pn:PartNumber)
        OPTIONAL MATCH (f)-[r2:RELEASES_SN_STRUCT]->(sn:SerialNumber)
        OPTIONAL MATCH (f)-[r3:RELEASES_STRUCT]->(c:Component)
        RETURN
          collect(DISTINCT pn.value) AS struct_pns,
          collect(DISTINCT sn.value) AS struct_sns,
          collect(DISTINCT c.value) AS struct_components,
          f.block_3_form_tracking_no AS tracking_no,
          f.signer_name AS signer,
          f.signer_block AS signer_block,
          f.cert_number AS cert_number,
          f.release_date_iso AS release_date,
          f.block_11_status_norm AS disposition;
    """)
    print(out)

    print("-- ITT Aerospace valve Form 1 (form1::BV4R090M) - was missing PNs --")
    out = execute_cypher("""
        MATCH (f:Form1 {value:'form1::BV4R090M'})
        OPTIONAL MATCH (f)-[r1:RELEASES_PN_STRUCT]->(pn:PartNumber)
        OPTIONAL MATCH (f)-[r2:RELEASES_SN_STRUCT]->(sn:SerialNumber)
        OPTIONAL MATCH (f)-[r3:RELEASES_STRUCT]->(c:Component)
        WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }
        RETURN
          collect(DISTINCT pn.value) AS struct_pns,
          collect(DISTINCT sn.value) AS struct_sns,
          collect(DISTINCT c.value) AS effective_components,
          f.block_3_form_tracking_no AS tracking_no,
          f.signer_name AS signer,
          f.signer_block AS signer_block,
          f.cert_number AS cert_number,
          f.release_date_iso AS release_date,
          f.block_11_status_norm AS disposition;
    """)
    print(out)

    print("-- Bombardier B0462730 Form 1 (was 20 RELEASES, should split per-page) --")
    out = execute_cypher("""
        MATCH (f:Form1 {value:'form1::B0462730 / 12 - 58'})
        OPTIONAL MATCH (f)-[r1:RELEASES_PN_STRUCT]->(pn:PartNumber)
        OPTIONAL MATCH (f)-[r2:RELEASES_SN_STRUCT]->(sn:SerialNumber)
        OPTIONAL MATCH (f)-[r3:RELEASES_STRUCT]->(c:Component)
        RETURN
          collect(DISTINCT pn.value) AS struct_pns,
          collect(DISTINCT sn.value) AS struct_sns,
          collect(DISTINCT c.value) AS struct_components;
    """)
    print(out)

    print("-- v7 RELEASES retag distribution --")
    out = execute_cypher("""
        MATCH (f:Form1)-[r:RELEASES {phase:'v7'}]->()
        RETURN coalesce(r.pn_verified_csv, 'untagged') AS pn_verified_csv, count(r) AS n
        ORDER BY n DESC;
    """)
    print(out)

    print("-- Overall edge counts --")
    out = execute_cypher("""
        MATCH (f:Form1)-[r]->()
        WITH type(r) AS edge, count(r) AS n
        RETURN edge, n ORDER BY n DESC;
    """)
    print(out)

    print()
    print("==== migration complete ====")


if __name__ == "__main__":
    main()
