"""Layer-0 (Asset profile) writers.

Owns: ``:Asset`` (+ secondary label), ``:Fleet``, ``:TypeCertificate``,
``:CountryRegistration``, plus the relationships between them.

``:Asset`` is the only node where the natural key is ``asset_id`` alone
(uniqueness ``(asset_id)``, not ``(asset_id, value)``) — the asset IS the
asset, exactly one per dossier. Other nodes in this module follow the
general ``(asset_id, value)`` pattern.

These writers are not fact-bearing in the SPARENGINE sense — there's no
``:EVIDENCED_BY`` requirement. ``:Asset`` is seeded in Phase 0 (judgement)
and confirmed in Phase 2 (corpus signals); the evidence trail for *what
the asset is* lives on the `:Component` and `:Event` nodes that follow.
"""

from __future__ import annotations

from typing import Any

from .date_node import link_date


# -----------------------------------------------------------------------------
#  :Asset
# -----------------------------------------------------------------------------

_WRITE_ASSET_CYPHER = """
MERGE (a:Asset {asset_id: $asset_id})
ON CREATE SET
    a.value                  = $asset_id,
    a.name                   = $name,
    a.msn                    = $msn,
    a.registration           = $registration,
    a.asset_type             = $asset_type,
    a.subtype                = $subtype,
    a.country_of_registration = $country_of_registration,
    a.created_at             = datetime()
ON MATCH SET
    a.name                   = coalesce($name, a.name),
    a.msn                    = coalesce($msn, a.msn),
    a.registration           = coalesce($registration, a.registration),
    a.asset_type             = coalesce($asset_type, a.asset_type),
    a.subtype                = coalesce($subtype, a.subtype),
    a.country_of_registration = coalesce($country_of_registration, a.country_of_registration),
    a.updated_at             = datetime()
WITH a
CALL apoc.create.addLabels(a, $secondary_labels) YIELD node
RETURN a.asset_id AS asset_id
"""


def _primitive_or_none(v: Any) -> str | int | float | bool | None:
    """Coerce to a Neo4j-acceptable primitive or None.

    Neo4j property values must be primitives or arrays of primitives;
    nested maps/dicts raise ``CypherTypeError`` at write time. Any
    dict/list/object passed to a writer is silently dropped to None.
    Lists of primitives pass through unchanged.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list) and all(
        isinstance(x, (str, int, float, bool)) or x is None for x in v
    ):
        return v
    # Anything else (dict, nested list, object) becomes None — the DAL
    # never lets a complex value reach the wire as a single property.
    return None


def write_asset(
    tx: Any,
    *,
    asset_id: str,
    asset_kind: str,                          # AssetKind.value, e.g. "AIRCRAFT"
    name: str | None = None,
    msn: str | None = None,
    registration: str | None = None,
    subtype: str | None = None,
    country_of_registration: str | None = None,
    manufacture_date_iso: str | None = None,
    delivery_date_iso: str | None = None,
) -> str:
    """Seed/refresh the :Asset node and add its secondary class label.

    The secondary label (``:Aircraft``, ``:Engine``, ``:Propeller``, …) is
    derived from ``asset_kind`` — pass an ``AssetKind`` enum value. APOC
    is required for dynamic-label addition (``apoc.create.addLabels``)
    because Cypher itself can't parameterise label names.

    Phase 0 calls this with the values it discovers from the dossier.
    Phase 2 calls it again with the same/refined values; ON MATCH coalesces
    so a Phase 2 call doesn't blank a Phase 0 finding.

    All scalar args are coerced via :func:`_primitive_or_none` — a defensive
    move because Phase 0's ``asset_profile.json`` historically used nested
    dicts (``registration: {current, history}``) for some fields. The
    semantically-rich part of those structures (history, alternate values)
    belongs on its own :Fleet / :CountryRegistration / event nodes, not
    inline on :Asset.

    Returns the asset_id (echo, for diagnostics).
    """
    # Defensive coercion before the wire.
    name                    = _primitive_or_none(name)
    msn                     = _primitive_or_none(msn)
    registration            = _primitive_or_none(registration)
    subtype                 = _primitive_or_none(subtype)
    country_of_registration = _primitive_or_none(country_of_registration)
    manufacture_date_iso    = _primitive_or_none(manufacture_date_iso)
    delivery_date_iso       = _primitive_or_none(delivery_date_iso)
    # Map AssetKind → secondary label (matches schema reference).
    label_map = {
        "AIRCRAFT": "Aircraft",
        "ENGINE": "Engine",
        "PROPELLER": "Propeller",
        "LANDING_GEAR_ASSEMBLY": "LandingGearAssembly",
        "APU": "APU",
        "ROTOR_SYSTEM": "RotorSystem",
        "GEARBOX": "Gearbox",
        "COMPONENT": "Component",   # generic component-only dossier
    }
    secondary = label_map.get(asset_kind)
    secondary_labels = [secondary] if secondary else []

    record = tx.run(
        _WRITE_ASSET_CYPHER,
        asset_id=asset_id,
        name=name,
        msn=msn,
        registration=registration,
        asset_type=asset_kind,
        subtype=subtype,
        country_of_registration=country_of_registration,
        secondary_labels=secondary_labels,
    ).single()
    if record is None:
        raise RuntimeError(f"write_asset: MERGE returned no record (asset_id={asset_id!r})")

    if manufacture_date_iso:
        link_date(
            tx,
            asset_id=asset_id,
            source_uid=asset_id,         # :Asset is keyed on asset_id (not value)
            source_label="Asset",
            role="manufacture",
            date_iso=manufacture_date_iso,
        )
    if delivery_date_iso:
        link_date(
            tx,
            asset_id=asset_id,
            source_uid=asset_id,
            source_label="Asset",
            role="delivery",
            date_iso=delivery_date_iso,
        )
    return record["asset_id"]


# Note: the :Asset's `value` property is set equal to its `asset_id` so
# that `link_date(source_uid=asset_id, source_label="Asset")` matches by
# the standard (asset_id, value) lookup pattern. The schema constraint
# is on asset_id alone; value is a denormalised echo for query symmetry.


# -----------------------------------------------------------------------------
#  :Fleet
# -----------------------------------------------------------------------------

_WRITE_FLEET_CYPHER = """
MERGE (f:Fleet {asset_id: $asset_id, value: $value})
ON CREATE SET f.name = $name, f.operator_id = $operator_id
ON MATCH  SET f.name = coalesce($name, f.name),
              f.operator_id = coalesce($operator_id, f.operator_id)
