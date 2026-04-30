# PHASE 8 — Asset-Level Investigation

**Intent.** Run the **mandatory checklist** at the asset level. Every item gets either an explicit finding OR an explicit "verified compliant" record. **No silent omissions.**

**Reference files:**
- `data_quality_rules.md`
- `severity_matrix.md`
- `finding_types.md`

**Inputs:** all tables through Phase 7.5.

---

## You answer each checklist item — do not stub

Same judgement rule as Phase 7 / 7.5. For each of the 12 checklist items below:

1. Print a section header:
   ```
   ### [Phase 8] <item name>  —  <asset_kind> <type_designation> <reg/msn>
   ```

2. State the question in your own words: what does this item mean for THIS asset, given its operator country, age, fleet position, and lease state. Example for AD compliance:
   > For an AW139 helicopter on an EASA-state operator (SE-HZJ → Sweden, EASA), the State of Design regulator is EASA. The Operator State is the same. Engine OEM is Pratt & Whitney Canada (PT6C-67C) — TCCA is the engine's regulator. Component AD applicability covers the rotor head, MGB, and emergency floats.

3. Use Bash to query the relevant tables (`requirements`, `events WHERE event_type='ad_compliance'`, etc.). Use Read to look at any AD compliance certificate the database points to.

4. Decide for each regulator: is the AD compliance verified, partially traced, or unverified? Cite the evidence. If unverified, raise a `AD_COMPLIANCE_UNVERIFIED` finding with a real reasoned description.

5. Append to `decisions.log`:
   ```
   [phase8] mandatory_checklist:ad_compliance | regulators_checked:[EASA,TCCA] | findings_raised:1 | evidence_pages_read:4 | outcome:partial | reason: EASA AD 2024-001 traced via SB-5021 in SVR-WP-419801.pdf p.18; TCCA equivalent not located after corpus search.
   ```

A `mandatory_checklist.json` produced by a hardcoded Python dict is **not Phase 8**. It's stub output. The check at the end of this file rejects it.

---

## Mandatory checklist (Phase 8 is not complete until every item is addressed)

```
☐ Asset TSN/CSN consensus per engine and at asset level
   Use the evidentiary_weight cascade. Reject sub-500h false readings from
   task duration fields. Record `assets.tsn`, `csn`, `tsn_confidence`,
   `csn_confidence`.

☐ AD compliance matrix per applicable regulator
   - State of Design regulator (EASA, FAA, TCCA, etc.)
   - Operator State authority (DGCA Indonesia, ANAC Brazil, CAAC China,
     FOCA Switzerland, GACA Saudi, etc.) if different from State of Design
   - Engine OEM ADs (per engine model)
   - Component-level ADs (LG OEM, propeller OEM, etc.)
   Output: one row per applicable AD with status (complied / not-complied /
   not-applicable / unverified) and source evidence.

☐ SB compliance list — every SB the operator has on record, with completion
   date or "not applicable" status.

☐ Major check history with next-due calculation
   (C-checks, structural checks, heavy maintenance, calendar checks).

☐ Dent and buckle chart status (for airframe assets)
   Every charted dent/buckle/repair traceable to a work record OR explicitly
   marked "monitored / no action required".

☐ Hard-time component status with remaining life per item.
   Use the hard-time / on-condition convention from data_quality_rules.md.

☐ Lease return / storage state determination
   Carry forward Phase 6.5's `lease_return_state` flag and document the
   WO-cluster / DUMMY-tag evidence.

☐ APU status — TSN/CSN, last shop visit, LLPs, AD compliance — OR an
   explicit "no APU" record if the asset doesn't have one.

☐ Engine TSN/CSN/TSO/CSO consensus per engine.

☐ Damage history (events with event_type == 'damage' and dent_and_buckle_chart documents).

☐ Operator country / state authority detection
   (drives MTS naming and AD applicability).
```

---

## Recording outcomes

For each checklist item:

- **If the item is satisfied** (record was found, AD complied, SB tracked, etc.) — write a row to a new `audit_outcomes` table OR add a structured note in `progress.log` with `status: verified_compliant` and the evidence (file, page, quote).
- **If the item could not be answered** — raise a finding (`AD_COMPLIANCE_UNVERIFIED`, `GAP_IN_DOSSIER`, etc.) rather than silently omitting it.

