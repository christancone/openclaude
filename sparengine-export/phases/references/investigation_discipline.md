# INVESTIGATION DISCIPLINE — DON'T FLAG WHAT YOU HAVEN'T LOOKED FOR

Reference file. Load when running phases 7 and 7.5.

The single biggest failure mode of the original ATR72 run: agents wrote `FORM1_MISSING` after one or two FTS searches that returned nothing. **The Form 1 usually existed** — embedded in a work order PDF whose title didn't contain "Form 1", filed under SN-only without the PN, or covered by a batch certificate. **523 raw findings → 100 genuine** mostly because investigations short-circuited.

---

## Hard prerequisite checklist for any "missing" finding

Before writing any of:
`FORM1_MISSING`, `SHOP_VISIT_MISSING`, `AD_COMPLIANCE_UNVERIFIED`, `SB_COMPLIANCE_UNVERIFIED`, `GAP_IN_DOSSIER`, `PRIOR_HISTORY_MISSING`

The agent must have completed all applicable items:

```
☐ Read every page of every work order package the component appears in.
  Do not stop at the WO summary — walk all pages with PART_OF_WORK_ORDER edges.

☐ Searched the corpus by SN alone (drop the PN). Form 1s are often filed
  by SN only.

☐ Searched the corpus by canonical PN AND each entry in alternate_pns.
  Manufacturer ↔ vendor PN pairs are the most common miss.

☐ Checked file names containing the PN as a substring (e.g. "*PN_{pn}*",
  "*{pn}*"). Many MROs file Form 1s under the PN in the filename.

☐ Checked file names containing the SN as a substring.

☐ Checked operator-consolidated status sheets — file names matching the
  asset MSN/registration prefix (e.g. "MSN_{msn}_*.pdf",
  "{registration}_status_*.pdf", "AC_{registration}_*.pdf").

☐ Checked batch Form 8130 / Form 1 ranges. If any batch certificate exists
  for the canonical PN, parse the SN range and check membership of the
  installed SN.

☐ Searched the immediate page neighbourhood (±3 pages in the same PDF).
  Form 1s are commonly attached after the job card that consumed them.

☐ For LLP limits: queried sibling components (same canonical PN on the
  other engine / opposite position / batch installation). OEM limits
  are per-PN, not per-SN.

☐ For shop visit checks: compared engine TSN to the OEM-typical first-SVR
  interval (data_quality_rules.md). If within interval → no finding.
```

If any applicable item was skipped, the finding is **provisional only** — `findings.status = 'provisional'`, `findings.discipline_complete = 0`. It feeds Phase 7.5 for a second look. Provisional findings never reach the final report without being upgraded to `open` after verification.

When you DO complete the checklist, set `findings.discipline_complete = 1` so Phase 7.5 knows it doesn't need to redo the work.

---

## Read the document, don't just keyword-match

The connectors built in Phase 6 mean every component already has its WO packages, certificates, and stamps linked. Phase 7 walks those edges; **it does not re-search FTS for the same data the connectors expose**. FTS is a fallback for entities the connectors didn't capture, not a substitute for reading the linked documents.

When in doubt: **prefer reading more, flagging less.** False positives cost the team more than false negatives — they shake confidence in every other finding the system produces.

---

## How to record completion in the database

```python
# After running discipline checks for a finding-candidate:
discipline_complete = (
    walked_wo_packages and
    searched_by_sn_alone and
    searched_by_alternate_pns and
    searched_filenames_for_pn and
    searched_filenames_for_sn and
    searched_operator_status_sheets and
    checked_batch_certificates and
    checked_page_neighbourhood and
    (queried_sibling_components or not_an_llp_limit_finding) and
    (compared_to_oem_typical or not_a_shop_visit_finding)
)

from graph_dal.finding import write_finding
from graph_dal import FindingSeverity

write_finding(
    tx, asset_id=asset_id, value=fid,
    severity=sev,                          # FindingSeverity.LEVEL_1.value etc.
    category=ftype,                         # closed enum from finding_types.md
    title=title,
    description=desc,                       # MUST cite (file:..., page:...) — 80+ chars
    evidence_page_uid=page_uid,             # required (golden rule)
    evidence_quote=verbatim_quote_excerpt,  # required (golden rule)
    flags_label=target_kind,                # "Component" | "Stamp" | "Form1" | ...
    flags_uid=target_id,
    component_uid=target_id if target_kind == "Component" else None,
    asset_level=(target_kind == "Asset"),
    audit_run_uid=run_id,
    status="provisional" if not discipline_complete else "open",
)
```

`status="provisional"` is a hard contract: Phase 7.5 MUST run the missing checks before this finding can become `open`. The DAL writer enforces the golden rule — `evidence_page_uid` and `evidence_quote` are required keyword arguments.
