"""Phase 10 — Graph export (lossless JSON + restore.cypher + tier_views.cypher).

Produces three files in the workdir:

  1. ``graph_export.json``  — viz-shape projection, fed to asset_graph.html
  2. ``restore.cypher``     — replayable into any Neo4j Community via
                              ``cypher-shell -f restore.cypher``
  3. ``tier_views.cypher``  — saved Cypher snippets for Browser favourites
                              (one query per ATA-derived tier)

This is a Coding phase — pure Cypher reads + APOC export. No judgement.
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
    raise RuntimeError("phase10.py: could not locate graph_dal")


_bootstrap_graph_dal()

from graph_dal import connect, database_name              # noqa: E402
from graph_dal.export import (                            # noqa: E402
    export_asset, export_edges, export_events_by_component,
    export_findings_by_component, export_lease_return_state,
    export_mandatory_checklist, export_nodes,
    export_priority_items, export_restore_cypher,
    export_stats, sanitize_restore_cypher,
)
from graph_dal.verify import verify_no_fact_orphans       # noqa: E402


# ATA → Tier rollup. Same mapping as `phases/tiers_and_ata.md`. Used to
# generate `tier_views.cypher` and to colour-code components in the viz.
TIER_QUERIES = {
    "ENGINE":        "c.ata_chapter STARTS WITH '7'",
    "PROPELLER":     "c.ata_chapter = '61'",
    "ROTOR_SYSTEM":  "c.ata_chapter IN ['62', '64', '66', '67']",
    "TRANSMISSION":  "c.ata_chapter IN ['63', '65']",
    "LANDING_GEAR":  "c.ata_chapter = '32'",
    "AIRFRAME":      "c.ata_chapter IN ['51', '52', '53', '54', '55', '56', '57']",
    "AVIONICS":      "c.ata_chapter IN ['22', '23', '27', '31', '34', '45']",
    "SYSTEMS":       "c.ata_chapter IN ['21', '24', '26', '28', '29', '30', '33', '35', '36', '38']",
    "INTERIOR":      "c.ata_chapter = '25'",
    "APU":           "c.ata_chapter = '49'",
}


def _make_tier_views_cypher(asset_id: str) -> str:
    out: list[str] = [
        f"// Sparengine — saved Cypher views per tier for asset {asset_id}",
        "// Drop these into Neo4j Browser favourites for one-click drilldowns.",
        "// Tier definitions follow phases/tiers_and_ata.md.",
        "",
    ]
    for tier, predicate in TIER_QUERIES.items():
        out.append(f"// :{tier}")
        out.append(f"// MATCH (c:Component {{asset_id: '{asset_id}'}}) "
                   f"WHERE {predicate} "
                   f"OPTIONAL MATCH (c)-[:HAS_FINDING]->(f:Finding) "
                   f"OPTIONAL MATCH (e:Event)-[:AFFECTED]->(c) "
                   f"RETURN c, f, e LIMIT 200;")
        out.append("")
    return "\n".join(out)


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
            raise RuntimeError("phase10: cannot determine asset_id")
        print(f"phase10: asset_id={asset_id}", flush=True)

        # 1. graph_export.json — viz-shape projection
        graph_data: dict = {}
        with driver.session(database=database_name()) as s:
            graph_data["asset"]               = export_asset(s, asset_id)
            graph_data["stats"]               = export_stats(s, asset_id)
            # No LIMIT — the dossier is in the few-thousand-node range and a
            # truncated export silently drops Persons / Events / PriorityItems
            # that fall beyond the cap.
            graph_data["nodes"]               = export_nodes(s, asset_id)
            graph_data["edges"]               = export_edges(s, asset_id)
            graph_data["events"]              = export_events_by_component(s, asset_id)
            graph_data["findings"]            = export_findings_by_component(s, asset_id)
            graph_data["priority_items"]      = export_priority_items(s, asset_id)
            graph_data["mandatory_checklist"] = export_mandatory_checklist(s, asset_id)
            graph_data["lease_return_state"]  = export_lease_return_state(s, asset_id)

        # Neo4j returns its own DateTime/Date types from datetime() calls;
        # convert them to ISO strings for JSON.
        def _json_default(o):
            iso = getattr(o, "iso_format", None) or getattr(o, "isoformat", None)
            if callable(iso):
                return iso()
            return str(o)

        export_path = workdir / "graph_export.json"
        export_path.write_text(
            json.dumps(graph_data, indent=2, default=_json_default),
            encoding="utf-8",
        )
        export_size = export_path.stat().st_size

        # 2. restore.cypher via APOC. APOC writes into the neo4j-import
        # volume (which is shared into this container at /app/neo4j-import
        # via docker-compose). After APOC writes, sanitize the schema-
        # creation lines (the schema is owned by phases/schema.cypher)
        # and copy the result into the asset workdir as `restore.cypher`.
        # APOC writes into /import (neo4j-import volume); sparengine sees
        # the same volume read-only at /app/neo4j-import. We then read +
        # sanitise + write the cleaned copy directly into the workdir
        # (sparengine-csvs volume, writable as `node`).
        restore_filename = f"restore-{asset_id}.cypher"
        shared_path = Path("/app/neo4j-import") / restore_filename
        workdir_path = workdir / "restore.cypher"
        commented_lines = 0
        try:
            with driver.session(database=database_name()) as s:
                export_restore_cypher(s, asset_id, restore_filename)
            if shared_path.exists():
                commented_lines = sanitize_restore_cypher(shared_path, workdir_path)
                restore_status = (
                    f"OK: {workdir_path.stat().st_size} bytes, "
                    f"{commented_lines} schema lines commented"
                )
            else:
                restore_status = (
                    f"FAILED: APOC reported success but {shared_path} not "
                    f"visible. Check neo4j-import volume mount in compose."
                )
        except Exception as e:
            restore_status = f"FAILED: {e}"

        # 3. tier_views.cypher
        tier_path = workdir / "tier_views.cypher"
        tier_path.write_text(_make_tier_views_cypher(asset_id), encoding="utf-8")

        # 4. Apply captions.cypher — adds a `name` property to every node so
        # Neo4j Browser auto-captions render meaningfully (otherwise Browser
        # falls back alphabetically to `asset_id`, which is the same on
        # every node). Idempotent. We read the file from the sparengine
        # tree and execute statements one-by-one over Bolt, which avoids
        # the `apoc.cypher.runFile` route (would require staging the file
        # in /import).
        captions_status = "skipped"
        captions_applied = 0
        # Locate the captions.cypher next to schema.cypher in the
        # sparengine-export tree.
        for parent in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
            cand = parent / "sparengine-export" / "phases" / "captions.cypher"
            if cand.is_file():
                captions_file = cand
                break
        else:
            captions_file = None

        if captions_file:
            try:
                raw = captions_file.read_text(encoding="utf-8")
                # Strip comments and split on `;`. captions.cypher uses //
                # line comments and one statement per block.
                cleaned: list[str] = []
                for line in raw.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("//") or not stripped:
                        continue
                    cleaned.append(line)
                blob = "\n".join(cleaned)
                statements = [s.strip() for s in blob.split(";") if s.strip()]
                with driver.session(database=database_name()) as s:
                    for stmt in statements:
                        s.run(stmt).consume()
                        captions_applied += 1
                captions_status = f"OK: {captions_applied} statements"
            except Exception as e:
                captions_status = f"FAILED: {e}"

        # MANDATORY VERIFICATION
        verify_counts = verify_no_fact_orphans(driver, asset_id, phase="10")

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 10 verification ==\n")
            f.write(f"- graph_export.json size                   : {export_size}\n")
            f.write(f"- nodes in export                          : {len(graph_data['nodes'])}\n")
            f.write(f"- edges in export                          : {len(graph_data['edges'])}\n")
            f.write(f"- events keyed by component                : "
                    f"{sum(len(v) for v in graph_data['events'].values())}\n")
            f.write(f"- findings keyed by component              : "
                    f"{sum(len(v) for v in graph_data['findings'].values())}\n")
            f.write(f"- priority_items                           : "
                    f"{len(graph_data['priority_items'])}\n")
            f.write(f"- mandatory_checklist items                : "
                    f"{len(graph_data['mandatory_checklist'])}\n")
            f.write(f"- restore.cypher                           : {restore_status}\n")
            f.write(f"- tier_views.cypher                        : "
                    f"{tier_path.stat().st_size} bytes\n")
            f.write(f"- captions.cypher applied                  : {captions_status}\n")
            f.write(f"- fact_nodes_no_evidence                   : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")

        print(
            f"phase10: OK — graph_export.json={export_size}b  "
            f"nodes={len(graph_data['nodes'])}  edges={len(graph_data['edges'])}  "
            f"orphans={verify_counts['fact_nodes_no_evidence']}",
            flush=True,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
