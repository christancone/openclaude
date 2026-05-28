"""Phase 4 — Component discovery for CL650-6134.

Walks the Phase-1 graph to promote (PN, SN) co-occurrences to :Component
nodes. Uses normalize_identifier to fold OCR variants so each (canonical_pn,
canonical_sn) yields exactly one component, even when block-8 holds multiple
PNs for the same SN (vendor + OEM) — the longest PN wins as primary; the
others are wired as HAS_ALTERNATE_PN.

The 8 selection rules (see brief) — implementation:
1. Seed list: aircraft (the asset) plus engine SNs known from profile (none
   reliably extractable here; skip).
2. Co-occurrence — sweep MENTIONS_PN/MENTIONS_SN.
3. Blocklist applied during write.
4. Threshold by ATA tier (≥1 for HIGH_VALUE; ≥2 for SYSTEMS/AVIONICS/INTERIOR).
5. Tier inference from ATA.
6. Same-PN clustering reported.
7. Batch certificate handling — detected via OCR "thru/through/range" text;
   if the page has ≥3 SNs for one PN on a Form 1, treat each as a Component.
8. OCR rejection — mid-string spaces in PN ⇒ skipped.
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
    raise RuntimeError("phase4.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name, normalize_identifier, is_noise_identifier
from graph_dal._phase_tag import phase
from graph_dal.component import (
    write_component, link_has_primary_pn, link_has_alternate_pn,
    link_has_sn, link_component_related_to_ata, link_asset_has_component,
)
from graph_dal.verify import verify_no_fact_orphans


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"


ATA_TO_TIER = {
    "32": "LANDING_GEAR", "49": "APU", "61": "PROPELLER",
    "63": "TRANSMISSION", "65": "TRANSMISSION",
    "51": "AIRFRAME", "52": "AIRFRAME", "53": "AIRFRAME",
    "54": "AIRFRAME", "55": "AIRFRAME", "56": "AIRFRAME", "57": "AIRFRAME",
    "22": "AVIONICS", "23": "AVIONICS", "27": "AVIONICS",
    "31": "AVIONICS", "34": "AVIONICS", "45": "AVIONICS",
    "21": "SYSTEMS", "24": "SYSTEMS", "26": "SYSTEMS", "28": "SYSTEMS",
    "29": "SYSTEMS", "30": "SYSTEMS", "33": "SYSTEMS",
    "35": "SYSTEMS", "36": "SYSTEMS", "38": "SYSTEMS",
    "25": "INTERIOR",
}
HIGH_VALUE_TIERS = {"ENGINE", "LANDING_GEAR", "PROPELLER",
                    "ROTOR_SYSTEM", "TRANSMISSION", "APU"}

LLP_DOC_TYPES = {"engine_llp_status_sheet", "life_limited_parts_status"}
HISTORY_DOC_TYPES = {"component_history_card", "component_logbook"}
FORM1_DOC_TYPES = {"easa_form_one", "faa_form_8130", "tcca_form_one",
                   "dual_release_certificate", "certificate_of_release_to_service"}


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _ata_to_tier(ata: str | None) -> str:
    if not ata:
        return "UNKNOWN"
    head = re.match(r"^(\d{2})", str(ata).strip())
    if not head:
        return "UNKNOWN"
    n = int(head.group(1))
    if 70 <= n <= 89:
        return "ENGINE"
    return ATA_TO_TIER.get(head.group(1), "UNKNOWN")


def _is_ocr_suspect_pn(pn: str) -> bool:
    s = pn.strip()
    if not s:
        return True
    # Mid-string spaces are suspect.
    if " " in s and not s.upper().startswith(("RMA ", "S/N ")):
        return True
    return False


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 4 START {start.isoformat()} ==========")
    driver = connect()

    counters: Counter = Counter()
    promoted: set[str] = set()

    with phase("phase4_components"):
        # 1. Pull every (page, PNs, SNs, ATAs, doc_type, evidence_class) record.
        page_data: dict[str, dict] = {}
        with driver.session(database=database_name()) as s:
            for r in s.run("""
                MATCH (p:Page {asset_id: $aid})<-[:HAS_PAGE]-(d:Document)
                OPTIONAL MATCH (p)-[:MENTIONS_PN]->(pn:PartNumber)
                OPTIONAL MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber)
                OPTIONAL MATCH (p)-[:COVERS_ATA]->(ata:ATAChapter)
                WITH p, d,
                     collect(DISTINCT pn.value) AS pns,
                     collect(DISTINCT sn.value) AS sns,
                     collect(DISTINCT ata.value) AS atas
                RETURN p.value AS uid, p.page_index AS pi,
                       d.document_type AS dt, d.evidence_class AS ec,
                       d.file_name AS fn,
                       pns, sns, atas
            """, aid=ASSET_ID):
                page_data[r["uid"]] = {
                    "page_index": r["pi"], "doc_type": r["dt"],
                    "evidence_class": r["ec"], "file_name": r["fn"],
                    "pns": [v for v in r["pns"] if v], "sns": [v for v in r["sns"] if v],
                    "atas": [v for v in r["atas"] if v],
                }

        counters["pages_examined"] = len(page_data)

        # 2. Build (canonical_pn, canonical_sn) → list of evidence pages.
        # Track the LONGEST raw PN for each canonical_pn so we have the primary.
        pair_pages: dict[tuple[str, str], list[str]] = defaultdict(list)
        pair_raw_pns: dict[tuple[str, str], set[str]] = defaultdict(set)
        pair_raw_sns: dict[tuple[str, str], set[str]] = defaultdict(set)
        canonical_to_longest_pn: dict[str, str] = {}
        canonical_to_longest_sn: dict[str, str] = {}

        for puid, info in page_data.items():
            for pn_raw in info["pns"]:
                if _is_ocr_suspect_pn(pn_raw):
                    continue
                # Skip OCR sentinels (N/A, TBD, -, see remarks, year strings, etc.).
                # Required to prevent the noise-SN fanout: a single phantom PN/SN
                # would otherwise become the center of a (PN_real, SN_phantom)
                # cluster covering every page that mentions the phantom token.
                if is_noise_identifier(pn_raw):
                    counters["pn_skipped_noise"] += 1
                    continue
                cpn = normalize_identifier(pn_raw)
                if not cpn:
                    continue
                if len(pn_raw) > len(canonical_to_longest_pn.get(cpn, "")):
                    canonical_to_longest_pn[cpn] = pn_raw.strip()
                for sn_raw in info["sns"]:
                    if is_noise_identifier(sn_raw):
                        counters["sn_skipped_noise"] += 1
                        continue
                    csn = normalize_identifier(sn_raw)
                    if not csn:
                        continue
                    if len(sn_raw) > len(canonical_to_longest_sn.get(csn, "")):
                        canonical_to_longest_sn[csn] = sn_raw.strip()
                    pair = (cpn, csn)
                    pair_pages[pair].append(puid)
                    pair_raw_pns[pair].add(pn_raw)
                    pair_raw_sns[pair].add(sn_raw)

        counters["distinct_pn_sn_pairs"] = len(pair_pages)
        _log(f"[phase4] pn_skipped_noise={counters['pn_skipped_noise']} "
             f"sn_skipped_noise={counters['sn_skipped_noise']} (Lukas Cheatsheet §13)")

        # 3. Apply threshold rule and promote.
        with driver.session(database=database_name()) as session:
            BATCH = 200
            buffer: list = []
            with session.begin_transaction() as tx:
                for pair, page_uids in pair_pages.items():
                    cpn, csn = pair
                    # Find best evidence page (prefer primary > secondary > others).
                    weighted = []
                    promotion_signal = False
                    is_llp = False
                    top_ata = None
                    for puid in page_uids:
                        info = page_data[puid]
                        ec = info["evidence_class"] or "reference"
                        w = {"primary": 0, "secondary": 1, "reference": 2, "administrative": 3}.get(ec, 4)
                        weighted.append((w, puid))
                        if info["doc_type"] in FORM1_DOC_TYPES or info["doc_type"] in HISTORY_DOC_TYPES:
                            promotion_signal = True
                        if info["doc_type"] in LLP_DOC_TYPES:
                            is_llp = True
                            promotion_signal = True
                        if info["atas"] and not top_ata:
                            top_ata = info["atas"][0]
                    weighted.sort(key=lambda x: x[0])
                    best_page_uid = weighted[0][1]
                    best_ec = page_data[best_page_uid]["evidence_class"] or "reference"

                    # v2-demo tightening: skip if ALL evidence pages are reference/administrative.
                    if best_ec in {"reference", "administrative"} and not promotion_signal:
                        counters["pairs_rejected_evidence_class"] += 1
                        continue

                    # Threshold rule.
                    tier = _ata_to_tier(top_ata)
                    n = len(page_uids)
                    threshold = (
                        1 if tier in HIGH_VALUE_TIERS or promotion_signal
                        else 2 if tier in {"AVIONICS", "SYSTEMS", "INTERIOR", "AIRFRAME"}
                        else 1
                    )
                    if n < threshold:
                        counters["pairs_rejected_threshold"] += 1
                        continue

                    # Promote.
                    # Use the LONGEST raw PN/SN observed for this canonical id.
                    primary_pn = canonical_to_longest_pn.get(cpn) or list(pair_raw_pns[pair])[0]
                    primary_sn = canonical_to_longest_sn.get(csn) or list(pair_raw_sns[pair])[0]
                    cuid = f"component::{primary_pn}::{primary_sn}"
                    if cuid in promoted:
                        continue
                    promoted.add(cuid)

                    file_name = page_data[best_page_uid]["file_name"] or ""
                    quote = f"PN={primary_pn} SN={primary_sn} on {file_name} page {page_data[best_page_uid]['page_index']}"
                    write_component(
                        tx, asset_id=ASSET_ID, value=cuid,
                        evidence_page_uid=best_page_uid,
                        evidence_quote=quote[:240],
                        canonical_pn=primary_pn, installed_sn=primary_sn,
                        ata_chapter=top_ata,
                        is_llp=is_llp,
                        status="DISCOVERED", source="page_mention",
                    )
                    link_has_primary_pn(tx, asset_id=ASSET_ID, component_uid=cuid, pn_value=primary_pn)
                    link_has_sn(tx, asset_id=ASSET_ID, component_uid=cuid, sn_value=primary_sn)
                    # Wire alternate PNs (for vendor-OEM dual-PN Form 1s).
                    for alt_pn in pair_raw_pns[pair]:
                        if alt_pn != primary_pn:
                            link_has_alternate_pn(tx, asset_id=ASSET_ID, component_uid=cuid,
                                                  pn_value=alt_pn,
                                                  source="block_8_multi_pn", confidence="high")
                            counters["alt_pn_wired"] += 1
                    if top_ata:
                        link_component_related_to_ata(tx, asset_id=ASSET_ID, component_uid=cuid, ata_value=top_ata)
                    counters["pairs_promoted_to_components"] += 1

                    if counters["pairs_promoted_to_components"] % BATCH == 0:
                        tx.commit()
                        tx = session.begin_transaction()
                tx.commit()

    # Cluster size distribution.
    clusters = defaultdict(list)
    for cuid in promoted:
        # parse pn back from "component::{pn}::{sn}"
        parts = cuid[len("component::"):].rsplit("::", 1)
        if len(parts) == 2:
            clusters[parts[0]].append(parts[1])
    size_dist = Counter(len(v) for v in clusters.values())

    _log("[phase4] running verify_no_fact_orphans ...")
    counts = verify_no_fact_orphans(driver, ASSET_ID, phase="4")

    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 4 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 4 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 4 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")
    _log(f"  - cluster_size_distribution: {dict(size_dist)}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
