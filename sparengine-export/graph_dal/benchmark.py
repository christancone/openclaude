""":BenchmarkRun + :PhaseScorecard writers (Layer C — cross-version analysis).

These nodes describe **meta-runs**: synthetic re-runs of the pipeline on
archetype CSVs (``helicopter_full``, ``engine_only``, ``ocr_variance``)
captured by ``tools/benchmark_archive.py`` after every ``make regression``.
They are NOT per-real-asset — they let us trend "how is sparengine itself
changing across versions?" independent of any one customer dossier.

Multi-tenancy
-------------

The codebase invariant is that every node carries an ``asset_id``. Benchmark
runs aren't real-asset data, but we keep the invariant intact by using a
**sentinel asset_id** of the shape ``benchmark::<archetype>``. That way:

  * every constraint stays ``(asset_id, value) UNIQUE``
  * trend queries like ``MATCH (b:BenchmarkRun {asset_id: 'benchmark::helicopter_full'})``
    work exactly like ``(:AuditRun {asset_id: '<real-uuid>'})``
  * ``verify_no_fact_orphans`` for real assets never sees benchmark data
    because the sentinel asset_id never matches a real UUID

The sentinel format is a constant of this module: ``benchmark_asset_id()``.

Wire shape
----------

``:BenchmarkRun``  — one per (version × archetype). Holds the count totals,
                     mechanical/LLM scores, deltas vs. previous version,
                     and the analyser verdict if ``analyse_change.py`` ran.

``:PhaseScorecard`` — one per (version × archetype × phase_id). Carries
                      phase-scoped metrics that ``benchmark_archive.py``
                      computes by reading ``per_phase/<phase_id>/metrics.json``
                      out of the archive directory. ``[:PART_OF]`` links
                      phase scorecards to their owning benchmark run.

``[:NEXT]`` chains link consecutive versions of the same archetype (for
``:BenchmarkRun``) and consecutive versions of the same phase (for
``:PhaseScorecard``) so trend traversals are one-hop.
"""
from __future__ import annotations

from typing import Any

from ._phase_tag import current_phase


# -----------------------------------------------------------------------------
#  Sentinel asset_id helper
# -----------------------------------------------------------------------------

def benchmark_asset_id(archetype: str) -> str:
    """Return the sentinel asset_id used for benchmark meta-runs.

    Format: ``benchmark::<archetype>`` (e.g. ``benchmark::helicopter_full``).
    The ``benchmark::`` prefix never appears in a real UUID, so trend queries
    over benchmark data and queries over real-asset data never collide.
    """
    if not archetype or not isinstance(archetype, str):
        raise ValueError(f"benchmark_asset_id: archetype must be non-empty str, got {archetype!r}")
    return f"benchmark::{archetype}"


# =============================================================================
#  :BenchmarkRun
# =============================================================================

_WRITE_BENCHMARK_RUN_CYPHER = """
MERGE (b:BenchmarkRun {asset_id: $asset_id, value: $value})
ON CREATE SET
    b.version            = $version,
    b.git_sha            = $git_sha,
    b.commit_msg         = $commit_msg,
    b.timestamp          = datetime(),
    b.archetype          = $archetype,
    b.sparengine_version = $sparengine_version,
    b.created_in_phase   = $created_in_phase,
    // count totals
    b.total_pages        = $total_pages,
    b.total_documents    = $total_documents,
    b.total_components   = $total_components,
    b.total_events       = $total_events,
    b.total_findings     = $total_findings,
    b.total_form1        = $total_form1,
    b.total_stamps       = $total_stamps,
    b.fact_orphan_count  = $fact_orphan_count,
    // judgement quality
    b.mechanical_overall = $mechanical_overall,
    b.llm_mean           = $llm_mean,
    b.llm_p20            = $llm_p20,
    // diff vs previous
    b.delta_components   = $delta_components,
    b.delta_events       = $delta_events,
    b.delta_findings     = $delta_findings,
    b.delta_mechanical   = $delta_mechanical,
    b.delta_llm_mean     = $delta_llm_mean,
    // archive pointer + analyser hooks
    b.archive_path       = $archive_path,
    b.analysed           = false,
    b.analysis_verdict   = null,
    b.analysis_confidence = null,
    b.analysis_path      = null
ON MATCH SET
    // benchmark_archive.py is rerun-safe; overwrite metrics on second invocation
    b.total_pages        = $total_pages,
    b.total_documents    = $total_documents,
    b.total_components   = $total_components,
    b.total_events       = $total_events,
    b.total_findings     = $total_findings,
    b.total_form1        = $total_form1,
    b.total_stamps       = $total_stamps,
    b.fact_orphan_count  = $fact_orphan_count,
    b.mechanical_overall = $mechanical_overall,
    b.llm_mean           = $llm_mean,
    b.llm_p20            = $llm_p20,
    b.delta_components   = $delta_components,
    b.delta_events       = $delta_events,
    b.delta_findings     = $delta_findings,
    b.delta_mechanical   = $delta_mechanical,
    b.delta_llm_mean     = $delta_llm_mean,
    b.archive_path       = $archive_path,
    b.last_updated       = datetime()
WITH b
// Chain to the previous BenchmarkRun for this archetype.
OPTIONAL MATCH (prev:BenchmarkRun {archetype: $archetype})
WHERE prev <> b AND prev.timestamp < b.timestamp
WITH b, prev ORDER BY prev.timestamp DESC LIMIT 1
FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END |
    MERGE (prev)-[:NEXT]->(b)
)
RETURN b.value AS value
"""


