# PHASE 2 — Asset Detection (confirmation against profile)

**Intent.** Phase 1 already seeded `:Asset` from `asset_profile.json`. Phase 2's job is to:

1. Re-affirm `:Asset` properties from the profile (idempotent).
2. Confirm and add the **secondary class label** (`:Aircraft`, `:Engine`, `:Propeller`, `:LandingGearAssembly`, `:APU`, `:RotorSystem`, `:Gearbox`).
3. Build the regulatory layer: `:TypeCertificate`, `:CountryRegistration`, optionally `:EngineModel` / `:APUModel` / `:PropellerModel` / `:RotorAssemblyModel`.
4. Aggregate per-page corpus signals (most-common SN, MSN, MIS system, latest dossier date) and reconcile against the profile.
5. Log the reconciliation table to `progress.log`.
6. Raise asset-level `CONTEXT_DISCREPANCY` findings for clear mismatches.

**Reference files to load:**
- `tiers_and_ata.md` (asset detection signals — section "Asset detection signals (Phase 2 confirmation)")
- `finding_types.md` (for `CONTEXT_DISCREPANCY`)

**Inputs:**
- `asset_profile.json`
- The :Page / :Document / :Stamp / connector graph from Phase 1

**Style:** Coding. Cypher aggregation + comparison. Use `csvs/.../AW139/phase2.py` as reference.

---

## Steps

### 1. Bootstrap + read profile

```python
from graph_dal import connect, database_name, AssetKind, FindingSeverity
from graph_dal.asset import write_asset, write_country_registration, write_type_certificate
from graph_dal.errors import VerificationFailed
from graph_dal.finding import write_audit_run, write_finding
from graph_dal.verify import verify_phase_2

profile = json.loads(profile_path.read_text(encoding="utf-8"))
```

### 2. Re-affirm `:Asset` (Phase 1 already seeded; ON MATCH coalesces)

The DAL helper `write_asset` is idempotent and uses APOC's `apoc.create.addLabels` to add the secondary class label (`:Aircraft`, `:Engine`, ...). Pick the right `asset_kind` from `profile.subtype`:

```python
asset_kind_map = {
    "HELICOPTER":           "AIRCRAFT", "FIXED_WING_JET": "AIRCRAFT",
    "FIXED_WING_TURBOPROP": "AIRCRAFT", "FIXED_WING_PISTON": "AIRCRAFT",
    "TURBOFAN":  "ENGINE",   "TURBOJET":   "ENGINE",
    "TURBOPROP": "ENGINE",   "TURBOSHAFT": "ENGINE", "PISTON": "ENGINE",
}
asset_kind = asset_kind_map.get(profile.get("subtype")) or AssetKind.AIRCRAFT.value
```

For component-only dossiers (engine alone, propeller alone, landing gear alone, APU, rotor head, gearbox), set `asset_kind` to the matching class — `ENGINE`, `PROPELLER`, `LANDING_GEAR_ASSEMBLY`, `APU`, `ROTOR_SYSTEM`, `GEARBOX`.

### 3. Build regulatory layer

```python
with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        if profile.get("type_designation"):
            write_type_certificate(
                tx, asset_id=asset_id, value=profile["type_designation"],
                tc_holder=profile.get("tc_holder"),
                tc_number=profile.get("tcds"),
                model_designation=profile["type_designation"],
                category=profile.get("certification_basis"),  # CS-25/27/29 etc
            )
        if profile.get("operator_country"):
            write_country_registration(
                tx, asset_id=asset_id, value=profile["operator_country"],
                iso_code=profile["operator_country"] if len(profile["operator_country"]) == 2 else None,
            )
        tx.commit()
```

Both helpers wire the relationship from `:Asset`:
- `:Asset-[:CERTIFIED_UNDER]->:TypeCertificate`
- `:Asset-[:REGISTERED_IN]->:CountryRegistration`

### 4. Open an `:AuditRun` so any findings raised here can :PRODUCED_BY it

```python
from datetime import datetime
run_id = f"audit::phase2::{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        write_audit_run(
            tx, asset_id=asset_id, value=run_id,
            dossier_cut_off_date_iso=profile.get("dossier_date"),
            audit_snapshot_date_iso=datetime.utcnow().date().isoformat(),
            sparengine_version="phase2-neo4j-1",
        )
        tx.commit()
```

