"""Phase 6.5 — Critical-items selection.

Walks the existing :Component graph and emits :PriorityItem nodes for
LLP components, plus sets ``Asset.lease_return_state`` based on dossier
end-of-life signals (best-effort heuristic: presence of redelivery or
delivery-acceptance documents).

Mixed phase per the brief — threshold detection here is mechanical.
Full ranking by urgency would require an LLM pass.
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
    raise RuntimeError("phase6_5.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

from graph_dal import connect, database_name                # noqa: E402
from graph_dal.finding import write_priority_item           # noqa: E402
from graph_dal.verify import verify_no_fact_orphans         # noqa: E402


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
            raise RuntimeError("phase6_5: cannot determine asset_id")
        print(f"phase6_5: asset_id={asset_id}", flush=True)

        # 1. LLP components → :PriorityItem
        with driver.session(database=database_name()) as s:
            llp_rows = list(s.run(
                "MATCH (c:Component {asset_id: $aid}) "
                "WHERE c.is_llp = true "
                "RETURN c.value AS uid, c.canonical_pn AS pn, c.installed_sn AS sn",
                aid=asset_id,
            ))

        items_written = 0
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for r in llp_rows:
                    pn = r["pn"] or "?"
                    sn = r["sn"] or "?"
                    pi_uid = f"priority::llp::{pn}::{sn}"
                    write_priority_item(
                        tx, asset_id=asset_id, value=pi_uid,
                        kind="llp_review",
                        title=f"LLP {pn}/{sn} requires review",
                        description=(
                            f"Component {r['uid']} is flagged is_llp=true. "
                            f"The dossier should carry a current LLP status sheet "
                            f"with remaining cycles/hours."
                        ),
                        urgency="within_30d",
                        component_uid=r["uid"],
                    )
                    items_written += 1
                tx.commit()

        # 2. Lease-return signal
        with driver.session(database=database_name()) as s:
            redelivery = s.run(
                "MATCH (d:Document {asset_id: $aid}) "
                "WHERE d.document_type IN ['redelivery_condition_report', "
                "                           'delivery_acceptance_certificate'] "
                "RETURN count(d) AS n",
                aid=asset_id,
            ).single()["n"]
        lease_return_state = "redelivery_active" if redelivery > 0 else "in_service"
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                tx.run(
                    "MATCH (a:Asset {asset_id: $aid}) "
                    "SET a.lease_return_state = $s, a.lease_return_signal_count = $n",
                    aid=asset_id, s=lease_return_state, n=redelivery,
                ).consume()
                tx.commit()

        # MANDATORY VERIFICATION
        verify_counts = verify_no_fact_orphans(driver, asset_id, phase="6.5")
        with driver.session(database=database_name()) as s:
            n_pi = s.run(
                "MATCH (p:PriorityItem {asset_id: $aid}) RETURN count(p) AS n",
                aid=asset_id,
            ).single()["n"]

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 6.5 verification ==\n")
            f.write(f"- llp_components_seen        : {len(llp_rows)}\n")
            f.write(f"- :PriorityItem count        : {n_pi}\n")
            f.write(f"- lease_return_state         : {lease_return_state}\n")
            f.write(f"- redelivery doc count       : {redelivery}\n")
            f.write(f"- fact_nodes_no_evidence     : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")

        print(f"phase6_5: OK — priority_items={n_pi}  lease={lease_return_state}  "
              f"orphans={verify_counts['fact_nodes_no_evidence']}", flush=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
