# SPARENGINE — KNOWLEDGE GRAPH BUILDER MISSION BRIEF
# Generic instruction for Claude Code
# Target: build a fully-connected `graph.db` and `asset_graph.html` from ANY aviation dossier
# Input: CSV produced by the Sparengine OCR pipeline (one row per PDF page, structured `extracted_json`)

---

## WHAT YOU ARE

You are an aviation records intelligence system. You read **already-structured** OCR output from a CSV (one row per PDF page), assemble it into a single connected knowledge graph in SQLite, and render that graph as an interactive HTML visualisation.

You are NOT doing OCR. You are NOT re-extracting entities from raw page text. The upstream OCR pass already extracts entities, events, stamps with spatial bindings, and document type per page — your job is to **trust that work, hydrate it into a graph, resolve cross-page identities, build connectors, and detect gaps**.

You are NOT a chatbot. You are an aviation records analyst that understands the difference between a Form 1, a CRS, an SB compliance record, an AD compliance record, a logbook entry, and a job card — and you know how each connects to the others.

The final outputs are:

1. `graph.db` — a SQLite knowledge graph that is the truth memory of the dossier. Every fact links back to a source page.
2. `graph_export.json` — the graph projected for the visualiser.
3. `asset_graph.html` — a self-contained interactive visualisation of the graph (vis-network), filterable by tier, status, edge type, severity, and view mode (Simple / Detailed / Documents / ATA).

The dossier may cover ANY aviation asset: fixed-wing jets (Boeing, Airbus, Bombardier, Embraer, Gulfstream, Cessna), turboprops (ATR, Dash-8, King Air), helicopters (Bell, Sikorsky, Airbus Helicopters, Leonardo, Robinson), piston aircraft, or component-only dossiers (engine, propeller, landing gear assembly, APU, gearbox, rotor head as a standalone tradeable unit). The graph schema accommodates all of these without modification.

---

## THE 6-LAYER ONION (the mental model for the graph)

Every dossier — aircraft or engine, jet or helicopter, full-life or end-of-lease — maps onto the same six-layer structure. Every node in the graph belongs to exactly one layer; every edge crosses at most one layer boundary. Once you internalise this, the rest of the schema becomes obvious.

```
┌─────────────────────────────────────────────────────────┐
│ Layer 0:  ASSET PROFILE     (1 node — the asset itself) │
├─────────────────────────────────────────────────────────┤
│ Layer 1:  TIER GROUPS       (3-7 nodes — top systems)   │
├─────────────────────────────────────────────────────────┤
│ Layer 2:  ATA CHAPTERS      (n nodes — regulatory grid) │
├─────────────────────────────────────────────────────────┤
│ Layer 3:  COMPONENTS        (n00s — PN×SN unique pairs) │
├─────────────────────────────────────────────────────────┤
│ Layer 4:  EVENTS            (n000s — installs, OH, SBs) │
├─────────────────────────────────────────────────────────┤
│ Layer 5:  DOCUMENTS         (n00s — every PDF in dossier)│
├─────────────────────────────────────────────────────────┤
│ Layer 6:  FINDINGS          (overlay — open/closed gaps)│
└─────────────────────────────────────────────────────────┘
```

**Layer rules:**

- **Layer 0 (Asset)** — exactly one node. Discovered in Phase 0 (asset orientation), never assumed from a config file. Drives every later phase.
- **Layer 1 (Tier groups)** — 3-7 nodes, pre-defined per asset class (see Universal Tier Structure below). Not discovered; selected from the asset_class.
- **Layer 2 (ATA chapters)** — independent classification, not a parent of components. Always emit ATA nodes; they unlock alternate views and SB/AD reconciliation.
- **Layer 3 (Components)** — every `(canonical_pn, installed_sn)` pair that survives the 8 selection rules (see Component Discovery below).
- **Layer 4 (Events)** — every discrete maintenance action with closed-set `event_type`. Each event carries `(file_name, page_index, text_evidence)` — non-negotiable.
- **Layer 5 (Documents)** — every PDF in the dossier. Documents are the citation backbone; every fact in any other layer must trace back here.
- **Layer 6 (Findings)** — an overlay, not a separate graph. Every finding anchors to a Layer 3 component (or Layer 0 asset) and points to Layer 5 evidence.

**The single golden rule:** every node, every edge, every fact in this graph must be traceable to `(file_name, page_index, verbatim_quote)`. The schema enforces this with `NOT NULL` on evidence columns. The agent prompts enforce it by refusing to write findings without a source. The verifier enforces it by refusing to close findings without resolution evidence.

When the golden rule is enforced top to bottom, the graph is audit-grade. When it slips anywhere, the graph fills with hallucinations. The original ATR72 run proved both directions of this — 523 raw findings (rule slipped in Phase 3) collapsed to 58 (rule enforced in Phase 6 verification).

---

## CROSS-PLATFORM PATH RULES — DO NOT HARDCODE

This system runs on Windows, macOS, and Ubuntu. Every path must work on every OS.

**Rules:**

1. **Never hardcode absolute paths.** No `C:\Users\...`, no `/home/...`, no `/Users/...` anywhere in the code.
2. **All inputs and outputs are relative to a `--workdir` argument** passed at runtime. Default `--workdir` is the current working directory.
3. **Use `pathlib.Path` everywhere.** Never use string concatenation or `os.path.join` with hardcoded separators. Never use raw backslashes in path literals.
4. **Path inputs accept either form.** If a user passes `data\dossier.csv` on Windows or `data/dossier.csv` on Mac/Linux, `Path()` handles both.
5. **All file URLs in the HTML use forward slashes only.** vis-network and the browser are slash-agnostic only when slashes are forward.
6. **Do not assume case sensitivity.** macOS HFS+ is case-insensitive by default; Linux is case-sensitive; Windows is case-insensitive. When matching `file_name` strings across CSV rows, normalise to the exact case found in the CSV — do not lowercase or uppercase.

**Standard runtime invocation:**

```
python main.py --csv ./input/dossier.csv --workdir ./run
```

Or, when the CSV is inside the workdir:

```
python main.py --workdir ./run --csv-name dossier.csv
```

The `--workdir` is the root for everything: `graph.db`, `graph_export.json`, `asset_graph.html`, intermediate files, logs.

**Reference path layout in code (always relative to `workdir`):**

```python
from pathlib import Path

workdir = Path(args.workdir).resolve()
csv_path = Path(args.csv).resolve() if args.csv else workdir / args.csv_name

db_path        = workdir / "graph.db"
export_path    = workdir / "graph_export.json"
html_path      = workdir / "asset_graph.html"
log_path       = workdir / "progress.log"
checkpoint_dir = workdir / "_checkpoints"
```

That's the only path discipline you need. Never deviate.

---

## CSV SCHEMA (unchanged across assets)

One row = one PDF page. Key fields:

```
id                  - chunk UUID (unique page identifier)
document_id         - groups pages from the same PDF document
page_index          - 0-based page number within the PDF
original_path       - relative path including folder structure (POSIX-style; treat as opaque string for grouping)
file_name           - PDF file name (your primary source citation)
extracted_json      - JSON string produced by the OCR pass — STRUCTURED, see next section
enhanced_s3_key     - S3 path to the page image (for vision calls)
asset_id            - asset UUID (all rows share the same value)
chunks              - JSON array of text chunks with embeddings (legacy retrieval surface)
```

`original_path` may use any path separator depending on what produced the CSV. When parsing it for folder-section context, normalise with `Path(original_path).as_posix()` and split on `/`. Do not interpret it as a real filesystem path.

---

## WHAT THE OCR ALREADY GIVES YOU — `extracted_json` STRUCTURE

This is the most important section of this brief. The OCR pass produces a rich, structured JSON per page with stable IDs, confidence scores, and spatial bindings. **Read this fully before designing the graph hydration logic.** Do not re-extract things the OCR has already extracted.

### Top-level shape

```json
{
  "page_index": 0,
  "is_blank": false,
  "is_template_empty": false,
  "rotation_hint": 0,
  "content": { ... }
}
```

- `is_blank: true` → completely empty page. Skip entity hydration; still index as a document boundary.
- `is_template_empty: true` → printed form with no filled values. Skip entity hydration; still index.
- `rotation_hint` ∈ {0, 90, 180, 270} → page rotation. If non-zero AND downstream extraction looks suspicious, trigger a vision re-read against `enhanced_s3_key`.

### `content` object — what's inside

