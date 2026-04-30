# PHASE 6 — Document Connector Building

**Intent.** Stop being a list, become a graph. Build edges between pages, documents, components, work orders, requirements, stakeholders, persons.

**Reference files:**
- `csv_and_ocr.md` (entities, metadata.reference_numbers)
- `document_types.md` (CRS family, Form 1 family)
- `tiers_and_ata.md` (ATA→tier)
- `data_quality_rules.md`

**Inputs:** all tables populated through Phase 5.

---

## Steps (apply in order)

### 1. Work order clustering

For every distinct `work_order` value in `metadata.reference_numbers` and `entities[]`:
- Insert into `work_orders` (`id` = normalised WO number, `description` from the most informative page, `open_date`/`close_date` from MIN/MAX of pages mentioning it, `mro` from the operator/MRO entity most often co-located).
- For every page mentioning the WO, insert an `edges` row with `edge_type = 'PART_OF_WORK_ORDER'`.
- **Detect CRS coverage:** if any page in the WO has `document_type IN ('certificate_of_release_to_service', 'dual_release_certificate')`, set `work_orders.has_crs = 1` and store the CRS file/page.
- **Set `component_count`** to the number of distinct components touched by events in this WO.

### 2. Reference cross-linking

Build a `reference_number → pages` index. For each shared reference (any `type`), link pages with `REFERENCES` edges (confidence: medium). Don't go quadratic — for very common references (>50 pages), just link to the WO node, not pairwise.

### 3. PN / SN linking

- Every page mentioning a PN gets `PAGE_REFERENCES` to its `part_types` node.
- Every (PN, SN) gets `PAGE_REFERENCES` to its `serials` node.
- Same `serial_number` on different `part_number` values → flag `SN_AMBIGUOUS`.

### 4. Requirement linking

- Every AD/SB/EO/STC reference creates a `requirements` row (`id` = `f"{kind}::{number}::{revision}"`).
- Every page mentioning the requirement → `PAGE_REFERENCES`.
- Every `event_type IN ('sb_compliance', 'ad_compliance')` → `COVERS_REQUIREMENT` edge.

### 5. ATA linking

- Insert `ata_chapters` rows from `metadata.ata_chapters[]` across all pages.
- Every component → `ASSIGNED_ATA` edge to its chapter.
- Every page → `PAGE_REFERENCES` edges to all chapters in `metadata.ata_chapters`.

### 6. Stakeholder linking

- `entities[]` where `entity_type IN ('operator', 'mro')` plus `address_block` sections plus `header_fields` keys like "MRO Name", "Bill To", "Approved Under" → `stakeholders` rows.
- `ISSUED_BY` from documents → MRO stakeholder.
- `APPROVED_UNDER` from documents → regulator (parsed from approval_number prefixes: `UK.145.*` → UK CAA, `EASA.145.*` → EASA, `FAA.145.*` → FAA).

### 7. Person linking

- `entities[]` where `entity_type == 'person'` plus `stamps_and_signatures[].person_name` → `persons` rows.
- `SIGNED_BY` edges from events to persons via stamps.

### 8. Stamp binding

For every stamp:
```python
match stamp.binds_to_target_kind:
    case 'event':     # STAMP_BINDS_TO edge: stamp → event
                      # AND SIGNED_BY edge: event → person (from stamp.person_name)
    case 'entity':    # STAMP_BINDS_TO edge: stamp → entity (PN/SN/WO)
    case 'table_row': # resolve table row to its event/entity
    case 'section':   # STAMP_BINDS_TO edge: stamp → section's event
    case 'page':      # STAMP_BINDS_TO edge: stamp → page (release to service)
```
If `binding_confidence == 'ambiguous'`, raise `STAMP_AMBIGUOUS_BINDING`.

### 9. Attachment detection

When a Form 1 page (`document_type` ∈ Form 1 family per `document_types.md`) directly follows a job card / NRC in the same `document_id` and shares a (PN, SN) → `ATTACHES` edge.

### 10. Date / MRO inferred packages

