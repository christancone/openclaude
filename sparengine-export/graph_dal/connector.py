"""Connector-identifier writers (Layer 5 — page-level mentions).

Owns the eight connector node labels we kept distinct (Q10) plus the
:Reference long-tail collapse, and the typed mention-edge writers from
:Page|:Document to each.

Connector identifiers are not fact-bearing — they're vocabulary nodes
that carry a single ``value`` property and exist to consolidate
cross-document references. The "evidence" for them is the set of
:MENTIONS_* edges from :Pages back; if a connector has no incoming
mention edge, it shouldn't exist (Phase 7.5 verification can audit
this if needed).

Distinct labels (Q10):
    :PartNumber, :SerialNumber, :CertificateNumber, :PurchaseOrder,
    :DrawingNumber, :BatchNumber, :TechLogPage

Long-tail collapse:
    :Reference {ref_type, value}   — covers approval, tracking, report,
    amendment, doc_control, config, project, docket, invoice
"""

from __future__ import annotations

from typing import Any
from ._phase_tag import current_phase
from ._normalize import normalize_identifier, is_noise_identifier


# =============================================================================
#  Identifier writers — one per kept-distinct label
# =============================================================================
#
# Each takes (asset_id, value) and any optional discriminating properties.
# All MERGE-on-(asset_id, value) for idempotency.

_WRITE_PART_NUMBER_CYPHER = """
MERGE (n:PartNumber {asset_id: $asset_id, value: $value})
ON CREATE SET n.manufacturer = $manufacturer,
              n.normalized       = $normalized,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.manufacturer = coalesce($manufacturer, n.manufacturer),
              n.normalized   = coalesce(n.normalized, $normalized)
RETURN n.value AS value
"""


def write_part_number(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    manufacturer: str | None = None,
) -> str | None:
    """MERGE :PartNumber. Returns None if ``value`` is OCR noise (N/A, TBD, etc.).

    Per Lukas Cheatsheet §13, sentinel values like "N/A" are OCR placeholders,
    not real PNs. Writing them creates fan-out noise. Callers should treat a
    None return as "skip — there is no PN here".
    """
    if is_noise_identifier(value):
        return None
    record = tx.run(
        _WRITE_PART_NUMBER_CYPHER,
        asset_id=asset_id, value=value, manufacturer=manufacturer,
        normalized=normalize_identifier(value),
        created_in_phase=current_phase(),
    ).single()
    return record["value"] if record else value


_WRITE_SERIAL_NUMBER_CYPHER = """
MERGE (n:SerialNumber {asset_id: $asset_id, value: $value})
ON CREATE SET n.normalized       = $normalized,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.normalized = coalesce(n.normalized, $normalized)
RETURN n.value AS value
"""


def write_serial_number(tx: Any, *, asset_id: str, value: str) -> str | None:
    """MERGE :SerialNumber. Returns None if ``value`` is OCR noise (N/A, TBD, etc.).

    Per Lukas Cheatsheet §13 and the CL650-6134 audit case study (90
    spurious Components attached to one phantom ``:SerialNumber {value:'N/A'}``),
    sentinel values must not be written as real nodes. Bulk-cert / non-
    serialised parts (oxygen hoses qty 348, placards qty 390, PMA sub-
    components) legitimately carry "N/A" in block 10 of the actual Form 1 —
    the cert is correct, but the graph model must not promote "N/A" to a
    node. Callers should treat a None return as "skip — there is no SN
    here; the part is non-serialised/bulk."
    """
    if is_noise_identifier(value):
        return None
    record = tx.run(_WRITE_SERIAL_NUMBER_CYPHER, asset_id=asset_id, value=value,
        normalized=normalize_identifier(value),
        created_in_phase=current_phase(),).single()
    return record["value"] if record else value


_WRITE_CERTIFICATE_NUMBER_CYPHER = """
MERGE (n:CertificateNumber {asset_id: $asset_id, value: $value})
ON CREATE SET n.cert_type = $cert_type,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.cert_type = coalesce($cert_type, n.cert_type)
RETURN n.value AS value
"""


