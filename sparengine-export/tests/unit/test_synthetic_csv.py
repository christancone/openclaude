"""Unit tests for tests/fixtures/build_synthetic.py — the synthetic CSV
generators that back the integration test suite.

The generators need to produce output matching the production OCR pipeline's
schema (see phases/references/csv_and_ocr.md). If the schema drifts and the
generator doesn't, integration tests pass against a fiction. These tests are
the canary.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tests.fixtures.build_synthetic import (
    build_synthetic_csv,
    build_ocr_variance_csv,
)


pytestmark = pytest.mark.unit


# Production CSV columns — see phases/references/csv_and_ocr.md.
EXPECTED_COLUMNS = {
    "id", "document_id", "page_index", "original_path", "file_name",
    "extracted_json", "enhanced_s3_key", "asset_id", "chunks", "text",
}


# ---------------------------------------------------------------------------
# build_synthetic_csv — the well-formed dossier
# ---------------------------------------------------------------------------

def test_synthetic_columns_match_production_schema(tmp_path):
    p = build_synthetic_csv(tmp_path / "syn.csv", asset_id="test-x")
    df = pd.read_csv(p)
    assert set(df.columns) >= EXPECTED_COLUMNS, (
        f"missing columns: {EXPECTED_COLUMNS - set(df.columns)}"
    )


def test_synthetic_row_count_is_ten(tmp_path):
    """Plan says 10 pages; lock that as the contract so unit tests can assert
    on counts (e.g. expect exactly 1 :Component, 1 :Form1, etc.)."""
    p = build_synthetic_csv(tmp_path / "syn.csv", asset_id="test-x")
    assert len(pd.read_csv(p)) == 10


def test_synthetic_every_row_has_asset_id(tmp_path):
    p = build_synthetic_csv(tmp_path / "syn.csv", asset_id="test-fixed")
    df = pd.read_csv(p)
    assert (df["asset_id"] == "test-fixed").all()


def test_synthetic_extracted_json_parses(tmp_path):
    """Every non-empty extracted_json must be valid JSON with the expected
    top-level shape (page_index, content, metadata)."""
    p = build_synthetic_csv(tmp_path / "syn.csv", asset_id="test-x")
    for raw in pd.read_csv(p)["extracted_json"]:
        if not raw or pd.isna(raw):
            continue
        parsed = json.loads(raw)
        assert "content" in parsed, parsed
        assert "document_type" in parsed["content"], parsed["content"]


def test_synthetic_contains_a_form_1(tmp_path):
    """Plan says the 10-page fixture has a Form 1 with PN-12345 / SN-A1.
    Lock that — integration tests for Phase 1 / 4 depend on it."""
    p = build_synthetic_csv(tmp_path / "syn.csv", asset_id="test-x")
    df = pd.read_csv(p)
    has_form_1 = any(
        json.loads(ej).get("content", {}).get("document_type") == "form_1"
        for ej in df["extracted_json"]
        if ej and not pd.isna(ej)
    )
    assert has_form_1, "no form_1 page in synthetic fixture"


def test_synthetic_pn_sn_appear(tmp_path):
    """The canonical part_number/serial_number appear in the entities list."""
    p = build_synthetic_csv(tmp_path / "syn.csv", asset_id="test-x")
    df = pd.read_csv(p)
    pns, sns = [], []
    for ej in df["extracted_json"]:
        if not ej or pd.isna(ej):
            continue
        for e in json.loads(ej).get("content", {}).get("entities", []):
            if e["entity_type"] == "part_number":   pns.append(e["value"])
            if e["entity_type"] == "serial_number": sns.append(e["value"])
    assert "PN-12345" in pns, pns
    assert "SN-A1"    in sns, sns


def test_synthetic_is_deterministic(tmp_path):
    """Same asset_id → byte-identical row contents (modulo `id` UUIDs).

    The `id` column is a fresh UUID per row (random), so two consecutive
    runs differ ONLY in that column. Everything else must match exactly.
    """
    p1 = build_synthetic_csv(tmp_path / "a.csv", asset_id="test-fixed")
    p2 = build_synthetic_csv(tmp_path / "b.csv", asset_id="test-fixed")
    df1 = pd.read_csv(p1).drop(columns=["id"])
    df2 = pd.read_csv(p2).drop(columns=["id"])
    pd.testing.assert_frame_equal(df1, df2)


# ---------------------------------------------------------------------------
# build_ocr_variance_csv — the dirty fixture
# ---------------------------------------------------------------------------

def test_ocr_variance_columns_match_production_schema(tmp_path):
    p = build_ocr_variance_csv(tmp_path / "ocr.csv", asset_id="test-x")
    df = pd.read_csv(p)
    assert set(df.columns) >= EXPECTED_COLUMNS


def test_ocr_variance_includes_empty_extracted_json(tmp_path):
    """The first row should test the `empty cell` failure mode."""
    p = build_ocr_variance_csv(tmp_path / "ocr.csv", asset_id="test-x")
    df = pd.read_csv(p)
    empties = [ej for ej in df["extracted_json"] if not ej or pd.isna(ej)]
    assert len(empties) >= 1, "ocr_variance fixture lost its empty-JSON row"


def test_ocr_variance_includes_malformed_json(tmp_path):
    """One row should be deliberately broken JSON (truncated mid-string).
    The indexer must treat it as no-content rather than crashing."""
    p = build_ocr_variance_csv(tmp_path / "ocr.csv", asset_id="test-x")
    df = pd.read_csv(p)
    malformed = 0
    for ej in df["extracted_json"]:
        if not ej or pd.isna(ej):
            continue
        try:
            json.loads(ej)
        except json.JSONDecodeError:
            malformed += 1
    assert malformed >= 1, "ocr_variance fixture lost its malformed-JSON row"


def test_ocr_variance_includes_non_ascii(tmp_path):
    """Non-ASCII operator name (Cathay 國泰航空) tests the encoding path."""
    p = build_ocr_variance_csv(tmp_path / "ocr.csv", asset_id="test-x")
    df = pd.read_csv(p)
    has_non_ascii = any(
        any(ord(c) > 127 for c in str(t))
        for t in df["text"]
    )
    assert has_non_ascii, "ocr_variance fixture lost its non-ASCII row"


def test_ocr_variance_includes_blank_page(tmp_path):
    """One row has is_blank: true. The indexer must skip entity hydration
    but still index the page as a document boundary."""
    p = build_ocr_variance_csv(tmp_path / "ocr.csv", asset_id="test-x")
    df = pd.read_csv(p)
    blank_pages = 0
    for ej in df["extracted_json"]:
        if not ej or pd.isna(ej):
            continue
        try:
            if json.loads(ej).get("is_blank"):
                blank_pages += 1
        except json.JSONDecodeError:
            pass
    assert blank_pages >= 1, "ocr_variance fixture lost its is_blank row"
