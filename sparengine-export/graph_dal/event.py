""":Event + :ComponentSnapshot writers (Layer 4 — time).

Owns the event nodes that record discrete maintenance actions and the
relationships between events, assets, components, and organizations.

Edges:
    (:Event)-[:OCCURRED_ON]->(:Asset)               — asset-level events
    (:Event)-[:AFFECTED {confidence}]->(:Component)  — event affects component
    (:Event)-[:PERFORMED_BY]->(:Organization)        — MRO that did the work
    (:Event)-[:RECORDED_BY]->(:Organization)         — CAMO that filed it
    (:Event)-[:GENERATES]->(:ComponentSnapshot)
    (:ComponentSnapshot)-[:AT_EVENT]->(:Event)
    (:ComponentSnapshot)-[:OF]->(:Component)
    (:Component)-[:INSTALLED_AT]->(:Event)
    (:Component)-[:REMOVED_AT]->(:Event)
    (:Component)-[:WAS_INSTALLED_ON {from_date, to_date}]->(:Asset)
    (:Form1)-[:RELEASES {block}]->(:Component)
    (:CRS)-[:CERTIFIES]->(:Component|:WorkPackage|:Asset)
"""

from __future__ import annotations

from typing import Any

from ._evidence_helpers import link_evidenced_by, require_evidence
from .date_node import link_date
from ._phase_tag import current_phase


# =============================================================================
#  :Event
# =============================================================================

_WRITE_EVENT_CYPHER = """
MERGE (e:Event {asset_id: $asset_id, value: $value})
ON CREATE SET e.kind                      = $kind,
              e.task_compliance_status     = $task_compliance_status,
              e.compliance_status_reason   = $compliance_status_reason,
              e.event_date                  = $date_iso,
              e.ac_hours                    = $ac_hours,
              e.ac_cycles                   = $ac_cycles,
              e.tsn_at_event                = $tsn_at_event,
              e.csn_at_event                = $csn_at_event,
              e.description                 = $description,
              e.task_reference              = $task_reference,
              e.work_order                  = $work_order,
              e.confidence                  = $confidence,
              e.created_in_phase = $created_in_phase
ON MATCH  SET e.kind                      = coalesce($kind, e.kind),
              e.task_compliance_status     = coalesce($task_compliance_status, e.task_compliance_status),
              e.compliance_status_reason   = coalesce($compliance_status_reason, e.compliance_status_reason),
              e.event_date                  = coalesce($date_iso, e.event_date),
              e.ac_hours                    = coalesce($ac_hours, e.ac_hours),
              e.ac_cycles                   = coalesce($ac_cycles, e.ac_cycles),
              e.tsn_at_event                = coalesce($tsn_at_event, e.tsn_at_event),
              e.csn_at_event                = coalesce($csn_at_event, e.csn_at_event),
              e.description                 = coalesce($description, e.description),
              e.task_reference              = coalesce($task_reference, e.task_reference),
              e.work_order                  = coalesce($work_order, e.work_order),
              e.confidence                  = coalesce($confidence, e.confidence)
RETURN e.value AS value
"""