def write_certificate_number(
    tx: Any, *, asset_id: str, value: str, cert_type: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_CERTIFICATE_NUMBER_CYPHER,
        asset_id=asset_id, value=value, cert_type=cert_type,
        created_in_phase=current_phase(),
    ).single()
    return record["value"] if record else value


_WRITE_PURCHASE_ORDER_CYPHER = """
MERGE (n:PurchaseOrder {asset_id: $asset_id, value: $value})
RETURN n.value AS value
"""


def write_purchase_order(tx: Any, *, asset_id: str, value: str) -> str:
    record = tx.run(_WRITE_PURCHASE_ORDER_CYPHER, asset_id=asset_id, value=value,
        created_in_phase=current_phase(),).single()
    return record["value"] if record else value


_WRITE_DRAWING_NUMBER_CYPHER = """
MERGE (n:DrawingNumber {asset_id: $asset_id, value: $value})
RETURN n.value AS value
"""


def write_drawing_number(tx: Any, *, asset_id: str, value: str) -> str:
    record = tx.run(_WRITE_DRAWING_NUMBER_CYPHER, asset_id=asset_id, value=value,
        created_in_phase=current_phase(),).single()
    return record["value"] if record else value


_WRITE_BATCH_NUMBER_CYPHER = """
MERGE (n:BatchNumber {asset_id: $asset_id, value: $value})
ON CREATE SET n.sn_range_start = $sn_range_start, n.sn_range_end = $sn_range_end,
              n.created_in_phase = $created_in_phase
ON MATCH  SET n.sn_range_start = coalesce($sn_range_start, n.sn_range_start),
              n.sn_range_end   = coalesce($sn_range_end, n.sn_range_end)
RETURN n.value AS value
"""


def write_batch_number(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    sn_range_start: str | None = None,
    sn_range_end: str | None = None,
) -> str:
    """Batch numbers carry SN-range when known (Phase 7.5 batch-range inheritance)."""
    record = tx.run(
        _WRITE_BATCH_NUMBER_CYPHER,
        asset_id=asset_id, value=value,
        sn_range_start=sn_range_start, sn_range_end=sn_range_end,
        created_in_phase=current_phase(),
    ).single()
    return record["value"] if record else value


_WRITE_TECH_LOG_PAGE_CYPHER = """
MERGE (n:TechLogPage {asset_id: $asset_id, value: $value})
RETURN n.value AS value
"""


def write_tech_log_page(tx: Any, *, asset_id: str, value: str) -> str:
    record = tx.run(_WRITE_TECH_LOG_PAGE_CYPHER, asset_id=asset_id, value=value,
        created_in_phase=current_phase(),).single()
    return record["value"] if record else value


_WRITE_REFERENCE_CYPHER = """
MERGE (n:Reference {asset_id: $asset_id, ref_type: $ref_type, value: $value})
RETURN n.value AS value
"""


# Closed enum for :Reference.ref_type — the long tail Q10 collapsed.
REFERENCE_TYPES = frozenset({
    "approval", "tracking", "report", "amendment",
    "doc_control", "config", "project", "docket", "invoice",
})


def write_reference(
    tx: Any, *, asset_id: str, ref_type: str, value: str,
) -> str:
    """Long-tail identifier. ``ref_type`` must be in REFERENCE_TYPES."""
    if ref_type not in REFERENCE_TYPES:
        raise ValueError(
            f"write_reference: ref_type={ref_type!r} is not in the closed enum. "
            f"Valid: {sorted(REFERENCE_TYPES)}. "
            f"If this is a real category, either map it to an existing one or "
            f"promote it to its own label (and update Q10 in the migration plan)."
        )
    record = tx.run(
        _WRITE_REFERENCE_CYPHER,
        asset_id=asset_id, ref_type=ref_type, value=value,
        created_in_phase=current_phase(),
    ).single()
    return record["value"] if record else value


