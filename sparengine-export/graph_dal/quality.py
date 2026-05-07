""":QualityScorecard writer — per-real-asset agent-quality measurement (Layer B).

A ``:QualityScorecard`` is one node per agent run, attached to its
``:AuditRun``. It carries the mechanical-rubric scores (citation presence,
description length, decisions-log parity, …) plus the LLM-as-judge sample
scores. Trend queries over many runs reveal whether the agent's
judgement quality is drifting.

These nodes are NOT fact-bearing — they describe a run, not a piece of
audit content — so the universal "no fact without page evidence" rule
does not apply. ``graph_dal.verify`` puts ``:QualityScorecard`` on the
non-fact-bearing allowlist.

The mechanical rubric is computed by ``tools/quality_scorecard.py``;
LLM-as-judge by ``tools/llm_judge.py``. Both call the writer here.

Wire shape
----------

One node per run, keyed on ``(asset_id, value)`` where ``value =
f"scorecard::{run_id}"`` so a single asset can have many scorecards (one
per re-run). The chain ``[:NEXT]`` between consecutive scorecards for
the same asset enables 30-day trend queries.

Properties
----------

::

    asset_id           — multi-tenancy key (real asset UUID)
    value              — "scorecard::{run_id}"
    run_id             — :AuditRun.value this scorecard describes
    sparengine_version — phase10.SPARENGINE_VERSION at run time
    timestamp          — datetime() set on create, never updated
    created_in_phase   — "post_run" (we run after Phase 10)

    # Mechanical rubric (0..100 scaled, integers):
    mechanical_overall          — weighted average of the rubric
    citation_present_pct        — % of findings with `(file:..., page:...)` citation
    description_length_ok_pct   — % with description ≥ 80 chars
    decisions_log_parity        — count(:Finding) == lines in decisions.log? (0 or 100)
    nine_discipline_pct         — % of "missing X" findings naming all 9 strategies
    severity_sanity_ok          — 0 or 100; passes the LEVEL_1 ratio sanity check
    dal_bypass_count            — number of raw tx.run("MERGE ...") calls detected
    fact_orphan_count           — must be 0; non-zero = golden-rule breach

    # LLM-as-judge (sampled — typically 10 findings, stratified):
    llm_sample_size            — N findings the judge looked at
    llm_mean                   — 1..5 mean across sampled findings
    llm_median                 — 1..5
    llm_p20                    — 20th percentile (worst 20% threshold)
    llm_total_cost_usd         — float, agent-CLI's reported cost

    # Population context (denormalised for cheap trend queries):
    total_findings, level_1_count, level_2_count, level_3_count

    # Deltas vs the previous :QualityScorecard for the same asset:
    delta_vs_previous_mechanical
    delta_vs_previous_llm
"""
from __future__ import annotations

from typing import Any

from ._phase_tag import current_phase


