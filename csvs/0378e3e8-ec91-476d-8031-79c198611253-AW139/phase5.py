"""Phase 5 — Event hydration (Layer 4).

Streams the dossier CSV, parses ``extracted_json.content.events[]``, and
writes :Event nodes anchored to the page each event was extracted from.
For each event, looks up the affected :Component via the OCR's
``bound_entities[]`` (entity_id → entity value → SN/PN match against
:Component.installed_sn / canonical_pn).

Scope of this pass:
    - Events from ``content.events[]`` (the canonical OCR surface).
    - :OCCURRED_ON :Asset on every event without a resolved component.
    - :AFFECTED :Component when bound_entities resolves.
    - :EVIDENCED_BY :Page (golden rule, enforced by DAL).
    - :ON_DATE :Date when event_date is set.

Deferred to a later pass (the brief lists them as additional sources):
    - defect_entry / inspection_finding / certification_statement sections
    - Parts tables → component_installation/removal events
    - LLP / SB / AD compliance tables
    - Phase 6 owns the cross-doc connector edges (PERFORMED_BY, etc.)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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

from graph_dal import connect, database_name                    # noqa: E402
from graph_dal.errors import VerificationFailed                  # noqa: E402
from graph_dal.event import write_event                          # noqa: E402
from graph_dal.verify import verify_no_fact_orphans              # noqa: E402


# -----------------------------------------------------------------------------
#  OCR event_type → :Event.kind (closed-enum property, not multi-label)
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
#  Component resolution cache
# -----------------------------------------------------------------------------

class ComponentResolver:
    """In-memory index from SN (and PN) to Component uid.

    Built once at the start of Phase 5 by walking
    :Component-[:HAS_SN]->:SerialNumber and :Component-[:HAS_PRIMARY_PN].
    Resolution priority:
      - Exact SN match  → highest confidence (SNs are typically unique)
      - PN-only match   → only if exactly one component has that PN; else
                          ambiguous (return None)
    """

    def __init__(self):
        self.by_sn: dict[str, list[str]] = {}     # sn → list of component uids
        self.by_pn: dict[str, list[str]] = {}     # pn → list of component uids

    @classmethod
    def build(cls, driver, asset_id: str) -> "ComponentResolver":
        r = cls()
        cypher = """
        MATCH (c:Component {asset_id: $aid})
        OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
        OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
        RETURN c.value AS uid, sn.value AS sn, pn.value AS pn
        """
        with driver.session(database=database_name()) as s:
            for record in s.run(cypher, aid=asset_id):
                cuid = record["uid"]
                sn = record["sn"]
                pn = record["pn"]
                if sn:
                    r.by_sn.setdefault(sn.upper(), []).append(cuid)
                if pn:
                    r.by_pn.setdefault(pn.upper(), []).append(cuid)
        return r

    def resolve(self, *, sn: str | None = None, pn: str | None = None) -> tuple[str | None, str]:
        """Return (component_uid_or_None, confidence) where confidence is
        one of "high", "medium", "ambiguous", "none"."""
        if sn:
            sn_u = sn.strip().upper()
            cands = self.by_sn.get(sn_u, [])
            if len(cands) == 1:
                return cands[0], "high"
            if len(cands) > 1:
                return None, "ambiguous"
        if pn:
            pn_u = pn.strip().upper()
            cands = self.by_pn.get(pn_u, [])
            if len(cands) == 1:
                return cands[0], "medium"
            if len(cands) > 1:
                return None, "ambiguous"
        return None, "none"


def _resolve_component_for_event(
    resolver: ComponentResolver,
    event: dict,
    entities_by_id: dict,
) -> tuple[str | None, str]:
    """Look at the event's bound_entities[] and resolve to a component."""
    for be in event.get("bound_entities") or []:
        if not isinstance(be, dict):
            continue
        ent = entities_by_id.get(be.get("entity_id"))
        if not ent:
            continue
        et = ent.get("entity_type")
        ev = ent.get("value")
        if not ev:
            continue
        if et == "serial_number":
            uid, conf = resolver.resolve(sn=str(ev))
            if uid:
                return uid, conf
        elif et == "part_number":
            uid, conf = resolver.resolve(pn=str(ev))
            if uid:
                return uid, conf
    return None, "none"


