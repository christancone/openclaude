# Sparengine QA — Operator's guide

Three layers, each independently runnable. Every layer writes its results
both to disk (for human inspection) and to Neo4j (for trend traversal).

```
Layer A — pytest suite               (deterministic, ~3s)
Layer B — per-real-asset scorecard   (mechanical + LLM-as-judge, ~30s)
Layer C — cross-version benchmark    (snapshot + agent-driven verdict, ~1min)
```

Everything below assumes the stack is up:

```powershell
docker compose up -d --build
```

> **Note on `make`** — the Makefile is a thin convenience wrapper. If `make`
> isn't on your container (older image) or you want to run things from a host
> shell that doesn't have `make`, every target has a direct equivalent shown
> in parentheses next to it.

---

## Layer A — pytest

The fast feedback loop. Lives in `sparengine-export/tests/`.

```bash
# Full pyramid (~3 sec)
docker compose exec sparengine make -C /app/sparengine-export test

# Pre-commit tier — unit + lint, no Neo4j touch
docker compose exec sparengine make -C /app/sparengine-export test-fast

# By layer
docker compose exec sparengine make -C /app/sparengine-export test-unit
docker compose exec sparengine make -C /app/sparengine-export test-lint
docker compose exec sparengine make -C /app/sparengine-export test-integration
```

What the layers cover:

- **unit** (`tests/unit/`) — pure-Python invariants: phase-tag context
  manager, benchmark sentinel format, verdict validation, synthetic CSV
  shape. No Neo4j; <1 second.
- **lint** (`tests/lint/`) — file-shape rules: phases/ subdirectory layout,
  no leftover `asset_graph.html` / `phase_viz` / `import sqlite3` /
  `graph.db` references in active code, no bare `phases/schema.cypher`
  references outside `cypher/`. Catches reorganisation drift.
- **integration** (`tests/integration/`) — DAL writers round-trip against
  the real Neo4j: `:QualityScorecard`, `:BenchmarkRun`, `:PhaseScorecard`,
  the `[:NEXT]` chains and `[:PART_OF]` edges. Each test gets a fresh
  `test-<uuid>` asset_id; cleanup is autouse.

To install pre-commit (one-time):

```bash
pip install pre-commit
pre-commit install        # installs into .git/hooks/
```

After install, every `git commit` runs the fast tier. The hook lives in
`tools/precommit_fast.sh` and runs the tests inside the running container —
fails loud if compose isn't up (instead of silently skipping).

---

## Layer B — per-real-asset scorecard

Runs after every real-asset agent run. Two tools, both write into the
asset's workdir AND merge a `:QualityScorecard` node in Neo4j.

### Mechanical rubric — fast, deterministic

```bash
docker compose exec sparengine make -C /app/sparengine-export \
    scorecard ASSET=/app/csvs/<uuid>-<label>
```

Computes:

| metric                       | what it checks                                |
|------------------------------|-----------------------------------------------|
| `citation_present_pct`       | `(file: …, page: …)` in finding descriptions  |
| `description_length_ok_pct`  | `len(description) >= 80`                      |
| `decisions_log_parity`       | `count(:Finding) ≈ lines(decisions.log)`      |
| `nine_discipline_pct`        | "missing X" findings naming all 9 strategies  |
| `severity_sanity_ok`         | LEVEL_1 ratio ≤ 20%                           |
| `dal_bypass_count`           | grep `tx.run("MERGE …")` in phase_*.py        |
| `fact_orphan_count`          | golden-rule check (must be 0)                 |
| `mechanical_overall`         | weighted average, 0..100                       |

Writes `quality_scorecard.json` into the workdir.

### LLM-as-judge — second-opinion grading

```bash
# Spawn the agent CLI to score 10 stratified findings
docker compose exec sparengine make -C /app/sparengine-export \
    llm-judge ASSET=/app/csvs/<uuid>-<label> SAMPLE=10

# Or skip the LLM entirely (writes null scores)
docker compose exec sparengine make -C /app/sparengine-export \
    llm-judge-skip ASSET=/app/csvs/<uuid>-<label>
```

Reuses the production `AGENT_CLI` (Gemini / Claude Pro/Max OAuth /
OpenClaude) — no separate API key. Costs ~$0.05 per run on Gemini-pro,
free under Pro/Max. Adds `llm_mean / llm_p20 / llm_total_cost_usd` to
the scorecard.

### Trend query

```cypher
// 30-day quality trend across all real-asset runs
MATCH (q:QualityScorecard)
WHERE q.timestamp > datetime() - duration("P30D")
RETURN q.asset_id, q.timestamp, q.mechanical_overall, q.llm_mean
ORDER BY q.timestamp;

// Recent runs whose mechanical rubric dropped — investigate
MATCH (a:QualityScorecard)-[:NEXT]->(b:QualityScorecard)
WHERE b.mechanical_overall < a.mechanical_overall - 5
RETURN a.run_id AS prev, b.run_id AS curr,
       a.mechanical_overall AS old_score,
       b.mechanical_overall AS new_score
ORDER BY b.timestamp DESC;
```

