""":Finding, :PriorityItem, :AuditRun writers (Layer 6 — audit overlay).

Findings are the Sparengine-internal output (not in the dossier). They
flag missing evidence, life-limit warnings, AD compliance gaps, etc.
Every finding traces back to its evidence page via :EVIDENCED_BY (Q7);
the writer enforces the golden rule.

Findings primarily attach to either :Asset (asset-level findings) or
:Component (component-level findings) via :HAS_FINDING. They also point
:FLAGS at the specific node they're flagging (which can be a Component,
Document, Page, Stamp, or any of the evidence records).

:AuditRun groups findings by the analytical pass that produced them. One
:AuditRun per pipeline run; :PRODUCED_BY links findings to it.

:PriorityItem is Phase 6.5 output — the critical-items list (high-risk
LLPs near limits, AD compliance gaps near deadlines, etc.). Per-asset
property `:Asset.lease_return_state` is set in the same phase but is a
property, not a node.
"""

from __future__ import annotations

from typing import Any

from ._evidence_helpers import link_evidenced_by, require_evidence
from .date_node import link_date


# =============================================================================
#  :Finding
# =============================================================================

_WRITE_FINDING_CYPHER = """
MERGE (f:Finding {asset_id: $asset_id, value: $value})
ON CREATE SET f.severity            = $severity,
              f.category             = $category,
              f.title                = $title,
              f.description          = $description,
              f.recommended_action   = $recommended_action,
              f.status               = $status
ON MATCH  SET f.severity            = coalesce($severity, f.severity),
              f.category             = coalesce($category, f.category),
              f.title                = coalesce($title, f.title),
              f.description          = coalesce($description, f.description),
              f.recommended_action   = coalesce($recommended_action, f.recommended_action),
              f.status               = coalesce($status, f.status)
RETURN f.value AS value
"""