Pages with no WO but same MRO and date within ±3 days → inferred `work_packages` row, `inferred = 1`, `confidence = medium`. These are speculation; use them only as Phase 7 hints, not as primary evidence.

### 11. WO chain edges (with administrative cap)

For each work order with `2 ≤ component_count ≤ 8`, create pairwise `wo_chain` rows in `asset_relations` between every pair of components touched by that WO. Label = the dominant `event_type` across the WO's events.

**For WOs with `component_count > 8`, set `is_administrative = 1` and SKIP the wo_chain edges entirely.** These are dossier-level admin documents (inspection sweeps, bulk preservation packages); 50×49/2 = 1225 edges per such WO would explode the visualiser.

### 12. Parent-of relationships

Infer `parent_of` rows in `asset_relations`:
- LLP → engine module (when LLP appears in an engine assembly record with a parent module SN)
- Module → engine (when module SN appears in the engine's shop visit assembly listing)
- Blade → propeller hub (when a blade SN appears in a propeller assembly record)
- Subcomponent → MLG/NLG assembly

Each `parent_of` row needs `evidence_file`, `evidence_page`, `evidence_quote` (NOT NULL).

### 13. Replaced-by relationships

When the same `position` (LH/RH/NLG/etc.) sees a `component_removal` event followed by a `component_installation` event of the same component class, create a `replaced_by` row from removed → installed, with `valid_from = removal_date` on the new component, `valid_to = removal_date` on the old one.

### 14. Installed-on relationships

For every component currently on the asset (most recent installation event with no subsequent removal), create an `installed_on` row from component → asset with `valid_from = installation_date`.

---

## MANDATORY VERIFICATION

```sql
-- Counts
SELECT 'edges_total' AS t, COUNT(*) FROM edges
UNION ALL SELECT 'work_orders',     COUNT(*) FROM work_orders
UNION ALL SELECT 'requirements',    COUNT(*) FROM requirements
UNION ALL SELECT 'stakeholders',    COUNT(*) FROM stakeholders
UNION ALL SELECT 'persons',         COUNT(*) FROM persons
UNION ALL SELECT 'ata_chapters',    COUNT(*) FROM ata_chapters
UNION ALL SELECT 'asset_relations', COUNT(*) FROM asset_relations;

-- Variety: distinct edge_types and relation_types must be >= the thresholds below.
SELECT COUNT(DISTINCT edge_type)     AS distinct_edge_types     FROM edges;
SELECT COUNT(DISTINCT relation_type) AS distinct_relation_types FROM asset_relations;

-- Per-type breakdown — print the full distribution to progress.log.
SELECT edge_type, COUNT(*) FROM edges GROUP BY edge_type ORDER BY 2 DESC;
SELECT relation_type, COUNT(*) FROM asset_relations GROUP BY relation_type;

-- Specific connector probes (these are the ones that make the graph a graph):
SELECT 'PART_OF_WORK_ORDER' AS k, COUNT(*) FROM edges WHERE edge_type='PART_OF_WORK_ORDER'
UNION ALL SELECT 'PAGE_REFERENCES',  COUNT(*) FROM edges WHERE edge_type='PAGE_REFERENCES'
UNION ALL SELECT 'ASSIGNED_ATA',     COUNT(*) FROM edges WHERE edge_type='ASSIGNED_ATA'
UNION ALL SELECT 'COVERS_REQUIREMENT', COUNT(*) FROM edges WHERE edge_type='COVERS_REQUIREMENT'
UNION ALL SELECT 'SIGNED_BY',        COUNT(*) FROM edges WHERE edge_type='SIGNED_BY'
UNION ALL SELECT 'STAMP_BINDS_TO',   COUNT(*) FROM edges WHERE edge_type='STAMP_BINDS_TO'
UNION ALL SELECT 'INSTALLATION',     COUNT(*) FROM edges WHERE edge_type='INSTALLATION'
UNION ALL SELECT 'REMOVAL',          COUNT(*) FROM edges WHERE edge_type='REMOVAL'
UNION ALL SELECT 'PART_REPLACED',    COUNT(*) FROM edges WHERE edge_type='PART_REPLACED'
UNION ALL SELECT 'parent_of',        COUNT(*) FROM asset_relations WHERE relation_type='parent_of'
UNION ALL SELECT 'replaced_by',      COUNT(*) FROM asset_relations WHERE relation_type='replaced_by'
UNION ALL SELECT 'wo_chain',         COUNT(*) FROM asset_relations WHERE relation_type='wo_chain';

-- Documents must have at least one edge each (admin docs excepted).
SELECT COUNT(*) AS docs_with_zero_edges FROM documents
WHERE id NOT IN (
    SELECT DISTINCT source_id FROM edges WHERE source_kind = 'DOCUMENT'
    UNION SELECT DISTINCT target_id FROM edges WHERE target_kind = 'DOCUMENT'
);

-- Stamps must be bound (every stamp from Phase 1 should produce STAMP_BINDS_TO).
SELECT COUNT(*) AS stamps_unbound FROM stamps
WHERE id NOT IN (SELECT source_id FROM edges WHERE edge_type='STAMP_BINDS_TO');
```

Append all of the above to `progress.log`.

```
THRESHOLDS (a real Phase 6 must clear ALL of these):
- distinct_edge_types                 : >= 6
  (You should have built: PART_OF_WORK_ORDER, PAGE_REFERENCES, ASSIGNED_ATA,
   plus at least three of: COVERS_REQUIREMENT, SIGNED_BY, STAMP_BINDS_TO,
   ISSUED_BY, APPROVED_UNDER, INSTALLATION, REMOVAL, PART_REPLACED, ATTACHES.)
- distinct_relation_types             : >= 2
  (At minimum: installed_on AND replaced_by. parent_of and wo_chain when
   the dossier has the underlying data.)
- count(edges WHERE edge_type='PART_OF_WORK_ORDER') : > 0 if any pages have
                                                       work_order references in metadata
- count(edges WHERE edge_type='ASSIGNED_ATA')        : > 0 if components have ata_chapter populated
- count(edges WHERE edge_type='STAMP_BINDS_TO')      : >= 0.5 * count(stamps)
- count(requirements)                                : > 0 if dossier mentions any AD/SB/EO/STC
- docs_with_zero_edges                               : ~ 0 (a few admin docs are fine)
- stamps_unbound                                     : ~ 0 (most stamps must bind to something)
- count(edges WHERE evidence_file = '' OR evidence_file IS NULL)  : 0
- count(asset_relations WHERE evidence_quote = '' OR evidence_quote IS NULL) : 0
```

**STOP conditions** (a graph that fails ANY of these is a list, not a graph):

- `count(edges) == 0`.
- `distinct_edge_types < 6`. The list-vs-graph baseline.
- `distinct_relation_types < 2`. Same cheat with `asset_relations`.
- `count(work_orders) == 0` AND the dossier has any pages whose `reference_numbers` include a `work_order` type.
- `count(requirements) == 0` AND any page has a non-empty `regulatory_references` array.
- `stamps_unbound > 0.5 * count(stamps)`. Means you skipped the stamp binding loop.
- More than 30% of `documents` have zero edges.
- Any row in `asset_relations` or `edges` has a NULL/empty `evidence_*` column. Golden-rule violation.

**ANTI-STUB GATES — each non-membership edge type must have a real count, not 1:**

A previous run cleared `distinct_edge_types >= 6` by inserting **exactly one row** of each edge type. That's not a graph; it's a tasting menu. The gates below kill that pattern.

```sql
-- Edges that point to nodes which don't exist (broken FK, classic stub)
SELECT 'orphan_assigned_ata' AS k, COUNT(*) FROM edges
   WHERE edge_type='ASSIGNED_ATA' AND target_id NOT IN (SELECT id FROM ata_chapters)
UNION ALL SELECT 'orphan_stamp_binds', COUNT(*) FROM edges
   WHERE edge_type='STAMP_BINDS_TO' AND source_id NOT IN (SELECT id FROM stamps)
UNION ALL SELECT 'orphan_signed_by',   COUNT(*) FROM edges
   WHERE edge_type='SIGNED_BY' AND target_id NOT IN (SELECT id FROM persons)
UNION ALL SELECT 'orphan_issued_by',   COUNT(*) FROM edges
   WHERE edge_type='ISSUED_BY' AND target_id NOT IN (SELECT id FROM stakeholders)
UNION ALL SELECT 'orphan_covers_req',  COUNT(*) FROM edges
   WHERE edge_type='COVERS_REQUIREMENT' AND target_id NOT IN (SELECT id FROM requirements);
-- Each must be 0.

-- Each edge type's count must be at least proportional to the underlying data.
SELECT 'pf_PART_OF_WORK_ORDER' AS k, COUNT(*) FROM edges WHERE edge_type='PART_OF_WORK_ORDER'
UNION ALL SELECT 'pf_PAGE_REFERENCES',  COUNT(*) FROM edges WHERE edge_type='PAGE_REFERENCES'
UNION ALL SELECT 'pf_ASSIGNED_ATA',     COUNT(*) FROM edges WHERE edge_type='ASSIGNED_ATA'
UNION ALL SELECT 'pf_COVERS_REQUIREMENT', COUNT(*) FROM edges WHERE edge_type='COVERS_REQUIREMENT'
UNION ALL SELECT 'pf_STAMP_BINDS_TO',    COUNT(*) FROM edges WHERE edge_type='STAMP_BINDS_TO'
UNION ALL SELECT 'pf_SIGNED_BY',         COUNT(*) FROM edges WHERE edge_type='SIGNED_BY'
UNION ALL SELECT 'pf_ISSUED_BY',         COUNT(*) FROM edges WHERE edge_type='ISSUED_BY';
```

```
- Every "orphan_*" count                                : MUST be 0.
                                                          (You inserted edges to nodes that don't exist.)

- count(edges WHERE edge_type='ASSIGNED_ATA')           : >= count(ata_chapters)
                                                          (At least one ASSIGNED_ATA per chapter you bothered to insert.)
- count(edges WHERE edge_type='PART_OF_WORK_ORDER')     : >= count(work_orders) * 3
                                                          (Each WO has multiple pages.)
- count(edges WHERE edge_type='STAMP_BINDS_TO')         : >= 0.5 * count(stamps)
                                                          (Most stamps must bind to something.)
- count(edges WHERE edge_type='COVERS_REQUIREMENT')     : >= count(requirements)
                                                          (Each requirement must be covered by at least one event.)
- count(edges WHERE edge_type='ISSUED_BY')              : >= count(stakeholders) WHERE kind='MRO'
- count(edges WHERE edge_type='PAGE_REFERENCES')        : >= 100 for any non-trivial dossier
                                                          (PN/SN/ATA/requirement references — if PAGE_REFERENCES is < 100,
                                                           you skipped the per-page reference indexing.)
- ANY edge_type with count exactly == 1                 : suspicious. Each edge type either has zero
                                                          rows (you didn't run that connector) or has
                                                          many rows (you ran it). Exactly 1 means stub.
                                                          List those edge_types in progress.log.
```

Stub-detection check:

```sql
SELECT edge_type, COUNT(*) AS n FROM edges
GROUP BY edge_type
HAVING n = 1;
-- If this returns ANY rows, those edge types are stubs. STOP.
```

**Exception:** if the dossier genuinely has only 1 work order, only 1 stakeholder, etc., the corresponding edge type may legitimately have 1 row. Document this in progress.log with the underlying count from the source table. If `count(work_orders) == 1` AND `count(edges WHERE edge_type='PART_OF_WORK_ORDER') == 1`, that's still suspicious — a real work order touches many pages.

If you fail any of these gates, do NOT proceed. Re-read the corresponding subsection (1-14) of this file and actually run that step against the dossier.
