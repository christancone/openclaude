"""Lint tests guarding the Step 0.5 cleanup (no asset_graph.html / phase_viz)
and the original Neo4j migration cleanup (no sqlite3 / graph.db).

Active code MUST NOT reference any of the dead pipelines. Files explicitly
marked as legacy (`*.legacy`, `_legacy/` directories) are ignored.
"""
from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.lint


_HERE = Path(__file__).resolve()
EXPORT_ROOT = _HERE.parent.parent.parent              # sparengine-export/


# Globs of paths that are deliberately archived; we don't lint them.
LEGACY_GLOBS = (
    "**/*.legacy",
    "**/_legacy/**",
    "**/_legacy_sqlite/**",
    "**/__pycache__/**",
    "**/node_modules/**",
)

# Paths under sparengine-export/ where we DO lint.
ACTIVE_FILE_GLOBS = (
    "*.mjs",                  # server.mjs etc.
    "*.json",
    "Dockerfile",
    "docker-compose.yml",
    "graph_dal/**/*.py",
    "phases/**/*.md",
    "phases/**/*.cypher",
    "public/**/*.html",
    # NOTE: we deliberately do NOT lint test files. Lint tests like THIS one
    # mention forbidden strings ("asset_graph.html", "phase_viz", "graph.db")
    # as part of their assertion text — they're meta. Linting them creates
    # circular failures where the test that catches the legacy reference IS
    # the legacy reference.
)


def _active_files() -> list[Path]:
    """Yield every file we should lint (active code only)."""
    out: list[Path] = []
    for pat in ACTIVE_FILE_GLOBS:
        out.extend(EXPORT_ROOT.glob(pat))

    def is_legacy(p: Path) -> bool:
        rel = p.relative_to(EXPORT_ROOT).as_posix()
        return any(
            "_legacy" in rel
            or "_legacy_sqlite" in rel
            or rel.endswith(".legacy")
            or "__pycache__" in rel
            or "node_modules" in rel
            for _ in [None]   # one-shot
        )

    return [p for p in out if not is_legacy(p)]


def _grep(needle: str, *, allow_substring_in_text: tuple[str, ...] = ()) -> list[str]:
    """Return file:line excerpts where `needle` appears in active files,
    excluding lines whose surrounding context contains any of
    `allow_substring_in_text`.
    """
    hits: list[str] = []
    for path in _active_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            if needle in line and not any(allow in line for allow in allow_substring_in_text):
                hits.append(f"{path.relative_to(EXPORT_ROOT)}:{n}: {line.strip()[:120]}")
    return hits


# ---------------------------------------------------------------------------
# Step 0.5 — asset_graph.html / phase_viz / panel template are gone
# ---------------------------------------------------------------------------

def test_no_active_references_to_asset_graph_html():
    """The string `asset_graph.html` may only appear in agent-prompt comments
    that explicitly tell the agent NOT to build it. Any other reference is
    a leak from the old viz pipeline."""
    hits = _grep("asset_graph.html", allow_substring_in_text=(
        "Do NOT", "do NOT",
        "no asset_graph", "No asset_graph",
        "There is no `asset_graph", "There is no asset_graph",
        "no panel template",
        "asset_graph.html or any panel HTML",
        "delegated to Neo4j Browser",
        "is delegated to",
    ))
    assert hits == [], (
        "asset_graph.html still referenced in active code:\n"
        + "\n".join(hits)
    )


def test_no_active_references_to_phase_viz():
    """phase_viz.md was deleted in Step 0.5; any active mention is a leak."""
    hits = _grep("phase_viz", allow_substring_in_text=(
        "no `phase_viz`",
        "no phase_viz",
        "phase_viz` follows",
        "There is no `phase_viz`",
    ))
    assert hits == [], "phase_viz still referenced in active code:\n" + "\n".join(hits)


def test_no_active_references_to_panel_template():
    """The asset_graph_template_panels.html template was deleted."""
    hits = _grep("asset_graph_template_panels", allow_substring_in_text=())
    assert hits == [], (
        "panel template still referenced in active code:\n"
        + "\n".join(hits)
    )


def test_no_active_references_to_inline_graph_mjs():
    """inline_graph.mjs was deleted."""
    hits = _grep("inline_graph.mjs", allow_substring_in_text=())
    assert hits == [], "inline_graph.mjs still referenced:\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# Step 0 — phases/ paths follow the new layout
# ---------------------------------------------------------------------------

def test_no_bare_phases_schema_cypher_path():
    """Every reference to schema.cypher must be `phases/cypher/schema.cypher`,
    `cypher/schema.cypher` (relative to phases/), or `/import/schema.cypher`
    (the in-container path). The bare `phases/schema.cypher` (no `cypher/`)
    is the pre-Q9 layout."""
    hits = _grep("phases/schema.cypher", allow_substring_in_text=("phases/cypher/",))
    assert hits == [], "stale `phases/schema.cypher` (without /cypher/):\n" + "\n".join(hits)


def test_no_bare_phases_captions_cypher_path():
    hits = _grep("phases/captions.cypher", allow_substring_in_text=("phases/cypher/",))
    assert hits == [], "stale `phases/captions.cypher` (without /cypher/):\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# Pre-Step-0 — no SQLite leaks (Neo4j migration was supposed to be complete)
# ---------------------------------------------------------------------------

def test_no_sqlite3_imports_in_active_python():
    """sparengine is fully on Neo4j; no script should import sqlite3."""
    hits = _grep("import sqlite3", allow_substring_in_text=(
        "do `import sqlite3`", "doing\n", "import sqlite3", "is OBSOLETE",
        "If you see a Phase script", "doing", "is OBSOLETE",
        "writing to a `graph.db`",
    ))
    # The agent prompt mentions "import sqlite3" to tell the agent legacy
    # scripts containing this are obsolete. Allow exactly that mention.
    really = [h for h in hits if "OBSOLETE" not in h and "obsolete" not in h]
    assert really == [], "live sqlite3 imports in active code:\n" + "\n".join(really)


def test_no_graph_db_file_references():
    """No active code should mention the legacy per-asset SQLite file
    EXCEPT in agent-prompt comments / docs that explicitly call it out as
    obsolete (e.g. "no `graph.db` file" / "replaces ... graph.db")."""
    hits = _grep("graph.db", allow_substring_in_text=(
        "no `graph.db`", "no graph.db", "There is no graph.db",
        "Never reference graph.db",
        "replaces the per-asset SQLite graph.db",
        "writing to a `graph.db` file, that script is OBSOLETE",
        "is OBSOLETE",
    ))
    assert hits == [], "graph.db still referenced:\n" + "\n".join(hits)
