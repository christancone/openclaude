"""Cross-version analyser — Layer C agent spawner.

Prepares an `_analysis/` workdir with the diffs / counts / samples / cited
evidence pages, then spawns the same agent CLI as production (per
``AGENT_CLI`` env var) pointed at ``phases/briefs/phase_analyse.md``. The
agent writes ``verdict.json`` + ``verdict.md`` into the workdir; we MERGE
the verdicts into ``:BenchmarkRun`` and per-phase ``:PhaseScorecard`` nodes.

CLI
---

::

    python -m tools.analyse_change --from <old_version> --to <new_version>
    python -m tools.analyse_change --previous            # most recent two
    python -m tools.analyse_change --from v3 --to v4 --phase phase4_components
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

from graph_dal import connect, database_name                              # noqa: E402
from graph_dal.benchmark import (                                         # noqa: E402
    merge_benchmark_verdict, merge_phase_verdict,
)


_REPO_ROOT  = _HERE.parent.parent.parent
_BENCHMARKS = _HERE.parent.parent / "benchmarks"
_BRIEFS_DIR = _HERE.parent.parent / "phases" / "briefs"
_GRAPH_DAL  = _HERE.parent.parent / "graph_dal"


# ---------------------------------------------------------------------------
# Version selection
# ---------------------------------------------------------------------------

def _list_versions() -> list[str]:
    """Versions present under benchmarks/, sorted alphabetically.

    Version strings are designed to sort naturally (e.g. "v4-2026-05-07-9c..."
    sorts after "v3-2026-05-06-..."), but the test fixture uses arbitrary
    names so we don't enforce a format.
    """
    if not _BENCHMARKS.is_dir():
        return []
    return sorted(p.name for p in _BENCHMARKS.iterdir() if p.is_dir() and p.name != "_analysis")


def _resolve_versions(args: argparse.Namespace) -> tuple[str, str]:
    if args.previous:
        versions = _list_versions()
        if len(versions) < 2:
            raise SystemExit(f"need at least 2 archived versions, found {versions}")
        return versions[-2], versions[-1]
    if not (args.from_v and args.to_v):
        raise SystemExit("specify --from <v> --to <v> OR --previous")
    return args.from_v, args.to_v


def _archetypes_present(version: str) -> list[str]:
    """Archetypes archived under benchmarks/<version>/."""
    root = _BENCHMARKS / version
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and p.name != "_analysis")


# ---------------------------------------------------------------------------
# Context bundle preparation
# ---------------------------------------------------------------------------

def _git_diff(old_sha: str, new_sha: str, paths: list[str]) -> str:
    """git diff filtered to the given paths. Empty string if anything fails."""
    if not (old_sha and new_sha):
        return ""
    try:
        out = subprocess.run(
            ["git", "diff", f"{old_sha}..{new_sha}", "--"] + paths,
            cwd=_REPO_ROOT, text=True, capture_output=True, timeout=30,
        )
        return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _read_metadata(version: str) -> dict[str, Any]:
    """Aggregate metadata: pull ANY archetype's metadata.json (they share
    the version-level fields like git sha, sparengine_version, captured_at)."""
    archs = _archetypes_present(version)
    if not archs:
        return {}
    p = _BENCHMARKS / version / archs[0] / "metadata.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _build_context(old_version: str, new_version: str,
                   only_phase: str | None) -> Path:
    """Materialise the analyser's _analysis/ workdir under benchmarks/<new>."""
    analysis_dir = _BENCHMARKS / new_version / "_analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "per_phase").mkdir(exist_ok=True)

    old_meta = _read_metadata(old_version)
    new_meta = _read_metadata(new_version)
    old_sha  = (old_meta.get("git") or {}).get("git_full_sha", "")
    new_sha  = (new_meta.get("git") or {}).get("git_full_sha", "")

    # ----- code_diff.patch -------------------------------------------------
    diff = _git_diff(old_sha, new_sha, [
        "sparengine-export/graph_dal/",
        "sparengine-export/phases/briefs/",
        "sparengine-export/phases/cypher/",
    ])
    (analysis_dir / "code_diff.patch").write_text(diff or "(no diff)\n", encoding="utf-8")

    # ----- context.md ------------------------------------------------------
    archs_old = set(_archetypes_present(old_version))
    archs_new = set(_archetypes_present(new_version))
    archs_both = sorted(archs_old & archs_new)

    lines: list[str] = []
    lines.append(f"# Cross-version analysis: {old_version} → {new_version}\n")
    lines.append(f"OLD git: `{old_sha[:8]}` — {(old_meta.get('git') or {}).get('commit_msg', '')}")
    lines.append(f"NEW git: `{new_sha[:8]}` — {(new_meta.get('git') or {}).get('commit_msg', '')}")
    lines.append("")
    lines.append("## Per-archetype count + score deltas\n")

    for arch in archs_both:
        old_counts = _read_json(_BENCHMARKS / old_version / arch / "counts.json", {})
        new_counts = _read_json(_BENCHMARKS / new_version / arch / "counts.json", {})
        old_qsc    = _read_json(_BENCHMARKS / old_version / arch / "quality_scorecard.json", {})
        new_qsc    = _read_json(_BENCHMARKS / new_version / arch / "quality_scorecard.json", {})

        lines.append(f"### {arch}")
        for key in ("node:Page", "node:Document", "node:Component",
                    "node:Event", "node:Finding", "node:Form1", "node:Stamp"):
            o, n = old_counts.get(key, 0), new_counts.get(key, 0)
            if o or n:
                lines.append(f"  {key:30s}  {o:>6}  →  {n:>6}  Δ {n - o:+d}")
        for key in ("mechanical_overall", "llm_mean", "nine_discipline_pct",
                    "fact_orphan_count"):
            o, n = old_qsc.get(key), new_qsc.get(key)
            if o is not None or n is not None:
                lines.append(f"  {key:30s}  {o!s:>6}  →  {n!s:>6}")
        lines.append("")

    (analysis_dir / "context.md").write_text("\n".join(lines), encoding="utf-8")

    # ----- evidence_pages.md ----------------------------------------------
    # Aggregate the cited page excerpts across the new version's finding
    # samples so the agent has the evidence in one place.
    ev_lines: list[str] = ["# Cited evidence — verbatim page excerpts (new version)\n"]
    for arch in archs_both:
        samples = _read_json(_BENCHMARKS / new_version / arch / "finding_samples.json", [])
        if not samples:
            continue
        ev_lines.append(f"## {arch}")
        for f in samples[:10]:                       # cap so file stays small
            ev_lines.append(f"- **{f.get('value')}** — {f.get('category')}: "
                            f"{(f.get('title') or '')[:80]}")
            for e in (f.get("evidence") or [])[:2]:
                ev_lines.append(
                    f"    page `{e.get('page')}` (file `{e.get('file')}`): "
                    f"\"{(e.get('quote') or '')[:200]}\""
                )
        ev_lines.append("")
    (analysis_dir / "evidence_pages.md").write_text("\n".join(ev_lines), encoding="utf-8")

    # ----- csv_samples.md (best-effort) ------------------------------------
    (analysis_dir / "csv_samples.md").write_text(
        "# CSV samples\n\n(skipped — fixture CSVs aren't archived in benchmarks/)\n",
        encoding="utf-8",
    )

    # ----- per_phase bundles -----------------------------------------------
    # For each phase, copy old/new metrics + samples + brief.diff
    phase_briefs = sorted(p.stem for p in _BRIEFS_DIR.glob("phase*_*.md"))
    for phase_id in phase_briefs:
        if only_phase and phase_id != only_phase:
            continue
        pdir = analysis_dir / "per_phase" / phase_id
        pdir.mkdir(parents=True, exist_ok=True)
        for arch in archs_both:
            old_pp = _BENCHMARKS / old_version / arch / "per_phase" / phase_id
            new_pp = _BENCHMARKS / new_version / arch / "per_phase" / phase_id
            for fname in ("metrics.json", "samples.json"):
                # Old + new for the agent to compare
                if (old_pp / fname).is_file():
                    shutil.copyfile(old_pp / fname, pdir / f"{arch}_{fname.replace('.json', '_old.json')}")
                if (new_pp / fname).is_file():
                    shutil.copyfile(new_pp / fname, pdir / f"{arch}_{fname.replace('.json', '_new.json')}")
        # brief.diff for THIS phase
        brief_diff = _git_diff(old_sha, new_sha, [
            f"sparengine-export/phases/briefs/{phase_id}.md",
        ])
        (pdir / "brief.diff").write_text(brief_diff or "(brief unchanged)\n", encoding="utf-8")

    # ----- Copy the analyser brief into the workdir so the agent can Read it
    shutil.copyfile(_BRIEFS_DIR / "phase_analyse.md", analysis_dir / "BRIEF.md")

    return analysis_dir


