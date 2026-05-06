"""Carrier-hierarchy writers (Layer 5).

Owns: ``:Folder``, ``:Box``, ``:Binder``, ``:Document``, ``:Page``,
``:DocumentType``.

These nodes are themselves the substrate the golden rule rests on. Pages
are the universal evidence target; every other fact-bearing node points
``:EVIDENCED_BY`` at one of these. So none of the writers in this module
require ``evidence_page_uid`` themselves — they're not fact-bearing in
the SPARENGINE sense.

Phase 1 calls these writers in the following order per CSV row:

    1. ``write_folder`` / ``write_box`` / ``write_binder``  (carrier path)
    2. ``write_document_type``                              (taxonomy)
    3. ``write_document``                                    (per file)
    4. ``write_page``                                        (per CSV row)

Then evidence-record writers in ``evidence.py`` consume those pages as
``:CARRIES`` targets.
"""

from __future__ import annotations

from typing import Any

from .date_node import link_date


# -----------------------------------------------------------------------------
#  :Folder, :Box, :Binder  — carrier hierarchy
# -----------------------------------------------------------------------------
#
# The carrier hierarchy is optional — many digital-only dossiers won't have
# physical Folder/Box/Binder structure. Phase 1 calls these only when the
# CSV's `extracted_json.metadata` carries the structure.
#
# The chain is :Asset-[:HAS_FOLDER]->:Folder-[:CONTAINS]->:Box-
#              [:CONTAINS]->:Binder-[:CONTAINS]->:Document.
# Any link in the chain may be skipped — ``write_document`` accepts an
# optional `binder_value`, `box_value`, `folder_value` and links whatever
# is supplied.

_WRITE_FOLDER_CYPHER = """
MERGE (f:Folder {asset_id: $asset_id, value: $value})
ON CREATE SET f.name = $name
ON MATCH  SET f.name = coalesce($name, f.name)
WITH f
MATCH (a:Asset {asset_id: $asset_id})
MERGE (a)-[:HAS_FOLDER]->(f)
RETURN f.value AS value
"""


def write_folder(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    name: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_FOLDER_CYPHER, asset_id=asset_id, value=value, name=name
    ).single()
    return record["value"] if record else value


_WRITE_BOX_CYPHER = """
MERGE (b:Box {asset_id: $asset_id, value: $value})
ON CREATE SET b.name = $name
ON MATCH  SET b.name = coalesce($name, b.name)
WITH b
OPTIONAL MATCH (f:Folder {asset_id: $asset_id, value: $folder_value})
FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END |
    MERGE (f)-[:CONTAINS]->(b)
)
RETURN b.value AS value
"""


def write_box(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    folder_value: str | None = None,
    name: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_BOX_CYPHER,
        asset_id=asset_id,
        value=value,
        folder_value=folder_value,
        name=name,
    ).single()
    return record["value"] if record else value


_WRITE_BINDER_CYPHER = """
MERGE (b:Binder {asset_id: $asset_id, value: $value})
ON CREATE SET b.name = $name
ON MATCH  SET b.name = coalesce($name, b.name)
WITH b
OPTIONAL MATCH (box:Box {asset_id: $asset_id, value: $box_value})
FOREACH (_ IN CASE WHEN box IS NULL THEN [] ELSE [1] END |
    MERGE (box)-[:CONTAINS]->(b)
)
RETURN b.value AS value
"""


def write_binder(
    tx: Any,
    *,
    asset_id: str,
    value: str,
    box_value: str | None = None,
    name: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_BINDER_CYPHER,
        asset_id=asset_id,
        value=value,
        box_value=box_value,
        name=name,
    ).single()
    return record["value"] if record else value


# -----------------------------------------------------------------------------
#  :DocumentType (per-asset taxonomy node)
# -----------------------------------------------------------------------------

