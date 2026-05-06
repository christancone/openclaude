"""Per-phase verification helpers — Cypher equivalents of the SQLite
``MANDATORY VERIFICATION`` blocks in the phase brief files.

Every phase script calls ``verify_phase_N(driver, asset_id)`` at the end
of its run, before declaring done. The verifier returns a counts dict
(written to ``progress.log``) and raises ``VerificationFailed`` if any
rule is violated. The phase script must STOP on raise.

The single universal rule that runs at every phase end is the **"no fact
node without page evidence"** orphan check (``count_fact_orphans``) —
that is the audit-grade discipline encoded at the data layer.

Phase 1 (the largest phase) has the most rules. As later phase writers
land, more ``verify_phase_N`` functions get added here.
"""

from __future__ import annotations

from typing import Any

from . import database_name
from .errors import VerificationFailed


# =============================================================================
#  Universal queries
# =============================================================================
#
# The list of fact-bearing labels is the "must trace to a Page" set. It
# matches the Phase 12 schema reference exactly. If you add a new fact-
# bearing label, add it here too — that's how the verifier learns to police
# it.

FACT_BEARING_LABELS: list[str] = [
    "Component",
    "Event",
    "Form1",
    "CRS",
    "WorkPackage",
    "JobCard",
    "NonRoutineCard",
    "Repair",
    "Modification",
    "STC",
    "Finding",
    "Stamp",
    "ComponentSnapshot",
    "BorescopeReport",
    "NDTReport",
    "DentBuckleEntry",
]

_FACT_ORPHAN_CYPHER = """
MATCH (n {asset_id: $asset_id})
WHERE any(l IN labels(n) WHERE l IN $fact_labels)
  AND NOT EXISTS { (n)-[:EVIDENCED_BY|CARRIES|HAS_STAMP|CORROBORATED_BY]-(:Page) }
RETURN labels(n) AS labels, count(*) AS n
"""


def count_fact_orphans(session: Any, asset_id: str) -> dict[str, int]:
    """Per-label count of fact-bearing nodes without page evidence.

    Empty dict means the golden rule holds across the whole asset.
    """
    result = session.run(
        _FACT_ORPHAN_CYPHER,
        asset_id=asset_id,
        fact_labels=FACT_BEARING_LABELS,
    )
    out: dict[str, int] = {}
    for record in result:
        # `labels` is a list (Neo4j returns multiple labels per node); pick
        # the most-specific fact-bearing label for the report.
        for lbl in record["labels"]:
            if lbl in FACT_BEARING_LABELS:
                out[lbl] = out.get(lbl, 0) + record["n"]
                break
    return out


_COUNT_LABEL_CYPHER = """
MATCH (n)
WHERE n.asset_id = $asset_id AND $label IN labels(n)
RETURN count(*) AS n
"""


def count_label(session: Any, asset_id: str, label: str) -> int:
    """Count nodes of a given label scoped to the asset."""
    record = session.run(
        _COUNT_LABEL_CYPHER, asset_id=asset_id, label=label
    ).single()
    return int(record["n"]) if record else 0


_FULLTEXT_INDEX_CYPHER = """
SHOW FULLTEXT INDEXES YIELD name, state
WHERE name = $name
RETURN state AS state
"""


def fulltext_index_online(session: Any, name: str = "page_text") -> bool:
    """True if the named fulltext index exists and is in state ONLINE."""
    record = session.run(_FULLTEXT_INDEX_CYPHER, name=name).single()
    return bool(record) and record["state"] == "ONLINE"


_CONSTRAINT_COUNT_CYPHER = """
SHOW CONSTRAINTS YIELD name
RETURN count(*) AS n
"""


def constraint_count(session: Any) -> int:
    """Total uniqueness constraints in the database (across all assets)."""
    record = session.run(_CONSTRAINT_COUNT_CYPHER).single()
    return int(record["n"]) if record else 0


# =============================================================================
#  Phase verifiers
# =============================================================================
#
# Each phase verifier returns a dict of counts shaped to match the
# MANDATORY VERIFICATION block in the corresponding phase brief.
#
# Convention:
#   - Counts are always non-negative ints.
#   - Rule violations are appended to a local list and surfaced together
#     so the phase script can log all of them at once (not just the first).
#   - On any rule violation, raise VerificationFailed at the end.


