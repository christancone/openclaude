"""Phase 8 — Asset audit (mandatory checklist stub).

Full Phase 8 walks 12 mandatory-checklist items, each requiring a
judgement call. This stub writes them as a single asset-level metadata
property on :Asset, indicating each item's coverage state based on
mechanical signals.
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
    raise RuntimeError("phase8.py: could not locate graph_dal")


_bootstrap_graph_dal()

from graph_dal import connect, database_name              # noqa: E402
from graph_dal.verify import verify_no_fact_orphans       # noqa: E402


CHECKLIST_ITEMS = [
    "asset_orientation",
    "logbooks_present",
    "form1_coverage",
    "llp_status_current",
    "ad_compliance_summary",
    "sb_compliance_summary",
    "modification_inventory",
    "weight_balance_current",
    "registration_history",
    "lease_history",
    "ndt_records",
    "dent_buckle_chart",
]


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
            raise RuntimeError("phase8: cannot determine asset_id")
        print(f"phase8: asset_id={asset_id}", flush=True)

        # Mechanical coverage check based on doc-type presence:
        coverage = {}
        with driver.session(database=database_name()) as s:
            doc_types = s.run(
                "MATCH (d:Document {asset_id: $aid}) "
                "RETURN d.document_type AS dt, count(*) AS n",
                aid=asset_id,
            )
            doc_type_counts = {r["dt"]: r["n"] for r in doc_types if r["dt"]}

            n_form1 = s.run(
                "MATCH (:Form1 {asset_id: $aid}) RETURN count(*) AS n",
                aid=asset_id,
            ).single()["n"]
            n_llp = s.run(
                "MATCH (c:Component {asset_id: $aid}) "
                "WHERE c.is_llp = true RETURN count(c) AS n",
                aid=asset_id,
            ).single()["n"]

        coverage = {
            "asset_orientation":     "present" if profile.get("asset_class") else "missing",
            "logbooks_present":      "present" if any(
                k.endswith("logbook") for k in doc_type_counts
            ) else "missing",
            "form1_coverage":        "present" if n_form1 > 0 else "missing",
            "llp_status_current":    "present" if (
                doc_type_counts.get("engine_llp_status_sheet", 0)
                or doc_type_counts.get("life_limited_parts_status", 0)
            ) else "missing",
            "ad_compliance_summary": "present" if (
                doc_type_counts.get("airworthiness_directive_compliance", 0)
                or doc_type_counts.get("ad_status_report", 0)
            ) else "missing",
            "sb_compliance_summary": "present" if (
                doc_type_counts.get("service_bulletin_compliance", 0)
                or doc_type_counts.get("sb_status_report", 0)
            ) else "missing",
            "modification_inventory": "present" if (
                doc_type_counts.get("modification_record", 0)
            ) else "missing",
            "weight_balance_current": "present" if (
                doc_type_counts.get("weight_and_balance_report", 0)
            ) else "missing",
            "registration_history":  "missing",   # Phase 0 didn't extract
            "lease_history":         "missing",
            "ndt_records":           "present" if (
                doc_type_counts.get("borescope_inspection_report", 0)
                or doc_type_counts.get("inspection_report", 0)
            ) else "missing",
            "dent_buckle_chart":     "present" if (
                doc_type_counts.get("dent_and_buckle_chart", 0)
            ) else "missing",
        }

        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                tx.run(
                    "MATCH (a:Asset {asset_id: $aid}) "
                    "SET a.mandatory_checklist = $cov, a.checklist_phase = 'phase8-stub'",
                    aid=asset_id, cov=json.dumps(coverage),
                ).consume()
                tx.commit()

        verify_counts = verify_no_fact_orphans(driver, asset_id, phase="8")

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 8 verification ==\n")
            f.write(f"- mandatory_checklist items                : {len(CHECKLIST_ITEMS)}\n")
            for item, state in coverage.items():
                f.write(f"  {item:<28s}: {state}\n")
            present = sum(1 for v in coverage.values() if v == "present")
            f.write(f"- coverage: {present}/{len(CHECKLIST_ITEMS)} items present\n")
            f.write(f"- fact_nodes_no_evidence                   : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")

        print(f"phase8: OK — checklist {sum(1 for v in coverage.values() if v == 'present')}/"
              f"{len(CHECKLIST_ITEMS)} present  "
              f"orphans={verify_counts['fact_nodes_no_evidence']}", flush=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