_WRITE_DOCUMENT_TYPE_CYPHER = """
MERGE (dt:DocumentType {asset_id: $asset_id, value: $value})
ON CREATE SET dt.name = $name, dt.schema_enum = $schema_enum
ON MATCH  SET dt.name = coalesce($name, dt.name),
              dt.schema_enum = coalesce($schema_enum, dt.schema_enum)
RETURN dt.value AS value
"""


def write_document_type(
    tx: Any,
    *,
    asset_id: str,
    value: str,                # closed-enum string from sparengine-export/phases/document_types.md
    name: str | None = None,   # human-readable name
    schema_enum: str | None = None,
) -> str:
    record = tx.run(
        _WRITE_DOCUMENT_TYPE_CYPHER,
        asset_id=asset_id,
        value=value,
        name=name,
        schema_enum=schema_enum,
    ).single()
    return record["value"] if record else value


# -----------------------------------------------------------------------------
#  :Document
# -----------------------------------------------------------------------------

_WRITE_DOCUMENT_CYPHER = """
MERGE (d:Document {asset_id: $asset_id, value: $value})
ON CREATE SET d.file_name        = $file_name,
              d.document_type    = $document_type,
              d.schema_enum      = $schema_enum,
              d.evidence_class   = $evidence_class,
              d.page_count       = $page_count,
              d.live_page_count  = $live_page_count,
              d.chunk_count      = $chunk_count,
              d.title            = $title,
              d.is_mis_export    = $is_mis_export,
              d.mis_system       = $mis_system
ON MATCH  SET d.file_name        = coalesce($file_name, d.file_name),
              d.document_type    = coalesce($document_type, d.document_type),
              d.schema_enum      = coalesce($schema_enum, d.schema_enum),
              d.evidence_class   = coalesce($evidence_class, d.evidence_class),
              d.page_count       = coalesce($page_count, d.page_count),
              d.live_page_count  = coalesce($live_page_count, d.live_page_count),
              d.chunk_count      = coalesce($chunk_count, d.chunk_count),
              d.title            = coalesce($title, d.title),
              d.is_mis_export    = coalesce($is_mis_export, d.is_mis_export),
              d.mis_system       = coalesce($mis_system, d.mis_system)
WITH d
OPTIONAL MATCH (binder:Binder {asset_id: $asset_id, value: $binder_value})
FOREACH (_ IN CASE WHEN binder IS NULL THEN [] ELSE [1] END |
    MERGE (binder)-[:CONTAINS]->(d)
)
WITH d
OPTIONAL MATCH (dt:DocumentType {asset_id: $asset_id, value: $document_type})
FOREACH (_ IN CASE WHEN dt IS NULL THEN [] ELSE [1] END |
    MERGE (d)-[:CLASSIFIED_AS]->(dt)
)
RETURN d.value AS value
"""


def write_document(
    tx: Any,
    *,
    asset_id: str,
    value: str,                          # canonical document identifier (UUID from upstream)
    file_name: str,
    document_type: str | None = None,    # closed-enum value from document_types.md
    schema_enum: str | None = None,
    evidence_class: str | None = None,   # primary | secondary | administrative | reference
    page_count: int | None = None,
    live_page_count: int | None = None,
    chunk_count: int | None = None,
    title: str | None = None,
    binder_value: str | None = None,     # parent :Binder if known
    is_mis_export: bool | None = None,
    mis_system: str | None = None,
    ingestion_date_iso: str | None = None,
) -> str:
    """MERGE :Document, link to :Binder (if supplied) and :DocumentType."""
    record = tx.run(
        _WRITE_DOCUMENT_CYPHER,
        asset_id=asset_id,
        value=value,
        file_name=file_name,
        document_type=document_type,
        schema_enum=schema_enum,
        evidence_class=evidence_class,
        page_count=page_count,
        live_page_count=live_page_count,
        chunk_count=chunk_count,
        title=title,
        is_mis_export=is_mis_export,
        mis_system=mis_system,
        binder_value=binder_value,
    ).single()
    if record is None:
        raise RuntimeError(f"write_document: MERGE returned no record (value={value!r})")
    if ingestion_date_iso:
        link_date(
            tx,
            asset_id=asset_id,
            source_uid=value,
            source_label="Document",
            role="ingestion",
            date_iso=ingestion_date_iso,
        )
    return record["value"]


