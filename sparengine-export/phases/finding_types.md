# FINDING TYPES — exact strings

Reference file. Use these strings verbatim in `findings.finding_type`. No new ones; no aliases; no lowercasing.

```
TIMES_INCOMPLETE         - TSN/CSN could not be determined
FORM1_MISSING            - No Form 1 / 8130 / TCCA found for installed SN
FORM1_SN_NOT_VERIFIED    - Parts cert found but SN does not match installed SN
SB_WITHOUT_CRS           - SB compliance event without CRS in same WO bundle
AD_WITHOUT_CRS           - AD compliance event without CRS in same WO bundle
WORK_PACKAGE_WITHOUT_CRS - WO has no release certificate
TASK_NOT_CONFIRMED       - Event with task_compliance_status in {listed_but_not_signed, ambiguous}
DATE_ANOMALY             - Date impossible for this asset
OCR_SUSPECTED            - Entity confidence low on a critical entity
CONTINUITY_BREAK         - Component disappears and reappears without removal record
SHOP_VISIT_MISSING       - Expected overhaul not found
LLP_LIMIT_CRITICAL       - <500 cycles/hours remaining
LLP_LIMIT_WARNING        - <1500 cycles/hours remaining
SN_AMBIGUOUS             - Same PN with multiple SNs, or same SN on different PNs
PN_ALTERNATE_UNRESOLVED  - Manufacturer PN and vendor PN both present, not confirmed same part
MTS_CONFLICT             - MIS export disagrees with primary physical record
AD_NOT_LISTED            - AD applicable to asset not found in dossier
SB_NOT_LISTED            - SB applicable to asset not found in dossier
DAMAGE_NOT_TRACED        - Damage event mentioned but no work report found
REPAIR_TEMPORARY         - Temporary repair without permanent resolution evidence
ICA_NOT_ENROLLED         - STC ICA requirements not enrolled in maintenance program
GAP_IN_DOSSIER           - No records found after exhaustive search
STAMP_AMBIGUOUS_BINDING  - Stamp binding_confidence == "ambiguous"
CONTEXT_DISCREPANCY      - OCR flagged metadata.context_discrepancy on a page
ROTATED_PAGE_LOW_CONF    - Page with rotation_hint != 0 yielded low-confidence derived data
AD_COMPLIANCE_UNVERIFIED - Applicable AD has no compliance record in dossier
SB_COMPLIANCE_UNVERIFIED - Applicable SB has no compliance record in dossier
HARD_TIME_LIMIT_APPROACH - Hard-time / on-condition component approaching limit
PRIOR_HISTORY_MISSING    - Component TSN > airframe TSN but no prior installation record
ALTERNATE_PN_NOT_LINKED  - Two PNs that should be linked as alternates not yet merged
LEASE_RETURN_GAP         - Documentation gap inside lease-return window (max Level 2)
```

If you find yourself wanting to invent a new finding_type, **don't.** Either the situation maps to one of the above, or it's not a finding (it's a note). When ambiguous, default to `GAP_IN_DOSSIER` and explain in `findings.description`.
