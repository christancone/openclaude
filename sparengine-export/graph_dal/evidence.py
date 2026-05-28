"""Evidence-record writers (Layer 5 — page-level evidence).

Owns the eleven structured evidence-record types that upstream OCR extracts
per page. All are created in Phase 1 (Q12a — evidence records are page-
level data extracted from the CSV's ``extracted_json``, not derivations).

Writers in this module:

    write_form1            — :Form1
    write_crs              — :CRS
    write_work_package     — :WorkPackage
    write_job_card         — :JobCard
    write_non_routine_card — :NonRoutineCard
    write_repair           — :Repair
    write_modification     — :Modification
    write_stc              — :STC
    write_borescope_report — :BorescopeReport
    write_ndt_report       — :NDTReport
    write_dent_buckle_entry — :DentBuckleEntry

Every writer enforces the golden rule (Q7) — calling without
``evidence_page_uid`` and ``evidence_quote`` raises ``GoldenRuleViolation``
**before** the MERGE runs. Every writer wires the
``(:Page)-[:CARRIES {quote}]->(:Record)`` edge after the MERGE, so the
resulting node is never orphaned from its evidence.

Pattern per writer
------------------
    1. ``require_evidence`` — gate the call on the golden rule.
    2. MERGE the record node with its typed properties.
    3. ``link_page_carries`` — wire :Page-[:CARRIES]->record.
    4. ``link_date`` for any dated property the record carries.
    5. Return the canonical ``value`` of the merged record.
"""

from __future__ import annotations

from typing import Any

from ._evidence_helpers import link_page_carries, require_evidence
from .date_node import link_date
from ._phase_tag import current_phase


# =============================================================================
#  :Form1
# =============================================================================
#
# EASA Form 1, FAA 8130-3, or "tag" — release-to-service certificate. The
# regulator-recognised primary evidence for a serviceable component.
#
# Block-11 carries the status (Serviceable / As-Removed / Inspected etc.).
# Block-12 carries the work description (free text).
# Block-13 carries the issue date.
# Block-14a/14b carry the signatures (modelled as :SIGNED_BY in event.py).

_WRITE_FORM1_CYPHER = """
MERGE (n:Form1 {asset_id: $asset_id, value: $value})
ON CREATE SET n.kind                       = $kind,
              n.block_3_form_tracking_no   = $block_3_form_tracking_no,
              n.block_4_issuer_text        = $block_4_issuer_text,
              n.block_4_part145_number     = $block_4_part145_number,
              n.block_8_pn                 = $block_8_pn,
              n.block_9_quantity           = $block_9_quantity,
              n.block_10_sn                = $block_10_sn,
              n.block_7_batch              = $block_7_batch,
              n.block_7_sn_range_text      = $block_7_sn_range_text,
              n.block_11_status            = $block_11_status,
              n.block_12_text              = $block_12_text,
              n.block_12_mod_status        = $block_12_mod_status,
              n.block_12_tsn_at_release    = $block_12_tsn_at_release,
              n.block_12_csn_at_release    = $block_12_csn_at_release,
              n.block_13_date              = $block_13_date_iso,
              n.block_13a_basis            = $block_13a_basis,
              n.block_13b_name             = $block_13b_name,
              n.block_13c_cert_number      = $block_13c_cert_number,
              n.is_batch_cert              = $is_batch_cert,
              n.batch_cert_reason          = $batch_cert_reason
ON MATCH  SET n.kind                       = coalesce($kind, n.kind),
              n.block_3_form_tracking_no   = coalesce($block_3_form_tracking_no, n.block_3_form_tracking_no),
              n.block_4_issuer_text        = coalesce($block_4_issuer_text, n.block_4_issuer_text),
              n.block_4_part145_number     = coalesce($block_4_part145_number, n.block_4_part145_number),
              n.block_8_pn                 = coalesce($block_8_pn, n.block_8_pn),
              n.block_9_quantity           = coalesce($block_9_quantity, n.block_9_quantity),
              n.block_10_sn                = coalesce($block_10_sn, n.block_10_sn),
              n.block_7_batch              = coalesce($block_7_batch, n.block_7_batch),
              n.block_7_sn_range_text      = coalesce($block_7_sn_range_text, n.block_7_sn_range_text),
              n.block_11_status            = coalesce($block_11_status, n.block_11_status),
              n.block_12_text              = coalesce($block_12_text, n.block_12_text),
              n.block_12_mod_status        = coalesce($block_12_mod_status, n.block_12_mod_status),
              n.block_12_tsn_at_release    = coalesce($block_12_tsn_at_release, n.block_12_tsn_at_release),
              n.block_12_csn_at_release    = coalesce($block_12_csn_at_release, n.block_12_csn_at_release),
              n.block_13_date              = coalesce($block_13_date_iso, n.block_13_date),
              n.block_13a_basis            = coalesce($block_13a_basis, n.block_13a_basis),
              n.block_13b_name             = coalesce($block_13b_name, n.block_13b_name),
              n.block_13c_cert_number      = coalesce($block_13c_cert_number, n.block_13c_cert_number),
              n.is_batch_cert              = coalesce($is_batch_cert, n.is_batch_cert),
              n.batch_cert_reason          = coalesce($batch_cert_reason, n.batch_cert_reason)
RETURN n.value AS value
"""


