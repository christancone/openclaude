# PHASE 4 — Part Type, Serial, and Component Hydration

**Intent.** Produce the Layer 3 component nodes. The 8 selection rules below are **all mandatory**.

**Reference files:**
- `csv_and_ocr.md` (entities, tables)
- `tiers_and_ata.md` (tier inference, ATA→tier)
- `data_quality_rules.md` (blocklist, OCR_SUSPECTED rules)

**Inputs:** `pages`, `documents`, `stamps`, `assets`, `asset_profile.json`.

---

## The 8 component selection rules (apply in order)

1. **Seed list first.** Create component rows for every entry in `asset_profile.expected_components`. Engines, propellers, MGB/IGB/TGB (helicopters), MLG/NLG, APU. These are the anchors; everything else hangs off them. Without seeds, FTS misses them under OCR garble.

2. **PN/SN regex pair extraction.** Sweep `entities[]` (read JSON from `pages.serial_numbers` / `pages.part_numbers`, plus re-parse a sample of `extracted_json` if needed) for `(part_number, serial_number)` co-occurrences. Use OEM-specific patterns where applicable:
   - P&WC LLPs: PN `\d{7}[A-Z]?-\d{2}` paired with 9-12 char alphanumeric SN
   - Bell helicopter components: PN `\d{3}-\d{3}-\d{3}-\d{3}`, SN `MN\d{3}` or `[A-Z]{2,3}\d{4,6}`
   - CFM56 LLPs: PN `\d{3}-?\d{4}-?\d{2}`, SN `[A-Z]{2}\d{6}`
   - Rolls-Royce 250-C20 family: PN often `[0-9A-Z]{6,9}`, SN `CAE-\d{6}` or `CAB-\d{6}`
   Fall back to generic `[A-Z0-9-]+` regex otherwise.

3. **Apply blocked-SN list** from `asset_profile.blocked_sn_list`. PLUS the universal blocklist: year strings 1990..2030, single characters, strings matching document numbers verbatim.

4. **Hit-count threshold by tier:**
   - `ENGINE`, `LANDING_GEAR`, `PROPELLER`, `ROTOR_SYSTEM`, `TRANSMISSION`, `APU`: **≥1 occurrence** (high-value tiers — single occurrence is likely real).
   - `AVIONICS`, `SYSTEMS`, `INTERIOR`: **≥2 occurrences** (low-value tiers — single occurrence is likely OCR noise).
   - `AIRFRAME`: **≥2 for structural components, ≥1 for repair-tracked items**.

5. **Tier inference from ATA chapter** — use `tiers_and_ata.md` mapping. Fall back to keyword scan of the description (`"engine"`, `"landing gear"`, `"main rotor"`, `"swashplate"`) only when ATA is missing.

6. **Same-PN clustering.** When multiple SNs appear under the same canonical PN, group them and mark as `siblings` — used by Phase 7.5 for sibling-PN limit propagation.

7. **Batch certificate detection.** When a single Form 8130 / Form 1 covers a serial range (e.g. `"SN 004-14658M thru 004-14759M"`), DO NOT create one component per SN in the range. Create one `serials` row per individual SN observed but anchor them all to the same parent `batch_certificate` reference. Phase 7.5 will close `FORM1_MISSING` for any SN in the covered range.

8. **OCR rejection:**
   - PN with mid-string spaces (`"123 4567"`) → `OCR_SUSPECTED` finding.
   - SN looking like a date (`"2024-01-15"`) → reject.
   - PN/SN containing visually similar OCR confusions in unusual positions (O↔0, l↔1, S↔5 in fields where context says digit/letter).
   - For high-value components (ENGINE, ROTOR), trigger a vision re-read on `enhanced_s3_key` before deciding.

---

## After applying the 8 rules

For each surviving (PN, SN) pair, **promote to a `components` row** when ANY of these is true:

- Appears on an `engine_llp_status_sheet` or `life_limited_parts_status` page → `is_llp = 1`.
- Appears on a `component_history_card` or `component_logbook`.
- Appears on a Form 1 / 8130 / TCCA / dual_release as the certified item.
- Appears in a parts table as `S/N On` (currently installed).
- Appears with overhaul / TBO / TSO entities nearby → `is_overhaul = 1`.

For each promoted pair:
- Insert into `part_types` (id = canonical PN), `serials` (id = `{pn}::{sn}`), `components` (id = `component::{pn}::{sn}`).
- Set `tier` from rule 5. Set `position` from header fields. Initial `status = 'DISCOVERED'`.
- Carry `ata_chapter`, `description`, `is_llp`, `is_overhaul` through.
- Set TSN/CSN/TSO/CSO from the highest evidentiary_weight page that mentions them.

---

## MANDATORY VERIFICATION

```sql
SELECT 'part_types' AS t, COUNT(*) AS n FROM part_types
UNION ALL SELECT 'serials',    COUNT(*) FROM serials
UNION ALL SELECT 'components', COUNT(*) FROM components;

SELECT tier, COUNT(*) FROM components GROUP BY tier;
SELECT is_llp, COUNT(*) FROM components GROUP BY is_llp;
```

```
- count(components)             : > 0    (must be > 0)
- count(part_types)             : > 0
- count(serials)                : > 0
- components grouped by tier    : at least one tier in expected_tiers represented
- LLP count                     : > 0 if dossier contains engine_llp_status_sheet pages
- count(components seeded from profile) : == len(asset_profile.expected_components.*)
```

**STOP conditions** — do NOT proceed to Phase 5 if:

- `count(components) == 0`.
- `count(components) == 1` AND that component was hand-coded from the folder name (cheating).
- `count(part_types) == 0`.
- The expected seeds from `asset_profile.expected_components` are not present as components.
- For an engine dossier with hundreds of LLP pages: `count(components WHERE is_llp = 1) == 0` — means LLP table parsing was silently skipped.

For a typical full aircraft dossier expect at least **20-50 components**. For an engine-only dossier expect at least **15-30** (LLPs, modules, accessories). One component for a 815-page dossier is not "rich" — it's evidence of a bug.