### 5. Aggregate corpus signals via Cypher

```cypher
MATCH (:Page {asset_id: $aid})-[:MENTIONS_SN]->(sn:SerialNumber)
WITH sn.value AS value, count(*) AS n
ORDER BY n DESC LIMIT 1
RETURN value, n
```

Pull the same way for: most-mentioned `:PartNumber`, most common `:Document.document_type`, latest `:Date.iso`, and presence of `:Document {is_mis_export: true}`.

### 6. Reconcile profile vs corpus + log

Build a small reconciliation table (field, profile_value, corpus_majority, verdict ∈ {match, mismatch, missing-profile, missing-corpus, not-checked}) and write to `progress.log`:

```
Reconciliation:
  field            profile                corpus_majority        verdict
  ---------------- ---------------------- ---------------------- -------
  msn              31050                  31050                  match
  registration     I-CEPA                 I-CEPA                 match
  operator         null                   null                   not-checked
  type_designation AW139                  AW139                  match
```

Apply fuzzy-match for whitespace and Ltd/Limited variants on `operator`. Apply CAMP-as-concept-not-software rule from `data_quality_rules.md` on `mis_system`.

### 7. Raise `CONTEXT_DISCREPANCY` findings for clear mismatches

For each mismatch, find an evidence page (e.g. a page that mentions the corpus_majority value) and call `write_finding`:

```python
write_finding(
    tx, asset_id=asset_id,
    value=f"finding::asset::CONTEXT_DISCREPANCY::msn",
    severity=FindingSeverity.LEVEL_2.value,
    category="CONTEXT_DISCREPANCY",
    title="MSN mismatch between profile and corpus",
    description=(
        f"asset_profile.json declares msn={profile_msn!r}, but the most-mentioned "
        f"serial number across the corpus is {corpus_msn!r} ({n_pages} pages). "
        f"Verify against the certificate of registration or aircraft logbook."
    ),
    evidence_page_uid=evidence_page_uid,
    evidence_quote=f"corpus majority MSN={corpus_msn} on {n_pages} pages",
    recommended_action=(
        "Verify against the asset's certificate of registration or aircraft "
        "logbook front page; update the profile if corpus is correct."
    ),
    asset_level=True,
    audit_run_uid=run_id,
)
```

For pages with `metadata.context_discrepancy IS NOT NULL` from Phase 1, you don't need to re-walk those — they're already on `:Page` properties. If you decide to surface them as findings, do so per page (one finding per page).

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_phase_2
counts = verify_phase_2(driver, asset_id)
```

Rules `verify_phase_2` enforces:

- `count(:Asset) == 1` (exactly one)
- `:Asset` has at least one secondary class label (`:Aircraft|:Engine|:Propeller|:LandingGearAssembly|:APU|:RotorSystem|:Gearbox`)
- `fact_nodes_no_evidence == 0` (golden rule still holds)

Append to `progress.log`:

```
== Phase 2 verification ==
- audit_run_uid                           : audit::phase2::20260506T...
- type_certificate                        : written | skipped (no profile.type_designation)
- country_registration                    : written | skipped (no profile.operator_country)
- assets                                  : 1
- asset_class_aircraft / engine / ...     : 1 / 0 / 0 / ... (exactly one should be 1)
- asset_class_label_total                 : 1
- fact_nodes_no_evidence                  : 0
- corpus signals: page_count, doc_count, stamp_count
  top_sn=(value, n)  top_pn=(value, n)
  top_doc_type=(value, n)  latest_date=YYYY-MM-DD
- findings written (CONTEXT_DISCREPANCY)  : <N>

Reconciliation:
  ... (the table from step 6)
```

---

## STOP conditions

- `count(:Asset) != 1` (Phase 1 missed it, or you wrote duplicates).
- No secondary class label was added (`asset_class_label_total == 0`) — Phase 2 must classify.
- `fact_nodes_no_evidence > 0` — golden rule violated.
- Any value on `:Asset` is the literal string "unknown" AND the profile has it populated — that means you wrote stub data instead of reading the profile.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase2.py` — verified-working canonical Phase 2.
