# DOCUMENT TYPE — CLOSED ENUM

Reference file. The OCR uses a fixed enum. Store these exact strings in `documents.document_type` and `pages.document_type`. Do not invent new ones. Do not lowercase or alias them.

```
Cover/Admin (administrative weight):
  workpack_cover_sheet, table_of_contents, document_control_list

Work Authorisation (secondary):
  maintenance_work_authorisation, work_order_contents_report, work_scope

Defect & Findings (secondary):
  defects_reconciled_summary, non_routine_card, routine_task_card,
  mis_task_card, mel_entry

Inspection & Reports (primary or secondary):
  inspection_report, borescope_inspection_report, condition_report,
  shop_visit_report, test_report, dent_and_buckle_chart

Certificates & Release (primary):
  easa_form_one, faa_form_8130, tcca_form_one, certificate_of_release_to_service,
  dual_release_certificate, certificate_of_airworthiness, certificate_of_registration,
  airworthiness_review_certificate

Component Records (primary or secondary):
  access_panel_chart, parts_identification_tag, component_history_card,
  component_logbook, life_limited_parts_status, engine_llp_status_sheet,
  structural_repair_report

Operational (primary):
  technical_journey_log, flight_log, engine_logbook, airframe_logbook,
  weight_and_balance_report

Engineering & Modifications (primary):
  engineering_order, service_bulletin_compliance, airworthiness_directive_compliance,
  sb_status_report, ad_status_report, modification_record,
  supplemental_type_certificate, afm_supplement

Transaction / Lease (reference):
  redelivery_condition_report, delivery_acceptance_certificate,
  purchase_order, invoice, quotation

MIS / System Exports (reference — treat as hypothesis):
  mis_export

Other:
  shipping_record, correspondence, other
```

**When reasoning about coverage** (e.g. "is there a CRS for this work order?") query by `document_type` directly. Do not pattern-match titles.

**Form 1 family** (used in Phase 6 attachment detection and Phase 7 chain checks):
- `easa_form_one`
- `faa_form_8130`
- `tcca_form_one`
- `dual_release_certificate`

**CRS family** (used in Phase 6 work-order coverage):
- `certificate_of_release_to_service`
- `dual_release_certificate`

**Primary evidentiary weight** by default for: certificates, logbooks, engineering orders, modification records, certain inspection reports.

**Secondary** by default for: task cards, work orders, condition reports, shop visit reports, MEL entries.

**Administrative** for: cover sheets, TOCs, document control lists.

**Reference** for: MIS exports, lease/purchase paperwork, correspondence.

The OCR sets the per-page `evidentiary_weight`. Phase 1 sets the document-level weight as the most common across pages. **Trust the OCR's assignment** unless you have specific evidence it's wrong (in which case raise a finding, don't silently overwrite).