WITH f
MATCH (a:Asset {asset_id: $asset_id})
MERGE (a)-[:PART_OF_FLEET]->(f)
RETURN f.value AS value
"""


def write_fleet(
    tx: Any,
    *,
    asset_id: str,
    value: str,                 # canonical fleet identifier (e.g. operator name + tail-pool key)
    name: str | None = None,
    operator_id: str | None = None,
) -> str:
    """Create/refresh ``:Fleet`` and link it to ``:Asset`` via ``:PART_OF_FLEET``."""
    record = tx.run(
        _WRITE_FLEET_CYPHER,
        asset_id=asset_id,
        value=value,
        name=name,
        operator_id=operator_id,
    ).single()
    return record["value"] if record else value


# -----------------------------------------------------------------------------
#  :TypeCertificate
# -----------------------------------------------------------------------------

_WRITE_TYPE_CERTIFICATE_CYPHER = """
MERGE (tc:TypeCertificate {asset_id: $asset_id, value: $value})
ON CREATE SET tc.tc_holder         = $tc_holder,
              tc.tc_number         = $tc_number,
              tc.model_designation = $model_designation,
              tc.category          = $category
ON MATCH  SET tc.tc_holder         = coalesce($tc_holder, tc.tc_holder),
              tc.tc_number         = coalesce($tc_number, tc.tc_number),
              tc.model_designation = coalesce($model_designation, tc.model_designation),
              tc.category          = coalesce($category, tc.category)
WITH tc
MATCH (a:Asset {asset_id: $asset_id})
MERGE (a)-[:CERTIFIED_UNDER]->(tc)
RETURN tc.value AS value
"""


def write_type_certificate(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # canonical TC identifier (e.g. "EASA.R.006")
    tc_holder: str | None = None,
    tc_number: str | None = None,
    model_designation: str | None = None,
    category: str | None = None,      # CS-25 / CS-23 / CS-27 / CS-29 / FAR-25 ...
) -> str:
    """Create/refresh ``:TypeCertificate`` and link to ``:Asset`` via ``:CERTIFIED_UNDER``."""
    record = tx.run(
        _WRITE_TYPE_CERTIFICATE_CYPHER,
        asset_id=asset_id,
        value=value,
        tc_holder=tc_holder,
        tc_number=tc_number,
        model_designation=model_designation,
        category=category,
    ).single()
    return record["value"] if record else value


# -----------------------------------------------------------------------------
#  :CountryRegistration
# -----------------------------------------------------------------------------

_WRITE_COUNTRY_REGISTRATION_CYPHER = """
MERGE (cr:CountryRegistration {asset_id: $asset_id, value: $value})
ON CREATE SET cr.iso_code = $iso_code, cr.prefix = $prefix
ON MATCH  SET cr.iso_code = coalesce($iso_code, cr.iso_code),
              cr.prefix   = coalesce($prefix,   cr.prefix)
WITH cr
MATCH (a:Asset {asset_id: $asset_id})
MERGE (a)-[:REGISTERED_IN]->(cr)
RETURN cr.value AS value
"""


def write_country_registration(
    tx: Any,
    *,
    asset_id: str,
    value: str,                       # canonical key — usually the iso_code itself ("CH", "G", "N", ...)
    iso_code: str | None = None,
    prefix: str | None = None,        # registration prefix (HB, G, N, D, ...)
) -> str:
    """Create/refresh ``:CountryRegistration`` + ``:REGISTERED_IN`` from ``:Asset``."""
    record = tx.run(
        _WRITE_COUNTRY_REGISTRATION_CYPHER,
        asset_id=asset_id,
        value=value,
        iso_code=iso_code,
        prefix=prefix,
    ).single()
    return record["value"] if record else value
