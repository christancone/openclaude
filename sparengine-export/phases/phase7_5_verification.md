# PHASE 7.5 — Verification Pass

**Intent.** For every finding from Phase 7 (open OR provisional), run a second pass with strategies the original investigation may not have used. Close false positives.

**Reference files:**
- `investigation_discipline.md`
- `data_quality_rules.md`
- `severity_matrix.md`

**Inputs:** all tables through Phase 7.

**Retrospective benchmark.** This phase closed ~80 findings out of 263 in the original ATR72 run. Skipping it is the single biggest source of false-positive inflation that survives into the final report.

---

## You own the per-finding verification loop

Same rule as Phase 7: this is judgement, not a one-shot script. For each finding you re-examine:

1. Print a section header in assistant text:
   ```
   ### [Phase 7.5] <finding_id>  —  <finding_type> on <component or asset>
   ```

2. State 1-2 sentences on **what you'd expect to find that would close this**. Example:
   > FORM1_MISSING for SN MN742 on PN 350A32-0110: a batch Form 8130 covering the manufacturing range, or a re-issue under the alternate vendor PN, or a sibling-engine Form 1 with the same canonical PN.

3. Run the **applicable verification strategies** (1-9 below). Use the **Read tool** when the strategy points to a candidate file. Don't just SQL — open the page and confirm.

4. Decide:
   - Resolved → set `status = 'closed'` (or `false_positive`), record the closing evidence (`resolution_file`, `resolution_page`, `resolution_quote`, `verification_strategy`).
   - Not resolved but downgrade applies → keep `open`, set `severity_downgrade_reason`, preserve `original_severity`.
   - Not resolved → keep `open`, fill in any discipline boxes Phase 7 left empty.

5. Append to `decisions.log`:
   ```
   [phase7.5] <finding_id> | strategies_run:[batch_range, sibling_propagation, oem_typical] | evidence_pages_read:2 | outcome:closed | reason:Batch Form 8130 in WO-419012-cert.pdf p.5 covers SN range 004-14658M..004-14759M, MN742 falls in range. Closed as false_positive.
   ```

The strategy name MUST be one of the actual nine below. Strings like `"Simulated Verification"` or `"Stub"` fail the Phase 7.5 STOP condition and the run is rejected.

---

## Verification strategies

For each finding, run the strategies applicable to its type:

### Strategy 1 — Re-search by SN alone (drop the PN)

Form 1s often filed by SN only. Run FTS / filename search for the SN substring across the whole corpus.

### Strategy 2 — Re-search by alternate PN

Pull `alternate_pns` from `part_types` for the component. Re-search for each alternate.

### Strategy 3 — File-name substring search

Search `documents.file_name` for the PN substring AND the SN substring independently.

### Strategy 4 — Batch certificate range membership

For `FORM1_MISSING`: parse all Form 8130 / EASA Form 1 documents that reference the canonical PN. If any covers a serial range and the SN falls in that range → close as false positive.

```python
# Pseudo-code:
for form1_page in find_form1_pages_for_pn(canonical_pn):
    range = parse_serial_range(form1_page.text)  # e.g. "004-14658M thru 004-14759M"
    if range and sn_in_range(component.installed_sn, range):
        close_finding(finding_id, 'false_positive',
            strategy='batch_certificate_range',
            resolution_quote=form1_page.quote,
            resolution_file=form1_page.file_name,
            resolution_page=form1_page.page_index)
```

### Strategy 5 — Sibling-PN limit propagation

For `LLP_LIMIT_CRITICAL` with **missing limit** (not threshold findings): query sibling components (same canonical PN on the other engine / position). If a sibling has the limit populated → copy with `confidence='high'`, `source='sibling_propagation'`. If recomputed remaining exceeds threshold → close.

### Strategy 6 — OEM-typical interval check

For `SHOP_VISIT_MISSING`: compare current TSN to OEM-typical first-SVR interval (`data_quality_rules.md`). If within interval → close as false positive (informational only).

### Strategy 7 — WO package re-read for stamps

For `TASK_NOT_CONFIRMED`: re-read the entire WO package. Stamps are sometimes on the certificate page (last page of the package), not on the task page. Cross-reference `stamps` table with `binds_to_target_ref` pointing to a different page in the same WO.

