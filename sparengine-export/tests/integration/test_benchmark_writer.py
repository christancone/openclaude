"""Integration tests for graph_dal.benchmark — :BenchmarkRun + :PhaseScorecard.

These nodes carry a sentinel asset_id (`benchmark::<archetype>`) rather
than a real UUID. The `benchmark_archetype` fixture handles cleanup.
"""
from __future__ import annotations

import time

import pytest

from graph_dal import database_name
from graph_dal.benchmark import (
    benchmark_asset_id,
    write_benchmark_run,
    write_phase_scorecard,
    merge_benchmark_verdict,
    merge_phase_verdict,
)


pytestmark = pytest.mark.integration


def _read_benchmark(driver, archetype, version):
    aid = benchmark_asset_id(archetype)
    val = f"benchmark::{version}::{archetype}"
    with driver.session(database=database_name()) as s:
        return s.run(
            "MATCH (b:BenchmarkRun {asset_id: $aid, value: $v}) "
            "RETURN b.archetype AS arch, b.version AS ver, "
            "       b.total_components AS comps, b.mechanical_overall AS mech, "
            "       b.analysis_verdict AS verdict, b.analysed AS analysed",
            aid=aid, v=val,
        ).single()


def test_write_benchmark_run_round_trips(neo4j_driver, benchmark_archetype):
    """write_benchmark_run creates a node, returns canonical value."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            value = write_benchmark_run(
                tx,
                archetype=benchmark_archetype, version="v1-test",
                git_sha="abc1234", commit_msg="test commit",
                sparengine_version="phase10-vTEST",
                archive_path=f"benchmarks/v1-test/{benchmark_archetype}",
                total_pages=100, total_documents=10,
                total_components=42, total_events=18, total_findings=7,
                mechanical_overall=88, llm_mean=4.0,
            )
            tx.commit()
    assert value == f"benchmark::v1-test::{benchmark_archetype}"

    rec = _read_benchmark(neo4j_driver, benchmark_archetype, "v1-test")
    assert rec["arch"] == benchmark_archetype
    assert rec["ver"]  == "v1-test"
    assert rec["comps"] == 42
    assert rec["mech"]  == 88
    assert rec["analysed"] is False, "fresh BenchmarkRun should be analysed=false"
    assert rec["verdict"] is None


def test_write_benchmark_run_idempotent(neo4j_driver, benchmark_archetype):
    """Re-running write_benchmark_run for the same (archetype, version)
    overwrites metrics rather than duplicating the node."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v2-test",
                git_sha="x", commit_msg="x", sparengine_version="x",
                archive_path="x",
                total_components=10,
            )
            tx.commit()
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v2-test",
                git_sha="x", commit_msg="x", sparengine_version="x",
                archive_path="x",
                total_components=99,                       # bumped
            )
            tx.commit()

        rs = s.run(
            "MATCH (b:BenchmarkRun {asset_id: $aid}) RETURN b.total_components AS comps",
            aid=benchmark_asset_id(benchmark_archetype),
        ).data()
    assert len(rs) == 1
    assert rs[0]["comps"] == 99


def test_consecutive_versions_chain_via_next(neo4j_driver, benchmark_archetype):
    """Two BenchmarkRuns for the same archetype, different versions, must
    be linked by [:NEXT] in timestamp order so trend traversal is one-hop."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v1",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            tx.commit()
        time.sleep(0.05)
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v2",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            tx.commit()

        rec = s.run(
            "MATCH (a:BenchmarkRun {asset_id: $aid, version: 'v1'})"
            "-[:NEXT]->(b:BenchmarkRun {asset_id: $aid, version: 'v2'}) "
            "RETURN a.value AS a, b.value AS b",
            aid=benchmark_asset_id(benchmark_archetype),
        ).single()
    assert rec is not None, "[:NEXT] chain not built between v1 and v2"


def test_merge_benchmark_verdict_updates_existing(neo4j_driver, benchmark_archetype):
    """merge_benchmark_verdict flips analysed=true and writes the verdict."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v3",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            tx.commit()
        with s.begin_transaction() as tx:
            merge_benchmark_verdict(
                tx, archetype=benchmark_archetype, version="v3",
                verdict="improvement", confidence=4,
                analysis_path="benchmarks/v3/_analysis/verdict.json",
            )
            tx.commit()

    rec = _read_benchmark(neo4j_driver, benchmark_archetype, "v3")
    assert rec["analysed"]   is True
    assert rec["verdict"]    == "improvement"


