"""Pytest fixtures for the Sparengine test suite.

Tests share the production Neo4j instance — multi-tenancy via the `asset_id`
property keeps test data isolated from real-asset data. Every test gets a
freshly-minted random asset_id; the `clean_asset` autouse fixture deletes
that asset's nodes/edges at teardown so the database stays clean.

Tests that don't touch Neo4j should declare themselves with
`@pytest.mark.no_neo4j` (or `@pytest.mark.unit` / `@pytest.mark.lint`, which
imply no_neo4j semantics for the cleanup fixture). Those tests skip the
cleanup teardown to stay fast.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Generator, Iterator

import pytest


# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
# Make `import graph_dal` and `from tests.fixtures.build_synthetic import ...`
# resolve regardless of where pytest is invoked from (host venv vs. container).

_HERE        = Path(__file__).resolve()
_TESTS_DIR   = _HERE.parent
_EXPORT_ROOT = _HERE.parent.parent                         # sparengine-export/
_REPO_ROOT   = _EXPORT_ROOT.parent                         # repo root

if str(_EXPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXPORT_ROOT))


# ---------------------------------------------------------------------------
# Neo4j driver  (session-scoped — one connection for the whole run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def neo4j_driver():
    """Session-scoped Neo4j driver. Reuses the production .env settings.

    Skips the entire Neo4j-dependent test set if the driver can't connect —
    that way `pytest -m unit` works on a workstation with no Neo4j running.
    """
    try:
        from graph_dal import connect
    except ImportError as e:                                   # pragma: no cover
        pytest.skip(f"graph_dal not importable: {e}")
    try:
        drv = connect()
        # Probe: a session that doesn't fail tells us the driver + auth are good.
        with drv.session() as s:
            s.run("RETURN 1").consume()
    except Exception as e:
        pytest.skip(f"Neo4j unreachable: {e}")
    yield drv
    drv.close()


# ---------------------------------------------------------------------------
# Per-test asset isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def asset_id() -> str:
    """A fresh random `test-<uuid>` per test.

    Used as the multi-tenancy key for any data the test writes to Neo4j.
    The `test-` prefix makes it obvious in cross-cutting Cypher queries that
    the data is from a test run; the cleanup fixture removes it on teardown.
    """
    return f"test-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def _clean_asset_after_test(request) -> Iterator[None]:
    """Autouse cleanup. After every test, delete all nodes carrying the
    test's asset_id so the production graph is unaffected.

    Skips for tests that:
      - Are marked @pytest.mark.no_neo4j / unit / lint (declarative)
      - Didn't request the `asset_id` fixture (nothing to clean)
      - Couldn't get a `neo4j_driver` (the driver fixture pytest.skip'd)
    """
    yield  # let the test run

    skip_markers = {"no_neo4j", "unit", "lint"}
    if any(m.name in skip_markers for m in request.node.iter_markers()):
        return

    if "asset_id" not in request.fixturenames:
        return

    try:
        # If the driver fixture skipped, this getfixturevalue raises Skipped —
        # we just swallow it because there's nothing to clean.
        from graph_dal import database_name
        driver = request.getfixturevalue("neo4j_driver")
        aid    = request.getfixturevalue("asset_id")
    except (pytest.skip.Exception, Exception):
        return

    with driver.session(database=database_name()) as s:
        # DETACH DELETE drops every node carrying this asset_id and any
        # relationships touching it. Bounded by the asset_id index so this
        # is cheap even on a large database.
        s.run(
            "MATCH (n) WHERE n.asset_id = $aid DETACH DELETE n",
            aid=aid,
        ).consume()


# ---------------------------------------------------------------------------
# CSV fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_csv(tmp_path, asset_id) -> Path:
    """Tiny deterministic synthetic CSV (10 pages, 1 doc, 1 component).

    Writes the asset_id directly into every row so the fixture can be loaded
    by Phase 1 without further substitution.
    """
    from tests.fixtures.build_synthetic import build_synthetic_csv
    csv_path = tmp_path / "synthetic.csv"
    build_synthetic_csv(csv_path, asset_id=asset_id)
    return csv_path


@pytest.fixture
def ocr_variance_csv(tmp_path, asset_id) -> Path:
    """Synthetic CSV with deliberately weird OCR shape (Q6).

    Edge cases: empty extracted_json, misspelled document_type, smart quotes
    in page text, non-ASCII characters, mixed-encoding entity values. Tests
    the indexing path's robustness — the answer should never be a crash.
    """
    from tests.fixtures.build_synthetic import build_ocr_variance_csv
    csv_path = tmp_path / "ocr_variance.csv"
    build_ocr_variance_csv(csv_path, asset_id=asset_id)
    return csv_path


@pytest.fixture
def helicopter_full_csv() -> Path:
    """Real AW139 CSV from `csvs/`. Skips the test if not present locally.

    The full archetype CSV is large (50MB+) and not tracked in git. Drop a
    real AW139 CSV at `csvs/<uuid>-AW139/<asset_id>.csv` and the fixture
    finds it; otherwise the test marks itself skipped.
    """
    candidates = sorted((_REPO_ROOT / "csvs").glob("*-AW139/*.csv"))
    if not candidates:
        pytest.skip("AW139 CSV not present under csvs/. "
                    "Drop a real CSV to run helicopter regression.")
    return candidates[0]


@pytest.fixture
def engine_only_csv() -> Path:
    """Real engine-only CSV (CFM56-5B or similar). Skips if not present."""
    candidates = (
        sorted((_REPO_ROOT / "csvs").glob("*-CFM56*/*.csv"))
        + sorted((_REPO_ROOT / "csvs").glob("*-engine*/*.csv"))
        + sorted((_REPO_ROOT / "csvs").glob("*-PW*/*.csv"))
    )
    if not candidates:
        pytest.skip("Engine-only CSV not present under csvs/. "
                    "Drop a real CSV to run engine regression.")
    return candidates[0]


# ---------------------------------------------------------------------------
# Benchmark / per-phase isolation
# ---------------------------------------------------------------------------
# :BenchmarkRun and :PhaseScorecard nodes don't carry a real asset_id —
# they use a sentinel `benchmark::<archetype>` (see graph_dal/benchmark.py).
# Tests that exercise those writers need an archetype name that won't
# collide with other tests, plus a teardown that sweeps the sentinel
# asset_id at the end. Use `benchmark_archetype` instead of inventing your own.

@pytest.fixture
def benchmark_archetype(asset_id, neo4j_driver) -> Iterator[str]:
    """A test-isolated archetype name + automatic cleanup.

    Generates `test_arch_<8-hex>` so it's clearly a test artifact AND unique
    per test (lifted from the test's own asset_id). After the test, deletes
    every node with the sentinel asset_id `benchmark::<archetype>`.
    """
    short = asset_id.split("-", 1)[1][:8] if "-" in asset_id else asset_id[:8]
    archetype = f"test_arch_{short}"
    yield archetype
    # Teardown
    from graph_dal import database_name
    from graph_dal.benchmark import benchmark_asset_id
    sentinel = benchmark_asset_id(archetype)
    with neo4j_driver.session(database=database_name()) as s:
        s.run("MATCH (n) WHERE n.asset_id = $aid DETACH DELETE n",
              aid=sentinel).consume()


# ---------------------------------------------------------------------------
# Workdir helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def workdir(tmp_path) -> Path:
    """Throwaway per-test workdir. Same shape as `csvs/<asset>/` — phase
    scripts can write asset_profile.json, progress.log, _checkpoints/, etc.
    here without touching the real csvs/ tree."""
    (tmp_path / "_checkpoints").mkdir()
    return tmp_path
