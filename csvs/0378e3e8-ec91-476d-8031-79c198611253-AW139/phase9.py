"""Phase 9 — Finding consolidation (stub).

Full Phase 9 deduplicates findings, rolls up component-level findings to
the asset level, and applies the severity matrix. This stub just verifies
no orphans and logs the current finding distribution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase9.py: could not locate graph_dal")


_bootstrap_graph_dal()

from graph_dal import connect, database_name              # noqa: E402
from graph_dal.verify import verify_no_fact_orphans       # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--asset-id", required=False)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    log_path = workdir / "progress.log"
    profile_path = workdir / "asset_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    asset_id = args.asset_id or profile.get("asset_id")

    driver = connect()
    try:
        if not asset_id:
            with driver.session(database=database_name()) as s:
                rec = s.run("MATCH (a:Asset) RETURN a.asset_id AS aid LIMIT 1").single()
                asset_id = rec["aid"] if rec else None
        if not asset_id:
            raise RuntimeError("phase9: cannot determine asset_id")
        print(f"phase9: asset_id={asset_id}", flush=True)

        with driver.session(database=database_name()) as s:
            by_severity = list(s.run(
                "MATCH (f:Finding {asset_id: $aid}) "
                "RETURN f.severity AS s, count(*) AS n ORDER BY n DESC",
                aid=asset_id,
            ))
            by_category = list(s.run(
                "MATCH (f:Finding {asset_id: $aid}) "
                "RETURN f.category AS c, count(*) AS n ORDER BY n DESC",
                aid=asset_id,
            ))
            by_status = list(s.run(
                "MATCH (f:Finding {asset_id: $aid}) "
                "RETURN f.status AS s, count(*) AS n ORDER BY n DESC",
                aid=asset_id,
            ))

        verify_counts = verify_no_fact_orphans(driver, asset_id, phase="9")

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 9 verification ==\n")
            f.write("- findings by severity:\n")
            for r in by_severity:
                f.write(f"    {r['s']:<10s} : {r['n']}\n")
            f.write("- findings by category:\n")
            for r in by_category:
                f.write(f"    {r['c']:<28s} : {r['n']}\n")
            f.write("- findings by status:\n")
            for r in by_status:
                f.write(f"    {r['s']:<10s} : {r['n']}\n")
            f.write(f"- fact_nodes_no_evidence                   : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")

        total = sum(r["n"] for r in by_severity)
        print(f"phase9: OK — total_findings={total}  "
              f"orphans={verify_counts['fact_nodes_no_evidence']}", flush=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
