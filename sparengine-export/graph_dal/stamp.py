""":Stamp writers + binding edges.

Owns: ``:Stamp`` plus the four stamp relationships:

    (:Page)-[:HAS_STAMP]->(:Stamp)               — written here
    (:Stamp)-[:STAMPED_BY]->(:Person)            — link helper
    (:Stamp)-[:CARRIES_CERT]->(:CertificateNumber) — link helper
    (:Stamp)-[:BINDS_TO {confidence, rule}]->(:Form1|:JobCard|:NRC|...)

A ``:Stamp`` is fact-bearing in the SPARENGINE sense — its evidence is
the page it sits on, anchored via ``:HAS_STAMP``. The writer requires
``page_uid`` (the page that physically carries the stamp) and a quote.
The :HAS_STAMP edge is what the verifier accepts as the page-evidence
relationship for stamps (see graph_dal/verify.py).
"""

from __future__ import annotations

from typing import Any

from ._evidence_helpers import require_evidence
from .date_node import link_date


# =============================================================================
#  :Stamp + :HAS_STAMP
# =============================================================================

_WRITE_STAMP_CYPHER = """
MERGE (s:Stamp {asset_id: $asset_id, value: $value})
ON CREATE SET s.type             = $type,
              s.text             = $text,
              s.person_name      = $person_name,
              s.title_role       = $title_role,
              s.date             = $date_iso,
              s.certificate_number = $certificate_number,
              s.location_context = $location_context,
              s.binding_status   = $binding_status
ON MATCH  SET s.type             = coalesce($type, s.type),
              s.text             = coalesce($text, s.text),
              s.person_name      = coalesce($person_name, s.person_name),
              s.title_role       = coalesce($title_role, s.title_role),
              s.date             = coalesce($date_iso, s.date),
              s.certificate_number = coalesce($certificate_number, s.certificate_number),
              s.location_context = coalesce($location_context, s.location_context),
              s.binding_status   = coalesce($binding_status, s.binding_status)
WITH s
MATCH (p:Page {asset_id: $asset_id, value: $page_uid})
MERGE (p)-[r:HAS_STAMP]->(s)
ON CREATE SET r.quote = $quote
ON MATCH  SET r.quote = $quote
RETURN s.value AS value
"""


def write_stamp(
    tx: Any,
    *,
    asset_id: str,
    value: str,                          # canonical stamp id, typically f"{page_uid}::{stamp_local_id}"
    page_uid: str,                       # the carrier page — required (golden rule)
    evidence_quote: str,                 # required (golden rule)
    type: str | None = None,             # signature | stamp | initials | approval_mark | date_stamp
    text: str | None = None,
    person_name: str | None = None,
    title_role: str | None = None,
    date_iso: str | None = None,
    certificate_number: str | None = None,
    location_context: str | None = None,
    binding_status: str | None = None,   # bound | ambiguous | unbound — Q11d
) -> str:
    """MERGE :Stamp + :HAS_STAMP edge from carrier page.

    The stamp's evidence anchor is the page that physically carries it,
    via :HAS_STAMP (Q11d). The verifier accepts :HAS_STAMP as a valid
    page-evidence relationship — same role as :EVIDENCED_BY for derived
    facts and :CARRIES for evidence records.

    The carrier-page evidence is required even for unbound stamps —
    they may not :BINDS_TO anything yet (or never), but they still have
    a page that physically carries them.
    """
    require_evidence(
        label="Stamp", value=value,
        evidence_page_uid=page_uid, evidence_quote=evidence_quote,
    )
    record = tx.run(
        _WRITE_STAMP_CYPHER,
        asset_id=asset_id, value=value, page_uid=page_uid, quote=evidence_quote,
        type=type, text=text,
        person_name=person_name, title_role=title_role,
        date_iso=date_iso,
        certificate_number=certificate_number,
        location_context=location_context,
        binding_status=binding_status,
    ).single()
    if record is None:
        raise RuntimeError(
            f"write_stamp: MERGE returned no record (value={value!r}, "
            f"page_uid={page_uid!r}). The carrier :Page must be written first."
        )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="Stamp",
            role="dated", date_iso=date_iso,
        )
    return record["value"]