The mandatory checklist is what makes the dossier audit complete; the findings tell the buyer what's still open.

For the `graph_export.json`'s `mandatory_checklist` field (Phase 10):

```json
{
  "tsn_csn_consensus":     { "status": "verified", "value": 9575, "evidence": [...] },
  "ad_compliance_eass":    { "status": "unverified", "open_findings": ["..."] },
  "ad_compliance_operator": { "status": "verified", "covered_count": 12 },
  "sb_compliance":         { "status": "verified", "covered_count": 28 },
  "major_check_history":   { "status": "verified", "next_due": "2027-03-15" },
  "dent_buckle":           { "status": "n/a", "reason": "engine-only dossier" },
  "hard_time":             { "status": "verified", "items_tracked": 14 },
  "lease_return":          { "status": "verified", "is_lease_return": false },
  "apu":                   { "status": "n/a", "reason": "engine-only dossier" },
  "engine_tsn_csn":        { "status": "verified", "engines": [...] },
  "damage_history":        { "status": "verified", "events_count": 0 },
  "operator_state":        { "status": "verified", "country": "ID", "regulator": "DGCA" }
}
```

---

## MANDATORY VERIFICATION

```sql
-- Findings raised in Phase 8
SELECT finding_type, COUNT(*) FROM findings
WHERE finding_type IN ('AD_COMPLIANCE_UNVERIFIED', 'SB_COMPLIANCE_UNVERIFIED',
                       'AD_NOT_LISTED', 'SB_NOT_LISTED', 'GAP_IN_DOSSIER',
                       'TIMES_INCOMPLETE')
GROUP BY 1;

-- Asset row updated with consensus values
SELECT id, tsn, csn, tsn_confidence, csn_confidence FROM assets;
```

```
- assets.tsn / csn populated and not 0          : yes (or have an explicit unverified finding)
- assets.tsn_confidence in {high, medium, low}  : yes (not null)
- mandatory_checklist has every item            : yes (12 items per the list above)
- every checklist item has status != 'silent_omit' : yes
```

**STOP conditions:**

- Any checklist item silently omitted (no `verified_compliant` record AND no finding).
- `assets.tsn = 0 AND tsn_confidence = 'low'` from Phase 0 still in place — Phase 8 must reconcile this from the corpus.
- The mandatory_checklist payload (for Phase 10) is missing any of the 12 items.
- Any value in `mandatory_checklist.json` evidence/value contains "Simulated", "Stub", "Mock", "Placeholder", "TODO", or "Demo" — **cheating**. The values must come from real SQL queries against `pages`, `events`, `requirements`, `assets`. If you cannot answer a checklist item from the corpus, raise the corresponding finding (`AD_COMPLIANCE_UNVERIFIED`, `GAP_IN_DOSSIER`, `TIMES_INCOMPLETE`) and set the checklist item's status to `unverified` — never invent a number.

  Verification:

  ```python
  import json, re, sqlite3
  mc = json.load(open('mandatory_checklist.json'))
  bad = re.compile(r'Simulat|Stub|Mock|Placeholder|TODO|Demo', re.I)
  for k, v in mc.items():
      blob = json.dumps(v)
      assert not bad.search(blob), f"Phase 8 cheat detected in {k}: {blob}"

  # Known fabricated values from past runs — these specific numbers were
  # the agent's internal placeholder, reused across unrelated assets. Block them.
  KNOWN_PLACEHOLDERS = {5432.1, 3210}
  c = sqlite3.connect('graph.db').cursor()
  tsn, csn = c.execute('SELECT tsn, csn FROM assets').fetchone()
  assert tsn not in KNOWN_PLACEHOLDERS, (
      f"assets.tsn = {tsn} is a known fabricated placeholder. "
      "Re-run TSN consensus from `events.tsn_at_event` and `pages` for the actual value."
  )
  assert csn not in KNOWN_PLACEHOLDERS, (
      f"assets.csn = {csn} is a known fabricated placeholder."
  )
  ```

  When `assets.tsn` cannot be determined from the corpus, set it to `NULL` and
  raise a `TIMES_INCOMPLETE` finding. Never substitute a made-up number.
