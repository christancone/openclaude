# PHASE 7.5 — Verification Pass

**Intent.** For every finding from Phase 7 (`status="OPEN"` or `"PROVISIONAL"`), run a second pass with strategies the original investigation may not have used. Close false positives. Downgrade findings where partial evidence appears.

**Reference files:**
- `investigation_discipline.md` — the same 9 strategies, applied with fresh state and full corpus context
- `finding_types.md`

**Inputs:** the post-Phase-7 graph; the Lucene fulltext index `page_text` over `:Page.text`.

**Style:** **Judgement.** YOU re-search the corpus per finding via `graph_dal.fulltext`. Do NOT write `verification_strategy = 'Simulated'`.

---

## What this phase produces

It MODIFIES `:Finding.status` and writes new `:CORROBORATED_BY` edges:

- `status="OPEN"` → `status="CLOSED_FALSE_POSITIVE"` when verification finds the missing evidence
- `status="OPEN"` → `status="DOWNGRADED"` (severity dropped one level) when partial evidence appears
- New `:Finding-[:CORROBORATED_BY]->:Page` edges when verification finds confirming or contradicting evidence

It does NOT raise new findings — those came from Phase 7. (If you discover an entirely new issue during 7.5, log it but raise it in Phase 9 consolidation.)

---

## Steps

### 1. Bootstrap + load fulltext

```python
from graph_dal.fulltext import escape_lucene, search_pages
from graph_dal.cite import cite_node, format_citation
```

### 2. Pull all OPEN findings + their context

```cypher
MATCH (f:Finding {asset_id: $aid})
WHERE f.status IN ['OPEN', 'PROVISIONAL']
OPTIONAL MATCH (c:Component)-[:HAS_FINDING]->(f)
RETURN f.value AS uid, f.category AS category, f.severity AS severity,
       f.title AS title, f.description AS description,
       c.value AS component_uid, c.canonical_pn AS pn, c.installed_sn AS sn
```

### 3. Per-finding verification (the 9 strategies, again)

For each finding, run as many of these as relevant. Each query uses the Lucene fulltext index:

| # | Strategy | Cypher |
|---|---|---|
| 1 | **wo_pages** — search the work-package's pages for the SN | `MATCH (wp:WorkPackage)-[:INCLUDES]->()-[:CARRIES]-(p:Page) WHERE ...` |
| 2 | **sn_alone** | `search_pages(s, asset_id=aid, query=f'"{sn}"', limit=10)` |
| 3 | **alt_pn** | `MATCH (c {value:cuid})-[:HAS_ALTERNATE_PN]->(pn) RETURN pn.value` then re-search |
| 4 | **filename_pn** | `MATCH (d:Document) WHERE d.file_name CONTAINS $pn` |
| 5 | **filename_sn** | `MATCH (d:Document) WHERE d.file_name CONTAINS $sn` |
| 6 | **batch_range** | `MATCH (b:BatchNumber) WHERE b.sn_range_start <= $sn AND b.sn_range_end >= $sn` |
| 7 | **page_neighbourhood** | pages adjacent (±5) to a known evidence page for this component |
| 8 | **siblings** | sibling SNs under the same `canonical_pn` (some Form 1s cover sibling ranges) |
| 9 | **oem_typical** | known OEM patterns from `data_quality_rules.md` (e.g. P&WC LLPs are typically grouped on `engine_llp_status_sheet` documents) |

For each strategy, log results to `decisions.log`:

```
[phase7.5] component::3036041-01::CAE-840837 | FORM1_MISSING | strategy=alt_pn | hits=2 | result: Form 1 located on alt PN 3036041-02 page 12 — closing finding
```

### 4. Lucene query examples

```python
def search_form1_for_sn(session, *, asset_id, sn):
    """Strategy 2: SN alone — find any page mentioning the SN that also
    contains 'Form 1' / 'EASA Form' / 'Form 8130'."""
    q = f'"{escape_lucene(sn)}" AND ("Form 1" OR "EASA Form" OR "8130")'
    return search_pages(session, asset_id=asset_id, query=q, limit=10)


def search_filename_pn(session, *, asset_id, pn):
    return list(session.run("""
        MATCH (d:Document {asset_id: $aid})
        WHERE toLower(d.file_name) CONTAINS toLower($pn)
        RETURN d.file_name, d.value LIMIT 10
    """, aid=asset_id, pn=pn))
```

### 5. Update the finding

When verification closes a finding:

```python
with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (f:Finding {asset_id: $aid, value: $fuid})
            SET f.status = 'CLOSED_FALSE_POSITIVE',
                f.closed_at = datetime(),
                f.closed_reason = $reason
        """, aid=asset_id, fuid=finding_uid, reason=reason).consume()
        # Add :CORROBORATED_BY edge to the page that proved the false-positive
        tx.run("""
            MATCH (f:Finding {asset_id: $aid, value: $fuid})
            MATCH (p:Page {asset_id: $aid, value: $puid})
            MERGE (f)-[:CORROBORATED_BY {strategy: $strategy}]->(p)
        """, aid=asset_id, fuid=finding_uid, puid=found_page_uid,
            strategy=strategy_name).consume()
        tx.commit()
```

When downgrading severity:

```python
tx.run("""
    MATCH (f:Finding {asset_id: $aid, value: $fuid})
    SET f.status = 'DOWNGRADED',
        f.severity = $new_sev,
        f.downgrade_reason = $reason
""", aid=asset_id, fuid=finding_uid, new_sev="level_3", reason=...).consume()
```

### 6. Reason out loud (per finding)

For every finding you close or downgrade, emit a paragraph in your assistant text:

```
### [Phase 7.5] Closing FORM1_MISSING for component PT6C-67C/PCE-KB0117
Strategy 'alt_pn' located the Form 1 on alternate PN 3036041-02 (this PN supersedes
3036041-01 per :SUPERSEDED_BY edge effective 2018-06-01). The Form 1 is on page 12
of "ATA 71- PT6C-67C - KB0117 ENGINE ASSY.pdf", issued 2018-11-13 by P&WC. Closing
finding as CLOSED_FALSE_POSITIVE with :CORROBORATED_BY edge.
```

---

## What to log

```
== Phase 7.5 verification ==
- findings examined                       : <N>
- closed (false positive)                 : <N>
- downgraded                              : <N>
- still OPEN                              : <N>
- by strategy that closed:
    wo_pages=<N>, sn_alone=<N>, alt_pn=<N>, filename_pn=<N>,
    filename_sn=<N>, batch_range=<N>, page_neighbourhood=<N>,
    siblings=<N>, oem_typical=<N>
- :CORROBORATED_BY edges added            : <N>
- decisions.log lines written             : <N>      ← MUST equal findings_examined
- fact_nodes_no_evidence                  : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="7.5")
```

Plus:
- `decisions.log` Phase 7.5 line count == findings_examined.
- Closed-rate is reasonable (typically 30–60% of Phase 7 OPEN findings close in 7.5; if 0% close, you skipped the strategies; if 100% close, you fabricated closures).
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `decisions.log` Phase 7.5 lines < findings_examined — you skipped strategies on some findings.
- 0% close rate AND > 20 findings — you went through the motions without actually running fulltext.
- 100% close rate — you over-closed (verification fabricated).
- Any closed finding lacks a `:CORROBORATED_BY` edge — there's no evidence of the close.
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase7_5.py` — verified-working **mechanical stub** that demonstrates the fulltext index works (10 hits for "Form 1" smoke query, 3 finding-search-with-hits). A judgement Phase 7.5 builds on this by running the 9 strategies per finding and updating statuses.