def verify_schema(driver: Any) -> dict[str, Any]:
    """Confirm that schema.cypher has been applied: constraints exist and
    the page_text fulltext index is online.

    Run by the orchestrator at startup and at the start of any phase.
    Not asset-scoped — schema is global.

    Returns
    -------
    ``{"constraints": int, "page_text_online": bool}``

    Raises
    ------
    VerificationFailed
        If no constraints are defined (schema.cypher hasn't been applied)
        or if the page_text index is missing/offline.
    """
    counts: dict[str, Any] = {}
    rule_violations: list[dict] = []
    with driver.session(database=database_name()) as s:
        n = constraint_count(s)
        counts["constraints"] = n
        if n == 0:
            rule_violations.append({
                "rule": "constraints_present",
                "expected": "> 0",
                "actual": 0,
                "detail": "Run `cypher-shell -f phases/schema.cypher` first.",
            })
        online = fulltext_index_online(s, "page_text")
        counts["page_text_online"] = online
        if not online:
            rule_violations.append({
                "rule": "page_text_fulltext_online",
                "expected": "ONLINE",
                "actual": "missing or non-ONLINE",
                "detail": "Page-level full-text search will fail. Re-run schema.cypher.",
            })
    if rule_violations:
        raise VerificationFailed(phase="schema", counts=counts, rule_violations=rule_violations)
    return counts


def verify_phase_1(driver: Any, asset_id: str) -> dict[str, int]:
    """Phase 1 (indexing) MANDATORY VERIFICATION.

    Rules
    -----
    - ``pages``                       must be > 0
    - ``documents``                   must be > 0
    - ``fact_nodes_no_evidence``      must be == 0  (golden rule)
    - ``page_text_online``            must be true

    Returns
    -------
    A dict of counts. The phase script should append this verbatim to
    ``progress.log``::

        == Phase 1 verification ==
        - pages: 1191
        - documents: 47
        - stamps: 312
        - evidence_records: 188
        - fact_nodes_no_evidence: 0
        - page_text_online: True
    """
    counts: dict[str, int] = {}
    rule_violations: list[dict] = []
    with driver.session(database=database_name()) as s:
        # Carrier hierarchy
        counts["pages"]     = count_label(s, asset_id, "Page")
        counts["documents"] = count_label(s, asset_id, "Document")
        counts["folders"]   = count_label(s, asset_id, "Folder")
        counts["boxes"]     = count_label(s, asset_id, "Box")
        counts["binders"]   = count_label(s, asset_id, "Binder")
        counts["stamps"]    = count_label(s, asset_id, "Stamp")

        # Evidence records (extracted by upstream OCR; created in Phase 1)
        evidence_record_labels = [
            "Form1", "CRS", "WorkPackage", "JobCard", "NonRoutineCard",
            "Repair", "Modification", "STC",
            "BorescopeReport", "NDTReport", "DentBuckleEntry",
        ]
        evidence_total = 0
        for lbl in evidence_record_labels:
            n = count_label(s, asset_id, lbl)
            counts[f"records_{lbl.lower()}"] = n
            evidence_total += n
        counts["evidence_records"] = evidence_total

        # Connector identifiers
        connector_labels = [
            "PartNumber", "SerialNumber", "CertificateNumber",
            "PurchaseOrder", "DrawingNumber", "BatchNumber", "TechLogPage",
            "Reference",
        ]
        connector_total = 0
        for lbl in connector_labels:
            n = count_label(s, asset_id, lbl)
            counts[f"ids_{lbl.lower()}"] = n
            connector_total += n
        counts["connector_identifiers"] = connector_total

        # Date materialiser
        counts["dates"] = count_label(s, asset_id, "Date")

        # Universal: every fact-bearing node must have a page-evidence edge.
        orphans = count_fact_orphans(s, asset_id)
        counts["fact_nodes_no_evidence"] = sum(orphans.values())
        for lbl, n in orphans.items():
            counts[f"orphans_{lbl.lower()}"] = n

        # Fulltext index
        counts["page_text_online"] = int(fulltext_index_online(s, "page_text"))

        # ---- rules ----
        if counts["pages"] == 0:
            rule_violations.append({
                "rule": "pages_present",
                "expected": "> 0",
                "actual": 0,
                "detail": "CSV ingestion failed; no :Page nodes were written.",
            })
        if counts["documents"] == 0:
            rule_violations.append({
                "rule": "documents_present",
                "expected": "> 0",
                "actual": 0,
                "detail": "No :Document nodes — every page must belong to a document.",
            })
        if counts["fact_nodes_no_evidence"] != 0:
            rule_violations.append({
                "rule": "no_fact_node_without_evidence",
                "expected": "0",
                "actual": counts["fact_nodes_no_evidence"],
                "detail": (
                    "Golden rule violated. Per-label breakdown: "
                    + ", ".join(f"{k}={v}" for k, v in orphans.items())
                ),
            })
        if not counts["page_text_online"]:
            rule_violations.append({
                "rule": "page_text_fulltext_online",
                "expected": "ONLINE",
                "actual": "missing or non-ONLINE",
                "detail": "Phase 7.5 verification re-search needs the fulltext index.",
            })

    if rule_violations:
        raise VerificationFailed(phase="1", counts=counts, rule_violations=rule_violations)
    return counts