def test_merge_benchmark_verdict_raises_when_run_missing(neo4j_driver, benchmark_archetype):
    """If you call merge_benchmark_verdict before writing the run,
    the writer should fail loud, not silently no-op."""
    with neo4j_driver.session(database=database_name()) as s:
        with pytest.raises(RuntimeError, match="no :BenchmarkRun found"):
            with s.begin_transaction() as tx:
                merge_benchmark_verdict(
                    tx, archetype=benchmark_archetype, version="never-written",
                    verdict="improvement", confidence=4,
                    analysis_path="x",
                )
                tx.commit()


def test_phase_scorecard_links_via_part_of(neo4j_driver, benchmark_archetype):
    """A PhaseScorecard for an existing BenchmarkRun must be linked
    [:PART_OF]->(:BenchmarkRun) automatically."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v4",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            write_phase_scorecard(
                tx, archetype=benchmark_archetype, version="v4",
                phase_id="phase4_components",
                phase_brief_path="phases/briefs/phase4_components.md",
                phase_brief_sha="0" * 64,
                phase_nodes_written=950, phase_findings_written=10,
                mechanical_overall=92, llm_mean=4.3,
            )
            tx.commit()

        rec = s.run(
            "MATCH (p:PhaseScorecard {phase_id: 'phase4_components', version: 'v4'})"
            "-[:PART_OF]->(b:BenchmarkRun {version: 'v4'}) "
            "WHERE b.asset_id = $aid AND p.asset_id = $aid "
            "RETURN p.value AS p, b.value AS b",
            aid=benchmark_asset_id(benchmark_archetype),
        ).single()
    assert rec is not None, "[:PART_OF] edge not created"


def test_phase_scorecard_consecutive_versions_chain_via_next(neo4j_driver, benchmark_archetype):
    """Same archetype + same phase_id across two versions → [:NEXT] chain."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v5",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            write_phase_scorecard(
                tx, archetype=benchmark_archetype, version="v5",
                phase_id="phase4_components",
                phase_brief_path="phases/briefs/phase4_components.md",
                phase_brief_sha="0" * 64,
                phase_nodes_written=100, mechanical_overall=80,
            )
            tx.commit()
        time.sleep(0.05)
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v6",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            write_phase_scorecard(
                tx, archetype=benchmark_archetype, version="v6",
                phase_id="phase4_components",
                phase_brief_path="phases/briefs/phase4_components.md",
                phase_brief_sha="1" * 64,
                phase_nodes_written=85, mechanical_overall=90,
            )
            tx.commit()

        rec = s.run(
            "MATCH (a:PhaseScorecard {version: 'v5', phase_id: 'phase4_components', asset_id: $aid})"
            "-[:NEXT]->(b:PhaseScorecard {version: 'v6', phase_id: 'phase4_components', asset_id: $aid}) "
            "RETURN a.version AS a, b.version AS b",
            aid=benchmark_asset_id(benchmark_archetype),
        ).single()
    assert rec is not None, "[:NEXT] chain not built between phase scorecards"


def test_merge_phase_verdict_round_trips(neo4j_driver, benchmark_archetype):
    """merge_phase_verdict writes verdict + confidence + reasoning."""
    with neo4j_driver.session(database=database_name()) as s:
        with s.begin_transaction() as tx:
            write_benchmark_run(
                tx, archetype=benchmark_archetype, version="v7",
                git_sha="x", commit_msg="x", sparengine_version="x", archive_path="x",
            )
            write_phase_scorecard(
                tx, archetype=benchmark_archetype, version="v7",
                phase_id="phase7_investigation",
                phase_brief_path="phases/briefs/phase7_investigation.md",
                phase_brief_sha="0" * 64,
            )
            merge_phase_verdict(
                tx, archetype=benchmark_archetype, version="v7",
                phase_id="phase7_investigation",
                verdict="regression", confidence=3,
                reasoning="Citation count dropped sharply.",
            )
            tx.commit()

        rec = s.run(
            "MATCH (p:PhaseScorecard {asset_id: $aid, version: 'v7', phase_id: 'phase7_investigation'}) "
            "RETURN p.analysis_verdict AS v, p.analysis_confidence AS c, "
            "       p.analysis_reasoning AS r",
            aid=benchmark_asset_id(benchmark_archetype),
        ).single()
    assert rec["v"] == "regression"
    assert rec["c"] == 3
    assert "Citation count" in rec["r"]
