"""Unit tests for graph_dal.benchmark — pure-Python pieces only.

The writers themselves need Neo4j (those tests live under integration/).
This module covers the helpers and the parameter-validation gates that
run before any Cypher.
"""
from __future__ import annotations

import pytest

from graph_dal.benchmark import (
    benchmark_asset_id,
    merge_benchmark_verdict,
    merge_phase_verdict,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# benchmark_asset_id — sentinel multi-tenancy key
# ---------------------------------------------------------------------------

def test_sentinel_format():
    assert benchmark_asset_id("helicopter_full") == "benchmark::helicopter_full"
    assert benchmark_asset_id("engine_only")     == "benchmark::engine_only"
    assert benchmark_asset_id("ocr_variance")    == "benchmark::ocr_variance"


def test_sentinel_collision_with_real_uuid_is_impossible():
    """A real asset_id is a UUID. `benchmark::*` never matches a UUID shape,
    so trend queries over real assets and benchmark queries can't collide."""
    sentinel = benchmark_asset_id("helicopter_full")
    assert sentinel.startswith("benchmark::")
    assert "::" in sentinel  # double colon never appears in UUIDs
    # Quick UUID-shape check (lowercase hex + 4 dashes in fixed positions)
    assert sentinel.count("-") == 0


def test_sentinel_rejects_empty_archetype():
    with pytest.raises(ValueError):
        benchmark_asset_id("")
    with pytest.raises(ValueError):
        benchmark_asset_id(None)        # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# merge_*_verdict — argument validation runs BEFORE any Cypher
# ---------------------------------------------------------------------------
# We pass a sentinel `tx=None` because the validation path must reject the
# args before reaching `tx.run(...)`. If validation lets a bad arg through,
# we'd get an AttributeError on None — which itself signals the validator
# failed.

class _RecordingTx:
    """Stub tx that records calls; for asserting "validation ran first"."""
    def __init__(self):
        self.calls = []
    def run(self, *args, **kwargs):              # pragma: no cover
        self.calls.append((args, kwargs))
        raise AssertionError("validation should have raised before tx.run")


def test_merge_benchmark_verdict_rejects_bad_verdict():
    tx = _RecordingTx()
    with pytest.raises(ValueError, match="bad verdict"):
        merge_benchmark_verdict(
            tx, archetype="x", version="v1",
            verdict="great_success", confidence=4,
            analysis_path="x.json",
        )
    assert tx.calls == [], "validator should reject before tx.run"


def test_merge_benchmark_verdict_rejects_bad_confidence():
    tx = _RecordingTx()
    for bad in (0, 6, 99, -1):
        with pytest.raises(ValueError, match="confidence"):
            merge_benchmark_verdict(
                tx, archetype="x", version="v1",
                verdict="improvement", confidence=bad,
                analysis_path="x.json",
            )


def test_merge_phase_verdict_rejects_bad_verdict():
    tx = _RecordingTx()
    with pytest.raises(ValueError, match="bad verdict"):
        merge_phase_verdict(
            tx, archetype="x", version="v1", phase_id="phase4",
            verdict="meh", confidence=3,
            reasoning="anything",
        )


def test_merge_phase_verdict_rejects_bad_confidence():
    tx = _RecordingTx()
    with pytest.raises(ValueError, match="confidence"):
        merge_phase_verdict(
            tx, archetype="x", version="v1", phase_id="phase4",
            verdict="regression", confidence=10,
            reasoning="anything",
        )


def test_merge_verdict_accepts_all_four_verdicts():
    """All four verdict strings from the plan must be accepted."""
    # We use an iterating tx that raises once validation passes — that proves
    # validation accepted the verdict (it just couldn't actually run Cypher).
    class _StopAfterValidation:
        def run(self, *args, **kwargs):
            raise StopIteration("validation passed; would have run cypher")
    tx = _StopAfterValidation()
    for verdict in ("improvement", "regression", "no_significant_effect", "mixed"):
        with pytest.raises(StopIteration):
            merge_benchmark_verdict(
                tx, archetype="x", version="v1",
                verdict=verdict, confidence=3,
                analysis_path="x.json",
            )
