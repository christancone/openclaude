# PHASE 7 — Component Investigation Loop

**Intent.** Walk every component's events chronologically, raise findings against the **Investigation Discipline checklist** and the **Severity Matrix**.

**Reference files (load all):**
- `investigation_discipline.md` — DO NOT SKIP. The discipline checklist is the difference between 100 real findings and 523 false positives.
- `severity_matrix.md` — DO NOT use a default severity; always look up by component criticality.
- `data_quality_rules.md`
- `finding_types.md`

**Inputs:** all tables through Phase 6.5.

The retrospective benchmark: original ATR72 run produced 523 raw findings. With the discipline checklist enforced + severity matrix, this dropped to ~100 genuine findings and ~30 L1.

---

## Processing order

```
1. All `priority_items` from Phase 6.5 first (regardless of tier).
2. Then tier priority order:
   ENGINE → ROTOR_SYSTEM → TRANSMISSION → LANDING_GEAR → PROPELLER →
   AIRFRAME → AVIONICS → APU → SYSTEMS → INTERIOR.
```

---

## You own the per-component loop — it is NOT a single script

Phase 7 is the most-cheated phase. The cheap path is `phase7.py` that does `SELECT * FROM components`, runs SQL probes, writes findings, and exits. **That path is banned.** Read it again in OVERVIEW.md "CODING vs JUDGEMENT". For each component you investigate:

1. Print a section header in your assistant text (visible in the UI):
   ```
   ### [Phase 7] component::<canonical_pn>::<sn>  —  <description>  (tier=<tier>, position=<pos>)
   ```

2. State your **expectation in 1-2 sentences**: what is this part, what records should exist for it, what would a Part-66 engineer expect to see. Example:
   > This is the LH PW127M LP compressor first-stage impeller, an LLP capped at 25,000 cycles by Pratt & Whitney Canada SB-NEW-127M-72-001. I expect a current LLP status sheet and a Form 1 issued by the last shop visit (HEICO Aerospace per the cover sheet) for the installed SN MN742.

3. Use the **Bash tool** to query the database for what's there. Use the **Read tool** to actually open the evidence page when the SQL points to one. Reading is the audit; SQL is the index.

4. Run the Investigation Discipline checklist (`investigation_discipline.md`). Record which boxes you ticked.

5. Decide. Apply the severity matrix (`severity_matrix.md`). Apply at most one downgrade rule. Write the finding with a 2-5 sentence `description` that cites the evidence file and page.

6. Append one line to `decisions.log` per the OVERVIEW format.

A `phase7.py` helper is fine for the SQL probes you call from your loop. The loop itself is YOU, calling the helper per component, reading evidence per component, deciding per component.

---

## Per-component checks

For each component, walk its events chronologically (sort by `event_date`, fall back to TSN/CSN when dates conflict). Run the **Investigation Discipline checklist** before raising any "missing" finding:

### Form 1 / 8130 chain
- Is there a Form 1 / 8130 / TCCA referencing the installed SN?
- If not, run the discipline checklist (`investigation_discipline.md`). If applicable items were skipped, write `provisional FORM1_MISSING`. Else write `open FORM1_MISSING`.
- Severity by component criticality (`severity_matrix.md`).

### Removal/installation continuity
- Every `component_removal` event needs a corresponding `component_installation` of the new SN.
- Gap → `CONTINUITY_BREAK`. Severity per matrix.

### LLP limits
- For `is_llp = 1` components, calculate `remaining_cycles = limit_cycles - csn` and `remaining_hours = limit_hours - tsn`.
- If limit is missing, run sibling-PN propagation (`data_quality_rules.md` rule 13) before flagging.
- `remaining_cycles < 500 OR remaining_hours < 500` → `LLP_LIMIT_CRITICAL` (always L1).
- `remaining_cycles < 1500 OR remaining_hours < 1500` → `LLP_LIMIT_WARNING` (always L1).

### Shop visit (engines)
- For `is_overhaul = 1` engine components, check last shop visit date.
- Compare current TSN to OEM-typical first-SVR interval (`data_quality_rules.md`).
- If within interval → no finding (informational only).
- If beyond interval and no SVR found → `SHOP_VISIT_MISSING`, severity per matrix.

