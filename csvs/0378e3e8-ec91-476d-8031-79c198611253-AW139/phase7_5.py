"""Phase 7.5 — Verification (fulltext re-search smoke test).

The full Phase 7.5 re-runs 9 search strategies per OPEN finding to see
if the missing evidence is somewhere in the corpus. That requires
LLM-driven query construction.

This stub demonstrates the fulltext index works — for each OPEN
FORM1_MISSING finding raised by phase7, run a single Lucene phrase
search for the component's PN and SN. Log the top hits so an auditor
can review whether the OCR genuinely missed the Form 1.
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
    raise RuntimeError("phase7_5.py: could not locate graph_dal")


_bootstrap_graph_dal()

from graph_dal import connect, database_name              # noqa: E402
from graph_dal.fulltext import escape_lucene, search_pages  # noqa: E402
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
            raise RuntimeError("phase7_5: cannot determine asset_id")
        print(f"phase7_5: asset_id={asset_id}", flush=True)

        # Smoke test: search for "Form 1" — should return high-scoring pages
        with driver.session(database=database_name()) as s:
            form1_hits = search_pages(
                s, asset_id=asset_id,
                query='"Form 1" OR "EASA Form" OR "Form 8130"', limit=10,
            )

        # For each open FORM1_MISSING finding (limit 20), run Lucene by PN+SN
        per_finding_hits: dict[str, list[dict]] = {}
        with driver.session(database=database_name()) as s:
            findings = list(s.run(
                "MATCH (f:Finding {asset_id: $aid}) "
                "WHERE f.category = 'FORM1_MISSING' AND f.status = 'OPEN' "
                "RETURN f.value AS uid, f.title AS title LIMIT 20",
                aid=asset_id,
            ))
            for f in findings:
                # Extract PN/SN from the title — heuristic
                title = f["title"] or ""
                pn_sn = title.replace("Form 1 not located for ", "").strip()
                if "/" not in pn_sn:
                    continue
                pn, sn = pn_sn.split("/", 1)
                pn = escape_lucene(pn.strip())
                sn = escape_lucene(sn.strip())
                if not pn or pn == "?" or not sn or sn == "?":
                    continue
                hits = search_pages(s, asset_id=asset_id,
                                    query=f'"{pn}" AND "{sn}"', limit=3)
                if hits:
                    per_finding_hits[f["uid"]] = hits

        verify_counts = verify_no_fact_orphans(driver, asset_id, phase="7.5")

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 7.5 verification ==\n")
            f.write(f"- 'Form 1' phrase smoke test hits         : {len(form1_hits)}\n")
            if form1_hits:
                f.write(f"  top score: {form1_hits[0]['score']:.2f} "
                        f"on page {form1_hits[0]['page_uid'][:8]}\n")
            f.write(f"- findings_with_potential_evidence_pages   : {len(per_finding_hits)}\n")
            f.write(f"- (Note: this is the mechanical stub; full 7.5 verification "
                    f"runs 9 strategies per finding via LLM.)\n")
            f.write(f"- fact_nodes_no_evidence                  : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")

        print(
            f"phase7_5: OK — form1_hits={len(form1_hits)}  "
            f"finding_searches_with_hits={len(per_finding_hits)}  "
            f"orphans={verify_counts['fact_nodes_no_evidence']}",
            flush=True,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