```
content.document_type           - CLOSED ENUM (see below) — already classified by OCR
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

### `content.entities[]` — the canonical entity list

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

**This is the canonical surface for the graph.** Hydrate `part_types`, `serials`, `work_orders`, `requirements`, `persons`, and `stakeholders` from `entities[]` — not from re-scanning text. Carry the per-entity `confidence` through to the corresponding edge in the graph.

### `content.events[]` — pre-extracted maintenance actions

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

**Critical: `task_compliance_status` is already evaluated by the OCR.** Do NOT re-derive from text. The OCR knows the difference between "task listed" and "task signed off". Trust it. If the status is `listed_but_not_signed` or `ambiguous`, that becomes a `TASK_NOT_CONFIRMED` finding on the resulting event.

### `content.stamps_and_signatures[]` — approval evidence with spatial binding

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

**The OCR has already done spatial reasoning to bind stamps to the things they apply to.** Use `binds_to.target_ref` directly to create graph edges. If `binding_confidence == "ambiguous"`, raise a `STAMP_AMBIGUOUS_BINDING` finding.

### `content.metadata` — indexing surface

```json
{
  "document_type": "same as content.document_type",
  "document_type_reason": "required if document_type is 'other'",
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

`metadata.reference_numbers` is the primary connector source — every typed reference becomes an edge in the graph. `metadata.is_mis_export == true` flags the page as MIS hypothesis (lower confidence than primary physical records). `metadata.context_discrepancy` becomes a `CONTEXT_DISCREPANCY` finding.

### `content.sections[]` — typed content blocks

Section types and how to use them:

```
text                    - prose; store as page text
form_fields             - extra label→value pairs beyond header_fields; merge into headers
handwritten             - handwritten content; preserve verbatim, lower confidence on derived edges
work_description        - "what needs to be done"; attach to the parent event as description
corrective_action       - "what was actually done"; attach to the parent event; promotes
                          a defect_entry to a fully-resolved event
certification_statement - emit one EVENT (event_type: release_to_service); the bound stamp
                          becomes the signing person; approval_number becomes a requirement edge
address_block           - extract organisation; create or link a STAKEHOLDER node
list                    - usually a task list inside a work package; rarely emits events directly
defect_entry            - emit one EVENT pair: a defect (inspection) + a corrective_action
                          (task_performed); link both to the same work order
inspection_finding      - emit one EVENT (event_type: inspection)
```

### `content.tables[]` — structured tables

```json
{
  "name": "Parts Installed",
  "headers": ["P/N", "Description", "S/N Off", "S/N On", "Qty", "Batch"],
  "rows": [["350A32-0110", "...", "MN738", "MN742", "1", "B-2024-018"]]
}
```

The most important table types and what they generate:

- **Parts tables** (`P/N`, `S/N Off`, `S/N On`, `Qty`, `Batch`) → emit one `component_removal` event per `S/N Off` and one `component_installation` event per `S/N On`. Hydrate `part_types`, `serials`. Emit a `PART_REPLACED` edge from the off-serial to the on-serial.
- **LLP tables** (`P/N`, `S/N`, `TSN`, `CSN`, `Life Limit`, `Remaining`) → hydrate components with `is_llp=1`, set times and remaining life. Trigger `LLP_LIMIT_CRITICAL` / `LLP_LIMIT_WARNING` per row.
- **SB/AD compliance tables** → hydrate requirements; emit one event per row with proper `task_compliance_status` mapped from the `Status` column.
- **Document control tables** (`Task #`, `Description`, `Raised stamp`, `Cleared stamp`) → cross-check that every listed task has a corresponding event in `events[]`; flag listed-but-uncovered tasks.
- **Flight data tables** → asset-level utilisation events (low audit weight, useful for timeline).
- **Work history tables** → asset-level events; link by date and TSN/CSN.

### `content.evidentiary_weight` — conflict resolution rule

When the same fact (e.g. a component's TSN at a given date) is asserted by multiple pages, resolve by:

1. **Primary > Secondary > Reference > Administrative**
2. Within the same weight: **physical signed record > MIS export** (use `metadata.is_mis_export`)
3. Within the same weight and source kind: **most recent date** wins
4. Within all of the above: **highest entity confidence** wins

Always store the chosen value WITH the source page reference and a `_conflict` array recording every alternative value seen and where.

---

## DOCUMENT TYPE — CLOSED ENUM (use the OCR's exact strings)

The OCR uses a fixed enum. The graph stores these exact strings. Do not invent new ones. Do not lowercase or alias them.

```
Cover/Admin (administrative weight):
  workpack_cover_sheet, table_of_contents, document_control_list

Work Authorisation (secondary):
  maintenance_work_authorisation, work_order_contents_report, work_scope

Defect & Findings (secondary):
  defects_reconciled_summary, non_routine_card, routine_task_card,
  mis_task_card, mel_entry

Inspection & Reports (primary or secondary):
  inspection_report, borescope_inspection_report, condition_report,
  shop_visit_report, test_report, dent_and_buckle_chart

Certificates & Release (primary):
  easa_form_one, faa_form_8130, tcca_form_one, certificate_of_release_to_service,
  dual_release_certificate, certificate_of_airworthiness, certificate_of_registration,
  airworthiness_review_certificate

Component Records (primary or secondary):
  access_panel_chart, parts_identification_tag, component_history_card,
  component_logbook, life_limited_parts_status, engine_llp_status_sheet,
  structural_repair_report

Operational (primary):
  technical_journey_log, flight_log, engine_logbook, airframe_logbook,
  weight_and_balance_report

Engineering & Modifications (primary):
  engineering_order, service_bulletin_compliance, airworthiness_directive_compliance,
  sb_status_report, ad_status_report, modification_record,
  supplemental_type_certificate, afm_supplement

Transaction / Lease (reference):
  redelivery_condition_report, delivery_acceptance_certificate,
  purchase_order, invoice, quotation

MIS / System Exports (reference — treat as hypothesis):
  mis_export

Other:
  shipping_record, correspondence, other
```

When reasoning about coverage (e.g. "is there a CRS for this work order?") use the document_type field directly. Do not pattern-match titles.

---

## ASSET TYPE DETECTION (Phase 2)

Detect the asset kind from `entities[]` aggregated across all pages. The signals:

```
AIRCRAFT (full):
  signals    - registration mark entities, msn entities, type designation in headers,
               full ATA chapter spread (ATA 21..80), multiple logbook types
               (airframe + engine + propeller / rotor)
  asset_kind - AIRCRAFT
  subtype    - FIXED_WING_JET | FIXED_WING_TURBOPROP | FIXED_WING_PISTON | HELICOPTER

ENGINE-only:
  signals    - dominant esn entity, engine model in titles, document_type frequencies
               dominated by easa_form_one / shop_visit_report / engine_llp_status_sheet /
               engine_logbook, no airframe_logbook, no registration entity
  asset_kind - ENGINE
  subtype    - TURBOFAN | TURBOJET | TURBOPROP | TURBOSHAFT | PISTON

PROPELLER-only:
  signals    - propeller model dominant, blade SNs, governor records, no engine_logbook,
               no airframe records
  asset_kind - PROPELLER

LANDING_GEAR_ASSEMBLY:
  signals    - MLG/NLG part numbers, actuator records, shock strut records,
               no airframe TSN/CSN, position-specific (LH/RH/NLG), no full ATA spread
  asset_kind - LANDING_GEAR_ASSEMBLY

APU-only:
  signals    - APU model, APU logbook, APU shop reports
  asset_kind - APU

ROTOR_SYSTEM / GEARBOX:
  signals    - main rotor head, tail rotor, swashplate, MGB / IGB / TGB
  asset_kind - ROTOR_SYSTEM | GEARBOX

COMPONENT (catch-all):
  signals    - single PN/SN dominates, narrow scope
  asset_kind - COMPONENT
```

Helicopter detection: if any of MGB / IGB / TGB / main rotor / tail rotor entities appear, subtype = HELICOPTER.

Write the detected asset to the `assets` table. If `metadata.context_discrepancy` appeared on any page during OCR, raise it as a `CONTEXT_DISCREPANCY` finding before continuing.

---

## UNIVERSAL TIER STRUCTURE

Every component in the graph belongs to exactly one tier. Tiers are the high-level grouping for visualisation and traversal.

```
AIRCRAFT_CENTER   - the asset node itself (always exactly one per dossier)
ENGINE            - powerplant assemblies, modules, LLPs, accessories
ROTOR_SYSTEM      - helicopter only: main rotor, tail rotor, hubs, blades, swashplates
TRANSMISSION      - helicopter only: MGB, IGB, TGB, drive shafts, freewheel units
PROPELLER         - propellers, hubs, blades, governors, spinners
LANDING_GEAR      - MLG, NLG, actuators, shock struts, wheels, brakes, tires
AIRFRAME          - structural components, control surfaces, doors, dent and buckle scope
AVIONICS          - communication, navigation, surveillance, flight controls electronics
APU               - auxiliary power unit and accessories (only if asset has one)
SYSTEMS           - hydraulic, pneumatic, fuel, electrical, ECS, fire protection, ice and rain
INTERIOR          - cabin, cockpit, emergency equipment, furnishings (only when relevant)
```

For component-only dossiers, set `tier = AIRCRAFT_CENTER` for the root component and use the relevant tier for any subcomponents found in its assembly records.

---

## ATA CHAPTER → TIER MAPPING

```
21 Air conditioning              → SYSTEMS
22 Auto flight                   → AVIONICS
23 Communications                → AVIONICS
24 Electrical power              → SYSTEMS
25 Equipment / furnishings       → INTERIOR
26 Fire protection               → SYSTEMS
27 Flight controls               → AIRFRAME (mechanical) or AVIONICS (FBW)
28 Fuel                          → SYSTEMS
29 Hydraulic power               → SYSTEMS
30 Ice and rain protection       → SYSTEMS
31 Indicating / recording        → AVIONICS
32 Landing gear                  → LANDING_GEAR
33 Lights                        → SYSTEMS
34 Navigation                    → AVIONICS
35 Oxygen                        → SYSTEMS
36 Pneumatic                     → SYSTEMS
38 Water / waste                 → SYSTEMS
45 Central maintenance system    → AVIONICS
49 APU                           → APU
51-57 Structures / doors / fuselage / nacelles / stabilisers / windows / wings → AIRFRAME
61 Propellers                    → PROPELLER
62 Main rotor                    → ROTOR_SYSTEM
63 Main rotor drive              → TRANSMISSION
64 Tail rotor                    → ROTOR_SYSTEM
65 Tail rotor drive              → TRANSMISSION
66 Folding blades / pylon        → ROTOR_SYSTEM
67 Rotors flight control         → ROTOR_SYSTEM
71-80 Power plant / engine       → ENGINE
```

When ATA is missing on a page but the component is clearly in a system, infer the chapter from the description.

---

## THE GRAPH — NODES AND EDGES

### Node kinds

```
ASSET                  - the dossier subject (1 per dossier)
COMPONENT              - any tracked component
EVENT                  - something that happened to a component or to the asset
DOCUMENT               - one PDF file
PAGE                   - one page (atomic evidence unit)
WORK_ORDER             - clusters all docs/events under one WO
WORK_PACKAGE           - logical grouping of WOs for one shop visit / check
REQUIREMENT            - AD, SB, EO, STC, ICA
ATA_CHAPTER            - ATA system grouping
STAKEHOLDER            - operator, owner, lessor, MRO, OEM, regulator
PERSON                 - mechanic, inspector, certifying staff
PART_TYPE              - canonical PN identity (with all alternate PNs)
SERIAL                 - specific physical unit (PN + SN)
FINDING                - audit finding
TIER_GROUP             - virtual visualisation node (ENGINE / LANDING_GEAR / etc.)
```

### Edge types

Every edge: `source_id`, `source_kind`, `target_id`, `target_kind`, `edge_type`, `confidence`, evidence (file_name + page_index + chunk_id + quote).

**Structural:**
```
HAS_TIER, BELONGS_TO_TIER, PART_OF, INSTALLED_ON,
HAS_PART_TYPE, HAS_SERIAL, ASSIGNED_ATA
```

**Event:**
```
EVENT_ON, EVENT_AFFECTED_ASSET,
INSTALLATION, REMOVAL, OVERHAUL, INSPECTION, SHOP_VISIT,
SB_COMPLIANCE, AD_COMPLIANCE, REPAIR, MODIFICATION,
DAMAGE, CHECK, RELEASE_TO_SERVICE, PART_REPLACED
```

**Document linking (the connectors):**
```
HAS_PAGE, PAGE_REFERENCES, EVIDENCES,
ISSUED_BY, SIGNED_BY, APPROVED_UNDER,
PART_OF_PACKAGE, PART_OF_WORK_ORDER, COVERS_REQUIREMENT,
SUPERSEDES, REFERENCES, ATTACHES,
STAMP_BINDS_TO   ← direct mapping from stamps_and_signatures[].binds_to
```

**Audit:**
```
RAISED_AGAINST, CLOSED_BY
```

---

## DOCUMENT CONNECTORS — HOW THE GRAPH IS WIRED

The graph's value comes from connections between documents. Each connector type below maps to a specific OCR field — **do not rebuild these from raw text**.

### 1. Work order number — strongest connector

Source: `metadata.reference_numbers[]` where `type == "work_order"`, plus `entities[]` where `entity_type == "work_order"`.

Build:
```
JOB_CARD ──PART_OF_WORK_ORDER──> WORK_ORDER <──PART_OF_WORK_ORDER── CRS
                                      ^
                                      │ PART_OF_WORK_ORDER
                                      │
                              FORM_1 / FORM_8130
```
Without a CRS in the same WO bundle: any SB/AD compliance event in that bundle gets `SB_WITHOUT_CRS` / `AD_WITHOUT_CRS`. Without a Form 1 referenced from the work package: the part swap gets `FORM1_MISSING`.

### 2. Reference numbers (typed)

Source: `metadata.reference_numbers[]`. Every typed reference (`task_card_number`, `nrc_number`, `tracking_number`, `certificate_number`, `purchase_order`, etc.) becomes a clustering key. Pages sharing a reference get a `REFERENCES` edge between them.

### 3. PN / SN linking

Source: `entities[]` filtered by `entity_type` in `{"part_number", "serial_number"}`.

Cross-page identity resolution:
- Same `part_number` value across pages → same `part_types` node. If alternate vendor/manufacturer PNs are mentioned together (e.g. on a Form 1 where item description shows both), merge into one `part_types` row with both in `alternate_pns`.
- Same (`part_number`, `serial_number`) pair across pages → same `serials` node.
- Same `serial_number` on different `part_number` values → flag `SN_AMBIGUOUS` and require physical verification.

### 4. Requirement linking

Source: `entities[]` where `entity_type` in `{"sb_number", "ad_number"}`, plus `metadata.regulatory_references[]`.

Every reference creates a `requirements` row. Compliance events (`event_type` in `{"sb_compliance", "ad_compliance"}`) get a `COVERS_REQUIREMENT` edge to the requirement.

### 5. Stamp bindings (already done by OCR)

Source: `stamps_and_signatures[].binds_to`.

For each stamp:
```
stamp.binds_to.target_type == "event"     → STAMP_BINDS_TO edge: stamp → event
                                             → SIGNED_BY edge: event → person (from stamp.person_name)
stamp.binds_to.target_type == "entity"    → STAMP_BINDS_TO edge: stamp → entity (PN/SN/WO)
stamp.binds_to.target_type == "table_row" → resolve table row to its event/entity
stamp.binds_to.target_type == "section"   → STAMP_BINDS_TO edge: stamp → section's event
stamp.binds_to.target_type == "page"      → STAMP_BINDS_TO edge: stamp → page (release to service for whole page)
binding_confidence == "ambiguous"         → STAMP_AMBIGUOUS_BINDING finding
```

### 6. ATA chapter linking

Source: `metadata.ata_chapters[]`. Every component → `ASSIGNED_ATA` to its chapter. Every page → `PAGE_REFERENCES` to all ATA chapters listed.

### 7. Stakeholder linking

Source: `entities[]` where `entity_type` in `{"operator", "mro"}`, plus `address_block` sections and `header_fields` keys like "MRO Name", "Bill To", "Approved Under". Create `stakeholders` rows; `ISSUED_BY` and `APPROVED_UNDER` edges from documents.

### 8. Person linking

Source: `entities[]` where `entity_type == "person"`, plus `stamps_and_signatures[].person_name`. Create `persons` rows. `SIGNED_BY` edges from events to persons via stamps.

### 9. Attachment detection

Source: page adjacency within the same `document_id` plus PN/SN/WO match. If a Form 1 page (`document_type` ∈ Form 1 family) directly follows a job card / task card / non-routine card and shares an entity → `ATTACHES` edge.

### 10. Date / MRO clustering for missing WOs

When pages have no `work_order` reference but share the same date (±3 days) and the same MRO stakeholder, group into an inferred `work_packages` row with `confidence = medium`.

### 11. Hours / cycles anchoring

Source: `entities[]` where `entity_type` in `{"tsn", "csn", "tso", "cso"}`. Two events at the same TSN/CSN are about the same moment in the asset's life regardless of dates — useful for resolving date OCR errors.

### 12. Cross-document references

Source: `sections[]` of type `text` and `certification_statement` that name another document number. Capture as `REFERENCES` edges (low/medium confidence, since this is text mention not structured).

---

## SQLITE SCHEMA

Create `graph.db` (path: `workdir / "graph.db"`) with the schema below. Same for every asset.

```sql
-- The asset itself (1 row)
CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    asset_kind TEXT NOT NULL,        -- AIRCRAFT | ENGINE | PROPELLER | LANDING_GEAR_ASSEMBLY |
                                     -- APU | ROTOR_SYSTEM | GEARBOX | COMPONENT
    subtype TEXT,                    -- FIXED_WING_JET | HELICOPTER | TURBOFAN | etc.
    type_designation TEXT,           -- e.g. "ATR72-212A", "PW127M", "Bell 412EP"
    tcds TEXT,                       -- Type Certificate Data Sheet reference (e.g. "EASA.A.084")
    yom INTEGER,                     -- year of manufacture
    msn TEXT,
    registration TEXT,               -- current registration (the latest in history)
    registration_history TEXT,       -- JSON array of { reg, country, from_date, to_date }
    operator TEXT,
    owner TEXT,
    primary_serial TEXT,             -- engine SN, propeller SN, etc. for component dossiers
    state TEXT,                      -- active | preserved | shop_visit | parted_out | lease_return
    tsn REAL, csn INTEGER,
    tsn_confidence TEXT, csn_confidence TEXT,
    dossier_date TEXT,
    profile_json TEXT,               -- the full Phase 0 asset orientation output
    notes TEXT
);

-- All components
CREATE TABLE components (
    id TEXT PRIMARY KEY,             -- "component::{canonical_pn}::{installed_sn}"
    asset_id TEXT,
    canonical_pn TEXT,
    alternate_pns TEXT,              -- JSON array
    installed_sn TEXT,
    description TEXT,
    ata_chapter TEXT,
    tier TEXT,
    position TEXT,                   -- LH | RH | NLG | FWD | AFT | INBOARD | OUTBOARD | etc.
    parent_component_id TEXT,        -- for subcomponents
    status TEXT,                     -- CLOSED | PARTIAL | GAP | INSTALLED_AT_MFG | DISCOVERED
    is_llp INTEGER DEFAULT 0,
    is_overhaul INTEGER DEFAULT 0,
    tsn REAL, tsn_confidence TEXT,
    csn INTEGER, csn_confidence TEXT,
    tso REAL, cso INTEGER,
    limit_cycles INTEGER, limit_hours REAL,
    remaining_cycles INTEGER, remaining_hours REAL,
    last_form1_file TEXT, last_form1_page INTEGER, last_form1_date TEXT,
    last_overhaul_file TEXT, last_overhaul_page INTEGER, last_overhaul_date TEXT,
    last_mro TEXT,
    notes TEXT,
    FOREIGN KEY (asset_id) REFERENCES assets(id),
    FOREIGN KEY (parent_component_id) REFERENCES components(id)
);

-- Every event
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    component_id TEXT,
    asset_id TEXT,
    event_type TEXT,                 -- task_performed | inspection | component_installation |
                                     -- component_removal | sb_compliance | ad_compliance |
                                     -- modification | repair | shop_visit | release_to_service |
                                     -- overhaul | check | damage | other
    task_compliance_status TEXT,     -- signed_off | listed_but_not_signed |
                                     -- marked_not_required | deferred | ambiguous
    compliance_status_reason TEXT,
    event_date TEXT,
    work_order_id TEXT,
    work_package_id TEXT,
    mro TEXT,
    tsn_at_event REAL,
    csn_at_event INTEGER,
    description TEXT,
    task_reference TEXT,
    file_name TEXT NOT NULL,         -- golden rule: every event has a source page
    page_index INTEGER NOT NULL,
    chunk_id TEXT,
    text_evidence TEXT NOT NULL,     -- golden rule: verbatim quote is mandatory
    confidence TEXT,
    evidentiary_weight TEXT,         -- carried from page: primary | secondary | administrative | reference
    FOREIGN KEY (component_id) REFERENCES components(id),
    FOREIGN KEY (asset_id) REFERENCES assets(id)
);

-- Documents (one per PDF)
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    document_type TEXT,              -- exact OCR enum
    evidentiary_weight TEXT,         -- primary | secondary | administrative | reference
    is_mis_export INTEGER DEFAULT 0,
    mis_system TEXT,                 -- CAMP | AMOS | SAP | custom | unknown | NULL
    title TEXT,
    issue_date TEXT,
    issuer TEXT,
    work_order_id TEXT,
    work_package_id TEXT,
    page_count INTEGER,
    original_path TEXT,
    notes TEXT
);

-- Pages (one per CSV row)
CREATE TABLE pages (
    id TEXT PRIMARY KEY,             -- the chunk UUID
    document_id TEXT,
    page_index INTEGER,
    document_type TEXT,
    evidentiary_weight TEXT,
    is_blank INTEGER DEFAULT 0,
    is_template_empty INTEGER DEFAULT 0,
    rotation_hint INTEGER DEFAULT 0,
    is_mis_export INTEGER DEFAULT 0,
    mis_system TEXT,
    title TEXT,
    date TEXT,
    work_order_id TEXT,
    enhanced_s3_key TEXT,
    text_content TEXT,               -- concatenated for FTS
    ata_chapters TEXT,               -- JSON array
    part_numbers TEXT,               -- JSON array
    serial_numbers TEXT,             -- JSON array
    reference_numbers TEXT,          -- JSON array of {type, value}
    regulatory_references TEXT,      -- JSON array
    context_discrepancy TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

-- Stamps (preserved as first-class evidence)
CREATE TABLE stamps (
    id TEXT PRIMARY KEY,             -- "{page_id}::{stamp_id}"
    page_id TEXT,
    stamp_local_id TEXT,             -- the OCR stamp_id within the page
    type TEXT,                       -- stamp | signature | initials | approval_mark | date_stamp
    text TEXT,
    person_name TEXT,
    title_role TEXT,
    date TEXT,
    certificate_number TEXT,
    location_context TEXT,
    binds_to_target_kind TEXT,       -- entity | event | table_row | section | page
    binds_to_target_ref TEXT,
    binding_confidence TEXT,
    binding_reason TEXT,
    FOREIGN KEY (page_id) REFERENCES pages(id)
);

-- Work orders / packages
CREATE TABLE work_orders (
    id TEXT PRIMARY KEY,             -- normalised WO number
    work_package_id TEXT,
    description TEXT,
    open_date TEXT,
    close_date TEXT,
    mro TEXT,
    has_crs INTEGER DEFAULT 0,
    crs_file_name TEXT,
    crs_page_index INTEGER,
    component_count INTEGER DEFAULT 0,  -- how many distinct components this WO touched
    is_administrative INTEGER DEFAULT 0 -- 1 if component_count > 8 (treat as noise, not as a connector)
);

CREATE TABLE work_packages (
    id TEXT PRIMARY KEY,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    mro TEXT,
    asset_id TEXT,
    inferred INTEGER DEFAULT 0,      -- 1 if grouped by date+MRO heuristic
    notes TEXT
);

-- Time-aware directed component relationships
-- (separate from `edges` because these have valid_from/valid_to and represent
-- structural reality across the asset's life — not just a single page reference)
CREATE TABLE asset_relations (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    from_kind TEXT NOT NULL,         -- COMPONENT | ASSET | WORK_ORDER
    to_id TEXT NOT NULL,
    to_kind TEXT NOT NULL,
    relation_type TEXT NOT NULL,     -- parent_of | replaced_by | installed_on |
                                     -- shop_visit_at | wo_chain
    valid_from TEXT,                 -- ISO date when the relation became true
    valid_to TEXT,                   -- ISO date when it ended (NULL = still valid)
    confidence TEXT,
    evidence_file TEXT NOT NULL,     -- enforced — golden rule
    evidence_page INTEGER NOT NULL,
    evidence_chunk_id TEXT,
    evidence_quote TEXT NOT NULL,
    notes TEXT
);

CREATE INDEX idx_relations_from ON asset_relations(from_id, relation_type);
CREATE INDEX idx_relations_to ON asset_relations(to_id, relation_type);
CREATE INDEX idx_relations_valid ON asset_relations(valid_from, valid_to);

-- Regulatory and engineering requirements
CREATE TABLE requirements (
    id TEXT PRIMARY KEY,             -- "{kind}::{number}::{revision}"
    kind TEXT,                       -- AD | SB | EO | STC | ICA | TASK | LIMIT
    number TEXT,
    revision TEXT,
    title TEXT,
    issuer TEXT,                     -- EASA | FAA | TCCA | DGCA | FOCA | CAAC | ANAC | OEM
    applicability TEXT,
    superseded_by TEXT,
    notes TEXT
);

-- Stakeholders
CREATE TABLE stakeholders (
    id TEXT PRIMARY KEY,
    name TEXT,
    kind TEXT,                       -- OPERATOR | OWNER | LESSOR | MRO | OEM | REGULATOR
    country TEXT,
    notes TEXT
);

-- Persons
CREATE TABLE persons (
    id TEXT PRIMARY KEY,
    name TEXT,
    stamp TEXT,
    license_no TEXT,
    role TEXT,
    organisation_id TEXT
);

-- Part types
CREATE TABLE part_types (
    id TEXT PRIMARY KEY,             -- canonical PN
    alternate_pns TEXT,              -- JSON array
    description TEXT,
    ata_chapter TEXT,
    is_llp INTEGER DEFAULT 0,
    is_overhaul INTEGER DEFAULT 0,
    typical_tier TEXT
);

-- Serials
CREATE TABLE serials (
    id TEXT PRIMARY KEY,             -- "{canonical_pn}::{sn}"
    part_type_id TEXT,
    serial_number TEXT,
    component_id TEXT,
    FOREIGN KEY (part_type_id) REFERENCES part_types(id),
    FOREIGN KEY (component_id) REFERENCES components(id)
);

-- ATA chapters
CREATE TABLE ata_chapters (
    id TEXT PRIMARY KEY,             -- e.g. "ATA32"
    chapter_number TEXT,
    title TEXT,
    tier TEXT
);

-- Findings
CREATE TABLE findings (
    id TEXT PRIMARY KEY,
    target_kind TEXT,                -- COMPONENT | EVENT | DOCUMENT | ASSET | REQUIREMENT | STAMP
    target_id TEXT,
    finding_type TEXT,
    severity INTEGER,                -- 1 | 2 | 3 (current; may be downgraded from original_severity)
    original_severity INTEGER,       -- severity before any downgrade rule applied
    severity_downgrade_reason TEXT,  -- e.g. 'lease_return_window' | 'oem_typical_interval'
    description TEXT,
    what_auditor_needs TEXT,
    file_name TEXT,
    page_index INTEGER,
    chunk_id TEXT,
    status TEXT DEFAULT 'open',      -- open | provisional | closed | false_positive
    discipline_complete INTEGER DEFAULT 0,  -- 1 if Investigation Discipline checklist passed
    verification_strategy TEXT,      -- which Phase 7.5 strategy closed it (if any)
    resolution TEXT,
    resolution_file TEXT,
    resolution_page INTEGER,
    resolution_chunk_id TEXT,
    resolution_quote TEXT
);

-- Critical items pre-scan output (Phase 6.5)
CREATE TABLE priority_items (
    id TEXT PRIMARY KEY,
    rank INTEGER,
    component_id TEXT,
    reason TEXT,                     -- e.g. 'first_limited_llp' | 'shop_visit_due_24mo' | 'damage_primary_structure'
    urgency TEXT,                    -- critical | high | medium
    metric REAL,                     -- e.g. remaining_cycles for LLP cases
    evidence_file TEXT,
    evidence_page INTEGER,
    notes TEXT,
    FOREIGN KEY (component_id) REFERENCES components(id)
);

-- Lease return state (Phase 6.5 detection)
CREATE TABLE lease_return_state (
    asset_id TEXT PRIMARY KEY,
    is_lease_return INTEGER DEFAULT 0,
    window_start TEXT,               -- usually dossier_date - 90 days
    window_end TEXT,                 -- dossier_date
    wo_count_in_window INTEGER,
    dummy_tag_count INTEGER,
    notes TEXT,
    FOREIGN KEY (asset_id) REFERENCES assets(id)
);

-- Universal edge table
CREATE TABLE edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    confidence TEXT,
    evidence_file TEXT,
    evidence_page INTEGER,
    evidence_chunk_id TEXT,
    evidence_quote TEXT,
    notes TEXT
);

CREATE INDEX idx_edges_source ON edges(source_id, source_kind);
CREATE INDEX idx_edges_target ON edges(target_id, target_kind);
CREATE INDEX idx_edges_type ON edges(edge_type);
CREATE INDEX idx_pages_doc ON pages(document_id);
CREATE INDEX idx_events_component ON events(component_id);
CREATE INDEX idx_events_wo ON events(work_order_id);
CREATE INDEX idx_components_asset ON components(asset_id);
CREATE INDEX idx_components_tier ON components(tier);
CREATE INDEX idx_stamps_page ON stamps(page_id);

CREATE VIRTUAL TABLE pages_fts USING fts5(
    page_id,
    text_content,
    file_name,
    document_type,
    content='pages',
    content_rowid='rowid'
);
```

---

## EXTRACTION PHASES

Run in order. Each phase can resume from the database state. Use `workdir / "_checkpoints"` for per-phase markers.

### Phase 0 — Asset Orientation (NEW — the foundation of everything)

Before any indexing, before any component discovery, **read a small representative slice of the dossier and produce a structured asset profile**. Every later phase consumes this profile. Without it, the pipeline is guessing what kind of asset it's processing and how to interpret what it finds.

**What to read (≤30 pages, hand-picked):**

1. The first 5 pages of the dossier index / cover sheets — establishes the asset's identity and the dossier's structure.
2. The Type Certificate Data Sheet (TCDS) page if present — anchors the asset to a regulatory baseline.
3. The Certificate of Airworthiness page — confirms registration and operator.
4. The aircraft data plate page if visible — confirms MSN.
5. The most recent airframe / engine logbook summary pages — establishes current TSN/CSN and current state.
6. The first few LLP status sheet pages — establishes which engines / propellers / rotor systems are tracked.
7. Any obvious "redelivery" / "lease return" / "preservation" cover pages — flags state up front.

Use the OCR's `entities[]` and `metadata` from these pages to build the profile. Do not run FTS yet; do not extract components yet.

**Output: `workdir / "asset_profile.json"` — the source of truth for Phases 1-10.**

```json
{
  "asset_class": "fixed_wing | rotorcraft | engine | apu | propeller | landing_gear",
  "subtype": "FIXED_WING_TURBOPROP",
  "type_designation": "ATR72-212A",
  "tcds": "EASA.A.084",
  "yom": 2014,
  "identifier": { "msn": "1191", "esn": null, "primary_serial": null },
  "registration": {
    "current": "PK-GAI",
    "history": [
      { "reg": "F-WWLE", "country": "FR", "from": "2014-08-01", "to": "2014-09-15" },
      { "reg": "PK-GAI", "country": "ID", "from": "2014-09-16", "to": null }
    ]
  },
  "operator": "Garuda Indonesia",
  "operator_country": "ID",
  "primary_mts": "AMOS",
  "expected_tiers": ["ENGINE", "LANDING_GEAR", "PROPELLER", "AIRFRAME", "AVIONICS"],
  "expected_components": {
    "engines": [
      { "model": "PW127M", "position": "LH", "esn": "ED1017" },
      { "model": "PW127M", "position": "RH", "esn": "ED0855" }
    ],
    "propellers": [
      { "model": "568F", "position": "LH" },
      { "model": "568F", "position": "RH" }
    ],
    "apu": null
  },
  "counters": {
    "tsn": 9575, "csn": 8698,
    "as_of_date": "2025-10-21",
    "confidence": "high"
  },
  "state": "lease_return",
  "state_evidence": {
    "wo_cluster_pattern": "WO-419xxx series, 73 WOs in Sep 2025",
    "dummy_tags_count": 18,
    "redelivery_cover_page": "01_EXTERNAL/00_INDEX/Redelivery_Cover.pdf"
  },
  "dossier_date": "2025-10-21",
  "risk_patterns_observed": [
    "lease return work order cluster",
    "DUMMY UNSERVICEABLE on propeller blades",
    "Engine RH installed 5 weeks before dossier date"
  ],
  "blocked_sn_list": ["1191", "PK-GAI", "ED1017", "ED0855", "F-WWLE"]
}
```

**What this profile drives in later phases:**

- **Phase 1 indexing** — the blocklist comes from `blocked_sn_list`; document_type classification trusts the operator's filing convention.
- **Phase 2 asset detection** — confirmed against the profile rather than re-derived. Any contradiction raises `CONTEXT_DISCREPANCY`.
- **Phase 3 tier groups** — exactly the set in `expected_tiers`.
- **Phase 4 component discovery** — seeds from `expected_components`; component selection thresholds (see Component Discovery below) use `asset_class`.
- **Phase 6.5 critical items** — `state == "lease_return"` triggers the lease-return window logic immediately; no detection needed in 6.5.
- **All MTS-related rules** — `primary_mts` field tells the system whether "CAMP" means software or regulatory concept.
- **All severity rules** — the `state` field feeds the severity downgrade rules (lease_return / shop_visit / parted_out states allow downgrades).

**The profile is small (≤2 KB) and human-readable. Print it to the log at the end of Phase 0. If anything looks wrong, fix the profile before running Phase 1 — every later phase reads from it.**

### Phase 1 — Corpus Indexing
Stream the CSV row-by-row. For each row, parse `extracted_json` once. Insert into `pages` (with `is_blank`, `is_template_empty`, `rotation_hint`, `evidentiary_weight`, `is_mis_export`, `mis_system`, `context_discrepancy`, and arrays from `metadata`). Group pages by `document_id` into `documents` (set document-level `evidentiary_weight` as the most common across pages, set `is_mis_export` if any page is MIS). Insert all `stamps_and_signatures[]` into `stamps` table preserving the `binds_to` info verbatim. Build the FTS index on `text_content`.

Apply the `blocked_sn_list` from `asset_profile.json` to the entity extraction — any entity matching the blocklist is dropped at ingest, never written to `pages.serial_numbers`.

Output: every page, document, and stamp is queryable. No graph edges yet.

### Phase 2 — Asset Detection (now: confirmation against profile)
Read `asset_profile.json` and write the `assets` row from it. Then aggregate `entities[]` across all pages and **confirm** each profile field against the corpus. Any field where the corpus contradicts the profile raises a `CONTEXT_DISCREPANCY` finding (severity per the matrix). The profile wins by default unless the corpus has overwhelming evidentiary_weight.

Set `assets.profile_json` to the full Phase 0 output.

### Phase 3 — Tier Group Creation
Insert `TIER_GROUP` virtual nodes for tiers applicable to this asset's `subtype`. Edges: `HAS_TIER` from asset to each tier group.

### Phase 4 — Part Type, Serial, and Component Hydration

This phase produces the Layer 3 component nodes. The 8 selection rules below are **all mandatory**. Skipping any of them causes the failure modes documented in the ATR72 retrospective (false-positive components, missed real components, fan-out from batch certificates, OCR garble noise).

**The 8 component selection rules (apply in order):**

1. **Seed list first** — start by creating component rows for every `expected_components` entry from `asset_profile.json`. Engines, propellers, MGB/IGB/TGB (helicopters), MLG/NLG, APU. These are the anchors; everything else hangs off them. Without seeds, FTS misses them under OCR garble.

2. **PN/SN regex pair extraction** — sweep `entities[]` for `(part_number, serial_number)` co-occurrences. Use OEM-specific canonical patterns where known:
   - P&WC LLPs: `\d{7}[A-Z]?-\d{2}` paired with 9-12 char alphanumeric SN
   - Bell helicopter components: PN `\d{3}-\d{3}-\d{3}-\d{3}`, SN `MN\d{3}` or `[A-Z]{2,3}\d{4,6}`
   - CFM56 LLPs: PN `\d{3}-?\d{4}-?\d{2}`, SN `[A-Z]{2}\d{6}`
   These vary by OEM — add patterns as they're encountered, fall back to generic regex otherwise.

3. **Apply blocked-SN list** — drop any (PN, SN) pair where the SN appears in `asset_profile.blocked_sn_list`. Plus the universal blocklist: year strings 1990..2030, single characters, strings matching document numbers verbatim.

4. **Hit-count threshold by tier** — promote a (PN, SN) pair to a component only if it meets the tier's threshold:
   - `ENGINE`, `LANDING_GEAR`, `PROPELLER`, `ROTOR_SYSTEM`, `TRANSMISSION`, `APU`: ≥1 occurrence (high-value tier — single-occurrence pairs are likely real)
   - `AVIONICS`, `SYSTEMS`, `INTERIOR`: ≥2 occurrences (low-value tier — single-occurrence pairs are likely OCR noise)
   - `AIRFRAME`: ≥2 occurrences for structural components, ≥1 for repair-tracked items

5. **Tier inference from ATA chapter** — use the ATA → tier map (see ATA Chapter Mapping above). Fall back to keyword scan of the description (`"engine"`, `"landing gear"`, `"main rotor"`, `"swashplate"`) only when ATA is missing.

6. **Same-PN clustering** — when multiple SNs appear under the same canonical PN, group them and mark as `siblings`. Used for cross-engine LLP limit propagation in Phase 7.5 verification: same PN means same OEM-published limit, regardless of SN.

7. **Batch certificate detection** — when a single Form 8130 / Form 1 covers a serial range (e.g. `"SN 004-14658M thru 004-14759M"`), DO NOT create one component per SN in the range. Create one `serials` row per individual SN observed but anchor them all to the same parent `batch_certificate` reference. Phase 7.5 will close `FORM1_MISSING` findings for any SN in the covered range.

8. **OCR rejection** — flag (PN, SN) pairs where:
   - PN has mid-string spaces (`"123 4567"`) → `OCR_SUSPECTED`
   - SN looks like a date (`"2024-01-15"`) → reject
   - PN/SN contains characters that are visually similar OCR confusions in unusual positions (O↔0, l↔1, S↔5 in fields where context says digit/letter)
   - For high-value components (ENGINE, ROTOR), trigger a vision re-read on `enhanced_s3_key` before deciding.

**After applying the 8 rules:**

For each surviving component, also pull from `entities[]` and `tables[]`:
- Promote to `components` row when ANY of these is true:
   - Appears on an `engine_llp_status_sheet` or `life_limited_parts_status` (→ `is_llp = 1`)
   - Appears on a `component_history_card` or `component_logbook`
   - Appears on a Form 1 / 8130 / TCCA / dual_release as the certified item
   - Appears in a parts table as `S/N On` (currently installed)
   - Appears with overhaul / TBO / TSO entities nearby (→ `is_overhaul = 1`)

Assign `tier` from rule 5. Assign `position` from header fields. Set initial `status = DISCOVERED`.

### Phase 5 — Event Hydration
For each page, insert every `events[]` entry into the `events` table. Map `event_type` directly. Carry `task_compliance_status` and `compliance_status_reason` verbatim. Resolve `bound_entities[].entity_id` to the matching `serials` / `components` / `requirements` row. Resolve `bound_stamps[]` to `stamps` rows.

Additional event sources:
- `defect_entry` sections → emit one `inspection` event for the discrepancy + one `task_performed` event for the corrective action, both linked to the same WO.
- `inspection_finding` sections → one `inspection` event.
- `certification_statement` sections → one `release_to_service` event.
- Parts tables with `S/N Off` and `S/N On` → one `component_removal` and one `component_installation` event per row.
- LLP and SB/AD compliance tables → one event per row.

For each event:
- If `task_compliance_status` is `listed_but_not_signed` or `ambiguous` → `TASK_NOT_CONFIRMED` finding.
- If `task_compliance_status` is `marked_not_required` → record but do NOT raise a finding (this is valid).

### Phase 6 — Document Connector Building
This is where the graph stops being a list and becomes a graph. Build edges:

1. **Work order clustering.** For every distinct `work_order` value in `metadata.reference_numbers` and `entities[]`, create a `work_orders` row. Edge `PART_OF_WORK_ORDER` from every page/document carrying that WO. Detect CRS coverage (page with `document_type ∈ {certificate_of_release_to_service, dual_release_certificate}` carrying the same WO). Set `work_orders.component_count` to the number of distinct components touched.
2. **Reference cross-linking.** Build `reference_number → pages` index. For each shared reference (any type), link pages with `REFERENCES` edges (confidence: medium).
3. **PN / SN linking.** Every page mentioning a PN gets `PAGE_REFERENCES` to its `part_types` node. Every (PN, SN) gets `PAGE_REFERENCES` to its `serials` node.
4. **Requirement linking.** Every AD/SB/EO/STC reference creates a `requirements` row. Every page mentioning it gets `PAGE_REFERENCES`. Every compliance event gets `COVERS_REQUIREMENT`.
5. **ATA linking.** Every component → `ASSIGNED_ATA`. Every page → `PAGE_REFERENCES` to all chapters in `metadata.ata_chapters`.
6. **Stakeholder linking.** Operator / MRO entities + address blocks → `stakeholders` rows. `ISSUED_BY` from documents to MROs. `APPROVED_UNDER` from documents to regulators (parsed from approval_number prefixes: UK.145.* → UK CAA, EASA.145.* → EASA, etc.).
7. **Person linking.** `entities[].entity_type == "person"` plus `stamps_and_signatures[].person_name` → `persons` rows. `SIGNED_BY` edges via stamps.
8. **Stamp binding.** For every stamp, create `STAMP_BINDS_TO` edge using `binds_to.target_kind` + `target_ref`. If `binding_confidence == "ambiguous"`, raise `STAMP_AMBIGUOUS_BINDING`.
9. **Attachment detection.** When a Form 1 page directly follows a job card / NRC in the same `document_id` and shares a (PN, SN) → `ATTACHES` edge.
10. **Date / MRO inferred packages.** Pages with no WO but same MRO and date within ±3 days → inferred `work_packages` row, confidence: medium.

11. **WO chain edges (with administrative cap).** For each work order with `2 ≤ component_count ≤ 8`, create pairwise `wo_chain` rows in `asset_relations` between every pair of components touched by that WO. Label = the dominant `event_type` across the WO's events. **For WOs with `component_count > 8`, set `is_administrative = 1` and skip the wo_chain edges entirely** — these are dossier-level admin documents, not meaningful component relationships, and adding 50×49/2 edges would explode the visualiser. Inspection sweeps and bulk preservation packages typically fall in this bucket.

12. **Parent-of relationships.** Infer `parent_of` rows in `asset_relations`:
    - LLP → engine module (when LLP appears in an engine assembly record with a parent module SN)
    - Module → engine (when module SN appears in the engine's shop visit assembly listing)
    - Blade → propeller hub (when a blade SN appears in a propeller assembly record)
    - Subcomponent → MLG/NLG assembly
    Each parent_of row needs evidence_file/page/quote — same NOT NULL rule as the rest.

13. **Replaced-by relationships.** When the same `position` (LH/RH/NLG/etc.) sees a `component_removal` event followed by a `component_installation` event of the same component class, create a `replaced_by` row from the removed component to the installed component, with `valid_from` = removal date and `valid_to` = NULL on the new component, `valid_to` = removal date on the old one.

14. **Installed-on relationships.** For every component currently on the asset (most recent installation event with no subsequent removal), create an `installed_on` row from component → asset with `valid_from` = installation date.

The `wo_chain`, `parent_of`, `replaced_by`, and `installed_on` edges all live in the `asset_relations` table (separate from the universal `edges` table) because they represent structural reality across time, not a single page reference. Every row has `evidence_file`, `evidence_page`, `evidence_quote` — the golden rule applies.

### Phase 6.5 — Critical Items Pre-Scan

Run BEFORE the tier sweep. The retrospective benchmark: in the original ATR72 run, the 605-cycle LP impeller on engine 127192 was buried at finding #66 because the agent worked tier-by-tier. It should have been #1. This phase prevents that.

Identify every item that drives transaction value or airworthiness risk. These get investigated FIRST, lead the executive summary, and bypass the normal tier ordering:

```
1. First-limited LLP per installed engine
   - For each engine, find the LLP with the lowest (remaining_cycles,
     remaining_hours). That LLP is the engine's commercial floor.

2. Any component with <1,500 cy OR <1,500 h remaining
   - Across ALL tiers, not just engines.

3. Any engine approaching shop visit within 24 months at typical utilisation
   - Compute: (current_TSN - last_SVR_TSN) > 0.8 * OEM_typical_interval

4. Any LG actuator / shock strut approaching major-inspection limit
   - Tier LANDING_GEAR, components with limit_hours present and
     remaining_hours < 1000.

5. Any flight recorder (FDR / CVR) without a current calibration record
   - calibration is annual or per-OEM; if last calibration > 1 year ago,
     surface here.

6. Lease-return window detection
   - Count WOs in the last 90 days before dossier_date.
   - If > 50 WOs in the window OR if any DUMMY tags are present in that
     window, mark the asset as `lease_return_state = true`.
   - Set the lease-return window: [dossier_date - 90 days, dossier_date].
   - Phase 7 uses this to apply the LEASE_RETURN_GAP severity downgrade
     to findings raised on documents inside the window.

7. Damage on primary structure
   - Any `dent_and_buckle_chart` events on primary structural components
     (fuselage, wings, stabilisers, primary frames).
```

Output: a `priority_items` table with columns `(rank, component_id, reason, urgency, evidence)` that drives Phase 7 ordering AND becomes the lead of the executive summary in Phase 10's stats.

### Phase 7 — Component Investigation Loop

Process components in this order (changed from previous):

1. **All `priority_items` from Phase 6.5 first** (regardless of tier).
2. **Then tier priority order:** ENGINE → ROTOR_SYSTEM → TRANSMISSION → LANDING_GEAR → PROPELLER → AIRFRAME → AVIONICS → APU → SYSTEMS → INTERIOR.

For each component, walk its events chronologically (sorted by `event_date`, falling back to TSN/CSN ordering if dates conflict). Run the **Investigation Discipline checklist** (above) before raising any "missing" finding. Verify:

- Form 1 / 8130 chain present for the installed SN (else provisional `FORM1_MISSING`)
- Removal events have a corresponding installation event for the new SN (else `CONTINUITY_BREAK`)
- For LLPs: `remaining_cycles` and `remaining_hours` calculated; raise `LLP_LIMIT_CRITICAL` (<500) or `LLP_LIMIT_WARNING` (<1500). Use sibling-PN propagation if limit is missing.
- For overhaul-tracked components: last shop visit date present (else provisional `SHOP_VISIT_MISSING`). For engines, compare to OEM-typical first-SVR interval before flagging.
- TSN / CSN / TSO / CSO populated from highest evidentiary_weight source (with `_conflict` if multiple values seen)

Apply the SEVERITY MATRIX to assign severity per finding. Apply the severity downgrade rules where context permits.

Set component `status`:
- `CLOSED` — all checks pass, full traceability
- `PARTIAL` — some data missing but not airworthiness-critical
- `GAP` — missing records on critical items (raises Level 1 finding)
- `INSTALLED_AT_MFG` — only OEM serialisation listing as birth record (acceptable)

Findings raised in this phase that did not complete the full Investigation Discipline checklist get `status = 'provisional'`. They feed Phase 7.5.

### Phase 7.5 — Verification Pass (NEW)

Run AFTER Phase 7 (component investigation), BEFORE Phase 8 (asset-level audit). The retrospective benchmark: this phase closed ~80 findings out of 263 in the original ATR72 run. Skipping it is the single biggest source of false-positive inflation that survives into the final report.

For every finding written in Phase 7 (open OR provisional), run a second pass with strategies the original investigation may not have used:

```
1. Re-search by SN alone (drop the PN entirely).
2. Re-search by alternate PN (manufacturer ↔ vendor pairing). Pull
   alternate_pns from the part_types table for the component.
3. Re-search file names for the PN substring AND the SN substring
   independently.
4. For FORM1_MISSING: parse all Form 8130 / EASA Form 1 documents in
   the dossier that reference the canonical PN. If any covers a serial
   range and the SN falls in that range → close as false positive.
5. For LLP_LIMIT_CRITICAL with missing limit (not the threshold
   findings): query sibling components (same canonical PN on the other
   engine / position). If a sibling has the limit populated → copy it
   over with confidence='high', source='sibling_propagation'. If the
   recomputed remaining_cycles/hours then exceeds threshold → close.
6. For SHOP_VISIT_MISSING: compare current TSN to OEM-typical first-SVR
   interval. If within interval → close as false positive
   (informational only, not a finding).
7. For TASK_NOT_CONFIRMED: re-read the entire WO package. Stamps are
   sometimes on the certificate page (last page of the package), not on
   the task page. Cross-reference stamps[] table with binds_to.target_ref
   pointing to a different page in the same WO.
8. For DATE_ANOMALY: check if the value is the sentinel 9999-12-31 →
   close (not an error). Check if it's the asset birth-year offset by
   one or two centuries (typo) → close with corrected date.
9. For CONTEXT_DISCREPANCY: re-verify against the asset table. If the
   page reference value matches operator/registration/MSN at all, it's
   probably not a real discrepancy.
```

Each finding takes one of three states out of this phase:

- **`open`** — survived all applicable verifications. This finding is real and goes to the final report.
- **`closed` / `false_positive`** — verification found resolving evidence. The closing evidence (file_name, page_index, chunk_id, quote, strategy) is attached to the finding row.
- **`open` (with severity downgrade)** — verification did not resolve, but the lease-return window or sibling-propagation context demands a downgrade. Original severity preserved in `original_severity` column for audit.

Provisional findings that did not complete the Investigation Discipline checklist in Phase 7 must complete it here before they can be set to `open`.

The retrospective benchmark for a properly-run verification pass: **50-80% of Phase 7 findings close as false positives** on a typical lease-return dossier. If your Phase 7.5 closure rate is below 30%, either Phase 7 was too restrictive (good, but check Investigation Discipline ran) or Phase 7.5 is incomplete (run more strategies).

### Phase 8 — Asset-Level Investigation

After components, run one pass on the asset itself. **This phase has a mandatory deliverables checklist** — Phase 8 is not complete until every item below has either an explicit finding OR an explicit "verified compliant" record. Missing any of these = the run is not finishable, the dossier is incomplete.

```
☐ Asset TSN/CSN consensus per engine and at the asset level. Use the
  evidentiary_weight cascade. Reject sub-500h false readings from task
  duration fields. Record `tsn_confidence` and `csn_confidence`.

☐ AD compliance matrix per applicable regulator:
    - State of Design regulator (EASA, FAA, TCCA, etc.) — always required
    - Operator State authority (DGCA Indonesia, ANAC Brazil, CAAC China,
      FOCA Switzerland, GACA Saudi, etc.) — always required if different
      from State of Design
    - Engine OEM ADs (per engine model)
    - Component-level ADs (LG OEM, propeller OEM, etc.)
  Output: one row per applicable AD with status (complied / not-complied /
  not-applicable / unverified) and source evidence.

☐ SB compliance list — every SB the operator has on record, with
  completion date or "not applicable" status.

☐ Major check history with next-due calculation
  (C-checks, structural checks, heavy maintenance, calendar checks).

☐ Dent and buckle chart status (for airframe assets) — every charted
  dent/buckle/repair traceable to a work record OR explicitly marked
  "monitored / no action required".

☐ Hard-time component status with remaining life per item. Use the
  hard-time / on-condition convention rule from Data Quality Rules.

☐ Lease return / storage state determination (carry forward Phase 6.5's
  `lease_return_state` flag and document the WO-cluster / DUMMY-tag
  evidence).

☐ APU status — TSN/CSN, last shop visit, LLPs, AD compliance — OR an
  explicit "no APU" record if the asset doesn't have one.

☐ Engine TSN/CSN/TSO/CSO consensus per engine.

☐ Damage history (events with `event_type == "damage"` and
  `dent_and_buckle_chart` documents).

☐ Operator country / state authority detection (drives MTS naming and
  AD applicability).
```

If any of the items above could not be answered after Phase 7.5, raise a finding (`AD_COMPLIANCE_UNVERIFIED`, `GAP_IN_DOSSIER`, etc.) rather than silently omitting it. The mandatory checklist is what makes the dossier audit complete; the findings tell the buyer what's still open.


### Phase 9 — Finding Consolidation

- 10+ findings of the same type → one summary finding listing affected SNs
- Batch certificates covering serial RANGES → collapse individual `FORM1_MISSING` for SNs within range
- Findings with closing evidence elsewhere in dossier → mark `closed` (false_positive) with the evidence

### Phase 10 — Graph Export
Write `workdir / "graph_export.json"`:

```json
{
  "asset": { "id": "...", "asset_kind": "...", "type_designation": "...", ... },
  "stats": {
    "total_pages": 0,
    "total_documents": 0,
    "total_components": 0,
    "total_events": 0,
    "total_stamps": 0,
    "components_by_tier": { "ENGINE": 0, "LANDING_GEAR": 0, ... },
    "components_by_status": { "CLOSED": 0, "PARTIAL": 0, "GAP": 0, "INSTALLED_AT_MFG": 0 },
    "documents_by_type": { "easa_form_one": 0, "routine_task_card": 0, ... },
    "evidentiary_weight_breakdown": { "primary": 0, "secondary": 0, ... },
    "mis_export_pages": 0,
    "findings_by_severity": { "1": 0, "2": 0, "3": 0 }
  },
  "nodes": [
    { "id": "asset::...", "label": "...", "tier": "AIRCRAFT_CENTER", "status": "...", "data": {...} },
    { "id": "tier::ENGINE", "label": "Engines", "tier": "ENGINE", "status": "TIER_GROUP" },
    { "id": "component::PN::SN", "label": "...", "tier": "ENGINE", "status": "CLOSED", "data": {...} }
  ],
  "edges": [
    { "id": "...", "from": "...", "to": "...", "type": "installation", "confidence": "high", "data": {...} }
  ],
  "doc_nodes": [...],
  "doc_edges": [...],
  "ata_nodes": [...],
  "ata_edges": [...]
}
```

`doc_nodes` / `ata_nodes` are loaded only when the user switches to the Documents / ATA view. Keep main `nodes` / `edges` focused on components and events for the default view.

---

## DATA QUALITY RULES (universal)

These apply to every dossier, layered on top of what the OCR already validated.

1. **Trust per-entity confidence.** Carry `entities[].confidence` through to every derived edge. Never upgrade confidence; only carry or downgrade.
2. **Impossible dates.** Any date before the asset's manufacture year, or more than 6 months after dossier date → `DATE_ANOMALY`. Do not use as evidence.
3. **Universal SN blocklist (per dossier).** Asset MSN, registration mark, primary engine SNs (engine-only), year strings 1990..2030, single characters, document numbers matching SN values verbatim.
4. **TSN false readings.** Values from "Total Hours" or "Hours" fields dramatically smaller than known asset TSN are task durations or leg hours, not asset totals. Cross-check evidentiary_weight before accepting.
5. **OCR_SUSPECTED entities.** When `entities[].confidence == "low"` AND the entity is critical (PN/SN/WO/AD/SB), trigger vision re-read on `enhanced_s3_key` for that page.
6. **Trust task_compliance_status, do not re-derive.** OCR has done the work; downstream just acts on the value.
7. **MTS source naming.** "CAMP" inside a regulatory citation = "Continuous Airworthiness Maintenance Program" (concept). "CAMP" as `mis_system` in OCR metadata = the software product. Different things; do not conflate when raising `MTS_CONFLICT`.
8. **Component TSN > airframe TSN is normal** (prior service history on another asset). Trace the prior history; do not flag as error.
9. **Batch certificates.** Form 1 / 8130 covering serial ranges are valid. Before raising `FORM1_MISSING` for an individual SN, parse the range and check membership. If the SN falls inside any batch range, close the finding as false positive.
10. **Lease return context.** DUMMY UNSERVICEABLE tags, placeholder serials, tight WO clusters in the weeks before dossier date → asset is being prepared for redelivery. Documentation gaps in this window are sequencing issues (Level 2), not airworthiness violations (Level 1).
11. **Evidentiary weight conflict resolution.** Primary > Secondary > Reference > Administrative. Within the same weight: physical > MIS export. Within those: most recent date. Within those: highest entity confidence.
12. **Rotated pages.** If `rotation_hint != 0` AND derived edges from that page have low confidence, queue a vision re-read.
13. **Sibling-PN limit propagation.** OEM publishes life limits per PN, not per SN. If a component has a missing limit but the same canonical PN appears elsewhere on the asset (sibling engine, opposite position, batch installation), copy the limit from the sibling with `confidence = high` and source `sibling_propagation`. Only flag `LLP_LIMIT_CRITICAL` after this lookup, not before.

---

### Aviation domain patterns (do not flag these as errors)

These are normal aviation-records conventions that look like errors to a naive parser. Every one of them was a false-positive driver in the original ATR72 run.

- **Sentinel date `9999-12-31`** in any date field — MTS placeholder for "no due date / unlimited / N/A". Not an OCR error. Treat as `null`.
- **Form 1 issue date older than signature date** — re-release after overhaul is the most common cause. Acceptable unless the gap exceeds ~2 years.
- **Component TSN > airframe TSN** — prior installation on another asset. Trace prior history; never flag.
- **Propeller hub TSN > airframe TSN** — same as above; common because props move between aircraft.
- **DUMMY UNSERVICEABLE / DUMMY INSTALLATION PERFORMED** in tag text — storage / lease return convention, not an airworthiness defect.
- **"NOT REQUIRED FOR THIS INPUT" / "N/A" in task action-taken fields** — task was correctly skipped per the work scope. Not a missing sign-off.
- **WO series clustering near dossier date** (e.g. 200+ WOs in the final 60 days, often a `419xxx` range or similar operator-specific block) — asset stripping for redelivery. All gaps in this window are Level 2 maximum.
- **Engine "PART OUT (SVC) — CUSTOMER REQUEST"** — operator harvesting serviceable components for spares before redelivery. Commercial state, not airworthiness.
- **Indonesian / Asian / European operators using "CAMP REFERENCE"** — almost always means the regulatory concept "Continuous Airworthiness Maintenance Program", not the US software product. Their actual MTS is usually AMOS, SAP, MXP, or a custom system. Detect operator country before raising `MTS_CONFLICT`.
- **Operator-consolidated status sheets** — file names matching the asset MSN/registration prefix (e.g. `MSN_1191_*.pdf`, `<reg>_status_*.pdf`) carry concentrated component status data. Always read these in full when investigating any component the operator tracks.

### OEM-typical first shop visit intervals (sanity baseline)

Use these intervals **before** raising `SHOP_VISIT_MISSING`. An engine within its OEM-typical first-SVR interval with no SVR record is informational, not a finding. Only flag when the interval is exceeded.

```
PW100 family (incl. PW127M, PW127N, PW150)   ~10,000 - 12,000 hours
PT6A family                                   ~3,500 - 5,000 hours (high variance)
CFM56-3                                       ~12,000 - 15,000 hours
CFM56-5B / -7B                                ~18,000 - 22,000 hours
LEAP-1A / -1B                                 ~20,000+ hours (newer fleet)
V2500 (IAE)                                   ~15,000 - 18,000 hours
GE90 / GEnx                                   ~20,000 - 25,000 hours
Trent 700 / 800 / 900 / 1000                  ~18,000 - 22,000 hours
PW4000 family                                 ~16,000 - 20,000 hours
RB211                                         ~12,000 - 15,000 hours
```

These are typical-fleet figures, not contractual limits. Use as sanity bounds only. The engine OEM's published shop visit interval (when present in the dossier) always wins.

### Hard-time / on-condition convention

- **Hard-time** components must be removed at the calendar/cycles/hours limit regardless of condition. Track remaining life; flag at <500 h/cy.
- **On-condition** components are removed when condition demands; they have no scheduled removal limit. Do not flag for missing remaining-life data.
- **Soft-time / TBO** is informational; the operator may extend TBO under approved conditions.

When in doubt, default to on-condition (no finding) rather than hard-time (false positive).

---

## INVESTIGATION DISCIPLINE — DON'T FLAG WHAT YOU HAVEN'T LOOKED FOR

The single biggest failure mode of the original ATR72 run was agents writing `FORM1_MISSING` after one or two FTS searches that returned nothing. The Form 1 usually existed — embedded in a work order PDF whose title didn't contain "Form 1", or filed under SN-only without the PN, or covered by a batch certificate. **523 raw findings → 100 genuine** mostly because investigations short-circuited.

This section makes that short-circuit illegal.

### Hard prerequisite checklist for any "missing" finding

Before writing any of `FORM1_MISSING`, `SHOP_VISIT_MISSING`, `AD_COMPLIANCE_UNVERIFIED`, `SB_COMPLIANCE_UNVERIFIED`, `GAP_IN_DOSSIER`, `PRIOR_HISTORY_MISSING`, the agent must have completed all applicable items:

```
☐ Read every page of every work order package the component appears in.
  Do not stop at the WO summary — walk all pages with PART_OF_WORK_ORDER edges.

☐ Searched the corpus by SN alone (drop the PN). Form 1s are often filed
  by SN only.

☐ Searched the corpus by canonical PN AND each entry in alternate_pns.
  Manufacturer ↔ vendor PN pairs are the most common miss.

☐ Checked file names containing the PN as a substring (e.g. "*PN_{pn}*",
  "*{pn}*"). Many MROs file Form 1s under the PN in the filename.

☐ Checked file names containing the SN as a substring.

☐ Checked operator-consolidated status sheets — file names matching the
  asset MSN/registration prefix (e.g. "MSN_{msn}_*.pdf",
  "{registration}_status_*.pdf", "AC_{registration}_*.pdf").

☐ Checked batch Form 8130 / Form 1 ranges. If any batch certificate exists
  for the canonical PN, parse the SN range and check membership of the
  installed SN.

☐ Searched the immediate page neighbourhood (±3 pages in the same PDF).
  Form 1s are commonly attached after the job card that consumed them.

☐ For LLP limits: queried sibling components (same canonical PN on the
  other engine / opposite position / batch installation). OEM limits
  are per-PN, not per-SN.

☐ For shop visit checks: compared engine TSN to the OEM-typical first-SVR
  interval (Aviation Domain Patterns above). If within interval → no finding.
```

If any applicable item was skipped, the finding is **provisional only** — it gets a `status = 'provisional'` flag and feeds Phase 7.5 (Verification Pass) for a second look. Provisional findings never go in the final report without being upgraded to `status = 'open'` after verification.

### Read the document, don't just keyword-match

The connectors built in Phase 6 mean every component already has its WO packages, certificates, and stamps linked. Phase 7 walks those edges; it does not re-search the FTS index for the same data the connectors expose. FTS is a fallback for entities the connectors didn't capture, not a substitute for reading the linked documents.

When in doubt: prefer reading more, flagging less. False positives cost the team more than false negatives — they shake confidence in every other finding the system produces.

---

## FINDING TYPES (universal — exact strings)

```
TIMES_INCOMPLETE         - TSN/CSN could not be determined
FORM1_MISSING            - No Form 1 / 8130 / TCCA found for installed SN
FORM1_SN_NOT_VERIFIED    - Parts cert found but SN does not match installed SN
SB_WITHOUT_CRS           - SB compliance event without CRS in same WO bundle
AD_WITHOUT_CRS           - AD compliance event without CRS in same WO bundle
WORK_PACKAGE_WITHOUT_CRS - WO has no release certificate
TASK_NOT_CONFIRMED       - Event with task_compliance_status in {listed_but_not_signed, ambiguous}
DATE_ANOMALY             - Date impossible for this asset
OCR_SUSPECTED            - Entity confidence low on a critical entity
CONTINUITY_BREAK         - Component disappears and reappears without removal record
SHOP_VISIT_MISSING       - Expected overhaul not found
LLP_LIMIT_CRITICAL       - <500 cycles/hours remaining
LLP_LIMIT_WARNING        - <1500 cycles/hours remaining
SN_AMBIGUOUS             - Same PN with multiple SNs, or same SN on different PNs
PN_ALTERNATE_UNRESOLVED  - Manufacturer PN and vendor PN both present, not confirmed same part
MTS_CONFLICT             - MIS export disagrees with primary physical record
AD_NOT_LISTED            - AD applicable to asset not found in dossier
SB_NOT_LISTED            - SB applicable to asset not found in dossier
DAMAGE_NOT_TRACED        - Damage event mentioned but no work report found
REPAIR_TEMPORARY         - Temporary repair without permanent resolution evidence
ICA_NOT_ENROLLED         - STC ICA requirements not enrolled in maintenance program
GAP_IN_DOSSIER           - No records found after exhaustive search
STAMP_AMBIGUOUS_BINDING  - Stamp binding_confidence == "ambiguous"
CONTEXT_DISCREPANCY      - OCR flagged metadata.context_discrepancy on a page
ROTATED_PAGE_LOW_CONF    - Page with rotation_hint != 0 yielded low-confidence derived data
AD_COMPLIANCE_UNVERIFIED - Applicable AD has no compliance record in dossier
SB_COMPLIANCE_UNVERIFIED - Applicable SB has no compliance record in dossier
HARD_TIME_LIMIT_APPROACH - Hard-time / on-condition component approaching limit
PRIOR_HISTORY_MISSING    - Component TSN > airframe TSN but no prior installation record
ALTERNATE_PN_NOT_LINKED  - Two PNs that should be linked as alternates not yet merged
LEASE_RETURN_GAP         - Documentation gap inside lease-return window (max Level 2)
```

## SEVERITY MATRIX — CRITICALITY-BY-COMPONENT (most important rule in the file)

**This is the single biggest source of false-positive inflation if ignored.** A `FORM1_MISSING` on an engine LLP is airworthiness; a `FORM1_MISSING` on a PBE oxygen generator is a paperwork gap. The original ATR72 run produced 263 raw L1 findings of which only 30 were genuine — almost entirely because every `FORM1_MISSING` defaulted to L1 regardless of what component it was on.

The severity of any finding depends on **what type of component it is on**, not on the finding type alone. The matrix below is non-negotiable.

### Level 1 (airworthiness — must resolve before transaction)

```
FORM1_MISSING / FORM1_SN_NOT_VERIFIED on:
  - Engine LLPs (HPC/HPT/LPC/LPT disks, shafts, impellers)
  - Engine modules subject to mandatory life limits
  - Landing gear primary assemblies (MLG/NLG shock struts, drag braces,
    main fittings, retraction actuators with major-inspection limits)
  - Propeller hubs
  - Rotor head / swashplate / pitch links (helicopters)
  - MGB / IGB / TGB main casings and shafts (helicopters)
  - Flight recorders (FDR, CVR)
  - Primary structural repairs (SRPSA, REO on primary structure)

LLP_LIMIT_CRITICAL                  (always — <500 cy or <500 h remaining)
LLP_LIMIT_WARNING                   (always — <1500 cy or <1500 h remaining)
AD_COMPLIANCE_UNVERIFIED            (always — every applicable AD must be traced)
CONTINUITY_BREAK on:
  - Engines
  - Landing gear primary assemblies
  - Propeller hubs
  - Rotor head / MGB (helicopters)
SHOP_VISIT_MISSING on engines beyond OEM-typical first-SVR interval
                                    (see Aviation Domain Patterns below)
DAMAGE_NOT_TRACED on primary structure
HARD_TIME_LIMIT_APPROACH on critical hard-time items (<500 h to limit)
```

### Level 2 (data correction — work was done but not recorded properly)

```
FORM1_MISSING / FORM1_SN_NOT_VERIFIED on:
  - On-condition accessories (sensors, transmitters, igniters, HBV, servos,
    fuel nozzles, fire extinguisher cartridges, valves, thermocouples,
    actuator subcomponents)
  - Emergency equipment (PBE, ELT, life rafts, slide rafts, emergency
    batteries, emergency lights, oxygen generators, escape ropes)
  - Cabin / interior items (seats, galley equipment, lavatory components)
  - Secondary structural items not on the primary load path

TIMES_INCOMPLETE on non-LLP components
DATE_ANOMALY where OCR is the likely cause and a correction is recoverable
TASK_NOT_CONFIRMED                  (default L2; only L1 if task is on a
                                    Level 1 component above)
SB_WITHOUT_CRS / AD_WITHOUT_CRS    (default L2; L1 only if the SB/AD is
                                    safety-critical and uncovered by another CRS)
WORK_PACKAGE_WITHOUT_CRS
LEASE_RETURN_GAP                   (always — documentation gaps in the
                                    lease-return window are sequencing issues,
                                    never airworthiness)
SB_COMPLIANCE_UNVERIFIED            (default L2 unless the SB is mandatory)
PRIOR_HISTORY_MISSING               (default L2)
SN_AMBIGUOUS                        (default L2)
MTS_CONFLICT                        (always L2 — physical record wins)
DAMAGE_NOT_TRACED on secondary structure
ICA_NOT_ENROLLED                    (default L2 unless STC is recent)
SHOP_VISIT_MISSING on engines within OEM-typical first-SVR interval
                                    (downgrade to L2 — this is informational,
                                    not airworthiness)
```

### Level 3 (improvement — housekeeping, future audits will surface again)

```
PN format variants (A36.560-1 / A36560-1, dashes vs dots, leading zeros)
SN suffix variants (R25-1M2C / R25-1M2CW)
Transcription typos (30396098 vs 3039609)
Notation conventions ("NA" vs "N/A" vs "—" vs blank)
Hard-time sheet calculation rounding errors (off by one cycle)
SB compliance noted in SVR text but full SB document not in dossier
ALTERNATE_PN_NOT_LINKED
PN_ALTERNATE_UNRESOLVED
OCR_SUSPECTED on non-critical entities
STAMP_AMBIGUOUS_BINDING on non-critical events
ROTATED_PAGE_LOW_CONF
CONTEXT_DISCREPANCY on non-identifying fields
REPAIR_TEMPORARY where monitoring is the agreed disposition
```

### Severity downgrade rules (apply AFTER the matrix above)

A finding's severity may be downgraded one level (never upgraded) by these context rules:

1. **Lease return window.** If the asset is in the lease-return window (Phase 6.5 detection), downgrade any L1 finding raised on a document inside that window to L2. Exception: LLP limits and AD compliance never downgrade — those are objective regardless of context.
2. **Batch certificate covers the SN.** If a batch certificate covers the SN's range, the finding is closed (false positive), not downgraded.
3. **Sibling component has the data.** If a sibling component (same PN on the other engine / position) has the missing data populated, the finding is closed with the sibling as evidence.
4. **OEM-typical interval.** For `SHOP_VISIT_MISSING`, if the engine is within the OEM-typical first-SVR interval, downgrade L1 → L2 → no finding (informational only).

The severity ladder, top to bottom: write the matrix-default severity, then apply at most one downgrade rule, then commit the finding.

---

## VISUALISATION — `asset_graph.html`

**Stop. Do not write this HTML from scratch. Do not let an LLM "design" it. Do not interpret a description.**

The visual design is fixed and lives in a real file you copy: `asset_graph_template.html` at the project root. The template was built and tested against a real dossier. Every dossier produces the same look — only the data changes. Re-implementing it from a description always produces the wrong thing (default vis-network white background, no toolbar, no panel, no legend — exactly the failure mode this section exists to prevent).

### The deliverable

```
{workdir}/
├── asset_graph_template.html   ← READ-ONLY source of truth for the design
└── viz.py                      ← copies the template, substitutes asset title
```

`viz.py` does string substitution. That is its only job. It does not generate CSS. It does not generate JavaScript. It does not "improve" the layout. The template is canon.

### How it works

1. `asset_graph_template.html` is checked into the project root. It contains the full inline CSS, the full inline JavaScript, the stats bar, the filter toolbar, the legend, the side panel, the vis-network configuration, the loading overlay, and one placeholder: `{{ASSET_TITLE}}` (used in `<title>` and `.title-text`).
2. The template loads `graph_export.json` from its own directory via `fetch('graph_export.json')`. The shape of that JSON is defined in Phase 10 (Graph Export) — the template depends on those exact keys (`asset`, `stats`, `nodes`, `edges`, `events`, `findings`, `findings_summary`, optionally `doc_nodes`, `doc_edges`, `ata_nodes`, `ata_edges`).
3. `viz.py` reads the assets table from `graph.db`, builds a one-line title (e.g. "ATR72-212A PK-GAI MSN 1191" for an aircraft, "PW127M ESN ED0855" for an engine-only dossier), substitutes `{{ASSET_TITLE}}`, writes `asset_graph.html` next to `graph_export.json`.

### What `viz.py` MUST NOT do

- Do not generate any HTML, CSS, or JavaScript inline in Python. If `viz.py` contains an `<html>` string literal, it is wrong — delete it and re-read this section.
- Do not modify the template's vis-network options. The physics tuning (`forceAtlas2Based`, `gravitationalConstant: -120`, `centralGravity: 0.008`, `springLength: 160`, etc.) is calibrated for these graphs.
- Do not change the colour palette, font, layout, or any class names. The `graph_export.json` field names match the template's expectations exactly — renaming a field on either side breaks rendering silently.
- Do not "simplify" the template by stripping unused features. Every checkbox group, every legend section, every panel section is used somewhere.

### What MUST match between template and Phase 10 export

The template's JavaScript reads these specific shapes. Phase 10 must produce them:

```
graphData.asset                    object — registration, msn, type_designation,
                                            operator, tsn, csn, dossier_date, etc.
graphData.stats                    object — total_components, total_events, total_findings,
                                            components_by_tier, components_by_status,
                                            documents_by_type, evidentiary_weight_breakdown
graphData.nodes                    array  — { id, label, group, shape, size, color,
                                              borderWidth, font, tier, status, data }
graphData.edges                    array  — { id, from, to, color, width, dashes,
                                              event_type, title }
graphData.events                   object — { component_id: [event, ...] }
graphData.findings                 object — { component_id: [finding, ...] }
graphData.findings_summary         object — by-severity counts and lists
graphData.doc_nodes / doc_edges    array  — only for Documents view
graphData.ata_nodes / ata_edges    array  — only for ATA view
graphData.time_nodes / time_edges  array  — only for Time view (see below)
graphData.lease_return_state       object — see Phase 6.5; drives the lease-return banner
graphData.priority_items           array  — see Phase 6.5; drives Critical Items panel
graphData.mandatory_checklist      object — see Phase 8; drives Mandatory Checklist panel
graphData.verification_stats       object — see Phase 7.5; drives Audit Quality panel
```

**Time view** (NEW): a fourth view alongside Simple / Detailed / Documents / ATA. Components plotted on a horizontal time axis at their installation date; replacement events shown as edges between successive components on the same position. The lease-return WO cluster (and any past engine-swap pattern) jumps off the page in this view.

```
graphData.time_nodes               array — { id, label, position, install_date,
                                              removal_date, tier, color }
                                            (one node per (component, install_date) — replaced
                                            components keep their own time-bounded node)
graphData.time_edges               array — { from, to, edge_type: "replaced_by",
                                              date, evidence_file, evidence_page }
```

The time view reads from `asset_relations` (specifically the `replaced_by` and `installed_on` rows produced in Phase 6 step 13 and 14).

If the template renders blank or shows "Failed to load graph_export.json", the bug is one of: (a) the file isn't in the same directory as the HTML, (b) the JSON is missing one of the keys above, (c) a key is named differently than the template expects. Open the browser DevTools console — the actual error is always there.

### Generation command

```
python viz.py --workdir ./run
```

Reads `./run/graph.db` for the title, reads `./run/asset_graph_template.html` (or `--template path/to/template.html`), reads `./run/graph_export.json` to verify it exists, writes `./run/asset_graph.html`.

### Cross-platform open

- Double-click the HTML file on Windows / macOS / Ubuntu — it should render in the default browser.
- Some browsers (Chrome in particular) block `fetch('graph_export.json')` over `file://` due to CORS. If the loading spinner stays forever and DevTools shows a CORS error, the user runs `python -m http.server` from the workdir and visits `http://localhost:8000/asset_graph.html`. The template's loading-error message must include this fallback instruction.

### If the template needs to change

Edit `asset_graph_template.html` directly. Test it against a real `graph_export.json`. Commit it. Do not edit `viz.py` to "patch" template issues — that path leads to two sources of truth and a broken visualiser.

---

## ENVIRONMENT SETUP — INSTALL DEPENDENCIES BEFORE PROCESSING

**This is Step 0. Do this before touching the dossier on any new machine.** Same instructions for Windows, macOS, and Ubuntu.

### Python version

Requires **Python 3.10 or newer** (uses modern `pathlib`, `dict | None` type hints, structural pattern matching). Check with:

```
python --version
```

If `python` resolves to Python 2 on your system (some older Macs, some Linuxes), use `python3` instead and substitute `python3` everywhere below.

### Step 1 — Create a virtual environment (recommended)

A virtual environment keeps this project's dependencies isolated from the system Python. Run from inside `--workdir`:

```
python -m venv .venv
```

Activate it (the activation command differs per OS — this is the only place OS matters):

```
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Windows cmd.exe
.venv\Scripts\activate.bat

# macOS / Linux / Ubuntu
source .venv/bin/activate
```

When active, your prompt will be prefixed with `(.venv)`. To leave the environment later: `deactivate`.

### Step 2 — Create `requirements.txt`

Place this file at the project root (`workdir / "requirements.txt"`):

```
# Core
anthropic>=0.39.0          # Claude API client (Phases 7 and 8 agents)
pandas>=2.0.0              # CSV streaming, dataframe ops
tqdm>=4.66.0               # Progress bars during long phases
python-dotenv>=1.0.0       # Load ANTHROPIC_API_KEY from .env file

# Entity resolution and fuzzy matching
rapidfuzz>=3.0.0           # Cross-page PN/SN identity resolution (much faster than fuzzywuzzy)

# Vision re-reads (when OCR confidence is low or rotation_hint != 0)
Pillow>=10.0.0             # Image decoding for vision tool calls
boto3>=1.34.0              # S3 access for enhanced_s3_key (only needed if using S3-hosted page images)

# JSON handling (faster than stdlib for 50k row CSVs)
orjson>=3.9.0              # Fast JSON parsing for extracted_json field

# Optional but recommended
rich>=13.0.0               # Better progress output and structured logging
```

**Notes:**

- `sqlite3`, `pathlib`, `json`, `concurrent.futures`, `csv` are part of the Python standard library. No install needed.
- `boto3` is only required if `enhanced_s3_key` values point to a real S3 bucket you need to fetch from. If page images are local-only, omit it.
- `orjson` provides 5–10× faster JSON parsing than the stdlib `json` module — important when streaming 50,000+ rows where each row contains a large `extracted_json` payload.

### Step 3 — Install everything

Same command on every OS:

```
pip install --upgrade pip
pip install -r requirements.txt
```

Verify the install:

```
python -c "import anthropic, pandas, tqdm, rapidfuzz, orjson; print('OK')"
```

If that prints `OK`, you're ready.

### Step 4 — API key

Create a `.env` file in the workdir (NEVER commit this to git):

```
ANTHROPIC_API_KEY=sk-ant-...
```

Add `.env` and `.venv/` to `.gitignore`. The agent code loads this with `python-dotenv` at startup.

### Step 5 — Sanity check the database engine

SQLite ships with Python but the FTS5 extension is required for `pages_fts`. Verify:

```
python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('FTS5 OK')"
```

If this fails on Linux, install a Python build with FTS5 enabled (Python.org installers and most distro packages have it; some minimal builds do not). On Windows and macOS the official Python installer always includes FTS5.

### Step 6 — Confirm the install before processing

Do not run Phase 1 until the previous five steps have all passed. Processing 50k rows with a missing dependency means losing the whole indexing pass and starting over.

---

## FILE STRUCTURE (relative to `--workdir`)

```
{workdir}/
├── GRAPH.md                          ← this file (reference)
├── asset_graph_template.html         ← READ-ONLY template for the visualisation
├── requirements.txt                  ← pip dependencies (see ENVIRONMENT SETUP)
├── .env                              ← ANTHROPIC_API_KEY (gitignored, never commit)
├── .gitignore                        ← excludes .env, .venv/, graph.db, _checkpoints/
├── .venv/                            ← virtual environment (gitignored)
├── main.py                           ← entry point, runs all phases
├── phase0_asset_orientation.py       ← reads ≤30 pages, writes asset_profile.json
├── phase1_index.py                   ← CSV → SQLite + FTS + stamp hydration
├── phase2_asset_detection.py         ← confirms asset_profile against corpus, writes assets row
├── phase3_tiers.py                   ← create tier group nodes
├── phase4_components.py              ← part_types / serials / components hydration
├── phase5_events.py                  ← event hydration from events[] + tables + sections
├── phase6_connectors.py              ← document connectors (the graph backbone)
├── phase6_5_critical_items.py        ← priority items pre-scan + lease return detection
├── phase7_investigation.py           ← per-component investigation
├── phase7_5_verification.py          ← false-positive closure pass
├── phase8_asset_audit.py             ← asset-level investigation (mandatory checklist)
├── phase9_consolidation.py           ← finding consolidation
├── phase10_export.py                 ← graph_export.json generation
├── viz.py                            ← string-substitutes ASSET_TITLE into the template
├── tools.py                          ← shared SQLite, FTS, retrieval helpers
├── agent.py                          ← Claude API wrapper (only used in Phases 7 & 8 for judgement calls)
├── graph.db                          ← created at runtime
├── asset_profile.json                ← created by Phase 0; consumed by every later phase
├── graph_export.json                 ← created at runtime
├── asset_graph.html                  ← created at runtime (copy of template + asset title)
├── progress.log                      ← created at runtime
└── _checkpoints/                     ← per-phase resume markers
```

All paths in code constructed via `Path(workdir) / "filename"`. Never use string concatenation, never hardcode separators.

---

## EXECUTION ORDER

```
# Run everything end-to-end:
python main.py --csv ./input/dossier.csv --workdir ./run

# Or step by step:
python main.py --workdir ./run --phase 0               # asset orientation (≤30 representative pages)
python main.py --workdir ./run --phase 1               # CSV indexing + stamps
python main.py --workdir ./run --phase 2               # asset detection
python main.py --workdir ./run --phase 3               # tier groups
python main.py --workdir ./run --phase 4 5             # components + events
python main.py --workdir ./run --phase 6               # connectors (graph backbone)
python main.py --workdir ./run --phase 6.5             # critical items pre-scan
python main.py --workdir ./run --phase 7               # component investigation
python main.py --workdir ./run --phase 7.5             # verification pass (closes false positives)
python main.py --workdir ./run --phase 8               # asset-level audit (mandatory checklist)
python main.py --workdir ./run --phase 9               # finding consolidation
python main.py --workdir ./run --phase 10              # graph_export.json
python main.py --workdir ./run --phase viz             # asset_graph.html

# Resume from last checkpoint:
python main.py --workdir ./run --resume
```

The same commands work identically on Windows PowerShell, macOS Terminal, and Ubuntu shell — no path adjustments needed.

**Health benchmarks per phase (catch problems early):**

```
Phase 0   — asset_profile.json written and human-reviewed before Phase 1 starts
Phase 1   — pages indexed should equal CSV row count
Phase 4   — components > 0 (if 0, no entities reached the table — bug in Phase 1)
Phase 6   — every document has at least one edge (if any document has zero
            connectors, Phase 6 missed it)
Phase 6.5 — priority_items table populated; lease_return_state row written
Phase 7   — findings ratio: provisional should be ~0% if discipline is
            running, ~50%+ if discipline was skipped
Phase 7.5 — closure rate 50-80% on lease-return dossiers; <30% means either
            Phase 7 was disciplined (good) or 7.5 is incomplete (bad)
Phase 8   — every mandatory checklist item has either a finding OR a
            "verified compliant" record; no silent omissions
Phase 10  — graph_export.json passes the structural check in viz.py
```

---

## WHAT GOOD LOOKS LIKE

By the end of execution:

- `graph.db` contains every page, every document, every stamp, every component, every event, every requirement, every stakeholder, and the edges between them. Every fact has a source page reference and a confidence carried from the OCR.
- `graph_export.json` is a clean projection sized to load in a browser without strain.
- `asset_graph.html` opens in any browser on any OS and shows the asset at the centre with tier groups radiating out, components attached to their tiers, events as edges, full filter / search / view-switching capability.
- Clicking any node opens the side panel with full context, including chronological event timeline and findings list.
- Switching to Documents view exposes source files. Switching to ATA view groups by system. Both load fast.
- Findings are traceable: opening the cited file at the cited page shows the evidence quote.

**Audit quality benchmarks (calibrated against the ATR72-1191 retrospective):**

- **L1 finding count:** ~5-15% of total findings. If >25% of findings are L1, the severity matrix is being misapplied (probably defaulting `FORM1_MISSING` to L1 regardless of component criticality).
- **Phase 7.5 closure rate:** 50-80% on lease-return / pre-redelivery dossiers, 20-40% on operational dossiers. <20% means verification is incomplete.
- **Mandatory checklist coverage:** every Phase 8 item has either an explicit finding OR an explicit "verified compliant" record. Zero silent omissions.
- **Critical items lead the report:** the executive summary's first paragraph names items from `priority_items`, not arbitrary findings from the tier sweep.
- **Lease return state acknowledged:** if `lease_return_state.is_lease_return == 1`, the report explicitly states this commercial context before listing any window-period findings.
- **Provisional findings cleared:** zero findings with `status = 'provisional'` in the final export. They were either upgraded to `open` or closed in Phase 7.5.

The graph is the deliverable. The HTML is the way humans see it. Both are projections of the SQLite truth memory.

---

## START HERE

1. Read this file completely.
2. **Confirm `asset_graph_template.html` is at the project root.** If it's missing, stop and ask — do not generate it from scratch. The template is the source of truth for the visualisation; without it, you will produce a default vis-network white-background graph that fails review immediately.
3. **Set up the environment.** Follow the ENVIRONMENT SETUP section above: check Python version, create `.venv`, write `requirements.txt`, run `pip install -r requirements.txt`, create `.env` with the API key, verify the FTS5 sanity check passes. Do not skip this — running Phase 1 without dependencies installed wastes hours.
4. Confirm `--csv` and `--workdir` arguments. Resolve to absolute paths once at startup, then work in `Path` objects only.
5. Create `tools.py` and `agent.py`. Implement SQLite helpers, FTS search, chunk retrieval. Use `pathlib.Path` everywhere.
6. **Run Phase 0 (asset orientation).** Read ≤30 representative pages, write `asset_profile.json`. **Read the profile yourself before continuing.** If `asset_class` / `type_designation` / `state` looks wrong, fix the profile (or the page selection) before running Phase 1. Every later phase is going to trust this file.
7. Run Phase 1 (indexing). Verify page count matches CSV row count. Spot-check that one page's `entities[]`, `events[]`, and `stamps_and_signatures[]` all landed in their tables.
8. Run Phase 2 (asset detection). It will confirm the profile against the corpus and raise `CONTEXT_DISCREPANCY` for any field where the corpus contradicts what Phase 0 wrote.
9. Run Phases 3–6. After Phase 6, the graph backbone exists — every document is connected by at least one edge. Spot-check the work order clustering and the `wo_chain` cap (no WO with >8 components produced wo_chain edges — those should be marked `is_administrative`).
10. **Run Phase 6.5 (critical items pre-scan).** Verify `priority_items` is populated and `lease_return_state` is written. If `asset_profile.state` was already `"lease_return"`, the lease-return window logic should already have fired in Phase 6.5 from the profile alone.
11. Run Phase 7 (component investigation). Process priority items first, then tier-by-tier. **Each "missing" finding must complete the Investigation Discipline checklist or be marked `provisional`.** This is the longest phase.
12. **Run Phase 7.5 (verification pass). Do not skip this.** It closes the false positives that Phase 7 raised in good faith. The retrospective benchmark says 50-80% closure on lease-return dossiers; if your closure rate is well below that, something in 7.5 is incomplete.
13. Run Phase 8 (asset-level audit). **Every mandatory checklist item must have either an explicit finding or an explicit "verified compliant" record before this phase is considered complete.**
14. Run Phases 9-10.
15. Generate `asset_graph.html` by running `python viz.py --workdir ./run`. **Open it. Confirm it has the dark theme, the stats bar, the toolbar with checkboxes, the legend bottom-left, and the side panel — exactly as in the template.** If it looks like a default vis-network graph (white background, no chrome), `viz.py` is generating HTML instead of copying the template. Fix `viz.py` before continuing.
16. **Review the L1 findings ratio.** If L1 is >25% of total findings, the severity matrix was misapplied — re-run Phase 7 with the matrix as the binding rule, not a guideline.
17. Spot-check three findings: open the source file at the cited page and confirm the evidence quote is correct.

The graph is the deliverable. Build it well.

Go.