def _resolve_component_from_page_entities(
    resolver: ComponentResolver, entities: list[dict],
) -> tuple[str | None, str]:
    """Fallback: pick a single page-level component if entities make it unambiguous.

    If the page has exactly one serial_number entity that resolves to a
    single :Component, attribute the event to that component. If multiple
    SNs resolve, return ambiguous (don't guess).
    """
    sn_resolved: set[str] = set()
    pn_resolved: set[str] = set()
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        et = ent.get("entity_type")
        ev = ent.get("value")
        if not ev:
            continue
        if et == "serial_number":
            uid, _ = resolver.resolve(sn=str(ev))
            if uid:
                sn_resolved.add(uid)
        elif et == "part_number":
            uid, _ = resolver.resolve(pn=str(ev))
            if uid:
                pn_resolved.add(uid)
    if len(sn_resolved) == 1:
        return next(iter(sn_resolved)), "medium"     # SN → medium because no bound_entities
    if not sn_resolved and len(pn_resolved) == 1:
        return next(iter(pn_resolved)), "low"
    return None, "none"


# -----------------------------------------------------------------------------
#  Derive events from sections and tables
# -----------------------------------------------------------------------------

# Section kinds → (kind, descriptor) for derived events.
# Maps to OCR section_type / kind / first-key heuristics from csv_and_ocr.md.
SECTION_KIND_MAP: dict[str, tuple[str, str]] = {
    "certification_statement": ("compliance",  "release_to_service"),
    "defect_entry":            ("inspection",  "defect"),
    "inspection_finding":      ("inspection",  "inspection_finding"),
    "work_description":        ("compliance",  "work_description"),
    "corrective_action":       ("compliance",  "corrective_action"),
}

# Table-name pattern → (kind, descriptor). Patterns are lowercase substrings.
TABLE_KIND_PATTERNS: list[tuple[str, str, str]] = [
    ("limited life",         "compliance", "llp_status"),
    ("life limited",         "compliance", "llp_status"),
    ("llp",                  "compliance", "llp_status"),
    ("assembly historical",  "compliance", "assembly_history"),
    ("activity record",      "compliance", "activity_record"),
    ("mandatory directive",  "compliance", "ad_compliance"),
    ("optional directive",   "compliance", "sb_compliance"),
    ("directives compliance","compliance", "directive_compliance"),
    ("installation",         "install",    "parts_install"),
    ("removal",              "removal",    "parts_removal"),
    ("incoming inspection",  "inspection", "incoming_inspection"),
    ("outgoing inspection",  "inspection", "outgoing_inspection"),
]


def _table_kind(table_name: str | None) -> tuple[str, str] | None:
    """Map a table name to (event_kind, descriptor) if known; else None."""
    if not table_name:
        return None
    lname = str(table_name).lower()
    for pat, kind, desc in TABLE_KIND_PATTERNS:
        if pat in lname:
            return (kind, desc)
    return None


def _derive_section_events(
    sections: list[dict], page_uid: str,
) -> list[dict]:
    """Yield event-spec dicts from sections."""
    out: list[dict] = []
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            continue
        # Section-type discrimination: try multiple keys (OCR variants)
        sec_kind = (sec.get("section_type") or sec.get("type") or sec.get("kind"))
        if not sec_kind:
            continue
        spec = SECTION_KIND_MAP.get(str(sec_kind))
        if not spec:
            continue
        kind, descriptor = spec
        text = str(sec.get("data") or sec.get("text") or "")[:240]
        out.append({
            "value": f"event::{page_uid}::sec_{i}",
            "kind": kind,
            "descriptor": descriptor,
            "description": text,
            "quote": text or f"{descriptor} section on page {page_uid[:8]}",
            "date_iso": None,
            "source": "section",
        })
        # defect_entry → also emit the corrective_action half (Step 3 of brief)
        if descriptor == "defect":
            out.append({
                "value": f"event::{page_uid}::sec_{i}_corr",
                "kind": "compliance",
                "descriptor": "corrective_action",
                "description": text,
                "quote": text or f"corrective action on page {page_uid[:8]}",
                "date_iso": None,
                "source": "section",
            })
    return out


def _derive_table_events(
    tables: list[dict], page_uid: str,
) -> list[dict]:
    """Yield event-spec dicts from tables. One event per row."""
    out: list[dict] = []
    for ti, tab in enumerate(tables):
        if not isinstance(tab, dict):
            continue
        name = tab.get("name") or ""
        spec = _table_kind(name)
        if not spec:
            continue
        kind, descriptor = spec
        rows = tab.get("rows") or []
        headers = tab.get("headers") or []
        for ri, row in enumerate(rows):
            if not isinstance(row, list):
                continue
            cells = [str(c) if c is not None else "" for c in row]
            quote = " | ".join(cells[:5])[:240] or f"{descriptor} row {ri}"
            out.append({
                "value": f"event::{page_uid}::tab_{ti}_row_{ri}",
                "kind": kind,
                "descriptor": descriptor,
                "description": quote,
                "quote": quote,
                "date_iso": None,         # row-level date extraction is per-table
                "source": f"table:{name[:40]}",
                # Carry the row's cells for table-row component resolution
                "row_cells": cells,
                "headers": [str(h) for h in headers],
            })
    return out


