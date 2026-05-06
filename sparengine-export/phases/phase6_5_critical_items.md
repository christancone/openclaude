# PHASE 6.5 — Critical Items + Lease-Return State

**Intent.** Identify the items that drive transaction value or airworthiness risk **before** Phase 7 walks every component. These get investigated FIRST in Phase 7. Also detect the lease-return state for the lease-return banner.

**Reference files:**
- `severity_matrix.md` (criticality)
- `data_quality_rules.md` (LLP / TBO patterns)

**Inputs:** the post-Phase-6 graph; `asset_profile.json`.

**Style:** Mixed. Threshold detection is mechanical Cypher; ranking by urgency is judgement.

---

## What this phase produces

| Node | Property |
|---|---|
| `:PriorityItem` (per critical component or compliance gap) | `kind`, `title`, `description`, `urgency` ∈ {`immediate`, `within_30d`, `within_90d`, `informational`} |
| `:Asset.lease_return_state` (property) | `redelivery_active` \| `in_service` \| `unknown` |
| `:Asset.lease_return_signal_count` (property) | count of redelivery / delivery-acceptance documents in the dossier |

Plus the edge `:Component-[:HAS_PRIORITY_ITEM]->:PriorityItem` (auto-wired by `write_priority_item` when `component_uid` is supplied).

---

## Steps

### 1. LLP components → :PriorityItem

```python
from graph_dal.finding import write_priority_item

with driver.session(database=database_name()) as s:
    llp_rows = list(s.run("""
        MATCH (c:Component {asset_id: $aid})
        WHERE c.is_llp = true
        RETURN c.value AS uid, c.canonical_pn AS pn,
               c.installed_sn AS sn, c.ata_chapter AS ata,
               c.life_limit AS life_limit, c.tsn AS tsn, c.csn AS csn
    """, aid=asset_id))

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        for r in llp_rows:
            urgency = "informational"
            # If life_limit and tsn/csn known, classify by remaining life:
            if r["life_limit"] and r["tsn"]:
                remaining = r["life_limit"] - r["tsn"]
                if remaining < 100:    urgency = "immediate"
                elif remaining < 500:  urgency = "within_30d"
                elif remaining < 1000: urgency = "within_90d"

            write_priority_item(
                tx, asset_id=asset_id,
                value=f"priority::llp::{r['pn']}::{r['sn']}",
                kind="llp_review",
                title=f"LLP {r['pn']}/{r['sn']} requires review",
                description=(
                    f"Component {r['uid']} is flagged is_llp=true. "
                    f"The dossier should carry a current LLP status sheet "
                    f"with remaining cycles/hours. " +
                    (f"Current TSN={r['tsn']}, life_limit={r['life_limit']}." if r["tsn"] else "")
                ),
                urgency=urgency,
                component_uid=r["uid"],
            )
        tx.commit()
```

### 2. AD compliance gaps → :PriorityItem

For each `:AirworthinessDirective` not covered by a `:COMPLIES_WITH` edge from any document:

```cypher
MATCH (ad:AirworthinessDirective {asset_id: $aid})
WHERE NOT EXISTS { (ad)<-[:COMPLIES_WITH]-(:Document) }
  AND NOT EXISTS { (ad)<-[:COMPLIES_WITH]-(:WorkPackage) }
RETURN ad.value AS ad_number, ad.compliance_date AS due
```

Emit a `:PriorityItem` per uncovered AD.

### 3. SB compliance gaps → :PriorityItem (lower urgency than AD)

Same pattern, lower urgency since SBs are usually optional.

### 4. Lease-return state detection

Set `:Asset.lease_return_state`:

```python
with driver.session(database=database_name()) as s:
    redelivery = s.run("""
        MATCH (d:Document {asset_id: $aid})
        WHERE d.document_type IN ['redelivery_condition_report',
                                   'delivery_acceptance_certificate']
        RETURN count(d) AS n
    """, aid=asset_id).single()["n"]

state = "redelivery_active" if redelivery > 0 else "in_service"

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (a:Asset {asset_id: $aid})
            SET a.lease_return_state = $s, a.lease_return_signal_count = $n
        """, aid=asset_id, s=state, n=redelivery).consume()
        tx.commit()
```

The viz HTML reads `lease_return_state` to colour the banner: green (in_service), orange (redelivery_active), grey (unknown).

### 5. Critical-items ranking (judgement)

For each `:PriorityItem`, write a 2–3 sentence reasoning paragraph in your own assistant text (not in Python `print`) explaining:
- Why this item is critical (LLP near limit / AD due / valuable engine module)
- What the auditor should look for first
- The expected paperwork (Form 1, shop visit report, etc.)

Cite at least one evidence page using `graph_dal.cite.cite_node()`.

---

## What to log

```
== Phase 6.5 verification ==
- llp_components_seen                     : <N>
- AD compliance gaps detected             : <N>
- SB compliance gaps detected             : <N>
- :PriorityItem count                     : <N>
- urgency distribution                    : immediate=<N>, within_30d=<N>, within_90d=<N>, informational=<N>
- redelivery doc count                    : <N>
- lease_return_state                      : in_service | redelivery_active
- fact_nodes_no_evidence                  : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="6.5")
```

Plus:
- If `count(:Component {is_llp: true}) > 0`, then `count(:PriorityItem) > 0` (every LLP gets at least an `informational` priority item).
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- The asset has LLPs but `count(:PriorityItem) == 0` — the LLP loop was skipped.
- `lease_return_state` is unset (not just "unknown" — actually NULL).
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase6_5.py` — verified-working. For AW139 it produces 24 priority items (one per LLP) and sets `lease_return_state="in_service"`.