# =============================================================================
#  :BINDS_TO (Stamp → record)
# =============================================================================

_LINK_BINDS_TO_CYPHER = """
MATCH (s:Stamp {asset_id: $asset_id, value: $stamp_uid})
MATCH (t {asset_id: $asset_id, value: $target_uid})
WHERE $target_label IN labels(t)
MERGE (s)-[r:BINDS_TO]->(t)
ON CREATE SET r.confidence = $confidence, r.rule = $rule
ON MATCH  SET r.confidence = coalesce($confidence, r.confidence),
              r.rule       = coalesce($rule, r.rule)
RETURN id(r) AS rid
"""


# Closed enum of valid :BINDS_TO targets (Q11d).
BIND_TARGETS = frozenset({
    "Form1", "JobCard", "NonRoutineCard", "WorkPackage",
    "Modification", "STC", "CRS",
})


def link_stamp_binds_to(
    tx: Any,
    *,
    asset_id: str,
    stamp_uid: str,
    target_label: str,
    target_uid: str,
    confidence: str | None = None,    # high | medium | ambiguous
    rule: str | None = None,          # location_context | single_evidence_on_page | ...
) -> None:
    """MERGE :BINDS_TO from a stamp to one of the bind-target labels."""
    if target_label not in BIND_TARGETS:
        raise ValueError(
            f"link_stamp_binds_to: target_label={target_label!r} not in {sorted(BIND_TARGETS)}"
        )
    tx.run(
        _LINK_BINDS_TO_CYPHER,
        asset_id=asset_id, stamp_uid=stamp_uid,
        target_label=target_label, target_uid=target_uid,
        confidence=confidence, rule=rule,
    ).consume()


# =============================================================================
#  :STAMPED_BY (Stamp → Person)
# =============================================================================

_LINK_STAMPED_BY_CYPHER = """
MATCH (s:Stamp {asset_id: $asset_id, value: $stamp_uid})
MERGE (p:Person {asset_id: $asset_id, value: $person_value})
ON CREATE SET p.name = $person_name
ON MATCH  SET p.name = coalesce($person_name, p.name)
MERGE (s)-[:STAMPED_BY]->(p)
"""


def link_stamped_by(
    tx: Any, *, asset_id: str, stamp_uid: str,
    person_value: str, person_name: str | None = None,
) -> None:
    """MERGE :Person and :STAMPED_BY edge from stamp to person.

    Note: :Person is fully written by Phase 6 (organization.py); this is
    a bare-bones MERGE so Phase 1 stamp ingestion isn't blocked. Phase 6
    will enrich the :Person node with role/cert details.
    """
    tx.run(
        _LINK_STAMPED_BY_CYPHER,
        asset_id=asset_id, stamp_uid=stamp_uid,
        person_value=person_value, person_name=person_name,
    ).consume()


# =============================================================================
#  :CARRIES_CERT (Stamp → CertificateNumber)
# =============================================================================

_LINK_STAMP_CARRIES_CERT_CYPHER = """
MATCH (s:Stamp {asset_id: $asset_id, value: $stamp_uid})
MERGE (c:CertificateNumber {asset_id: $asset_id, value: $cert_value})
MERGE (s)-[:CARRIES_CERT]->(c)
"""


def link_stamp_carries_cert(
    tx: Any, *, asset_id: str, stamp_uid: str, cert_value: str,
) -> None:
    """MERGE :CertificateNumber + :CARRIES_CERT edge from a stamp."""
    tx.run(
        _LINK_STAMP_CARRIES_CERT_CYPHER,
        asset_id=asset_id, stamp_uid=stamp_uid, cert_value=cert_value,
    ).consume()
