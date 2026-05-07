""":Component (Layer 3) writers + PN/SN identity edges.

Owns: ``:Component`` plus the PN/SN/ATA/sub-assembly relationships:

    (:Component)-[:HAS_PRIMARY_PN]->(:PartNumber)
    (:Component)-[:HAS_ALTERNATE_PN {source, confidence}]->(:PartNumber)
    (:Component)-[:HAS_SN]->(:SerialNumber)
    (:Component)-[:RELATED_TO_ATA]->(:ATAChapter)
    (:Component)-[:OF_MODEL]->(:EngineModel|:APUModel|...)
    (:Component)-[:PART_OF]->(:Component)         # sub-assembly tree
    (:Component)-[:OF_FAMILY]->(:PartFamily)

    (:PartNumber)-[:SAME_AS]->(:PartNumber)               # synonyms (Q11c)
    (:PartNumber)-[:SUPERSEDED_BY {effective_date}]->(:PartNumber)

Components are fact-bearing: every write requires page evidence, enforced
at the chokepoint via ``require_evidence`` + ``link_evidenced_by``.
"""

from __future__ import annotations

from typing import Any

from ._evidence_helpers import link_evidenced_by, require_evidence
from ._phase_tag import current_phase


# =============================================================================
#  :Component
# =============================================================================

_WRITE_COMPONENT_CYPHER = """
MERGE (c:Component {asset_id: $asset_id, value: $value})
ON CREATE SET c.canonical_pn        = $canonical_pn,
              c.installed_sn         = $installed_sn,
              c.description          = $description,
              c.position             = $position,
              c.component_category   = $component_category,
              c.status               = $status,
              c.source               = $source,
              c.ata_chapter          = $ata_chapter,
              c.is_llp               = $is_llp,
              c.is_overhaul          = $is_overhaul,
              c.life_limit           = $life_limit,
              c.life_unit            = $life_unit,
              c.tsn                  = $tsn,
              c.csn                  = $csn,
              c.tso                  = $tso,
              c.cso                  = $cso,
              c.created_in_phase = $created_in_phase
ON MATCH  SET c.canonical_pn        = coalesce($canonical_pn, c.canonical_pn),
              c.installed_sn         = coalesce($installed_sn, c.installed_sn),
              c.description          = coalesce($description, c.description),
              c.position             = coalesce($position, c.position),
              c.component_category   = coalesce($component_category, c.component_category),
              c.status               = coalesce($status, c.status),
              c.source               = coalesce($source, c.source),
              c.ata_chapter          = coalesce($ata_chapter, c.ata_chapter),
              c.is_llp               = coalesce($is_llp, c.is_llp),
              c.is_overhaul          = coalesce($is_overhaul, c.is_overhaul),
              c.life_limit           = coalesce($life_limit, c.life_limit),
              c.life_unit            = coalesce($life_unit, c.life_unit),
              c.tsn                  = coalesce($tsn, c.tsn),
              c.csn                  = coalesce($csn, c.csn),
              c.tso                  = coalesce($tso, c.tso),
              c.cso                  = coalesce($cso, c.cso)
RETURN c.value AS value
"""


