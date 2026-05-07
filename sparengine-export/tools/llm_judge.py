"""Layer B (LLM-as-judge) — agent-driven scoring of findings on a real-asset run.

Picks N findings stratified by severity (default 2 L1 / 3 L2 / 5 L3 = 10),
prepares a small prompt for each, spawns the same agent CLI as production
(via AGENT_CLI), and asks "is this finding well-founded? 1..5". Aggregates
mean / median / p20 and merges them into the existing :QualityScorecard.

This complements the mechanical rubric in `quality_scorecard.py`: the
mechanical rubric checks shape (citations present, descriptions long enough,
…); the LLM-as-judge checks substance (does the finding actually make sense
given its cited evidence?).

Cost
----

Reuses the production agent CLI (Gemini via Claude Code, or Claude
Pro/Max OAuth, or OpenClaude) — same auth, same budget. ~10 small agent
calls per run, ~$0.05 at Gemini pricing OR free under Pro/Max.

Run with --no-llm to skip the LLM call entirely (writes
``llm_*: null`` onto the scorecard so trend queries can ``WHERE … IS NOT
NULL``). Useful when the budget is exhausted or the agent CLI is unavailable.

CLI
---

::

    python -m tools.llm_judge --workdir /app/csvs/<uuid>-<label> --sample 10
    python -m tools.llm_judge --workdir /app/csvs/<uuid>-<label> --no-llm
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

from graph_dal import connect, database_name                              # noqa: E402
from graph_dal.quality import write_quality_scorecard                     # noqa: E402


_REPO_ROOT = _HERE.parent.parent.parent                                   # workspace root


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------

def _stratified_sample(driver, asset_id: str, *,
                       n_l1: int, n_l2: int, n_l3: int) -> list[dict[str, Any]]:
    """Pick `n_l1` LEVEL_1, `n_l2` LEVEL_2, `n_l3` LEVEL_3 findings.

    Includes their cited :Page text excerpt (~500 chars) so the agent has
    the evidence in front of it without a separate Read tool call.
    """
    out: list[dict[str, Any]] = []
    for sev, n in [("level_1", n_l1), ("level_2", n_l2), ("level_3", n_l3)]:
        with driver.session(database=database_name()) as s:
            rs = s.run(
                """
                MATCH (f:Finding {asset_id: $aid, severity: $sev})
                OPTIONAL MATCH (f)-[e:EVIDENCED_BY]->(p:Page)
                RETURN f.value AS value, f.category AS category,
                       f.title AS title, f.description AS description,
                       collect({page: p.value, file: p.file_name,
                                quote: e.quote,
                                excerpt: substring(coalesce(p.text, ''), 0, 500)})
                       AS evidence
                ORDER BY f.value
                LIMIT $n
                """,
                aid=asset_id, sev=sev, n=n,
            )
            for r in rs:
                out.append(dict(r))
    return out


# ---------------------------------------------------------------------------
# Agent spawn (mirrors server.mjs's runAgent)
# ---------------------------------------------------------------------------

def _agent_cli_path() -> tuple[list[str], str]:
    """Return ([interpreter, …], description) tuple for the active AGENT_CLI."""
    cli = os.environ.get("AGENT_CLI", "openclaude").lower()
    if cli == "openclaude":
        cli_mjs = _REPO_ROOT / "dist" / "cli.mjs"
        if not cli_mjs.is_file():
            raise FileNotFoundError(
                f"AGENT_CLI=openclaude but {cli_mjs} not found. "
                f"Run `bun run build` from the repo root."
            )
        return (["node", str(cli_mjs)], "openclaude")
    elif cli == "claude":
        # The Claude Code CLI is npm-installed at /usr/local/bin/claude.
        return (["claude"], "claude")
    raise ValueError(f"Unknown AGENT_CLI: {cli!r}")


def _judge_one_finding(finding: dict[str, Any]) -> tuple[int | None, float]:
    """Spawn the agent for a single finding, ask for a 1..5 score.

    Returns (score, cost_usd_estimate). Score is None if parsing fails.
    """
    prompt = _make_judge_prompt(finding)
    cmd, name = _agent_cli_path()
    # Use a temp workdir so the agent has somewhere to write.
    with tempfile.TemporaryDirectory(prefix="llm_judge_") as td:
        # Stream-JSON input lets us pipe a single user message in.
        full_cmd = cmd + ["--print", "--max-turns", "3", "--cwd", td]
        try:
            result = subprocess.run(
                full_cmd,
                input=prompt,
                text=True, capture_output=True, timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (None, 0.0)
        out = (result.stdout or "") + "\n" + (result.stderr or "")
        # Find a JSON object with {"score": <int>, …} in the output.
        score = _parse_score_from_agent_output(out)
        # Crude cost: 0.005 USD per agent turn at Gemini-pro pricing — tweak when known.
        return (score, 0.005)


def _make_judge_prompt(finding: dict[str, Any]) -> str:
    """Compact prompt; the agent answers in one JSON line."""
    ev = finding.get("evidence") or []
    ev_text = "\n".join(
        f"  - {e.get('file', '?')} p.{e.get('page', '?')}: "
        f"\"{(e.get('quote') or '')[:120]}\" (excerpt: \"{(e.get('excerpt') or '')[:200]}\")"
        for e in ev[:3]
    ) or "  (no evidence pages linked)"
    return (
        "You are a senior aviation records auditor reviewing one finding from a "
        "Sparengine audit run. Score it on a 1..5 scale:\n"
        "  5 = excellent (specific, cited, actionable)\n"
        "  4 = good\n"
        "  3 = acceptable\n"
        "  2 = weak (vague or under-cited)\n"
        "  1 = poor (no actionable signal)\n"
        "\n"
        "FINDING:\n"
        f"  category: {finding.get('category')}\n"
        f"  title:    {finding.get('title')}\n"
        f"  description (verbatim):\n    {(finding.get('description') or '').strip()}\n"
        "\n"
        "CITED EVIDENCE:\n"
        f"{ev_text}\n"
        "\n"
        "Answer in ONE LINE of JSON only, like: "
        '{"score": 4, "reason": "good citation, specific recommended_action"}'
    )


def _parse_score_from_agent_output(s: str) -> int | None:
    """Find a {"score": int, ...} object in arbitrary agent output."""
    import re as _re
    for m in _re.finditer(r'\{[^{}]*"score"\s*:\s*([1-5])[^{}]*\}', s):
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def judge(*, workdir: Path, sample: int, no_llm: bool,
          asset_id: str | None = None,
          sparengine_version: str | None = None,
          run_id: str | None = None) -> dict[str, Any]:
    """Run the LLM-as-judge over a stratified sample. Persist to JSON + Neo4j."""
    workdir = workdir.resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"workdir not found: {workdir}")

    # Resolve asset_id from quality_scorecard.json if available
    qsc_path = workdir / "quality_scorecard.json"
    qsc: dict[str, Any] = {}
    if qsc_path.is_file():
        try:
            qsc = json.loads(qsc_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            qsc = {}
    asset_id = asset_id or qsc.get("asset_id")
    if not asset_id:
        # Fall back to folder name
        asset_id = workdir.name.split("-", 5)[:5] and "-".join(workdir.name.split("-", 5)[:5])
    sparengine_version = sparengine_version or qsc.get("sparengine_version") or "phase10-dev"
    run_id = run_id or qsc.get("run_id") or f"run-{int(time.time())}"

    # Split sample size 2:3:5 (clamped to the requested total).
    n_l1 = max(1, sample * 2 // 10)
    n_l3 = max(1, sample * 5 // 10)
    n_l2 = max(0, sample - n_l1 - n_l3)

    driver = connect()
    try:
        sampled = _stratified_sample(driver, asset_id, n_l1=n_l1, n_l2=n_l2, n_l3=n_l3)
        if no_llm or not sampled:
            judgement = {
                "asset_id": asset_id, "run_id": run_id,
                "no_llm": True if no_llm else False,
                "reason": "skipped (--no-llm)" if no_llm else "no findings to sample",
                "sample_size": 0, "scores": [],
                "mean": None, "median": None, "p20": None, "total_cost_usd": 0.0,
            }
        else:
            scores: list[int] = []
            cost = 0.0
            details: list[dict[str, Any]] = []
            for f in sampled:
                score, c = _judge_one_finding(f)
                cost += c
                if score is not None:
                    scores.append(score)
                details.append({
                    "value":    f.get("value"),
                    "category": f.get("category"),
                    "score":    score,
                })
            scores_sorted = sorted(scores)
            judgement = {
                "asset_id": asset_id, "run_id": run_id,
                "sample_size": len(scores),
                "scores": details,
                "mean":   mean(scores) if scores else None,
                "median": median(scores) if scores else None,
                "p20":    scores_sorted[max(0, len(scores) // 5 - 1)] if scores else None,
                "total_cost_usd": round(cost, 4),
            }

        # Persist
        (workdir / "llm_judgement.json").write_text(json.dumps(judgement, indent=2), encoding="utf-8")

        # Merge into the existing :QualityScorecard. We re-call write_quality_scorecard
        # with the LLM fields populated; the writer's ON MATCH clause overwrites.
        # Other fields are read back from the scorecard JSON if present.
        with driver.session(database=database_name()) as s:
            with s.begin_transaction() as tx:
                write_quality_scorecard(
                    tx, asset_id=asset_id, run_id=run_id,
                    sparengine_version=sparengine_version,
                    mechanical_overall=qsc.get("mechanical_overall", 0),
                    citation_present_pct=qsc.get("citation_present_pct", 0),
                    description_length_ok_pct=qsc.get("description_length_ok_pct", 0),
                    decisions_log_parity=qsc.get("decisions_log_parity", 0),
                    nine_discipline_pct=qsc.get("nine_discipline_pct", 0),
                    severity_sanity_ok=qsc.get("severity_sanity_ok", 0),
                    dal_bypass_count=qsc.get("dal_bypass_count", 0),
                    fact_orphan_count=qsc.get("fact_orphan_count", 0),
                    llm_sample_size=judgement["sample_size"],
                    llm_mean=judgement["mean"],
                    llm_median=judgement["median"],
                    llm_p20=judgement["p20"],
                    llm_total_cost_usd=judgement["total_cost_usd"],
                    total_findings=qsc.get("total_findings", 0),
                    level_1_count=qsc.get("level_1_count", 0),
                    level_2_count=qsc.get("level_2_count", 0),
                    level_3_count=qsc.get("level_3_count", 0),
                )
                tx.commit()
    finally:
        driver.close()

    return judgement


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--sample", type=int, default=10,
                   help="Total findings to score (default 10)")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip LLM call; write null scores into the scorecard")
    p.add_argument("--asset-id", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--sparengine-version", default=None)
    args = p.parse_args()

    out = judge(
        workdir=args.workdir, sample=args.sample, no_llm=args.no_llm,
        asset_id=args.asset_id, run_id=args.run_id,
        sparengine_version=args.sparengine_version,
    )
    summary = (
        f"llm_mean={out['mean']} sample={out['sample_size']} "
        f"cost_usd=${out['total_cost_usd']:.4f}"
    )
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
