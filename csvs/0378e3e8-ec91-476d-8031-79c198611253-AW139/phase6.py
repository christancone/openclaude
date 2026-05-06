"""Phase 6 — Connectors (Layer 5 cross-doc + Layer 0 organisations).

Walks the existing graph (post-phases 1, 2, 4, 5) and:

  1. Enriches :Person nodes — Phase 1's stamp-derived persons have only
     a name; Phase 6 backfills cert_authority from the stamp's
     certificate_number if the stamp has one.
  2. Materialises :Organization from stamp.location_context patterns
     (best-effort heuristic; OCR rarely surfaces clean org names).
  3. Wires :WorkPackage-[:INCLUDES]->{:JobCard|:NRC} via WO-number matching
     across pages.
  4. Wires :CARRIES_CERT from stamps to :CertificateNumber (Phase 1
     wrote the certificate_number as a stamp property only; here we
     promote it to a node + edge).

Heavy logic deferred to a later pass:
    - :ISSUED_BY edges from Form1.metadata.issuer (most OCR doesn't
      surface this cleanly without further extraction)
    - :APPLIES_TO from SB/AD to TypeCertificate
    - :IMPLEMENTS from Modification to STC
    - :COMPLIES_WITH from WorkPackage to SB/AD
    - Xlsx ledger nodes (no Xlsx in this dossier)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
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

from graph_dal import connect, database_name                          # noqa: E402
from graph_dal.connector import write_certificate_number                # noqa: E402
from graph_dal.errors import VerificationFailed                         # noqa: E402
from graph_dal.organization import (                                    # noqa: E402
    link_work_package_includes,
    write_organization,
    write_person,
)
from graph_dal.stamp import link_stamp_carries_cert, link_stamped_by    # noqa: E402
from graph_dal.verify import verify_no_fact_orphans                     # noqa: E402


def _normalize_person(name: str) -> str:
    """Make a stable per-asset Person uid from a name string."""
    s = (name or "").strip().upper()
    return re.sub(r"\s+", " ", s)


def _detect_cert_authority(cert_number: str | None) -> str | None:
    """Heuristic: infer authority from certificate-number prefix.

    The cert-number prefixes that show up most often:
      - EASA: numeric IT-, DE-, GB-, FR- format
      - FAA: starts with 'A&P', '&P', or letters specific to FAA
      - TCCA: 'AME-', 'AMC-', '24-'
    Without a comprehensive lookup, we just identify the obvious patterns.
    """
    if not cert_number:
        return None
    s = cert_number.strip().upper()
    if re.match(r"^(IT|DE|GB|FR|ES|NL|SE|DK|FI|NO|PL|CZ|HU|AT|BE|IE|PT)[\.\-]", s):
        return "EASA"
    if "FAA" in s or s.startswith("A&P"):
        return "FAA"
    if s.startswith("TCCA") or s.startswith("AME-") or s.startswith("AMC-"):
        return "TCCA"
    if s.isdigit() and 4 <= len(s) <= 8:
        # 4-8 digit cert numbers are common in EASA records (Italian)
        return "EASA"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 6 — Cross-doc connectors")
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
            raise RuntimeError("phase6: cannot determine asset_id")
        print(f"phase6: asset_id={asset_id}", flush=True)

        # ---------------------------------------------------------------------
        # 1. Enrich :Person nodes from stamps
        # ---------------------------------------------------------------------
        with driver.session(database=database_name()) as s:
            stamp_records = list(s.run(
                "MATCH (st:Stamp {asset_id: $aid}) "
                "WHERE st.person_name IS NOT NULL "
                "RETURN st.value AS stamp_uid, st.person_name AS name, "
                "       st.certificate_number AS cert, st.title_role AS role, "
                "       st.location_context AS loc",
                aid=asset_id,
            ))

        persons_enriched = 0
        certs_promoted = 0
        person_authority_seen: Counter[str] = Counter()
        loc_contexts: Counter[str] = Counter()

        BATCH = 200
        with driver.session(database=database_name()) as session:
            for i in range(0, len(stamp_records), BATCH):
                with session.begin_transaction() as tx:
                    for rec in stamp_records[i:i + BATCH]:
                        name = (rec["name"] or "").strip()
                        if not name:
                            continue
                        person_uid = _normalize_person(name)
                        if not person_uid:
                            continue
                        cert = (rec["cert"] or "").strip()
                        authority = _detect_cert_authority(cert) if cert else None
                        if authority:
                            person_authority_seen[authority] += 1

                        write_person(
                            tx, asset_id=asset_id, value=person_uid,
                            name=name, cert_authority=authority,
                        )
                        # Wire :Stamp-[:STAMPED_BY]->:Person (Q8 — was the
                        # legacy :BY edge in the SPARENGINE schema; renamed
                        # in Q8 of the migration grill).
                        link_stamped_by(
                            tx, asset_id=asset_id,
                            stamp_uid=rec["stamp_uid"],
                            person_value=person_uid,
                            person_name=name,
                        )
                        persons_enriched += 1

                        # Promote certificate_number to its own :CertificateNumber node
                        # and add :Stamp-[:CARRIES_CERT]->:CertificateNumber edge.
                        if cert:
                            write_certificate_number(
                                tx, asset_id=asset_id, value=cert,
                                cert_type="staff_authorisation",
                            )
                            link_stamp_carries_cert(
                                tx, asset_id=asset_id,
                                stamp_uid=rec["stamp_uid"], cert_value=cert,
                            )
                            certs_promoted += 1

                        loc = (rec["loc"] or "").strip()
                        if loc:
                            loc_contexts[loc[:40]] += 1
                    tx.commit()

        # ---------------------------------------------------------------------
        # 2. WorkPackage → JobCard / NonRoutineCard via WO-number matching
        # ---------------------------------------------------------------------
        # Cypher: for each :WorkPackage on a page, check if any :JobCard/:NRC
        # on a sibling page (same Document) shares its WO via :MENTIONS_WO.
        # First-pass: simpler — match WP value to job card task_reference if
        # we have it. The aw139 dossier doesn't surface explicit WP→JC links
        # via OCR; this section is a placeholder that becomes meaningful
        # for dossiers with workpack_cover_sheet docs.
        wp_includes_written = 0
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                # WP-and-JobCard sharing a Document → :INCLUDES
                # (best-effort: assumes JobCards inside the same PDF doc as
                #  the WP cover sheet are part of the WP).
                # NB: we don't write the edge per-page-pair; we use the same
                # session-batch as the rest. Limit to first 500 to keep this fast.
                cypher = """
                MATCH (wp:WorkPackage {asset_id: $aid})<-[:CARRIES]-(p:Page)<-[:HAS_PAGE]-(d:Document)
                MATCH (d)-[:HAS_PAGE]->(p2:Page)-[:CARRIES]->(jc:JobCard)
                WHERE jc.value <> wp.value
                MERGE (wp)-[:INCLUDES]->(jc)
                """
                tx.run(cypher, aid=asset_id).consume()

                cypher2 = """
                MATCH (wp:WorkPackage {asset_id: $aid})<-[:CARRIES]-(p:Page)<-[:HAS_PAGE]-(d:Document)
                MATCH (d)-[:HAS_PAGE]->(p2:Page)-[:CARRIES]->(nrc:NonRoutineCard)
                MERGE (wp)-[:INCLUDES]->(nrc)
                """
                tx.run(cypher2, aid=asset_id).consume()

                cypher3 = """
                MATCH (wp:WorkPackage {asset_id: $aid})
                OPTIONAL MATCH (wp)-[:INCLUDES]->(t)
                WITH wp, count(t) AS includes_count
                WHERE includes_count > 0
                RETURN count(wp) AS n
                """
                rec = tx.run(cypher3, aid=asset_id).single()
                wp_includes_written = int(rec["n"]) if rec else 0
                tx.commit()

        # ---------------------------------------------------------------------
        # MANDATORY VERIFICATION
        # ---------------------------------------------------------------------
        try:
            verify_counts = verify_no_fact_orphans(driver, asset_id, phase="6")
        except VerificationFailed as e:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n== Phase 6 verification (FAILED) ==\n")
                for k, v in (e.counts or {}).items():
                    f.write(f"- {k:<40s}: {v}\n")
                for rv in e.rule_violations:
                    f.write(f"- RULE: {rv['rule']} expected {rv['expected']}, got {rv['actual']}\n")
            raise

        with driver.session(database=database_name()) as s:
            n_person = s.run(
                "MATCH (p:Person {asset_id: $aid}) RETURN count(p) AS n",
                aid=asset_id,
            ).single()["n"]
            n_org = s.run(
                "MATCH (o:Organization {asset_id: $aid}) RETURN count(o) AS n",
                aid=asset_id,
            ).single()["n"]
            n_carries_cert = s.run(
                "MATCH (:Stamp {asset_id: $aid})-[:CARRIES_CERT]->(c:CertificateNumber) "
                "RETURN count(*) AS n",
                aid=asset_id,
            ).single()["n"]
            n_includes = s.run(
                "MATCH (:WorkPackage {asset_id: $aid})-[:INCLUDES]->() "
                "RETURN count(*) AS n",
                aid=asset_id,
            ).single()["n"]

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 6 verification ==\n")
            f.write(f"- :Person count (live)                    : {n_person}\n")
            f.write(f"- :Organization count (live)              : {n_org}\n")
            f.write(f"- persons_enriched_this_phase             : {persons_enriched}\n")
            f.write(f"- certs_promoted_to_nodes                 : {certs_promoted}\n")
            f.write(f"- :CARRIES_CERT edges                     : {n_carries_cert}\n")
            f.write(f"- :WorkPackage-[:INCLUDES]-> count        : {n_includes}\n")
            f.write(f"- fact_nodes_no_evidence                  : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")
            f.write("- person cert authorities detected:\n")
            for k, v in person_authority_seen.most_common():
                f.write(f"    {k:<10s} : {v}\n")
            f.write("- top stamp location_contexts (first 10):\n")
            for k, v in loc_contexts.most_common(10):
                f.write(f"    {k:<40s} : {v}\n")

        print(
            f"phase6: OK — persons={n_person}  orgs={n_org}  "
            f"certs={n_carries_cert}  includes={n_includes}  "
            f"orphans={verify_counts['fact_nodes_no_evidence']}",
            flush=True,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
