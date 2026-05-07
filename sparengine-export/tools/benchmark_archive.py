"""Benchmark archive — snapshots a finished pipeline run for cross-version
analysis (Layer C). One archive per `(version, archetype)`.

Triggered by `make regression`. Does NOT drive the pipeline itself —
expects the pipeline to have already completed against the archetype CSV.
The archive captures:

  benchmarks/<version>/<archetype>/
    metadata.json          (version, git context, brief diff)
    counts.json            (node + edge counts per label / type)
    progress.log           (verbatim copy from workdir)
    decisions.log          (verbatim copy)
    quality_scorecard.json (Layer B output)
    llm_judgement.json     (Layer B LLM-as-judge, if run)
    finding_samples.json   (20 stratified findings, with cited evidence)
    graph_export.json      (full lossless export — large, .gitignore'd)
    restore.cypher         (replayable Cypher — large, .gitignore'd)
    per_phase/<phase_id>/
      metrics.json         (phase-scoped node/edge counts)
      samples.json         (5 representative phase outputs)

Then MERGE :BenchmarkRun + :PhaseScorecard into Neo4j.

CLI
---

::

    python -m tools.benchmark_archive \\
        --archetype helicopter_full \\
        --workdir /tmp/regression/helicopter_full \\
        --version v4-2026-05-07-9c2d4f3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

from graph_dal import connect, database_name                              # noqa: E402
from graph_dal.benchmark import (                                         # noqa: E402
    write_benchmark_run, write_phase_scorecard, benchmark_asset_id,
)


_REPO_ROOT     = _HERE.parent.parent.parent
_BENCHMARKS    = _HERE.parent.parent / "benchmarks"
_BRIEFS_DIR    = _HERE.parent.parent / "phases" / "briefs"


# ---------------------------------------------------------------------------
# Phase enumeration — walks the brief filenames to enumerate phases
# ---------------------------------------------------------------------------

def _enumerate_phase_briefs() -> list[Path]:
    """Return the phase brief paths in pipeline order."""
    # OVERVIEW.md and phase_analyse.md are NOT phase briefs in the pipeline
    # sense; only `phaseN_*.md` files count.
    out = sorted(p for p in _BRIEFS_DIR.glob("phase*_*.md")
                 if p.name not in ("phase_analyse.md",))
    return out


def _phase_id_from_brief(brief: Path) -> str:
    """`phase4_components.md` → `phase4_components`."""
    return brief.stem


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Git context
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git"] + list(args), cwd=_REPO_ROOT,
            text=True, capture_output=True, timeout=10,
        )
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _git_context() -> dict[str, str]:
    return {
        "git_sha":     _git("rev-parse", "--short", "HEAD") or "unknown",
        "git_full_sha": _git("rev-parse", "HEAD") or "unknown",
        "commit_msg":   _git("log", "-1", "--format=%s") or "unknown",
        "branch":       _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
    }


# ---------------------------------------------------------------------------
# Counts + finding sampling
# ---------------------------------------------------------------------------

def _read_counts(driver, asset_id: str) -> dict[str, int]:
    """Per-label node count + per-edge-type relationship count for the asset."""
    counts: dict[str, int] = {}
    with driver.session(database=database_name()) as s:
        # Node counts per label
        rs = s.run(
            "MATCH (n {asset_id: $aid}) "
            "UNWIND labels(n) AS l "
            "RETURN l AS label, count(*) AS n",
            aid=asset_id,
        )
        for r in rs:
            counts[f"node:{r['label']}"] = int(r["n"])
        # Edge counts per type
        rs = s.run(
            "MATCH (a {asset_id: $aid})-[r]-(b {asset_id: $aid}) "
            "RETURN type(r) AS t, count(*) AS n",
            aid=asset_id,
        )
        for r in rs:
            # The MATCH counts each edge twice (a->b and b->a paths). Halve.
            counts[f"edge:{r['t']}"] = int(r["n"]) // 2
    return counts


def _sample_findings_stratified(driver, asset_id: str) -> list[dict[str, Any]]:
    """20 findings stratified by severity (3 L1, 7 L2, 10 L3)."""
    out: list[dict[str, Any]] = []
    plan = [("level_1", 3), ("level_2", 7), ("level_3", 10)]
    for sev, n in plan:
        with driver.session(database=database_name()) as s:
            rs = s.run(
                """
                MATCH (f:Finding {asset_id: $aid, severity: $sev})
                OPTIONAL MATCH (f)-[e:EVIDENCED_BY]->(p:Page)
                RETURN f.value AS value, f.category AS category,
                       f.title AS title, f.description AS description,
                       collect({page: p.value, file: p.file_name, quote: e.quote})[0..3]
                       AS evidence
                ORDER BY f.value LIMIT $n
                """,
                aid=asset_id, sev=sev, n=n,
            )
            out.extend(dict(r) for r in rs)
    return out


# ---------------------------------------------------------------------------
# Per-phase metrics
# ---------------------------------------------------------------------------

def _phase_metrics(driver, asset_id: str, phase_id: str) -> dict[str, Any]:
    """Phase-scoped counts: nodes/edges/findings tagged created_in_phase=phase_id.

    DAL writers retrofit-tag with `created_in_phase` from `_phase_tag.py`.
    Until every writer is retrofitted, phases without any tagged nodes
    will report zero — that's a feature (it surfaces missing instrumentation).
    """
    with driver.session(database=database_name()) as s:
        n_rec = s.run(
            "MATCH (n {asset_id: $aid, created_in_phase: $p}) RETURN count(n) AS n",
            aid=asset_id, p=phase_id,
        ).single()
        f_rec = s.run(
            "MATCH (f:Finding {asset_id: $aid, created_in_phase: $p}) RETURN count(f) AS n",
            aid=asset_id, p=phase_id,
        ).single()
    return {
        "phase_id":              phase_id,
        "phase_nodes_written":   int(n_rec["n"]) if n_rec else 0,
        "phase_findings_written": int(f_rec["n"]) if f_rec else 0,
    }


def _phase_samples(driver, asset_id: str, phase_id: str) -> list[dict[str, Any]]:
    """Up to 5 representative nodes a phase wrote, with their primary properties."""
    with driver.session(database=database_name()) as s:
        rs = s.run(
            """
            MATCH (n {asset_id: $aid, created_in_phase: $p})
            RETURN labels(n) AS labels, n.value AS value,
                   n.title AS title, n.description AS description
            ORDER BY n.value LIMIT 5
            """,
            aid=asset_id, p=phase_id,
        )
        return [dict(r) for r in rs]


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def archive(*, archetype: str, workdir: Path, version: str,
            asset_id: str | None = None,
            sparengine_version: str | None = None) -> Path:
    """Snapshot the workdir into benchmarks/<version>/<archetype>/ and MERGE
    :BenchmarkRun + per-phase :PhaseScorecard nodes.

    Returns the archive path.
    """
    workdir = workdir.resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"workdir not found: {workdir}")

    archive_dir = _BENCHMARKS / version / archetype
    archive_dir.mkdir(parents=True, exist_ok=True)
    per_phase_dir = archive_dir / "per_phase"
    per_phase_dir.mkdir(exist_ok=True)

    # Resolve asset_id
    qsc_path = workdir / "quality_scorecard.json"
    qsc: dict[str, Any] = {}
    if qsc_path.is_file():
        try:
            qsc = json.loads(qsc_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    asset_id = asset_id or qsc.get("asset_id")
    if not asset_id:
        # Fallback: probe the workdir folder name UUID (if it matches the
        # csvs/<uuid>-<label> pattern)
        asset_id = workdir.name.rsplit("-", 1)[0] if "-" in workdir.name else workdir.name
    sparengine_version = (
        sparengine_version or qsc.get("sparengine_version")
        or os.environ.get("SPARENGINE_VERSION", "phase10-dev")
    )

    git = _git_context()

    driver = connect()
    try:
        # 1. Counts
        counts = _read_counts(driver, asset_id)
        (archive_dir / "counts.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")

        # 2. Stratified finding sample
        samples = _sample_findings_stratified(driver, asset_id)
        (archive_dir / "finding_samples.json").write_text(
            json.dumps(samples, indent=2), encoding="utf-8")

        # 3. Copy supporting files from the workdir if present
        for fname in ("progress.log", "decisions.log", "quality_scorecard.json",
                      "llm_judgement.json", "graph_export.json", "restore.cypher"):
            src = workdir / fname
            if src.is_file():
                shutil.copyfile(src, archive_dir / fname)

        # 4. metadata.json
        metadata = {
            "version":          version,
            "archetype":        archetype,
            "sparengine_version": sparengine_version,
            "asset_id":         asset_id,
            "git":              git,
            "captured_at":      time.strftime("%Y-%m-%dT%H:%M:%S%z") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "workdir":          str(workdir),
        }
        (archive_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        # 5. Per-phase artifacts
        phase_briefs = _enumerate_phase_briefs()
        per_phase_results: list[dict[str, Any]] = []
        for brief in phase_briefs:
            pid       = _phase_id_from_brief(brief)
            pdir      = per_phase_dir / pid
            pdir.mkdir(exist_ok=True)
            metrics   = _phase_metrics(driver, asset_id, pid)
            samples_p = _phase_samples(driver, asset_id, pid)
            (pdir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            (pdir / "samples.json").write_text(json.dumps(samples_p, indent=2), encoding="utf-8")
            per_phase_results.append({
                "phase_id":          pid,
                "phase_brief_path":  str(brief.relative_to(_HERE.parent.parent)),
                "phase_brief_sha":   _sha256(brief),
                **metrics,
            })

        # 6. MERGE :BenchmarkRun + :PhaseScorecard
        with driver.session(database=database_name()) as s:
            with s.begin_transaction() as tx:
                write_benchmark_run(
                    tx,
                    archetype=archetype, version=version,
                    git_sha=git["git_sha"], commit_msg=git["commit_msg"],
                    sparengine_version=sparengine_version,
                    archive_path=str(archive_dir.relative_to(_HERE.parent.parent.parent)),
                    total_pages=counts.get("node:Page", 0),
                    total_documents=counts.get("node:Document", 0),
                    total_components=counts.get("node:Component", 0),
                    total_events=counts.get("node:Event", 0),
                    total_findings=counts.get("node:Finding", 0),
                    total_form1=counts.get("node:Form1", 0),
                    total_stamps=counts.get("node:Stamp", 0),
                    fact_orphan_count=qsc.get("fact_orphan_count", 0),
                    mechanical_overall=qsc.get("mechanical_overall"),
                    llm_mean=qsc.get("llm_mean"),
                    llm_p20=qsc.get("llm_p20"),
                )
                for ph in per_phase_results:
                    write_phase_scorecard(
                        tx,
                        archetype=archetype, version=version,
                        phase_id=ph["phase_id"],
                        phase_brief_path=ph["phase_brief_path"],
                        phase_brief_sha=ph["phase_brief_sha"],
                        phase_nodes_written=ph["phase_nodes_written"],
                        phase_findings_written=ph["phase_findings_written"],
                    )
                tx.commit()

        # 7. Update benchmarks/index.json
        _bump_index(version, archetype, archive_dir, git)

    finally:
        driver.close()

    print(f"archived → {archive_dir}", file=sys.stderr)
    return archive_dir


def _bump_index(version: str, archetype: str, archive_dir: Path, git: dict[str, str]) -> None:
    """Maintain benchmarks/index.json — a small registry of archived runs."""
    index_path = _BENCHMARKS / "index.json"
    if index_path.is_file():
        try:
            index: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = {"versions": {}}
    else:
        index = {"versions": {}}

    versions = index.setdefault("versions", {})
    entry = versions.setdefault(version, {"archetypes": {}, "git": git})
    entry["archetypes"][archetype] = {
        "path": str(archive_dir.relative_to(_BENCHMARKS)),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--archetype", required=True,
                   help="helicopter_full | engine_only | ocr_variance | …")
    p.add_argument("--workdir",  required=True, type=Path)
    p.add_argument("--version",  default=os.environ.get("SPARENGINE_VERSION", "vDEV"))
    p.add_argument("--asset-id", default=None)
    p.add_argument("--sparengine-version", default=None)
    args = p.parse_args()

    archive(
        archetype=args.archetype, workdir=args.workdir,
        version=args.version, asset_id=args.asset_id,
        sparengine_version=args.sparengine_version,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
