# PHASE 7 — Component Investigation Loop

**Intent.** Walk every component's events chronologically; raise `:Finding` nodes against the **Investigation Discipline checklist** (`references/investigation_discipline.md`) and the **Severity Matrix** (`references/severity_matrix.md`). Every finding must trace to a page via `:EVIDENCED_BY` (the DAL enforces this).

**Reference files (load all):**
- `references/investigation_discipline.md` — the 9-step checklist for any "missing" finding
- `references/severity_matrix.md` — Level 1 / 2 / 3 mapping per component class
- `references/finding_types.md` — closed list of category strings
- `references/data_quality_rules.md`

**Inputs:** the post-Phase-6.5 graph.

**Style:** **Judgement.** YOU walk each component. Per component, you Read evidence pages with the Read tool, run the Investigation Discipline checklist, and write a reasoning paragraph. **Not a single mechanical script.**

A mechanical-only Phase 7 produces 500 findings, 30 of which are real. A judgement Phase 7 produces 50 findings, 50 of which are real. Aim for the latter.

---

## What this phase produces

| Node | Properties |
|---|---|
| `:Finding` | `severity` ∈ {`level_1`, `level_2`, `level_3`}, `category` (closed enum from references/finding_types.md), `title`, `description` (≥80 chars, must cite page), `recommended_action`, `status="OPEN"` |
| `:AuditRun` (one per Phase 7 run) | `audit_snapshot_date`, `sparengine_version` |

Edges:
- `:Finding-[:EVIDENCED_BY {quote}]->:Page`           (golden rule, enforced by DAL)
- `:Component-[:HAS_FINDING]->:Finding`               (or `:Asset-[:HAS_FINDING]->:Finding` for asset-level)
- `:Finding-[:FLAGS {severity, category}]->:<target>`  (the specific node being flagged)
- `:Finding-[:PRODUCED_BY]->:AuditRun`

---

## Steps

### 1. Bootstrap + open AuditRun

```python
from datetime import datetime
from graph_dal import FindingSeverity
from graph_dal.finding import write_audit_run, write_finding
from graph_dal.cite import cite_node, format_citation
from graph_dal.fulltext import search_pages, escape_lucene

run_id = f"audit::phase7::{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        write_audit_run(
            tx, asset_id=asset_id, value=run_id,
            audit_snapshot_date_iso=datetime.utcnow().date().isoformat(),
            sparengine_version="phase7-judgement-1",
        )
        tx.commit()
```

### 2. Pull the component-walk list — prioritised by Phase 6.5

```cypher
MATCH (c:Component {asset_id: $aid})
OPTIONAL MATCH (c)-[:HAS_PRIORITY_ITEM]->(pi:PriorityItem)
WITH c, pi
ORDER BY
    CASE coalesce(pi.urgency, 'informational')
        WHEN 'immediate' THEN 0
        WHEN 'within_30d' THEN 1
        WHEN 'within_90d' THEN 2
        ELSE 3
    END,
    c.canonical_pn, c.installed_sn
RETURN c.value AS uid, c.canonical_pn AS pn, c.installed_sn AS sn,
       c.is_llp AS is_llp, c.is_overhaul AS is_overhaul,
       c.ata_chapter AS ata, c.tsn AS tsn, c.csn AS csn
```

### 3. Per-component walk (the judgement core)

For each component:

#### 3a. Pull all evidence + events for this component

```cypher
MATCH (c:Component {asset_id: $aid, value: $cuid})
OPTIONAL MATCH (c)-[:EVIDENCED_BY]->(p:Page)<-[:HAS_PAGE]-(d:Document)
OPTIONAL MATCH (e:Event)-[:AFFECTED]->(c)
OPTIONAL MATCH (e)-[:EVIDENCED_BY]->(ep:Page)<-[:HAS_PAGE]-(ed:Document)
RETURN c, collect(DISTINCT {file: d.file_name, page: p.page_index}) AS evidence,
       collect(DISTINCT {kind: e.kind, date: e.event_date, file: ed.file_name, page: ep.page_index}) AS events
```

#### 3b. Run the Investigation Discipline checklist

For any finding category that involves "missing X" (FORM1_MISSING, AD_COMPLIANCE_UNVERIFIED, etc.), you MUST run all 9 disciplines from `references/investigation_discipline.md` before raising the finding:

1. **wo_pages** — search the work-package pages for the component
2. **sn_alone** — fulltext search for the SN alone (`graph_dal.fulltext.search_pages`)
3. **alt_pn** — search alternate PNs (via `:HAS_ALTERNATE_PN`)
4. **filename_pn** — Documents whose file_name contains the PN
5. **filename_sn** — Documents whose file_name contains the SN
6. **batch_range** — check `:BatchNumber` nodes whose sn_range covers this SN
7. **page_neighbourhood** — pages near a known evidence page (±5)
8. **siblings** — sibling SNs under the same canonical_pn
9. **oem_typical** — known OEM filing patterns (from references/data_quality_rules.md)

Implement each as a small helper function. Use `graph_dal.fulltext.search_pages(session, asset_id=..., query=..., limit=10)` for the Lucene-backed searches.

If ANY discipline produces a hit, the missing-X is found — do NOT raise the finding (or raise a downgraded `*_LOCATED_LATE` informational variant).

#### 3c. Reason out loud

Emit a paragraph of prose in your own assistant text (not in Python `print` — the Read tool / UI need to see it):

```
### [Phase 7] Component PT6C-67C / PCE-KB0117 (engine #1)
The dossier carries a Form 1 issued 2018-11-13 by Pratt & Whitney on
"ATA 71- PT6C-67C - KB0117 ENGINE ASSY.pdf" page 0. The component
history card on page 3 shows TSN=4523, CSN=2845. No subsequent shop
visit report is present, and the LLP table on page 7 lists 4 LLPs with
remaining cycles ≥ 1000. No findings raised — coverage is clean.
```

