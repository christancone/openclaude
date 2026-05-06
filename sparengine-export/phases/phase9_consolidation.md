# PHASE 9 — Finding Consolidation

**Intent.** Roll up duplicate findings, collapse batch-covered findings, harmonise severities, and produce the final findings_summary that Phase 10 exports.

**Reference files:**
- `severity_matrix.md`
- `finding_types.md`

**Inputs:** all `:Finding` nodes from Phases 7 + 7.5 + 8.

**Style:** Mixed. Roll-up rules are mechanical (e.g. "if 5 LLPs from the same engine all have FORM1_MISSING, roll into one engine-level finding"). Deciding whether two findings should merge requires reading their descriptions.

---

## What this phase produces

It MODIFIES existing findings (status, severity, parent linkage) and writes summary properties:

- `:Finding.status` updates: `OPEN → ROLLED_UP` (when merged into a parent finding) or `OPEN → CLOSED_DUPLICATE`
- `:Finding.parent_finding_uid` (string) when one finding rolls into another
- `:Asset.findings_summary` (JSON property) — pre-aggregated counts for the panel HTML

It does NOT raise new findings — those came from Phases 7/7.5/8.

---

## Steps

### 1. Bootstrap

```python
from graph_dal.cite import cite_node
```

### 2. Mechanical roll-ups

For each rule, query the relevant findings, decide which should merge, and update statuses.

#### Rule A — sibling-PN consolidation

If multiple `:Component` nodes share the same `canonical_pn` and ALL have `FORM1_MISSING` findings, roll them into one `FORM1_MISSING_SIBLING_GROUP` finding:

```cypher
MATCH (c:Component {asset_id: $aid})-[:HAS_FINDING]->(f:Finding)
WHERE f.category = 'FORM1_MISSING' AND f.status = 'OPEN'
WITH c.canonical_pn AS pn, collect(f) AS group_findings, count(*) AS n
WHERE n > 1
RETURN pn, group_findings, n
```

For each group, pick a representative finding to keep, mark the rest as `ROLLED_UP` with `parent_finding_uid` pointing to the kept one.

#### Rule B — batch-covered findings

If a `:BatchNumber` covers an SN that has a FORM1_MISSING finding, AND the batch has its own Form 1 with `:RELEASES` to any sibling component, close the FORM1_MISSING as `CLOSED_DUPLICATE` (the batch certificate covers it).

```cypher
MATCH (c:Component {asset_id: $aid})-[:HAS_FINDING]->(f:Finding {category: 'FORM1_MISSING', status: 'OPEN'})
MATCH (b:BatchNumber {asset_id: $aid})
WHERE c.installed_sn >= b.sn_range_start AND c.installed_sn <= b.sn_range_end
MATCH (b)<-[:CARRIES]-(:Page)<-[:HAS_PAGE]-(:Document)-[:HAS_PAGE]->(:Page)-[:CARRIES]->(form1:Form1)
RETURN c.value, f.value, b.value, form1.value
```

For each match, close the FORM1_MISSING with `closed_reason="batch_covered_by={batch_value}"`.

#### Rule C — severity harmonisation

If multiple findings on the same component disagree on severity, take the highest:

```cypher
MATCH (c:Component {asset_id: $aid})-[:HAS_FINDING]->(f:Finding)
WHERE f.status = 'OPEN'
WITH c, collect(f) AS fs, max(f.severity) AS top_sev   // string max — level_1 < level_2 < level_3 alphabetically; reverse the comparison
RETURN c.value, fs, top_sev
```

Alternatively: don't auto-harmonise; leave per-finding severity and let Phase 10 export the raw distribution.

### 3. Cross-finding judgement merge

For each pair of findings on the same component, read both descriptions. If they describe the same underlying issue (e.g. "FORM1_MISSING" and "TASK_NOT_CONFIRMED" on the same job card), merge:
- Keep the higher-severity one
- Mark the other `ROLLED_UP` with `parent_finding_uid`

Reason out loud per merge:

```
### [Phase 9] Merging FORM1_MISSING + TASK_NOT_CONFIRMED on JC-32-11-04
Both findings flag the same underlying issue: the JobCard wasn't signed off,
which is why no Form 1 was issued. FORM1_MISSING is the symptom; TASK_NOT_CONFIRMED
is the root cause. Keeping TASK_NOT_CONFIRMED (level_2) as the parent;
FORM1_MISSING rolls into it.
```

### 4. Write findings_summary

```python
with driver.session(database=database_name()) as s:
    by_severity = {r["s"]: r["n"] for r in s.run("""
        MATCH (f:Finding {asset_id: $aid})
        WHERE f.status IN ['OPEN', 'DOWNGRADED']
        RETURN f.severity AS s, count(*) AS n
    """, aid=asset_id)}
    by_category = {r["c"]: r["n"] for r in s.run("""
        MATCH (f:Finding {asset_id: $aid})
        WHERE f.status IN ['OPEN', 'DOWNGRADED']
        RETURN f.category AS c, count(*) AS n ORDER BY n DESC
    """, aid=asset_id)}

summary = {
    "by_severity": by_severity,
    "by_category": by_category,
    "total_active": sum(by_severity.values()),
    "total_closed": s.run(
        "MATCH (f:Finding {asset_id: $aid}) "
        "WHERE f.status IN ['CLOSED_FALSE_POSITIVE', 'CLOSED_DUPLICATE', 'ROLLED_UP'] "
        "RETURN count(*) AS n", aid=asset_id
    ).single()["n"],
}

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (a:Asset {asset_id: $aid})
            SET a.findings_summary = $s
        """, aid=asset_id, s=json.dumps(summary)).consume()
        tx.commit()
```

---

## What to log

```
== Phase 9 verification ==
- findings before consolidation           : <N>
- rolled up                               : <N>
- closed as duplicate / batch_covered     : <N>
- still active (OPEN + DOWNGRADED)        : <N>
- by severity:
    level_1 / level_2 / level_3
- by category:
    FORM1_MISSING / LLP_LIMIT_CRITICAL / AD_COMPLIANCE_UNVERIFIED / ...
- by status:
    OPEN / DOWNGRADED / CLOSED_FALSE_POSITIVE / CLOSED_DUPLICATE / ROLLED_UP
- :Asset.findings_summary set             : yes
- fact_nodes_no_evidence                  : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="9")
```

Plus:
- `:Asset.findings_summary` is set and is valid JSON.
- Sum of all status counts == total :Finding count (no findings lost).
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `:Asset.findings_summary` is null.
- Number of findings dropped (some `:Finding` nodes were DELETED instead of having their status updated).
- Total roll-up rate > 80% — over-aggressive consolidation hides real findings.
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase9.py` — verified-working **stub** that just reports current finding distribution without rolling up. For AW139 it reports 50 findings (all OPEN, all FORM1_MISSING from the Phase 7 stub). A judgement Phase 9 would consolidate sibling-PN groups + run the cross-finding merge logic.
