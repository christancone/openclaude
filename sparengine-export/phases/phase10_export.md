# PHASE 10 — Graph Export

**Intent.** Read the Neo4j graph; produce the three per-asset deliverables:

1. `graph_export.json` — viz-shape projection (panel HTML reads it).
2. `restore.cypher`    — replayable into any Neo4j Community via `cypher-shell -f restore.cypher`. Sanitised so schema-creation lines don't conflict with an existing schema.
3. `tier_views.cypher` — saved Cypher snippets for Neo4j Browser favourites (one per ATA-derived tier).

Also auto-applies `phases/captions.cypher` so Browser auto-captions on every node label are meaningful.

**Reference files:**
- `tiers_and_ata.md` (the ATA→tier mapping for `tier_views.cypher`)
- `severity_matrix.md`
- `captions.cypher`

**Inputs:** the post-Phase-9 graph.

**Style:** Coding. Pure Cypher reads + APOC export. No judgement.

---

## What this phase produces (in `--workdir`)

| File | Source | Size (typical) |
|---|---|---|
| `graph_export.json` | DAL `export_*` helpers → JSON projection | 1–3 MB |
| `restore.cypher`    | APOC `apoc.export.cypher.query` → sanitised | 5–10 MB |
| `tier_views.cypher` | static template substituted with asset_id | ~3 KB |

---

## ANTI-CHEAT RULE

**`graph_export.json` is a Cypher query result, NOT a Python dict literal.** Past runs hand-wrote a 30-line dict and called it done. Empty graph result.

If your `phase10.py` contains anything like:

```python
export_data = {
    "asset": {"id": "asset_1", ...},   # ← BAD. Read from :Asset.
    "nodes": [{"id": "..."}],          # ← BAD. Build from query results.
}
```

It is **wrong**. Delete it and use the DAL `export_*` helpers.

---

## Steps

### 1. Bootstrap

```python
from graph_dal.export import (
    export_asset, export_edges, export_events_by_component,
    export_findings_by_component, export_lease_return_state,
    export_mandatory_checklist, export_nodes,
    export_priority_items, export_restore_cypher,
    export_stats, sanitize_restore_cypher,
)
from graph_dal.verify import verify_no_fact_orphans
```

### 2. Build the JSON projection

```python
graph_data: dict = {}
with driver.session(database=database_name()) as s:
    graph_data["asset"]               = export_asset(s, asset_id)
    graph_data["stats"]               = export_stats(s, asset_id)
    graph_data["nodes"]               = export_nodes(s, asset_id)            # NO LIMIT
    graph_data["edges"]               = export_edges(s, asset_id)            # NO LIMIT
    graph_data["events"]              = export_events_by_component(s, asset_id)
    graph_data["findings"]            = export_findings_by_component(s, asset_id)
    graph_data["priority_items"]      = export_priority_items(s, asset_id)
    graph_data["mandatory_checklist"] = export_mandatory_checklist(s, asset_id)
    graph_data["lease_return_state"]  = export_lease_return_state(s, asset_id)
```

**Do NOT cap `export_nodes` / `export_edges` with a LIMIT.** A truncated export silently drops Persons / Events / Findings / PriorityItems beyond the cap, breaking the panel HTML rendering.

### 3. Serialise to JSON

Neo4j returns its own DateTime / Date types from `datetime()` calls — install a default encoder:

```python
def _json_default(o):
    iso = getattr(o, "iso_format", None) or getattr(o, "isoformat", None)
    if callable(iso): return iso()
    return str(o)

export_path = workdir / "graph_export.json"
export_path.write_text(
    json.dumps(graph_data, indent=2, default=_json_default),
    encoding="utf-8",
)
```

### 4. Generate restore.cypher (APOC export + sanitise)

APOC writes into `/import` (the `neo4j-import` volume). The sparengine container sees the same volume read-only at `/app/neo4j-import`. Phase 10 reads + sanitises + writes the cleaned copy directly into the workdir.

