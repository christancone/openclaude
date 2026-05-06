"""Phase 4 — Component discovery (Layer 3 hydration).

Reads :Page-[:MENTIONS_PN]->(:PartNumber) and :Page-[:MENTIONS_SN]->(:SerialNumber)
edges that Phase 1 wrote, plus :COVERS_ATA edges, and produces :Component
nodes for the (PN, SN) pairs that survive the SPARENGINE 8 selection rules.

Rules 1–6 implemented in this pass:
    1. Seed list   — :Component for every entry in profile.expected_components
    2. PN/SN pairs — co-occurrence on the same page
    3. Blocklist   — drop SNs in profile.blocked_sn_list + universal blocklist
    4. Threshold   — high-value tiers ≥1, low-value tiers ≥2 occurrences
    5. ATA → tier  — informational only (we killed :Tier in Q6); the
                      ATA chapter still drives :RELATED_TO_ATA
    6. Same-PN clustering — multiple SNs under one PN form siblings

Rules 7 (batch certificates) and 8 (OCR rejection / vision re-read) are
deferred to a later pass — they're heavy and the framework supports them
once we want to add them.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase4.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

from graph_dal import connect, database_name                           # noqa: E402
from graph_dal.component import (                                       # noqa: E402
    link_asset_has_component,
    link_component_related_to_ata,
    link_has_primary_pn,
    link_has_sn,
    write_component,
)
from graph_dal.connector import write_part_number, write_serial_number  # noqa: E402
from graph_dal.errors import VerificationFailed                         # noqa: E402
from graph_dal.verify import verify_no_fact_orphans                     # noqa: E402


# -----------------------------------------------------------------------------
#  ATA → tier mapping (informational; tier no longer a graph node — see Q6)
# -----------------------------------------------------------------------------

ATA_TO_TIER = {
    "32": "LANDING_GEAR",
    "49": "APU",
    "61": "PROPELLER",
    "62": "ROTOR_SYSTEM", "64": "ROTOR_SYSTEM",
    "66": "ROTOR_SYSTEM", "67": "ROTOR_SYSTEM",
    "63": "TRANSMISSION", "65": "TRANSMISSION",
    "51": "AIRFRAME", "52": "AIRFRAME", "53": "AIRFRAME", "54": "AIRFRAME",
    "55": "AIRFRAME", "56": "AIRFRAME", "57": "AIRFRAME",
    "22": "AVIONICS", "23": "AVIONICS", "27": "AVIONICS", "31": "AVIONICS",
    "34": "AVIONICS", "45": "AVIONICS",
    "21": "SYSTEMS", "24": "SYSTEMS", "26": "SYSTEMS", "28": "SYSTEMS",
    "29": "SYSTEMS", "30": "SYSTEMS", "33": "SYSTEMS", "35": "SYSTEMS",
    "36": "SYSTEMS", "38": "SYSTEMS",
    "25": "INTERIOR",
}

HIGH_VALUE_TIERS = {"ENGINE", "LANDING_GEAR", "PROPELLER", "ROTOR_SYSTEM",
                    "TRANSMISSION", "APU"}


def _ata_to_tier(ata: str | None) -> str:
    """Map an ATA chapter string to a tier label (informational only)."""
    if not ata:
        return "UNKNOWN"
    head = re.match(r"^(\d{2})", str(ata).strip())
    if not head:
        return "UNKNOWN"
    n = int(head.group(1))
    if 70 <= n <= 89:
        return "ENGINE"
    return ATA_TO_TIER.get(head.group(1), "UNKNOWN")


def _normalize(s: object) -> str:
    return str(s or "").strip().upper()


def _is_blocked_sn(sn: str, custom_blocked: set[str]) -> bool:
    """Universal blocklist + per-asset custom blocklist."""
    s = sn.strip().upper()
    if not s:
        return True
    if s in custom_blocked:
        return True
    # Universal: year strings 1990..2030
    if s.isdigit() and len(s) == 4 and 1990 <= int(s) <= 2030:
        return True
    # Single character
    if len(s) <= 1:
        return True
    # Date-like
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return True
    return False


# -----------------------------------------------------------------------------
#  Data fetch from Neo4j
# -----------------------------------------------------------------------------

def _fetch_co_mentions(driver, asset_id: str) -> list[dict]:
    """For each :Page, return its mentioned PartNumbers, SerialNumbers, ATAs.

    Output: ``[{"page_uid", "doc_type", "pns", "sns", "atas", "title"}, ...]``
    """
    cypher = """
    MATCH (p:Page {asset_id: $aid})
    OPTIONAL MATCH (p)-[:MENTIONS_PN]->(pn:PartNumber {asset_id: $aid})
    OPTIONAL MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber {asset_id: $aid})
    OPTIONAL MATCH (p)-[:COVERS_ATA]->(ata:ATAChapter {asset_id: $aid})
    OPTIONAL MATCH (p)<-[:HAS_PAGE]-(d:Document {asset_id: $aid})
    WITH p, d.document_type AS doc_type, p.title AS title,
         collect(DISTINCT pn.value) AS pns,
         collect(DISTINCT sn.value) AS sns,
         collect(DISTINCT ata.value) AS atas
    RETURN p.value AS page_uid, doc_type, title, pns, sns, atas
    """
    rows: list[dict] = []
    with driver.session(database=database_name()) as s:
        for record in s.run(cypher, aid=asset_id):
            rows.append({
                "page_uid": record["page_uid"],
                "doc_type": record["doc_type"],
                "title": record["title"],
                "pns":   [v for v in (record["pns"] or []) if v],
                "sns":   [v for v in (record["sns"] or []) if v],
                "atas":  [v for v in (record["atas"] or []) if v],
            })
    return rows


# -----------------------------------------------------------------------------
#  Promotion: which (PN, SN) pairs become :Component nodes?
# -----------------------------------------------------------------------------

LLP_DOC_TYPES = {"engine_llp_status_sheet", "life_limited_parts_status"}
HISTORY_DOC_TYPES = {"component_history_card", "component_logbook"}
FORM1_DOC_TYPES = {"easa_form_one", "faa_form_8130", "tcca_form_one",
                   "dual_release_certificate", "certificate_of_release_to_service"}


def _promote(
    pair: tuple[str, str],
    hits: list[tuple[str, str | None]],
    threshold: int,
) -> bool:
    """Decide whether a (pn, sn) pair clears the threshold for promotion."""
    if len(hits) >= threshold:
        return True
    # Single hit but on a high-evidentiary-weight document type → promote
    for _, doc_type in hits:
        if doc_type in LLP_DOC_TYPES | HISTORY_DOC_TYPES | FORM1_DOC_TYPES:
            return True
    return False


# -----------------------------------------------------------------------------
#  Main
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 — Component hydration")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--asset-id", required=False)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    profile_path = workdir / "asset_profile.json"
    log_path = workdir / "progress.log"

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    blocked_sn_set: set[str] = {
        str(b).strip().upper() for b in (profile.get("blocked_sn_list") or []) if b
    }

    asset_id = args.asset_id or profile.get("asset_id")

    driver = connect()
    try:
        # If asset_id wasn't supplied, learn it from the live :Asset.
        if not asset_id:
            with driver.session(database=database_name()) as s:
                rec = s.run(
                    "MATCH (a:Asset) RETURN a.asset_id AS aid LIMIT 1"
                ).single()
                asset_id = rec["aid"] if rec else None
        if not asset_id:
            raise RuntimeError("phase4: cannot determine asset_id")

        print(f"phase4: asset_id={asset_id}", flush=True)

        # ---------------------------------------------------------------------
        # Rule 1 — Seed list
        # ---------------------------------------------------------------------
        seeded: list[str] = []
        exp = profile.get("expected_components") or {}

        # We need an evidence page for each seeded component. Use the
        # first page that mentions any of the seed's identifiers, or
        # fall back to the first page of the dossier.
        with driver.session(database=database_name()) as s:
            first_page = s.run(
                "MATCH (p:Page {asset_id: $aid}) "
                "RETURN p.value AS uid ORDER BY p.page_index LIMIT 1",
                aid=asset_id,
            ).single()
            seed_evidence_uid = first_page["uid"] if first_page else None

        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                # Engines
                for i, e in enumerate(exp.get("engines") or []):
                    esn = (e or {}).get("esn")
                    if not esn or not seed_evidence_uid:
                        continue
                    pn = "ENGINE_SEED_PN"
                    sn = _normalize(esn)
                    cuid = f"component::{pn}::{sn}"
                    write_part_number(tx, asset_id=asset_id, value=pn)
                    write_serial_number(tx, asset_id=asset_id, value=sn)
                    write_component(
                        tx,
                        asset_id=asset_id,
                        value=cuid,
                        evidence_page_uid=seed_evidence_uid,
                        evidence_quote=f"Profile-seeded engine #{i+1} esn={esn}",
                        canonical_pn=pn,
                        installed_sn=sn,
                        description="Engine (seeded from asset_profile.expected_components)",
                        component_category="Engine_Module",
                        status="DISCOVERED",
                        source="seed",
                        ata_chapter="72",
                        is_overhaul=True,
                    )
                    link_has_primary_pn(tx, asset_id=asset_id, component_uid=cuid, pn_value=pn)
                    link_has_sn(tx, asset_id=asset_id, component_uid=cuid, sn_value=sn)
                    link_asset_has_component(tx, asset_id=asset_id, component_uid=cuid)
                    seeded.append(cuid)

                # Propellers (simpler — usually pn+sn together)
                for i, p in enumerate(exp.get("propellers") or []):
                    psn = (p or {}).get("sn") or (p or {}).get("psn")
                    ppn = (p or {}).get("pn") or "PROPELLER_SEED_PN"
                    if not psn or not seed_evidence_uid:
                        continue
                    sn = _normalize(psn)
                    pn = _normalize(ppn)
                    cuid = f"component::{pn}::{sn}"
                    write_part_number(tx, asset_id=asset_id, value=pn)
                    write_serial_number(tx, asset_id=asset_id, value=sn)
                    write_component(
                        tx,
                        asset_id=asset_id,
                        value=cuid,
                        evidence_page_uid=seed_evidence_uid,
                        evidence_quote=f"Profile-seeded propeller #{i+1}",
                        canonical_pn=pn, installed_sn=sn,
                        description="Propeller (seeded)",
                        status="DISCOVERED", source="seed",
                        ata_chapter="61",
                    )
                    link_has_primary_pn(tx, asset_id=asset_id, component_uid=cuid, pn_value=pn)
                    link_has_sn(tx, asset_id=asset_id, component_uid=cuid, sn_value=sn)
                    link_asset_has_component(tx, asset_id=asset_id, component_uid=cuid)
                    seeded.append(cuid)

                tx.commit()

        # ---------------------------------------------------------------------
        # Rules 2–6 — page co-mention extraction
        # ---------------------------------------------------------------------
        rows = _fetch_co_mentions(driver, asset_id)
        print(f"phase4: examining {len(rows)} pages for PN/SN pairs", flush=True)

        # pair → list[(page_uid, doc_type)]
        pair_hits: dict[tuple[str, str], list[tuple[str, str | None]]] = defaultdict(list)
        # pair → list of ATA chapters seen on those pages
        pair_atas: dict[tuple[str, str], Counter] = defaultdict(Counter)
        # pair → first quote (we use the page title as the verbatim trace)
        pair_quote: dict[tuple[str, str], str] = {}

        sn_blocked_total = 0
        for row in rows:
            pns = [_normalize(v) for v in row["pns"] if v]
            sns = [_normalize(v) for v in row["sns"] if v]
            if not pns or not sns:
                continue
            allowed_sns = []
            for sn in sns:
                if _is_blocked_sn(sn, blocked_sn_set):
                    sn_blocked_total += 1
                    continue
                allowed_sns.append(sn)
            if not allowed_sns:
                continue
            for pn in pns:
                if not pn:
                    continue
                for sn in allowed_sns:
                    pair = (pn, sn)
                    pair_hits[pair].append((row["page_uid"], row["doc_type"]))
                    for ata in row["atas"]:
                        pair_atas[pair][_normalize(ata)] += 1
                    pair_quote.setdefault(
                        pair,
                        row["title"] or f"{pn} / {sn} on page {row['page_uid'][:8]}",
                    )

        # Rule 4 — threshold by tier (we don't store :Tier but do use it
        # informationally to choose threshold).
        promoted: list[tuple[str, str]] = []
        rejected_low_threshold = 0
        for pair, hits in pair_hits.items():
            top_ata = pair_atas[pair].most_common(1)
            ata_value = top_ata[0][0] if top_ata else None
            tier = _ata_to_tier(ata_value)
            threshold = 1 if tier in HIGH_VALUE_TIERS else 2
            if _promote(pair, hits, threshold):
                promoted.append(pair)
            else:
                rejected_low_threshold += 1

        # ---------------------------------------------------------------------
        # Write :Component nodes for promoted pairs
        # ---------------------------------------------------------------------
        components_written = 0
        ata_links = 0
        # Process in batches of 200 pairs/transaction
        BATCH = 200
        with driver.session(database=database_name()) as session:
            for i in range(0, len(promoted), BATCH):
                with session.begin_transaction() as tx:
                    for pair in promoted[i:i + BATCH]:
                        pn, sn = pair
                        cuid = f"component::{pn}::{sn}"
                        # Skip if already a seed component
                        if cuid in seeded:
                            continue

                        hits = pair_hits[pair]
                        first_page_uid, first_doc_type = hits[0]

                        top_ata = pair_atas[pair].most_common(1)
                        ata_value = top_ata[0][0] if top_ata else None

                        is_llp = any(dt in LLP_DOC_TYPES for _, dt in hits if dt)
                        is_overhaul = any(dt in HISTORY_DOC_TYPES for _, dt in hits if dt)

                        write_component(
                            tx,
                            asset_id=asset_id,
                            value=cuid,
                            evidence_page_uid=first_page_uid,
                            evidence_quote=pair_quote[pair][:240],
                            canonical_pn=pn,
                            installed_sn=sn,
                            description=None,           # Phase 5/6 enrich
                            ata_chapter=ata_value,
                            is_llp=is_llp,
                            is_overhaul=is_overhaul,
                            status="DISCOVERED",
                            source="page_mention",
                        )
                        link_has_primary_pn(tx, asset_id=asset_id, component_uid=cuid, pn_value=pn)
                        link_has_sn(tx, asset_id=asset_id, component_uid=cuid, sn_value=sn)
                        if ata_value:
                            link_component_related_to_ata(
                                tx, asset_id=asset_id, component_uid=cuid, ata_value=ata_value,
                            )
                            ata_links += 1
                        components_written += 1
                    tx.commit()
                if (i // BATCH) % 5 == 0:
                    print(f"phase4: wrote {min(i + BATCH, len(promoted))}/{len(promoted)} pairs",
                          flush=True)

        # ---------------------------------------------------------------------
        # Rule 6 — Same-PN clustering (informational; we can flag siblings later)
        # ---------------------------------------------------------------------
        same_pn_clusters = defaultdict(list)
        for (pn, sn) in promoted:
            same_pn_clusters[pn].append(sn)
        cluster_sizes = Counter(len(v) for v in same_pn_clusters.values())

        # ---------------------------------------------------------------------
        # MANDATORY VERIFICATION
        # ---------------------------------------------------------------------
        try:
            verify_counts = verify_no_fact_orphans(driver, asset_id, phase="4")
        except VerificationFailed as e:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n== Phase 4 verification (FAILED) ==\n")
                for k, v in (e.counts or {}).items():
                    f.write(f"- {k:<40s}: {v}\n")
                for rv in e.rule_violations:
                    f.write(f"- RULE: {rv['rule']} expected {rv['expected']}, got {rv['actual']}\n")
            raise

        # Component / part-type counts from live graph
        with driver.session(database=database_name()) as s:
            n_comp = s.run(
                "MATCH (c:Component {asset_id: $aid}) RETURN count(c) AS n",
                aid=asset_id,
            ).single()["n"]
            n_pn_used = s.run(
                "MATCH (:Component {asset_id: $aid})-[:HAS_PRIMARY_PN]->(pn:PartNumber)"
                " RETURN count(DISTINCT pn) AS n",
                aid=asset_id,
            ).single()["n"]
            n_sn_used = s.run(
                "MATCH (:Component {asset_id: $aid})-[:HAS_SN]->(sn:SerialNumber)"
                " RETURN count(DISTINCT sn) AS n",
                aid=asset_id,
            ).single()["n"]

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 4 verification ==\n")
            f.write(f"- pages_examined                          : {len(rows)}\n")
            f.write(f"- distinct_pn_sn_pairs                    : {len(pair_hits)}\n")
            f.write(f"- pairs_promoted_to_components            : {len(promoted)}\n")
            f.write(f"- pairs_rejected_threshold                : {rejected_low_threshold}\n")
            f.write(f"- sn_blocked_total                        : {sn_blocked_total}\n")
            f.write(f"- components_seeded                       : {len(seeded)}\n")
            f.write(f"- components_written_this_phase           : {components_written}\n")
            f.write(f"- :Component count (live)                 : {n_comp}\n")
            f.write(f"- distinct PartNumbers used by components : {n_pn_used}\n")
            f.write(f"- distinct SerialNumbers used by components: {n_sn_used}\n")
            f.write(f"- :RELATED_TO_ATA edges written           : {ata_links}\n")
            f.write(f"- same-PN cluster size distribution        : "
                    f"{dict(sorted(cluster_sizes.items()))}\n")
            f.write(f"- fact_nodes_no_evidence                  : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")

        print(
            f"phase4: OK — components={n_comp} (seeded={len(seeded)}, "
            f"discovered={components_written})  orphans={verify_counts['fact_nodes_no_evidence']}",
            flush=True,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
