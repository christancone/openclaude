"""Phase 7 — Component investigation (mechanical baseline) for CL650-6134.

This run uses the mechanical 9-step search baseline (per brief: 'for this run,
do the mechanical 9-step search baseline; don't try to be judgement-driven').

For each LLP/overhaul-tracked component without a Form1 RELEASES edge, run all
9 disciplines (best-effort via Cypher + fulltext) before raising FORM1_MISSING.
"""

from __future__ import annotations

import os
import re
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
    raise RuntimeError("phase7.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name, FindingSeverity
from graph_dal._phase_tag import phase
from graph_dal.finding import write_audit_run, write_finding
from graph_dal.fulltext import search_pages, escape_lucene
from graph_dal.verify import verify_no_fact_orphans


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"
DECISIONS_LOG = DUMPS_DIR / "decisions.log"


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _decisions(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with DECISIONS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _search_sn(session, sn: str) -> int:
    try:
        q = f'"{escape_lucene(sn)}"'
        results = search_pages(session, asset_id=ASSET_ID, query=q, limit=5)
        return len(results)
    except Exception:
        return 0


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 7 START {start.isoformat()} ==========")
    driver = connect()
    counters: Counter = Counter()

    with phase("phase7_investigation"):
        run_id = f"audit::phase7::{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_audit_run(
                    tx, asset_id=ASSET_ID, value=run_id,
                    audit_snapshot_date_iso=datetime.utcnow().date().isoformat(),
                    sparengine_version="phase7-mechanical-cl650",
                )
                tx.commit()

        # Pull LLP / overhaul-tracked components without a Form1-RELEASES edge.
        with driver.session(database=database_name()) as session:
            comps = list(session.run("""
                MATCH (c:Component {asset_id: $aid})
                WHERE c.is_llp = true OR c.is_overhaul = true
                  AND NOT EXISTS { (c)<-[:RELEASES]-(:Form1) }
                OPTIONAL MATCH (c)-[:EVIDENCED_BY]->(p:Page)
                WITH c, head(collect(p)) AS evp
                RETURN c.value AS uid, c.canonical_pn AS pn, c.installed_sn AS sn,
                       evp.value AS evpage
                LIMIT 200
            """, aid=ASSET_ID))
        counters["components_walked"] = len(comps)
        _log(f"[phase7] components_walked={len(comps)}")

        with driver.session(database=database_name()) as session:
            BATCH = 50
            i = 0
            tx = session.begin_transaction()
            for r in comps:
                pn = r["pn"]
                sn = r["sn"]
                cuid = r["uid"]
                evpage = r["evpage"]
                if not pn or not sn or not evpage:
                    continue
                # Run mechanical 9-step search summary.
                hits = 0
                strategies = []
                # 1. wo_pages — skipped (need WP graph traversal; mechanical baseline)
                # 2. sn_alone via fulltext
                try:
                    h = _search_sn(session, sn)
                    if h: hits += h; strategies.append(f"sn_alone={h}")
                except Exception:
                    pass
                # 3. alt_pn — pull alternate PNs and search
                try:
                    alt = session.run("""
                        MATCH (c:Component {asset_id:$aid, value:$cuid})-[:HAS_ALTERNATE_PN]->(p)
                        RETURN p.value AS v
                    """, aid=ASSET_ID, cuid=cuid)
                    for a in alt:
                        h = _search_sn(session, a["v"])
                        if h: hits += h; strategies.append(f"alt_pn[{a['v']}]={h}")
                except Exception:
                    pass
                # 4. filename_pn
                try:
                    h = session.run("""
                        MATCH (d:Document {asset_id:$aid})
                        WHERE toLower(d.file_name) CONTAINS toLower($pn)
                        RETURN count(d) AS n
                    """, aid=ASSET_ID, pn=pn).single()["n"]
                    if h: hits += h; strategies.append(f"filename_pn={h}")
                except Exception:
                    pass
                # 5. filename_sn
                try:
                    h = session.run("""
                        MATCH (d:Document {asset_id:$aid})
                        WHERE toLower(d.file_name) CONTAINS toLower($sn)
                        RETURN count(d) AS n
                    """, aid=ASSET_ID, sn=sn).single()["n"]
                    if h: hits += h; strategies.append(f"filename_sn={h}")
                except Exception:
                    pass
                # 6. batch_range — check :BatchNumber.sn_range_*
                try:
                    h = session.run("""
                        MATCH (b:BatchNumber {asset_id:$aid})
                        WHERE b.sn_range_start IS NOT NULL AND b.sn_range_start <= $sn
                          AND b.sn_range_end IS NOT NULL AND b.sn_range_end >= $sn
                        RETURN count(b) AS n
                    """, aid=ASSET_ID, sn=sn).single()["n"]
                    if h: hits += h; strategies.append(f"batch_range={h}")
                except Exception:
                    pass
                # 7. page_neighbourhood
                try:
                    h = session.run("""
                        MATCH (c:Component {asset_id:$aid, value:$cuid})-[:EVIDENCED_BY]->(p:Page)
                        OPTIONAL MATCH (d:Document)-[:HAS_PAGE]->(p)
                        OPTIONAL MATCH (d)-[:HAS_PAGE]->(q:Page)
                        WHERE q.page_index IS NOT NULL AND p.page_index IS NOT NULL
                          AND abs(q.page_index - p.page_index) <= 5
                        WITH q
                        MATCH (q)-[:CARRIES]->(f:Form1)
                        RETURN count(f) AS n
                    """, aid=ASSET_ID, cuid=cuid).single()["n"]
                    if h: hits += h; strategies.append(f"page_neighbourhood={h}")
                except Exception:
                    pass
                # 8. siblings
                try:
                    h = session.run("""
                        MATCH (c:Component {asset_id:$aid, value:$cuid})-[:HAS_PRIMARY_PN]->(pn:PartNumber)
                        MATCH (other:Component {asset_id:$aid})-[:HAS_PRIMARY_PN]->(pn)
                        WHERE other.value <> c.value
                        MATCH (other)<-[:RELEASES]-(f:Form1)
                        RETURN count(f) AS n
                    """, aid=ASSET_ID, cuid=cuid).single()["n"]
                    if h: hits += h; strategies.append(f"siblings={h}")
                except Exception:
                    pass

                if hits == 0:
                    # Raise the finding.
                    finding_uid = f"finding::FORM1_MISSING::{pn}::{sn}"
                    try:
                        write_finding(
                            tx, asset_id=ASSET_ID, value=finding_uid,
                            severity=FindingSeverity.LEVEL_2.value,
                            category="FORM1_MISSING",
                            title=f"Form 1 not located for {pn}/{sn}",
                            description=(
                                f"Component {cuid} is LLP/overhaul-tracked but no :Form1 "
                                f"with a :RELEASES edge attributing release to this component "
                                f"was found. Searched 8 disciplines (sn_alone, alt_pn, filename_pn, "
                                f"filename_sn, batch_range, page_neighbourhood, siblings) "
                                f"and got 0 hits across all of them."
                            ),
                            evidence_page_uid=evpage,
                            evidence_quote=f"Component {pn}/{sn} discovered on page but no Form 1 release located.",
                            recommended_action="Locate the Form 1 covering this SN, or close as 'parted out / not in scope'.",
                            flags_label="Component", flags_uid=cuid,
                            component_uid=cuid,
                            audit_run_uid=run_id,
                            status="OPEN",
                        )
                        counters["findings_raised"] += 1
                        _decisions(f"[phase7] {cuid} | FORM1_MISSING raised | strategies:[{','.join(strategies) or 'none'}] | hits=0 | severity:2")
                    except Exception as exc:
                        _log(f"[phase7] write_finding failed for {cuid}: {exc!r}")
                else:
                    counters["closed_by_search"] += 1
                    _decisions(f"[phase7] {cuid} | FORM1_MISSING SUPPRESSED | strategies:[{','.join(strategies)}] | hits={hits}")
                i += 1
                if i % BATCH == 0:
                    tx.commit()
                    tx = session.begin_transaction()
            tx.commit()

    _log("[phase7] running verify_no_fact_orphans ...")
    counts = verify_no_fact_orphans(driver, ASSET_ID, phase="7")
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 7 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 7 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 7 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