### Time accounting
- TSN / CSN / TSO / CSO populated from highest evidentiary_weight source.
- If multiple values seen, store `_conflict` array.
- Could not be determined → `TIMES_INCOMPLETE`, severity per matrix.

### Date sanity
- Any event with `event_date` impossible per rule 2 → `DATE_ANOMALY`.

---

## Setting component status

After all checks for a component:

- `CLOSED` — all checks pass, full traceability.
- `PARTIAL` — some data missing but not airworthiness-critical.
- `GAP` — missing records on critical items (raises Level 1 finding).
- `INSTALLED_AT_MFG` — only OEM serialisation listing as birth record (acceptable).

---

## Writing findings

```python
# Apply severity matrix BEFORE writing
severity = severity_for(finding_type, component, severity_matrix)
original_severity = severity

# Apply downgrade rules (lease return, sibling, OEM-interval)
new_severity, reason = apply_downgrade_rules(severity, component, lease_return_state, ...)

cursor.execute("""
    INSERT INTO findings (id, target_kind, target_id, finding_type, severity,
        original_severity, severity_downgrade_reason, description, what_auditor_needs,
        file_name, page_index, chunk_id, status, discipline_complete)
    VALUES (?, 'COMPONENT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    fid, component_id, finding_type,
    new_severity, original_severity, reason,
    description, what_auditor_needs,
    fn, pi, ch,
    'provisional' if not discipline_complete else 'open',
    1 if discipline_complete else 0,
))
```

**Findings raised in this phase that did not complete the full discipline checklist get `status = 'provisional'`.** They feed Phase 7.5.

---

## MANDATORY VERIFICATION

```sql
SELECT COUNT(*) AS findings_total FROM findings;
SELECT severity, COUNT(*) FROM findings GROUP BY severity;
SELECT finding_type, COUNT(*) FROM findings GROUP BY finding_type ORDER BY 2 DESC LIMIT 15;
SELECT status, COUNT(*) FROM findings GROUP BY status;
SELECT discipline_complete, COUNT(*) FROM findings GROUP BY discipline_complete;
SELECT status, COUNT(*) FROM components GROUP BY status;
```

```
- count(findings)                                : > 0    (must be > 0 for non-trivial dossier)
- count(findings WHERE status = 'provisional')   : > 0 OR everything was discipline-complete
- count(findings WHERE discipline_complete = 1)  : > 0 if any 'missing'-type findings exist
- count(components WHERE status != 'DISCOVERED') : == count(components) (all components investigated)
- L1 share of total findings                     : 5..30% (very high if matrix misapplied)
```

**STOP conditions:**

- `count(findings) == 0` for a dossier with non-trivial events. Means investigation didn't run.
- `count(components WHERE status = 'DISCOVERED') == count(components)`. Means status was never set — investigation didn't run.
- L1 share > 50%. Means severity matrix was bypassed (everything defaulted to L1). Re-run with `severity_matrix.md` as binding rule.
- `count(findings WHERE discipline_complete = 0 AND status = 'open') > 0`. Means findings without discipline are leaking into `open` — they should be `provisional`.
- `count(findings WHERE finding_type NOT IN (the 30 strings in finding_types.md)) > 0`. Means new finding_types were invented — fix.

**Decisions log + reasoning quality gates** (these catch script-only Phase 7 runs):

```bash
# decisions.log must have one line per component you investigated
wc -l decisions.log
# Must be >= number of components processed (count(components WHERE status != 'DISCOVERED'))

# Spot-check: every L1 finding's description must be a sentence with a file:page citation
sqlite3 graph.db "
  SELECT COUNT(*) FROM findings
   WHERE status = 'open'
     AND severity = 1
     AND (description IS NULL
          OR length(description) < 80
          OR description NOT LIKE '%file:%'
          OR description NOT LIKE '%page:%');
"
# Must be 0.
```

- `decisions.log` line count for `[phase7]` < count(components investigated) → you skipped the per-component loop.
- Any L1 finding with `description` shorter than 80 chars or without a `(file:..., page:...)` citation → STOP. This is what the buyer will read; "Form 1 missing" is not enough.
- More than 50% of findings have `description` strings that are template-shaped (e.g. all start with "No Form 1 found for") → you wrote a SQL-string-template loop instead of reasoning per component. Re-do.