#### 3d. Write the finding (when raising)

```python
write_finding(
    tx, asset_id=asset_id,
    value=f"finding::FORM1_MISSING::{pn}::{sn}",
    severity=FindingSeverity.LEVEL_2.value,
    category="FORM1_MISSING",
    title=f"Form 1 not located for {pn}/{sn}",
    description=(
        f"Component {component_uid} is LLP/overhaul-tracked but no :Form1 "
        f"with a :RELEASES edge attributing release to this component was "
        f"found. Searched: wo_pages, sn_alone, alt_pn, filename_pn, "
        f"filename_sn, batch_range, page_neighbourhood, siblings, oem_typical "
        f"(9 strategies, 0 hits). Last known reference: {citation_string}."
    ),
    evidence_page_uid=evidence_page_uid,
    evidence_quote=verbatim_quote_from_page,
    recommended_action="Locate the Form 1 covering the installed serial number, or close as 'parted out / not in scope'.",
    flags_label="Component", flags_uid=component_uid,
    component_uid=component_uid,
    audit_run_uid=run_id,
    status="OPEN",
)
```

#### 3e. Write to `decisions.log`

```
[phase7] component::3036041-01::CAE-840837 | FORM1_MISSING raised | discipline:[wo_pages,sn_alone,alt_pn,filename_pn,filename_sn,batch_range,page_neighbourhood,siblings,oem_typical] | evidence_pages_read:3 | severity:1 (matrix=1, no downgrade) | reason: Form 1 covering SN MN742 not located after 9-step search; closest match is SN MN738 in WO-419012 page 12.
```

The orchestrator mechanically checks decisions.log entries match findings written. Missing entries → STOP.

### 4. Common categories (from `references/finding_types.md`)

| Category | When raised | Default severity |
|---|---|---|
| `FORM1_MISSING` | LLP/overhaul component without `:Form1-[:RELEASES]->:Component` (after 9-step search) | level_2 |
| `FORM1_AMBIGUOUS_BINDING` | Stamp `binding_status="ambiguous"` on a Form 1 carrier page | level_3 |
| `LLP_LIMIT_CRITICAL` | `c.tsn / c.life_limit > 0.9` | level_1 |
| `LLP_LIMIT_WARNING` | `c.tsn / c.life_limit > 0.7` | level_2 |
| `AD_COMPLIANCE_UNVERIFIED` | `:AirworthinessDirective` without `:COMPLIES_WITH` edge from any document or work package | level_1 |
| `SB_COMPLIANCE_UNVERIFIED` | `:ServiceBulletin` (alert kind) without `:COMPLIES_WITH` | level_2 |
| `CONTINUITY_BREAK` | gap in component history (no event between install date and dossier date for >2 years on a high-value component) | level_2 |
| `TASK_NOT_CONFIRMED` | event with `task_compliance_status ∈ {listed_but_not_signed, ambiguous}` | level_2 |
| `STAMP_AMBIGUOUS_BINDING` | stamp with `binding_status="ambiguous"` on a primary-evidence page | level_3 |
| `OCR_SUSPECTED` | PN or SN with mid-string spaces or visual confusion | level_3 |
| `CONTEXT_DISCREPANCY` | per-page `metadata.context_discrepancy` set, OR profile-vs-corpus mismatch (Phase 2 may also raise) | level_2 |

### 5. Asset-level findings (raise via `asset_level=True`)

Some categories are asset-level, not component-level:
- `MTS_CONFLICT` — multiple MIS systems referenced (CAMP vs AMOS vs SAP)
- `REGISTRATION_HISTORY_MISSING` — registration_history empty but corpus shows multiple registrations
- `WEIGHT_BALANCE_OUT_OF_DATE` — most recent W&B report > 6 months old

```python
write_finding(
    tx, asset_id=asset_id, value=f"finding::asset::MTS_CONFLICT::main",
    severity=FindingSeverity.LEVEL_2.value,
    category="MTS_CONFLICT", title="...", description="...",
    evidence_page_uid=..., evidence_quote=...,
    asset_level=True, audit_run_uid=run_id,
)
```

---

## What to log

```
== Phase 7 verification ==
- components_walked                       : <N>
- evidence_pages_read (total)             : <N>
- findings_raised                         : <N>
  by severity: level_1=<N>, level_2=<N>, level_3=<N>
  by category: FORM1_MISSING=<N>, LLP_LIMIT_CRITICAL=<N>, AD_COMPLIANCE_UNVERIFIED=<N>, ...
- audit_run_uid                           : audit::phase7::...
- decisions.log lines written             : <N>      ← MUST equal findings_raised
- fact_nodes_no_evidence                  : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="7")
```

Plus:
- `decisions.log` lines for this phase ≈ `:Finding` count (every raised finding has a discipline log line).
- Every `:Finding` has a non-empty `evidence_page_uid` (DAL enforces).
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `decisions.log` line count ≠ findings count — you skipped the discipline log.
- Any finding has `description` shorter than 80 chars — you didn't write the buyer-facing reasoning.
- A LEVEL_1 finding has no Read-tool page-read in the conversation — you raised a critical without reading evidence.
- `count(:Finding)` is suspiciously high (>200 for a typical dossier) — discipline checklist was skipped (the cure for too many findings is more discipline, not lower thresholds).
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase7.py` — verified-working **mechanical stub** (raises FORM1_MISSING for LLP/overhaul components without :RELEASES). For AW139 the stub raised 50 findings; a true judgement Phase 7 (running the 9-step discipline + reading pages with the Read tool) would close most of those and raise a much smaller number of high-quality findings. The stub establishes the DAL contract; the judgement work happens on top.