def write_finding(
    tx: Any,
    *,
    asset_id: str,
    value: str,                           # canonical finding id
    severity: str,                        # FindingSeverity.value: level_1|level_2|level_3
    category: str,                        # closed enum from finding_types.md
    title: str,
    description: str,
    evidence_page_uid: str,               # required (golden rule)
    evidence_quote: str,                  # required (golden rule)
    recommended_action: str | None = None,
    status: str = "OPEN",                 # OPEN | CLOSED | DOWNGRADED
    flags_label: str | None = None,       # the label of the node being flagged
    flags_uid: str | None = None,         # the value of the node being flagged
    asset_level: bool = False,            # if True, link :Asset-[:HAS_FINDING]
    component_uid: str | None = None,     # if set, link :Component-[:HAS_FINDING]
    audit_run_uid: str | None = None,     # if set, link :Finding-[:PRODUCED_BY]
) -> str:
    """MERGE :Finding + :EVIDENCED_BY + :FLAGS + ownership edge.

    Ownership rule (matches schema): every :Finding is owned by either
    the :Asset (asset_level=True) or a :Component (via component_uid).
    At least one must be supplied.
    """
    require_evidence(
        label="Finding", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    if not asset_level and not component_uid:
        raise ValueError(
            "write_finding: every Finding must be owned by either the Asset "
            "(asset_level=True) or a Component (component_uid). Neither was supplied."
        )

    tx.run(
        _WRITE_FINDING_CYPHER,
        asset_id=asset_id, value=value,
        severity=severity, category=category,
        title=title, description=description,
        recommended_action=recommended_action, status=status,
    ).consume()

    # Page evidence anchor.
    link_evidenced_by(
        tx, asset_id=asset_id, source_uid=value, source_label="Finding",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )

    # Ownership: :Asset-[:HAS_FINDING]->:Finding OR :Component-[:HAS_FINDING]->:Finding
    if asset_level:
        tx.run(
            "MATCH (a:Asset {asset_id: $asset_id}) "
            "MATCH (f:Finding {asset_id: $asset_id, value: $value}) "
            "MERGE (a)-[:HAS_FINDING]->(f)",
            asset_id=asset_id, value=value,
        ).consume()
    if component_uid:
        tx.run(
            "MATCH (c:Component {asset_id: $asset_id, value: $component_uid}) "
            "MATCH (f:Finding {asset_id: $asset_id, value: $value}) "
            "MERGE (c)-[:HAS_FINDING]->(f)",
            asset_id=asset_id, value=value, component_uid=component_uid,
        ).consume()

    # Optional :FLAGS relationship to the specific node being flagged.
    if flags_label and flags_uid:
        tx.run(
            "MATCH (f:Finding {asset_id: $asset_id, value: $finding_uid}) "
            "MATCH (target {asset_id: $asset_id, value: $flags_uid}) "
            "WHERE $flags_label IN labels(target) "
            "MERGE (f)-[r:FLAGS]->(target) "
            "ON CREATE SET r.severity = $severity, r.category = $category "
            "ON MATCH  SET r.severity = $severity, r.category = $category",
            asset_id=asset_id, finding_uid=value,
            flags_label=flags_label, flags_uid=flags_uid,
            severity=severity, category=category,
        ).consume()

    # Optional :PRODUCED_BY → :AuditRun
    if audit_run_uid:
        tx.run(
            "MATCH (f:Finding {asset_id: $asset_id, value: $finding_uid}) "
            "MATCH (a:AuditRun {asset_id: $asset_id, value: $audit_run_uid}) "
            "MERGE (f)-[:PRODUCED_BY]->(a)",
            asset_id=asset_id, finding_uid=value, audit_run_uid=audit_run_uid,
        ).consume()

    return value


# =============================================================================
#  :AuditRun
# =============================================================================

_WRITE_AUDIT_RUN_CYPHER = """
MERGE (a:AuditRun {asset_id: $asset_id, value: $value})
ON CREATE SET a.dossier_cut_off_date = $dossier_cut_off_date_iso,
              a.audit_snapshot_date  = $audit_snapshot_date_iso,
              a.sparengine_version   = $sparengine_version
ON MATCH  SET a.dossier_cut_off_date = coalesce($dossier_cut_off_date_iso, a.dossier_cut_off_date),
              a.audit_snapshot_date  = coalesce($audit_snapshot_date_iso, a.audit_snapshot_date),
              a.sparengine_version   = coalesce($sparengine_version, a.sparengine_version)
WITH a
MATCH (asset:Asset {asset_id: $asset_id})
MERGE (a)-[:RUN_ON]->(asset)
RETURN a.value AS value
"""


def write_audit_run(
    tx: Any,
    *,
    asset_id: str,
    value: str,                              # canonical run id (e.g. ISO timestamp)
    dossier_cut_off_date_iso: str | None = None,
    audit_snapshot_date_iso: str | None = None,
    sparengine_version: str | None = None,
) -> str:
    """MERGE :AuditRun + :RUN_ON edge to :Asset."""
    record = tx.run(
        _WRITE_AUDIT_RUN_CYPHER,
        asset_id=asset_id, value=value,
        dossier_cut_off_date_iso=dossier_cut_off_date_iso,
        audit_snapshot_date_iso=audit_snapshot_date_iso,
        sparengine_version=sparengine_version,
    ).single()
    if dossier_cut_off_date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="AuditRun",
            role="audit_cutoff", date_iso=dossier_cut_off_date_iso,
        )
    if audit_snapshot_date_iso:
        link_date(
            tx, asset_id=asset_id, source_uid=value, source_label="AuditRun",
            role="audit_snapshot", date_iso=audit_snapshot_date_iso,
        )
    return record["value"] if record else value


# =============================================================================
#  :PriorityItem (Phase 6.5)
# =============================================================================

_WRITE_PRIORITY_ITEM_CYPHER = """
MERGE (p:PriorityItem {asset_id: $asset_id, value: $value})
ON CREATE SET p.kind        = $kind,
              p.title       = $title,
              p.description = $description,
              p.urgency     = $urgency
ON MATCH  SET p.kind        = coalesce($kind, p.kind),
              p.title       = coalesce($title, p.title),
              p.description = coalesce($description, p.description),
              p.urgency     = coalesce($urgency, p.urgency)
WITH p
OPTIONAL MATCH (c:Component {asset_id: $asset_id, value: $component_uid})
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
    MERGE (c)-[:HAS_PRIORITY_ITEM]->(p)
)
RETURN p.value AS value
"""


def write_priority_item(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    kind: str,                               # llp_near_limit | ad_due | hsi_due | ...
    title: str,
    description: str,
    urgency: str,                            # immediate | within_30d | within_90d | informational
    component_uid: str | None = None,        # link to component if applicable
) -> str:
    record = tx.run(
        _WRITE_PRIORITY_ITEM_CYPHER,
        asset_id=asset_id, value=value,
        kind=kind, title=title, description=description, urgency=urgency,
        component_uid=component_uid,
    ).single()
    return record["value"] if record else value