def write_event(
    tx: Any,
    *,
    asset_id: str,
    value: str,                                # canonical event uid
    kind: str,                                 # EventKind value
    evidence_page_uid: str,                    # required (golden rule)
    evidence_quote: str,                       # required (golden rule)
    date_iso: str | None = None,
    description: str | None = None,
    task_reference: str | None = None,
    task_compliance_status: str | None = None,
    compliance_status_reason: str | None = None,
    work_order: str | None = None,
    tsn_at_event: float | None = None,
    csn_at_event: int | None = None,
    ac_hours: float | None = None,
    ac_cycles: int | None = None,
    confidence: str | None = None,
    asset_event: bool = False,                  # :OCCURRED_ON :Asset if True
    component_uid: str | None = None,           # :AFFECTED :Component if set
    affected_confidence: str | None = None,
) -> str:
    """MERGE :Event + :EVIDENCED_BY + optional ownership edges."""
    require_evidence(
        label="Event", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_EVENT_CYPHER,
        asset_id=asset_id, value=value,
        kind=kind, date_iso=date_iso,
        description=description, task_reference=task_reference,
        task_compliance_status=task_compliance_status,
        compliance_status_reason=compliance_status_reason,
        work_order=work_order,
        tsn_at_event=tsn_at_event, csn_at_event=csn_at_event,
        ac_hours=ac_hours, ac_cycles=ac_cycles,
        confidence=confidence,
        created_in_phase=current_phase(),
    ).consume()
    link_evidenced_by(
        tx, asset_id=asset_id, source_uid=value, source_label="Event",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="Event",
            role="event", date_iso=date_iso,
        )
    if asset_event:
        tx.run(
            "MATCH (e:Event {asset_id: $aid, value: $v}) "
            "MATCH (a:Asset {asset_id: $aid}) "
            "MERGE (e)-[:OCCURRED_ON]->(a)",
            aid=asset_id, v=value,
        ).consume()
    if component_uid:
        tx.run(
            "MATCH (e:Event {asset_id: $aid, value: $v}) "
            "MATCH (c:Component {asset_id: $aid, value: $cuid}) "
            "MERGE (e)-[r:AFFECTED]->(c) "
            "ON CREATE SET r.confidence = $conf "
            "ON MATCH  SET r.confidence = coalesce($conf, r.confidence)",
            aid=asset_id, v=value, cuid=component_uid, conf=affected_confidence,
        ).consume()
    return value


# =============================================================================
#  Optional event-relationship helpers
# =============================================================================

def link_event_performed_by(
    tx: Any, *, asset_id: str, event_uid: str, organization_value: str,
) -> None:
    """:Event-[:PERFORMED_BY]->:Organization (creates the org if missing)."""
    tx.run(
        "MATCH (e:Event {asset_id: $aid, value: $v}) "
        "MERGE (o:Organization {asset_id: $aid, value: $org}) "
        "MERGE (e)-[:PERFORMED_BY]->(o)",
        aid=asset_id, v=event_uid, org=organization_value,
    ).consume()


def link_event_recorded_by(
    tx: Any, *, asset_id: str, event_uid: str, organization_value: str,
) -> None:
    """:Event-[:RECORDED_BY]->:Organization."""
    tx.run(
        "MATCH (e:Event {asset_id: $aid, value: $v}) "
        "MERGE (o:Organization {asset_id: $aid, value: $org}) "
        "MERGE (e)-[:RECORDED_BY]->(o)",
        aid=asset_id, v=event_uid, org=organization_value,
    ).consume()


def link_component_installed_at(
    tx: Any, *, asset_id: str, component_uid: str, event_uid: str,
) -> None:
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $c}) "
        "MATCH (e:Event {asset_id: $aid, value: $e}) "
        "MERGE (c)-[:INSTALLED_AT]->(e)",
        aid=asset_id, c=component_uid, e=event_uid,
    ).consume()


def link_component_removed_at(
    tx: Any, *, asset_id: str, component_uid: str, event_uid: str,
) -> None:
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $c}) "
        "MATCH (e:Event {asset_id: $aid, value: $e}) "
        "MERGE (c)-[:REMOVED_AT]->(e)",
        aid=asset_id, c=component_uid, e=event_uid,
    ).consume()


def link_was_installed_on(
    tx: Any, *, asset_id: str, component_uid: str,
    from_date_iso: str | None = None, to_date_iso: str | None = None,
) -> None:
    """Denormalised summary edge :Component-[:WAS_INSTALLED_ON]->:Asset (Q11b)."""
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $c}) "
        "MATCH (a:Asset {asset_id: $aid}) "
        "MERGE (c)-[r:WAS_INSTALLED_ON]->(a) "
        "ON CREATE SET r.from_date = $from_d, r.to_date = $to_d "
        "ON MATCH  SET r.from_date = coalesce($from_d, r.from_date), "
        "              r.to_date   = coalesce($to_d, r.to_date)",
        aid=asset_id, c=component_uid, from_d=from_date_iso, to_d=to_date_iso,
    ).consume()


# =============================================================================
#  Form1 / CRS → Component edges (created in Phase 5 when we know the binding)
# =============================================================================

