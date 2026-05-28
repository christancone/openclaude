"""Phase 6.5 — Critical items + lease-return state for CL650-6134.

- LLP components → :PriorityItem
- AD compliance gaps → :PriorityItem (level 1)
- SB compliance gaps → :PriorityItem (level 2/3)
- :Asset.lease_return_state derived from redelivery / delivery_acceptance docs.
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
    raise RuntimeError("phase6_5.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name
from graph_dal._phase_tag import phase
from graph_dal.finding import write_priority_item
from graph_dal.verify import verify_no_fact_orphans


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
    _log(f"\n========== PHASE 6.5 START {start.isoformat()} ==========")
    driver = connect()
    counters: Counter = Counter()

    with phase("phase6_5_critical_items"):
        # 1. LLPs.
        with driver.session(database=database_name()) as s:
            llps = list(s.run("""
                MATCH (c:Component {asset_id: $aid})
                WHERE c.is_llp = true
                RETURN c.value AS uid, c.canonical_pn AS pn, c.installed_sn AS sn,
                       c.life_limit AS life_limit, c.tsn AS tsn
            """, aid=ASSET_ID))
        counters["llp_components"] = len(llps)

        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for r in llps:
                    urgency = "informational"
                    if r["life_limit"] and r["tsn"]:
                        remaining = r["life_limit"] - r["tsn"]
                        if remaining < 100: urgency = "immediate"
                        elif remaining < 500: urgency = "within_30d"
                        elif remaining < 1000: urgency = "within_90d"
                    write_priority_item(
                        tx, asset_id=ASSET_ID,
                        value=f"priority::llp::{r['pn']}::{r['sn']}",
                        kind="llp_review",
                        title=f"LLP {r['pn']}/{r['sn']} requires review",
                        description=f"Component {r['uid']} is flagged is_llp=true. " +
                                   (f"TSN={r['tsn']}, life_limit={r['life_limit']}." if r["tsn"] else "Current TSN unknown — pull LLP status sheet."),
                        urgency=urgency, component_uid=r["uid"],
                    )
                    counters[f"urgency_{urgency}"] += 1
                tx.commit()

        # 2. AD gaps.
        with driver.session(database=database_name()) as s:
            ad_gaps = list(s.run("""
                MATCH (ad:AirworthinessDirective {asset_id: $aid})
                WHERE NOT EXISTS { (:Document)-[:COMPLIES_WITH]->(ad) }
                  AND NOT EXISTS { (:WorkPackage)-[:COMPLIES_WITH]->(ad) }
                RETURN ad.value AS v LIMIT 100
            """, aid=ASSET_ID))
        counters["ad_gaps"] = len(ad_gaps)

        # PriorityItem doesn't require evidence_page — it's not fact-bearing.
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for r in ad_gaps:
                    ad_v = r["v"]
                    write_priority_item(
                        tx, asset_id=ASSET_ID,
                        value=f"priority::ad_gap::{ad_v}",
                        kind="ad_compliance_gap",
                        title=f"AD {ad_v} compliance not verified",
                        description=f"No :Document or :WorkPackage carries a :COMPLIES_WITH edge to AD {ad_v}. Verify compliance documentation.",
                        urgency="within_30d",
                    )
                    counters["priority_ad_gap"] += 1
                tx.commit()

        # 3. Lease-return state.
        with driver.session(database=database_name()) as s:
            redelivery = s.run("""
                MATCH (d:Document {asset_id: $aid})
                WHERE d.document_type IN ['redelivery_condition_report',
                                           'delivery_acceptance_certificate']
                RETURN count(d) AS n
            """, aid=ASSET_ID).single()["n"]
        state = "redelivery_active" if redelivery > 0 else "in_service"
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                tx.run("""
                    MATCH (a:Asset {asset_id: $aid})
                    SET a.lease_return_state = $s, a.lease_return_signal_count = $n
                """, aid=ASSET_ID, s=state, n=redelivery).consume()
                tx.commit()
        counters["lease_return_state"] = state
        counters["lease_return_signal_count"] = redelivery

    _log("[phase6.5] running verify_no_fact_orphans ...")
    counts = verify_no_fact_orphans(driver, ASSET_ID, phase="6.5")
    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 6.5 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 6.5 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 6.5 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
