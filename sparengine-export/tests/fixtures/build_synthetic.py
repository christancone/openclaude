"""Synthetic CSV builders for the test suite.

Two fixture generators here:

- ``build_synthetic_csv`` — a tiny, well-formed dossier (10 pages, 1 PDF,
  1 component). Used by unit + integration tests that need a deterministic
  Phase-1-able input.

- ``build_ocr_variance_csv`` — a small CSV with deliberately weird OCR
  shape (empty extracted_json, smart quotes, non-ASCII, unknown
  document_type). Used by the OCR-variance archetype regression to confirm
  the indexing path is robust to real-world dirt.

Both write CSVs that match the production schema (one row per page, the
same column set the OCR pipeline emits — see
``phases/references/csv_and_ocr.md``).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extracted_json(
    *,
    document_type: str,
    evidentiary_weight: str = "primary",
    title: str | None = None,
    entities: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    stamps: list[dict[str, Any]] | None = None,
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    header_fields: dict[str, str] | None = None,
    metadata_dates: list[dict[str, str]] | None = None,
    is_blank: bool = False,
) -> str:
    """Build a JSON string matching the OCR pipeline's `extracted_json` shape.

    Only fills the fields the production pipeline actually consumes; leaves
    other keys absent so we don't accidentally test against fields the real
    OCR doesn't emit. Returns the JSON-encoded string, ready for the CSV cell.
    """
    content = {
        "document_type": document_type,
        "evidentiary_weight": evidentiary_weight,
        "title": title,
        "header_fields": header_fields or {},
        "sections": sections or [],
        "tables": tables or [],
        "stamps_and_signatures": stamps or [],
        "entities": entities or [],
        "events": events or [],
        "metadata": {
            "dates": metadata_dates or [],
            "mis_system": [],
        },
    }
    return json.dumps(
        {
            "page_index": 0,
            "is_blank": is_blank,
            "is_template_empty": False,
            "rotation_hint": 0,
            "content": content,
        },
        ensure_ascii=False,
    )


def _row(
    *,
    asset_id: str,
    document_id: str,
    page_index: int,
    original_path: str,
    file_name: str,
    text: str,
    extracted: str,
) -> dict[str, Any]:
    """One CSV row matching the production column set."""
    return {
        "id": str(uuid.uuid4()),
        "document_id": document_id,
        "page_index": page_index,
        "original_path": original_path,
        "file_name": file_name,
        "extracted_json": extracted,
        "enhanced_s3_key": f"s3://test/{file_name}/{page_index}.png",
        "asset_id": asset_id,
        "chunks": "[]",
        "text": text,
    }


# ---------------------------------------------------------------------------
# Tiny well-formed dossier (10 pages, 1 PDF, 1 component)
# ---------------------------------------------------------------------------

def build_synthetic_csv(out_path: Path, *, asset_id: str) -> Path:
    """Write a 10-page synthetic CSV that exercises the canonical shape.

    Layout:
      pages 0..1   — certificate of airworthiness (cover sheets)
      pages 2..3   — engine logbook entries
      pages 4..7   — work order package (cover + Form 1 + job card + signoff)
      pages 8..9   — generic 'other' filler

    The work order on pages 4..7 references one component (PN-12345 / SN-A1)
    so Phase 4 should hydrate exactly one :Component, Phase 5 should hydrate
    one install/overhaul :Event, and Phase 6 should resolve a :Person from
    the signoff stamp on page 7.
    """
    rows: list[dict[str, Any]] = []

    doc1 = "doc-cofa.pdf"
    doc2 = "doc-engine-logbook.pdf"
    doc3 = "doc-wo-419012.pdf"
    doc4 = "doc-other.pdf"

    # --- Cover sheets ----------------------------------------------------
    rows.append(_row(
        asset_id=asset_id, document_id="d1", page_index=0,
        original_path=f"folder1/{doc1}", file_name=doc1,
        text="Certificate of Airworthiness — Issued 2024-06-01 — Reg TEST-001",
        extracted=_extracted_json(
            document_type="certificate_of_airworthiness",
            title="Certificate of Airworthiness",
            entities=[
                {"entity_id": "e1", "entity_type": "registration",
                 "value": "TEST-001", "confidence": "high"},
                {"entity_id": "e2", "entity_type": "operator",
                 "value": "Test Operator Ltd", "confidence": "high"},
            ],
            metadata_dates=[{"value": "2024-06-01", "role": "issue"}],
        ),
    ))
    rows.append(_row(
        asset_id=asset_id, document_id="d1", page_index=1,
        original_path=f"folder1/{doc1}", file_name=doc1,
        text="Type: SYNTHETIC-1 — MSN 0001 — Operator Test Operator Ltd",
        extracted=_extracted_json(
            document_type="certificate_of_airworthiness",
            entities=[
                {"entity_id": "e3", "entity_type": "msn",
                 "value": "0001", "confidence": "high"},
            ],
        ),
    ))

    # --- Engine logbook --------------------------------------------------
    rows.append(_row(
        asset_id=asset_id, document_id="d2", page_index=0,
        original_path=f"folder1/logs/{doc2}", file_name=doc2,
        text="Engine Logbook — ESN ESN-TEST-001 — TSN 1000 — CSN 500",
        extracted=_extracted_json(
            document_type="engine_logbook",
            title="Engine Logbook",
            entities=[
                {"entity_id": "e4", "entity_type": "esn",
                 "value": "ESN-TEST-001", "confidence": "high"},
                {"entity_id": "e5", "entity_type": "tsn",
                 "value": "1000", "confidence": "high"},
                {"entity_id": "e6", "entity_type": "csn",
                 "value": "500", "confidence": "high"},
            ],
        ),
    ))
    rows.append(_row(
        asset_id=asset_id, document_id="d2", page_index=1,
        original_path=f"folder1/logs/{doc2}", file_name=doc2,
        text="Inspection performed 2024-05-15 per task TC-001",
        extracted=_extracted_json(
            document_type="engine_logbook",
            entities=[
                {"entity_id": "e7", "entity_type": "task_card_number",
                 "value": "TC-001", "confidence": "high"},
            ],
            events=[{
                "event_id": "ev1",
                "event_type": "inspection",
                "description": "Borescope inspection",
                "task_reference": "TC-001",
                "task_compliance_status": "signed_off",
                "compliance_status_reason": "Stamp present, date valid",
                "date": "2024-05-15",
                "bound_entities": [],
                "bound_stamps": [],
            }],
            metadata_dates=[{"value": "2024-05-15", "role": "performed"}],
        ),
    ))

    # --- Work order package ---------------------------------------------
    rows.append(_row(
        asset_id=asset_id, document_id="d3", page_index=0,
        original_path=f"folder2/wo/{doc3}", file_name=doc3,
        text="Work Order WO-419012 — opened 2024-06-02",
        extracted=_extracted_json(
            document_type="work_package",
            title="Work Order WO-419012",
            entities=[
                {"entity_id": "e8", "entity_type": "work_order",
                 "value": "WO-419012", "confidence": "high"},
            ],
            metadata_dates=[{"value": "2024-06-02", "role": "opened"}],
        ),
    ))
    rows.append(_row(
        asset_id=asset_id, document_id="d3", page_index=1,
        original_path=f"folder2/wo/{doc3}", file_name=doc3,
        text="Form 1 — PN PN-12345 — SN SN-A1 — Approved 2024-06-03",
        extracted=_extracted_json(
            document_type="form_1",
            title="EASA Form 1",
            entities=[
                {"entity_id": "e9", "entity_type": "part_number",
                 "value": "PN-12345", "confidence": "high"},
                {"entity_id": "e10", "entity_type": "serial_number",
                 "value": "SN-A1", "confidence": "high"},
                {"entity_id": "e11", "entity_type": "certificate_number",
                 "value": "CERT-2024-001", "confidence": "high"},
            ],
            metadata_dates=[{"value": "2024-06-03", "role": "approved"}],
        ),
    ))
    rows.append(_row(
        asset_id=asset_id, document_id="d3", page_index=2,
        original_path=f"folder2/wo/{doc3}", file_name=doc3,
        text="Job Card JC-001 — install component PN-12345 SN-A1",
        extracted=_extracted_json(
            document_type="job_card",
            entities=[
                {"entity_id": "e12", "entity_type": "task_card_number",
                 "value": "JC-001", "confidence": "high"},
                {"entity_id": "e13", "entity_type": "part_number",
                 "value": "PN-12345", "confidence": "high"},
                {"entity_id": "e14", "entity_type": "serial_number",
                 "value": "SN-A1", "confidence": "high"},
            ],
            events=[{
                "event_id": "ev2",
                "event_type": "component_installation",
                "description": "Install PN-12345 SN-A1",
                "task_reference": "JC-001",
                "task_compliance_status": "signed_off",
                "compliance_status_reason": "Stamped + dated",
                "date": "2024-06-04",
                "bound_entities": [
                    {"entity_id": "e13", "role": "part_installed"},
                    {"entity_id": "e14", "role": "part_installed"},
                ],
                "bound_stamps": ["stamp_1"],
            }],
            metadata_dates=[{"value": "2024-06-04", "role": "performed"}],
        ),
    ))
    rows.append(_row(
        asset_id=asset_id, document_id="d3", page_index=3,
        original_path=f"folder2/wo/{doc3}", file_name=doc3,
        text="Signoff: J. Doe (cert 1234567) — 2024-06-04",
        extracted=_extracted_json(
            document_type="certificate_of_release_to_service",
            entities=[
                {"entity_id": "e15", "entity_type": "person",
                 "value": "J. Doe", "confidence": "high"},
                {"entity_id": "e16", "entity_type": "approval_number",
                 "value": "1234567", "confidence": "high"},
            ],
            stamps=[{
                "stamp_id": "stamp_1",
                "type": "stamp",
                "text": "J. DOE 1234567",
                "person_name": "J. Doe",
                "title_role": "Certifying Staff",
                "date": "2024-06-04",
                "certificate_number": "1234567",
            }],
            metadata_dates=[{"value": "2024-06-04", "role": "signed"}],
        ),
    ))

    # --- Filler ----------------------------------------------------------
    for page_index in range(2):
        rows.append(_row(
            asset_id=asset_id, document_id="d4", page_index=page_index,
            original_path=f"folder3/{doc4}", file_name=doc4,
            text=f"Generic content page {page_index}",
            extracted=_extracted_json(document_type="other"),
        ))

    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    return out_path


# ---------------------------------------------------------------------------
# OCR-variance archetype  (Q6: deliberately weird shape)
# ---------------------------------------------------------------------------

def build_ocr_variance_csv(out_path: Path, *, asset_id: str) -> Path:
    """Small CSV exercising real-world OCR dirt.

    Each row tests a different failure mode the indexing path must tolerate:
      - empty `extracted_json` cell
      - JSON parse failure (malformed `extracted_json`)
      - unknown / misspelled `document_type` (must fall back, not crash)
      - smart quotes + em-dash in `text`
      - non-ASCII operator name
      - a row with `is_blank: true` (entity hydration must be skipped, but
        the page must still be indexed as a document boundary)
    """
    rows: list[dict[str, Any]] = []

    # Empty extracted_json
    rows.append(_row(
        asset_id=asset_id, document_id="dvar1", page_index=0,
        original_path="weird/empty.pdf", file_name="empty.pdf",
        text="", extracted="",
    ))

    # Malformed JSON (no fix — the indexer should treat it as no-content)
    rows.append(_row(
        asset_id=asset_id, document_id="dvar2", page_index=0,
        original_path="weird/malformed.pdf", file_name="malformed.pdf",
        text="text exists but extracted_json is broken",
        extracted='{"page_index": 0, "is_blank": false, "content": ',  # truncated
    ))

    # Unknown document_type
    rows.append(_row(
        asset_id=asset_id, document_id="dvar3", page_index=0,
        original_path="weird/unknown_type.pdf", file_name="unknown_type.pdf",
        text="A page whose document_type isn't in the closed enum",
        extracted=_extracted_json(document_type="form-1-typo-not-in-enum"),
    ))

    # Smart quotes + em-dash in page text + entity values
    rows.append(_row(
        asset_id=asset_id, document_id="dvar4", page_index=0,
        original_path="weird/smartquotes.pdf", file_name="smartquotes.pdf",
        text="Inspection “per EASA Part-145” — signed by J. Doe",
        extracted=_extracted_json(
            document_type="other",
            entities=[
                {"entity_id": "ev1", "entity_type": "person",
                 "value": "J. Doe", "confidence": "high",
                 "location_context": "stamp at bottom — page 1"},
            ],
        ),
    ))

    # Non-ASCII operator name
    rows.append(_row(
        asset_id=asset_id, document_id="dvar5", page_index=0,
        original_path="weird/intl.pdf", file_name="intl.pdf",
        text="Operator: Cathay Pacific 國泰航空",
        extracted=_extracted_json(
            document_type="certificate_of_registration",
            entities=[
                {"entity_id": "ev2", "entity_type": "operator",
                 "value": "Cathay Pacific 國泰航空", "confidence": "high"},
            ],
        ),
    ))

    # is_blank: true — must be indexed but not entity-hydrated
    rows.append(_row(
        asset_id=asset_id, document_id="dvar6", page_index=0,
        original_path="weird/blank.pdf", file_name="blank.pdf",
        text="",
        extracted=_extracted_json(document_type="other", is_blank=True),
    ))

    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    return out_path


# ---------------------------------------------------------------------------
# CLI: regenerate the on-disk fixture CSVs
# ---------------------------------------------------------------------------
# Run with:
#   python -m tests.fixtures.build_synthetic
# from the sparengine-export/ directory. Uses a fixed asset_id so the output
# is byte-stable for git diffs.

if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    fixed_id = "test-fixture-00000000-0000-0000-0000-000000000001"
    build_synthetic_csv(here / "synthetic_pages.csv", asset_id=fixed_id)
    build_ocr_variance_csv(here / "ocr_variance.csv", asset_id=fixed_id)
    print("wrote synthetic_pages.csv + ocr_variance.csv into", here)
