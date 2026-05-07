"""Closed mapping from CSV ``content.document_type`` to evidence-record kind.

Phase 1 uses this to decide which evidence-record writer (if any) to call
for a page based on its ``document_type``. The mapping is one-to-one
(each document_type produces at most one evidence-record kind), and the
closed enum source is ``sparengine-export/phases/references/document_types.md``.

A return of ``None`` means the page does not carry a structured evidence
record we extract in Phase 1 — it's still indexed as a :Page (Phase 1
always writes the page), it just doesn't have a Form 1 / CRS / etc.
node attached.
"""

from __future__ import annotations

from typing import Literal

# Type alias: the closed set of evidence-record kinds Phase 1 writes.
# Maps to the writer functions in ``graph_dal.evidence``:
#   form1               -> write_form1
#   crs                 -> write_crs
#   work_package        -> write_work_package
#   job_card            -> write_job_card
#   non_routine_card    -> write_non_routine_card
#   repair              -> write_repair
#   modification        -> write_modification
#   stc                 -> write_stc
#   borescope_report    -> write_borescope_report
#   ndt_report          -> write_ndt_report
#   dent_buckle_entry   -> write_dent_buckle_entry
EvidenceRecordKind = Literal[
    "form1", "crs", "work_package",
    "job_card", "non_routine_card", "repair", "modification", "stc",
    "borescope_report", "ndt_report", "dent_buckle_entry",
]


# The mapping. Document types from
# ``sparengine-export/phases/references/document_types.md``.
#
# Logic:
#   - Form 1 family → :Form1
#   - CRS family → :CRS
#   - Task cards → :JobCard
#   - Non-routine cards → :NonRoutineCard
#   - Borescope inspection report → :BorescopeReport
#   - Inspection / structural reports with NDT → :NDTReport (heuristic)
#   - Dent and buckle chart → :DentBuckleEntry (one per page; we extract
#     individual entries from the table inside the page in a later phase
#     if needed)
#   - Modification record → :Modification
#   - Supplemental type certificate → :STC
#   - Workpack cover sheet → :WorkPackage
#   - Repair-related → :Repair (heuristic; structural_repair_report)
#
# Document types that don't map: cover sheets, table of contents,
# document control lists, logbooks, life-limited parts status sheets,
# correspondence, MIS exports, certificates of airworthiness/registration.
# These are still indexed as :Page; they just don't have a structured
# evidence record extracted from the page itself in Phase 1.
DOCTYPE_TO_RECORD: dict[str, EvidenceRecordKind] = {
    # Form 1 family
    "easa_form_one":            "form1",
    "faa_form_8130":            "form1",
    "tcca_form_one":            "form1",
    "dual_release_certificate": "form1",   # ALSO maps to crs; see DUAL_RECORDS

    # CRS family
    "certificate_of_release_to_service": "crs",

    # Workpack cover
    "workpack_cover_sheet":      "work_package",

    # Task cards
    "routine_task_card":          "job_card",
    "mis_task_card":              "job_card",

    # Non-routine
    "non_routine_card":           "non_routine_card",

    # Inspection / NDT / borescope
    "borescope_inspection_report": "borescope_report",
    # ``inspection_report`` is broad — many inspections aren't NDT. We
    # leave it unmapped here; phase scripts that want fine-grained
    # extraction can handle it themselves.

    # Structural
    "structural_repair_report": "repair",
    "dent_and_buckle_chart":    "dent_buckle_entry",

    # Modifications / STCs
    "modification_record":          "modification",
    "supplemental_type_certificate": "stc",
}


# Document types that map to TWO record kinds simultaneously. The Phase 1
# extractor calls both writers, anchoring each to the same page.
DUAL_RECORDS: dict[str, tuple[EvidenceRecordKind, EvidenceRecordKind]] = {
    # A dual-release certificate is BOTH a Form 1 and a CRS — by definition.
    # See document_types.md (lines 22–23 + lines 53, 60).
    "dual_release_certificate": ("form1", "crs"),
}


def derive_evidence_record_kinds(document_type: str | None) -> list[EvidenceRecordKind]:
    """Return the list of evidence-record kinds to write for a page.

    Returns ``[]`` if the document type doesn't map to any record kind.
    Returns one or more kinds if it does (e.g. ``dual_release_certificate``
    returns ``["form1", "crs"]``).
    """
    if not document_type:
        return []
    if document_type in DUAL_RECORDS:
        return list(DUAL_RECORDS[document_type])
    kind = DOCTYPE_TO_RECORD.get(document_type)
    return [kind] if kind else []