def write_component(
    tx: Any,
    *,
    asset_id: str,
    value: str,                              # canonical "component::{pn}::{sn}"
    evidence_page_uid: str,
    evidence_quote: str,
    canonical_pn: str | None = None,
    installed_sn: str | None = None,
    description: str | None = None,
    position: str | None = None,
    component_category: str | None = None,    # LLP|Hard_Time|On_Condition|Engine_Module|...
    status: str = "DISCOVERED",                # DISCOVERED|CLOSED|PARTIAL|GAP|INSTALLED_AT_MFG
    source: str = "page_mention",              # seed|anchor|xlsx|curated|page_mention
    ata_chapter: str | None = None,
    is_llp: bool | None = None,
    is_overhaul: bool | None = None,
    life_limit: int | float | None = None,
    life_unit: str | None = None,             # hours|cycles|months
    tsn: float | None = None,
    csn: int | None = None,
    tso: float | None = None,
    cso: int | None = None,
) -> str:
    require_evidence(
        label="Component", value=value,
        evidence_page_uid=evidence_page_uid, evidence_quote=evidence_quote,
    )
    tx.run(
        _WRITE_COMPONENT_CYPHER,
        asset_id=asset_id, value=value,
        canonical_pn=canonical_pn, installed_sn=installed_sn,
        description=description, position=position,
        component_category=component_category, status=status, source=source,
        ata_chapter=ata_chapter, is_llp=is_llp, is_overhaul=is_overhaul,
        life_limit=life_limit, life_unit=life_unit,
        tsn=tsn, csn=csn, tso=tso, cso=cso,
        created_in_phase=current_phase(),
    ).consume()
    link_evidenced_by(
        tx, asset_id=asset_id, source_uid=value, source_label="Component",
        page_uid=evidence_page_uid, quote=evidence_quote,
    )
    return value


# =============================================================================
#  Component → identity edges
# =============================================================================

def link_has_primary_pn(
    tx: Any, *, asset_id: str, component_uid: str, pn_value: str,
) -> None:
    """MERGE :Component-[:HAS_PRIMARY_PN]->:PartNumber. PN node must exist."""
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $cuid}) "
        "MATCH (pn:PartNumber {asset_id: $aid, value: $pn}) "
        "MERGE (c)-[:HAS_PRIMARY_PN]->(pn)",
        aid=asset_id, cuid=component_uid, pn=pn_value,
    ).consume()


def link_has_alternate_pn(
    tx: Any, *, asset_id: str, component_uid: str, pn_value: str,
    source: str | None = None, confidence: str | None = None,
) -> None:
    """MERGE :Component-[:HAS_ALTERNATE_PN]->:PartNumber with provenance."""
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $cuid}) "
        "MATCH (pn:PartNumber {asset_id: $aid, value: $pn}) "
        "MERGE (c)-[r:HAS_ALTERNATE_PN]->(pn) "
        "ON CREATE SET r.source = $source, r.confidence = $confidence "
        "ON MATCH  SET r.source = coalesce($source, r.source), "
        "              r.confidence = coalesce($confidence, r.confidence)",
        aid=asset_id, cuid=component_uid, pn=pn_value,
        source=source, confidence=confidence,
    ).consume()


def link_has_sn(
    tx: Any, *, asset_id: str, component_uid: str, sn_value: str,
) -> None:
    """MERGE :Component-[:HAS_SN]->:SerialNumber. SN node must exist."""
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $cuid}) "
        "MATCH (sn:SerialNumber {asset_id: $aid, value: $sn}) "
        "MERGE (c)-[:HAS_SN]->(sn)",
        aid=asset_id, cuid=component_uid, sn=sn_value,
    ).consume()


def link_component_related_to_ata(
    tx: Any, *, asset_id: str, component_uid: str, ata_value: str,
) -> None:
    """MERGE :Component-[:RELATED_TO_ATA]->:ATAChapter."""
    tx.run(
        "MATCH (c:Component {asset_id: $aid, value: $cuid}) "
        "MATCH (a:ATAChapter {asset_id: $aid, value: $ata}) "
        "MERGE (c)-[:RELATED_TO_ATA]->(a)",
        aid=asset_id, cuid=component_uid, ata=ata_value,
    ).consume()


def link_component_part_of(
    tx: Any, *, asset_id: str, child_uid: str, parent_uid: str,
    source: str | None = None, confidence: str | None = None,
) -> None:
    """MERGE :Component-[:PART_OF {source, confidence}]->:Component (sub-assembly)."""
    tx.run(
        "MATCH (child:Component {asset_id: $aid, value: $child_uid}) "
        "MATCH (parent:Component {asset_id: $aid, value: $parent_uid}) "
        "MERGE (child)-[r:PART_OF]->(parent) "
        "ON CREATE SET r.source = $source, r.confidence = $confidence "
        "ON MATCH  SET r.source = coalesce($source, r.source), "
        "              r.confidence = coalesce($confidence, r.confidence)",
        aid=asset_id, child_uid=child_uid, parent_uid=parent_uid,
        source=source, confidence=confidence,
    ).consume()


