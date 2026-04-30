# PHASE 10 — Graph Export

**Intent.** Read the database. Project it into `graph_export.json` matching the template's expected shape exactly.

**Reference files:**
- `tiers_and_ata.md` (status colour map below)
- `severity_matrix.md`

**Inputs:** every table written by phases 0-9.

---

## ANTI-CHEAT RULE

**`graph_export.json` is a database query result, NOT a Python dict literal.** Past runs hand-wrote a 30-line dict and called it done. Empty graph result.

If your `phase10_export.py` contains a literal like:

```python
export_data = {
    "asset": {"id": "asset_1", ...},   # ← BAD. Read from `assets` table.
    "nodes": [{"id": "..."}],          # ← BAD. Build from query results.
}
```

It is **wrong**. Delete it and re-read this file.

Every field below comes from an explicit SQL query against `graph.db`.

---

## Required keys (template depends on every one)

```
graphData.asset                    object — registration, msn, type_designation, operator, tsn, csn, dossier_date
graphData.stats                    object — total_components, total_events, total_findings,
                                            components_by_tier, components_by_status,
                                            documents_by_type, evidentiary_weight_breakdown,
                                            findings_by_severity
graphData.nodes                    array  — { id, label, group, shape, size, color, tier, status, data }
graphData.edges                    array  — { id, from, to, color, width, dashes, event_type, title }
graphData.events                   object — { component_id: [event, ...] }
graphData.findings                 object — { component_id: [finding, ...] }
graphData.findings_summary         object — by-severity counts and lists (Phase 9)
graphData.doc_nodes / doc_edges    array  — Documents view
graphData.ata_nodes / ata_edges    array  — ATA view
graphData.time_nodes / time_edges  array  — Time view
graphData.lease_return_state       object — Phase 6.5 → drives lease-return banner
graphData.priority_items           array  — Phase 6.5 → drives Critical Items panel
graphData.mandatory_checklist      object — Phase 8 → drives Mandatory Checklist panel
graphData.verification_stats       object — Phase 7.5 → drives Audit Quality panel
```

---

## Building each section

### `asset`
```sql
SELECT id, asset_kind, subtype, type_designation, tcds, yom, msn, registration,
       operator, owner, primary_serial, state, tsn, csn, tsn_confidence,
       csn_confidence, dossier_date FROM assets;
```

### `stats`
```sql
SELECT (SELECT COUNT(*) FROM pages)       AS total_pages,
       (SELECT COUNT(*) FROM documents)   AS total_documents,
       (SELECT COUNT(*) FROM components)  AS total_components,
       (SELECT COUNT(*) FROM events)      AS total_events,
       (SELECT COUNT(*) FROM stamps)      AS total_stamps,
       (SELECT COUNT(*) FROM findings WHERE status='open') AS total_findings_open;

SELECT tier, COUNT(*) FROM components GROUP BY tier;
SELECT status, COUNT(*) FROM components GROUP BY status;
SELECT document_type, COUNT(*) FROM documents GROUP BY document_type;
SELECT evidentiary_weight, COUNT(*) FROM pages GROUP BY evidentiary_weight;
SELECT severity, COUNT(*) FROM findings WHERE status='open' GROUP BY severity;
```

### `nodes`

Three categories:

1. **Asset node** — one row from `assets`. `id = "asset::<id>"`, `tier = "AIRCRAFT_CENTER"`, `shape = "star"`, `size = 40`, color from status.
2. **Tier groups** — one node per tier in `expected_tiers`. `id = "tier::<TIER>"`, `_status = "TIER_GROUP"`, `shape = "dot"`, `size = 30`.
3. **Components** — one row per `components`. `id = component.id`, `tier = component.tier`, `status = component.status`, `data = { canonical_pn, installed_sn, position, is_llp, remaining_cycles, remaining_hours, last_form1_file, last_form1_date, finding_count }`.

