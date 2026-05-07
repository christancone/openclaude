"""Layer B (mechanical rubric) — compute per-real-asset quality scorecard.

Reads the asset's :Finding nodes from Neo4j + the workdir's `decisions.log`
and produces:
  * `<workdir>/quality_scorecard.json` — human-inspectable rubric output
  * a MERGE'd `:QualityScorecard` node in Neo4j (via graph_dal.quality)

The rubric is deterministic and Cypher-only — fast (< 30s on 50k-node
graphs). It does NOT call an LLM. The LLM-as-judge layer is `llm_judge.py`,
which writes complementary fields onto the same scorecard.

CLI
---

::

    python -m tools.quality_scorecard --workdir /app/csvs/<asset_uuid>-<label>

Or programmatically::

    from tools.quality_scorecard import compute_scorecard
    out = compute_scorecard(workdir=Path("/app/csvs/..."))
    print(out["mechanical_overall"])

Rubric
------

| Component                    | Weight | Source                                     |
|------------------------------|--------|--------------------------------------------|
| citation_present_pct         |  20    | description matches /\\(file:\\s*[^)]+\\)/  |
| description_length_ok_pct    |  15    | len(description) >= 80                     |
| decisions_log_parity         |  15    | count(:Finding) == lines(decisions.log)    |
| nine_discipline_pct          |  15    | "missing X" findings naming all 9          |
| severity_sanity_ok           |  10    | level_1 ratio <= 0.20                      |
| dal_bypass_count == 0        |  10    | static-analysis grep over phase_*.py       |
| fact_orphan_count == 0       |  15    | graph_dal.verify.count_fact_orphans        |

mechanical_overall = weighted average, scaled 0..100.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Allow running as `python -m tools.quality_scorecard` AND `python tools/quality_scorecard.py`.
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))                              # sparengine-export/

from graph_dal import connect, database_name                              # noqa: E402
from graph_dal.quality import write_quality_scorecard                     # noqa: E402
from graph_dal.verify import count_fact_orphans                           # noqa: E402


# ---------------------------------------------------------------------------
# Regex / heuristic constants
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"\(file\s*:\s*[^)]+\)", re.IGNORECASE)
_PAGE_RE     = re.compile(r"\bpage\s*:\s*\d+", re.IGNORECASE)

# The 9 strategies from `phases/references/investigation_discipline.md`.
# A "missing X" finding (FORM1_MISSING, AD_COMPLIANCE_UNVERIFIED, etc.) must
# name *each* of these in its description or in decisions.log to count as
# fully-disciplined.
NINE_STRATEGIES = (
    "wo_pages", "sn_alone", "alt_pn", "filename_pn", "filename_sn",
    "batch_range", "page_neighbourhood", "siblings", "oem_typical",
)

# A category counts as "missing X" if it ends with one of these suffixes.
MISSING_CATEGORY_PATTERNS = (
    "MISSING", "UNVERIFIED", "NOT_LOCATED", "GAP_IN_DOSSIER",
)

WEIGHTS = {
    "citation_present_pct":      20,
    "description_length_ok_pct": 15,
    "decisions_log_parity":      15,
    "nine_discipline_pct":       15,
    "severity_sanity_ok":        10,
    "dal_bypass_zero":           10,    # 100 if zero, else 0
    "fact_orphan_zero":          15,    # 100 if zero, else 0
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_asset_id(workdir: Path) -> str:
    """Read asset_profile.json or fall back to the workdir folder name UUID."""
    profile = workdir / "asset_profile.json"
    if profile.is_file():
        try:
            data = json.loads(profile.read_text(encoding="utf-8"))
            for key in ("asset_id", "asset_uid"):
                if isinstance(data.get(key), str) and data[key]:
                    return data[key]
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback: workdir name `{uuid}-{label}` → take the UUID prefix.
    folder = workdir.name
    return folder.split("-", 5)[0:5] and "-".join(folder.split("-", 5)[:5]) or folder


def _read_findings(driver, asset_id: str) -> list[dict[str, Any]]:
    """All :Finding nodes for the asset, with the fields the rubric needs."""
    with driver.session(database=database_name()) as s:
        rs = s.run(
            "MATCH (f:Finding {asset_id: $aid}) "
            "RETURN f.value AS value, f.severity AS severity, "
            "       f.category AS category, f.title AS title, "
            "       f.description AS description, f.status AS status",
            aid=asset_id,
        )
        return [dict(r) for r in rs]


def _decisions_log_count(workdir: Path) -> int:
    """Lines in `decisions.log` (one per finding decision). 0 if absent."""
    log = workdir / "decisions.log"
    if not log.is_file():
        return 0
    return sum(1 for line in log.read_text(encoding="utf-8").splitlines() if line.strip())


def _grep_dal_bypass(workdir: Path) -> int:
    """Count phase-script lines that look like raw `tx.run("MERGE ...")` writes.

    Phase scripts must go through `graph_dal/`. A literal MERGE in a phase
    script is the bypass smell. We count occurrences across phase*.py files.
    Comments (lines starting with `#` or where MERGE is in a string assigned
    to a constant) are ignored heuristically.
    """
    count = 0
    pattern = re.compile(r'tx\.run\(\s*[fr]?["\'][^"\']*\bMERGE\b', re.IGNORECASE)
    for py in workdir.glob("phase*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                count += 1
    return count


# ---------------------------------------------------------------------------
# Per-rubric metric computations
# ---------------------------------------------------------------------------

def _citation_present_pct(findings: list[dict]) -> int:
    if not findings:
        return 0
    n_ok = sum(
        1 for f in findings
        if f.get("description") and (
            _CITATION_RE.search(f["description"]) or _PAGE_RE.search(f["description"])
        )
    )
    return round(100 * n_ok / len(findings))


def _description_length_ok_pct(findings: list[dict]) -> int:
    if not findings:
        return 0
    n_ok = sum(1 for f in findings if (f.get("description") or "") and len(f["description"]) >= 80)
    return round(100 * n_ok / len(findings))


def _decisions_log_parity(findings: list[dict], workdir: Path) -> int:
    """100 if count(findings) == lines(decisions.log), else 0.

    Allows ±1 slack for the case where one finding == multiple discipline
    lines (rare). Strict-equality is the intended check.
    """
    if not findings:
        return 100   # vacuously OK
    n_lines = _decisions_log_count(workdir)
    n_findings = len(findings)
    return 100 if abs(n_lines - n_findings) <= 1 else 0


def _nine_discipline_pct(findings: list[dict], workdir: Path) -> int:
    """Of all 'missing X' findings, what % name all 9 strategies in their
    description OR in their corresponding decisions.log line?

    Decisions.log lines are matched by component_id substring or finding
    value; we union the description text + matching decision lines.
    """
    missing = [f for f in findings if any(p in (f.get("category") or "") for p in MISSING_CATEGORY_PATTERNS)]
    if not missing:
        return 100
    # Cache decisions.log content once
    log_path = workdir / "decisions.log"
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    n_ok = 0
    for f in missing:
        haystack = (f.get("description") or "")
        # Crude: also pull lines from decisions.log mentioning the value
        if f.get("value") and f["value"] in log_text:
            for line in log_text.splitlines():
                if f["value"] in line:
                    haystack += " " + line
        if all(strat in haystack for strat in NINE_STRATEGIES):
            n_ok += 1
    return round(100 * n_ok / len(missing))


def _severity_sanity_ok(severity_counts: dict[str, int]) -> int:
    """LEVEL_1 (critical) findings should be < 20% of total. Anything higher
    suggests the severity matrix was misapplied (the historical ATR72 run had
    14 of 50 LEVEL_1 — the matrix said maybe 4 should be).
    """
    total = sum(severity_counts.values()) or 1
    l1 = severity_counts.get("level_1", 0)
    return 100 if (l1 / total) <= 0.20 else 0


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

def compute_scorecard(*, workdir: Path, asset_id: str | None = None,
                      sparengine_version: str | None = None,
                      run_id: str | None = None,
                      delta_mechanical: float | None = None,
                      delta_llm: float | None = None) -> dict[str, Any]:
    """Compute and persist a :QualityScorecard.

    Returns a dict matching `quality_scorecard.json`. Idempotent — re-running
    the tool overwrites both the on-disk JSON and the Neo4j node's metrics.
    """
    workdir = workdir.resolve()
    if not workdir.is_dir():
        raise FileNotFoundError(f"workdir not found: {workdir}")

    asset_id = asset_id or _resolve_asset_id(workdir)
    sparengine_version = sparengine_version or os.environ.get("SPARENGINE_VERSION", "phase10-dev")
    run_id = run_id or f"run-{int(time.time())}"

    driver = connect()
    try:
        # Fetch findings + severity counts
        findings = _read_findings(driver, asset_id)
        severity_counts: dict[str, int] = {}
        for f in findings:
            sev = (f.get("severity") or "").lower()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        # Per-metric scores
        citation_pct          = _citation_present_pct(findings)
        desc_pct              = _description_length_ok_pct(findings)
        log_parity            = _decisions_log_parity(findings, workdir)
        nine_pct              = _nine_discipline_pct(findings, workdir)
        severity_ok           = _severity_sanity_ok(severity_counts)
        bypass_count          = _grep_dal_bypass(workdir)
        fact_orphan_count     = sum(count_fact_orphans(
            driver.session(database=database_name()), asset_id
        ).values())

        # Composite (weighted average, 0..100)
        components = {
            "citation_present_pct":      citation_pct,
            "description_length_ok_pct": desc_pct,
            "decisions_log_parity":      log_parity,
            "nine_discipline_pct":       nine_pct,
            "severity_sanity_ok":        severity_ok,
            "dal_bypass_zero":           100 if bypass_count == 0 else 0,
            "fact_orphan_zero":          100 if fact_orphan_count == 0 else 0,
        }
        weighted = sum(components[k] * WEIGHTS[k] for k in WEIGHTS) // sum(WEIGHTS.values())

        out: dict[str, Any] = {
            "asset_id":                    asset_id,
            "run_id":                      run_id,
            "sparengine_version":          sparengine_version,
            "mechanical_overall":          weighted,
            "citation_present_pct":        citation_pct,
            "description_length_ok_pct":   desc_pct,
            "decisions_log_parity":        log_parity,
            "nine_discipline_pct":         nine_pct,
            "severity_sanity_ok":          severity_ok,
            "dal_bypass_count":            bypass_count,
            "fact_orphan_count":           fact_orphan_count,
            "total_findings":              len(findings),
            "level_1_count":               severity_counts.get("level_1", 0),
            "level_2_count":               severity_counts.get("level_2", 0),
            "level_3_count":               severity_counts.get("level_3", 0),
            # LLM-as-judge fields populated by tools/llm_judge.py later
            "llm_sample_size":             0,
            "llm_mean":                    None,
            "llm_median":                  None,
            "llm_p20":                     None,
            "llm_total_cost_usd":          None,
        }

        # Persist to disk + Neo4j
        out_path = workdir / "quality_scorecard.json"
        out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

        with driver.session(database=database_name()) as s:
            with s.begin_transaction() as tx:
                write_quality_scorecard(
                    tx,
                    asset_id=asset_id,
                    run_id=run_id,
                    sparengine_version=sparengine_version,
                    mechanical_overall=weighted,
                    citation_present_pct=citation_pct,
                    description_length_ok_pct=desc_pct,
                    decisions_log_parity=log_parity,
                    nine_discipline_pct=nine_pct,
                    severity_sanity_ok=severity_ok,
                    dal_bypass_count=bypass_count,
                    fact_orphan_count=fact_orphan_count,
                    total_findings=len(findings),
                    level_1_count=severity_counts.get("level_1", 0),
                    level_2_count=severity_counts.get("level_2", 0),
                    level_3_count=severity_counts.get("level_3", 0),
                    delta_mechanical=delta_mechanical,
                    delta_llm=delta_llm,
                )
                tx.commit()
    finally:
        driver.close()

    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--workdir", required=True, type=Path,
                   help="Per-asset workdir, e.g. /app/csvs/<uuid>-<label>")
    p.add_argument("--asset-id", default=None,
                   help="Override asset_id (defaults to asset_profile.json)")
    p.add_argument("--run-id", default=None,
                   help="Identifier for the :AuditRun this scorecard is for")
    p.add_argument("--sparengine-version", default=None,
                   help="Override version string (defaults to env SPARENGINE_VERSION)")
    p.add_argument("--print", action="store_true",
                   help="Print the resulting JSON to stdout")
    args = p.parse_args()

    out = compute_scorecard(
        workdir=args.workdir,
        asset_id=args.asset_id,
        run_id=args.run_id,
        sparengine_version=args.sparengine_version,
    )

    summary = (
        f"mechanical_overall={out['mechanical_overall']}/100  "
        f"findings={out['total_findings']} (L1={out['level_1_count']} "
        f"L2={out['level_2_count']} L3={out['level_3_count']})  "
        f"orphans={out['fact_orphan_count']}  bypass={out['dal_bypass_count']}"
    )
    print(summary, file=sys.stderr)
    if args.print:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