### Strategy 8 — Sentinel-date check

For `DATE_ANOMALY`: check if the value is the sentinel `9999-12-31` → close (not an error). Check if it's the asset birth-year offset by one or two centuries (typo) → close with corrected date.

### Strategy 9 — Context discrepancy re-verification

For `CONTEXT_DISCREPANCY`: re-verify against the asset table. If the page reference value matches `assets.operator/registration/msn` at all, it's probably not a real discrepancy.

---

## Updating the finding row

Each finding takes one of three states out of this phase:

```python
# Survived all applicable verifications — REAL.
cursor.execute("""
    UPDATE findings
       SET status = 'open',
           verification_strategy = ?,
           discipline_complete = 1
     WHERE id = ?
""", (strategies_applied, finding_id))

# Verification found resolving evidence — CLOSED.
cursor.execute("""
    UPDATE findings
       SET status = 'closed',
           verification_strategy = ?,
           resolution = ?,
           resolution_file = ?,
           resolution_page = ?,
           resolution_chunk_id = ?,
           resolution_quote = ?
     WHERE id = ?
""", (strategy, reason, fn, pi, ch, quote, finding_id))

# Cannot resolve, but lease-return / sibling context demands a downgrade.
cursor.execute("""
    UPDATE findings
       SET status = 'open',
           severity = ?,
           severity_downgrade_reason = ?,
           verification_strategy = ?
     WHERE id = ?
""", (downgraded_severity, reason, strategy, finding_id))
```

`original_severity` is preserved for audit. `severity` is the current value. `verification_strategy` is which strategy closed/downgraded the finding.

---

## Provisional findings still pending

Provisional findings that did NOT complete the Investigation Discipline checklist in Phase 7 must complete it here before they can be set to `open`. If the discipline still cannot be completed (e.g. dossier truly does not contain the missing record), promote to `open` with `discipline_complete = 1` and the verification trail attached.

---

## MANDATORY VERIFICATION

```sql
SELECT status, COUNT(*) FROM findings GROUP BY status;
SELECT verification_strategy, COUNT(*) FROM findings
WHERE verification_strategy IS NOT NULL GROUP BY 1;
SELECT severity_downgrade_reason, COUNT(*) FROM findings
WHERE severity_downgrade_reason IS NOT NULL GROUP BY 1;

-- Closure rate:
SELECT
    1.0 * SUM(CASE WHEN status IN ('closed', 'false_positive') THEN 1 ELSE 0 END)
    / NULLIF(COUNT(*), 0) AS closure_rate
FROM findings;
```

```
- count(findings WHERE status = 'provisional')           : 0    (every provisional was resolved one way or another)
- count(findings WHERE discipline_complete = 0)          : 0    (every finding completed discipline)
- closure_rate (lease-return dossiers)                   : 0.50 .. 0.80
- closure_rate (operational dossiers)                    : 0.20 .. 0.40
```

**STOP conditions:**

- Any `provisional` findings remain. They cannot ship.
- Any `discipline_complete = 0` findings remain.
- Closure rate < 0.20 — Phase 7.5 is incomplete. Re-run the strategies you skipped.
- Closure rate > 0.95 — almost everything closed. Suggests Phase 7 over-flagged or Phase 7.5 is being too lenient. Spot-check 10 closed findings.
- Any `verification_strategy` value contains the words `Simulated`, `Stub`, `Mock`, `Placeholder`, `TODO`, or `Demo` (case-insensitive). **The strategies must actually run** — re-search FTS, re-parse Form 8130 ranges, query siblings, look at OEM intervals. Naming a strategy `"Simulated Verification"` and writing 16 closures with that label is **cheating** — the past run's `verification_stats.json` did exactly this and it must be rejected.

  Verification SQL:

  ```sql
  SELECT COUNT(*) AS bogus_strategies
  FROM findings
  WHERE verification_strategy LIKE '%Simulat%'
     OR verification_strategy LIKE '%Stub%'
     OR verification_strategy LIKE '%Mock%'
     OR verification_strategy LIKE '%Placeholder%'
     OR verification_strategy LIKE '%TODO%'
     OR verification_strategy LIKE '%Demo%';
  -- Must be 0.
  ```
