"""Form 1 structural edge writers — the simple five-edge contract.

Per the simplified Form 1 model adopted 2026-05-19: every Form 1 has
exactly five direct relationships, nothing nested or transitive:

    :Page-[:CARRIES]->:Form1                 — evidence anchor (page mentions it)
    :Form1-[:RELEASES_PN]->:PartNumber       — block 8 PN
    :Form1-[:RELEASES_SN]->:SerialNumber     — block 10 SN
    :Form1-[:SIGNED_BY]->:Person             — block 13b/14b signer
    :Component-[:HAS_SN]->:SerialNumber      — Component ↔ Form 1 bridge (via shared SN)

The DEPRECATED edge writers below are kept as no-ops for backward
compatibility but should NOT be called by new phase code:

    link_form1_covers_range          — was :Form1-[:COVERS_RANGE]->:BatchNumber
    link_form1_post_sb_release       — was :Form1-[:POST_SB_RELEASE]->:ServiceBulletin
    link_crs_paired_with_form1       — was :CRS-[:PAIRED_WITH]->:Form1

Form1 → Component direct link (link_form1_releases_component in event.py) is
also deprecated — use the SN bridge instead.

Every helper accepts an optional ``source`` and ``confidence`` so phases
can tag *where* an edge came from:

    source='ocr_block_8'        — Phase 1 wrote it from the OCR's structured
                                  block 8 entry. Highest authority.
    source='csv_table_row'      — migration script read it from the CSV's
                                  tables[].rows directly.
    source='page_co_mention'    — fallback heuristic when block extraction
                                  was unavailable. confidence='medium'.
    source='mod_status_property'— derived from form1.block_12_mod_status.

Each writer walks the high-confidence PN/SN alias graph (``ALIAS_OF`` with
``kind IN ['ocr_variant','incomplete_pn','sn_ocr_separator','sn_zero_padding']``)
once before the MERGE, so the edge target is always the canonical primary.
The literal block-6/block-7 OCR value is preserved on the edge as
``r.raw_pn`` / ``r.raw_sn`` for audit.
"""

from __future__ import annotations

from typing import Any
from ._phase_tag import current_phase


# Closed enums.
RELEASES_PN_BLOCKS = frozenset({"6", "8", "6_inferred", "8_inferred"})
RELEASES_SN_BLOCKS = frozenset({"7", "10", "7_inferred", "10_inferred", "7_batch"})
RELEASES_PN_SOURCES = frozenset({
    "ocr_block_8", "ocr_block_6", "csv_table_row",
    "page_co_mention", "page_co_mention_strict",
})
RELEASES_SN_SOURCES = frozenset({
    "ocr_block_10", "ocr_block_7", "csv_table_row",
    "page_co_mention", "page_co_mention_strict", "batch_range_expansion",
})
POST_SB_SOURCES = frozenset({
    "mod_status_property", "page_co_mention", "block_12_text_regex",
})
CONFIDENCES = frozenset({"high", "medium", "ambiguous", "low"})

# Alias kinds we walk during edge writing. Anything beyond these is too
# noisy (e.g. v8.4 'vendor_oem_co_mention' historically produced fanout).
HIGH_CONF_PN_ALIAS_KINDS = ["ocr_variant", "incomplete_pn"]
HIGH_CONF_SN_ALIAS_KINDS = ["sn_ocr_separator", "sn_zero_padding"]


# =============================================================================
#  :Form1-[:RELEASES_PN]->:PartNumber
# =============================================================================

