"""External-standard writers (regulatory + manufacturer documents).

Owns the bare-bones nodes for external standards referenced by a dossier:

    :ATAChapter
    :ServiceBulletin
    :AirworthinessDirective
    :EngineeringOrder
    :RegulatoryRef

Phase 1 creates these as **identity-only** nodes when a page mentions
them — just ``(asset_id, value)``, no enrichment. Phase 6 (connectors)
adds the cross-doc relationships:

    (:SB|:AD)-[:APPLIES_TO]->(:TypeCertificate|:EngineModel|:PartFamily)
    (:SB)-[:ISSUED_BY]->(:DesignOrganization)
    (:AD)-[:ISSUED_BY]->(:RegulatoryAuthority)

The mention edges are typed per-target-label per Q10:

    :Page|:Document -[:MENTIONS_SB]-> :ServiceBulletin
    :Page|:Document -[:MENTIONS_AD]-> :AirworthinessDirective
    :Page|:Document -[:MENTIONS_EO]-> :EngineeringOrder
    :Page|:Document -[:COVERS_ATA]-> :ATAChapter
    :Page|:Document -[:CITES]      -> :RegulatoryRef
"""

from __future__ import annotations

from typing import Any


# =============================================================================
#  Identity-only writers
# =============================================================================
#
# Phase 1 calls these when a page mentions one of these standards. The
# only required data is ``value`` (the canonical identifier — SB number,
# AD number, ATA chapter code, etc.).

_WRITE_ATA_CHAPTER_CYPHER = """
MERGE (n:ATAChapter {asset_id: $asset_id, value: $value})
RETURN n.value AS value
"""


def write_ata_chapter(tx: Any, *, asset_id: str, value: str) -> str:
    """MERGE :ATAChapter. ``value`` is the chapter code (e.g. "32", "32-11-04")."""
    record = tx.run(_WRITE_ATA_CHAPTER_CYPHER, asset_id=asset_id, value=value).single()
    return record["value"] if record else value


_WRITE_SB_CYPHER = """
MERGE (n:ServiceBulletin {asset_id: $asset_id, value: $value})
ON CREATE SET n.kind = $kind
ON MATCH  SET n.kind = coalesce($kind, n.kind)
RETURN n.value AS value
"""


def write_service_bulletin(
    tx: Any, *, asset_id: str, value: str, kind: str | None = None,
) -> str:
    """MERGE :ServiceBulletin. ``kind`` ∈ {alert, recommended} if known."""
    record = tx.run(
        _WRITE_SB_CYPHER, asset_id=asset_id, value=value, kind=kind,
    ).single()
    return record["value"] if record else value


_WRITE_AD_CYPHER = """
MERGE (n:AirworthinessDirective {asset_id: $asset_id, value: $value})
ON CREATE SET n.authority = $authority
ON MATCH  SET n.authority = coalesce($authority, n.authority)
RETURN n.value AS value
"""


def write_airworthiness_directive(
    tx: Any, *, asset_id: str, value: str, authority: str | None = None,
) -> str:
    """MERGE :AirworthinessDirective. ``authority`` ∈ {EASA, FAA, TCCA, ...}."""
    record = tx.run(
        _WRITE_AD_CYPHER, asset_id=asset_id, value=value, authority=authority,
    ).single()
    return record["value"] if record else value


_WRITE_EO_CYPHER = """
MERGE (n:EngineeringOrder {asset_id: $asset_id, value: $value})
RETURN n.value AS value
"""


def write_engineering_order(tx: Any, *, asset_id: str, value: str) -> str:
    record = tx.run(_WRITE_EO_CYPHER, asset_id=asset_id, value=value).single()
    return record["value"] if record else value


_WRITE_REGULATORY_REF_CYPHER = """
MERGE (n:RegulatoryRef {asset_id: $asset_id, value: $value})
ON CREATE SET n.ref_type = $ref_type, n.authority = $authority
ON MATCH  SET n.ref_type  = coalesce($ref_type, n.ref_type),
              n.authority = coalesce($authority, n.authority)
RETURN n.value AS value
"""


def write_regulatory_ref(
    tx: Any, *, asset_id: str, value: str,
    ref_type: str | None = None, authority: str | None = None,
) -> str:
    """MERGE :RegulatoryRef. e.g. ``value="Part-145"``, ``authority="EASA"``."""
    record = tx.run(
        _WRITE_REGULATORY_REF_CYPHER,
        asset_id=asset_id, value=value, ref_type=ref_type, authority=authority,
    ).single()
    return record["value"] if record else value


# =============================================================================
#  Mention/coverage/citation edges (page|document → external standard)
# =============================================================================


def _mention_query(edge_type: str, target_label: str) -> str:
    return f"""
MATCH (src {{asset_id: $asset_id, value: $source_uid}})
WHERE $source_label IN labels(src)
MATCH (tgt:{target_label} {{asset_id: $asset_id, value: $target_value}})
MERGE (src)-[r:{edge_type}]->(tgt)
ON CREATE SET r.level = $level
ON MATCH  SET r.level = $level
"""


_LINK_COVERS_ATA_CYPHER = _mention_query("COVERS_ATA", "ATAChapter")
_LINK_MENTIONS_SB_CYPHER = _mention_query("MENTIONS_SB", "ServiceBulletin")
_LINK_MENTIONS_AD_CYPHER = _mention_query("MENTIONS_AD", "AirworthinessDirective")
_LINK_MENTIONS_EO_CYPHER = _mention_query("MENTIONS_EO", "EngineeringOrder")
_LINK_CITES_CYPHER = _mention_query("CITES", "RegulatoryRef")


def link_covers_ata(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_COVERS_ATA_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level).consume()


def link_mentions_sb(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_SB_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level).consume()


def link_mentions_ad(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_AD_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level).consume()


def link_mentions_eo(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_EO_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level).consume()


def link_cites(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_CITES_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level).consume()