The `:QualityScorecard` is also auto-emitted from `localhost:2001`'s SSE
stream right after Phase 10 finishes — the UI shows a compact `QA
mechanical: 87/100 · findings 42 (L1=3) · orphans 0` summary in the log
row sequence before the redirect to Neo4j Browser fires.

---

## Layer C — cross-version benchmark

Captures snapshots of pipeline runs against archetype CSVs, lets the
analyser agent decide whether a sparengine version change was an
**improvement / regression / no_significant_effect / mixed**.

### Capture an archive (after a regression run)

```bash
docker compose exec sparengine make -C /app/sparengine-export \
    archive ARCHETYPE=helicopter_full \
            WORKDIR=/path/to/regression/workdir \
            VERSION=v4-2026-05-07-9c2d4f3
```

This snapshots the workdir into
`benchmarks/<version>/<archetype>/` (counts, scorecards, finding samples,
per-phase metrics) and MERGEs `:BenchmarkRun` + per-phase
`:PhaseScorecard` nodes.

### Run the analyser agent

```bash
# Compare the two most-recently archived versions
docker compose exec sparengine make -C /app/sparengine-export analyse

# Or be specific
docker compose exec sparengine python -m tools.analyse_change \
    --from v3-2026-05-06-a3f9c2d --to v4-2026-05-07-9c2d4f3

# Or scope to a single phase
docker compose exec sparengine make -C /app/sparengine-export \
    analyse-phase PHASE=phase4_components
```

The tool builds an `_analysis/` workdir under `benchmarks/<new_version>/`
containing the diffs / counts / sampled findings / cited evidence pages,
then spawns the agent (same `AGENT_CLI` as production) pointed at
`phases/briefs/phase_analyse.md`. The agent writes:

- `verdict.json` — machine-readable (overall + per-phase)
- `verdict.md`   — human-readable summary, paste-ready for changelogs

The orchestrator merges the verdicts into the matching `:BenchmarkRun`
and `:PhaseScorecard` nodes (`analysis_verdict`, `analysis_confidence`).

### Quick side-by-side (no agent)

```bash
docker compose exec sparengine make -C /app/sparengine-export \
    compare FROM=v3-2026-05-06 TO=v4-2026-05-07
```

Prints a count + score table for every archetype. Add `PHASE=phase4_components`
to scope to a single phase across archetypes.

### Trend queries

```cypher
// Overall verdict trend per archetype
MATCH (b:BenchmarkRun {archetype: "helicopter_full"})
RETURN b.version, b.total_components, b.mechanical_overall,
       b.analysis_verdict, b.analysis_confidence
ORDER BY b.timestamp;

// Which phases have been regressing across the last 5 versions?
MATCH (p:PhaseScorecard)
WHERE p.analysis_verdict = "regression"
RETURN p.version, p.archetype, p.phase_id,
       p.delta_mechanical, p.analysis_reasoning
ORDER BY p.timestamp DESC
LIMIT 25;

// Which versions need human attention?
MATCH (b:BenchmarkRun)
WHERE b.analysis_verdict IN ["regression", "mixed"]
   OR b.analysis_confidence < 3
RETURN b.version, b.archetype, b.analysis_verdict,
       b.analysis_path
ORDER BY b.timestamp DESC;
```

---

## File map

```
sparengine-export/
  Makefile                         — entry points (test, scorecard, archive, …)
  .pre-commit-config.yaml          — fast-tier hook
  docs/
    QA.md                          — this file
  graph_dal/
    quality.py                     — :QualityScorecard writer
    benchmark.py                   — :BenchmarkRun + :PhaseScorecard writers
    _phase_tag.py                  — created_in_phase context manager
    verify.py                      — fact-orphan check + non-fact-bearing list
  phases/
    briefs/
      phase_analyse.md             — Layer C analyser agent brief
    cypher/
      schema.cypher                — constraints for the QA labels
      captions.cypher              — Browser auto-captions for the QA labels
  tests/
    conftest.py                    — pytest fixtures (incl. benchmark_archetype)
    pytest.ini                     — strict markers + test paths
    fixtures/
      build_synthetic.py           — deterministic synthetic CSV generators
      README.md                    — fixture inventory
    unit/                          — pure-Python tests (28)
    lint/                          — file-shape rules (15)
    integration/                   — DAL round-trip tests (13)
  tools/
    quality_scorecard.py           — Layer B mechanical rubric
    llm_judge.py                   — Layer B LLM-as-judge sampler
    benchmark_archive.py           — Layer C snapshot
    analyse_change.py              — Layer C agent spawner
    compare_versions.py            — Layer C side-by-side
    precommit_fast.sh              — pre-commit wrapper
  benchmarks/
    index.json                     — registry of archived versions
    <version>/<archetype>/         — captured run state + verdicts
```

---

## Cost summary

| Layer | When                | Cost                                  |
|-------|---------------------|---------------------------------------|
| A     | every commit / push | seconds of CPU                        |
| B mech | after every run    | seconds of CPU                        |
| B llm  | after every run    | ~$0.05 (Gemini) or free (Pro/Max OAuth) |
| C archive | per `make regression` | seconds of CPU + disk           |
| C analyser | per change / regression | ~30-60s agent time, same as B llm |

No new API keys, no new auth surface — Layer B + Layer C reuse the
production agent CLI's auth.
