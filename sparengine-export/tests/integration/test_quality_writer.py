"""Integration tests for graph_dal.quality.write_quality_scorecard.

Exercises the writer end-to-end against the production Neo4j instance.
Each test gets a fresh test asset_id (random UUID, prefixed `test-`) and
the conftest's autouse cleanup deletes everything carrying that asset_id
at teardown.
"""
from __future__ import annotations

import pytest

from graph_dal import database_name
from graph_dal._phase_tag import phase
from graph_dal.quality import write_quality_scorecard


pytestmark = pytest.mark.integration


def _writer_args(**overrides):
    """Return a kwargs dict with all required scorecard fields filled in."""
    base = dict(
        run_id="run-integration-001",
        sparengine_version="phase10-vTEST",
        mechanical_overall=85,
        citation_present_pct=92,
        description_length_ok_pct=88,
        decisions_log_parity=100,
        nine_discipline_pct=80,
        severity_sanity_ok=100,
        dal_bypass_count=0,
        fact_orphan_count=0,
        llm_sample_size=10,
        llm_mean=4.1,
        llm_median=4.0,
        llm_p20=3.4,
        llm_total_cost_usd=0.05,
        total_findings=42,
        level_1_count=3,
        level_2_count=14,
        level_3_count=25,
    )
    base.update(overrides)
    return base


def _read_scorecard(driver, asset_id, value):
    with driver.session(database=database_name()) as s:
        return s.run(
            "MATCH (q:QualityScorecard {asset_id: $aid, value: $v}) "
            "RETURN q.mechanical_overall AS mech, q.llm_mean AS llm, "
            "       q.created_in_phase AS phase, q.total_findings AS findings",
            aid=asset_id, v=value,
        ).single()


def test_write_creates_node_with_canonical_value(neo4j_driver, asset_id):
    """write_quality_scorecard returns scorecard::<run_id> and creates a node."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            value = write_quality_scorecard(
                tx, asset_id=asset_id, **_writer_args(run_id="run-A"),
            )
            tx.commit()
    assert value == "scorecard::run-A"

    rec = _read_scorecard(neo4j_driver, asset_id, value)
    assert rec is not None, "scorecard not persisted"
    assert rec["mech"] == 85
    assert abs(rec["llm"] - 4.1) < 1e-9
    assert rec["findings"] == 42


def test_phase_tag_propagates_to_created_in_phase(neo4j_driver, asset_id):
    """When write runs inside `with phase(...)`, the property carries the id."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            with phase("post_run"):
                value = write_quality_scorecard(
                    tx, asset_id=asset_id, **_writer_args(run_id="run-B"),
                )
            tx.commit()
    rec = _read_scorecard(neo4j_driver, asset_id, value)
    assert rec["phase"] == "post_run"


def test_default_phase_tag_when_no_context(neo4j_driver, asset_id):
    """Outside any `phase()` block, the default `created_in_phase` is "post_run"."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            value = write_quality_scorecard(
                tx, asset_id=asset_id, **_writer_args(run_id="run-C"),
            )
            tx.commit()
    rec = _read_scorecard(neo4j_driver, asset_id, value)
    assert rec["phase"] == "post_run"


def test_idempotent_overwrites_metrics(neo4j_driver, asset_id):
    """Re-running the rubric tool MUST overwrite metrics, not duplicate the node.

    The same (asset_id, run_id) should land on the same scorecard; a second
    write with different metrics updates them in place.
    """
    with neo4j_driver.session(database=database_name()) as s:
        # First write: mech=85
        with s.begin_transaction() as tx:
            v1 = write_quality_scorecard(
                tx, asset_id=asset_id, **_writer_args(run_id="run-D", mechanical_overall=85),
            )
            tx.commit()
        # Second write: mech=91 (the rubric tool got better numbers)
        with s.begin_transaction() as tx:
            v2 = write_quality_scorecard(
                tx, asset_id=asset_id, **_writer_args(run_id="run-D", mechanical_overall=91),
            )
            tx.commit()

    assert v1 == v2 == "scorecard::run-D"

    # Single node, mech == latest write
    with neo4j_driver.session(database=database_name()) as s:
        rs = s.run(
            "MATCH (q:QualityScorecard {asset_id: $aid}) RETURN q.mechanical_overall AS mech",
            aid=asset_id,
        ).data()
    assert len(rs) == 1, f"expected one scorecard, got {len(rs)}"
    assert rs[0]["mech"] == 91


def test_chains_to_previous_via_next(neo4j_driver, asset_id):
    """Two scorecards for the same asset should be linked by [:NEXT] in
    timestamp order. This is what powers the 30-day trend query."""
    import time
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_quality_scorecard(
                tx, asset_id=asset_id, **_writer_args(run_id="run-T1"),
            )
            tx.commit()

    # Sleep so the second scorecard's timestamp is strictly later. Neo4j
    # `datetime()` resolves to ms; 5ms gap is enough.
    time.sleep(0.05)

    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_quality_scorecard(
                tx, asset_id=asset_id, **_writer_args(run_id="run-T2"),
            )
            tx.commit()

        rec = s.run(
            "MATCH (a:QualityScorecard {asset_id: $aid, value: 'scorecard::run-T1'})"
            "-[:NEXT]->(b:QualityScorecard {asset_id: $aid, value: 'scorecard::run-T2'}) "
            "RETURN a.value AS a, b.value AS b",
            aid=asset_id,
        ).single()
    assert rec is not None, "[:NEXT] chain not built between consecutive scorecards"
    assert rec["a"] == "scorecard::run-T1"
    assert rec["b"] == "scorecard::run-T2"
