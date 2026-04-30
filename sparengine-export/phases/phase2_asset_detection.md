# PHASE 2 — Asset Detection (confirmation against profile)

**Intent.** Write the `assets` row from `asset_profile.json`, then confirm each profile field against the indexed corpus. Any contradiction raises `CONTEXT_DISCREPANCY`.

**Reference files to load:**
- `tiers_and_ata.md` (asset detection signals)
- `finding_types.md` (for `CONTEXT_DISCREPANCY`)

**Inputs:**
- `asset_profile.json`
- `pages`, `documents` (Phase 1 output)

---

## Steps

1. **Read `asset_profile.json`.** This is the source of truth.

2. **Insert one row into `assets`** populated from the profile:
   ```python
   cursor.execute("""
       INSERT INTO assets (id, asset_kind, subtype, type_designation, tcds, yom,
           msn, registration, registration_history, operator, owner, primary_serial,
           state, tsn, csn, tsn_confidence, csn_confidence, dossier_date, profile_json)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
   """, (...))
   ```
   - `id` = `f"asset::{asset_class}::{primary_serial or msn or registration}"`.
   - `asset_kind` = profile's `asset_class` upper-cased (`AIRCRAFT`, `ENGINE`, etc.).
   - `profile_json` = the entire profile JSON as a string (audit trail).

3. **Aggregate from the corpus** (use SQL on `pages`):
   - Most common `registration` value across pages with that entity.
   - Most common `msn`, `esn` values.
   - Most common `operator`.
   - Most common `mis_system`.
   - Latest `dossier_date` candidate.

4. **Compare each aggregated value against the profile:**
   - Mismatch on `type_designation` → `CONTEXT_DISCREPANCY` finding (severity per matrix; default L2).
   - Mismatch on `registration` after accounting for `registration_history` → `CONTEXT_DISCREPANCY`.
   - Mismatch on `operator` (after fuzzy-match for whitespace / Ltd vs Limited) → `CONTEXT_DISCREPANCY`.
   - Mismatch on `mis_system` → `MTS_CONFLICT` (apply CAMP-as-concept-not-software rule from `data_quality_rules.md`).

5. **Raise `CONTEXT_DISCREPANCY` per page** that has `pages.context_discrepancy IS NOT NULL`.

6. **Log a per-field reconciliation table** to `progress.log`:
   ```
   field            profile          corpus_majority   verdict
   ---------------- ---------------- ----------------- -------
   type_designation Rolls-Royce 250  Rolls-Royce 250   match
   registration     null             null              match
   esn              CAE-840837       CAE-840837        match
   ```

---

## MANDATORY VERIFICATION

```sql
SELECT 'assets' AS t, COUNT(*) AS n FROM assets;
SELECT id, asset_kind, type_designation, msn, registration FROM assets;
```

```
- count(assets)                       : must be exactly 1
- assets.asset_kind populated         : yes
- assets.profile_json IS NOT NULL     : yes
- assets.dossier_date populated       : yes
- count(findings WHERE finding_type='CONTEXT_DISCREPANCY') logged
```

**STOP conditions:**

- `count(assets) != 1`.
- `assets.profile_json IS NULL` (means you didn't actually load the profile).
- Any field on `assets` is the literal string `"unknown"` AND the profile has it populated — that means you wrote stub data instead of reading the profile.
