# PHASE 5 — Event Hydration (Layer 4)

**Intent.** Stream the dossier CSV, parse `extracted_json.content.events[]`, and write `:Event` nodes anchored to the page each event was extracted from. Where `events[]` is empty (some OCR vintages don't populate it), derive events from `sections[]` and `tables[]`. For each event, look up the affected `:Component` via the OCR's `bound_entities[]`.

**Reference files:**
- `csv_and_ocr.md` (events shape, sections, tables)
- `finding_types.md` (`TASK_NOT_CONFIRMED`)

**Inputs:** the Phase 1–4 graph; the original CSV (re-read to access `extracted_json.events[]`, `sections[]`, `tables[]`).

**Style:** Coding. One Python script. Use `csvs/.../AW139/phase5.py` as reference.

---

## What this phase produces

| Node | Edges out |
|---|---|
| `:Event` (per OCR event or derived row/section) | `:OCCURRED_ON` :Asset (when no component is resolved) or `:AFFECTED {confidence}` :Component, `:EVIDENCED_BY {quote}` :Page, `:ON_DATE {role:"event"}` :Date |
| `:ComponentSnapshot` (when TSN/CSN values present at event time) | `:OF` :Component, `:AT_EVENT` :Event, `:GENERATES` (reverse from :Event) |

Plus optional secondary edges Phase 5 may wire:
- `:Form1-[:RELEASES {block}]->:Component` (when the event is a release_to_service and the bound_entity is a component installed)
- `:CRS-[:CERTIFIES]->:Component|:WorkPackage|:Asset`
- `:Component-[:INSTALLED_AT]->:Event` and `:Component-[:REMOVED_AT]->:Event` for parts-table installation/removal events
- `:Component-[:WAS_INSTALLED_ON {from_date, to_date}]->:Asset` (denormalised summary)

---

## Steps

### 1. Bootstrap + build the component resolver cache

```python
from graph_dal.event import write_event
# (full bootstrap as in OVERVIEW.md)

class ComponentResolver:
    """Build once at start. Index :Component by SN and PN."""
    def __init__(self):
        self.by_sn: dict[str, list[str]] = {}
        self.by_pn: dict[str, list[str]] = {}

    @classmethod
    def build(cls, driver, asset_id):
        r = cls()
        cypher = """
        MATCH (c:Component {asset_id: $aid})
        OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
        OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
        RETURN c.value AS uid, sn.value AS sn, pn.value AS pn
        """
        with driver.session(database=database_name()) as s:
            for record in s.run(cypher, aid=asset_id):
                uid, sn, pn = record["uid"], record["sn"], record["pn"]
                if sn: r.by_sn.setdefault(sn.upper(), []).append(uid)
                if pn: r.by_pn.setdefault(pn.upper(), []).append(uid)
        return r

    def resolve(self, *, sn=None, pn=None):
        """Return (component_uid_or_None, confidence ∈ {high, medium, ambiguous, none})."""
        if sn:
            cands = self.by_sn.get(sn.strip().upper(), [])
            if len(cands) == 1: return cands[0], "high"
            if len(cands) > 1:  return None, "ambiguous"
        if pn:
            cands = self.by_pn.get(pn.strip().upper(), [])
            if len(cands) == 1: return cands[0], "medium"
            if len(cands) > 1:  return None, "ambiguous"
        return None, "none"
```

SN match wins (high confidence — SNs are typically unique). Falls back to PN-only if no SN — but only when exactly one component carries that PN; otherwise ambiguous.

### 2. Stream the CSV; for each row:

```python
df_iter = pd.read_csv(csv_path, chunksize=args.chunksize)

with driver.session(database=database_name()) as session:
    for chunk_idx, chunk in enumerate(df_iter):
        with session.begin_transaction() as tx:
            for _, row in chunk.iterrows():
                ext = orjson.loads(row["extracted_json"])
                content = ext.get("content") or {}
                page_uid = str(row["id"])

                events = content.get("events") or ext.get("events") or []
                sections = content.get("sections") or ext.get("sections") or []
                tables = content.get("tables") or ext.get("tables") or []
                page_entities = content.get("entities") or ext.get("entities") or []

                # Build entity index for bound_entities resolution
                entities_by_id = {
                    e.get("entity_id"): e for e in page_entities
                    if isinstance(e, dict) and e.get("entity_id")
                }

                # 1. Native events (the canonical OCR surface)
                for ev_i, ev in enumerate(events):
                    write_native_event(tx, asset_id, page_uid, ev_i, ev,
                                       resolver, entities_by_id)

                # 2. Derived events from sections + tables (most important
                #    when content.events[] is empty)
                for spec in derive_section_events(sections, page_uid):
                    write_derived_event(tx, asset_id, spec, resolver, entities_by_id)
                for spec in derive_table_events(tables, page_uid):
                    write_derived_event(tx, asset_id, spec, resolver, entities_by_id)
            tx.commit()
```

### 3. Native events from `content.events[]`

For each event entry:

```python
event_uid = f"event::{page_uid}::{ev.get('event_id') or f'evt_{ev_i}'}"
ocr_event_type = ev.get("event_type") or "other"
kind = OCR_EVENT_TYPE_MAP.get(ocr_event_type, "compliance")
# OCR_EVENT_TYPE_MAP — see below

description = ev.get("description") or ""
quote = description[:240] or f"{ocr_event_type} on page {page_uid[:8]}"
date_iso = ev.get("date")
task_status = ev.get("task_compliance_status")
task_reason = ev.get("compliance_status_reason")
task_ref = ev.get("task_reference")

# Resolve component via bound_entities (subject / part_installed / part_removed)
comp_uid, comp_conf = resolve_via_bound_entities(resolver, ev, entities_by_id)

write_event(
    tx, asset_id=asset_id, value=event_uid,
    kind=kind,
    evidence_page_uid=page_uid, evidence_quote=quote,
    date_iso=date_iso,
    description=description,
    task_reference=task_ref,
    task_compliance_status=task_status,
    compliance_status_reason=task_reason,
    asset_event=(comp_uid is None),
    component_uid=comp_uid,
    affected_confidence=comp_conf if comp_uid else None,
)
```

Where:

```python
OCR_EVENT_TYPE_MAP = {
    "task_performed":         "compliance",
    "inspection":             "inspection",
    "component_installation": "install",
    "component_removal":      "removal",
    "sb_compliance":          "compliance",
    "ad_compliance":          "compliance",
    "modification":           "compliance",
    "repair":                 "compliance",
    "shop_visit":             "shop_visit",
    "release_to_service":     "compliance",
    "other":                  "compliance",
}
```

**Critical:** `task_compliance_status` is already evaluated by the OCR. Do NOT re-derive from text. If status is `listed_but_not_signed` or `ambiguous`, that becomes a `TASK_NOT_CONFIRMED` finding in Phase 7.

### 4. Derived events from `sections[]`

The OCR's `content.sections[]` includes typed blocks. Map each kind to an event:

| Section kind | Event(s) emitted | Notes |
|---|---|---|
| `defect_entry` | TWO events: `kind="inspection"` (the defect) + `kind="compliance"` (the corrective_action) | Both linked to the same WO |
| `inspection_finding` | ONE `kind="inspection"` | |
| `certification_statement` | ONE `kind="compliance"` (descriptor: `release_to_service`) | The bound stamp's `person_name` becomes the signing person; `approval_number` becomes a `:Requirement` edge in Phase 6 |
| `work_description` | ONE `kind="compliance"` | Description on parent event |
| `corrective_action` | ONE `kind="compliance"` | Promotes a defect_entry to fully resolved |

```python
SECTION_KIND_MAP = {
    "certification_statement": ("compliance", "release_to_service"),
    "defect_entry":            ("inspection", "defect"),       # also emit corrective_action
    "inspection_finding":      ("inspection", "inspection_finding"),
    "work_description":        ("compliance", "work_description"),
    "corrective_action":       ("compliance", "corrective_action"),
}
```

### 5. Derived events from `tables[]`

Map table names to event kinds. Heuristic — match name against lowercase substrings:

```python
TABLE_KIND_PATTERNS = [
    ("limited life",          "compliance", "llp_status"),
    ("life limited",          "compliance", "llp_status"),
    ("llp",                   "compliance", "llp_status"),
    ("assembly historical",   "compliance", "assembly_history"),
    ("activity record",       "compliance", "activity_record"),
    ("mandatory directive",   "compliance", "ad_compliance"),
    ("optional directive",    "compliance", "sb_compliance"),
    ("directives compliance", "compliance", "directive_compliance"),
    ("installation",          "install",    "parts_install"),
    ("removal",               "removal",    "parts_removal"),
    ("incoming inspection",   "inspection", "incoming_inspection"),
    ("outgoing inspection",   "inspection", "outgoing_inspection"),
]
```

For each row of each matching table, emit one event. Resolve component via row cells (look for S/N or P/N columns by header name):

```python
def resolve_component_from_row(resolver, headers, cells):
    sn_col, pn_col = None, None
    for i, h in enumerate(headers):
        hl = h.lower()
        if sn_col is None and ("s/n" in hl or "serial" in hl): sn_col = i
        if pn_col is None and ("p/n" in hl or "part" in hl):   pn_col = i
    sn = cells[sn_col].strip() if sn_col is not None and sn_col < len(cells) else ""
    pn = cells[pn_col].strip() if pn_col is not None and pn_col < len(cells) else ""
    if sn:
        uid, conf = resolver.resolve(sn=sn)
        if uid: return uid, conf
    if pn:
        uid, conf = resolver.resolve(pn=pn)
        if uid: return uid, conf
    return None, "none"
```

For specific table types:
- **Parts tables** with `S/N Off` and `S/N On` → emit `kind="removal"` per Off and `kind="install"` per On. After writing, also wire `:Component-[:INSTALLED_AT]->:Event` / `:Component-[:REMOVED_AT]->:Event` via `link_component_installed_at` / `link_component_removed_at` from `graph_dal.event`.
- **LLP tables** → one event per row. If the row has a "Status: Complied" column, `kind="compliance"`; if "Listed", `kind="inspection"`. Hydrate component TSN/CSN/Life/Remaining from row columns and write a `:ComponentSnapshot` (see step 7).
- **SB/AD compliance tables** → one event per row, mapping the `Status` column to `task_compliance_status`.
- **Document control tables** (`Task #, Description, Raised stamp, Cleared stamp`) → cross-check that every listed task has a corresponding event already written; flag listed-but-uncovered tasks.

### 6. Component fallback resolution

If `bound_entities[]` doesn't resolve and the row cells didn't either, fall back to **page-level resolution**: if the page mentions exactly one component (one PN×SN pair that resolved), attribute the event to that component with confidence `medium`. If multiple resolve, leave `comp_uid=None` and emit `:Event-[:OCCURRED_ON]->:Asset` (asset-level event).

### 7. ComponentSnapshot writes (LLP tables, shop visits)

When TSN/CSN/TSO/CSO values are present in a table row at event time:

```python
write_component_snapshot(
    tx, asset_id=asset_id,
    value=f"snapshot::{event_uid}::{component_uid}",
    component_uid=component_uid,
    evidence_page_uid=page_uid, evidence_quote=row_quote,
    date_iso=row_date, tsn=tsn, csn=csn, tso=tso, cso=cso,
    event_uid=event_uid,    # auto-wires :GENERATES, :AT_EVENT, :OF
)
```

### 8. Form1 → Component RELEASES + CRS → Component CERTIFIES (when applicable)

When a `release_to_service` event resolves to a Component AND the page CARRIES a Form1 / CRS, wire the relationship:

```python
from graph_dal.event import link_form1_releases_component, link_crs_certifies

# Find the Form1 on this page
form1_on_page = session.run(
    "MATCH (p:Page {asset_id: $aid, value: $puid})-[:CARRIES]->(f:Form1) "
    "RETURN f.value AS uid LIMIT 1",
    aid=asset_id, puid=page_uid,
).single()
if form1_on_page and component_uid:
    link_form1_releases_component(
        tx, asset_id=asset_id,
        form1_uid=form1_on_page["uid"],
        component_uid=component_uid,
        block="14a",   # if known from the OCR
    )
```

### 9. WAS_INSTALLED_ON summary edge

After processing all events for the chunk, refresh the denormalised summary edge:

```python
from graph_dal.event import link_was_installed_on
# For each Component with an INSTALLED_AT or REMOVED_AT edge written this chunk:
link_was_installed_on(
    tx, asset_id=asset_id, component_uid=component_uid,
    from_date_iso=install_date, to_date_iso=removal_date,
)
```

---

## Performance notes

- Re-parsing `extracted_json` is the slowest part. If memory permits, you can stash the parsed JSON in Phase 1 — but for ≤50k-row dossiers, re-parsing in Phase 5 is acceptable (~2 minutes).
- One transaction per chunk (~1000 events).

---

## What to log

```
== Phase 5 verification ==
- rows_processed                          : <N>
- :Event count (live)                     : <N>
- events_with_component_link              : <N>     # ≥ 40% of total expected
- events_with_date                        : <N>
- :ComponentSnapshot count                : <N>
- fact_nodes_no_evidence                  : 0
- event kinds:
    install / removal / overhaul / inspection / shop_visit / compliance / ...
- compliance status:
    signed_off / listed_but_not_signed / marked_not_required / deferred / ambiguous
- component-resolution confidence:
    high / medium / ambiguous / none
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="5")
```

Plus these counts:

- `count(:Event)` > 0
- `count(:Event)` ratio to `count(:Page)` ≈ 0.3..3.0 typical
- `count(:Event WHERE component link exists)` ≥ 0.40 × `count(:Event)` (40% threshold; below this, bound_entities resolution is broken)
- distinct event_kind ≥ 3 (install, inspection, compliance — at minimum)
- `count(:Event WHERE kind='compliance') / count(:Event) < 0.95` (if 95%+ are compliance, you skipped sections/tables)
- `fact_nodes_no_evidence == 0`

---

## STOP conditions

- `count(:Event) == 0`. (Even with no `events[]`, sections + tables should produce some.)
- Events with `component_link` < 40% of events — `bound_entities` resolution is broken or you didn't read `entities_by_id`.
- `distinct event_kind` count < 3 — only one event source iterated. Re-read step 4/5.
- 95%+ of events are `kind='compliance'` — same cheat.
- `fact_nodes_no_evidence > 0` — golden rule violated.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase5.py` — verified-working canonical Phase 5. For AW139 it produces 730 events derived from sections + tables (this OCR vintage's `content.events[]` is empty; the derivation path is what carries the load).