# Stubs for later phases. They follow the same shape; we'll fill in the
# rules as we write each phase. A phase script that calls a stub gets the
# universal orphan check at minimum — which is the floor we want.

def verify_phase_2(driver: Any, asset_id: str) -> dict[str, int]:
    """Phase 2 (asset detection) — exactly one :Asset confirmed with a
    secondary asset-class label.
    """
    counts: dict[str, int] = {}
    rule_violations: list[dict] = []
    with driver.session(database=database_name()) as s:
        counts["assets"] = count_label(s, asset_id, "Asset")
        # Secondary-label coverage
        for lbl in ["Aircraft", "Engine", "Propeller", "LandingGearAssembly",
                    "APU", "RotorSystem", "Gearbox"]:
            counts[f"asset_class_{lbl.lower()}"] = count_label(s, asset_id, lbl)
        secondary = sum(
            counts[f"asset_class_{lbl.lower()}"]
            for lbl in ["Aircraft", "Engine", "Propeller", "LandingGearAssembly",
                        "APU", "RotorSystem", "Gearbox"]
        )
        counts["asset_class_label_total"] = secondary

        orphans = count_fact_orphans(s, asset_id)
        counts["fact_nodes_no_evidence"] = sum(orphans.values())

        if counts["assets"] != 1:
            rule_violations.append({
                "rule": "exactly_one_asset",
                "expected": "1",
                "actual": counts["assets"],
                "detail": "Each dossier has exactly one :Asset node.",
            })
        if secondary == 0:
            rule_violations.append({
                "rule": "asset_class_label_present",
                "expected": ">= 1 (Aircraft|Engine|Propeller|...)",
                "actual": 0,
                "detail": "Phase 2 must add a secondary asset-class label.",
            })
        if counts["fact_nodes_no_evidence"] != 0:
            rule_violations.append({
                "rule": "no_fact_node_without_evidence",
                "expected": "0",
                "actual": counts["fact_nodes_no_evidence"],
                "detail": ", ".join(f"{k}={v}" for k, v in orphans.items()),
            })
    if rule_violations:
        raise VerificationFailed(phase="2", counts=counts, rule_violations=rule_violations)
    return counts


def verify_no_fact_orphans(driver: Any, asset_id: str, *, phase: str) -> dict[str, int]:
    """Universal at-end-of-phase check.

    Use this from any phase script that doesn't have a dedicated verifier
    yet. Stricter dedicated verifiers (verify_phase_1, _2, …) call this
    internally; standalone callers from phases 4/5/6/6.5/7/7.5/8/9/10
    should call this until their dedicated verifier is written.
    """
    counts: dict[str, int] = {}
    rule_violations: list[dict] = []
    with driver.session(database=database_name()) as s:
        orphans = count_fact_orphans(s, asset_id)
        counts["fact_nodes_no_evidence"] = sum(orphans.values())
        for lbl, n in orphans.items():
            counts[f"orphans_{lbl.lower()}"] = n
        if counts["fact_nodes_no_evidence"] != 0:
            rule_violations.append({
                "rule": "no_fact_node_without_evidence",
                "expected": "0",
                "actual": counts["fact_nodes_no_evidence"],
                "detail": ", ".join(f"{k}={v}" for k, v in orphans.items()),
            })
    if rule_violations:
        raise VerificationFailed(phase=phase, counts=counts, rule_violations=rule_violations)
    return counts
