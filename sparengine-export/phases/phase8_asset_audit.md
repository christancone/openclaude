# PHASE 8 — Asset-Level Investigation

**Intent.** Run the **mandatory checklist** at the asset level. Every item gets either an explicit "present" mark OR an explicit asset-level `:Finding`. No silent omissions.

The checklist is stored as a JSON property on `:Asset.mandatory_checklist` so the panel HTML can render it directly.

**Reference files:**
- `finding_types.md`
- `severity_matrix.md`

**Inputs:** the post-Phase-7.5 graph; `asset_profile.json`.

**Style:** **Judgement.** Each item requires you to read the relevant pages and decide whether the dossier covers it. Don't write `"present"` based on document_type counts alone — that catches existence but not currency or completeness.

---

## What this phase produces

| Property | On |
|---|---|
| `:Asset.mandatory_checklist` | JSON object: `{<item_name>: "present" | "missing" | "stale" | "uncertain"}` |
| `:Asset.checklist_phase` | `"phase8-judgement-1"` (set explicitly so audit trail records the source) |
| New `:Finding` (asset-level) per missing/stale item | with `category` indicating which checklist item failed |

---

## The 12 mandatory checklist items

```python
CHECKLIST_ITEMS = [
    "asset_orientation",          # asset_profile.json populated, type confirmed
    "logbooks_present",            # at least one *_logbook document
    "form1_coverage",              # at least one easa_form_one / faa_form_8130 / tcca_form_one / dual_release
    "llp_status_current",          # engine_llp_status_sheet or life_limited_parts_status, dated within last 12 months
    "ad_compliance_summary",       # airworthiness_directive_compliance or ad_status_report
    "sb_compliance_summary",       # service_bulletin_compliance or sb_status_report
    "modification_inventory",      # modification_record(s) covering the asset
    "weight_balance_current",      # weight_and_balance_report dated within last 6 months (most recent)
    "registration_history",        # asset_profile.registration.history populated
    "lease_history",               # operator/owner timeline (often unavailable; mark "uncertain")
    "ndt_records",                 # borescope_inspection_report / dent_and_buckle_chart / structural_repair_report
    "dent_buckle_chart",           # dent_and_buckle_chart specifically
]
```

---

## Steps

### 1. For each item, do the mechanical query AND the judgement read

```python
import json
from datetime import date, timedelta

with driver.session(database=database_name()) as s:
    doc_types = {r["dt"]: r["n"] for r in s.run(
        "MATCH (d:Document {asset_id: $aid}) WHERE d.document_type IS NOT NULL "
        "RETURN d.document_type AS dt, count(*) AS n", aid=asset_id
    ) if r["dt"]}
    n_form1 = s.run("MATCH (:Form1 {asset_id: $aid}) RETURN count(*) AS n",
                    aid=asset_id).single()["n"]
    n_llp = s.run("MATCH (c:Component {asset_id: $aid}) WHERE c.is_llp = true "
                  "RETURN count(c) AS n", aid=asset_id).single()["n"]
```

#### Per-item logic

```python
coverage = {}

# 1. asset_orientation
coverage["asset_orientation"] = "present" if profile.get("asset_class") else "missing"

# 2. logbooks_present
coverage["logbooks_present"] = "present" if any(
    k.endswith("logbook") for k in doc_types
) else "missing"

# 3. form1_coverage — has Form 1s + each LLP/overhaul component has a RELEASES edge
if n_form1 == 0:
    coverage["form1_coverage"] = "missing"
else:
    # Read a few Form 1 pages to confirm they cover the right components.
    # Judgement check, not just count.
    coverage["form1_coverage"] = "present"

# 4. llp_status_current — has the doc AND is dated within 12 months
if doc_types.get("engine_llp_status_sheet", 0) or doc_types.get("life_limited_parts_status", 0):
    # Find the most recent dated LLP page
    latest = s.run("""
        MATCH (d:Document {asset_id: $aid})-[:HAS_PAGE]->(p:Page)-[:ON_DATE]->(dt:Date)
        WHERE d.document_type IN ['engine_llp_status_sheet', 'life_limited_parts_status']
        RETURN max(dt.iso) AS latest
    """, aid=asset_id).single()
    twelve_months_ago = (date.today() - timedelta(days=365)).isoformat()
    if latest and latest["latest"] and latest["latest"] >= twelve_months_ago:
        coverage["llp_status_current"] = "present"
    else:
        coverage["llp_status_current"] = "stale"
else:
    coverage["llp_status_current"] = "missing"

# 5. ad_compliance_summary
coverage["ad_compliance_summary"] = "present" if (
    doc_types.get("airworthiness_directive_compliance", 0) or
    doc_types.get("ad_status_report", 0)
) else "missing"

# 6. sb_compliance_summary — same pattern
coverage["sb_compliance_summary"] = "present" if (
    doc_types.get("service_bulletin_compliance", 0) or
    doc_types.get("sb_status_report", 0)
) else "missing"

# 7. modification_inventory
coverage["modification_inventory"] = "present" if (
    doc_types.get("modification_record", 0)
) else "missing"

# 8. weight_balance_current
if doc_types.get("weight_and_balance_report", 0):
    # Same kind of dated check as LLP
    six_months_ago = (date.today() - timedelta(days=183)).isoformat()
    latest = s.run("""
        MATCH (d:Document {asset_id: $aid})-[:HAS_PAGE]->(p:Page)-[:ON_DATE]->(dt:Date)
        WHERE d.document_type = 'weight_and_balance_report'
        RETURN max(dt.iso) AS latest
    """, aid=asset_id).single()
    if latest and latest["latest"] and latest["latest"] >= six_months_ago:
        coverage["weight_balance_current"] = "present"
    else:
        coverage["weight_balance_current"] = "stale"
else:
    coverage["weight_balance_current"] = "missing"

# 9. registration_history
reg = profile.get("registration") or {}
coverage["registration_history"] = "present" if reg.get("history") else "missing"

# 10. lease_history — usually uncertain; only "present" if explicit
coverage["lease_history"] = "uncertain"

# 11. ndt_records
coverage["ndt_records"] = "present" if (
    doc_types.get("borescope_inspection_report", 0) or
    doc_types.get("inspection_report", 0) or
    doc_types.get("structural_repair_report", 0)
) else "missing"

# 12. dent_buckle_chart
coverage["dent_buckle_chart"] = "present" if (
    doc_types.get("dent_and_buckle_chart", 0)
) else "missing"
```

