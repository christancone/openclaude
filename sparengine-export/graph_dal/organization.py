""":Person, :Organization, and authority-organization writers (Phase 6).

Owns:

    :Person                       — humans (signers, inspectors, mechanics)
    :Organization                 — generic org with `role` (MRO/CAMO/Operator/OEM)
    :RegulatoryAuthority          — EASA, FAA, TCCA, CAAC
    :DesignOrganization           — DOA-holders
    :ProductionOrganization       — POA-holders
    :MaintenanceOrganization      — Part 145 / equivalent

These are bare-bones writers for Phase 6. The cross-document linkage
edges (:INCLUDES, :COMPLIES_WITH, :IMPLEMENTS, :APPLIES_TO, :SIGNED_BY,
:ISSUED_BY) are wired by phase6.py using these node writers.
"""

from __future__ import annotations

from typing import Any


_WRITE_PERSON_CYPHER = """
MERGE (p:Person {asset_id: $asset_id, value: $value})
ON CREATE SET p.name           = $name,
              p.cert_authority = $cert_authority
ON MATCH  SET p.name           = coalesce($name, p.name),
              p.cert_authority = coalesce($cert_authority, p.cert_authority)
RETURN p.value AS value
"""


def write_person(
    tx: Any, *, asset_id: str, value: str,
    name: str | None = None, cert_authority: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_PERSON_CYPHER,
        asset_id=asset_id, value=value,
        name=name, cert_authority=cert_authority,
    ).single()
    return record["value"] if record else value


_WRITE_ORGANIZATION_CYPHER = """
MERGE (o:Organization {asset_id: $asset_id, value: $value})
ON CREATE SET o.name      = $name,
              o.role      = $role,
              o.cage_code = $cage_code,
              o.country   = $country
ON MATCH  SET o.name      = coalesce($name, o.name),
              o.role      = coalesce($role, o.role),
              o.cage_code = coalesce($cage_code, o.cage_code),
              o.country   = coalesce($country, o.country)
RETURN o.value AS value
"""


def write_organization(
    tx: Any, *, asset_id: str, value: str,
    name: str | None = None, role: str | None = None,    # MRO|CAMO|Operator|OEM
    cage_code: str | None = None, country: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_ORGANIZATION_CYPHER,
        asset_id=asset_id, value=value,
        name=name, role=role, cage_code=cage_code, country=country,
    ).single()
    return record["value"] if record else value


_WRITE_REGULATORY_AUTHORITY_CYPHER = """
MERGE (a:RegulatoryAuthority {asset_id: $asset_id, value: $value})
ON CREATE SET a.name = $name
ON MATCH  SET a.name = coalesce($name, a.name)
RETURN a.value AS value
"""


def write_regulatory_authority(
    tx: Any, *, asset_id: str, value: str, name: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_REGULATORY_AUTHORITY_CYPHER,
        asset_id=asset_id, value=value, name=name,
    ).single()
    return record["value"] if record else value


_WRITE_DESIGN_ORG_CYPHER = """
MERGE (d:DesignOrganization {asset_id: $asset_id, value: $value})
ON CREATE SET d.name = $name, d.doa_number = $doa_number
ON MATCH  SET d.name = coalesce($name, d.name),
              d.doa_number = coalesce($doa_number, d.doa_number)
RETURN d.value AS value
"""


def write_design_organization(
    tx: Any, *, asset_id: str, value: str,
    name: str | None = None, doa_number: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_DESIGN_ORG_CYPHER,
        asset_id=asset_id, value=value, name=name, doa_number=doa_number,
    ).single()
    return record["value"] if record else value


_WRITE_PRODUCTION_ORG_CYPHER = """
MERGE (p:ProductionOrganization {asset_id: $asset_id, value: $value})
ON CREATE SET p.name = $name, p.poa_number = $poa_number
ON MATCH  SET p.name = coalesce($name, p.name),
              p.poa_number = coalesce($poa_number, p.poa_number)
RETURN p.value AS value
"""


def write_production_organization(
    tx: Any, *, asset_id: str, value: str,
    name: str | None = None, poa_number: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_PRODUCTION_ORG_CYPHER,
        asset_id=asset_id, value=value, name=name, poa_number=poa_number,
    ).single()
    return record["value"] if record else value


