# DATA QUALITY RULES — universal

Reference file. Load when running phases 6.5, 7, 7.5, 8.

These apply on top of what the OCR already validated. They are calibrated against the ATR72-1191 retrospective, where naive parsing produced 523 raw findings that collapsed to 100 once these rules were applied.

---

## Universal rules (apply every phase that emits findings)

1. **Trust per-entity confidence.** Carry `entities[].confidence` through every derived edge. Never upgrade confidence; only carry or downgrade.
2. **Impossible dates.** Any date before the asset's manufacture year, or more than 6 months after the dossier date → `DATE_ANOMALY`. Do not use as evidence.
3. **Universal SN blocklist** (per dossier): asset MSN, registration mark, primary engine SNs (engine-only dossiers), year strings 1990..2030, single characters, document numbers matching SN values verbatim.
4. **TSN false readings.** Values from "Total Hours" or "Hours" fields dramatically smaller than known asset TSN are task durations or leg hours, not asset totals. Cross-check evidentiary_weight before accepting.
5. **OCR_SUSPECTED entities.** When `entities[].confidence == "low"` AND the entity is critical (PN/SN/WO/AD/SB), trigger a vision re-read on `enhanced_s3_key` for that page.
6. **Trust `task_compliance_status`, do not re-derive.** OCR has done the work; act on the value.
7. **MTS source naming.** "CAMP" inside a regulatory citation = "Continuous Airworthiness Maintenance Program" (concept). "CAMP" as `mis_system` in OCR metadata = the software product. Do not conflate.
8. **Component TSN > airframe TSN is normal** — prior service history on another asset. Trace the prior history; do not flag.
9. **Batch certificates.** Form 1 / 8130 covering serial ranges are valid. Before raising `FORM1_MISSING` for an individual SN, parse the range and check membership. If the SN falls inside any batch range, close as false positive.
10. **Lease return context.** DUMMY UNSERVICEABLE tags, placeholder serials, tight WO clusters in the weeks before dossier date → asset is being prepared for redelivery. Documentation gaps in this window are sequencing issues (Level 2), not airworthiness violations (Level 1).
11. **Evidentiary weight conflict resolution.** Primary > Secondary > Reference > Administrative. Within the same weight: physical > MIS export. Within those: most recent date. Within those: highest entity confidence.
12. **Rotated pages.** If `rotation_hint != 0` AND derived edges from that page have low confidence, queue a vision re-read.
13. **Sibling-PN limit propagation.** OEM publishes life limits per PN, not per SN. If a component has a missing limit but the same canonical PN appears elsewhere on the asset (sibling engine, opposite position, batch installation), copy the limit from the sibling with `confidence = high` and source `sibling_propagation`. Only flag `LLP_LIMIT_CRITICAL` after this lookup.

---

## Aviation domain patterns — DO NOT FLAG these as errors

Every one of these was a false-positive driver in the original ATR72 run.

- **Sentinel date `9999-12-31`** in any date field — MTS placeholder for "no due date / unlimited / N/A". Treat as `null`. Not an OCR error.
- **Form 1 issue date older than signature date** — re-release after overhaul is the most common cause. Acceptable unless the gap exceeds ~2 years.
- **Component TSN > airframe TSN** — prior installation on another asset. Trace; never flag.
- **Propeller hub TSN > airframe TSN** — same as above; common because props move between aircraft.
- **DUMMY UNSERVICEABLE / DUMMY INSTALLATION PERFORMED** in tag text — storage / lease return convention, not an airworthiness defect.
- **"NOT REQUIRED FOR THIS INPUT" / "N/A" in task action-taken fields** — task was correctly skipped per the work scope. Not a missing sign-off.
- **WO series clustering near dossier date** (e.g. 200+ WOs in the final 60 days, often a `419xxx` range or similar operator-specific block) — asset stripping for redelivery. All gaps in this window are Level 2 maximum.
- **Engine "PART OUT (SVC) — CUSTOMER REQUEST"** — operator harvesting serviceable components for spares before redelivery. Commercial state, not airworthiness.
- **Indonesian / Asian / European operators using "CAMP REFERENCE"** — almost always means the regulatory concept "Continuous Airworthiness Maintenance Program", not the US software product. Their actual MTS is usually AMOS, SAP, MXP, or a custom system. Detect operator country before raising `MTS_CONFLICT`.
- **Operator-consolidated status sheets** — file names matching the asset MSN/registration prefix (e.g. `MSN_1191_*.pdf`, `<reg>_status_*.pdf`) carry concentrated component status. Always read these in full when investigating any component the operator tracks.

---

## OEM-typical first shop visit intervals (sanity baseline)

Use these intervals **before** raising `SHOP_VISIT_MISSING`. An engine within its OEM-typical first-SVR interval with no SVR record is informational, not a finding.

```
PW100 family (incl. PW127M, PW127N, PW150)   ~10,000 - 12,000 hours
PT6A family                                   ~3,500 - 5,000 hours (high variance)
CFM56-3                                       ~12,000 - 15,000 hours
CFM56-5B / -7B                                ~18,000 - 22,000 hours
LEAP-1A / -1B                                 ~20,000+ hours
V2500 (IAE)                                   ~15,000 - 18,000 hours
GE90 / GEnx                                   ~20,000 - 25,000 hours
Trent 700 / 800 / 900 / 1000                  ~18,000 - 22,000 hours
PW4000 family                                 ~16,000 - 20,000 hours
RB211                                         ~12,000 - 15,000 hours
Rolls-Royce 250-C20 / -C30 series             ~3,000 - 3,500 hours TBO
```

These are typical-fleet figures, not contractual. Use as sanity bounds. The engine OEM's published interval (when present in the dossier) always wins.

---

## Hard-time / on-condition convention

- **Hard-time** components must be removed at the calendar/cycles/hours limit regardless of condition. Track remaining life; flag at <500 h/cy.
- **On-condition** components are removed when condition demands; no scheduled removal limit. Do not flag for missing remaining-life data.
- **Soft-time / TBO** is informational; the operator may extend TBO under approved conditions.

When in doubt, default to **on-condition** (no finding) rather than hard-time (false positive).
