"""Phase 1 — Corpus indexing for CL650-6134.

Streams the Full Challenger dossier CSV one chunk at a time and writes:
- :Asset (seeded)
- :Document, :Page, :DocumentType
- Evidence records (:Form1, :CRS, :WorkPackage, :JobCard, :NonRoutineCard,
  :Repair, :Modification, :STC, :BorescopeReport, :NDTReport, :DentBuckleEntry)
- :Stamp + :HAS_STAMP + :BINDS_TO (when high-confidence)
- Connector identifiers (:PartNumber, :SerialNumber, :CertificateNumber,
  :PurchaseOrder, :DrawingNumber, :BatchNumber, :TechLogPage, :Reference)
- External standards (:ATAChapter, :ServiceBulletin, :AirworthinessDirective,
  :EngineeringOrder, :RegulatoryRef)
- :Date materialisation via :ON_DATE
- All :MENTIONS_*, :COVERS_ATA, :REFS, :CITES edges

Form 1 specifics (the 10 critical lessons from the previous build):
1. Form1 natural key = block 3 form_tracking_number (NOT block 13c/14c approval ref)
2. Block 8 PN cells can carry multiple PNs separated by newline (vendor + OEM)
3. EASA single-item header_fields variant — fall back to entity location_context
4. Header substring detection used carefully — see _parse_table_indices
5. Date precedence: block 14e > block 13e > release_to_service event date > metadata.dates[0]
6. Signer fallback walks stamps_and_signatures for block 13b/14b mentions
7. Form1->Component link deferred to Phase 5 (here we only write structural data)
8. No vendor_oem_co_mention alias tier
9. Disposition normalisation deferred to Phase 5
10. Stamp BINDS_TO wired when binding_confidence='high' and target_type='event'
    can be resolved to a Form 1 on the same page.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import orjson
import pandas as pd


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase1.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name
from graph_dal._doctype_to_record import derive_evidence_record_kinds
from graph_dal._normalize import normalize_date, is_noise_identifier
from graph_dal._phase_tag import phase
from graph_dal.asset import write_asset
from graph_dal.connector import (
    REFERENCE_TYPES,
    link_mentions_batch, link_mentions_cert, link_mentions_drawing,
    link_mentions_pn, link_mentions_po, link_mentions_sn,
    link_mentions_techlog_page, link_refs,
    page_mentions_pn, page_mentions_sn, page_mentions_cert,
    page_mentions_po, page_mentions_drawing, page_mentions_batch,
    page_mentions_techlog, page_refs,
    write_batch_number, write_certificate_number, write_drawing_number,
    write_part_number, write_purchase_order, write_reference,
    write_serial_number, write_tech_log_page,
)
from graph_dal.document import (
    write_document, write_document_type, write_page,
)
from graph_dal.errors import GoldenRuleViolation, VerificationFailed
from graph_dal.evidence import (
    write_borescope_report, write_crs, write_dent_buckle_entry,
    write_form1, write_job_card, write_modification,
    write_ndt_report, write_non_routine_card, write_repair,
    write_stc, write_work_package,
)
from graph_dal.external_standards import (
    link_cites, link_covers_ata, link_mentions_ad, link_mentions_eo,
    link_mentions_sb, page_covers_ata, page_mentions_sb, page_mentions_ad,
    page_mentions_eo, page_cites,
    write_airworthiness_directive, write_ata_chapter,
    write_engineering_order, write_regulatory_ref, write_service_bulletin,
)
from graph_dal.stamp import write_stamp, link_stamp_binds_to
from graph_dal.verify import verify_phase_1, verify_schema


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
CSV_PATH = Path("D:/work/openclaude/csvs/Full challenger dossier.csv")
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"

# Universal SN blocklist criteria.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _is_blocked_sn(value: str | None) -> bool:
    """Universal noise gate for SerialNumbers (and PartNumbers — same rules).

    Delegates to ``graph_dal._normalize.is_noise_identifier`` so the rule
    is one canonical thing across DAL writers and phase scripts. Examples
    that return True: ``N/A``, ``TBD``, ``-``, ``see remarks``, year
    strings like ``2019``, ISO dates like ``2019-08-27``, single chars.
    """
    return is_noise_identifier(value)


# Reference number type → routing target.
TYPED_CONNECTOR_BY_OCR_TYPE = {
    "part_number": "pn",
    "serial_number": "sn", "esn": "sn", "msn": "sn",
    "certificate_number": "cert", "approval_number": "cert",
    "drawing_number": "drawing", "batch_number": "batch",
    "sb_number": "sb", "ad_number": "ad",
    "eo_number": "eo",
    "purchase_order": "po", "po": "po",
    "tech_log_page": "techlog", "techlog_page": "techlog",
    "work_order": "ref_tracking", "task_card_number": "ref_doc_control",
    "nrc_number": "ref_doc_control",
    "form_tracking_number": "ref_tracking",
    "document_control_number": "ref_doc_control",
}

REFERENCE_TYPE_BY_OCR_TYPE = {
    "approval": "approval",
    "tracking": "tracking",
    "report": "report",
    "amendment": "amendment",
    "doc_control": "doc_control",
    "config": "config",
    "project": "project",
    "docket": "docket",
    "invoice": "invoice",
}

ENTITY_TO_CONNECTOR = {
    "part_number": "pn",
    "serial_number": "sn", "esn": "sn", "msn": "sn",
    "certificate_number": "cert", "approval_number": "cert",
    "drawing_number": "drawing", "batch_number": "batch",
    "sb_number": "sb", "ad_number": "ad",
}


# -----------------------------------------------------------------------------
#  Form 1 entity extraction (the heart of this phase for CL650)
# -----------------------------------------------------------------------------

# Block number regex from location_context — captures "Block N" or "Block Na/Nb/...".
_BLOCK_RE = re.compile(r"\bblock\s*(\d{1,2}[a-z]?)\b", re.IGNORECASE)
# Header field N pattern (FAA variant).
_HEADER_FIELD_RE = re.compile(r"\bheader\s+field\s+(\d{1,2}[a-z]?)\b", re.IGNORECASE)


def _entity_block(loc: str | None) -> str | None:
    if not loc:
        return None
    m = _BLOCK_RE.search(loc)
    if m:
        return m.group(1).lower()
    m = _HEADER_FIELD_RE.search(loc)
    if m:
        return m.group(1).lower()
    return None


def _split_pn_cell(value: str | None) -> list[str]:
    """A block-8 cell can hold multiple PNs separated by newline/comma/semicolon."""
    if not value:
        return []
    raw = str(value).replace("\r\n", "\n")
    parts = []
    for piece in re.split(r"[\n,;]+", raw):
        s = piece.strip()
        if s and s.upper() not in {"NONE", "N/A", "NA"}:
            parts.append(s)
    return parts


def _split_sn_cell(value: str | None) -> list[str]:
    """A block-10 cell can hold multiple SNs (e.g. 'S/N 4005\nS/N 4006\nS/N 4007')."""
    if not value:
        return []
    raw = str(value).replace("\r\n", "\n")
    tokens = []
    for piece in re.split(r"[\n,;]+", raw):
        s = piece.strip()
        if not s:
            continue
        # Strip "S/N ", "SN ", "Serial:" prefixes if present.
        s = re.sub(r"^(?:s\s*/\s*n|sn|serial)\s*[:.\-]?\s*", "", s, flags=re.IGNORECASE).strip()
        if s and s.upper() not in {"NONE", "N/A", "NA"}:
            tokens.append(s)
    return tokens


def _parse_table_indices(headers: list[str]) -> dict[str, int]:
    """Map column meaning → index from a parts-table header row."""
    idx = {}
    for i, h in enumerate(headers or []):
        hl = (h or "").lower().strip()
        if not hl:
            continue
        # Part number — handle "Part No.", "8. Part Number", "P/N", etc.
        if "part" in hl and (
            "number" in hl or "no." in hl or "no " in hl
            or hl.endswith("no") or hl == "part" or hl.endswith("part")
            or "p/n" in hl
        ):
            idx.setdefault("pn", i)
        elif "p/n" in hl:
            idx.setdefault("pn", i)
        elif "serial" in hl or "s/n" in hl or "batch" in hl:
            idx.setdefault("sn", i)
        elif "descr" in hl:
            idx.setdefault("description", i)
        elif "qty" in hl or "quantity" in hl:
            idx.setdefault("qty", i)
        elif "status" in hl or "work" in hl:
            idx.setdefault("status", i)
        elif hl.startswith("item") or hl == "6" or "6. item" in hl:
            idx.setdefault("item", i)
    return idx


def _extract_form1_fields(ext: dict, page_uid: str) -> dict:
    """Pull all Form-1 block-level fields from the page payload.

    Reads `header_fields` (preferred when present), `entities[]` (canonical
    surface), `tables[]` (parts-table), and `stamps_and_signatures[]`
    (signer fallback). Returns a dict with keys suitable for `write_form1`
    kwargs plus `pns: list[str]`, `sns: list[str]`.
    """
    entities = ext.get("entities") or []
    tables = ext.get("tables") or []
    stamps = ext.get("stamps_and_signatures") or []
    metadata = ext.get("metadata") or {}
    events = ext.get("events") or []
    header_fields = ext.get("header_fields") if isinstance(ext.get("header_fields"), dict) else {}

    f1: dict = {
        "tracking_no": None,
        "issuer_text": None,
        "part145_number": None,
        "block_8_pn": None,
        "block_9_quantity": None,        # NEW — non-serialised parts carry qty>1 here (batch certs)
        "block_10_sn": None,
        "block_7_batch": None,
        "block_11_status": None,
        "block_12_text": None,
        "block_12_mod_status": None,
        "block_13_date": None,
        "block_13a_basis": None,
        "block_13b_name": None,
        "block_13c_cert_number": None,
        "signer_name": None,
        "signer_block": None,
        "signer_cert": None,
        "pns": [],
        "sns": [],
        "batch": None,
        "work_order": None,
        "is_batch_cert": False,          # NEW — true when block 10 is N/A and qty>1, or block 7 carries a Lot/Batch ref
        "batch_cert_reason": None,       # NEW — explain why is_batch_cert was set
    }

    # 0. Walk header_fields when present (most reliable when it's there).
    def _hf_get(*keys: str) -> str | None:
        for k_pattern in keys:
            for k, v in (header_fields or {}).items():
                if k_pattern in k.lower() and isinstance(v, str) and v.strip():
                    return v.strip()
        return None

    f1["tracking_no"] = _hf_get("3. form tracking", "3.form tracking", "tracking n")
    f1["issuer_text"] = _hf_get("4. organization", "4. approved organization", "4. approving organization", "4.organization")
    # block 5 work order
    f1["work_order"] = _hf_get("5. work order", "5. component work", "5.work")
    # block 11 status
    f1["block_11_status"] = _hf_get("11. status", "11.status")
    # block 14e/13e date
    date_raw = _hf_get("14e. date", "14e.date", "13e. date", "13e.date")
    if date_raw:
        f1["block_13_date"] = normalize_date(date_raw) or date_raw
    # block 14c/13c approval ref (signer cert)
    cert_raw = _hf_get("14c. approval", "14c.approval", "14c. certificate", "13c. approval", "13c.approval", "13c. certificate")
    if cert_raw:
        f1["signer_cert"] = cert_raw
        f1["block_13c_cert_number"] = cert_raw
    # block 14d/13d name
    name_raw = _hf_get("14d. name", "14d.name", "13d. name", "13d.name")
    if name_raw:
        f1["signer_name"] = name_raw
        f1["signer_block"] = "14b" if "14d" in (" ".join((header_fields or {}).keys()).lower()) else "13b"
    # block 8 PN, block 10 SN — pull from header_fields for single-item EASA variant.
    pn_raw_hf = _hf_get("8. part", "8.part")
    if pn_raw_hf:
        for pn in _split_pn_cell(pn_raw_hf):
            if pn not in f1["pns"]:
                f1["pns"].append(pn)
    sn_raw_hf = _hf_get("10. serial", "10.serial", "10. serial number", "10.serial/batch", "10. serial/batch")
    if sn_raw_hf:
        for sn in _split_sn_cell(sn_raw_hf):
            if sn not in f1["sns"]:
                f1["sns"].append(sn)
    # block 7 batch
    batch_raw = _hf_get("7. batch", "7.batch", "7. lot")
    if batch_raw:
        f1["batch"] = batch_raw
    # block 9 quantity — critical for bulk/non-serialised certs (hoses qty 348, etc.)
    qty_raw = _hf_get("9. qty", "9.qty", "9. quantity", "9.quantity")
    if qty_raw:
        try:
            f1["block_9_quantity"] = int(re.sub(r"[^\d]", "", str(qty_raw)) or 0) or None
        except (ValueError, TypeError):
            f1["block_9_quantity"] = None
    # block 12 mod status
    mod_raw = _hf_get("12. mod", "12.mod", "modification status")
    if mod_raw:
        f1["block_12_mod_status"] = mod_raw

    # 1. Walk entities[] keyed by location_context / entity_type.
    entity_by_id = {e.get("entity_id"): e for e in entities if isinstance(e, dict)}
    for e in entities:
        if not isinstance(e, dict):
            continue
        et = (e.get("entity_type") or "").lower()
        val = (e.get("value") or "").strip() if isinstance(e.get("value"), str) else None
        loc = e.get("location_context") or ""
        block = _entity_block(loc)

        if not val:
            continue

        # Block 3 — tracking number.
        if et in {"form_tracking_number", "tracking_number", "document_control_number"} and (block == "3" or "3." in loc or "tracking" in loc.lower()):
            if not f1["tracking_no"]:
                f1["tracking_no"] = val
        # Block 4 — issuer (MRO/POA name).
        elif et in {"mro", "organization", "production_organization", "design_organization"} and (block == "4" or "4." in loc):
            if not f1["issuer_text"]:
                f1["issuer_text"] = val
        elif et == "other" and "block 4" in loc.lower() and not f1["issuer_text"]:
            f1["issuer_text"] = val
        # Block 5 — work order.
        elif et == "work_order" and (block == "5" or "5." in loc or "header field 5" in loc.lower()):
            f1["work_order"] = val
        # Block 8 PN.
        elif et == "part_number" and (block == "8" or "block 8" in loc.lower() or "part number column" in loc.lower() or "part no. column" in loc.lower() or "table row" in loc.lower()):
            if val not in f1["pns"]:
                f1["pns"].append(val)
        # Block 10 SN.
        elif et in {"serial_number"} and (block == "10" or "block 10" in loc.lower() or "serial/batch" in loc.lower() or "header field 10" in loc.lower() or "table row" in loc.lower()):
            if val not in f1["sns"]:
                f1["sns"].append(val)
        # Block 7 batch.
        elif et == "batch_number" and (block == "7" or "block 7" in loc.lower()):
            f1["batch"] = val
        # Block 11 — status (sometimes typed as "status" or surfaces in tables).
        elif et in {"status"} and (block == "11" or "block 11" in loc.lower()):
            f1["block_11_status"] = val
        # Block 12 mod / SB list.
        elif et in {"sb_number", "modification_status"} and (block == "12" or "block 12" in loc.lower()):
            if f1["block_12_mod_status"]:
                f1["block_12_mod_status"] += "," + val
            else:
                f1["block_12_mod_status"] = val
        # Block 13/14 — signer name / cert / date.
        elif et == "person":
            if block in {"13b", "14b", "13d", "14d"} or "13b" in loc.lower() or "14b" in loc.lower() or "13d" in loc.lower() or "14d" in loc.lower():
                if not f1["signer_name"]:
                    f1["signer_name"] = val
                    if "14" in (block or "") or "14" in loc:
                        f1["signer_block"] = "14b"
                    else:
                        f1["signer_block"] = "13b"
        elif et in {"certificate_number", "approval_number"}:
            # Block 13c / 14c.
            if block in {"13c", "14c"} or "13c" in loc.lower() or "14c" in loc.lower() or "header field 14c" in loc.lower():
                if not f1["signer_cert"]:
                    f1["signer_cert"] = val
                    f1["block_13c_cert_number"] = val
        elif et == "date":
            if block in {"13e", "14e"} or "13e" in loc.lower() or "14e" in loc.lower() or "header field 14e" in loc.lower():
                # Prefer block 14e then 13e.
                if "14e" in (block or "") or "14e" in loc.lower():
                    f1["block_13_date"] = val
                elif not f1["block_13_date"]:
                    f1["block_13_date"] = val

    # 2. Walk tables[] for parts-table style rows.
    for t in tables:
        if not isinstance(t, dict):
            continue
        headers = t.get("headers") or []
        rows = t.get("rows") or []
        idx = _parse_table_indices(headers)
        if "pn" not in idx and "sn" not in idx and "status" not in idx:
            continue  # not a parts table
        for row in rows:
            if not isinstance(row, list):
                continue
            def _cell(key):
                i = idx.get(key)
                if i is None or i >= len(row):
                    return None
                v = row[i]
                if v is None:
                    return None
                return str(v).strip()
            pn_cell = _cell("pn")
            sn_cell = _cell("sn")
            status_cell = _cell("status")
            for pn in _split_pn_cell(pn_cell):
                if pn not in f1["pns"]:
                    f1["pns"].append(pn)
            for sn in _split_sn_cell(sn_cell):
                if sn not in f1["sns"]:
                    f1["sns"].append(sn)
            if status_cell and not f1["block_11_status"]:
                f1["block_11_status"] = status_cell

    # 3. Signer fallback — walk stamps for block 13b/14b mentions.
    for st in stamps:
        if not isinstance(st, dict):
            continue
        loc = (st.get("location_context") or "").lower()
        if not f1["signer_name"] and st.get("person_name"):
            if "13b" in loc or "14b" in loc or "authoris" in loc or "authoriz" in loc or "release" in loc:
                f1["signer_name"] = st["person_name"]
                f1["signer_block"] = "14b" if "14" in loc else "13b"
                if st.get("certificate_number") and not f1["signer_cert"]:
                    f1["signer_cert"] = st["certificate_number"]
                    f1["block_13c_cert_number"] = st["certificate_number"]

    # 4. Date precedence: events[].date for release_to_service > metadata.dates[0].
    if not f1["block_13_date"]:
        for ev in events:
            if isinstance(ev, dict) and ev.get("event_type") == "release_to_service" and ev.get("date"):
                f1["block_13_date"] = ev["date"]
                break
    if not f1["block_13_date"]:
        dates = (metadata.get("dates") or [])
        if dates:
            f1["block_13_date"] = dates[0]
    f1["block_13_date"] = normalize_date(f1["block_13_date"]) or f1["block_13_date"]

    # 5. Filter OCR noise out of pns/sns BEFORE composing scalar fields.
    # The previous build wrote `:SerialNumber {value:'N/A'}` and then 90
    # phantom Components attached. Per Lukas Cheatsheet §13, sentinels like
    # "N/A" / "TBD" / "Various" / dates / "see remarks" are placeholder
    # text on the cert (legitimate for non-serialised parts!), not real
    # identifiers — they must not become graph nodes.
    f1["pns"] = [pn for pn in f1["pns"] if not is_noise_identifier(pn)]
    sns_raw_before_filter = list(f1["sns"])
    f1["sns"] = [sn for sn in f1["sns"] if not is_noise_identifier(sn)]

    # 6. Detect "this is a batch/bulk cert" — block 10 is N/A (or empty)
    #    while block 9 quantity > 1, or block 7 carries a Lot/Batch reference.
    has_sn_after_filter = bool(f1["sns"])
    qty = f1.get("block_9_quantity")
    block_10_was_noisy = bool(sns_raw_before_filter) and not has_sn_after_filter
    block_10_was_empty = not sns_raw_before_filter
    has_batch_ref = bool(f1["batch"])
    if has_batch_ref:
        f1["is_batch_cert"] = True
        f1["batch_cert_reason"] = "block_7_batch_ref"
    elif (qty or 0) > 1 and not has_sn_after_filter:
        f1["is_batch_cert"] = True
        f1["batch_cert_reason"] = "qty_gt_1_no_sn"
    elif block_10_was_noisy:
        f1["is_batch_cert"] = True
        f1["batch_cert_reason"] = "block_10_sentinel"

    # 7. Compose final scalar fields.
    if f1["pns"]:
        # Pick the longest PN as the primary value for the Form 1 node.
        f1["block_8_pn"] = max(f1["pns"], key=len)
    # block_10_sn: when there are real SNs, concatenate; otherwise NULL
    # (do NOT store "N/A" — that's the bug we're fixing).
    if f1["sns"]:
        f1["block_10_sn"] = ", ".join(f1["sns"])
    else:
        f1["block_10_sn"] = None
    if f1["batch"]:
        f1["block_7_batch"] = f1["batch"]

    return f1


# -----------------------------------------------------------------------------
#  Row processing
# -----------------------------------------------------------------------------

def _connector_write(tx, asset_id: str, conn_type: str, value: str) -> bool:
    """Dispatch table for connector writes. Returns True if written."""
    if conn_type == "pn":
        write_part_number(tx, asset_id=asset_id, value=value)
    elif conn_type == "sn":
        if _is_blocked_sn(value):
            return False
        write_serial_number(tx, asset_id=asset_id, value=value)
    elif conn_type == "cert":
        write_certificate_number(tx, asset_id=asset_id, value=value)
    elif conn_type == "drawing":
        write_drawing_number(tx, asset_id=asset_id, value=value)
    elif conn_type == "batch":
        write_batch_number(tx, asset_id=asset_id, value=value)
    elif conn_type == "po":
        write_purchase_order(tx, asset_id=asset_id, value=value)
    elif conn_type == "techlog":
        write_tech_log_page(tx, asset_id=asset_id, value=value)
    else:
        return False
    return True


def _connector_link_mention(tx, asset_id: str, conn_type: str, page_uid: str, value: str):
    if conn_type == "pn":
        page_mentions_pn(tx, asset_id=asset_id, page_uid=page_uid, pn_value=value)
    elif conn_type == "sn":
        page_mentions_sn(tx, asset_id=asset_id, page_uid=page_uid, sn_value=value)
    elif conn_type == "cert":
        page_mentions_cert(tx, asset_id=asset_id, page_uid=page_uid, cert_value=value)
    elif conn_type == "drawing":
        page_mentions_drawing(tx, asset_id=asset_id, page_uid=page_uid, drawing_value=value)
    elif conn_type == "batch":
        page_mentions_batch(tx, asset_id=asset_id, page_uid=page_uid, batch_value=value)
    elif conn_type == "po":
        page_mentions_po(tx, asset_id=asset_id, page_uid=page_uid, po_value=value)
    elif conn_type == "techlog":
        page_mentions_techlog(tx, asset_id=asset_id, page_uid=page_uid, techlog_value=value)


def _process_row(tx, row, asset_id: str, documents_seen: dict, counters: Counter):
    counters["rows_processed"] += 1
    try:
        ext = orjson.loads(row["extracted_json"])
    except Exception:
        counters["rows_failed_parse"] += 1
        return

    if not isinstance(ext, dict):
        counters["rows_failed_parse"] += 1
        return

    # OCR vintage variation: some put fields under `content`, others at root.
    content = ext.get("content") if isinstance(ext.get("content"), dict) else {}
    def _g(key, default=None):
        v = content.get(key)
        if v is None:
            v = ext.get(key)
        return v if v is not None else default

    doc_type = _g("document_type")
    title = _g("title")
    sections = _g("sections") or []
    entities = _g("entities") or []
    stamps = _g("stamps_and_signatures") or []
    metadata = _g("metadata") or {}
    events = _g("events") or []

    is_blank = bool(ext.get("is_blank"))
    is_template_empty = bool(ext.get("is_template_empty"))
    rotation = int(ext.get("rotation_hint") or row.get("rotation_deg") or 0)

    # Build text_content for fulltext search.
    text_parts = []
    if title:
        text_parts.append(str(title))
    for sec in sections:
        if isinstance(sec, dict) and "data" in sec and sec["data"]:
            text_parts.append(str(sec["data"]))
    text_content = "" if is_blank else "\n".join(text_parts)

    # ---- DocumentType ----
    if doc_type:
        write_document_type(tx, asset_id=asset_id, value=str(doc_type), name=str(doc_type))

    # ---- Document (once per document_id) ----
    doc_uid = str(row.get("document_id") or "")
    if doc_uid and doc_uid not in documents_seen:
        documents_seen[doc_uid] = []
        write_document(
            tx,
            asset_id=asset_id, value=doc_uid,
            file_name=str(row.get("file_name") or ""),
            document_type=str(doc_type) if doc_type else None,
            evidence_class=_g("evidentiary_weight"),
            title=str(title) if title else None,
            is_mis_export=bool(metadata.get("is_mis_export")) if metadata.get("is_mis_export") is not None else None,
            mis_system=metadata.get("mis_system"),
        )
        counters["documents"] += 1
    weight = _g("evidentiary_weight")
    if weight and doc_uid in documents_seen:
        documents_seen[doc_uid].append(weight)

    # ---- Page ----
    page_uid = str(row["id"])
    write_page(
        tx,
        asset_id=asset_id, value=page_uid,
        document_uid=doc_uid,
        page_index=int(row.get("page_index") or 0),
        text=text_content,
        title=str(title) if title else None,
        file_type=str(row.get("file_type") or "") or None,
        is_blank=is_blank, is_template_empty=is_template_empty,
        rotation_deg=rotation,
        s3_key=str(row.get("enhanced_s3_key") or "") or None,
        original_path=str(row.get("original_path") or "") or None,
    )
    counters["pages"] += 1

    quote_base = (str(title) if title else (text_content[:240].strip() if text_content else "")) or f"(see {page_uid}, doctype={doc_type!r})"

    # ---- Evidence records ----
    form1_uids_for_page: list[str] = []
    for kind in derive_evidence_record_kinds(doc_type):
        if kind == "form1":
            f1 = _extract_form1_fields(ext, page_uid)
            tracking = f1["tracking_no"]
            f1_value = f"form1::{tracking}" if tracking else f"form1::{page_uid}"
            form1_uids_for_page.append(f1_value)
            write_form1(
                tx, asset_id=asset_id, value=f1_value,
                evidence_page_uid=page_uid, evidence_quote=quote_base[:240],
                kind={"easa_form_one": "easa", "faa_form_8130": "faa",
                      "tcca_form_one": "tcca", "dual_release_certificate": "dual"}.get(doc_type),
                block_3_form_tracking_no=tracking,
                block_4_issuer_text=f1["issuer_text"],
                block_4_part145_number=f1["part145_number"],
                block_8_pn=f1["block_8_pn"],
                block_9_quantity=f1.get("block_9_quantity"),
                block_10_sn=f1["block_10_sn"],     # None for non-serialised parts (NOT "N/A")
                block_7_batch=f1["block_7_batch"],
                block_11_status=f1["block_11_status"],
                block_12_text=f1["block_12_text"],
                block_12_mod_status=f1["block_12_mod_status"],
                block_13_date_iso=f1["block_13_date"],
                block_13a_basis=f1["block_13a_basis"],
                block_13b_name=f1["signer_name"],
                block_13c_cert_number=f1["block_13c_cert_number"],
                is_batch_cert=f1.get("is_batch_cert") or None,
                batch_cert_reason=f1.get("batch_cert_reason"),
            )
            counters["records_form1"] += 1

            # Write the per-PN / per-SN connector nodes for the Form1's block contents.
            # The PartNumber/SerialNumber may already get written below from metadata.
            for pn in f1["pns"]:
                write_part_number(tx, asset_id=asset_id, value=pn)
                page_mentions_pn(tx, asset_id=asset_id, page_uid=page_uid, pn_value=pn)
            for sn in f1["sns"]:
                if not _is_blocked_sn(sn):
                    write_serial_number(tx, asset_id=asset_id, value=sn)
                    page_mentions_sn(tx, asset_id=asset_id, page_uid=page_uid, sn_value=sn)
        elif kind == "crs":
            crs_value = f"crs::{page_uid}"
            write_crs(tx, asset_id=asset_id, value=crs_value,
                      evidence_page_uid=page_uid, evidence_quote=quote_base[:240],
                      date_iso=normalize_date((metadata.get("dates") or [None])[0]))
            counters["records_crs"] += 1
        elif kind == "work_package":
            # Try to pull a WO number from entities.
            wo_value = None
            for e in entities:
                if isinstance(e, dict) and (e.get("entity_type") in {"work_order", "wo_number"}):
                    wo_value = e.get("value")
                    if wo_value:
                        break
            wp_value = (str(wo_value).strip() if wo_value else f"wp::{page_uid}")
            write_work_package(tx, asset_id=asset_id, value=wp_value,
                               evidence_page_uid=page_uid, evidence_quote=quote_base[:240],
                               package_name=str(title) if title else None,
                               date_iso=normalize_date((metadata.get("dates") or [None])[0]))
            counters["records_work_package"] += 1
        elif kind == "job_card":
            tc_value = None
            for e in entities:
                if isinstance(e, dict) and e.get("entity_type") == "task_card_number":
                    tc_value = e.get("value"); break
            jc_value = str(tc_value).strip() if tc_value else f"jc::{page_uid}"
            write_job_card(tx, asset_id=asset_id, value=jc_value,
                           evidence_page_uid=page_uid, evidence_quote=quote_base[:240],
                           ata=(metadata.get("ata_chapters") or [None])[0])
            counters["records_job_card"] += 1
        elif kind == "non_routine_card":
            nrc_value = None
            for e in entities:
                if isinstance(e, dict) and e.get("entity_type") == "nrc_number":
                    nrc_value = e.get("value"); break
            n_value = str(nrc_value).strip() if nrc_value else f"nrc::{page_uid}"
            write_non_routine_card(tx, asset_id=asset_id, value=n_value,
                                   evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_non_routine_card"] += 1
        elif kind == "repair":
            write_repair(tx, asset_id=asset_id, value=f"repair::{page_uid}",
                         evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_repair"] += 1
        elif kind == "modification":
            write_modification(tx, asset_id=asset_id, value=f"mod::{page_uid}",
                               evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_modification"] += 1
        elif kind == "stc":
            write_stc(tx, asset_id=asset_id, value=f"stc::{page_uid}",
                      evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_stc"] += 1
        elif kind == "borescope_report":
            write_borescope_report(tx, asset_id=asset_id, value=f"bsi::{page_uid}",
                                   evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_borescope_report"] += 1
        elif kind == "ndt_report":
            write_ndt_report(tx, asset_id=asset_id, value=f"ndt::{page_uid}",
                             evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_ndt_report"] += 1
        elif kind == "dent_buckle_entry":
            write_dent_buckle_entry(tx, asset_id=asset_id, value=f"dbe::{page_uid}",
                                    evidence_page_uid=page_uid, evidence_quote=quote_base[:240])
            counters["records_dent_buckle_entry"] += 1

    # ---- Stamps ----
    stamp_index_to_uid: dict[str, str] = {}
    for i, st in enumerate(stamps):
        if not isinstance(st, dict):
            continue
        local_id = st.get("stamp_id") or st.get("id") or f"st_{i}"
        full_id = f"{page_uid}::{local_id}"
        stamp_index_to_uid[str(local_id)] = full_id
        binds_raw = st.get("binds_to")
        # Defensive: OCR has shipped both dict and list-of-dict for binds_to.
        if isinstance(binds_raw, list):
            binds = binds_raw[0] if (binds_raw and isinstance(binds_raw[0], dict)) else {}
        elif isinstance(binds_raw, dict):
            binds = binds_raw
        else:
            binds = {}
        bind_conf = binds.get("binding_confidence")
        binding_status = (
            "bound" if bind_conf == "high"
            else "ambiguous" if bind_conf == "ambiguous"
            else "unbound" if not binds.get("target_ref") else "ambiguous"
        )
        stext = (st.get("text") or st.get("person_name") or (title or "") or "(stamp on page)")[:240]
        s_date = normalize_date(st.get("date")) or (st.get("date") if isinstance(st.get("date"), str) and len(st.get("date")) == 10 else None)
        write_stamp(
            tx, asset_id=asset_id, value=full_id, page_uid=page_uid,
            evidence_quote=stext,
            type=st.get("type"), text=st.get("text"),
            person_name=st.get("person_name"), title_role=st.get("title_role"),
            date_iso=s_date,
            certificate_number=st.get("certificate_number"),
            location_context=st.get("location_context"),
            binding_status=binding_status,
        )
        counters["stamps"] += 1

        # Stamp -[:BINDS_TO]-> Form1 is DROPPED per the simplified Form 1
        # contract (2026-05-19). Form 1 incoming is only :CARRIES from :Page.
        # The signer linkage Stamp -> Person via STAMPED_BY is wired in the
        # existing write_stamp call above (when person_name is set), and
        # Form1 -> Person via SIGNED_BY is derived in Phase 6 from
        # page+stamp+form1 co-location without the intermediate BINDS_TO edge.

    # ---- Connector identifiers: walk metadata + entities ----
    seen_keys: set[tuple[str, str]] = set()

    # Part numbers (from metadata).
    for pn in (metadata.get("part_numbers") or []):
        if not isinstance(pn, str) or not pn.strip():
            continue
        v = pn.strip()
        key = ("pn", v)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        write_part_number(tx, asset_id=asset_id, value=v)
        page_mentions_pn(tx, asset_id=asset_id, page_uid=page_uid, pn_value=v)
        counters["mention_pn"] += 1

    # Serial numbers (from metadata).
    md_sn = metadata.get("serial_number")
    md_sns = metadata.get("serial_numbers") or ([md_sn] if md_sn else [])
    for sn in md_sns:
        if not isinstance(sn, str) or not sn.strip():
            continue
        v = sn.strip()
        if _is_blocked_sn(v):
            counters["sn_blocked_total"] += 1
            continue
        key = ("sn", v)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        write_serial_number(tx, asset_id=asset_id, value=v)
        page_mentions_sn(tx, asset_id=asset_id, page_uid=page_uid, sn_value=v)
        counters["mention_sn"] += 1

    # ATA chapters.
    for ata in (metadata.get("ata_chapters") or []):
        if not isinstance(ata, str) or not ata.strip():
            continue
        v = ata.strip()
        key = ("ata", v)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        write_ata_chapter(tx, asset_id=asset_id, value=v)
        page_covers_ata(tx, asset_id=asset_id, page_uid=page_uid, ata_value=v)
        counters["mention_ata"] += 1

    # Regulatory references.
    for reg in (metadata.get("regulatory_references") or []):
        if not isinstance(reg, str) or not reg.strip():
            continue
        v = reg.strip()
        key = ("regref", v)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        write_regulatory_ref(tx, asset_id=asset_id, value=v)
        page_cites(tx, asset_id=asset_id, page_uid=page_uid, regref_value=v)
        counters["mention_regref"] += 1

    # Typed reference numbers — route to the right connector or :Reference.
    for ref in (metadata.get("reference_numbers") or []):
        if not isinstance(ref, dict):
            continue
        rtype = (ref.get("type") or "").lower().strip()
        rval = ref.get("value")
        if not isinstance(rval, str) or not rval.strip():
            continue
        v = rval.strip()
        # First try typed connector.
        conn = TYPED_CONNECTOR_BY_OCR_TYPE.get(rtype)
        if conn in {"pn", "sn", "cert", "drawing", "batch", "po", "techlog"}:
            key = (conn, v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if conn == "sn" and _is_blocked_sn(v):
                counters["sn_blocked_total"] += 1
                continue
            if _connector_write(tx, asset_id, conn, v):
                _connector_link_mention(tx, asset_id, conn, page_uid, v)
                counters[f"mention_{conn}"] += 1
            continue
        if conn == "sb":
            write_service_bulletin(tx, asset_id=asset_id, value=v)
            page_mentions_sb(tx, asset_id=asset_id, page_uid=page_uid, sb_value=v)
            counters["mention_sb"] += 1
            continue
        if conn == "ad":
            write_airworthiness_directive(tx, asset_id=asset_id, value=v)
            page_mentions_ad(tx, asset_id=asset_id, page_uid=page_uid, ad_value=v)
            counters["mention_ad"] += 1
            continue
        if conn == "eo":
            write_engineering_order(tx, asset_id=asset_id, value=v)
            page_mentions_eo(tx, asset_id=asset_id, page_uid=page_uid, eo_value=v)
            counters["mention_eo"] += 1
            continue
        # Long-tail :Reference collapse.
        ref_type_resolved = (
            "tracking" if rtype in {"work_order", "form_tracking_number", "tracking_number", "tracking"}
            else "doc_control" if rtype in {"document_control_number", "doc_control", "task_card_number", "nrc_number"}
            else "report" if rtype in {"report"}
            else "approval" if rtype in {"approval"}
            else "amendment" if rtype == "amendment"
            else "config" if rtype == "config"
            else "project" if rtype == "project"
            else "docket" if rtype == "docket"
            else "invoice" if rtype == "invoice"
            else None
        )
        if ref_type_resolved and ref_type_resolved in REFERENCE_TYPES:
            write_reference(tx, asset_id=asset_id, ref_type=ref_type_resolved, value=v)
            page_refs(tx, asset_id=asset_id, page_uid=page_uid, ref_type=ref_type_resolved, target_value=v)
            counters[f"mention_ref_{ref_type_resolved}"] += 1

    # Defensive: also walk entities[] (catches things metadata might miss).
    for e in entities:
        if not isinstance(e, dict):
            continue
        et = (e.get("entity_type") or "").lower()
        val = e.get("value")
        if not isinstance(val, str) or not val.strip():
            continue
        v = val.strip()
        target = ENTITY_TO_CONNECTOR.get(et)
        if target == "pn":
            key = ("pn", v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            write_part_number(tx, asset_id=asset_id, value=v)
            page_mentions_pn(tx, asset_id=asset_id, page_uid=page_uid, pn_value=v)
            counters["mention_pn_from_entity"] += 1
        elif target == "sn":
            if _is_blocked_sn(v):
                counters["sn_blocked_total"] += 1
                continue
            key = ("sn", v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            write_serial_number(tx, asset_id=asset_id, value=v)
            page_mentions_sn(tx, asset_id=asset_id, page_uid=page_uid, sn_value=v)
            counters["mention_sn_from_entity"] += 1
        elif target == "cert":
            key = ("cert", v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            write_certificate_number(tx, asset_id=asset_id, value=v)
            page_mentions_cert(tx, asset_id=asset_id, page_uid=page_uid, cert_value=v)
            counters["mention_cert"] += 1
        elif target == "drawing":
            key = ("drawing", v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            write_drawing_number(tx, asset_id=asset_id, value=v)
            page_mentions_drawing(tx, asset_id=asset_id, page_uid=page_uid, drawing_value=v)
            counters["mention_drawing"] += 1
        elif target == "batch":
            key = ("batch", v)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            write_batch_number(tx, asset_id=asset_id, value=v)
            page_mentions_batch(tx, asset_id=asset_id, page_uid=page_uid, batch_value=v)
            counters["mention_batch"] += 1
        elif target == "sb":
            write_service_bulletin(tx, asset_id=asset_id, value=v)
            page_mentions_sb(tx, asset_id=asset_id, page_uid=page_uid, sb_value=v)
        elif target == "ad":
            write_airworthiness_directive(tx, asset_id=asset_id, value=v)
            page_mentions_ad(tx, asset_id=asset_id, page_uid=page_uid, ad_value=v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunksize", type=int, default=500)
    ap.add_argument("--max-rows", type=int, default=None, help="for smoke testing")
    args = ap.parse_args()

    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 1 START {start.isoformat()} ==========")
    _log(f"CSV: {CSV_PATH}")
    _log(f"asset_id: {ASSET_ID}")

    driver = connect()
    verify_schema(driver)

    counters: Counter = Counter()
    documents_seen: dict[str, list[str]] = {}

    with phase("phase1_indexing"):
        # 1. Seed the asset.
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_asset(
                    tx, asset_id=ASSET_ID,
                    asset_kind="AIRCRAFT",
                    name="Bombardier Challenger 650",
                    msn="6134",
                    registration=None,
                    subtype="FIXED_WING_JET",
                    country_of_registration=None,
                )
                tx.commit()
        _log("[phase1] :Asset seeded")

        # 2. Stream CSV.
        df_iter = pd.read_csv(CSV_PATH, chunksize=args.chunksize)
        with driver.session(database=database_name()) as session:
            for chunk_idx, chunk in enumerate(df_iter):
                if args.max_rows is not None and counters["rows_processed"] >= args.max_rows:
                    break
                try:
                    with session.begin_transaction() as tx:
                        for _, row in chunk.iterrows():
                            if args.max_rows is not None and counters["rows_processed"] >= args.max_rows:
                                break
                            _process_row(tx, row, ASSET_ID, documents_seen, counters)
                        tx.commit()
                except GoldenRuleViolation:
                    raise
                except Exception as e:
                    _log(f"[phase1] chunk {chunk_idx} FAILED: {e!r}")
                    raise
                if chunk_idx % 5 == 0:
                    _log(f"[phase1] chunk {chunk_idx} OK rows_processed={counters['rows_processed']} pages={counters['pages']} stamps={counters['stamps']} records_form1={counters['records_form1']}")

        # 3. Document-level evidentiary_weight rollup.
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for doc_uid, weights in documents_seen.items():
                    if weights:
                        mode = Counter(weights).most_common(1)[0][0]
                        tx.run(
                            "MATCH (d:Document {asset_id: $aid, value: $v}) "
                            "SET d.evidence_class = $w",
                            aid=ASSET_ID, v=doc_uid, w=mode,
                        ).consume()
                tx.commit()

    # 4. Verify.
    _log("[phase1] running verify_phase_1 ...")
    counts = verify_phase_1(driver, ASSET_ID)
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 1 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 1 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 1 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")

    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