_LINK_RELEASES_PN_CYPHER = """
MATCH (f:Form1 {asset_id: $aid, value: $f_uid})
MATCH (pn_raw:PartNumber {asset_id: $aid, value: $pn_value})
OPTIONAL MATCH (pn_raw)-[a:ALIAS_OF]->(pn_primary:PartNumber)
WHERE a.kind IN $alias_kinds
WITH f, pn_raw, coalesce(pn_primary, pn_raw) AS pn
MERGE (f)-[r:RELEASES_PN]->(pn)
ON CREATE SET r.block      = $block,
              r.source     = $source,
              r.confidence = $confidence,
              r.raw_pn     = pn_raw.value,
              r.walked_alias = (pn <> pn_raw),
              r.form_tracking_no = $form_tracking_no,
              r.created_in_phase = $phase
ON MATCH  SET r.confidence = coalesce($confidence, r.confidence),
              r.source     = coalesce($source, r.source),
              r.form_tracking_no = coalesce($form_tracking_no, r.form_tracking_no)
"""


def link_form1_releases_pn(
    tx: Any, *, asset_id: str, form1_uid: str, pn_value: str,
    block: str = "8",
    source: str = "ocr_block_8",
    confidence: str = "high",
    form_tracking_no: str | None = None,
) -> None:
    """:Form1-[:RELEASES_PN]->:PartNumber, alias-walked to canonical primary."""
    if block not in RELEASES_PN_BLOCKS:
        raise ValueError(f"link_form1_releases_pn: block={block!r} not in {sorted(RELEASES_PN_BLOCKS)}")
    if source not in RELEASES_PN_SOURCES:
        raise ValueError(f"link_form1_releases_pn: source={source!r} not in {sorted(RELEASES_PN_SOURCES)}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"link_form1_releases_pn: confidence={confidence!r} not in {sorted(CONFIDENCES)}")
    tx.run(
        _LINK_RELEASES_PN_CYPHER,
        aid=asset_id, f_uid=form1_uid, pn_value=pn_value,
        block=block, source=source, confidence=confidence,
        form_tracking_no=form_tracking_no,
        alias_kinds=HIGH_CONF_PN_ALIAS_KINDS,
        phase=current_phase(),
    ).consume()


# =============================================================================
#  :Form1-[:RELEASES_SN]->:SerialNumber
# =============================================================================

_LINK_RELEASES_SN_CYPHER = """
MATCH (f:Form1 {asset_id: $aid, value: $f_uid})
MATCH (sn_raw:SerialNumber {asset_id: $aid, value: $sn_value})
OPTIONAL MATCH (sn_raw)-[a:ALIAS_OF]->(sn_primary:SerialNumber)
WHERE a.kind IN $alias_kinds
WITH f, sn_raw, coalesce(sn_primary, sn_raw) AS sn
MERGE (f)-[r:RELEASES_SN]->(sn)
ON CREATE SET r.block      = $block,
              r.source     = $source,
              r.confidence = $confidence,
              r.raw_sn     = sn_raw.value,
              r.walked_alias = (sn <> sn_raw),
              r.form_tracking_no = $form_tracking_no,
              r.created_in_phase = $phase
ON MATCH  SET r.confidence = coalesce($confidence, r.confidence),
              r.source     = coalesce($source, r.source),
              r.form_tracking_no = coalesce($form_tracking_no, r.form_tracking_no)
"""


def link_form1_releases_sn(
    tx: Any, *, asset_id: str, form1_uid: str, sn_value: str,
    block: str = "10",
    source: str = "ocr_block_10",
    confidence: str = "high",
    form_tracking_no: str | None = None,
) -> None:
    """:Form1-[:RELEASES_SN]->:SerialNumber, alias-walked to canonical primary."""
    if block not in RELEASES_SN_BLOCKS:
        raise ValueError(f"link_form1_releases_sn: block={block!r} not in {sorted(RELEASES_SN_BLOCKS)}")
    if source not in RELEASES_SN_SOURCES:
        raise ValueError(f"link_form1_releases_sn: source={source!r} not in {sorted(RELEASES_SN_SOURCES)}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"link_form1_releases_sn: confidence={confidence!r} not in {sorted(CONFIDENCES)}")
    tx.run(
        _LINK_RELEASES_SN_CYPHER,
        aid=asset_id, f_uid=form1_uid, sn_value=sn_value,
        block=block, source=source, confidence=confidence,
        form_tracking_no=form_tracking_no,
        alias_kinds=HIGH_CONF_SN_ALIAS_KINDS,
        phase=current_phase(),
    ).consume()


# =============================================================================
#  :Form1-[:COVERS_RANGE]->:BatchNumber
# =============================================================================

_LINK_COVERS_RANGE_CYPHER = """
MATCH (f:Form1 {asset_id: $aid, value: $f_uid})
MATCH (bn:BatchNumber {asset_id: $aid, value: $batch_value})
MERGE (f)-[r:COVERS_RANGE]->(bn)
ON CREATE SET r.source           = $source,
              r.sn_range_start   = bn.sn_range_start,
              r.sn_range_end     = bn.sn_range_end,
              r.form_tracking_no = $form_tracking_no,
              r.created_in_phase = $phase
ON MATCH  SET r.form_tracking_no = coalesce($form_tracking_no, r.form_tracking_no)
"""


def link_form1_covers_range(
    tx: Any, *, asset_id: str, form1_uid: str, batch_value: str,
    source: str = "ocr_block_7",
    form_tracking_no: str | None = None,
) -> None:
    """DEPRECATED 2026-05-19. No-op.

    Per the simplified Form 1 contract, Form 1 carries only PN, SN, signer,
    page evidence. Batch coverage is implicit via the batch-cert flag
    (``Form1.is_batch_cert``); no graph edge is written.
    """
    return  # intentional no-op


# =============================================================================
#  :Form1-[:POST_SB_RELEASE]->:ServiceBulletin
# =============================================================================

_LINK_POST_SB_RELEASE_CYPHER = """
MATCH (f:Form1 {asset_id: $aid, value: $f_uid})
MERGE (sb:ServiceBulletin {asset_id: $aid, value: $sb_value})
ON CREATE SET sb.kind = $sb_kind, sb.created_in_phase = $phase
MERGE (f)-[r:POST_SB_RELEASE]->(sb)
ON CREATE SET r.source     = $source,
              r.confidence = $confidence,
              r.created_in_phase = $phase
ON MATCH  SET r.source     = coalesce($source, r.source),
              r.confidence = coalesce($confidence, r.confidence)
"""


def link_form1_post_sb_release(
    tx: Any, *, asset_id: str, form1_uid: str, sb_value: str,
    source: str = "mod_status_property",
    confidence: str = "high",
    sb_kind: str | None = "inferred_from_form1",
) -> None:
    """DEPRECATED 2026-05-19. No-op.

    Mod status / SB compliance is stored as ``Form1.block_12_mod_status``
    property (comma-separated SB list). No graph edge to :ServiceBulletin
    is wired from the Form 1.
    """
    return  # intentional no-op


# =============================================================================
#  :CRS-[:PAIRED_WITH]->:Form1
# =============================================================================

_LINK_CRS_PAIRED_WITH_CYPHER = """
MATCH (crs:CRS {asset_id: $aid, value: $crs_uid})
MATCH (f:Form1 {asset_id: $aid, value: $f_uid})
MERGE (crs)-[r:PAIRED_WITH]->(f)
ON CREATE SET r.via        = $via,
              r.confidence = $confidence,
              r.created_in_phase = $phase
ON MATCH  SET r.via        = coalesce($via, r.via),
              r.confidence = coalesce($confidence, r.confidence)
"""


def link_crs_paired_with_form1(
    tx: Any, *, asset_id: str, crs_uid: str, form1_uid: str,
    via: str = "work_package_and_component",
    confidence: str = "high",
) -> None:
    """DEPRECATED 2026-05-19. No-op.

    CRS and Form 1 sibling pairing is not part of the simplified Form 1
    contract. Auditors can find the pair via shared :Component or shared
    :Document membership at query time.
    """
    return  # intentional no-op
