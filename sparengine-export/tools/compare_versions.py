"""Side-by-side count + score comparison between two archived versions.

No agent, no LLM — fast (< 1s). Reads the JSON summaries under
``benchmarks/<version>/<archetype>/`` and prints a table.

CLI
---

::

    python -m tools.compare_versions <old_version> <new_version>
    python -m tools.compare_versions v3 v4 --phase phase4_components
    python -m tools.compare_versions v3 v4 --archetype helicopter_full
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
_BENCHMARKS = _HERE.parent.parent / "benchmarks"


def _read_json(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _format_delta(old: Any, new: Any) -> str:
    """Render a delta column. Numeric → diff with sign; other → '→'."""
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        d = new - old
        pct = ""
        if old:
            pct = f"  ({d / old * 100:+.1f}%)"
        return f"{d:+g}{pct}"
    return "→"


def _print_archetype_table(old_version: str, new_version: str, archetype: str) -> None:
    old_dir = _BENCHMARKS / old_version / archetype
    new_dir = _BENCHMARKS / new_version / archetype

    old_counts = _read_json(old_dir / "counts.json")
    new_counts = _read_json(new_dir / "counts.json")
    old_qsc    = _read_json(old_dir / "quality_scorecard.json")
    new_qsc    = _read_json(new_dir / "quality_scorecard.json")

    print(f"\n## {archetype}")
    print(f"{'metric':40s}  {'old':>10s}  {'new':>10s}  {'delta'}")
    print("-" * 80)

    # Count metrics
    all_keys = sorted(set(old_counts) | set(new_counts))
    for key in all_keys:
        if not key.startswith(("node:", "edge:")):
            continue
        o, n = old_counts.get(key, 0), new_counts.get(key, 0)
        if o or n:
            print(f"{key:40s}  {o:>10}  {n:>10}  {_format_delta(o, n)}")

    # Score metrics
    score_keys = (
        "mechanical_overall", "citation_present_pct",
        "description_length_ok_pct", "decisions_log_parity",
        "nine_discipline_pct", "severity_sanity_ok",
        "dal_bypass_count", "fact_orphan_count",
        "llm_mean", "llm_median", "llm_p20",
        "total_findings", "level_1_count", "level_2_count", "level_3_count",
    )
    for key in score_keys:
        o, n = old_qsc.get(key), new_qsc.get(key)
        if o is None and n is None:
            continue
        # Stringify None as "—"
        os_, ns_ = ("—" if o is None else o), ("—" if n is None else n)
        print(f"{key:40s}  {os_!s:>10}  {ns_!s:>10}  {_format_delta(o, n)}")


def _print_phase_table(old_version: str, new_version: str, phase_id: str) -> None:
    print(f"\n## phase: {phase_id}")
    print(f"{'archetype':25s}  {'metric':25s}  {'old':>8s}  {'new':>8s}  delta")
    print("-" * 80)

    archetypes = sorted(
        p.name for p in (_BENCHMARKS / old_version).iterdir()
        if p.is_dir() and (p / "per_phase" / phase_id).is_dir()
    ) if (_BENCHMARKS / old_version).is_dir() else []

    for arch in archetypes:
        old_m = _read_json(_BENCHMARKS / old_version / arch / "per_phase" / phase_id / "metrics.json")
        new_m = _read_json(_BENCHMARKS / new_version / arch / "per_phase" / phase_id / "metrics.json")
        for key in ("phase_nodes_written", "phase_edges_written",
                    "phase_findings_written", "mechanical_overall", "llm_mean"):
            o, n = old_m.get(key), new_m.get(key)
            if o is None and n is None:
                continue
            os_, ns_ = ("—" if o is None else o), ("—" if n is None else n)
            print(f"{arch:25s}  {key:25s}  {os_!s:>8}  {ns_!s:>8}  {_format_delta(o, n)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("old_version")
    p.add_argument("new_version")
    p.add_argument("--archetype", default=None,
                   help="Compare just this archetype (default: all)")
    p.add_argument("--phase", default=None,
                   help="Compare just this phase across all archetypes")
    args = p.parse_args()

    if not (_BENCHMARKS / args.old_version).is_dir():
        print(f"old version not found: {_BENCHMARKS / args.old_version}", file=sys.stderr)
        return 2
    if not (_BENCHMARKS / args.new_version).is_dir():
        print(f"new version not found: {_BENCHMARKS / args.new_version}", file=sys.stderr)
        return 2

    print(f"# Sparengine cross-version comparison")
    print(f"#   old:  {args.old_version}")
    print(f"#   new:  {args.new_version}")

    if args.phase:
        _print_phase_table(args.old_version, args.new_version, args.phase)
        return 0

    archetypes = (
        [args.archetype] if args.archetype
        else sorted(p.name for p in (_BENCHMARKS / args.new_version).iterdir() if p.is_dir())
    )
    for arch in archetypes:
        if arch == "_analysis":
            continue
        _print_archetype_table(args.old_version, args.new_version, arch)

    return 0


if __name__ == "__main__":
    sys.exit(main())