def write_form1(
    tx: Any,
    *,
    asset_id: str,
    value: str,                                 # natural key (preferred: block 3 Form Tracking No)
    evidence_page_uid: str,                     # required (golden rule)
    evidence_quote: str,                        # required (golden rule)
    kind: str | None = None,                    # easa | faa | tcca | dual | tag
    # Block-level structural fields. Each is optional but every available one
    # should be passed; Phase 5 reads block_8_pn / block_10_sn to wire the
    # authoritative Form1 -> Component edge. Phase 6 reads block_12_mod_status
    # to wire POST_SB_RELEASE. Phase 7 reads block_3_form_tracking_no to detect
    # merged-cert nodes that need splitting.
    block_3_form_tracking_no: str | None = None,
    block_4_issuer_text: str | None = None,
    block_4_part145_number: str | None = None,
    block_8_pn: str | None = None,
    block_9_quantity: int | None = None,        # critical for bulk/batch certs (e.g. 348 hoses)
    block_10_sn: str | None = None,             # NULL for non-serialised parts — DO NOT write "N/A"
    block_7_batch: str | None = None,
    block_7_sn_range_text: str | None = None,
    block_11_status: str | None = None,         # Serviceable | As-Removed | Overhauled | Repaired | Inspected
    block_12_text: str | None = None,
    block_12_mod_status: str | None = None,     # comma-separated SB list e.g. "17,19,20,22,24,32,40"
    block_12_tsn_at_release: float | None = None,
    block_12_csn_at_release: int | None = None,
    block_13_date_iso: str | None = None,
    block_13a_basis: str | None = None,
    block_13b_name: str | None = None,
    block_13c_cert_number: str | None = None,
    is_batch_cert: bool | None = None,          # true when this cert releases a population/lot, not a serialised unit
    batch_cert_reason: str | None = None,       # "block_7_batch_ref" | "qty_gt_1_no_sn" | "block_10_sentinel"
) -> str:
    """MERGE :Form1 + :CARRIES from page + :ON_DATE for block 13.

    The ``value`` natural key SHOULD be the block 3 Form Tracking Number
    (e.g. ``00029737``). Falling back to block 13c (the certifier's approval
    ref) collapses every cert signed by the same engineer into one node —
    see CL650-6134 case study. Phase 1 should derive ``value`` as:

        value = block_3_form_tracking_no or f"form1::{page_uid}"

    (NEVER use block 13c / block 14c / approval ref, which is engineer-scoped.)
    """
    require_evidence(
        label="Form1",
        value=value,
        evidence_page_uid=evidence_page_uid,
        evidence_quote=evidence_quote,
    )
    # Strip sentinel values out of block_10_sn before writing — never persist
    # "N/A" / "NA" / "TBD" / etc. as a serial number string. See _normalize.is_noise_identifier.
    from ._normalize import is_noise_identifier
    if block_10_sn and is_noise_identifier(block_10_sn):
        block_10_sn = None
    tx.run(
        _WRITE_FORM1_CYPHER,
        asset_id=asset_id,
        value=value,
        kind=kind,
        block_3_form_tracking_no=block_3_form_tracking_no,
        block_4_issuer_text=block_4_issuer_text,
        block_4_part145_number=block_4_part145_number,
        block_8_pn=block_8_pn,
        block_9_quantity=block_9_quantity,
        block_10_sn=block_10_sn,
        block_7_batch=block_7_batch,
        block_7_sn_range_text=block_7_sn_range_text,
        block_11_status=block_11_status,
        block_12_text=block_12_text,
        block_12_mod_status=block_12_mod_status,
        block_12_tsn_at_release=block_12_tsn_at_release,
        block_12_csn_at_release=block_12_csn_at_release,
        block_13_date_iso=block_13_date_iso,
        block_13a_basis=block_13a_basis,
        block_13b_name=block_13b_name,
        block_13c_cert_number=block_13c_cert_number,
        is_batch_cert=is_batch_cert,
        batch_cert_reason=batch_cert_reason,
    ).consume()
    link_page_carries(
        tx,
        asset_id=asset_id,
        source_uid=value,
        source_label="Form1",
        page_uid=evidence_page_uid,
        quote=evidence_quote,
    )
    if block_13_date_iso:
        link_date(
            tx,
            asset_id=asset_id,
            source_uid=value,
            source_label="Form1",
            role="block_13",
            date_iso=block_13_date_iso,
        )
    return value