def write_benchmark_run(
    tx: Any,
    *,
    archetype: str,
    version: str,
    git_sha: str,
    commit_msg: str,
    sparengine_version: str,
    archive_path: str,
    # Counts
    total_pages: int = 0,
    total_documents: int = 0,
    total_components: int = 0,
    total_events: int = 0,
    total_findings: int = 0,
    total_form1: int = 0,
    total_stamps: int = 0,
    fact_orphan_count: int = 0,
    # Quality
    mechanical_overall: int | None = None,
    llm_mean: float | None = None,
    llm_p20: float | None = None,
    # Deltas vs previous version of the same archetype
    delta_components: int | None = None,
    delta_events: int | None = None,
    delta_findings: int | None = None,
    delta_mechanical: float | None = None,
    delta_llm_mean: float | None = None,
) -> str:
    """MERGE a ``:BenchmarkRun`` for ``(archetype, version)``.

    Idempotent — re-running ``benchmark_archive.py`` against the same
    version/archetype updates the metrics in-place rather than creating
    duplicates. Chains the new run to the previous version's run via
    ``[:NEXT]``.

    Returns
    -------
    str
        The ``value`` property — ``benchmark::<version>::<archetype>``.
    """
    asset_id = benchmark_asset_id(archetype)
    value    = f"benchmark::{version}::{archetype}"
    record = tx.run(
        _WRITE_BENCHMARK_RUN_CYPHER,
        asset_id=asset_id,
        value=value,
        version=version,
        git_sha=git_sha,
        commit_msg=commit_msg,
        archetype=archetype,
        sparengine_version=sparengine_version,
        archive_path=archive_path,
        created_in_phase=current_phase() or "benchmark_archive",
        total_pages=int(total_pages),
        total_documents=int(total_documents),
        total_components=int(total_components),
        total_events=int(total_events),
        total_findings=int(total_findings),
        total_form1=int(total_form1),
        total_stamps=int(total_stamps),
        fact_orphan_count=int(fact_orphan_count),
        mechanical_overall=mechanical_overall,
        llm_mean=llm_mean,
        llm_p20=llm_p20,
        delta_components=delta_components,
        delta_events=delta_events,
        delta_findings=delta_findings,
        delta_mechanical=delta_mechanical,
        delta_llm_mean=delta_llm_mean,
    ).single()
    return record["value"] if record else value


# =============================================================================
#  :BenchmarkRun — analysis verdict merge (Layer C agent post-run)
# =============================================================================

_MERGE_BENCHMARK_VERDICT_CYPHER = """
MATCH (b:BenchmarkRun {asset_id: $asset_id, value: $value})
SET
    b.analysed            = true,
    b.analysis_verdict    = $verdict,
    b.analysis_confidence = $confidence,
    b.analysis_path       = $analysis_path,
    b.analysis_timestamp  = datetime()
RETURN b.value AS value
"""


def merge_benchmark_verdict(
    tx: Any,
    *,
    archetype: str,
    version: str,
    verdict: str,
    confidence: int,
    analysis_path: str,
) -> str:
    """Merge the analyser's verdict into an existing ``:BenchmarkRun``.

    Called by ``tools/analyse_change.py`` after the agent writes its
    ``verdict.json`` into ``benchmarks/<version>/_analysis/``. Verdict is
    one of: ``improvement``, ``regression``, ``no_significant_effect``,
    ``mixed``. Confidence is 1..5.
    """
    if verdict not in {"improvement", "regression", "no_significant_effect", "mixed"}:
        raise ValueError(f"merge_benchmark_verdict: bad verdict {verdict!r}")
    if not (1 <= int(confidence) <= 5):
        raise ValueError(f"merge_benchmark_verdict: confidence must be 1..5, got {confidence!r}")
    asset_id = benchmark_asset_id(archetype)
    value    = f"benchmark::{version}::{archetype}"
    record = tx.run(
        _MERGE_BENCHMARK_VERDICT_CYPHER,
        asset_id=asset_id,
        value=value,
        verdict=verdict,
        confidence=int(confidence),
        analysis_path=analysis_path,
    ).single()
    if record is None:
        raise RuntimeError(
            f"merge_benchmark_verdict: no :BenchmarkRun found for "
            f"archetype={archetype!r}, version={version!r}. "
            f"Run benchmark_archive.py for this version first."
        )
    return record["value"]


