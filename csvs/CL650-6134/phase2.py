"""Phase 2 — Asset detection / confirmation for CL650-6134.

Confirms :Asset properties, adds the secondary class label (Aircraft for this
asset), wires the regulatory layer (:TypeCertificate, :CountryRegistration),
aggregates corpus signals, and raises any CONTEXT_DISCREPANCY findings.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase2.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name, AssetKind
from graph_dal.asset import write_asset, write_country_registration, write_type_certificate
from graph_dal.finding import write_audit_run
from graph_dal._phase_tag import phase
from graph_dal.verify import verify_phase_2


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 2 START {start.isoformat()} ==========")
    driver = connect()
    with phase("phase2_asset_detection"):
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_asset(
                    tx, asset_id=ASSET_ID,
                    asset_kind=AssetKind.AIRCRAFT.value,
                    name="Bombardier Challenger 650",
                    msn="6134",
                    subtype="FIXED_WING_JET",
                )
                # TypeCertificate.
                write_type_certificate(
                    tx, asset_id=ASSET_ID, value="CL-600-2B16 (Challenger 650)",
                    tc_holder="Bombardier Inc.",
                    model_designation="CL-600-2B16",
                    category="CS-25",
                )
                # CountryRegistration unknown — skip writing one (profile has no value).
                tx.commit()

        # AuditRun for Phase 2.
        run_id = f"audit::phase2::{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_audit_run(
                    tx, asset_id=ASSET_ID, value=run_id,
                    audit_snapshot_date_iso=datetime.utcnow().date().isoformat(),
                    sparengine_version="phase2-neo4j-cl650",
                )
                tx.commit()

        # Corpus aggregation.
        with driver.session(database=database_name()) as s:
            top_sn = s.run("""
                MATCH (:Page {asset_id:$aid})-[:MENTIONS_SN]->(sn:SerialNumber)
                RETURN sn.value AS v, count(*) AS n ORDER BY n DESC LIMIT 1
            """, aid=ASSET_ID).single()
            top_pn = s.run("""
                MATCH (:Page {asset_id:$aid})-[:MENTIONS_PN]->(pn:PartNumber)
                RETURN pn.value AS v, count(*) AS n ORDER BY n DESC LIMIT 1
            """, aid=ASSET_ID).single()
            top_doc = s.run("""
                MATCH (d:Document {asset_id:$aid})
                WHERE d.document_type IS NOT NULL
                RETURN d.document_type AS v, count(*) AS n ORDER BY n DESC LIMIT 1
            """, aid=ASSET_ID).single()
            latest_date = s.run("""
                MATCH (d:Date {asset_id:$aid})
                RETURN d.iso AS v ORDER BY d.iso DESC LIMIT 1
            """, aid=ASSET_ID).single()

    _log(f"[phase2] top_sn={top_sn['v'] if top_sn else None} count={top_sn['n'] if top_sn else 0}")
    _log(f"[phase2] top_pn={top_pn['v'] if top_pn else None} count={top_pn['n'] if top_pn else 0}")
    _log(f"[phase2] top_doc_type={top_doc['v'] if top_doc else None} count={top_doc['n'] if top_doc else 0}")
    _log(f"[phase2] latest_date={latest_date['v'] if latest_date else None}")

    _log("[phase2] running verify_phase_2 ...")
    counts = verify_phase_2(driver, ASSET_ID)
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 2 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 2 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
