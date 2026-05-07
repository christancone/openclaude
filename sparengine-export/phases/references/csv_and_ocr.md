# CSV SCHEMA + `extracted_json` STRUCTURE

Reference file. Load when running phases 1, 4, 5, 6.

---

## CSV SCHEMA (input — same shape for every dossier)

One row = one PDF page. Key fields:

```
id                  - chunk UUID (unique page identifier)
document_id         - groups pages from the same PDF document
page_index          - 0-based page number within the PDF
original_path       - relative path including folder structure (POSIX-ish; treat as opaque string for grouping)
file_name           - PDF file name (your primary source citation)
extracted_json      - JSON string produced by the OCR pass (see structure below)
enhanced_s3_key     - S3 path to the page image (for vision calls)
asset_id            - asset UUID (all rows share the same value)
chunks              - JSON array of text chunks with embeddings (legacy retrieval surface)
```

`original_path` may use any path separator. Normalise with `Path(original_path).as_posix()` and split on `/`. Do not interpret it as a real filesystem path.

---

## `extracted_json` — top-level shape

```json
{
  "page_index": 0,
  "is_blank": false,
  "is_template_empty": false,
  "rotation_hint": 0,
  "content": { ... }
}
```

- `is_blank: true` → empty page. Skip entity hydration; still index as a document boundary.
- `is_template_empty: true` → printed form with no filled values. Skip entity hydration; still index.
- `rotation_hint` ∈ {0, 90, 180, 270} → page rotation. If non-zero AND derived data looks suspicious, queue a vision re-read against `enhanced_s3_key`.

---

## `content` object — what's inside

```
content.document_type           - CLOSED ENUM (see document_types.md)
content.evidentiary_weight      - "primary" | "secondary" | "administrative" | "reference"
content.title                   - document title (or null)
content.header_fields           - flat dict of label → value (only fields that have values)
content.sections                - typed content blocks (see Section Types below)
content.tables                  - structured tables: name, headers, rows
content.stamps_and_signatures   - approval evidence WITH spatial bindings to entities
content.entities                - canonical entity list with per-entity confidence
content.events                  - maintenance events WITH task_compliance_status
content.metadata                - identifiers and flags for indexing
```

---

## `content.entities[]` — the canonical entity list

```json
{
  "entity_id": "ent_1",
  "entity_type": "part_number" | "serial_number" | "work_order" | "nrc_number" |
                 "task_card_number" | "ata_chapter" | "registration" | "msn" | "esn" |
                 "operator" | "mro" | "person" | "date" | "tsn" | "csn" | "tso" | "cso" |
                 "tbo" | "life_limit" | "certificate_number" | "approval_number" |
                 "batch_number" | "sb_number" | "ad_number" | "other",
  "value": "string",
  "confidence": "high" | "medium" | "low",
  "confidence_reason": "short explanation",
  "source": "printed" | "handwritten" | "stamp" | "mixed",
  "location_context": "where on the page"
}
```

**This is the canonical surface for the graph.** Hydrate `part_types`, `serials`, `work_orders`, `requirements`, `persons`, and `stakeholders` from `entities[]` — not from re-scanning text. Carry per-entity `confidence` through to derived edges.

---

## `content.events[]` — pre-extracted maintenance actions

```json
{
  "event_id": "evt_1",
  "event_type": "task_performed" | "inspection" | "component_installation" |
                "component_removal" | "sb_compliance" | "ad_compliance" | "modification" |
                "repair" | "shop_visit" | "release_to_service" | "other",
  "description": "string",
  "task_reference": "task card / SB / AD / EO number or null",
  "task_compliance_status": "signed_off" | "listed_but_not_signed" |
                            "marked_not_required" | "deferred" | "ambiguous",
  "compliance_status_reason": "short explanation",
  "date": "YYYY-MM-DD or null",
  "bound_entities": [
    { "entity_id": "ent_1", "role": "subject" | "tool" | "part_installed" |
                                    "part_removed" | "performer" | "authorizer" | "location" }
  ],
  "bound_stamps": ["stamp_1", "stamp_2"]
}
```

**Critical: `task_compliance_status` is already evaluated by the OCR. Do NOT re-derive from text.** If status is `listed_but_not_signed` or `ambiguous`, that becomes a `TASK_NOT_CONFIRMED` finding.

---

## `content.stamps_and_signatures[]` — approval evidence with spatial binding