_WRITE_QUALITY_SCORECARD_CYPHER = """
MERGE (q:QualityScorecard {asset_id: $asset_id, value: $value})
ON CREATE SET
    q.run_id                       = $run_id,
    q.sparengine_version           = $sparengine_version,
    q.timestamp                    = datetime(),
    q.created_in_phase             = $created_in_phase,

    q.mechanical_overall           = $mechanical_overall,
    q.citation_present_pct         = $citation_present_pct,
    q.description_length_ok_pct    = $description_length_ok_pct,
    q.decisions_log_parity         = $decisions_log_parity,
    q.nine_discipline_pct          = $nine_discipline_pct,
    q.severity_sanity_ok           = $severity_sanity_ok,
    q.dal_bypass_count             = $dal_bypass_count,
    q.fact_orphan_count            = $fact_orphan_count,

    q.llm_sample_size              = $llm_sample_size,
    q.llm_mean                     = $llm_mean,
    q.llm_median                   = $llm_median,
    q.llm_p20                      = $llm_p20,
    q.llm_total_cost_usd           = $llm_total_cost_usd,

    q.total_findings               = $total_findings,
    q.level_1_count                = $level_1_count,
    q.level_2_count                = $level_2_count,
    q.level_3_count                = $level_3_count,

    q.delta_vs_previous_mechanical = $delta_mechanical,
    q.delta_vs_previous_llm        = $delta_llm
ON MATCH SET
    // re-runs of the scorecard tool overwrite the metrics but keep timestamp
    q.mechanical_overall           = $mechanical_overall,
    q.citation_present_pct         = $citation_present_pct,
    q.description_length_ok_pct    = $description_length_ok_pct,
    q.decisions_log_parity         = $decisions_log_parity,
    q.nine_discipline_pct          = $nine_discipline_pct,
    q.severity_sanity_ok           = $severity_sanity_ok,
    q.dal_bypass_count             = $dal_bypass_count,
    q.fact_orphan_count            = $fact_orphan_count,
    q.llm_sample_size              = $llm_sample_size,
    q.llm_mean                     = $llm_mean,
    q.llm_median                   = $llm_median,
    q.llm_p20                      = $llm_p20,
    q.llm_total_cost_usd           = $llm_total_cost_usd,
    q.total_findings               = $total_findings,
    q.level_1_count                = $level_1_count,
    q.level_2_count                = $level_2_count,
    q.level_3_count                = $level_3_count,
    q.delta_vs_previous_mechanical = $delta_mechanical,
    q.delta_vs_previous_llm        = $delta_llm,
    q.last_updated                 = datetime()
WITH q
// Link to the AuditRun (created in Phase 7). FOR_RUN edge points scorecard → run.
OPTIONAL MATCH (r:AuditRun {asset_id: $asset_id, value: $run_id})
FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
    MERGE (q)-[:FOR_RUN]->(r)
)
WITH q
// Chain to the previous scorecard for this asset, by timestamp ordering.
OPTIONAL MATCH (prev:QualityScorecard {asset_id: $asset_id})
WHERE prev <> q AND prev.timestamp < q.timestamp
WITH q, prev ORDER BY prev.timestamp DESC LIMIT 1
FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END |
    MERGE (prev)-[:NEXT]->(q)
)
RETURN q.value AS value
"""


def write_quality_scorecard(
    tx: Any,
    *,
    asset_id: str,
    run_id: str,
    sparengine_version: str,
    # Mechanical rubric
    mechanical_overall: int,
    citation_present_pct: int,
    description_length_ok_pct: int,
    decisions_log_parity: int,
    nine_discipline_pct: int,
    severity_sanity_ok: int,
    dal_bypass_count: int,
    fact_orphan_count: int,
    # LLM-as-judge
    llm_sample_size: int = 0,
    llm_mean: float | None = None,
    llm_median: float | None = None,
    llm_p20: float | None = None,
    llm_total_cost_usd: float | None = None,
    # Population context
    total_findings: int = 0,
    level_1_count: int = 0,
    level_2_count: int = 0,
    level_3_count: int = 0,
    # Deltas vs previous
    delta_mechanical: float | None = None,
    delta_llm: float | None = None,
) -> str:
    """MERGE a ``:QualityScorecard`` for the given run and link it to its
    ``:AuditRun`` and the previous scorecard's ``[:NEXT]`` chain.

    Parameters are keyword-only by design — too many to remember by position
    and most are optional. Missing LLM-as-judge metrics (``llm_*``) leave the
    properties as ``null`` on the node so trend queries can ``WHERE … IS NOT
    NULL``.

    Returns
    -------
    str
        The ``value`` property of the scorecard ("scorecard::<run_id>").
    """
    value = f"scorecard::{run_id}"
    record = tx.run(
        _WRITE_QUALITY_SCORECARD_CYPHER,
        asset_id=asset_id,
        value=value,
        run_id=run_id,
        sparengine_version=sparengine_version,
        created_in_phase=current_phase() or "post_run",
        mechanical_overall=int(mechanical_overall),
        citation_present_pct=int(citation_present_pct),
        description_length_ok_pct=int(description_length_ok_pct),
        decisions_log_parity=int(decisions_log_parity),
        nine_discipline_pct=int(nine_discipline_pct),
        severity_sanity_ok=int(severity_sanity_ok),
        dal_bypass_count=int(dal_bypass_count),
        fact_orphan_count=int(fact_orphan_count),
        llm_sample_size=int(llm_sample_size),
        llm_mean=llm_mean,
        llm_median=llm_median,
        llm_p20=llm_p20,
        llm_total_cost_usd=llm_total_cost_usd,
        total_findings=int(total_findings),
        level_1_count=int(level_1_count),
        level_2_count=int(level_2_count),
        level_3_count=int(level_3_count),
        delta_mechanical=delta_mechanical,
        delta_llm=delta_llm,
    ).single()
    return record["value"] if record else value