def _read_json(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


# ---------------------------------------------------------------------------
# Agent spawn
# ---------------------------------------------------------------------------

def _agent_cmd_and_flags() -> tuple[list[str], list[str], bool]:
    """Return (interpreter+entry, print/stream flags, use_shell) for AGENT_CLI.

    Mirrors how server.mjs builds the spawn command:
      - openclaude: `node dist/cli.mjs --print --dangerously-skip-permissions <prompt>`
      - claude:     `claude -p --dangerously-skip-permissions <prompt>`

    The prompt is the LAST positional arg. `cwd` is set as a subprocess
    option, NOT passed as `--cwd`.
    """
    cli = os.environ.get("AGENT_CLI", "openclaude").lower()
    if cli == "openclaude":
        cli_mjs = _REPO_ROOT / "dist" / "cli.mjs"
        if not cli_mjs.is_file():
            raise FileNotFoundError(f"AGENT_CLI=openclaude but {cli_mjs} not found")
        # Use the same node binary that runs the orchestrator.
        return (["node", str(cli_mjs)],
                ["--print", "--dangerously-skip-permissions"],
                False)
    elif cli == "claude":
        return (["claude"],
                ["-p", "--dangerously-skip-permissions"],
                os.name == "nt")           # shell=True on Windows for npm-shim'd `claude.cmd`
    raise ValueError(f"unknown AGENT_CLI: {cli}")


def _spawn_analyser(analysis_dir: Path) -> subprocess.CompletedProcess:
    """Spawn the agent with the analyser brief, with cwd set to analysis_dir.

    The agent reads `_analysis/BRIEF.md` (a copy of phase_analyse.md) plus
    everything else under `_analysis/`, and writes verdict.json + verdict.md
    into the analysis_dir.
    """
    cmd_prefix, flags, use_shell = _agent_cmd_and_flags()
    prompt = (
        "Read `BRIEF.md` first. Then read `context.md`, `code_diff.patch`, "
        "`evidence_pages.md`, and the `per_phase/<phase_id>/` bundles. "
        "Write your verdict to `verdict.json` (machine-readable) and "
        "`verdict.md` (human-readable summary). Stop when both files exist."
    )
    cmd = cmd_prefix + flags + [prompt]
    return subprocess.run(
        cmd,
        cwd=str(analysis_dir),
        text=True, capture_output=True, timeout=600,
        check=False, shell=use_shell,
        stdin=subprocess.DEVNULL,                  # avoid the "no stdin in 3s" warning
    )


# ---------------------------------------------------------------------------
# Verdict merge
# ---------------------------------------------------------------------------

def _merge_verdict_into_neo4j(*, new_version: str, analysis_dir: Path) -> None:
    verdict_path = analysis_dir / "verdict.json"
    if not verdict_path.is_file():
        print(f"WARNING: agent did not produce verdict.json", file=sys.stderr)
        return
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: verdict.json unparseable: {e}", file=sys.stderr)
        return

    # Rewrite the analysis_path to be relative to the repo root for the DAL.
    analysis_rel = str(verdict_path.relative_to(_HERE.parent.parent.parent))

    overall = verdict.get("overall_verdict") or "no_significant_effect"
    confidence = int(verdict.get("confidence") or 3)
    confidence = min(max(confidence, 1), 5)

    archs = _archetypes_present(new_version)
    driver = connect()
    try:
        with driver.session(database=database_name()) as s:
            with s.begin_transaction() as tx:
                # Overall verdict applies to every archetype's :BenchmarkRun
                # (the agent's verdict is asset-cross-cutting).
                for arch in archs:
                    try:
                        merge_benchmark_verdict(
                            tx, archetype=arch, version=new_version,
                            verdict=overall, confidence=confidence,
                            analysis_path=analysis_rel,
                        )
                    except RuntimeError as e:
                        print(f"  skip {arch}: {e}", file=sys.stderr)

                # Per-phase verdicts
                per_phase = verdict.get("per_phase") or {}
                for phase_id, ph in per_phase.items():
                    if not isinstance(ph, dict):
                        continue
                    p_verdict    = ph.get("verdict") or "no_significant_effect"
                    p_confidence = int(ph.get("confidence") or 3)
                    p_confidence = min(max(p_confidence, 1), 5)
                    p_reasoning  = (ph.get("reasoning") or "")[:1000]
                    for arch in archs:
                        try:
                            merge_phase_verdict(
                                tx, archetype=arch, version=new_version,
                                phase_id=phase_id,
                                verdict=p_verdict, confidence=p_confidence,
                                reasoning=p_reasoning,
                            )
                        except RuntimeError:
                            pass         # PhaseScorecard might not exist for this archetype
                tx.commit()
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--from", dest="from_v", help="Old version name under benchmarks/")
    p.add_argument("--to",   dest="to_v",   help="New version name under benchmarks/")
    p.add_argument("--previous", action="store_true",
                   help="Use the most-recently archived two versions")
    p.add_argument("--phase", default=None,
                   help="Only build context for this single phase_id")
    p.add_argument("--no-spawn", action="store_true",
                   help="Build context but don't spawn the agent (for tests)")
    args = p.parse_args()

    old_v, new_v = _resolve_versions(args)
    print(f"comparing  {old_v}  →  {new_v}", file=sys.stderr)

    analysis_dir = _build_context(old_v, new_v, args.phase)
    print(f"context  →  {analysis_dir}", file=sys.stderr)

    if args.no_spawn:
        print("skipping agent spawn (--no-spawn)", file=sys.stderr)
        return 0

    result = _spawn_analyser(analysis_dir)
    if result.returncode != 0:
        sys.stderr.write(result.stdout or "")
        sys.stderr.write(result.stderr or "")
        print(f"agent exited with {result.returncode}", file=sys.stderr)

    _merge_verdict_into_neo4j(new_version=new_v, analysis_dir=analysis_dir)

    # Print a 5-line summary
    verdict_path = analysis_dir / "verdict.json"
    if verdict_path.is_file():
        try:
            v = json.loads(verdict_path.read_text(encoding="utf-8"))
            print(f"verdict: {v.get('overall_verdict')} "
                  f"(confidence {v.get('confidence')})", file=sys.stderr)
            print(f"action:  {v.get('recommended_action')}", file=sys.stderr)
            print(f"see:     {analysis_dir / 'verdict.md'}", file=sys.stderr)
        except (json.JSONDecodeError, OSError):
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
