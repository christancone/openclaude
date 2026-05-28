"""Phase 6 — Cross-doc connectors for CL650-6134.

- Enrich :Person nodes from stamp data + wire :STAMPED_BY edges.
- Promote stamp certificate_number to :CertificateNumber nodes and wire :CARRIES_CERT.
- Wire :WorkPackage-[:INCLUDES]->:JobCard|:NRC|:CRS|:Form1 via same-document chain.
- PN alias graph (ocr_variant + incomplete_pn ONLY — no vendor_oem_co_mention).
- SN alias graph (sn_ocr_separator + sn_zero_padding).
- Stamp BINDS_TO Form 1 fallback: page has 1 form1 and ≥1 unbound stamp -> BINDS_TO medium.
- Form1 -> Person SIGNED_BY edges.
- Form1 -> MaintenanceOrganization ISSUED_BY when block 4 issuer surfaced.
- Form1 -> ServiceBulletin POST_SB_RELEASE from block_12_mod_status.
- CRS <-> Form1 PAIRED_WITH (already wired in Phase 5 via same page; re-confirm).
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase6.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name, normalize_identifier
from graph_dal._phase_tag import phase
from graph_dal.connector import write_certificate_number
from graph_dal.component import link_part_number_same_as
# link_form1_post_sb_release dropped per simplified Form 1 contract (2026-05-19).
from graph_dal.organization import (
    write_person, write_maintenance_organization,
    link_signed_by, link_issued_by,
)
from graph_dal.stamp import link_stamped_by, link_stamp_carries_cert, link_stamp_binds_to
from graph_dal.verify import verify_no_fact_orphans


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _detect_cert_authority(cert_number: str | None) -> str | None:
    if not cert_number:
        return None
    s = cert_number.strip().upper()
    if s.startswith("EASA"):
        return "EASA"
    if "FAA" in s or s.startswith("A&P"):
        return "FAA"
    if s.startswith("TCCA") or s.startswith("AME-") or s.startswith("AMC-") or s.startswith("BV"):
        return "TCCA"
    if re.match(r"^[A-Z]{2}\d", s):
        return "TCCA"
    return None


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 6 START {start.isoformat()} ==========")
    driver = connect()
    counters: Counter = Counter()

    with phase("phase6_connectors"):
        # 1. Stamp -> Person + cert enrichment.
        with driver.session(database=database_name()) as s:
            stamp_rows = list(s.run("""
                MATCH (st:Stamp {asset_id: $aid})
                WHERE st.person_name IS NOT NULL
                RETURN st.value AS stamp_uid, st.person_name AS name,
                       st.certificate_number AS cert
            """, aid=ASSET_ID))

        BATCH = 200
        with driver.session(database=database_name()) as session:
            for i in range(0, len(stamp_rows), BATCH):
                with session.begin_transaction() as tx:
                    for rec in stamp_rows[i:i+BATCH]:
                        name = (rec["name"] or "").strip()
                        if not name:
                            continue
                        person_uid = re.sub(r"\s+", " ", name).upper()
                        cert = (rec["cert"] or "").strip()
                        authority = _detect_cert_authority(cert) if cert else None
                        write_person(tx, asset_id=ASSET_ID, value=person_uid,
                                     name=name, cert_authority=authority)
                        link_stamped_by(tx, asset_id=ASSET_ID, stamp_uid=rec["stamp_uid"],
                                        person_value=person_uid, person_name=name)
                        counters["stamped_by"] += 1
                        if cert:
                            write_certificate_number(tx, asset_id=ASSET_ID, value=cert,
                                                     cert_type="staff_authorisation")
                            link_stamp_carries_cert(tx, asset_id=ASSET_ID,
                                                    stamp_uid=rec["stamp_uid"], cert_value=cert)
                            counters["carries_cert"] += 1
                    tx.commit()
        _log(f"[phase6] stamped_by={counters['stamped_by']} carries_cert={counters['carries_cert']}")

        # 2. Form1 -> Person SIGNED_BY (from block_13b_name).
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                r = tx.run("""
                    MATCH (f:Form1 {asset_id: $aid})
                    WHERE f.block_13b_name IS NOT NULL AND f.block_13b_name <> ''
                    WITH f, toUpper(trim(f.block_13b_name)) AS pkey, f.block_13b_name AS pname,
                         f.block_13_date AS d
                    MERGE (p:Person {asset_id: $aid, value: pkey})
                    ON CREATE SET p.name = pname, p.created_in_phase = 'phase6_connectors'
                    ON MATCH SET p.name = coalesce(p.name, pname)
                    MERGE (f)-[s:SIGNED_BY]->(p)
                    ON CREATE SET s.block = '14b', s.date = d
                    RETURN count(*) AS n
                """, aid=ASSET_ID).single()
                counters["form1_signed_by"] = r["n"] if r else 0
                tx.commit()
        _log(f"[phase6] form1_signed_by={counters['form1_signed_by']}")

        # 3. Form1 -> MaintenanceOrganization ISSUED_BY  — REMOVED 2026-05-19.
        # Per the simplified Form 1 contract, block_4_issuer_text stays as a
        # property on the Form1 node; no cross-node ISSUED_BY edge is wired.
        # An auditor needing the issuing org reads Form1.block_4_issuer_text
        # directly.

        # 4. Form1 -> ServiceBulletin POST_SB_RELEASE  — REMOVED 2026-05-19.
        # block_12_mod_status stays as a comma-separated string property on
        # the Form1 node; no cross-node POST_SB_RELEASE edge is wired. Mod-
        # status queries: read Form1.block_12_mod_status directly.

        # 5. WorkPackage INCLUDES Form1/CRS/etc.  — REMOVED for Form1 only,
        # because the Form 1 contract is now {CARRIES, RELEASES_PN, RELEASES_SN,
        # SIGNED_BY} and no parent grouping edge. INCLUDES for JobCard / NRC /
        # CRS is kept — those still group under their work package.
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for tgt in ("JobCard", "NonRoutineCard", "CRS"):
                    r = tx.run(f"""
                        MATCH (wp:WorkPackage {{asset_id: $aid}})<-[:CARRIES]-(p1:Page)<-[:HAS_PAGE]-(d:Document)
                        MATCH (d)-[:HAS_PAGE]->(p2:Page)-[:CARRIES]->(t:{tgt})
                        WHERE t.value <> wp.value
                        MERGE (wp)-[:INCLUDES]->(t)
                        RETURN count(*) AS n
                    """, aid=ASSET_ID).single()
                    counters[f"wp_includes_{tgt.lower()}"] = r["n"] if r else 0
                tx.commit()
        _log(f"[phase6] wp_includes (Form1 EXCLUDED per simple contract): "
             f"jc={counters['wp_includes_jobcard']}, "
             f"nrc={counters['wp_includes_nonroutinecard']}, "
             f"crs={counters['wp_includes_crs']}")

        # 6. PN alias graph — ocr_variant (separator/case folding).
        # Build the alias graph in memory then write batched.
        with driver.session(database=database_name()) as s:
            pn_rows = list(s.run(
                "MATCH (p:PartNumber {asset_id: $aid}) RETURN p.value AS v",
                aid=ASSET_ID,
            ))
        canon_to_pns: dict[str, list[str]] = defaultdict(list)
        for r in pn_rows:
            v = r["v"]
            if not v:
                continue
            c = normalize_identifier(v)
            if c:
                canon_to_pns[c].append(v)

        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for canon, pns in canon_to_pns.items():
                    if len(pns) < 2:
                        continue
                    # Pick the longest as primary; alias others to it.
                    primary = max(pns, key=len)
                    for alias in pns:
                        if alias == primary:
                            continue
                        # ALIAS_OF kind = ocr_variant. The DAL doesn't have a generic
                        # ALIAS_OF writer; we use a small inline write here since it's
                        # the canonical pattern documented in form1_edges.py docstring.
                        tx.run("""
                            MATCH (a:PartNumber {asset_id: $aid, value: $a})
                            MATCH (p:PartNumber {asset_id: $aid, value: $p})
                            MERGE (a)-[r:ALIAS_OF]->(p)
                            ON CREATE SET r.kind = 'ocr_variant', r.confidence = 'high',
                                          r.created_in_phase = 'phase6_connectors'
                        """, aid=ASSET_ID, a=alias, p=primary).consume()
                        counters["pn_alias_ocr_variant"] += 1
                tx.commit()
        _log(f"[phase6] pn_alias_ocr_variant={counters['pn_alias_ocr_variant']}")

        # 7. PN incomplete_pn alias: dashless ↔ dashed via shared SN.
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                r = tx.run("""
                    MATCH (a:PartNumber {asset_id: $aid})<-[:HAS_PRIMARY_PN|HAS_ALTERNATE_PN]-(c:Component)-[:HAS_PRIMARY_PN|HAS_ALTERNATE_PN]->(b:PartNumber {asset_id: $aid})
                    WHERE a.value <> b.value
                      AND replace(a.value,'-','') = b.value
                    MERGE (a)-[r:ALIAS_OF]->(b)
                    ON CREATE SET r.kind='incomplete_pn', r.confidence='high',
                                  r.created_in_phase='phase6_connectors'
                    RETURN count(r) AS n
                """, aid=ASSET_ID).single()
                counters["pn_alias_incomplete"] = r["n"] if r else 0
                tx.commit()
        _log(f"[phase6] pn_alias_incomplete={counters['pn_alias_incomplete']}")

        # 8. SN alias graph (sn_ocr_separator).
        with driver.session(database=database_name()) as s:
            sn_rows = list(s.run(
                "MATCH (n:SerialNumber {asset_id: $aid}) RETURN n.value AS v",
                aid=ASSET_ID,
            ))
        canon_to_sns: dict[str, list[str]] = defaultdict(list)
        for r in sn_rows:
            v = r["v"]
            if not v:
                continue
            c = normalize_identifier(v)
            if c:
                canon_to_sns[c].append(v)
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for canon, sns in canon_to_sns.items():
                    if len(sns) < 2:
                        continue
                    primary = max(sns, key=len)
                    for alias in sns:
                        if alias == primary:
                            continue
                        tx.run("""
                            MATCH (a:SerialNumber {asset_id: $aid, value: $a})
                            MATCH (p:SerialNumber {asset_id: $aid, value: $p})
                            MERGE (a)-[r:ALIAS_OF]->(p)
                            ON CREATE SET r.kind = 'sn_ocr_separator', r.confidence = 'high',
                                          r.created_in_phase = 'phase6_connectors'
                        """, aid=ASSET_ID, a=alias, p=primary).consume()
                        counters["sn_alias_separator"] += 1
                tx.commit()
        _log(f"[phase6] sn_alias_separator={counters['sn_alias_separator']}")

        # 9. Stamp BINDS_TO Form 1 fallback  — REMOVED 2026-05-19.
        # The simplified Form 1 contract uses :Form1-[:SIGNED_BY]->:Person
        # directly (wired in section 2 above). Stamp→Form 1 binding is the
        # intermediate that produced SIGNED_BY in the previous build; with
        # SIGNED_BY now derived from page+stamp, no Stamp→Form 1 edge is
        # required at the graph level. (Stamp→Person via :STAMPED_BY is
        # wired in Phase 1 and remains useful.)

    _log("[phase6] running verify_no_fact_orphans ...")
    counts = verify_no_fact_orphans(driver, ASSET_ID, phase="6")
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 6 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 6 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 6 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
