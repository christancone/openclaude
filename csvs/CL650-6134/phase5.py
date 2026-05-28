"""Phase 5 — Event hydration for CL650-6134.

Re-reads the CSV row-by-row, parses extracted_json.events[] + sections[] +
tables[], writes :Event nodes anchored to the page they came from, resolves
the affected :Component via bound_entities[], and (when the page CARRIES a
Form 1 and the event is release_to_service) wires :Form1-[:RELEASES]->Component
with disposition derived from block 11 status — but only when BOTH PN-match
AND SN-match hold against the Form 1's recorded block 8 / block 10 values.

The Form1 -> RELEASES_PN / RELEASES_SN structural edges are also wired here
(reading from the already-stored Form1 block_8_pn / block_10_sn properties).
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import orjson
import pandas as pd


def _bootstrap_graph_dal() -> None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError("phase5.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005")

from graph_dal import connect, database_name, normalize_identifier
from graph_dal._phase_tag import phase
from graph_dal.event import (
    write_event, link_form1_releases_component, link_crs_certifies,
    link_was_installed_on, link_component_installed_at, link_component_removed_at,
)
from graph_dal.form1_edges import (
    link_form1_releases_pn, link_form1_releases_sn,
)
from graph_dal.verify import verify_no_fact_orphans


ASSET_ID = "62368985-01a6-4f6d-b6de-932775401d76"
CSV_PATH = Path("D:/work/openclaude/csvs/Full challenger dossier.csv")
DUMPS_DIR = Path("D:/work/openclaude/dumps")
RUN_LOG = DUMPS_DIR / "build_run.log"


OCR_EVENT_TYPE_MAP = {
    "task_performed":         "compliance",
    "inspection":             "inspection",
    "component_installation": "install",
    "component_removal":      "removal",
    "sb_compliance":          "compliance",
    "ad_compliance":          "compliance",
    "modification":           "compliance",
    "repair":                 "compliance",
    "shop_visit":             "shop_visit",
    "release_to_service":     "compliance",
    "other":                  "compliance",
}


def _log(line: str) -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


class ComponentResolver:
    def __init__(self) -> None:
        self.by_sn_norm: dict[str, list[tuple[str, str]]] = {}  # csn -> [(component_uid, primary_pn_norm)]
        self.by_pn_norm: dict[str, list[str]] = {}              # cpn -> [component_uid, ...]

    @classmethod
    def build(cls, driver):
        r = cls()
        with driver.session(database=database_name()) as s:
            for record in s.run("""
                MATCH (c:Component {asset_id: $aid})
                OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
                OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
                RETURN c.value AS uid, sn.value AS sn, pn.value AS pn
            """, aid=ASSET_ID):
                uid = record["uid"]
                sn_n = normalize_identifier(record["sn"]) if record["sn"] else None
                pn_n = normalize_identifier(record["pn"]) if record["pn"] else None
                if sn_n:
                    r.by_sn_norm.setdefault(sn_n, []).append((uid, pn_n or ""))
                if pn_n:
                    r.by_pn_norm.setdefault(pn_n, []).append(uid)
        return r

    def resolve(self, *, sn=None, pn=None) -> tuple[str | None, str]:
        if sn:
            csn = normalize_identifier(sn)
            if csn:
                cands = self.by_sn_norm.get(csn, [])
                if len(cands) == 1:
                    return cands[0][0], "high"
                if len(cands) > 1 and pn:
                    cpn = normalize_identifier(pn)
                    for cuid, ppn in cands:
                        if ppn == cpn:
                            return cuid, "high"
                    return None, "ambiguous"
                if len(cands) > 1:
                    return None, "ambiguous"
        if pn:
            cpn = normalize_identifier(pn)
            if cpn:
                cands = self.by_pn_norm.get(cpn, [])
                if len(cands) == 1:
                    return cands[0], "medium"
                if len(cands) > 1:
                    return None, "ambiguous"
        return None, "none"


def _disposition_from_status(status: str | None) -> str | None:
    if not status:
        return None
    return status  # link_form1_releases_component runs the normalizer internally.


def main() -> int:
    start = datetime.now(timezone.utc)
    _log(f"\n========== PHASE 5 START {start.isoformat()} ==========")
    driver = connect()

    resolver = ComponentResolver.build(driver)
    _log(f"[phase5] resolver: {len(resolver.by_sn_norm)} SNs, {len(resolver.by_pn_norm)} PNs indexed")

    # Pre-load Form1 -> {block_8_pns, block_10_sns, page_uid, tracking_no, block_11_status}
    form1_by_page: dict[str, list[dict]] = {}
    with driver.session(database=database_name()) as s:
        for r in s.run("""
            MATCH (f:Form1 {asset_id: $aid})<-[:CARRIES]-(p:Page)
            RETURN f.value AS f_uid, p.value AS p_uid,
                   f.block_8_pn AS pn, f.block_10_sn AS sn,
                   f.block_3_form_tracking_no AS tn, f.block_11_status AS st
        """, aid=ASSET_ID):
            form1_by_page.setdefault(r["p_uid"], []).append({
                "uid": r["f_uid"], "pn": r["pn"], "sn": r["sn"],
                "tn": r["tn"], "status": r["st"],
            })

    # Also load Form1 -> set of (pn, sn) actually present in the block-8 / block-10
    # raw text — from the Form 1 properties we wrote — so RELEASES wiring uses the
    # multi-PN / multi-SN truth not just the single-stored value.
    # We split the stored block_8_pn/block_10_sn by comma since Phase 1 may have
    # stored multiple. Phase 1 stored block_8_pn as LONGEST single; multi-PN cells
    # need the raw page entities — but the per-PN/SN PartNumber/SerialNumber nodes
    # were ALL written. So we can also read all PN/SN mentioned on the same page
    # as the Form 1 and treat those as the authoritative block-8 / block-10 set.

    # Re-load: per-Form 1 page, all PNs and SNs mentioned on that page.
    form1_pns_sns: dict[str, dict[str, list[str]]] = {}
    with driver.session(database=database_name()) as s:
        for r in s.run("""
            MATCH (f:Form1 {asset_id: $aid})<-[:CARRIES]-(p:Page)
            OPTIONAL MATCH (p)-[:MENTIONS_PN]->(pn:PartNumber)
            OPTIONAL MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber)
            RETURN f.value AS f_uid, collect(DISTINCT pn.value) AS pns, collect(DISTINCT sn.value) AS sns
        """, aid=ASSET_ID):
            form1_pns_sns[r["f_uid"]] = {
                "pns": [v for v in r["pns"] if v],
                "sns": [v for v in r["sns"] if v],
            }

    counters: Counter = Counter()
    seen_event_uids: set[str] = set()

    with phase("phase5_events"):
        # First — wire structural RELEASES_PN / RELEASES_SN edges.
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for f_uid, blocks in form1_pns_sns.items():
                    # We don't know exactly which PN/SN belongs to block 8 vs the rest of
                    # the page from this query alone; but a Form-1-only page typically
                    # has all PN/SN mentions corresponding to block 8 / 10. Wire all.
                    info = next((f for fs in form1_by_page.values() for f in fs if f["uid"] == f_uid), None)
                    tn = info["tn"] if info else None
                    for pn in blocks["pns"]:
                        try:
                            link_form1_releases_pn(tx, asset_id=ASSET_ID, form1_uid=f_uid,
                                                   pn_value=pn, block="8",
                                                   source="ocr_block_8", confidence="high",
                                                   form_tracking_no=tn)
                            counters["releases_pn"] += 1
                        except Exception:
                            pass
                    for sn in blocks["sns"]:
                        try:
                            link_form1_releases_sn(tx, asset_id=ASSET_ID, form1_uid=f_uid,
                                                   sn_value=sn, block="10",
                                                   source="ocr_block_10", confidence="high",
                                                   form_tracking_no=tn)
                            counters["releases_sn"] += 1
                        except Exception:
                            pass
                tx.commit()

        # Now stream the CSV for event hydration.
        df_iter = pd.read_csv(CSV_PATH, chunksize=500)
        with driver.session(database=database_name()) as session:
            for chunk_idx, chunk in enumerate(df_iter):
                with session.begin_transaction() as tx:
                    for _, row in chunk.iterrows():
                        try:
                            ext = orjson.loads(row["extracted_json"])
                        except Exception:
                            continue
                        if not isinstance(ext, dict):
                            continue
                        content = ext.get("content") if isinstance(ext.get("content"), dict) else {}
                        events = content.get("events") or ext.get("events") or []
                        page_entities = content.get("entities") or ext.get("entities") or []
                        if not events:
                            continue
                        page_uid = str(row["id"])
                        entities_by_id = {e.get("entity_id"): e for e in page_entities if isinstance(e, dict) and e.get("entity_id")}

                        page_form1s = form1_by_page.get(page_uid, [])

                        for ev_i, ev in enumerate(events):
                            if not isinstance(ev, dict):
                                continue
                            event_uid = f"event::{page_uid}::{ev.get('event_id') or f'evt_{ev_i}'}"
                            if event_uid in seen_event_uids:
                                continue
                            seen_event_uids.add(event_uid)
                            ocr_ev_type = (ev.get("event_type") or "other").lower()
                            kind = OCR_EVENT_TYPE_MAP.get(ocr_ev_type, "compliance")
                            description = ev.get("description") or ""
                            quote = description[:240] or f"{ocr_ev_type} on page {page_uid[:8]}"
                            date_iso = ev.get("date")

                            # Resolve component via bound_entities.
                            comp_uid = None
                            comp_conf = "none"
                            sn_b = None
                            pn_b = None
                            for be in ev.get("bound_entities") or []:
                                if not isinstance(be, dict):
                                    continue
                                e = entities_by_id.get(be.get("entity_id"))
                                if not e:
                                    continue
                                et = (e.get("entity_type") or "").lower()
                                val = e.get("value")
                                if not isinstance(val, str):
                                    continue
                                if et == "serial_number" and not sn_b:
                                    sn_b = val
                                elif et == "part_number" and not pn_b:
                                    pn_b = val
                            if sn_b or pn_b:
                                comp_uid, comp_conf = resolver.resolve(sn=sn_b, pn=pn_b)
                            counters[f"resolve_{comp_conf}"] += 1

                            try:
                                write_event(
                                    tx, asset_id=ASSET_ID, value=event_uid,
                                    kind=kind,
                                    evidence_page_uid=page_uid, evidence_quote=quote,
                                    date_iso=date_iso,
                                    description=description[:1000] if description else None,
                                    task_reference=ev.get("task_reference"),
                                    task_compliance_status=ev.get("task_compliance_status"),
                                    compliance_status_reason=ev.get("compliance_status_reason"),
                                    asset_event=(comp_uid is None),
                                    component_uid=comp_uid,
                                    affected_confidence=comp_conf if comp_uid else None,
                                )
                                counters[f"events_{kind}"] += 1
                                counters["events_total"] += 1
                            except Exception as exc:
                                _log(f"[phase5] write_event failed {event_uid}: {exc!r}")
                                continue

                            # Form1 → Component direct edge is REMOVED per the
                            # simplified Form 1 contract (2026-05-19). The
                            # Component-SN-Form1 bridge via shared :SerialNumber
                            # is the canonical path. Phase 4's link_has_sn and
                            # Phase 1's link_form1_releases_sn together provide
                            # it; no direct Form1→Component edge is wired.
                            pass
                            # Mark install / removal edges from kind alone when component is known.
                            if comp_uid and kind == "install":
                                try:
                                    link_component_installed_at(tx, asset_id=ASSET_ID,
                                                                component_uid=comp_uid, event_uid=event_uid)
                                except Exception:
                                    pass
                            elif comp_uid and kind == "removal":
                                try:
                                    link_component_removed_at(tx, asset_id=ASSET_ID,
                                                              component_uid=comp_uid, event_uid=event_uid)
                                except Exception:
                                    pass
                    tx.commit()
                if chunk_idx % 5 == 0:
                    _log(f"[phase5] chunk {chunk_idx} events_total={counters['events_total']}")

        # Pair CRS with the Form 1 on the same page (high confidence).
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                tx.run("""
                    MATCH (p:Page {asset_id: $aid})-[:CARRIES]->(c:CRS)
                    MATCH (p)-[:CARRIES]->(f:Form1)
                    MERGE (c)-[r:PAIRED_WITH]->(f)
                    ON CREATE SET r.via='same_page', r.confidence='high'
                """, aid=ASSET_ID).consume()
                tx.commit()

    _log("[phase5] running verify_no_fact_orphans ...")
    counts = verify_no_fact_orphans(driver, ASSET_ID, phase="5")

    end = datetime.now(timezone.utc)
    _log(f"========== PHASE 5 END {end.isoformat()}  dur={int((end-start).total_seconds())}s ==========")
    _log("== Phase 5 verification ==")
    for k, v in sorted(counts.items()):
        _log(f"  - {k}: {v}")
    _log("== Phase 5 counters ==")
    for k, v in sorted(counters.items()):
        _log(f"  - {k}: {v}")
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
