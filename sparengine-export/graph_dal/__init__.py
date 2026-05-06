"""SPARENGINE — Neo4j Data Access Layer.

The single chokepoint between phase scripts and the Neo4j driver. Every
read and every write goes through the helpers in this package. Phase
scripts MUST NOT construct raw Cypher.

Module layout
-------------
- ``__init__``       — ``connect()`` factory, common enums, error re-exports.
- ``errors``         — ``GoldenRuleViolation``, ``VerificationFailed``.
- ``date_node``      — ``link_date()`` materialises ``:Date`` + ``:ON_DATE`` edges.
- ``asset``          — ``:Asset``, ``:Fleet``, ``:TypeCertificate``, ``:CountryRegistration``.
- ``document``       — ``:Document``, ``:Page``, ``:Folder``, ``:Box``, ``:Binder``, ``:DocumentType``.
- ``evidence``       — ``:Form1``, ``:CRS``, ``:JobCard``, ``:NRC``, ``:Repair``, ``:Modification``,
                       ``:STC``, ``:WorkPackage``, ``:BorescopeReport``, ``:NDTReport``, ``:DentBuckleEntry``.
- ``component``      — (Phase 4) ``:Component``, ``:PartNumber``, ``:SerialNumber``, alias edges.
- ``event``          — (Phase 5) ``:Event``, ``:ComponentSnapshot``, install/removal edges.
- ``connector``      — Connector identifiers + ``:Reference`` long tail.
- ``organization``   — ``:Organization``, ``:Person``, regulatory authorities.
- ``finding``        — ``:Finding``, ``:PriorityItem``, ``:AuditRun``.
- ``stamp``          — ``:Stamp`` + ``:BINDS_TO`` logic.
- ``fulltext``       — Lucene-syntax wrappers around the ``:Page.text`` fulltext index.
- ``verify``         — Per-phase verifiers + universal "no fact without evidence" check.
- ``export``         — Phase 10 read queries + APOC export helpers.

Connection lifecycle
--------------------
One ``Driver`` per Python process, one ``Session`` per phase, transactions
explicit per batch. Phase scripts open the driver via ``connect()``::

    from graph_dal import connect
    driver = connect()
    with driver.session(database='neo4j') as session:
        with session.begin_transaction() as tx:
            # ... DAL calls take `tx` as their first argument ...
            tx.commit()
    driver.close()

Environment variables (read by ``connect()``):
    NEO4J_URI       (default: bolt://neo4j:7687)
    NEO4J_USER      (default: neo4j)
    NEO4J_PASSWORD  (required — no default; raises if unset)
    NEO4J_DATABASE  (default: neo4j)
"""

from __future__ import annotations

import enum
import os

from neo4j import Driver, GraphDatabase

from .errors import GoldenRuleViolation, VerificationFailed

__all__ = [
    "connect",
    "GoldenRuleViolation",
    "VerificationFailed",
    "AssetKind",
    "EventKind",
    "DateRole",
    "FindingSeverity",
    "BindingStatus",
    "EvidenceClass",
]


# -----------------------------------------------------------------------------
#  Driver factory
# -----------------------------------------------------------------------------


