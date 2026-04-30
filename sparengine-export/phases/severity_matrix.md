# SEVERITY MATRIX — criticality-by-component

Reference file. **The single biggest source of false-positive inflation if ignored.**

A `FORM1_MISSING` on an engine LLP is airworthiness; a `FORM1_MISSING` on a PBE oxygen generator is a paperwork gap. The original ATR72 run produced 263 raw L1 findings of which only 30 were genuine — almost entirely because every `FORM1_MISSING` defaulted to L1 regardless of what component it was on.

**The severity of any finding depends on what type of component it is on, not on the finding type alone.**

---

## Level 1 (airworthiness — must resolve before transaction)

```
FORM1_MISSING / FORM1_SN_NOT_VERIFIED on:
  - Engine LLPs (HPC/HPT/LPC/LPT disks, shafts, impellers)
  - Engine modules subject to mandatory life limits
  - Landing gear primary assemblies (MLG/NLG shock struts, drag braces,
    main fittings, retraction actuators with major-inspection limits)
  - Propeller hubs
  - Rotor head / swashplate / pitch links (helicopters)
  - MGB / IGB / TGB main casings and shafts (helicopters)
  - Flight recorders (FDR, CVR)
  - Primary structural repairs (SRPSA, REO on primary structure)

LLP_LIMIT_CRITICAL                  (always — <500 cy or <500 h remaining)
LLP_LIMIT_WARNING                   (always — <1500 cy or <1500 h remaining)
AD_COMPLIANCE_UNVERIFIED            (always — every applicable AD must be traced)
CONTINUITY_BREAK on:
  - Engines
  - Landing gear primary assemblies
  - Propeller hubs
  - Rotor head / MGB (helicopters)
SHOP_VISIT_MISSING on engines beyond OEM-typical first-SVR interval
DAMAGE_NOT_TRACED on primary structure
HARD_TIME_LIMIT_APPROACH on critical hard-time items (<500 h to limit)
```

---

## Level 2 (data correction — work was done but not recorded properly)

```
FORM1_MISSING / FORM1_SN_NOT_VERIFIED on:
  - On-condition accessories (sensors, transmitters, igniters, HBV, servos,
    fuel nozzles, fire extinguisher cartridges, valves, thermocouples,
    actuator subcomponents)
  - Emergency equipment (PBE, ELT, life rafts, slide rafts, emergency
    batteries, emergency lights, oxygen generators, escape ropes)
  - Cabin / interior items (seats, galley equipment, lavatory components)
  - Secondary structural items not on the primary load path

TIMES_INCOMPLETE on non-LLP components
DATE_ANOMALY where OCR is the likely cause and a correction is recoverable
TASK_NOT_CONFIRMED                  (default L2; only L1 if task is on a
                                     Level 1 component above)
SB_WITHOUT_CRS / AD_WITHOUT_CRS    (default L2; L1 only if the SB/AD is
                                     safety-critical and uncovered by another CRS)
WORK_PACKAGE_WITHOUT_CRS
LEASE_RETURN_GAP                   (always — documentation gaps in the
                                     lease-return window are sequencing issues,
                                     never airworthiness)
SB_COMPLIANCE_UNVERIFIED            (default L2 unless the SB is mandatory)
PRIOR_HISTORY_MISSING               (default L2)
SN_AMBIGUOUS                        (default L2)
MTS_CONFLICT                        (always L2 — physical record wins)
DAMAGE_NOT_TRACED on secondary structure
ICA_NOT_ENROLLED                    (default L2 unless STC is recent)
SHOP_VISIT_MISSING on engines within OEM-typical first-SVR interval
                                    (downgrade to L2 — informational, not airworthiness)
```

---

## Level 3 (improvement — housekeeping, future audits will surface again)

```
PN format variants (A36.560-1 / A36560-1, dashes vs dots, leading zeros)
SN suffix variants (R25-1M2C / R25-1M2CW)
Transcription typos (30396098 vs 3039609)
Notation conventions ("NA" vs "N/A" vs "—" vs blank)
Hard-time sheet calculation rounding errors (off by one cycle)
SB compliance noted in SVR text but full SB document not in dossier
ALTERNATE_PN_NOT_LINKED
PN_ALTERNATE_UNRESOLVED
OCR_SUSPECTED on non-critical entities
STAMP_AMBIGUOUS_BINDING on non-critical events
ROTATED_PAGE_LOW_CONF
CONTEXT_DISCREPANCY on non-identifying fields
REPAIR_TEMPORARY where monitoring is the agreed disposition
```

---

## Severity downgrade rules (apply AFTER the matrix above)

A finding's severity may be downgraded one level (never upgraded) by these context rules:

1. **Lease return window.** If the asset is in the lease-return window (Phase 6.5 detection), downgrade any L1 finding raised on a document inside that window to L2. Exception: LLP limits and AD compliance never downgrade — those are objective regardless of context.
2. **Batch certificate covers the SN.** If a batch certificate covers the SN's range, the finding is **closed** (false positive), not downgraded.
3. **Sibling component has the data.** If a sibling component (same PN on the other engine / position) has the missing data populated, the finding is closed with the sibling as evidence.
4. **OEM-typical interval.** For `SHOP_VISIT_MISSING`, if the engine is within the OEM-typical first-SVR interval, downgrade L1 → L2 → no finding (informational only).

The severity ladder, top to bottom: write the matrix-default severity into `original_severity`, apply at most one downgrade rule into `severity` and `severity_downgrade_reason`, then commit the finding.

---

## Quality benchmark

After Phase 7.5, the L1 share of total findings should be **5-15%**. If >25% of findings are L1, the matrix is being misapplied — re-run Phase 7 treating the matrix as binding rule, not a guideline.