# =============================================================================
#  Mention-edge writers — page/document → identifier
# =============================================================================
#
# Each link_mentions_X takes:
#   - tx, asset_id
#   - source_label: "Page" or "Document" (the only valid mention sources)
#   - source_uid:   the value of the source node
#   - target_value: the identifier's value
#   - level:        "page" or "doc" (mirrors source_label; carried as edge prop)
#
# The edge type is per-target-label (Q10 — typed edges for the kept-distinct
# connectors, single :REFS edge for the long-tail :Reference).
#
# Implementation note: we cannot parameterise edge types in Cypher, so each
# helper has its own templated query. APOC could collapse this with
# apoc.merge.relationship, but explicit edge types are faster and more
# readable.


def _mention_query(edge_type: str, target_label: str) -> str:
    """Internal: generate the Cypher for a typed :MENTIONS_X edge.

    Both source labels (:Page and :Document) live in the same per-asset
    namespace and key on (asset_id, value), so one query covers both.
    """
    return f"""
MATCH (src {{asset_id: $asset_id, value: $source_uid}})
WHERE $source_label IN labels(src)
MATCH (tgt:{target_label} {{asset_id: $asset_id, value: $target_value}})
MERGE (src)-[r:{edge_type}]->(tgt)
ON CREATE SET r.level = $level
ON MATCH  SET r.level = $level
"""


_LINK_MENTIONS_PN_CYPHER = _mention_query("MENTIONS_PN", "PartNumber")
_LINK_MENTIONS_SN_CYPHER = _mention_query("MENTIONS_SN", "SerialNumber")
_LINK_MENTIONS_CERT_CYPHER = _mention_query("MENTIONS_CERT", "CertificateNumber")
_LINK_MENTIONS_PO_CYPHER = _mention_query("MENTIONS_PO", "PurchaseOrder")
_LINK_MENTIONS_DRAWING_CYPHER = _mention_query("MENTIONS_DRAWING", "DrawingNumber")
_LINK_MENTIONS_BATCH_CYPHER = _mention_query("MENTIONS_BATCH", "BatchNumber")
_LINK_MENTIONS_TECHLOG_PAGE_CYPHER = _mention_query("MENTIONS_TECHLOG_PAGE", "TechLogPage")