def connect(
    *,
    uri: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> Driver:
    """Create a Neo4j driver instance from environment variables.

    Parameters override env vars when supplied (used by tests). The returned
    driver is the long-lived per-process connection pool — close it once at
    process exit, not per-phase.

    Raises
    ------
    RuntimeError
        If ``NEO4J_PASSWORD`` is not set in the environment and no override
        is supplied. We refuse to silently default — every connection must
        be authenticated.
    """
    uri = uri or os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    user = user or os.environ.get("NEO4J_USER", "neo4j")
    password = password or os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError(
            "NEO4J_PASSWORD is not set. "
            "Add it to the repo-root .env (see sparengine-export/docker-compose.yml)."
        )
    return GraphDatabase.driver(uri, auth=(user, password))


def database_name() -> str:
    """The target database name. Always ``neo4j`` in Community Edition."""
    return os.environ.get("NEO4J_DATABASE", "neo4j")


# -----------------------------------------------------------------------------
#  Common enums (closed value sets used across phases)
# -----------------------------------------------------------------------------
#
# These are str-Enums so they serialise cleanly into Cypher parameters.
# Phase scripts pass them directly: ``write_event(tx, ..., kind=EventKind.OVERHAUL)``.


class AssetKind(str, enum.Enum):
    """Top-level asset class. Drives the secondary label on ``:Asset``."""

    AIRCRAFT = "AIRCRAFT"
    ENGINE = "ENGINE"
    PROPELLER = "PROPELLER"
    LANDING_GEAR_ASSEMBLY = "LANDING_GEAR_ASSEMBLY"
    APU = "APU"
    ROTOR_SYSTEM = "ROTOR_SYSTEM"
    GEARBOX = "GEARBOX"
    COMPONENT = "COMPONENT"


class EventKind(str, enum.Enum):
    """``:Event.kind`` closed enum."""

    INSTALL = "install"
    REMOVAL = "removal"
    OVERHAUL = "overhaul"
    INSPECTION = "inspection"
    SHOP_VISIT = "shop_visit"
    BORESCOPE = "borescope"
    NDT = "ndt"
    SUBCOMPONENT_CHANGE = "subcomponent_change"
    LOGBOOK_BLOCK = "logbook_block"
    COMPLIANCE = "compliance"


class DateRole(str, enum.Enum):
    """Closed enum for ``:ON_DATE.role`` (Q5).

    Every dated entity → ``:Date`` edge carries one of these roles, so a
    single edge type ``:ON_DATE {role}`` works across all temporal entities.
    """

    EVENT = "event"
    BLOCK_13 = "block_13"               # Form 1 issue date
    DATED = "dated"                     # generic single-date roles (CRS, JobCard accomplished, etc.)
    ACCOMPLISHED = "accomplished"
    INGESTION = "ingestion"             # Document.ingestion_date
    MANUFACTURE = "manufacture"         # Asset.manufacture_date
    DELIVERY = "delivery"               # Asset.delivery_date
    REVISION = "revision"               # Manual / ElectronicDataEntry revision_date
    EFFECTIVE = "effective"             # AD effective_date, Supersession effective_date
    COMPLIANCE_DUE = "compliance_due"   # AD compliance_date
    FROM = "from"                       # LogbookEntry / TechLogEntry from
    TO = "to"                           # LogbookEntry / TechLogEntry to
    FIRST_INSTALL = "first_install"     # Component.first_install_date
    LAST_REPAIR = "last_repair"         # Component.last_repair_date
    REMOVAL = "removal"                 # Component.removal_date
    SNAPSHOT = "snapshot"               # ComponentSnapshot.date
    AUDIT_CUTOFF = "audit_cutoff"       # AuditRun.dossier_cut_off_date
    AUDIT_SNAPSHOT = "audit_snapshot"   # AuditRun.audit_snapshot_date
    SIGNED = "signed"                   # SIGNED_BY relationship date
    ISSUED = "issued"                   # ISSUED_BY relationship date


class FindingSeverity(str, enum.Enum):
    """``:Finding.severity`` — three levels per the SPARENGINE severity matrix."""

    LEVEL_1 = "level_1"   # critical / regulator-stop
    LEVEL_2 = "level_2"   # significant
    LEVEL_3 = "level_3"   # informational


class BindingStatus(str, enum.Enum):
    """``:Stamp.binding_status`` — denormalised flag of the :BINDS_TO edge state.

    Q11d: kept as a property for fast filter queries; ``:BINDS_TO`` edges
    are the truth.
    """

    BOUND = "bound"
    AMBIGUOUS = "ambiguous"
    UNBOUND = "unbound"


class EvidenceClass(str, enum.Enum):
    """``:Document.evidence_class`` — categorises documents by audit weight."""

    PRIMARY = "primary"               # Form 1, CRS, ARC, EASA Form 25
    SECONDARY = "secondary"           # logbook pages, shop visit reports
    ADMINISTRATIVE = "administrative" # invoices, packing slips
    REFERENCE = "reference"           # manuals, electronic data, manufacturer literature