_WRITE_MAINTENANCE_ORG_CYPHER = """
MERGE (m:MaintenanceOrganization {asset_id: $asset_id, value: $value})
ON CREATE SET m.name = $name,
              m.part145_number = $part145_number,
              m.country = $country
ON MATCH  SET m.name = coalesce($name, m.name),
              m.part145_number = coalesce($part145_number, m.part145_number),
              m.country = coalesce($country, m.country)
RETURN m.value AS value
"""


def write_maintenance_organization(
    tx: Any, *, asset_id: str, value: str,
    name: str | None = None, part145_number: str | None = None,
    country: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_MAINTENANCE_ORG_CYPHER,
        asset_id=asset_id, value=value, name=name,
        part145_number=part145_number, country=country,
    ).single()
    return record["value"] if record else value


# =============================================================================
#  Cross-document linkage edges
# =============================================================================

def link_signed_by(
    tx: Any, *, asset_id: str, source_label: str, source_uid: str,
    person_value: str, block: str | None = None,
    date_iso: str | None = None, role: str | None = None,
) -> None:
    """:Form1|:CRS|:JobCard-[:SIGNED_BY {block, date, role}]->:Person."""
    if source_label not in {"Form1", "CRS", "JobCard"}:
        raise ValueError(f"link_signed_by: source_label={source_label!r} not allowed")
    tx.run(
        f"MATCH (src:{source_label} {{asset_id: $aid, value: $sv}}) "
        "MATCH (p:Person {asset_id: $aid, value: $pv}) "
        "MERGE (src)-[r:SIGNED_BY]->(p) "
        "ON CREATE SET r.block = $block, r.date = $date, r.role = $role "
        "ON MATCH  SET r.block = coalesce($block, r.block), "
        "              r.date  = coalesce($date,  r.date), "
        "              r.role  = coalesce($role,  r.role)",
        aid=asset_id, sv=source_uid, pv=person_value,
        block=block, date=date_iso, role=role,
    ).consume()


def link_issued_by(
    tx: Any, *, asset_id: str, source_label: str, source_uid: str,
    target_label: str, target_uid: str,
    date_iso: str | None = None,
) -> None:
    """:Form1|:CRS|:SB|:AD|:STC-[:ISSUED_BY {date}]->:Org/:Authority/:DOA/:POA/:MRO."""
    valid_sources = {"Form1", "CRS", "ServiceBulletin", "AirworthinessDirective",
                     "STC", "EngineeringOrder", "Modification"}
    if source_label not in valid_sources:
        raise ValueError(f"link_issued_by: source_label={source_label!r} not allowed")
    valid_targets = {"Organization", "RegulatoryAuthority", "DesignOrganization",
                     "ProductionOrganization", "MaintenanceOrganization"}
    if target_label not in valid_targets:
        raise ValueError(f"link_issued_by: target_label={target_label!r} not allowed")
    tx.run(
        f"MATCH (src:{source_label} {{asset_id: $aid, value: $sv}}) "
        f"MATCH (tgt:{target_label} {{asset_id: $aid, value: $tv}}) "
        "MERGE (src)-[r:ISSUED_BY]->(tgt) "
        "ON CREATE SET r.date = $date "
        "ON MATCH  SET r.date = coalesce($date, r.date)",
        aid=asset_id, sv=source_uid, tv=target_uid, date=date_iso,
    ).consume()


def link_work_package_includes(
    tx: Any, *, asset_id: str, work_package_uid: str,
    target_label: str, target_uid: str,
) -> None:
    """:WorkPackage-[:INCLUDES]->:JobCard|:NRC|:CRS|:Form1."""
    if target_label not in {"JobCard", "NonRoutineCard", "CRS", "Form1"}:
        raise ValueError(f"link_work_package_includes: target_label={target_label!r} not allowed")
    tx.run(
        "MATCH (wp:WorkPackage {asset_id: $aid, value: $wp}) "
        f"MATCH (t:{target_label} {{asset_id: $aid, value: $t}}) "
        "MERGE (wp)-[:INCLUDES]->(t)",
        aid=asset_id, wp=work_package_uid, t=target_uid,
    ).consume()
