# PHASE 4 — Component Discovery (Layer 3 hydration)

**Intent.** Read the `:Page-[:MENTIONS_PN]->(:PartNumber)` and `:Page-[:MENTIONS_SN]->(:SerialNumber)` edges that Phase 1 wrote, plus `:COVERS_ATA`, and produce `:Component` nodes for the (PN, SN) pairs that survive the SPARENGINE 8 selection rules.

**Reference files:**
- `csv_and_ocr.md` (entities, tables)
- `tiers_and_ata.md` (informational — ATA → tier mapping; Tier nodes are no longer written, but the mapping drives threshold selection in rule 4)
- `data_quality_rules.md` (blocklist, OCR_SUSPECTED rules)

**Inputs:** the live graph from Phases 0–2; `asset_profile.json`.

**Style:** Mixed. Rules 1–6 are mechanical Cypher + helpers; rules 7 (batch certificate) and 8 (OCR rejection) require you to actually look at pages.

---

## What this phase produces

| Node | Edges out |
|---|---|
| `:Component` (the central output) | `:HAS_PRIMARY_PN`, `:HAS_ALTERNATE_PN {source, confidence}`, `:HAS_SN`, `:RELATED_TO_ATA`, `:OF_MODEL`, `:PART_OF`, `:OF_FAMILY` |
| `:PartFamily` (when known) | — |
| Edges between PartNumbers (synonyms / supersession): `:SAME_AS`, `:SUPERSEDED_BY {effective_date}` |

Plus `:Asset-[:HAS_COMPONENT]->:Component` for top-level components (engines, propellers, MGB, MLG, NLG, APU on full-aircraft dossiers).

Every `:Component` carries page evidence via `:EVIDENCED_BY` (golden rule, enforced by `write_component`).

---

## The 8 component selection rules (apply in order)

### Rule 1 — Seed list first

Create `:Component` rows for every entry in `asset_profile.expected_components` (engines, propellers, MGB/IGB/TGB on helicopters, MLG/NLG, APU). These are the anchors; everything else hangs off them. Without seeds, fulltext search misses them under OCR garble.

```python
exp = profile.get("expected_components") or {}

# An evidence page for seeds — pick the dossier's first page
with driver.session(database=database_name()) as s:
    seed_evidence = s.run(
        "MATCH (p:Page {asset_id: $aid}) "
        "RETURN p.value AS uid ORDER BY p.page_index LIMIT 1",
        aid=asset_id,
    ).single()
    seed_evidence_uid = seed_evidence["uid"] if seed_evidence else None

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        for i, e in enumerate(exp.get("engines") or []):
            esn = (e or {}).get("esn")
            if not esn or not seed_evidence_uid:
                continue
            pn = "ENGINE_SEED_PN"   # or e.get("pn") if known
            sn = esn.strip().upper()
            cuid = f"component::{pn}::{sn}"
            write_part_number(tx, asset_id=asset_id, value=pn)
            write_serial_number(tx, asset_id=asset_id, value=sn)
            write_component(
                tx, asset_id=asset_id, value=cuid,
                evidence_page_uid=seed_evidence_uid,
                evidence_quote=f"Profile-seeded engine #{i+1} esn={esn}",
                canonical_pn=pn, installed_sn=sn,
                description="Engine (seeded from asset_profile.expected_components)",
                component_category="Engine_Module",
                status="DISCOVERED", source="seed",
                ata_chapter="72", is_overhaul=True,
            )
            link_has_primary_pn(tx, asset_id=asset_id, component_uid=cuid, pn_value=pn)
            link_has_sn(tx, asset_id=asset_id, component_uid=cuid, sn_value=sn)
            link_asset_has_component(tx, asset_id=asset_id, component_uid=cuid)
```

Same pattern for propellers (`exp.get("propellers")`), MGB/IGB/TGB, APU, landing gear.

### Rule 2 — PN/SN co-occurrence extraction

Sweep the live `:Page-[:MENTIONS_PN]->:PartNumber` and `:Page-[:MENTIONS_SN]->:SerialNumber` edges. For each `:Page`, gather the PNs and SNs mentioned on that page; every (pn, sn) pair where both are non-empty is a candidate.

```cypher
MATCH (p:Page {asset_id: $aid})
OPTIONAL MATCH (p)-[:MENTIONS_PN]->(pn:PartNumber {asset_id: $aid})
OPTIONAL MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber {asset_id: $aid})
OPTIONAL MATCH (p)-[:COVERS_ATA]->(ata:ATAChapter {asset_id: $aid})
OPTIONAL MATCH (p)<-[:HAS_PAGE]-(d:Document {asset_id: $aid})
WITH p, d.document_type AS doc_type, p.title AS title,
     collect(DISTINCT pn.value) AS pns,
     collect(DISTINCT sn.value) AS sns,
     collect(DISTINCT ata.value) AS atas
RETURN p.value AS page_uid, doc_type, title, pns, sns, atas
```