def _resolve_component_from_row(
    resolver: ComponentResolver, headers: list[str], cells: list[str],
) -> tuple[str | None, str]:
    """Look at table headers + row cells for SN/PN columns and resolve."""
    if not headers or not cells:
        return None, "none"
    sn_col, pn_col = None, None
    for i, h in enumerate(headers):
        hl = h.lower()
        if sn_col is None and ("s/n" in hl or "serial" in hl):
            sn_col = i
        if pn_col is None and ("p/n" in hl or "part" in hl):
            pn_col = i
    sn = cells[sn_col].strip() if sn_col is not None and sn_col < len(cells) else ""
    pn = cells[pn_col].strip() if pn_col is not None and pn_col < len(cells) else ""
    if sn:
        uid, conf = resolver.resolve(sn=sn)
        if uid:
            return uid, conf
    if pn:
        uid, conf = resolver.resolve(pn=pn)
        if uid:
            return uid, conf
    return None, "none"


# -----------------------------------------------------------------------------
#  Main
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 — Event hydration")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--asset-id", required=False)
    parser.add_argument("--chunksize", type=int, default=500)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    csv_path = Path(args.csv).resolve()
    log_path = workdir / "progress.log"
    profile_path = workdir / "asset_profile.json"

    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    asset_id = args.asset_id or profile.get("asset_id")

    driver = connect()
    try:
        if not asset_id:
            with driver.session(database=database_name()) as s:
                rec = s.run(
                    "MATCH (a:Asset) RETURN a.asset_id AS aid LIMIT 1"
                ).single()
                asset_id = rec["aid"] if rec else None
        if not asset_id:
            raise RuntimeError("phase5: cannot determine asset_id")

        print(f"phase5: asset_id={asset_id}", flush=True)

        # Build component resolver once (warm cache)
        resolver = ComponentResolver.build(driver, asset_id)
        print(f"phase5: resolver built — {len(resolver.by_sn)} SNs, "
              f"{len(resolver.by_pn)} PNs indexed", flush=True)

        # Stream the CSV row by row, write events
        events_total = 0
        events_with_component = 0
        events_with_date = 0
        kinds_seen: Counter[str] = Counter()
        compliance_status_seen: Counter[str] = Counter()
        confidence_seen: Counter[str] = Counter()
        rows_processed = 0

        df_iter = pd.read_csv(csv_path, chunksize=args.chunksize)
        with driver.session(database=database_name()) as session:
            for chunk_idx, chunk in enumerate(df_iter):
                with session.begin_transaction() as tx:
                    for _, row in chunk.iterrows():
                        rows_processed += 1
                        try:
                            ext = orjson.loads(row["extracted_json"])
                        except Exception:
                            continue

                        page_uid = str(row.get("id", ""))
                        # OCR variants: sometimes content.* is the wrapper, sometimes
                        # everything is at the top level (this AW139 has the latter).
                        content = ext.get("content") or {}
                        sections = content.get("sections") or ext.get("sections") or []
                        tables = content.get("tables")  or ext.get("tables")  or []
                        page_entities = content.get("entities") or ext.get("entities") or []

                        # Build entity index for this page (for bound_entities resolution)
                        entities_by_id = {}
                        for ent in page_entities:
                            eid = ent.get("entity_id") if isinstance(ent, dict) else None
                            if eid:
                                entities_by_id[eid] = ent

                        # 1. Native events from content.events[] (rare in this OCR vintage)
                        events = content.get("events") or ext.get("events") or []

                        # 2. Derived events from sections + tables (the bulk of this dossier)
                        derived = _derive_section_events(sections, page_uid)
                        derived += _derive_table_events(tables, page_uid)

                        if not events and not derived:
                            continue

                        # Pre-resolve a page-level component fallback for events
                        # without bound_entities or row-level resolution
                        page_fallback_uid, page_fallback_conf = (
                            _resolve_component_from_page_entities(resolver, page_entities)
                        )

                        # Process native events first
                        for ev_i, ev in enumerate(events):
                            if not isinstance(ev, dict):
                                continue
                            event_local_id = ev.get("event_id") or f"evt_{ev_i}"
                            event_uid = f"event::{page_uid}::{event_local_id}"

                            ocr_event_type = (ev.get("event_type") or "other")
                            kind = OCR_EVENT_TYPE_MAP.get(ocr_event_type, "compliance")

                            description = ev.get("description") or ""
                            quote = description[:240] if description else (
                                f"{ocr_event_type} on page {page_uid[:8]}"
                            )
                            date_iso = ev.get("date")
                            task_status = ev.get("task_compliance_status")
                            task_reason = ev.get("compliance_status_reason")
                            task_ref = ev.get("task_reference")

                            comp_uid, comp_conf = _resolve_component_for_event(
                                resolver, ev, entities_by_id,
                            )
                            if not comp_uid:
                                comp_uid, comp_conf = page_fallback_uid, page_fallback_conf

                            try:
                                write_event(
                                    tx,
                                    asset_id=asset_id,
                                    value=event_uid,
                                    kind=kind,
                                    evidence_page_uid=page_uid,
                                    evidence_quote=quote,
                                    date_iso=date_iso,
                                    description=description,
                                    task_reference=task_ref,
                                    task_compliance_status=task_status,
                                    compliance_status_reason=task_reason,
                                    asset_event=(comp_uid is None),
                                    component_uid=comp_uid,
                                    affected_confidence=comp_conf if comp_uid else None,
                                )
                            except Exception as e:
                                print(f"phase5: skipping event {event_uid}: {e}",
                                      file=sys.stderr)
                                continue

                            events_total += 1
                            kinds_seen[kind] += 1
                            if task_status:
                                compliance_status_seen[task_status] += 1
                            confidence_seen[comp_conf] += 1
                            if comp_uid:
                                events_with_component += 1
                            if date_iso:
                                events_with_date += 1

                        # Process derived events (sections + tables)
                        for ev in derived:
                            event_uid = ev["value"]
                            kind = ev["kind"]
                            description = ev["description"]
                            quote = ev["quote"]
                            date_iso = ev.get("date_iso")
                            descriptor = ev["descriptor"]

                            # Resolve component:
                            #  - For table rows: look at the row's S/N or P/N columns
                            #  - For sections: fall back to page-level resolution
                            comp_uid: str | None = None
                            comp_conf = "none"
                            if "row_cells" in ev:
                                comp_uid, comp_conf = _resolve_component_from_row(
                                    resolver, ev.get("headers") or [], ev["row_cells"],
                                )
                            if not comp_uid:
                                comp_uid, comp_conf = page_fallback_uid, page_fallback_conf

                            try:
                                write_event(
                                    tx,
                                    asset_id=asset_id,
                                    value=event_uid,
                                    kind=kind,
                                    evidence_page_uid=page_uid,
                                    evidence_quote=quote,
                                    date_iso=date_iso,
                                    description=description,
                                    task_reference=descriptor,   # carry source descriptor
                                    asset_event=(comp_uid is None),
                                    component_uid=comp_uid,
                                    affected_confidence=comp_conf if comp_uid else None,
                                )
                            except Exception as e:
                                print(f"phase5: skipping derived event {event_uid}: {e}",
                                      file=sys.stderr)
                                continue

                            events_total += 1
                            kinds_seen[kind] += 1
                            confidence_seen[comp_conf] += 1
                            if comp_uid:
                                events_with_component += 1
                            if date_iso:
                                events_with_date += 1
                    tx.commit()
                if (chunk_idx % 10) == 0:
                    print(f"phase5: chunk {chunk_idx} rows={rows_processed} events={events_total}",
                          flush=True)

        # MANDATORY VERIFICATION
        try:
            verify_counts = verify_no_fact_orphans(driver, asset_id, phase="5")
        except VerificationFailed as e:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n== Phase 5 verification (FAILED) ==\n")
                for k, v in (e.counts or {}).items():
                    f.write(f"- {k:<40s}: {v}\n")
                for rv in e.rule_violations:
                    f.write(f"- RULE: {rv['rule']} expected {rv['expected']}, got {rv['actual']}\n")
            raise

        with driver.session(database=database_name()) as s:
            n_event = s.run(
                "MATCH (e:Event {asset_id: $aid}) RETURN count(e) AS n",
                aid=asset_id,
            ).single()["n"]

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 5 verification ==\n")
            f.write(f"- rows_processed                          : {rows_processed}\n")
            f.write(f"- :Event count (live)                     : {n_event}\n")
            f.write(f"- events_with_component_link              : {events_with_component}\n")
            f.write(f"- events_with_date                        : {events_with_date}\n")
            f.write(f"- fact_nodes_no_evidence                  : "
                    f"{verify_counts.get('fact_nodes_no_evidence', 0)}\n")
            f.write("- event kinds:\n")
            for k, v in kinds_seen.most_common():
                f.write(f"    {k:<28s} : {v}\n")
            f.write("- compliance status:\n")
            for k, v in compliance_status_seen.most_common():
                f.write(f"    {k:<28s} : {v}\n")
            f.write("- component-resolution confidence:\n")
            for k, v in confidence_seen.most_common():
                f.write(f"    {k:<28s} : {v}\n")

        print(
            f"phase5: OK — events={n_event}  with_component={events_with_component}  "
            f"with_date={events_with_date}  orphans={verify_counts['fact_nodes_no_evidence']}",
            flush=True,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