```json
{
  "stamp_id": "stamp_1",
  "type": "stamp" | "signature" | "initials" | "approval_mark" | "date_stamp",
  "text": "string or null",
  "person_name": "string or null",
  "title_role": "string or null",
  "date": "YYYY-MM-DD or null",
  "certificate_number": "string or null",
  "location_context": "where on the page",
  "binds_to": {
    "target_type": "entity" | "event" | "table_row" | "section" | "page",
    "target_ref": "entity_id / event_id / row reference / section heading",
    "binding_confidence": "high" | "medium" | "ambiguous",
    "binding_reason": "short explanation"
  }
}
```

The OCR already bound stamps to the things they apply to. Use `binds_to.target_ref` directly to create graph edges. If `binding_confidence == "ambiguous"`, raise a `STAMP_AMBIGUOUS_BINDING` finding.

---

## `content.metadata` — indexing surface

```json
{
  "document_type": "same as content.document_type",
  "is_mis_export": false,
  "mis_system": "CAMP" | "AMOS" | "SAP" | "custom" | "unknown" | null,
  "serial_number": "primary SN for this page (if any)",
  "part_numbers": ["...", "..."],
  "dates": ["YYYY-MM-DD", "..."],
  "reference_numbers": [{ "type": "work_order" | "nrc_number" | "...", "value": "..." }],
  "ata_chapters": ["72", "79-21-13"],
  "regulatory_references": ["Part-145", "CAR 571"],
  "context_discrepancy": "string or null"
}
```

`metadata.reference_numbers` is the primary connector source — every typed reference becomes an edge. `is_mis_export == true` flags the page as MIS hypothesis (lower confidence than primary physical records). `context_discrepancy` becomes a `CONTEXT_DISCREPANCY` finding.

---

## `content.sections[]` — typed content blocks

```
text                    - prose; store as page text
form_fields             - extra label→value pairs beyond header_fields; merge into headers
handwritten             - handwritten content; preserve verbatim, lower confidence on derived edges
work_description        - "what needs to be done"; attach to parent event as description
corrective_action       - "what was actually done"; attach to parent event; promotes
                          a defect_entry to a fully-resolved event
certification_statement - emit one EVENT (event_type: release_to_service); bound stamp
                          becomes signing person; approval_number becomes a requirement edge
address_block           - extract organisation; create or link a STAKEHOLDER node
list                    - usually a task list inside a work package
defect_entry            - emit one EVENT pair: a defect (inspection) + a corrective_action
                          (task_performed); link both to the same work order
inspection_finding      - emit one EVENT (event_type: inspection)
```

---

## `content.tables[]` — structured tables

```json
{
  "name": "Parts Installed",
  "headers": ["P/N", "Description", "S/N Off", "S/N On", "Qty", "Batch"],
  "rows": [["350A32-0110", "...", "MN738", "MN742", "1", "B-2024-018"]]
}
```

The most important table types and what they generate:

- **Parts tables** (`P/N`, `S/N Off`, `S/N On`, `Qty`, `Batch`) → one `component_removal` event per `S/N Off` and one `component_installation` event per `S/N On`. Hydrate `part_types`, `serials`. Emit `PART_REPLACED` edge from off-serial to on-serial.
- **LLP tables** (`P/N`, `S/N`, `TSN`, `CSN`, `Life Limit`, `Remaining`) → hydrate components with `is_llp=1`, set times and remaining life. Trigger `LLP_LIMIT_CRITICAL` / `LLP_LIMIT_WARNING` per row.
- **SB/AD compliance tables** → hydrate requirements; emit one event per row with proper `task_compliance_status` mapped from the `Status` column.
- **Document control tables** (`Task #`, `Description`, `Raised stamp`, `Cleared stamp`) → cross-check that every listed task has a corresponding event in `events[]`; flag listed-but-uncovered tasks.
- **Flight data / work history tables** → asset-level events; link by date and TSN/CSN.

---

## Conflict resolution (`evidentiary_weight`)

When the same fact (e.g. a component's TSN at a given date) is asserted by multiple pages:

1. **Primary > Secondary > Reference > Administrative**
2. Within the same weight: **physical signed record > MIS export** (use `metadata.is_mis_export`)
3. Within the same weight and source kind: **most recent date** wins
4. Within all of the above: **highest entity confidence** wins

Always store the chosen value WITH the source page reference and a `_conflict` array recording every alternative seen and where.

---

## Practical reading rule

When in doubt about a structure, log a sample row's parsed `extracted_json` and inspect it. **Do not assume.** Different OCR versions emit slightly different shapes; defensive `.get(key, default)` with logging when keys are missing beats KeyError after 30 minutes of indexing.