Use OEM-specific PN/SN regex patterns where applicable (the OCR usually got the values right, but you can validate them):
- P&WC LLPs: PN `\d{7}[A-Z]?-\d{2}` paired with 9–12 char alphanumeric SN
- Bell helicopter components: PN `\d{3}-\d{3}-\d{3}-\d{3}`, SN `MN\d{3}` or `[A-Z]{2,3}\d{4,6}`
- CFM56 LLPs: PN `\d{3}-?\d{4}-?\d{2}`, SN `[A-Z]{2}\d{6}`
- Rolls-Royce 250-C20 family: PN often `[0-9A-Z]{6,9}`, SN `CAE-\d{6}` or `CAB-\d{6}`

### Rule 3 — Apply blocklist

Drop SNs that match `asset_profile.blocked_sn_list` PLUS the universal blocklist:
- year strings 1990..2030
- single character
- date-like (`\d{4}-\d{2}-\d{2}`)
- strings matching document numbers verbatim (e.g. equal to the work order number on the page)

```python
def _is_blocked_sn(sn: str, custom_blocked: set[str]) -> bool:
    s = sn.strip().upper()
    if not s or s in custom_blocked or len(s) <= 1:
        return True
    if s.isdigit() and len(s) == 4 and 1990 <= int(s) <= 2030:
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return True
    return False
```

### Rule 4 — Hit-count threshold by tier

Compute the (informational) tier from the page's ATA chapter:

```python
ATA_TO_TIER = {
    "32": "LANDING_GEAR", "49": "APU", "61": "PROPELLER",
    "62": "ROTOR_SYSTEM", "64": "ROTOR_SYSTEM",
    "66": "ROTOR_SYSTEM", "67": "ROTOR_SYSTEM",
    "63": "TRANSMISSION", "65": "TRANSMISSION",
    "51": "AIRFRAME", "52": "AIRFRAME", "53": "AIRFRAME",
    "54": "AIRFRAME", "55": "AIRFRAME", "56": "AIRFRAME", "57": "AIRFRAME",
    "22": "AVIONICS", "23": "AVIONICS", "27": "AVIONICS",
    "31": "AVIONICS", "34": "AVIONICS", "45": "AVIONICS",
    "21": "SYSTEMS", "24": "SYSTEMS", "26": "SYSTEMS", "28": "SYSTEMS",
    "29": "SYSTEMS", "30": "SYSTEMS", "33": "SYSTEMS",
    "35": "SYSTEMS", "36": "SYSTEMS", "38": "SYSTEMS",
    "25": "INTERIOR",
}
HIGH_VALUE_TIERS = {"ENGINE", "LANDING_GEAR", "PROPELLER",
                    "ROTOR_SYSTEM", "TRANSMISSION", "APU"}

def ata_to_tier(ata: str | None) -> str:
    if not ata: return "UNKNOWN"
    head = re.match(r"^(\d{2})", str(ata).strip())
    if not head: return "UNKNOWN"
    n = int(head.group(1))
    if 70 <= n <= 89: return "ENGINE"
    return ATA_TO_TIER.get(head.group(1), "UNKNOWN")
```

Threshold rule:
- `ENGINE`, `LANDING_GEAR`, `PROPELLER`, `ROTOR_SYSTEM`, `TRANSMISSION`, `APU`: **≥ 1 occurrence**
- `AVIONICS`, `SYSTEMS`, `INTERIOR`: **≥ 2 occurrences**
- `AIRFRAME`: **≥ 2 for structural items, ≥ 1 for repair-tracked**

A pair below threshold can still promote if **any** of its hit-pages has a `document_type` in:
```python
LLP_DOC_TYPES     = {"engine_llp_status_sheet", "life_limited_parts_status"}
HISTORY_DOC_TYPES = {"component_history_card", "component_logbook"}
FORM1_DOC_TYPES   = {"easa_form_one", "faa_form_8130", "tcca_form_one",
                     "dual_release_certificate", "certificate_of_release_to_service"}
```

### Rule 5 — Tier inference from ATA, with description fallback

Use the mapping in Rule 4. When ATA is missing on a page but the description is clearly in a system, infer tier from keyword scan of the description (`engine`, `landing gear`, `main rotor`, `swashplate`).

The tier is now an *informational* property used for threshold + visualisation only — there is **no `:Tier` node** any more (Q6 of the migration plan). Store the tier string on `:Component.component_category` if it adds value (e.g. `Engine_Module`, `LLP`, `On_Condition`).

### Rule 6 — Same-PN clustering (siblings)

After promoting components, cluster them by `canonical_pn`. Multiple SNs under the same PN are *siblings* — Phase 7.5 uses this for sibling-PN limit propagation.

You can compute this with one Cypher query and report cluster sizes in `progress.log`:

```cypher
MATCH (c:Component {asset_id: $aid})-[:HAS_PRIMARY_PN]->(pn:PartNumber)
WITH pn.value AS pn, collect(c.installed_sn) AS sns, count(*) AS n
ORDER BY n DESC
RETURN pn, sns, n LIMIT 30
```

### Rule 7 — Batch certificate detection (mixed)