# =============================================================================
#  :PhaseScorecard
# =============================================================================

_WRITE_PHASE_SCORECARD_CYPHER = """
MERGE (p:PhaseScorecard {asset_id: $asset_id, value: $value})
ON CREATE SET
    p.version                       = $version,
    p.archetype                     = $archetype,
    p.phase_id                      = $phase_id,
    p.phase_brief_path              = $phase_brief_path,
    p.phase_brief_sha               = $phase_brief_sha,
    p.timestamp                     = datetime(),
    p.created_in_phase              = $created_in_phase,
    // phase-scoped structural metrics
    p.phase_nodes_written           = $phase_nodes_written,
    p.phase_edges_written           = $phase_edges_written,
    p.phase_findings_written        = $phase_findings_written,
    p.phase_runtime_seconds         = $phase_runtime_seconds,
    // mechanical scorecard scoped to this phase's outputs
    p.mechanical_overall            = $mechanical_overall,
    p.citation_present_pct          = $citation_present_pct,
    p.description_length_ok_pct     = $description_length_ok_pct,
    p.nine_discipline_pct           = $nine_discipline_pct,
    p.dal_bypass_count              = $dal_bypass_count,
    // LLM-as-judge sampled from this phase's outputs
    p.llm_sample_size               = $llm_sample_size,
    p.llm_mean                      = $llm_mean,
    p.llm_p20                       = $llm_p20,
    // delta vs previous version of THIS phase
    p.delta_nodes_written           = $delta_nodes_written,
    p.delta_findings_written        = $delta_findings_written,
    p.delta_mechanical              = $delta_mechanical,
    p.delta_llm_mean                = $delta_llm_mean,
    p.brief_changed_since_previous  = $brief_changed_since_previous,
    // analyser verdict (filled in later by analyse_change.py)
    p.analysis_verdict              = null,
    p.analysis_confidence           = null,
    p.analysis_reasoning            = null
ON MATCH SET
    p.phase_nodes_written           = $phase_nodes_written,
    p.phase_edges_written           = $phase_edges_written,
    p.phase_findings_written        = $phase_findings_written,
    p.phase_runtime_seconds         = $phase_runtime_seconds,
    p.mechanical_overall            = $mechanical_overall,
    p.citation_present_pct          = $citation_present_pct,
    p.description_length_ok_pct     = $description_length_ok_pct,
    p.nine_discipline_pct           = $nine_discipline_pct,
    p.dal_bypass_count              = $dal_bypass_count,
    p.llm_sample_size               = $llm_sample_size,
    p.llm_mean                      = $llm_mean,
    p.llm_p20                       = $llm_p20,
    p.delta_nodes_written           = $delta_nodes_written,
    p.delta_findings_written        = $delta_findings_written,
    p.delta_mechanical              = $delta_mechanical,
    p.delta_llm_mean                = $delta_llm_mean,
    p.brief_changed_since_previous  = $brief_changed_since_previous,
    p.last_updated                  = datetime()
WITH p
// Link to the owning :BenchmarkRun for this (archetype, version).
OPTIONAL MATCH (b:BenchmarkRun {asset_id: $asset_id, archetype: $archetype, version: $version})
FOREACH (_ IN CASE WHEN b IS NULL THEN [] ELSE [1] END |
    MERGE (p)-[:PART_OF]->(b)
)
WITH p
// Chain to the previous PhaseScorecard for this (archetype, phase_id).
OPTIONAL MATCH (prev:PhaseScorecard {archetype: $archetype, phase_id: $phase_id})
WHERE prev <> p AND prev.timestamp < p.timestamp
WITH p, prev ORDER BY prev.timestamp DESC LIMIT 1
FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END |
    MERGE (prev)-[:NEXT]->(p)
)
RETURN p.value AS value
"""


