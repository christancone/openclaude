# PHASE 0 — Asset Orientation

**Intent.** Read a small representative slice of the dossier and produce a structured `asset_profile.json`. Every later phase consumes this profile. Without it, the pipeline is guessing.

**Reference files to load alongside this one:**
- `csv_and_ocr.md` (CSV schema + extracted_json structure)
- `tiers_and_ata.md` (asset detection signals)

**Inputs:** the CSV file at `csv_path`. **Phase 0 writes nothing to Neo4j** — its only output is `asset_profile.json` on disk. Phase 1 reads the profile, seeds `:Asset` from it, and starts the graph build.

**Style:** Judgement. **You** read the pages, **you** decide what the asset is. A script that hardcodes from the folder name is cheating.

---

## ANTI-CHEAT RULE

You may NOT derive the profile from the workdir folder name, the CSV filename, or any string outside the dossier itself. Past runs have hardcoded `type_designation` and `esn` from the folder name and called the phase done. **That run produced an empty graph.** If you write the profile without reading the CSV, **you are cheating.**

The profile must be derived from `entities[]` and `metadata` aggregated across the representative slice. Log which pages you read.

---

## Steps

1. **Pick ≤30 representative pages.** Stream the CSV with pandas; do not load it all. Hand-pick by these heuristics in order:

   1. First 5 pages of the dossier (cover sheets, index, TOC).
   2. Pages where `extracted_json.content.document_type` is in: `certificate_of_airworthiness`, `certificate_of_registration`, `airworthiness_review_certificate`, `airframe_logbook`, `engine_logbook`, `engine_llp_status_sheet`, `life_limited_parts_status`, `weight_and_balance_report`.
   3. Pages whose `original_path` mentions "redelivery", "lease return", "preservation", "cover".
   4. Most recent ≤5 pages by `metadata.dates[]` if you can sort cheaply.

2. **Aggregate `entities[]` and `metadata`** across those pages:
   - Collect all `registration`, `msn`, `esn`, `type_designation` entities; pick highest-confidence values.
   - Collect all `operator`, `mro` entities to seed operator/operator country.
   - Collect all `mis_system` values from `metadata` to determine `primary_mts`.
   - Detect `state` from cover-page hints (redelivery/preservation/lease return) plus WO-cluster density near dossier date.
   - Detect `asset_class` and `subtype` per `tiers_and_ata.md` rules.

3. **Build `expected_tiers`** from the detected `asset_class` (per `tiers_and_ata.md`). Engine-only → `["ENGINE"]`. Aircraft → `["ENGINE","LANDING_GEAR","PROPELLER","AIRFRAME","AVIONICS"]` (+ `APU` if helicopter or detected; + `ROTOR_SYSTEM`, `TRANSMISSION` if helicopter).

4. **Build `expected_components`**:
   - For aircraft: each detected engine SN with its position, propeller models, APU model.
   - For engine dossier: a single engine entry with the primary ESN.
   - Helicopter: rotor system + transmission entries.

5. **Build `blocked_sn_list`**: asset MSN, registration history values, primary engine SNs, year strings 1990..2030. Phase 1 uses this to skip writing matching `:SerialNumber` nodes and `:MENTIONS_SN` edges (universal blocklist + this custom list).

6. **Detect dossier date.** Latest "approved/dated" entry seen across the slice.

7. **Write `workdir / "asset_profile.json"`.** Print it to `progress.log` for human review.

---

## Output schema (`asset_profile.json`)

```json
{
  "asset_class": "fixed_wing | rotorcraft | engine | apu | propeller | landing_gear",
  "subtype": "FIXED_WING_TURBOPROP | TURBOSHAFT | ...",
  "type_designation": "ATR72-212A",
  "tcds": "EASA.A.084 or null",
  "yom": 2014,
  "identifier": { "msn": "1191", "esn": null, "primary_serial": null },
  "registration": {
    "current": "PK-GAI",
    "history": [{ "reg": "F-WWLE", "country": "FR", "from": "2014-08-01", "to": "2014-09-15" }]
  },
  "operator": "Garuda Indonesia",
  "operator_country": "ID",
  "primary_mts": "AMOS",
  "expected_tiers": ["ENGINE", "LANDING_GEAR", "PROPELLER", "AIRFRAME", "AVIONICS"],
  "expected_components": {
    "engines":    [{ "model": "PW127M", "position": "LH", "esn": "ED1017" }],
    "propellers": [{ "model": "568F",   "position": "LH" }],
    "apu": null
  },
  "counters": { "tsn": 9575, "csn": 8698, "as_of_date": "2025-10-21", "confidence": "high" },
  "state": "lease_return | active | preserved | shop_visit | parted_out | unknown",
  "state_evidence": { "wo_cluster_pattern": "...", "dummy_tags_count": 18, "redelivery_cover_page": "..." },
  "dossier_date": "2025-10-21",
  "risk_patterns_observed": ["lease return WO cluster", "DUMMY UNSERVICEABLE on prop blades"],
  "blocked_sn_list": ["1191", "PK-GAI", "ED1017", "F-WWLE"]
}
```

When a field genuinely cannot be determined from the representative slice, set it to `null` — do NOT guess. Phase 2 will raise `CONTEXT_DISCREPANCY` if Phase 1's full corpus contradicts a guessed value.

---

## MANDATORY VERIFICATION

After Phase 0 finishes, append this to `progress.log` and stop if it fails:

```
== Phase 0 verification ==
- asset_profile.json exists                          : <yes/no>
- type_designation derived from CSV (not folder name): <yes/no>  ← CRITICAL
- expected_tiers contains at least one tier           : <yes/no>
- pages_read_in_phase0                                : <count>  (target: 5..30)
- entities_aggregated                                 : <count>  (target: > 0)
```

**STOP conditions** — do NOT proceed to Phase 1 if any of these:

- `asset_profile.json` does not exist.
- The file was written without reading any CSV row (cheating).
- `expected_tiers == []`.
- `pages_read_in_phase0 == 0`.
- `entities_aggregated == 0`. You opened 30 pages and pulled 0 entities — that means you didn't actually parse `extracted_json.content.entities[]`. The OCR has hundreds of entities per page; if the count is 0, your aggregation code is broken.
- ALL of `operator`, `dossier_date`, `registration.current` (for aircraft) are `null`. For an aircraft dossier this is impossible — pages 1-5 always have at least an operator name and a dossier date. If they're all null, you didn't read the header_fields. (For an engine-only dossier `registration.current` may legitimately be null, but `operator` should still be populated from a Form 1 / shop-visit cover.)
- `dossier_date` is `null` AND any of the 30 pages had a date in `metadata.dates[]`. The latest non-null date in the slice IS the dossier date — emit it, don't leave it null.

Print `asset_profile.json` to the log so a human can sanity-check before Phase 1 burns through the full corpus.
