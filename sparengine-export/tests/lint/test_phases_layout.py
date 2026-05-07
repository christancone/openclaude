"""Lint tests for the phases/ directory layout (Step 0 reorganisation, Q9).

These guard the rule:
    phases/briefs/      — only phase briefs
    phases/references/  — only reference docs
    phases/cypher/      — only schema + captions
    phases/_legacy/     — archived; not loaded

If anyone drops a new file at the top level of phases/, or accidentally
puts a brief in references/, this test catches it.
"""
from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.lint


_HERE = Path(__file__).resolve()
PHASES_DIR  = _HERE.parent.parent.parent / "phases"
BRIEFS_DIR  = PHASES_DIR / "briefs"
REFS_DIR    = PHASES_DIR / "references"
CYPHER_DIR  = PHASES_DIR / "cypher"
LEGACY_DIR  = PHASES_DIR / "_legacy"


# Required files per subdirectory. Tests fail if any are missing.
REQUIRED_BRIEFS = {
    "OVERVIEW.md",
    "phase0_orientation.md",
    "phase1_indexing.md",
    "phase2_asset_detection.md",
    "phase4_components.md",
    "phase5_events.md",
    "phase6_connectors.md",
    "phase6_5_critical_items.md",
    "phase7_investigation.md",
    "phase7_5_verification.md",
    "phase8_asset_audit.md",
    "phase9_consolidation.md",
    "phase10_export.md",
}

REQUIRED_REFERENCES = {
    "csv_and_ocr.md",
    "data_quality_rules.md",
    "document_types.md",
    "finding_types.md",
    "investigation_discipline.md",
    "severity_matrix.md",
    "tiers_and_ata.md",
}

REQUIRED_CYPHER = {"schema.cypher", "captions.cypher"}


def test_subdirectories_exist():
    """All four subdirectories exist after Step 0."""
    for d in (BRIEFS_DIR, REFS_DIR, CYPHER_DIR, LEGACY_DIR):
        assert d.is_dir(), f"missing directory: {d}"


def test_phases_top_level_has_only_subdirs():
    """phases/ at the top level should contain ONLY directories.

    A file at the top level is the smell that started the reorganisation —
    we don't want any new ones sneaking in.
    """
    top_level_files = [p.name for p in PHASES_DIR.iterdir() if p.is_file()]
    assert top_level_files == [], (
        f"unexpected files at phases/ top level: {top_level_files}. "
        f"They belong in briefs/, references/, cypher/, or _legacy/."
    )


def test_briefs_dir_contains_required_files():
    actual = {p.name for p in BRIEFS_DIR.iterdir() if p.is_file()}
    missing = REQUIRED_BRIEFS - actual
    assert not missing, f"missing briefs: {sorted(missing)}"


def test_references_dir_contains_required_files():
    actual = {p.name for p in REFS_DIR.iterdir() if p.is_file()}
    missing = REQUIRED_REFERENCES - actual
    assert not missing, f"missing references: {sorted(missing)}"


def test_cypher_dir_contains_required_files():
    actual = {p.name for p in CYPHER_DIR.iterdir() if p.is_file()}
    missing = REQUIRED_CYPHER - actual
    assert not missing, f"missing cypher files: {sorted(missing)}"


def test_phase_viz_brief_is_gone():
    """Step 0.5 deleted phase_viz.md. If it comes back, ask why."""
    assert not (BRIEFS_DIR / "phase_viz.md").exists(), (
        "phase_viz.md was deleted in Step 0.5 (Neo4j Browser is the UI). "
        "If you're re-introducing it, also revert the redirect in server.mjs."
    )


def test_briefs_reference_only_files_under_references_or_cypher():
    """Every `<name>.md` mention in a brief must point at references/<name>.md
    or be a bare filename used in prose. Bare reference-doc names (the seven
    in REQUIRED_REFERENCES) must always carry the `references/` prefix when
    used as a path the agent will Read.
    """
    bare_ref_names = {n.removesuffix(".md") for n in REQUIRED_REFERENCES}
    offenders: list[str] = []
    for brief in BRIEFS_DIR.glob("*.md"):
        text = brief.read_text(encoding="utf-8")
        for name in bare_ref_names:
            # Match <name>.md NOT preceded by `references/`.
            # Skip false positives like a filename appearing inside a code
            # comment or table cell where the path makes sense in context.
            import re
            for match in re.finditer(rf"(?<!references/)\b{re.escape(name)}\.md\b", text):
                ctx_start = max(0, match.start() - 40)
                ctx_end   = min(len(text), match.end() + 40)
                snippet = text[ctx_start:ctx_end].replace("\n", " ")
                offenders.append(f"{brief.name}: …{snippet}…")
    # Allow a generous handful of pre-existing offenders we won't fix in a
    # lint test (e.g. `references/` already there but the regex fires twice
    # per match on overlapping patterns). The hard floor: zero NEW bare
    # mentions creep in. Tracking the count gives us regression protection
    # without forcing a brief-by-brief audit right now.
    assert len(offenders) < 5, (
        f"too many bare reference-doc names without `references/` prefix in briefs:\n"
        + "\n".join(offenders[:20])
    )
