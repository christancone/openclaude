# PHASE 3 — Tier Group Creation

**Intent.** Insert virtual `TIER_GROUP` nodes for the tiers in `asset_profile.expected_tiers`, plus `HAS_TIER` edges from the asset to each.

**Reference files:** `tiers_and_ata.md`.

**Inputs:** `assets` row, `asset_profile.json` (for `expected_tiers`).

---

## Steps

1. Read `expected_tiers` from `asset_profile.json` (or from `assets.profile_json`).

2. For each tier in that list, insert into `edges`:
   ```python
   for tier in expected_tiers:
       tier_id = f"tier::{tier}"
       cursor.execute("""
           INSERT OR IGNORE INTO edges (id, source_id, source_kind, target_id, target_kind,
               edge_type, confidence, evidence_file, evidence_page, evidence_quote)
           VALUES (?, ?, 'ASSET', ?, 'TIER_GROUP', 'HAS_TIER', 'high',
                   'asset_profile.json', 0,
                   ?)
       """, (
           f"edge::has_tier::{tier}",
           asset_id,
           tier_id,
           f"expected_tiers contains {tier}",
       ))
   ```

3. Phase 3 does not create rows in `components`. The `tier::*` ids are referenced by Phase 10's `nodes` array directly — no separate node table needed (vis-network template treats `_status: 'TIER_GROUP'` as the tier-group renderer).

---

## MANDATORY VERIFICATION

```sql
SELECT COUNT(*) AS has_tier_edges
FROM edges
WHERE edge_type = 'HAS_TIER';
```

```
- count(edges WHERE edge_type='HAS_TIER') : must equal len(expected_tiers)
- each expected tier has exactly one edge : yes
```

**STOP conditions:**

- `has_tier_edges == 0`.
- `has_tier_edges != len(expected_tiers)`.