```python
restore_filename = f"restore-{asset_id}.cypher"
shared_path = Path("/app/neo4j-import") / restore_filename
workdir_path = workdir / "restore.cypher"

with driver.session(database=database_name()) as s:
    export_restore_cypher(s, asset_id, restore_filename)

if shared_path.exists():
    n_commented = sanitize_restore_cypher(shared_path, workdir_path)
    # Comments out CREATE FULLTEXT INDEX / CREATE CONSTRAINT lines.
    # Schema is owned by phases/schema.cypher; replay shouldn't recreate it.
```

`export_restore_cypher` embeds the asset_id directly into the inner query (it's a UUID; no quoting risk) because APOC's `apoc.export.cypher.query` doesn't propagate outer-query parameters into the inner query context.

### 5. Generate tier_views.cypher

```python
TIER_QUERIES = {
    "ENGINE":        "c.ata_chapter STARTS WITH '7'",
    "PROPELLER":     "c.ata_chapter = '61'",
    "ROTOR_SYSTEM":  "c.ata_chapter IN ['62', '64', '66', '67']",
    "TRANSMISSION":  "c.ata_chapter IN ['63', '65']",
    "LANDING_GEAR":  "c.ata_chapter = '32'",
    "AIRFRAME":      "c.ata_chapter IN ['51','52','53','54','55','56','57']",
    "AVIONICS":      "c.ata_chapter IN ['22','23','27','31','34','45']",
    "SYSTEMS":       "c.ata_chapter IN ['21','24','26','28','29','30','33','35','36','38']",
    "INTERIOR":      "c.ata_chapter = '25'",
    "APU":           "c.ata_chapter = '49'",
}

# Render as Cypher comments + queries, write to workdir / "tier_views.cypher"
```

### 6. Apply captions.cypher

After all writes, run `phases/captions.cypher` to ensure every node has a `name` property (so Browser auto-captions display meaningfully). The DAL way:

```python
# Locate captions.cypher in the sparengine-export tree
for parent in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
    cand = parent / "sparengine-export" / "phases" / "captions.cypher"
    if cand.is_file():
        captions_file = cand
        break

# Strip comments, split on `;`, run each statement
raw = captions_file.read_text(encoding="utf-8")
cleaned = "\n".join(
    line for line in raw.splitlines()
    if line.strip() and not line.strip().startswith("//")
)
statements = [s.strip() for s in cleaned.split(";") if s.strip()]
with driver.session(database=database_name()) as s:
    for stmt in statements:
        s.run(stmt).consume()
```

---

## What to log

```
== Phase 10 verification ==
- graph_export.json size                   : <bytes>
- nodes in export                          : <N>
- edges in export                          : <N>
- events keyed by component                : <N>
- findings keyed by component              : <N>
- priority_items                           : <N>
- mandatory_checklist items                : 12
- restore.cypher                           : OK: <bytes> bytes, <N> schema lines commented
- tier_views.cypher                        : <bytes> bytes
- captions.cypher applied                  : OK: <N> statements
- fact_nodes_no_evidence                   : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="10")
```

Plus:
- `graph_export.json` exists, is valid JSON, has all 9 required top-level keys: `asset`, `stats`, `nodes`, `edges`, `events`, `findings`, `priority_items`, `mandatory_checklist`, `lease_return_state`.
- `restore.cypher` exists in the workdir and starts with `:begin` (the APOC cypher-shell format).
- `tier_views.cypher` exists.
- captions step ran (`captions_status == "OK"` in the log).
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `graph_export.json` doesn't exist OR is not valid JSON.
- `nodes < expected_total` (sum of every visible label) — the export was LIMITed accidentally.
- `restore.cypher` doesn't exist OR has 0 schema lines commented (sanitiser didn't run; replay will fail).
- `captions.cypher applied` is `FAILED` or `skipped` — Browser captions will be meaningless.
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase10.py` — verified-working canonical Phase 10. For AW139 it produces:
- `graph_export.json` 2.0 MB (3894 visible nodes, 1182 edges)
- `restore.cypher` 7.6 MB (9339 nodes, 16798 edges, 24 schema lines commented; replay-verified)
- `tier_views.cypher` 2.7 KB (10 tiers)
- `captions.cypher applied: OK: 57 statements`
