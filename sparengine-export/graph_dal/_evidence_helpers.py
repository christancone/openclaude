"""Internal helpers used by every fact-bearing writer.

Not part of the public DAL surface — phase scripts should not import from
this module. The two helpers here enforce the "golden rule" (Q7) at a
single chokepoint:

1. ``require_evidence()`` — argument-validation gate. Raises
   ``GoldenRuleViolation`` if ``evidence_page_uid`` or ``evidence_quote``
   is missing/empty. Called at the **top** of every fact-write helper,
   **before** the MERGE runs.

2. ``link_evidence()`` — MERGEs the ``-[:EVIDENCED_BY {quote}]->(:Page)``
   edge from the just-written fact node to its evidence page. Called
   after the fact-node MERGE.

Together they guarantee that no fact-bearing node exists in the graph
without at least one ``:EVIDENCED_BY`` edge into a ``:Page``.
"""

from __future__ import annotations

from typing import Any

from .errors import GoldenRuleViolation


def require_evidence(
    *,
    label: str,
    value: str | None,
    evidence_page_uid: str | None,
    evidence_quote: str | None,
) -> None:
    """Validate that page evidence has been supplied. Raise if not.

    Called before the MERGE — the goal is to fail fast, before any node is
    written without evidence. Empty strings count as missing.
    """
    if not evidence_page_uid:
        raise GoldenRuleViolation(
            label=label,
            value=value,
            missing="evidence_page_uid",
        )
    if not evidence_quote:
        raise GoldenRuleViolation(
            label=label,
            value=value,
            missing="evidence_quote",
        )


_LINK_EVIDENCED_BY_CYPHER = """
MATCH (src {asset_id: $asset_id, value: $source_uid})
WHERE $source_label IN labels(src)
MATCH (p:Page {asset_id: $asset_id, value: $page_uid})
MERGE (src)-[r:EVIDENCED_BY]->(p)
ON CREATE SET r.quote = $quote
ON MATCH  SET r.quote = $quote
RETURN 1 AS ok
"""

_LINK_PAGE_CARRIES_CYPHER = """
MATCH (src {asset_id: $asset_id, value: $source_uid})
WHERE $source_label IN labels(src)
MATCH (p:Page {asset_id: $asset_id, value: $page_uid})
MERGE (p)-[r:CARRIES]->(src)
ON CREATE SET r.quote = $quote
ON MATCH  SET r.quote = $quote
RETURN 1 AS ok
"""


def link_evidenced_by(
    tx: Any,
    *,
    asset_id: str,
    source_uid: str,
    source_label: str,
    page_uid: str,
    quote: str,
) -> None:
    """MERGE the ``(source)-[:EVIDENCED_BY {quote}]->(:Page)`` edge.

    Used by **derived-fact** writers — ``:Component``, ``:Event``,
    ``:Finding``, ``:ComponentSnapshot``. These nodes don't physically
    appear on a page; they're derivations *anchored* to a page.

    Both nodes must already exist (the fact-write helper MERGEs the source
    node just before calling this; pages are created in Phase 1 before any
    other phase runs).

    Raises
    ------
    GoldenRuleViolation
        If either the source node or the page is missing.
    """
    record = tx.run(
        _LINK_EVIDENCED_BY_CYPHER,
        asset_id=asset_id,
        source_uid=source_uid,
        source_label=source_label,
        page_uid=page_uid,
        quote=quote,
    ).single()
    if record is None:
        raise GoldenRuleViolation(
            label=source_label,
            value=source_uid,
            missing=(
                f"either source node or evidence page (page_uid={page_uid!r}) "
                f"not found in asset_id={asset_id!r}"
            ),
        )


def link_page_carries(
    tx: Any,
    *,
    asset_id: str,
    source_uid: str,
    source_label: str,
    page_uid: str,
    quote: str,
) -> None:
    """MERGE the ``(:Page)-[:CARRIES {quote}]->(source)`` edge.

    Used by **evidence-record** writers — ``:Form1``, ``:CRS``,
    ``:WorkPackage``, ``:JobCard``, ``:NonRoutineCard``, ``:Repair``,
    ``:Modification``, ``:STC``, ``:BorescopeReport``, ``:NDTReport``,
    ``:DentBuckleEntry``. These nodes physically appear on a page; the
    page is the *carrier*, not just an evidence anchor.

    Direction: ``Page → record``. The semantic is "this page carries this
    record" (cf. Q11a). The verbatim quote on the edge is the excerpt
    that establishes the record's existence.

    Both nodes must already exist; raises ``GoldenRuleViolation`` if not.
    """
    record = tx.run(
        _LINK_PAGE_CARRIES_CYPHER,
        asset_id=asset_id,
        source_uid=source_uid,
        source_label=source_label,
        page_uid=page_uid,
        quote=quote,
    ).single()
    if record is None:
        raise GoldenRuleViolation(
            label=source_label,
            value=source_uid,
            missing=(
                f"either source node or carrier page (page_uid={page_uid!r}) "
                f"not found in asset_id={asset_id!r}"
            ),
        )


# Backward-compatible alias (older callers may still use the original name).
link_evidence = link_evidenced_by
