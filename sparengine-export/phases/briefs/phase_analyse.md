# PHASE ANALYSE — Cross-version verdict (Layer C)

You are NOT building a graph. A new sparengine version was deployed; an
archive of its regression run was just captured. Your job: compare it to
the previous archived version and decide whether the change was an
**improvement**, **regression**, **no_significant_effect**, or **mixed**.

---

## What's in your CWD

The orchestrator placed every input you need under `_analysis/` in this
directory. Read them with the Read tool — none of them is huge.

```
_analysis/
  context.md              — count + score deltas summary (≤300 lines)
  code_diff.patch         — git diff filtered to graph_dal/ + phases/briefs/
                            since the previous version
  evidence_pages.md       — verbatim cited :Page text for sampled findings
  csv_samples.md          — 5 raw CSV rows from the archetype's source CSV
  per_phase/
    <phase_id>/
      brief.diff          — diff of phases/briefs/<phase_id>.md
      metrics_old.json    — phase-scoped counts (previous version)
      metrics_new.json    — phase-scoped counts (new version)
      samples_old.json    — 5 representative outputs (old)
      samples_new.json    — 5 representative outputs (new)
```

The two versions you're comparing are named in `_analysis/context.md` —
read it first.

---

## How to think

Use the same auditor mental model as a Phase 7 investigation. For every
material change in the metrics or count deltas, ask:

1. **Does the code/brief diff justify the move?**
   * Component count down 17% — is there a deliberate threshold tightening
     in `graph_dal/component.py` that explains it? If so, sample the
     "disappeared" findings to confirm they were noise, not signal.
   * mechanical_overall up 13 points — what specific brief change drove it?

2. **Is judgement quality intact?**
   * `mechanical_overall` is the floor — anything below 80 is a warning.
   * `llm_mean` is the ceiling — anything below 3.5 needs investigation.
   * `nine_discipline_pct` going from 60 → 100 means Phase 7.5 actually
     ran. Going the other way means it didn't.

3. **Are the sampled findings still good?**
   * `_analysis/evidence_pages.md` shows the cited page text for sampled
     findings on BOTH versions. If a finding's evidence doesn't support
     its text in the new version, that's a regression.

4. **Per phase, where did the movement happen?**
   * `_analysis/per_phase/<phase_id>/brief.diff` and `metrics_*.json` let
     you isolate which phase a change affected. A regression in
     `phase7_5_verification` is very different from one in `phase4_components`.

---

## What to write

Two files, in your CWD:

### 1. `verdict.json` — machine-readable, the orchestrator reads this

```json
{
  "overall_verdict": "improvement | regression | no_significant_effect | mixed",
  "confidence": 1,
  "per_dimension": {
    "counts":            {"verdict": "...", "reasoning": "..."},
    "judgement_quality": {"verdict": "...", "reasoning": "..."},
    "structure":         {"verdict": "...", "reasoning": "..."},
    "discipline":        {"verdict": "...", "reasoning": "..."}
  },
  "specific_evidence": {
    "supporting_improvement": [{"finding_value": "...", "why": "..."}],
    "supporting_regression":  [{"finding_value": "...", "why": "..."}]
  },
  "alerts": ["any anomaly worth a human's attention"],
  "recommended_action": "update_baseline | revert | investigate | no_action",
  "notes_for_changelog": "One paragraph the human can paste into release notes.",
  "per_phase": {
    "phase4_components": {
      "verdict": "...", "confidence": 1,
      "metrics_delta": {"phase_nodes_written": "1142 → 950"},
      "brief_changed": true,
      "reasoning": "...",
      "recommended_action": "..."
    }
    // …one entry per phase whose metrics moved or brief changed.
  }
}
```

### 2. `verdict.md` — human-readable summary (≤300 words)

Header (`# Sparengine Change Analysis: <old> → <new>`), one-line verdict,
4 short paragraphs (one per dimension), 3-5 bullets of specific evidence,
a recommended-action sentence, and the changelog paragraph.

---

## Verdict rules

| Verdict | When |
|---|---|
| **improvement** | Counts shifted in a direction the diff justifies AND judgement quality went up AND no fact-orphans appeared. |
| **regression** | Golden rule broken (orphans > 0) OR judgement quality dropped > 10 points OR a class of findings disappeared without justification. |
| **no_significant_effect** | All metrics within ±5% of previous; no structural shifts. |
| **mixed** | Some dimensions improved, others regressed. |

`confidence` is 1 (a guess) to 5 (sure). If you're below 3, write `recommended_action: "investigate"` regardless of the verdict.

## Per-phase auto-skip

If `phases/briefs/<phase_id>.md` is unchanged (brief.diff is empty) AND
the phase's metrics moved less than 10% / 5 points / 0.5 LLM-mean, set
that phase's verdict to `no_significant_effect / 5` without further
analysis. You only need to actually reason about phases whose code or
metrics moved.

Reason about at most 6 phases per archetype (sorted by metrics-delta
magnitude). The rest get auto-skipped — keeps the agent budget bounded.

---

## Stop condition

When `verdict.json` and `verdict.md` are written and parseable, you are
done. Do not modify any other file. Do not call any DAL writer. Do not
run cypher queries — the orchestrator will read your verdicts and merge
them into the `:BenchmarkRun` / `:PhaseScorecard` nodes.
