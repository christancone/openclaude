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
ON CREATE SET n.kind             = $kind,
              n.block_11_status  = $block_11_status,
              n.block_12_text    = $block_12_text,
              n.block_13_date    = $block_13_date_iso
ON MATCH  SET n.kind             = coalesce($kind, n.kind),
              n.block_11_status  = coalesce($block_11_status, n.block_11_status),
              n.block_12_text    = coalesce($block_12_text, n.block_12_text),
              n.block_13_date    = coalesce($block_13_date_iso, n.block_13_date)
RETURN n.value AS value
"""


def write_form1(
    tx: Any,
    *,
    asset_id: str,
    value: str,                              # Form 1 number — the canonical natural key
    evidence_page_uid: str,                  # required (golden rule)
    evidence_quote: str,                     # required (golden rule)
    kind: str | None = None,                 # easa | faa | tag
    block_11_status: str | None = None,      # Serviceable | As-Removed | Inspected | …
    block_12_text: str | None = None,        # free-text description of work
    block_13_date_iso: str | None = None,    # issue date
) -> str:
    """MERGE :Form1 + :CARRIES from page + :ON_DATE for block 13."""
    require_evidence(
        label="Form1",
        value=value,
        evidence_page_uid=evidence_page_uid,
        evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_FORM1_CYPHER,
        asset_id=asset_id,
        value=value,
        kind=kind,
        block_11_status=block_11_status,
        block_12_text=block_12_text,
        block_13_date_iso=block_13_date_iso,
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
ON CREATE SET n.date = $date_iso
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
    tx.run(_WRITE_CRS_CYPHER, asset_id=asset_id, value=value, date_iso=date_iso).consume()
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
ON CREATE SET n.package_name = $package_name, n.date = $date_iso
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
ON CREATE SET n.ata = $ata, n.accomplished = $accomplished_iso
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
ON CREATE SET n.status = $status, n.description = $description
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
              n.ndt_done              = $ndt_done
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
ON CREATE SET n.ata = $ata
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
    tx.run(_WRITE_MODIFICATION_CYPHER, asset_id=asset_id, value=value, ata=ata).consume()
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
    tx.run(_WRITE_STC_CYPHER, asset_id=asset_id, value=value).consume()
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
              n.date               = $date_iso
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
ON CREATE SET n.method = $method, n.result = $result
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
              n.repair_record_ref  = $repair_record_ref
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
    ).consume()
    link_page_carries(
        tx, asset_id=asset_id, source_uid=value, source_label="DentBuckleEntry",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    return value