# -----------------------------------------------------------------------------
#  :Page
# -----------------------------------------------------------------------------

_WRITE_PAGE_CYPHER = """
MERGE (p:Page {asset_id: $asset_id, value: $value})
ON CREATE SET p.page_index             = $page_index,
              p.file_type              = $file_type,
              p.is_blank               = $is_blank,
              p.is_template_empty      = $is_template_empty,
              p.is_removed             = $is_removed,
              p.rotation_deg           = $rotation_deg,
              p.chunk_count            = $chunk_count,
              p.chunks_with_embeddings = $chunks_with_embeddings,
              p.s3_key                 = $s3_key,
              p.original_path          = $original_path,
              p.text                   = $text,
              p.title                  = $title
ON MATCH  SET p.page_index             = coalesce($page_index, p.page_index),
              p.file_type              = coalesce($file_type, p.file_type),
              p.is_blank               = coalesce($is_blank, p.is_blank),
              p.is_template_empty      = coalesce($is_template_empty, p.is_template_empty),
              p.is_removed             = coalesce($is_removed, p.is_removed),
              p.rotation_deg           = coalesce($rotation_deg, p.rotation_deg),
              p.chunk_count            = coalesce($chunk_count, p.chunk_count),
              p.chunks_with_embeddings = coalesce($chunks_with_embeddings, p.chunks_with_embeddings),
              p.s3_key                 = coalesce($s3_key, p.s3_key),
              p.original_path          = coalesce($original_path, p.original_path),
              p.text                   = coalesce($text, p.text),
              p.title                  = coalesce($title, p.title)
WITH p
MATCH (d:Document {asset_id: $asset_id, value: $document_uid})
MERGE (d)-[:HAS_PAGE]->(p)
RETURN p.value AS value
"""


def write_page(
    tx: Any,
    *,
    asset_id: str,
    value: str,                              # canonical page identifier (UUID from upstream)
    document_uid: str,                       # parent document UID — required
    page_index: int,
    text: str = "",                          # the OCR'd text (indexed by the page_text fulltext)
    title: str | None = None,
    file_type: str | None = None,
    is_blank: bool | None = None,
    is_template_empty: bool | None = None,
    is_removed: bool | None = None,
    rotation_deg: int | None = None,
    chunk_count: int | None = None,
    chunks_with_embeddings: int | None = None,
    s3_key: str | None = None,
    original_path: str | None = None,
) -> str:
    """MERGE :Page, link to its parent :Document via :HAS_PAGE.

    The full OCR text for the page is stored in ``p.text``; the fulltext
    index ``page_text`` (defined in schema.cypher) covers this property
    plus ``p.title``. Phase 7.5 verification searches use this index via
    ``graph_dal.fulltext.search_pages()``.

    Pages are the universal evidence target — they are not fact-bearing
    nodes themselves and don't carry an :EVIDENCED_BY edge. Every other
    fact-bearing writer points :EVIDENCED_BY at a :Page created here.
    """
    record = tx.run(
        _WRITE_PAGE_CYPHER,
        asset_id=asset_id,
        value=value,
        document_uid=document_uid,
        page_index=page_index,
        text=text,
        title=title,
        file_type=file_type,
        is_blank=is_blank,
        is_template_empty=is_template_empty,
        is_removed=is_removed,
        rotation_deg=rotation_deg,
        chunk_count=chunk_count,
        chunks_with_embeddings=chunks_with_embeddings,
        s3_key=s3_key,
        original_path=original_path,
    ).single()
    if record is None:
        raise RuntimeError(
            f"write_page: MERGE returned no record (value={value!r}, "
            f"document_uid={document_uid!r}). The parent :Document must "
            f"be written before any of its pages."
        )
    return record["value"]