Status colour map (use exactly this — the template's STATUS_COLORS reads it):

```
CLOSED              #4CAF50
PARTIAL             #FF9800
GAP                 #F44336
INSTALLED_AT_MFG    #2196F3
DISCOVERED          #9E9E9E
TIER_GROUP          #5C6BC0
```

### `edges`

Build from `edges` (universal) + `asset_relations` (the structural ones):

```sql
SELECT id, source_id AS "from", target_id AS "to", edge_type, confidence, evidence_quote AS title
FROM edges
WHERE edge_type IN ('HAS_TIER', 'BELONGS_TO_TIER', 'PART_OF', 'INSTALLED_ON',
                    'INSTALLATION', 'REMOVAL', 'OVERHAUL', 'INSPECTION', 'SHOP_VISIT',
                    'SB_COMPLIANCE', 'AD_COMPLIANCE', 'PART_REPLACED', 'RELEASE_TO_SERVICE');
```

Edge colour by `event_type` (template's EDGE_COLORS):
```
installation        #2196F3
removal             #F44336
overhaul            #4CAF50
inspection          #FF9800
shop_visit          #9C27B0
sb_compliance       #00BCD4
ad_compliance       #00ACC1
release_to_service  #8BC34A
membership          #444444  (HAS_TIER / BELONGS_TO_TIER)
references          #888888
```

Confidence styling: `high` → solid, `medium` → dashed `[5,5]`, `low` → dotted `[2,4]`.

### `events` (keyed by component_id)
```sql
SELECT component_id, id AS event_id, event_type, event_date, description,
       task_compliance_status, task_reference, file_name, page_index, text_evidence
FROM events
ORDER BY component_id, event_date;
```
Group rows by `component_id` into `{ component_id: [event, ...] }`.

### `findings` (keyed by component_id, only open)
```sql
SELECT target_id AS component_id, id, finding_type, severity, original_severity,
       severity_downgrade_reason, description, what_auditor_needs,
       file_name, page_index, status, verification_strategy
FROM findings
WHERE status = 'open' AND target_kind = 'COMPONENT'
ORDER BY target_id, severity;
```

### `findings_summary` — read Phase 9's payload (or recompute).

### `doc_nodes` / `doc_edges` — for Documents view
- Node per `documents` row.
- Edge per `edges` where `source_kind = 'DOCUMENT'` or `target_kind = 'DOCUMENT'`.

### `ata_nodes` / `ata_edges` — for ATA view
- Node per `ata_chapters` row.
- Edge per `edges` where `edge_type = 'ASSIGNED_ATA'` or `edge_type = 'PAGE_REFERENCES'` to ATA.

### `time_nodes` / `time_edges` — for Time view
```sql
SELECT c.id, c.canonical_pn || ' (' || c.installed_sn || ')' AS label,
       c.position, ar.valid_from AS install_date, ar.valid_to AS removal_date,
       c.tier, c.status
FROM components c
LEFT JOIN asset_relations ar
       ON ar.from_id = c.id AND ar.relation_type = 'installed_on';

SELECT ar.from_id AS "from", ar.to_id AS "to", 'replaced_by' AS edge_type,
       ar.valid_from AS date, ar.evidence_file, ar.evidence_page
FROM asset_relations ar
WHERE ar.relation_type = 'replaced_by';
```

### `lease_return_state` — Phase 6.5 row
```sql
SELECT * FROM lease_return_state;
```

### `priority_items` — Phase 6.5 rows, joined to component label
```sql
SELECT pi.*, c.canonical_pn || ' (' || c.installed_sn || ')' AS component_label
FROM priority_items pi
LEFT JOIN components c ON c.id = pi.component_id
ORDER BY pi.rank;
```

### `mandatory_checklist` — Phase 8 payload (file or DB).

### `verification_stats` — Phase 9 payload (file or compute).

---

## MANDATORY VERIFICATION

After writing `graph_export.json`, validate:

```python
import json
data = json.load(open('graph_export.json'))

required_keys = ['asset', 'stats', 'nodes', 'edges', 'events', 'findings',
                 'findings_summary', 'doc_nodes', 'doc_edges', 'ata_nodes',
                 'ata_edges', 'time_nodes', 'time_edges', 'lease_return_state',
                 'priority_items', 'mandatory_checklist', 'verification_stats']
for k in required_keys:
    assert k in data, f"Missing required key: {k}"

assert data['asset']['id'].startswith('asset::')
assert data['stats']['total_components'] > 0
assert data['stats']['total_events'] > 0
assert len(data['nodes']) >= data['stats']['total_components']
assert len(data['edges']) > 0
```

Append to `progress.log`:

```
- graph_export.json size                        : <bytes>
- nodes count                                   : <N>   (must equal asset+tiers+components)
- edges count                                   : <N>   (must be > 0)
- events keyed by component                     : <N keys>
- findings keyed by component                   : <N keys>
- mandatory_checklist items present             : 12
```

**STOP conditions:**

- `total_components == 0` while `count(components)` in DB is > 0 → query was wrong.
- Any required key missing.
- `nodes` array doesn't include the asset node.
- `nodes` count is hardcoded (e.g. exactly 3) → cheating.
- The file is small enough to be a literal (< 5 KB for a real dossier) — means the agent inlined a stub.