def write_phase_scorecard(
    tx: Any,
    *,
    archetype: str,
    version: str,
    phase_id: str,                              # e.g. "phase4_components"
    phase_brief_path: str,                      # e.g. "phases/briefs/phase4_components.md"
    phase_brief_sha: str,                       # sha256 of the brief at run time
    # Phase-scoped counts
    phase_nodes_written: int = 0,
    phase_edges_written: int = 0,
    phase_findings_written: int = 0,
    phase_runtime_seconds: float = 0.0,
    # Mechanical rubric
    mechanical_overall: int | None = None,
    citation_present_pct: int | None = None,
    description_length_ok_pct: int | None = None,
    nine_discipline_pct: int | None = None,
    dal_bypass_count: int | None = None,
    # LLM-as-judge
    llm_sample_size: int = 0,
    llm_mean: float | None = None,
    llm_p20: float | None = None,
    # Deltas
    delta_nodes_written: int | None = None,
    delta_findings_written: int | None = None,
    delta_mechanical: float | None = None,
    delta_llm_mean: float | None = None,
    brief_changed_since_previous: bool = False,
) -> str:
    """MERGE a ``:PhaseScorecard`` for ``(archetype, version, phase_id)``.

    Linked into the graph via:
      * ``[:PART_OF]`` to the owning ``:BenchmarkRun``
      * ``[:NEXT]`` from the previous version's scorecard for the same
        ``(archetype, phase_id)`` pair

    Returns
    -------
    str
        The ``value`` property — ``phase::<version>::<archetype>::<phase_id>``.
    """
    if not phase_id:
        raise ValueError("write_phase_scorecard: phase_id is required")
    asset_id = benchmark_asset_id(archetype)
    value    = f"phase::{version}::{archetype}::{phase_id}"
    record = tx.run(
        _WRITE_PHASE_SCORECARD_CYPHER,
        asset_id=asset_id,
        value=value,
        version=version,
        archetype=archetype,
        phase_id=phase_id,
        phase_brief_path=phase_brief_path,
        phase_brief_sha=phase_brief_sha,
        created_in_phase=current_phase() or "benchmark_archive",
        phase_nodes_written=int(phase_nodes_written),
        phase_edges_written=int(phase_edges_written),
        phase_findings_written=int(phase_findings_written),
        phase_runtime_seconds=float(phase_runtime_seconds),
        mechanical_overall=mechanical_overall,
        citation_present_pct=citation_present_pct,
        description_length_ok_pct=description_length_ok_pct,
        nine_discipline_pct=nine_discipline_pct,
        dal_bypass_count=dal_bypass_count,
        llm_sample_size=int(llm_sample_size),
        llm_mean=llm_mean,
        llm_p20=llm_p20,
        delta_nodes_written=delta_nodes_written,
        delta_findings_written=delta_findings_written,
        delta_mechanical=delta_mechanical,
        delta_llm_mean=delta_llm_mean,
        brief_changed_since_previous=bool(brief_changed_since_previous),
    ).single()
    return record["value"] if record else value


# =============================================================================
#  :PhaseScorecard — analyser verdict merge
# =============================================================================

_MERGE_PHASE_VERDICT_CYPHER = """
MATCH (p:PhaseScorecard {asset_id: $asset_id, value: $value})
SET
    p.analysis_verdict    = $verdict,
    p.analysis_confidence = $confidence,
    p.analysis_reasoning  = $reasoning,
    p.analysis_timestamp  = datetime()
RETURN p.value AS value
"""


def merge_phase_verdict(
    tx: Any,
    *,
    archetype: str,
    version: str,
    phase_id: str,
    verdict: str,
    confidence: int,
    reasoning: str,
) -> str:
    """Merge per-phase verdict from the analyser into an existing ``:PhaseScorecard``."""
    if verdict not in {"improvement", "regression", "no_significant_effect", "mixed"}:
        raise ValueError(f"merge_phase_verdict: bad verdict {verdict!r}")
    if not (1 <= int(confidence) <= 5):
        raise ValueError(f"merge_phase_verdict: confidence must be 1..5, got {confidence!r}")
    asset_id = benchmark_asset_id(archetype)
    value    = f"phase::{version}::{archetype}::{phase_id}"
    record = tx.run(
        _MERGE_PHASE_VERDICT_CYPHER,
        asset_id=asset_id,
        value=value,
        verdict=verdict,
        confidence=int(confidence),
        reasoning=reasoning,
    ).single()
    if record is None:
        raise RuntimeError(
            f"merge_phase_verdict: no :PhaseScorecard found for "
            f"archetype={archetype!r}, version={version!r}, phase_id={phase_id!r}. "
            f"Run benchmark_archive.py for this version first."
        )
    return record["value"]