def link_asset_has_component(
    tx: Any, *, asset_id: str, component_uid: str,
) -> None:
    """MERGE :Asset-[:HAS_COMPONENT]->:Component. Top-level component link."""
    tx.run(
        "MATCH (a:Asset {asset_id: $aid}) "
        "MATCH (c:Component {asset_id: $aid, value: $cuid}) "
        "MERGE (a)-[:HAS_COMPONENT]->(c)",
        aid=asset_id, cuid=component_uid,
    ).consume()


# =============================================================================
#  PartNumber alias edges (Q11c)
# =============================================================================

def link_part_number_same_as(
    tx: Any, *, asset_id: str, pn_a: str, pn_b: str,
    source: str | None = None, confidence: str | None = None,
    evidence_chunk_id: str | None = None,
) -> None:
    """MERGE :PartNumber-[:SAME_AS]->:PartNumber (synonym; bidirectional in spirit)."""
    tx.run(
        "MATCH (a:PartNumber {asset_id: $aid, value: $a}) "
        "MATCH (b:PartNumber {asset_id: $aid, value: $b}) "
        "MERGE (a)-[r:SAME_AS]->(b) "
        "ON CREATE SET r.source = $source, r.confidence = $confidence, "
        "              r.evidence_chunk_id = $ec "
        "ON MATCH  SET r.source = coalesce($source, r.source), "
        "              r.confidence = coalesce($confidence, r.confidence)",
        aid=asset_id, a=pn_a, b=pn_b,
        source=source, confidence=confidence, ec=evidence_chunk_id,
    ).consume()


def link_part_number_superseded_by(
    tx: Any, *, asset_id: str, pn_old: str, pn_new: str,
    effective_date_iso: str | None = None,
    source: str | None = None, confidence: str | None = None,
) -> None:
    """MERGE :PartNumber-[:SUPERSEDED_BY {effective_date}]->:PartNumber."""
    tx.run(
        "MATCH (a:PartNumber {asset_id: $aid, value: $old}) "
        "MATCH (b:PartNumber {asset_id: $aid, value: $new}) "
        "MERGE (a)-[r:SUPERSEDED_BY]->(b) "
        "ON CREATE SET r.effective_date = $eff, r.source = $source, "
        "              r.confidence = $confidence "
        "ON MATCH  SET r.effective_date = coalesce($eff, r.effective_date)",
        aid=asset_id, old=pn_old, new=pn_new, eff=effective_date_iso,
        source=source, confidence=confidence,
    ).consume()


# =============================================================================
#  :PartFamily
# =============================================================================

_WRITE_PART_FAMILY_CYPHER = """
MERGE (pf:PartFamily {asset_id: $asset_id, value: $value})
ON CREATE SET pf.description = $description, pf.tc_scope = $tc_scope,
              pf.created_in_phase = $created_in_phase
ON MATCH  SET pf.description = coalesce($description, pf.description),
              pf.tc_scope    = coalesce($tc_scope, pf.tc_scope)
RETURN pf.value AS value
"""


def write_part_family(
    tx: Any, *, asset_id: str, value: str,
    description: str | None = None, tc_scope: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_PART_FAMILY_CYPHER,
        asset_id=asset_id, value=value,
        description=description, tc_scope=tc_scope,
        created_in_phase=current_phase(),
    ).single()
    return record["value"] if record else value


def link_pn_of_family(
    tx: Any, *, asset_id: str, pn_value: str, family_value: str,
) -> None:
    """MERGE :PartNumber-[:OF_FAMILY]->:PartFamily."""
    tx.run(
        "MATCH (pn:PartNumber {asset_id: $aid, value: $pn}) "
        "MATCH (pf:PartFamily {asset_id: $aid, value: $fam}) "
        "MERGE (pn)-[:OF_FAMILY]->(pf)",
        aid=asset_id, pn=pn_value, fam=family_value,
    ).consume()
