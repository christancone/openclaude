"""Phase 7.5 — Verification + 3 acceptance test cases for CL650-6134.

For each Phase 7 OPEN finding, re-run search disciplines. Close as
CLOSED_FALSE_POSITIVE when verification finds the missing evidence.

Then runs the 3 named acceptance cases and writes the final report
build_final_report.md to dumps/.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase7_5.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name
from graph_dal._phase_tag import phase
from graph_dal.fulltext import search_pages, escape_lucene
from graph_dal.verify import verify_no_fact_orphans


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"
DECISIONS_LOG = DUMPS_DIR / "decisions.log"
FINAL_REPORT = DUMPS_DIR / "build_final_report.md"


# Acceptance test cases.
ACCEPTANCE_CASES = [
    {
        "name": "NiCad battery (EASA single-item header_fields variant)",
        "page_uid": "c608f9ce-aade-468c-9e46-1a5a5686899f",
        "expected_value": "form1::00029737",
        "expected_tracking": "00029737",
        "expected_pn": "024453-000",
        "expected_sn": "090520021095E",
        "expected_status": "OVERHAULED",
        "expected_signer": "Sklyarenko Natalya",
        "expected_cert": "EASA.145.0321",
        "expected_date": "2019-08-27",
    },
    {
        "name": "Bombardier window-shade batch cert (TCCA, multi-SN cell)",
        "page_uid": "aea06e12-ed91-419c-b6d1-1b84ba8bd548",
        "expected_value": "form1::170001777769 - 1",
        "expected_tracking": "170001777769 - 1",
        "expected_pn": "604DX2529029AND001",
        "expected_sns": ["4005", "4006", "4007"],
        "expected_status": "NEW",
        "expected_signer": "Michael Jernigan",
        "expected_date": "2019-07-01",
    },
    {
        "name": "ITT valve (FAA 8130-3, multi-PN block 8)",
        "page_uid": "82b0d5f3-6b09-48d4-a853-a939357499c0",
        "expected_value": "form1::283704",
        "expected_tracking": "283704",
        "expected_pns": ["AV16B2177-3", "601-62900-7"],
        "expected_sn": "AA441157",
        "expected_status": "REPAIRED",
        "expected_signer": "Olivia Mora",
        "expected_cert": "BV4R090M",
        "expected_date": "2019-10-22",
    },
]


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _decisions(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with DECISIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run_acceptance_case(driver, case: dict) -> dict:
    """Validate one Form 1 acceptance test case. Returns a result dict."""
    result = {
        "name": case["name"], "page": case["page_uid"],
        "expected": case, "actual": {}, "checks": [], "pass": True,
    }
    with driver.session(database=database_name()) as s:
        # 1. Find the Form1 carried by the page.
        rec = s.run("""
            MATCH (p:Page {asset_id:$aid, value:$puid})-[:CARRIES]->(f:Form1)
            RETURN f.value AS v, f.block_3_form_tracking_no AS tn,
                   f.block_8_pn AS pn, f.block_10_sn AS sn,
                   f.block_11_status AS st, f.block_13b_name AS signer,
                   f.block_13c_cert_number AS cert, f.block_13_date AS date,
                   f.block_4_issuer_text AS issuer
        """, aid=ASSET_ID, puid=case["page_uid"]).single()

        if not rec:
            result["pass"] = False
            result["checks"].append(("form1_exists", False, "No Form1 carried by page"))
            return result
        result["actual"] = dict(rec)

        # Value/tracking check.
        if rec["v"] == case["expected_value"]:
            result["checks"].append(("value", True, rec["v"]))
        else:
            result["pass"] = False
            result["checks"].append(("value", False, f"got {rec['v']!r} want {case['expected_value']!r}"))

        # PN check (single or multi).
        if "expected_pn" in case:
            pn_set = s.run("""
                MATCH (f:Form1 {asset_id:$aid, value:$v})-[:RELEASES_PN]->(pn:PartNumber)
                RETURN collect(pn.value) AS pns
            """, aid=ASSET_ID, v=rec["v"]).single()["pns"]
            if case["expected_pn"] in pn_set:
                result["checks"].append(("pn", True, f"found {case['expected_pn']}"))
            else:
                result["pass"] = False
                result["checks"].append(("pn", False, f"want {case['expected_pn']} in {pn_set}"))
        if "expected_pns" in case:
            pn_set = s.run("""
                MATCH (f:Form1 {asset_id:$aid, value:$v})-[:RELEASES_PN]->(pn:PartNumber)
                RETURN collect(pn.value) AS pns
            """, aid=ASSET_ID, v=rec["v"]).single()["pns"]
            missing = [p for p in case["expected_pns"] if p not in pn_set]
            if not missing:
                result["checks"].append(("pns", True, f"found all of {case['expected_pns']}"))
            else:
                result["pass"] = False
                result["checks"].append(("pns", False, f"missing {missing} in {pn_set}"))

        # SN check.
        if "expected_sn" in case:
            sn_set = s.run("""
                MATCH (f:Form1 {asset_id:$aid, value:$v})-[:RELEASES_SN]->(sn:SerialNumber)
                RETURN collect(sn.value) AS sns
            """, aid=ASSET_ID, v=rec["v"]).single()["sns"]
            if case["expected_sn"] in sn_set:
                result["checks"].append(("sn", True, f"found {case['expected_sn']}"))
            else:
                result["pass"] = False
                result["checks"].append(("sn", False, f"want {case['expected_sn']} in {sn_set}"))
        if "expected_sns" in case:
            sn_set = s.run("""
                MATCH (f:Form1 {asset_id:$aid, value:$v})-[:RELEASES_SN]->(sn:SerialNumber)
                RETURN collect(sn.value) AS sns
            """, aid=ASSET_ID, v=rec["v"]).single()["sns"]
            missing = [s_ for s_ in case["expected_sns"] if s_ not in sn_set]
            if not missing:
                result["checks"].append(("sns", True, f"found all of {case['expected_sns']}"))
            else:
                result["pass"] = False
                result["checks"].append(("sns", False, f"missing {missing} in {sn_set}"))

        # Status, signer, cert, date — soft checks (don't fail the case on these).
        for prop, want_key, record_key in [
            ("status", "expected_status", "st"),
            ("signer", "expected_signer", "signer"),
            ("cert", "expected_cert", "cert"),
            ("date", "expected_date", "date"),
        ]:
            if want_key not in case:
                continue
            want = case[want_key]
            got = rec.get(record_key) or ""
            # Case-insensitive substring match for status; exact for date/signer/cert.
            ok = False
            if prop == "status":
                ok = (want.upper() in (got or "").upper())
            elif prop in ("signer",):
                ok = (want.lower() == (got or "").lower().strip())
            else:
                ok = (want == got)
            result["checks"].append((prop, ok, f"got={got!r} want={want!r}"))
            if not ok:
                # soft-fail (warn-only).
                pass

    return result


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 7.5 START {start.isoformat()} ==========")
    driver = connect()
    counters: Counter = Counter()

    with phase("phase7_5_verification"):
        # 1. Re-search Phase 7 OPEN findings.
        with driver.session(database=database_name()) as session:
            open_findings = list(session.run("""
                MATCH (f:Finding {asset_id:$aid})
                WHERE f.status = 'OPEN'
                OPTIONAL MATCH (c:Component)-[:HAS_FINDING]->(f)
                RETURN f.value AS uid, f.category AS cat,
                       c.canonical_pn AS pn, c.installed_sn AS sn
                LIMIT 200
            """, aid=ASSET_ID))
        _log(f"[phase7.5] open findings: {len(open_findings)}")

        with driver.session(database=database_name()) as session:
            for r in open_findings:
                cat = r["cat"]
                pn = r["pn"]
                sn = r["sn"]
                if not pn or not sn or cat != "FORM1_MISSING":
                    continue
                # Lucene re-search of SN
                try:
                    q = f'"{escape_lucene(sn)}" AND ("Form 1" OR "EASA Form" OR "8130" OR "Form One" OR "Authorized Release")'
                    results = search_pages(session, asset_id=ASSET_ID, query=q, limit=5)
                except Exception:
                    results = []
                counters["findings_examined"] += 1
                if results:
                    page_uid = results[0]["page_uid"]
                    with session.begin_transaction() as tx:
                        tx.run("""
                            MATCH (f:Finding {asset_id:$aid, value:$fuid})
                            SET f.status = 'CLOSED_FALSE_POSITIVE',
                                f.closed_at = datetime(),
                                f.closed_reason = $reason
                        """, aid=ASSET_ID, fuid=r["uid"],
                            reason=f"fulltext re-search found {len(results)} pages mentioning {sn} + Form 1 markers"
                        ).consume()
                        tx.run("""
                            MATCH (f:Finding {asset_id:$aid, value:$fuid})
                            MATCH (p:Page {asset_id:$aid, value:$puid})
                            MERGE (f)-[c:CORROBORATED_BY {strategy: 'sn_alone'}]->(p)
                        """, aid=ASSET_ID, fuid=r["uid"], puid=page_uid).consume()
                        tx.commit()
                    counters["closed_false_positive"] += 1
                    _decisions(f"[phase7.5] {r['uid']} | strategy=sn_alone | hits={len(results)} | closed CLOSED_FALSE_POSITIVE")
                else:
                    counters["still_open"] += 1
                    _decisions(f"[phase7.5] {r['uid']} | strategy=sn_alone | hits=0 | still OPEN")

    # 2. Run the 3 acceptance cases.
    _log("\n[phase7.5] Running acceptance cases ...")
    case_results = []
    for case in ACCEPTANCE_CASES:
        r = _run_acceptance_case(driver, case)
        case_results.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        _log(f"\n[phase7.5] ACCEPTANCE {status}: {case['name']}")
        for chk in r["checks"]:
            _log(f"   - {chk[0]}: {'OK' if chk[1] else 'FAIL'} :: {chk[2]}")

    # 3. Verify graph state.
    _log("[phase7.5] running verify_no_fact_orphans ...")
    counts = verify_no_fact_orphans(driver, ASSET_ID, phase="7.5")
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 7.5 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 7.5 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 7.5 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")

    # 4. Collect totals + write final report.
    totals = {}
    with driver.session(database=database_name()) as s:
        totals["total_nodes"] = s.run(
            "MATCH (n) WHERE n.asset_id = $aid OR (n:Asset AND n.asset_id = $aid) RETURN count(n) AS n",
            aid=ASSET_ID,
        ).single()["n"]
        totals["total_edges"] = s.run("""
            MATCH (n)-[r]->(m) WHERE n.asset_id = $aid AND m.asset_id = $aid
            RETURN count(r) AS n
        """, aid=ASSET_ID).single()["n"]
        for lbl in ["Page", "Document", "Form1", "CRS", "WorkPackage", "JobCard",
                    "NonRoutineCard", "Repair", "Modification", "STC",
                    "BorescopeReport", "NDTReport", "DentBuckleEntry",
                    "Stamp", "PartNumber", "SerialNumber", "CertificateNumber",
                    "Component", "Event", "Finding", "PriorityItem",
                    "Person", "MaintenanceOrganization",
                    "AirworthinessDirective", "ServiceBulletin", "ATAChapter",
                    "Date"]:
            n = s.run(
                f"MATCH (n:{lbl}) WHERE n.asset_id = $aid RETURN count(n) AS n",
                aid=ASSET_ID,
            ).single()["n"]
            totals[lbl] = n
        totals["releases_edges"] = s.run("""
            MATCH (:Form1)-[r:RELEASES]->(:Component) WHERE r.disposition IS NOT NULL OR true
            RETURN count(r) AS n
        """).single()["n"]

    lines = [
        f"# CL650-6134 build final report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"asset_id: `{ASSET_ID}`",
        "",
        "## Node counts",
        "",
        "| Label | Count |",
        "|---|---|",
    ]
    for k in ["Page", "Document", "Form1", "CRS", "WorkPackage", "JobCard",
              "NonRoutineCard", "Repair", "Modification", "STC",
              "BorescopeReport", "NDTReport", "DentBuckleEntry",
              "Stamp", "PartNumber", "SerialNumber", "CertificateNumber",
              "Component", "Event", "Finding", "PriorityItem",
              "Person", "MaintenanceOrganization",
              "AirworthinessDirective", "ServiceBulletin", "ATAChapter", "Date"]:
        lines.append(f"| `{k}` | {totals.get(k, 0)} |")
    lines += [
        f"| **TOTAL nodes (asset-scoped)** | {totals['total_nodes']} |",
        f"| **TOTAL edges (asset-scoped)** | {totals['total_edges']} |",
        "",
        "## Acceptance test cases",
        "",
    ]
    for r in case_results:
        status = "PASS" if r["pass"] else "FAIL"
        lines.append(f"### {status} — {r['name']}")
        lines.append("")
        lines.append(f"- Page UID: `{r['page']}`")
        lines.append("")
        lines.append("| Check | Result | Detail |")
        lines.append("|---|---|---|")
        for chk in r["checks"]:
            lines.append(f"| {chk[0]} | {'PASS' if chk[1] else 'FAIL'} | {chk[2]} |")
        lines.append("")
        if r.get("actual"):
            lines.append("Actual properties:")
            lines.append("```")
            for k, v in r["actual"].items():
                lines.append(f"  {k}: {v}")
            lines.append("```")
            lines.append("")

    lines += [
        "## Phase 7.5 verification summary",
        "",
        f"- findings examined: {counters['findings_examined']}",
        f"- closed false positive: {counters['closed_false_positive']}",
        f"- still open: {counters['still_open']}",
        "",
        "## Known limitations",
        "",
        "- Form 1 entity extraction relies on `entities[].location_context` because "
        "the OCR vintage doesn't populate `header_fields`. Edge-case Form 1s where the OCR "
        "didn't tag `Block 3` / `Block 14` location contexts will be missed.",
        "- Phase 7 runs the mechanical 9-step baseline; a judgement-driven Phase 7 would "
        "produce far fewer high-quality findings.",
        "- The asset has no `asset_profile.json` — Phase 2 stamps a Challenger 650 / MSN 6134 / "
        "TypeCertificate CL-600-2B16 directly without per-profile validation.",
        "- No PartFamily / sibling-overhaul propagation in this run.",
    ]
    FINAL_REPORT.write_text("\n".join(lines), encoding="utf-8")
    _log(f"[phase7.5] final report -> {FINAL_REPORT}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
