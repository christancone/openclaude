# PHASE 9 — Finding Consolidation

**Intent.** Roll up duplicate findings, close findings whose resolution evidence appeared elsewhere in the dossier, collapse batch-covered findings.

**Reference files:** `severity_matrix.md`, `data_quality_rules.md`.

**Inputs:** all tables through Phase 8.

---

## Consolidation rules

### Rule 1 — Same-type roll-up

If 10+ findings have the same `finding_type` AND share a tier or component-class, create one summary finding listing the affected SNs in `description`. Mark the originals `closed` with `verification_strategy = 'rolled_into_summary'` and `resolution_file = '<summary_finding_id>'`.

```sql
-- Find candidates
SELECT finding_type, COUNT(*) FROM findings
WHERE status = 'open'
GROUP BY finding_type
HAVING COUNT(*) >= 10;
```

### Rule 2 — Batch certificate close-out

If a batch certificate covers a serial range (already detected in Phase 4 / 6), every individual `FORM1_MISSING` for an SN in that range becomes `closed` (false_positive) — Phase 7.5 should already have done this; Rule 2 here is a safety net for findings that were re-raised after Phase 7.5.

### Rule 3 — Cross-finding closure

For every open finding, scan other findings in the same dossier for resolution evidence:
- A `WORK_PACKAGE_WITHOUT_CRS` finding closes when a sibling finding for a different page in the same WO turns up the CRS.
- A `FORM1_SN_NOT_VERIFIED` closes when a re-issued Form 1 with the correct SN appears in the dossier (possibly raised as a separate finding type but containing the resolution).

### Rule 4 — Provisional cleanup (final pass)

Any finding still `provisional` at the start of Phase 9 — promote to `open` after running discipline + verification one more time. If discipline still cannot complete, write `GAP_IN_DOSSIER` instead and close the original.

### Rule 5 — Severity sanity

After consolidation, audit:
- L1 share should be 5-15% of total open findings.
- If a downgrade rule wasn't applied where it should have been, apply now.
- L1 findings without `original_severity` populated → backfill.

---

## Build `findings_summary` for Phase 10

This is the JSON shape Phase 10 reads:

```json
{
  "severity_counts": { "1": 12, "2": 47, "3": 89 },
  "by_type": [
    { "finding_type": "FORM1_MISSING",    "count": 23, "severity_breakdown": {"1": 8, "2": 15} },
    { "finding_type": "TASK_NOT_CONFIRMED","count": 14, "severity_breakdown": {"2": 14} }
  ],
  "by_component": [
    { "component_id": "component::3036041-01::CAE-840837", "count": 5, "max_severity": 1 }
  ],
  "level_1_lists": [
    { "finding_type": "LLP_LIMIT_CRITICAL", "ids": ["fid::...", "fid::..."] }
  ]
}
```

Stash this in a JSON file at `workdir / "findings_summary.json"` for Phase 10 to read, or compute on the fly in Phase 10.

---

## Verification stats payload (Phase 7.5 result, for the visualiser)

Build:

```json
{
  "phase7_findings_raw":      263,
  "phase7_5_closed":          178,
  "phase7_5_closure_rate":    0.677,
  "phase7_5_open_remaining":   85,
  "by_strategy": [
    { "strategy": "batch_certificate_range", "closed": 42 },
    { "strategy": "sibling_propagation",     "closed": 31 },
    { "strategy": "oem_typical_interval",    "closed": 18 }
  ]
}
```

This drives the Audit Quality panel in the template.

---

## MANDATORY VERIFICATION

```sql
SELECT status, COUNT(*) FROM findings GROUP BY status;
SELECT severity, COUNT(*) FROM findings WHERE status = 'open' GROUP BY severity;
SELECT 1.0 * SUM(CASE WHEN severity = 1 THEN 1 ELSE 0 END) / COUNT(*)
       AS l1_share FROM findings WHERE status = 'open';
```

```
- count(findings WHERE status = 'provisional')      : 0
- L1 share of open findings                         : 0.05 .. 0.30
- findings_summary.severity_counts present          : yes
- verification_stats.closure_rate present           : yes
```

**STOP conditions:**

- Any `provisional` rows remain.
- L1 share > 50% AND no severity_downgrade_reason populated → matrix not applied.
- `findings_summary` missing or empty AND `count(findings) > 0` — means Phase 10 will render a stub.

**Description quality gates** (these are what the buyer reads — they cannot be one-liners):

```sql
-- L1 findings without a real description
SELECT COUNT(*) FROM findings
 WHERE status = 'open'
   AND severity = 1
   AND (description IS NULL
        OR length(description) < 80
        OR description NOT LIKE '%file:%'
        OR description NOT LIKE '%page:%');
-- Must be 0.

-- All-findings template-shape detection (lazy template-string descriptions)
SELECT description, COUNT(*) AS n
  FROM findings WHERE status='open'
 GROUP BY description
 HAVING n >= 5;
-- If ANY description appears 5+ times verbatim, you wrote a template loop instead
-- of reasoning per finding. STOP and rewrite.
```

```
- L1 findings without 80-char + file:page description : 0
- duplicate description count >= 5                    : 0
```

**Decisions log gate** (catches script-only Phase 7/7.5/8 runs):

```bash
test -f decisions.log || (echo "decisions.log missing — STOP" && exit 1)
grep -c '^\[phase7\]'    decisions.log    # must be >= count(components investigated)
grep -c '^\[phase7\.5\]' decisions.log    # must be >= count(findings touched in 7.5)
grep -c '^\[phase8\]'    decisions.log    # must be exactly 12 (one per checklist item)
```

If any of these counts are below threshold, the agent skipped reasoning. STOP, re-do that phase with the per-item loop.