### 2. Persist the checklist on :Asset

```python
with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (a:Asset {asset_id: $aid})
            SET a.mandatory_checklist = $cov,
                a.checklist_phase = 'phase8-judgement-1'
        """, aid=asset_id, cov=json.dumps(coverage)).consume()
        tx.commit()
```

### 3. Raise asset-level findings for missing / stale items

For each `coverage[item] in {"missing", "stale"}`, raise a `:Finding` keyed `f"finding::asset::{item.upper()}"` with the corresponding category from `finding_types.md`. Use a representative evidence page (e.g. the first page of the asset_profile.json reference, or the asset's first :Page).

```python
SEVERITY_BY_ITEM = {
    "ad_compliance_summary": FindingSeverity.LEVEL_1.value,
    "llp_status_current":     FindingSeverity.LEVEL_1.value,
    "form1_coverage":         FindingSeverity.LEVEL_2.value,
    # ... etc
}

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        for item, state in coverage.items():
            if state in ("missing", "stale"):
                write_finding(
                    tx, asset_id=asset_id,
                    value=f"finding::asset::CHECKLIST::{item}",
                    severity=SEVERITY_BY_ITEM.get(item, FindingSeverity.LEVEL_2.value),
                    category=item.upper(),
                    title=f"Mandatory checklist: {item} is {state}",
                    description=(
                        f"Phase 8 mandatory-checklist item '{item}' is "
                        f"'{state}' for this asset. State derived from "
                        f"document_type counts and dated-document recency check."
                    ),
                    evidence_page_uid=evidence_page,    # asset's first page or representative
                    evidence_quote=f"Checklist item {item} = {state}",
                    asset_level=True,
                    audit_run_uid=run_id,
                )
        tx.commit()
```

### 4. Reason out loud per item

```
### [Phase 8] llp_status_current
The dossier contains 7 `engine_llp_status_sheet` pages. The most recent
dated page in those documents is 2024-03-15. Today's snapshot date is
2026-05-06 — that's 2 years stale, well over the 12-month freshness
window. Marking 'stale' and raising LLP_STATUS_STALE finding (Level 1).
```

---

## What to log

```
== Phase 8 verification ==
- mandatory_checklist items                : 12
  asset_orientation              : present
  logbooks_present               : present
  form1_coverage                 : present
  llp_status_current             : stale
  ad_compliance_summary          : present
  ...
- coverage: 6/12 items present
- asset-level findings raised this phase   : <N>
- fact_nodes_no_evidence                   : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="8")
```

Plus:
- `:Asset.mandatory_checklist` is set and is parseable JSON with all 12 items.
- For every "missing" or "stale" item, an asset-level `:Finding` exists.
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `:Asset.mandatory_checklist` is null — Phase 8 didn't run.
- Number of items in checklist != 12.
- A "missing" item without a corresponding asset-level finding.
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase8.py` — verified-working **mechanical stub** that derives coverage from document_type counts only (no dated-recency checks, no judgement reads). For AW139 the stub reports 6/12 items present. A judgement Phase 8 layers the dated-recency checks + page reads on top.