def link_form1_releases_component(
    tx: Any, *, asset_id: str, form1_uid: str, component_uid: str,
    block: str | None = None,
) -> None:
    """:Form1-[:RELEASES {block}]->:Component."""
    tx.run(
        "MATCH (f:Form1 {asset_id: $aid, value: $f}) "
        "MATCH (c:Component {asset_id: $aid, value: $c}) "
        "MERGE (f)-[r:RELEASES]->(c) "
        "ON CREATE SET r.block = $block "
        "ON MATCH  SET r.block = coalesce($block, r.block)",
        aid=asset_id, f=form1_uid, c=component_uid, block=block,
    ).consume()


def link_crs_certifies(
    tx: Any, *, asset_id: str, crs_uid: str,
    target_label: str, target_uid: str,
) -> None:
    """:CRS-[:CERTIFIES]->:Component | :WorkPackage | :Asset."""
    if target_label not in {"Component", "WorkPackage", "Asset"}:
        raise ValueError(f"link_crs_certifies: target_label={target_label!r} not allowed")
    tx.run(
        "MATCH (crs:CRS {asset_id: $aid, value: $crs}) "
        "MATCH (t {asset_id: $aid, value: $tgt}) "
        "WHERE $tgt_label IN labels(t) "
        "MERGE (crs)-[:CERTIFIES]->(t)",
        aid=asset_id, crs=crs_uid, tgt=target_uid, tgt_label=target_label,
    ).consume()


# =============================================================================
#  :ComponentSnapshot
# =============================================================================

_WRITE_COMPONENT_SNAPSHOT_CYPHER = """
MERGE (s:ComponentSnapshot {asset_id: $asset_id, value: $value})
ON CREATE SET s.date                     = $date_iso,
              s.tsn                       = $tsn,
              s.csn                       = $csn,
              s.tso                       = $tso,
              s.cso                       = $cso,
              s.tsh                       = $tsh,
              s.condition_classification  = $condition,
              s.status                    = $status,
              s.created_in_phase = $created_in_phase
ON MATCH  SET s.date                     = coalesce($date_iso, s.date),
              s.tsn                       = coalesce($tsn, s.tsn),
              s.csn                       = coalesce($csn, s.csn),
              s.tso                       = coalesce($tso, s.tso),
              s.cso                       = coalesce($cso, s.cso),
              s.tsh                       = coalesce($tsh, s.tsh),
              s.condition_classification  = coalesce($condition, s.condition_classification),
              s.status                    = coalesce($status, s.status)
WITH s
MATCH (c:Component {asset_id: $asset_id, value: $component_uid})
MERGE (s)-[:OF]->(c)
RETURN s.value AS value
"""


def write_component_snapshot(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    component_uid: str,
    evidence_page_uid: str,
    evidence_quote: str,
    date_iso: str | None = None,
    tsn: float | None = None,
    csn: int | None = None,
    tso: float | None = None,
    cso: int | None = None,
    tsh: float | None = None,
    condition: str | None = None,
    status: str | None = None,
    event_uid: str | None = None,            # if set, link :AT_EVENT and :GENERATES
) -> str:
    require_evidence(
        label="ComponentSnapshot", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_COMPONENT_SNAPSHOT_CYPHER,
        asset_id=asset_id, value=value, component_uid=component_uid,
        date_iso=date_iso, tsn=tsn, csn=csn, tso=tso, cso=cso, tsh=tsh,
        condition=condition, status=status,
        created_in_phase=current_phase(),
    ).consume()
    link_evidenced_by(
        tx, asset_id=asset_id, source_uid=value, source_label="ComponentSnapshot",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    if event_uid:
        tx.run(
            "MATCH (s:ComponentSnapshot {asset_id: $aid, value: $s}) "
            "MATCH (e:Event {asset_id: $aid, value: $e}) "
            "MERGE (s)-[:AT_EVENT]->(e) "
            "MERGE (e)-[:GENERATES]->(s)",
            aid=asset_id, s=value, e=event_uid,
        ).consume()
    if date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="ComponentSnapshot",
            role="snapshot", date_iso=date_iso,
        )
    return value