# =============================================================================
#  :CRS — Certificate of Release to Service
# =============================================================================

_WRITE_CRS_CYPHER = """
MERGE (n:CRS {asset_id: $asset_id, value: $value})
ON CREATE SET n.date = $date_iso,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.date = coalesce($date_iso, n.date)
RETURN n.value AS value
"""


def write_crs(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # CRS number
    evidence_page_uid: str,
    evidence_quote: str,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="CRS", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(_WRITE_CRS_CYPHER, asset_id=asset_id, value=value, date_iso=date_iso,
        created_in_phase=current_phase(),).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="CRS",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="CRS",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :WorkPackage — dual-natured (Q11a): page-level cover sheet + container
# =============================================================================
#
# Phase 1 creates the node with its cover-sheet :CARRIES anchor.
# Phase 6 derives :INCLUDES edges to JobCards/NRCs/CRSs/Form1s by matching
# WO numbers across the dossier.

_WRITE_WORK_PACKAGE_CYPHER = """
MERGE (n:WorkPackage {asset_id: $asset_id, value: $value})
ON CREATE SET n.package_name = $package_name, n.date = $date_iso,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.package_name = coalesce($package_name, n.package_name),
              n.date         = coalesce($date_iso, n.date)
RETURN n.value AS value
"""


def write_work_package(
    tx: Any,
    *,
    asset_id: str,
    value: str,                        # WO number
    evidence_page_uid: str,
    evidence_quote: str,
    package_name: str | None = None,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="WorkPackage", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_WORK_PACKAGE_CYPHER,
        asset_id=asset_id, value=value, package_name=package_name, date_iso=date_iso,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="WorkPackage",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="WorkPackage",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :JobCard
# =============================================================================

_WRITE_JOB_CARD_CYPHER = """
MERGE (n:JobCard {asset_id: $asset_id, value: $value})
ON CREATE SET n.ata = $ata, n.accomplished = $accomplished_iso,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.ata          = coalesce($ata, n.ata),
              n.accomplished = coalesce($accomplished_iso, n.accomplished)
RETURN n.value AS value
"""


def write_job_card(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # card number
    evidence_page_uid: str,
    evidence_quote: str,
    ata: str | None = None,           # ATA chapter (string form, e.g. "32-11-04")
    accomplished_iso: str | None = None,
) -> str:
    require_evidence(
        label="JobCard", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_JOB_CARD_CYPHER,
        asset_id=asset_id, value=value, ata=ata, accomplished_iso=accomplished_iso,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="JobCard",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if accomplished_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="JobCard",
            role="accomplished", date_iso=accomplished_iso,
        )
    return value


# =============================================================================
#  :NonRoutineCard
# =============================================================================

_WRITE_NRC_CYPHER = """
MERGE (n:NonRoutineCard {asset_id: $asset_id, value: $value})
ON CREATE SET n.status = $status, n.description = $description,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.status      = coalesce($status, n.status),
              n.description = coalesce($description, n.description)
RETURN n.value AS value
"""


def write_non_routine_card(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # NRC number
    evidence_page_uid: str,
    evidence_quote: str,
    status: str | None = None,
    description: str | None = None,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="NonRoutineCard", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_NRC_CYPHER,
        asset_id=asset_id, value=value, status=status, description=description,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="NonRoutineCard",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="NonRoutineCard",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :Repair
# =============================================================================

_WRITE_REPAIR_CYPHER = """
MERGE (n:Repair {asset_id: $asset_id, value: $value})
ON CREATE SET n.kind                 = $kind,
              n.location              = $location,
              n.approved_data_ref     = $approved_data_ref,
              n.ndt_required          = $ndt_required,
              n.ndt_done              = $ndt_done,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.kind                 = coalesce($kind, n.kind),
              n.location              = coalesce($location, n.location),
              n.approved_data_ref     = coalesce($approved_data_ref, n.approved_data_ref),
              n.ndt_required          = coalesce($ndt_required, n.ndt_required),
              n.ndt_done              = coalesce($ndt_done, n.ndt_done)
RETURN n.value AS value
"""


def write_repair(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # repair id (canonical)
    evidence_page_uid: str,
    evidence_quote: str,
    kind: str | None = None,
    location: str | None = None,
    approved_data_ref: str | None = None,
    ndt_required: bool | None = None,
    ndt_done: bool | None = None,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="Repair", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_REPAIR_CYPHER,
        asset_id=asset_id, value=value,
        kind=kind, location=location,
        approved_data_ref=approved_data_ref,
        ndt_required=ndt_required, ndt_done=ndt_done,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="Repair",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="Repair",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :Modification
# =============================================================================

_WRITE_MODIFICATION_CYPHER = """
MERGE (n:Modification {asset_id: $asset_id, value: $value})
ON CREATE SET n.ata = $ata,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.ata = coalesce($ata, n.ata)
RETURN n.value AS value
"""


def write_modification(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # mod number
    evidence_page_uid: str,
    evidence_quote: str,
    ata: str | None = None,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="Modification", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(_WRITE_MODIFICATION_CYPHER, asset_id=asset_id, value=value, ata=ata,
        created_in_phase=current_phase(),).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="Modification",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="Modification",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :STC — Supplemental Type Certificate (per-asset embodiment record)
# =============================================================================

_WRITE_STC_CYPHER = """
MERGE (n:STC {asset_id: $asset_id, value: $value})
RETURN n.value AS value
"""


def write_stc(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # STC number
    evidence_page_uid: str,
    evidence_quote: str,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="STC", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(_WRITE_STC_CYPHER, asset_id=asset_id, value=value,
        created_in_phase=current_phase(),).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="STC",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="STC",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :BorescopeReport
# =============================================================================

_WRITE_BORESCOPE_REPORT_CYPHER = """
MERGE (n:BorescopeReport {asset_id: $asset_id, value: $value})
ON CREATE SET n.engine_position    = $engine_position,
              n.findings_severity  = $findings_severity,
              n.date               = $date_iso,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.engine_position    = coalesce($engine_position, n.engine_position),
              n.findings_severity  = coalesce($findings_severity, n.findings_severity),
              n.date               = coalesce($date_iso, n.date)
RETURN n.value AS value
"""


def write_borescope_report(
    tx: Any,
    *,
    asset_id: str,
    value: str,                          # report id
    evidence_page_uid: str,
    evidence_quote: str,
    engine_position: str | None = None,  # e.g. "ENG#1"
    findings_severity: str | None = None,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="BorescopeReport", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_BORESCOPE_REPORT_CYPHER,
        asset_id=asset_id, value=value,
        engine_position=engine_position,
        findings_severity=findings_severity,
        date_iso=date_iso,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="BorescopeReport",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="BorescopeReport",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :NDTReport — non-destructive testing report
# =============================================================================

_WRITE_NDT_REPORT_CYPHER = """
MERGE (n:NDTReport {asset_id: $asset_id, value: $value})
ON CREATE SET n.method = $method, n.result = $result,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.method = coalesce($method, n.method),
              n.result = coalesce($result, n.result)
RETURN n.value AS value
"""


def write_ndt_report(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # report id
    evidence_page_uid: str,
    evidence_quote: str,
    method: str | None = None,        # eddy | ultrasonic | dye-pen | MPI | other
    result: str | None = None,
    date_iso: str | None = None,
) -> str:
    require_evidence(
        label="NDTReport", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_NDT_REPORT_CYPHER,
        asset_id=asset_id, value=value, method=method, result=result,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="NDTReport",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="NDTReport",
            role="dated", date_iso=date_iso,
        )
    return value


# =============================================================================
#  :DentBuckleEntry — structural condition log entry
# =============================================================================

_WRITE_DENT_BUCKLE_CYPHER = """
MERGE (n:DentBuckleEntry {asset_id: $asset_id, value: $value})
ON CREATE SET n.location          = $location,
              n.dimensions         = $dimensions,
              n.repair_record_ref  = $repair_record_ref,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.location          = coalesce($location, n.location),
              n.dimensions         = coalesce($dimensions, n.dimensions),
              n.repair_record_ref  = coalesce($repair_record_ref, n.repair_record_ref)
RETURN n.value AS value
"""


def write_dent_buckle_entry(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # entry id
    evidence_page_uid: str,
    evidence_quote: str,
    location: str | None = None,
    dimensions: str | None = None,    # free text — e.g. "0.5in x 0.25in"
    repair_record_ref: str | None = None,
) -> str:
    require_evidence(
        label="DentBuckleEntry", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_DENT_BUCKLE_CYPHER,
        asset_id=asset_id, value=value,
        location=location, dimensions=dimensions, repair_record_ref=repair_record_ref,
        created_in_phase=current_phase(),
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="DentBuckleEntry",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    return value