def link_mentions_pn(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_PN_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


def link_mentions_sn(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_SN_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


def link_mentions_cert(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_CERT_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


def link_mentions_po(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_PO_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


def link_mentions_drawing(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_DRAWING_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


def link_mentions_batch(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_BATCH_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


def link_mentions_techlog_page(tx: Any, *, asset_id, source_label, source_uid, target_value, level):
    tx.run(_LINK_MENTIONS_TECHLOG_PAGE_CYPHER, asset_id=asset_id,
           source_label=source_label, source_uid=source_uid,
           target_value=target_value, level=level,
           created_in_phase=current_phase(),).consume()


_LINK_REFS_CYPHER = """
MATCH (src {asset_id: $asset_id, value: $source_uid})
WHERE $source_label IN labels(src)
MATCH (tgt:Reference {asset_id: $asset_id, ref_type: $ref_type, value: $target_value})
MERGE (src)-[r:REFS {ref_type: $ref_type}]->(tgt)
ON CREATE SET r.level = $level,
              r.created_in_phase = $created_in_phase
ON MATCH  SET r.level = $level
"""


def link_refs(
    tx: Any, *, asset_id: str, source_label: str, source_uid: str,
    ref_type: str, target_value: str, level: str,
) -> None:
    """Long-tail :REFS edge to :Reference (carries ref_type on both sides).

    ``ref_type`` must be in :data:`REFERENCE_TYPES`.
    """
    if ref_type not in REFERENCE_TYPES:
        raise ValueError(
            f"link_refs: ref_type={ref_type!r} not in REFERENCE_TYPES."
        )
    tx.run(
        _LINK_REFS_CYPHER,
        asset_id=asset_id, source_label=source_label, source_uid=source_uid,
        ref_type=ref_type, target_value=target_value, level=level,
        created_in_phase=current_phase(),
    ).consume()


# =============================================================================
#  Typed mention-edge writers — fast path
# =============================================================================
#
# The functions above use a label-free ``MATCH (src ...)`` then a
# ``WHERE $source_label IN labels(src)`` filter — that pattern bypasses the
# per-label (asset_id, value) unique-constraint index and falls back to an
# AllNodesScan, costing minutes per merge on large dossiers. The helpers
# below are the index-friendly variants: the source label is baked into the
# query template so Neo4j can use the index seek. They have the same
# semantics as their untyped counterparts, just faster.
#
# Two variants per connector: ``_from_page`` and ``_from_document``.

def _mention_query_typed(edge_type: str, source_label: str, target_label: str) -> str:
    return f"""
MATCH (src:{source_label} {{asset_id: $asset_id, value: $source_uid}})
MATCH (tgt:{target_label} {{asset_id: $asset_id, value: $target_value}})
MERGE (src)-[r:{edge_type}]->(tgt)
ON CREATE SET r.level = $level
ON MATCH  SET r.level = $level
"""


_PAGE_MENTIONS_PN = _mention_query_typed("MENTIONS_PN", "Page", "PartNumber")
_PAGE_MENTIONS_SN = _mention_query_typed("MENTIONS_SN", "Page", "SerialNumber")
_PAGE_MENTIONS_CERT = _mention_query_typed("MENTIONS_CERT", "Page", "CertificateNumber")
_PAGE_MENTIONS_PO = _mention_query_typed("MENTIONS_PO", "Page", "PurchaseOrder")
_PAGE_MENTIONS_DRAWING = _mention_query_typed("MENTIONS_DRAWING", "Page", "DrawingNumber")
_PAGE_MENTIONS_BATCH = _mention_query_typed("MENTIONS_BATCH", "Page", "BatchNumber")
_PAGE_MENTIONS_TECHLOG = _mention_query_typed("MENTIONS_TECHLOG_PAGE", "Page", "TechLogPage")


def page_mentions_pn(tx, *, asset_id, page_uid, pn_value, level="page"):
    tx.run(_PAGE_MENTIONS_PN, asset_id=asset_id,
           source_uid=page_uid, target_value=pn_value, level=level).consume()


def page_mentions_sn(tx, *, asset_id, page_uid, sn_value, level="page"):
    tx.run(_PAGE_MENTIONS_SN, asset_id=asset_id,
           source_uid=page_uid, target_value=sn_value, level=level).consume()


def page_mentions_cert(tx, *, asset_id, page_uid, cert_value, level="page"):
    tx.run(_PAGE_MENTIONS_CERT, asset_id=asset_id,
           source_uid=page_uid, target_value=cert_value, level=level).consume()


def page_mentions_po(tx, *, asset_id, page_uid, po_value, level="page"):
    tx.run(_PAGE_MENTIONS_PO, asset_id=asset_id,
           source_uid=page_uid, target_value=po_value, level=level).consume()


def page_mentions_drawing(tx, *, asset_id, page_uid, drawing_value, level="page"):
    tx.run(_PAGE_MENTIONS_DRAWING, asset_id=asset_id,
           source_uid=page_uid, target_value=drawing_value, level=level).consume()


def page_mentions_batch(tx, *, asset_id, page_uid, batch_value, level="page"):
    tx.run(_PAGE_MENTIONS_BATCH, asset_id=asset_id,
           source_uid=page_uid, target_value=batch_value, level=level).consume()


def page_mentions_techlog(tx, *, asset_id, page_uid, techlog_value, level="page"):
    tx.run(_PAGE_MENTIONS_TECHLOG, asset_id=asset_id,
           source_uid=page_uid, target_value=techlog_value, level=level).consume()


_PAGE_REFS_CYPHER = """
MATCH (src:Page {asset_id: $asset_id, value: $source_uid})
MATCH (tgt:Reference {asset_id: $asset_id, ref_type: $ref_type, value: $target_value})
MERGE (src)-[r:REFS {ref_type: $ref_type}]->(tgt)
ON CREATE SET r.level = $level
ON MATCH  SET r.level = $level
"""


def page_refs(tx, *, asset_id, page_uid, ref_type, target_value, level="page"):
    if ref_type not in REFERENCE_TYPES:
        raise ValueError(f"page_refs: ref_type={ref_type!r} not in REFERENCE_TYPES.")
    tx.run(_PAGE_REFS_CYPHER, asset_id=asset_id, source_uid=page_uid,
           ref_type=ref_type, target_value=target_value, level=level).consume()
