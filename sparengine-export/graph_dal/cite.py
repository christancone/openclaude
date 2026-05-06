"""Citation helpers — turn any node into ``(document, page, original_path)``.

Used by Phase 7 / 7.5 / 9 to attach evidence pages to findings, by Phase 10
to embed citations in the export, and by ad-hoc Browser queries to drill
from a graph node back to the source PDF page.

The citation chain is one of:

  fact_node -[:EVIDENCED_BY]-> Page                      (Component, Event, Finding, ComponentSnapshot)
  Page -[:CARRIES]-> evidence_record                       (Form1, CRS, WorkPackage, JobCard, NRC,
                                                            Repair, Modification, STC, BorescopeReport,
                                                            NDTReport, DentBuckleEntry)
  Page -[:HAS_STAMP]-> Stamp                                (Stamps anchor on their carrier page)
  Page -[:MENTIONS_*]-> connector_identifier                (PN, SN, Cert, PO, Drawing, Batch, TLP)
  Page -[:REFS]-> Reference                                  (long-tail typed references)
  Page -[:COVERS_ATA|CITES|MENTIONS_SB|MENTIONS_AD|MENTIONS_EO]-> external_standard
  Stamp -[:CARRIES_CERT]-> CertificateNumber                 (cert numbers from stamp metadata)
  Date <-[:ON_DATE]- temporal_entity                         (date nodes inherit citation)

Every reachable node finds a :Page within 1–3 hops, and that :Page → Document
gives ``(file_name, page_index, original_path)`` for the citation record.
"""

from __future__ import annotations

from typing import Any


# Single Cypher that walks any node back to its citation pages.
# We use `*1..3` because the longest non-trivial chain is:
#   CertificateNumber <- CARRIES_CERT <- Stamp <- HAS_STAMP <- Page    (3 hops)
# Date <- ON_DATE <- (any fact) -> EVIDENCED_BY -> Page                 (3 hops via fact)
_CITE_NODE_CYPHER = """
MATCH (n {asset_id: $asset_id, value: $node_value})
OPTIONAL MATCH (n)-[*1..3]-(p:Page {asset_id: $asset_id})
WITH DISTINCT n, p
WHERE p IS NOT NULL
OPTIONAL MATCH (d:Document {asset_id: $asset_id})-[:HAS_PAGE]->(p)
RETURN DISTINCT
    p.value         AS page_uid,
    p.page_index    AS page_index,
    d.value         AS document_uid,
    d.file_name     AS file_name,
    p.original_path AS original_path
ORDER BY file_name, page_index
"""


def cite_node(
    session: Any, *, asset_id: str, node_value: str, limit: int = 25,
) -> list[dict]:
    """Return citation rows for any node identified by ``(asset_id, value)``.

    Each row is a dict::

        {
            "page_uid":        str,    # Page.value (UUID from upstream OCR)
            "page_index":      int,    # 0-based page within the PDF
            "document_uid":    str,    # Document.value (UUID)
            "file_name":       str,    # PDF file name
            "original_path":   str,    # POSIX-ish path for grouping
        }

    Sorted by ``(file_name, page_index)``. Empty list ⇢ either the node
    doesn't exist or it's a pure vocabulary node with no incoming
    mention chain (in which case it's a non-fact and not auditable).

    For nodes keyed on ``iso`` instead of ``value`` (only ``:Date``), pass
    the ISO string as ``node_value`` — the Date node's ``name`` and
    ``iso`` are aligned by the captions step, but ``value`` is not the
    canonical key for Date. Use :func:`cite_date` for those.
    """
    rows = session.run(
        _CITE_NODE_CYPHER, asset_id=asset_id, node_value=node_value,
    )
    out = [
        {
            "page_uid":      r["page_uid"],
            "page_index":    r["page_index"],
            "document_uid":  r["document_uid"],
            "file_name":     r["file_name"],
            "original_path": r["original_path"],
        }
        for r in rows
    ]
    return out[:limit]


_CITE_DATE_CYPHER = """
MATCH (d:Date {asset_id: $asset_id, iso: $iso})
OPTIONAL MATCH (src)-[r:ON_DATE]->(d)
OPTIONAL MATCH (src)-[*0..2]-(p:Page {asset_id: $asset_id})
WITH DISTINCT d, src, r.role AS role, p
WHERE p IS NOT NULL
OPTIONAL MATCH (doc:Document {asset_id: $asset_id})-[:HAS_PAGE]->(p)
RETURN DISTINCT
    role AS via_role,
    labels(src)[0] AS source_label,
    src.value AS source_uid,
    doc.file_name AS file_name,
    p.page_index AS page_index,
    p.original_path AS original_path
ORDER BY file_name, page_index
"""


def cite_date(
    session: Any, *, asset_id: str, iso: str, limit: int = 25,
) -> list[dict]:
    """Citation rows for a :Date node — every temporal entity that ON_DATEs it
    plus that entity's carrier page.

    Each row::

        {
            "via_role":      str,    # the ON_DATE.role (block_13 / event / dated / ...)
            "source_label":  str,    # Form1 / Event / Stamp / ...
            "source_uid":    str,    # the source node's value
            "file_name":     str,
            "page_index":    int,
            "original_path": str,
        }
    """
    rows = session.run(_CITE_DATE_CYPHER, asset_id=asset_id, iso=iso)
    out = [
        {
            "via_role":      r["via_role"],
            "source_label":  r["source_label"],
            "source_uid":    r["source_uid"],
            "file_name":     r["file_name"],
            "page_index":    r["page_index"],
            "original_path": r["original_path"],
        }
        for r in rows
    ]
    return out[:limit]


# Pretty-printed citation string for a single row.
def format_citation(row: dict) -> str:
    """Render one citation row as ``"<file_name> p.<n>"``.

    Used in finding descriptions and progress.log entries.
    """
    fn = row.get("file_name") or "?"
    pi = row.get("page_index")
    if pi is None:
        return fn
    return f"{fn} p.{pi}"
