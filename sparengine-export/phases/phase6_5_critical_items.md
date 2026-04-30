# PHASE 6.5 — Critical Items Pre-Scan

**Intent.** Identify the items that drive transaction value or airworthiness risk BEFORE the tier sweep. These get investigated FIRST in Phase 7. Plus detect the lease-return state if not already set in Phase 0.

**Reference files:**
- `data_quality_rules.md` (OEM-typical first-SVR intervals, lease-return patterns)
- `tiers_and_ata.md`

**Inputs:** all tables through Phase 6, especially `components`, `events`, `work_orders`.

The retrospective benchmark: in the original ATR72 run, the 605-cycle LP impeller on engine 127192 was buried at finding #66 because the agent worked tier-by-tier. It should have been #1. This phase prevents that.

---

## Critical-item detection rules

```
1. First-limited LLP per installed engine
   - For each engine, find the LLP with the lowest (remaining_cycles, remaining_hours).
   - That LLP is the engine's commercial floor.

2. Any component with <1,500 cy OR <1,500 h remaining
   - Across ALL tiers, not just engines.

3. Any engine approaching shop visit within 24 months at typical utilisation
   - Compute: (current_TSN - last_SVR_TSN) > 0.8 * OEM_typical_interval

4. Any LG actuator / shock strut approaching major-inspection limit
   - Tier LANDING_GEAR, components with limit_hours present and remaining_hours < 1000.

5. Any flight recorder (FDR / CVR) without a current calibration record
   - Calibration is annual or per-OEM; if last calibration > 1 year ago, surface here.

6. Damage on primary structure
   - Any `dent_and_buckle_chart` events on primary structural components
     (fuselage, wings, stabilisers, primary frames).
```

For each detected critical item, insert into `priority_items`:
```python
cursor.execute("""
    INSERT INTO priority_items (id, rank, component_id, reason, urgency, metric,
        evidence_file, evidence_page, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    f"prio::{component_id}::{reason}",
    rank,                    # 1 = highest priority
    component_id,
    reason,                  # 'first_limited_llp' | 'shop_visit_due_24mo' | 'damage_primary_structure' | 'fdr_calibration_overdue' | 'lg_inspection_due'
    urgency,                 # 'critical' | 'high' | 'medium'
    metric,                  # e.g. remaining_cycles for LLP cases
    evidence_file, evidence_page, notes
))
```

---

## Lease-return detection

If `asset_profile.state == 'lease_return'` from Phase 0, the lease return is already known — proceed directly to writing `lease_return_state` from the profile.

Otherwise, detect now:

```
- Count WOs with open_date in [dossier_date - 90 days, dossier_date].
- Count DUMMY tags / placeholder serials in events from that window.
- If WO_count_in_window > 50 OR dummy_tag_count > 5
  → mark asset as lease_return.
```

Insert into `lease_return_state`:
```python
cursor.execute("""
    INSERT INTO lease_return_state (asset_id, is_lease_return, window_start, window_end,
        wo_count_in_window, dummy_tag_count, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", (asset_id, is_lease_return, window_start, window_end,
       wo_count, dummy_count, notes))
```

This drives Phase 7's `LEASE_RETURN_GAP` severity downgrade for findings raised on documents inside the window.

---

## MANDATORY VERIFICATION

```sql
SELECT COUNT(*) AS priority_items_count FROM priority_items;
SELECT urgency, COUNT(*) FROM priority_items GROUP BY urgency;
SELECT * FROM lease_return_state;
```

```
- count(priority_items)         : depends on dossier; for an engine dossier with LLPs, > 0 expected
- count(distinct urgency)       : >= 1 if any priorities found
- count(lease_return_state)     : exactly 1 (one row per asset)
- lease_return_state.is_lease_return matches profile.state ('lease_return')
```

**STOP conditions:**

- `count(lease_return_state) != 1`.
- `count(priority_items) == 0` AND the dossier has any LLP records — means rule 1 was skipped.
- `count(priority_items) == 0` AND `asset_profile.state == 'lease_return'` — at minimum the lease-return WO cluster should generate priorities.