When a single Form 1 / 8130 covers a serial **range** (e.g. *"SN 004-14658M thru 004-14759M"*), DO NOT create one component per SN in the range. Instead:

- Create one `:BatchNumber {value, sn_range_start, sn_range_end}` node (Phase 1's `write_batch_number` accepts ranges).
- Create one `:Component` per individual SN that is *actually observed* on a page.
- Phase 7.5 will close `FORM1_MISSING` for any SN in the covered range that the batch certificate covers.

To detect batch certificates, you must Read the candidate Form 1 pages — look for "thru", "through", "to", or a hyphenated range in the SN field. This is the judgement-mixed part of Phase 4. Use `graph_dal.cite.cite_node()` to get the Form 1 page URI and read it.

### Rule 8 — OCR rejection

- PN with mid-string spaces (`"123 4567"`) → raise an `OCR_SUSPECTED` finding (Phase 7 owns these; here you may flag the Component with `status="OCR_SUSPECTED"` or set a property the verifier can pick up).
- SN looking like a date (`"2024-01-15"`) → reject (Rule 3 already does).
- Visual OCR confusion (O↔0, l↔1, S↔5) in unusual positions → trigger a vision re-read on the Page's `s3_key` before deciding.
- For high-value components (ENGINE, ROTOR), trigger a vision re-read before promoting if the entity confidence on the source page was `low`.

---

## Promote a (PN, SN) pair to a `:Component`

For each surviving pair, **promote** when ANY of these is true:

- Appears on an `engine_llp_status_sheet` or `life_limited_parts_status` page → `is_llp = True`.
- Appears on a `component_history_card` or `component_logbook`.
- Appears on a Form 1 / 8130 / TCCA / dual_release as the certified item.
- Appears in a parts table as `S/N On` (currently installed).
- Appears with overhaul / TBO / TSO entities nearby → `is_overhaul = True`.

For each promoted pair:

```python
write_component(
    tx, asset_id=asset_id,
    value=f"component::{pn}::{sn}",
    evidence_page_uid=first_page_uid,        # the highest-evidentiary-weight page that mentions it
    evidence_quote=verbatim_excerpt[:240],
    canonical_pn=pn, installed_sn=sn,
    description=None,                        # Phase 5/6 may enrich
    ata_chapter=top_ata,
    is_llp=is_llp, is_overhaul=is_overhaul,
    status="DISCOVERED", source="page_mention",
)
link_has_primary_pn(tx, asset_id=asset_id, component_uid=cuid, pn_value=pn)
link_has_sn(tx, asset_id=asset_id, component_uid=cuid, sn_value=sn)
if top_ata:
    link_component_related_to_ata(tx, asset_id=asset_id, component_uid=cuid, ata_value=top_ata)
```

Set `last_form1_file/page/date` and TSN/CSN/TSO/CSO from the highest evidentiary_weight page — these are component properties (not separate edges). The DAL writer accepts them as kwargs.

---

## Performance notes

- One transaction per ~200 promoted pairs. Use `BATCH = 200` in your driver.
- Build the (PN, SN) co-occurrence dict in memory first (one Cypher pass over pages), then promote in batches.
- The fulltext index is read-side only; you don't write to it explicitly.

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="4")
```

Plus your own counts logged to `progress.log`:

```
== Phase 4 verification ==
- pages_examined                          : <N>
- distinct_pn_sn_pairs                    : <N>
- pairs_promoted_to_components            : <N>
- pairs_rejected_threshold                : <N>
- sn_blocked_total                        : <N>
- components_seeded                       : <N>
- components_written_this_phase           : <N>
- :Component count (live)                 : <N>
- distinct PartNumbers used by components : <N>
- distinct SerialNumbers used by components: <N>
- :RELATED_TO_ATA edges written           : <N>
- same-PN cluster size distribution       : {1:..., 2:..., 3:..., ...}
- LLP component count                     : <N>
- fact_nodes_no_evidence                  : 0
```

Rules:

- `count(:Component) > 0`. **MUST be > 0.**
- `count(distinct PartNumbers used by components) > 0`.
- For an engine dossier: `count(:Component {is_llp: true}) > 0` if the dossier has any `engine_llp_status_sheet` pages.
- Every component-seed in `expected_components` must be present as a :Component (you can verify with a Cypher count).
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `count(:Component) == 0`.
- `count(:Component) == 1` AND that component was hand-coded from the folder name (cheating).
- `count(:PartNumber)` is way out of proportion with `count(:Component)` — typically 0.5–2 components per PartNumber. If you have 2000 PNs and 5 components, something is wrong with the promotion rules.
- Expected seeds from `asset_profile.expected_components` are not present.
- For an engine dossier with hundreds of LLP pages: `count(:Component {is_llp: true}) == 0` — means LLP detection was silently skipped.

For a typical full aircraft dossier expect at least **20–50 components**. For an engine-only dossier expect at least **15–30** (LLPs, modules, accessories). Single-digit components for an 800-page dossier is evidence of a bug.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase4.py` — verified-working canonical Phase 4 implementing rules 1–6 (rules 7–8 left for future enrichment passes). For AW139 it produces 1142 components.
