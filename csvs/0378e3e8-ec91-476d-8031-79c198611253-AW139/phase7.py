"""Phase 7 — Component investigation (mechanical FORM1_MISSING stub).

The full SPARENGINE Phase 7 is a JUDGEMENT phase that walks each component,
runs the Investigation Discipline checklist, and writes a paragraph of
reasoning per finding. That requires an LLM pass per component.

This stub is the mechanical floor: walk components that have no incoming
:RELEASES edge from a :Form1 (i.e. no Form 1 we can attribute to them)
and emit a FORM1_MISSING finding per component.
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
    raise RuntimeError("phase7.py: could not locate graph_dal")


_bootstrap_graph_dal()

from graph_dal import connect, database_name, FindingSeverity   # noqa: E402
from graph_dal.finding import write_audit_run, write_finding    # noqa: E402
from graph_dal.verify import verify_no_fact_orphans             # noqa: E402


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
            raise RuntimeError("phase7: cannot determine asset_id")
        print(f"phase7: asset_id={asset_id}", flush=True)

        # AuditRun anchor
        from datetime import datetime
        run_id = f"audit::phase7::{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_audit_run(
                    tx, asset_id=asset_id, value=run_id,
                    audit_snapshot_date_iso=datetime.utcnow().date().isoformat(),
                    sparengine_version="phase7-stub-1",
                )
                tx.commit()

        # Find components without any :Form1 RELEASES edge.
        # Limit to LLP and overhaul-tracked components — those are the
        # ones an auditor cares about (basic fasteners with no Form 1
        # are not findings).
        with driver.session(database=database_name()) as s:
            rows = list(s.run(
                "MATCH (c:Component {asset_id: $aid}) "
                "WHERE (c.is_llp = true OR c.is_overhaul = true) "
                "  AND NOT EXISTS { (:Form1)-[:RELEASES]->(c) } "
                "OPTIONAL MATCH (c)-[:EVIDENCED_BY]->(p:Page) "
                "WITH c, p ORDER BY c.value LIMIT 50 "
                "RETURN c.value AS uid, c.canonical_pn AS pn, c.installed_sn AS sn, "
                "       p.value AS page_uid",
                aid=asset_id,
            ))

        findings_written = 0
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for r in rows:
                    if not r["page_uid"]:
                        continue            # can't raise without page evidence
                    pn = r["pn"] or "?"
                    sn = r["sn"] or "?"
                    fuid = f"finding::FORM1_MISSING::{pn}::{sn}"
                    write_finding(
                        tx, asset_id=asset_id, value=fuid,
                        severity=FindingSeverity.LEVEL_2.value,
                        category="FORM1_MISSING",
                        title=f"Form 1 not located for {pn}/{sn}",
                        description=(
                            f"Component {r['uid']} is LLP/overhaul-tracked but has "
                            f"no :Form1 with a :RELEASES edge attributing release "
                            f"to this component. Phase 7.5 verification should "
                            f"re-search for batch certificates, neighbouring pages, "
                            f"and alternate PNs before this finding is closed."
                        ),
                        evidence_page_uid=r["page_uid"],
                        evidence_quote=f"No Form 1 located for component {pn}/{sn}",
                        recommended_action=(
                            "Locate the Form 1 covering the installed serial number, "
                            "or close as 'parted out / not in scope' if the dossier "
                            "permits."
                        ),
                        flags_label="Component", flags_uid=r["uid"],
                        component_uid=r["uid"],
                        audit_run_uid=run_id,
                        status="OPEN",
                    )
                    findings_written += 1
                tx.commit()

        verify_counts = verify_no_fact_orphans(driver, asset_id, phase="7")
        with driver.session(database=database_name()) as s:
            n_findings = s.run(
                "MATCH (f:Finding {asset_id: $aid}) RETURN count(f) AS n",
                aid=asset_id,
            ).single()["n"]

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 7 verification ==\n")
            f.write(f"- candidate components scanned (limit 50)  : {len(rows)}\n")
            f.write(f"- FORM1_MISSING findings written           : {findings_written}\n")
            f.write(f"- :Finding count (live)                    : {n_findings}\n")
            f.write(f"- audit_run_uid                             : {run_id}\n")
            f.write(f"- fact_nodes_no_evidence                    : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")
            f.write("- (Note: this is the mechanical stub; full Phase 7 judgement "
                    "requires an LLM-driven walk per component.)\n")

        print(f"phase7: OK — findings={n_findings}  "
              f"orphans={verify_counts['fact_nodes_no_evidence']}", flush=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
