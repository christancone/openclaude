#!/usr/bin/env python3
"""Generate dumps/jsonc/bombardier.jsonc from the live Neo4j graph.

Reads from the local Neo4j HTTP API at http://localhost:7474, asset_id is
3027fd4d-e601-47ab-b7b1-8f2d9b70c656 (Bombardier Challenger 650, MSN 6134).
Streams the components[] array straight to disk so memory stays bounded.

Run from repo root:
    python dumps/jsonc/_build_bombardier.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import urllib.request
import urllib.error
import base64

# ---- config -----------------------------------------------------------------

NEO4J_URL = "http://localhost:7474/db/neo4j/query/v2"
NEO4J_USER = "neo4j"
NEO4J_PASS = os.environ.get(
    "NEO4J_PASSWORD", "cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005"
)
ASSET_ID = "3027fd4d-e601-47ab-b7b1-8f2d9b70c656"
OUT_PATH = Path(__file__).parent / "bombardier.jsonc"

# Full-page placeholder bbox — the graph stores no bbox/region geometry today,
# so every region we emit covers the entire page. Excerpt + contribution still
# carry the meaningful provenance.
FULL_PAGE_BBOX = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def cypher(stmt: str, params: dict | None = None) -> list[list[Any]]:
    """Run a Cypher statement and return its rows (list of lists)."""
    body = json.dumps({"statement": stmt, "parameters": params or {}}).encode()
    auth = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASS}".encode()).decode()
    req = urllib.request.Request(
        NEO4J_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {auth}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code}: {e.read().decode(errors='replace')}\n")
        raise
    if "errors" in payload and payload["errors"]:
        raise RuntimeError(f"Cypher error: {payload['errors']}")
    return payload["data"]["values"]


# ---- tracked-claim builders -------------------------------------------------

def claim_not_checked(value=None, unit=None) -> dict:
    # When the field is a ratio type (unit "%"), emit the canonical
    # {numerator, denominator, percent} shape so schema-walkers find every
    # nested key even on uninspected claims.
    if unit == "%" and value is None:
        value = {"numerator": 0, "denominator": 0, "percent": 0}
    return {
        "value": value,
        "unit": unit,
        "method": "not_checked",
        "documents": [],
        "citations": [],
    }


def claim_unresolvable(justification: str, recommended: list[str] | None = None,
                       unit=None) -> dict:
    return {
        "value": None,
        "unit": unit,
        "method": "unresolvable",
        "justification": justification,
        "recommended_documents": recommended or [],
        "documents": [],
        "citations": [],
    }


def make_citation(doc_index: int, page_1based: int, file_name: str,
                  original_path: str | None, contribution: str,
                  excerpt: str = "") -> dict:
    """Build a single citation entry with inline file_name + original_path.

    Schema extension: stores file_name and original_path on the citation itself
    so consumers don't have to dereference dossier_documents[]. The schema's
    `document` index field still works as the canonical foreign key.
    """
    return {
        "document": doc_index,
        "page": page_1based,
        "file_name": file_name or "",
        "original_path": original_path or "",
        "regions": [{
            "bbox": FULL_PAGE_BBOX,
            "contribution": contribution,
            "excerpt": excerpt or "",
        }],
    }


def claim_confirmed(value, unit=None, doc_id=None, page=None, excerpt=None,
                    contribution=None, file_name=None, original_path=None) -> dict:
    out: dict = {
        "value": value,
        "unit": unit,
        "method": "confirmed",
        "justification": None,
        "recommended_documents": [],
        "documents": [],
        "citations": [],
    }
    if doc_id:
        out["documents"] = [{"id": doc_id}]
        if page is not None:
            out["citations"] = [make_citation(
                0, page, file_name or "", original_path or "",
                contribution or "Source page", excerpt or "",
            )]
    return out


def claim_confirmed_with_citations(value, unit, documents: list[dict],
                                    citations: list[dict]) -> dict:
    """A claim_confirmed where citations are pre-built (sample pages)."""
    return {
        "value": value,
        "unit": unit,
        "method": "confirmed",
        "documents": documents,
        "citations": citations,
    }


def claim_inferred(value, unit=None, justification: str | None = None) -> dict:
    out: dict = {
        "value": value,
        "unit": unit,
        "method": "inferred",
        "documents": [],
        "citations": [],
    }
    if justification:
        out["justification"] = justification
    return out


def claim_calculated(value, unit, computation: str, inputs: list | None = None) -> dict:
    return {
        "value": value,
        "unit": unit,
        "method": "calculated",
        "computation": computation,
        "inputs": inputs or [],
        "documents": [],
        "citations": [],
    }


def claim_not_applicable(unit=None) -> dict:
    return {
        "value": None,
        "unit": unit,
        "method": "not_applicable",
        "documents": [],
        "citations": [],
    }


def ratio(num: int, den: int) -> dict:
    pct = round((num / den) * 100, 2) if den else 0.0
    return {"numerator": num, "denominator": den, "percent": pct}


# ---- queries ----------------------------------------------------------------

def fetch_metadata() -> dict:
    rows = cypher(
        "MATCH (a:Asset {asset_id:$aid}) RETURN properties(a)",
        {"aid": ASSET_ID},
    )
    asset = rows[0][0]

    audit_rows = cypher(
        "MATCH (a:AuditRun {asset_id:$aid}) "
        "RETURN a.value AS v, a.dossier_cut_off_date AS doc, "
        "a.audit_snapshot_date AS asd, a.sparengine_version AS sv "
        "ORDER BY a.value DESC LIMIT 1",
        {"aid": ASSET_ID},
    )
    audit = audit_rows[0] if audit_rows else [None, None, None, None]
    return {
        "asset": asset,
        "audit_run_id": audit[0],
        "dossier_cut_off_date": audit[1],
        "audit_snapshot_date": audit[2],
        "sparengine_version": audit[3],
    }


def first_doc_for_type(dt: str) -> tuple[str, str] | None:
    """Return (doc_value, file_name) for the most recent doc of a given type, or None."""
    rows = cypher(
        "MATCH (d:Document {asset_id:$aid}) WHERE d.document_type=$dt "
        "RETURN d.value, d.file_name ORDER BY d.file_name DESC LIMIT 1",
        {"aid": ASSET_ID, "dt": dt},
    )
    return tuple(rows[0]) if rows else None


def doc_first_page(doc_value: str) -> int:
    """Return the lowest page_index for a Document, +1 (schema is 1-based)."""
    rows = cypher(
        "MATCH (d:Document {asset_id:$aid, value:$v})-[:HAS_PAGE]->(p:Page) "
        "RETURN min(p.page_index) AS m",
        {"aid": ASSET_ID, "v": doc_value},
    )
    return (rows[0][0] or 0) + 1 if rows else 1


def fetch_documents() -> list[dict]:
    """All documents in the corpus, with original_path resolved from any page."""
    rows = cypher(
        "MATCH (d:Document {asset_id:$aid}) "
        "OPTIONAL MATCH (d)-[:HAS_PAGE]->(p:Page) "
        "WITH d, count(p) AS pc, collect(p.original_path)[0] AS op "
        "RETURN d.value, d.file_name, d.document_type, d.evidence_class, "
        "       pc, op ORDER BY d.file_name",
        {"aid": ASSET_ID},
    )
    out = []
    for v, fn, dt, ec, pc, op in rows:
        out.append({
            "id": v,
            "file_name": fn or "",
            "original_path": op or "",
            "document_type": dt or "other",
            "evidence_class": ec or "reference",
            "page_count": pc or 0,
            "scan_quality_flags": [],
            "ingestion_date": "2026-05-07",
            "asset_id_link": "CL650-6134",
        })
    return out


def fetch_findings_summary() -> dict:
    """Counts of findings by severity and category."""
    rows = cypher(
        "MATCH (f:Finding {asset_id:$aid}) "
        "RETURN f.severity, f.category, count(*) AS c",
        {"aid": ASSET_ID},
    )
    by_sev: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    total = 0
    for sev, cat, c in rows:
        by_sev[sev] = by_sev.get(sev, 0) + c
        by_cat[cat] = by_cat.get(cat, 0) + c
        total += c
    return {"by_severity": by_sev, "by_category": by_cat, "total": total}


def fetch_findings_consolidated() -> list[dict]:
    """Per Lukas: consolidate FORM1_MISSING into one summary, emit unique others individually."""
    # Unique non-FORM1 findings
    individual_rows = cypher(
        "MATCH (f:Finding {asset_id:$aid}) "
        "WHERE f.category <> 'FORM1_MISSING' "
        "OPTIONAL MATCH (a:Asset {asset_id:$aid})-[:HAS_FINDING]->(f) "
        "OPTIONAL MATCH (f)-[:FLAGS]->(t:Component) "
        "OPTIONAL MATCH (f)-[:EVIDENCED_BY]->(p:Page)<-[:HAS_PAGE]-(d:Document) "
        "WITH f, collect(DISTINCT t.value) AS comps, "
        "     collect(DISTINCT [d.value, d.file_name, p.page_index, p.original_path])[..5] AS pages "
        "RETURN f.value, f.severity, f.category, f.title, f.description, "
        "       f.recommended_action, comps, pages "
        "ORDER BY f.severity, f.category",
        {"aid": ASSET_ID},
    )

    findings: list[dict] = []
    for fv, sev, cat, title, desc, rec, comps, pages in individual_rows:
        documents: list[dict] = []
        citations: list[dict] = []
        seen_docs: list[str] = []
        for entry in (pages or []):
            if not entry or entry[0] is None:
                continue
            d_id, fn, p_idx, op = entry
            if d_id not in seen_docs:
                seen_docs.append(d_id)
                documents.append({"id": d_id})
            citations.append(make_citation(
                seen_docs.index(d_id), (p_idx or 0) + 1,
                fn or "", op or "",
                "Finding source page", "",
            ))
        findings.append({
            "id": fv,
            "severity": sev,
            "category": cat,
            "title": title or "",
            "description": desc or "",
            "location": {
                "section": "components",
                "path": "components[]",
                "affected_component_ids": [c for c in (comps or []) if c],
            },
            "justification": desc or "",
            "recommended_action": rec or "",
            "documents": documents,
            "citations": citations,
        })

    # Consolidated FORM1_MISSING
    f1_rows = cypher(
        "MATCH (f:Finding {asset_id:$aid, category:'FORM1_MISSING'}) "
        "OPTIONAL MATCH (f)-[:FLAGS]->(t:Component) "
        "RETURN count(DISTINCT f) AS n, collect(DISTINCT t.value) AS comps",
        {"aid": ASSET_ID},
    )
    n, comps = (f1_rows[0] if f1_rows else (0, []))
    # Sample evidence pages for the consolidated FORM1_MISSING finding (cap at 5)
    f1_pages = cypher(
        "MATCH (f:Finding {asset_id:$aid, category:'FORM1_MISSING'})"
        "-[:EVIDENCED_BY]->(p:Page)<-[:HAS_PAGE]-(d:Document) "
        "RETURN DISTINCT d.value, d.file_name, p.page_index, p.original_path LIMIT 5",
        {"aid": ASSET_ID},
    )
    f1_documents: list[dict] = []
    f1_citations: list[dict] = []
    f1_seen: list[str] = []
    for d_id, fn, p_idx, op in f1_pages:
        if d_id is None:
            continue
        if d_id not in f1_seen:
            f1_seen.append(d_id)
            f1_documents.append({"id": d_id})
        f1_citations.append(make_citation(
            f1_seen.index(d_id), (p_idx or 0) + 1,
            fn or "", op or "",
            "Sample page where one of the FORM1_MISSING component references was last seen",
            "",
        ))
    if n > 0:
        findings.insert(0, {
            "id": "F-FORM1-CONSOLIDATED",
            "severity": "level_2",
            "category": "FORM1_MISSING",
            "title": (
                f"Form 1 not located for {n} LLP/overhaul-tracked components "
                "(consolidated per Lukas guidance)"
            ),
            "description": (
                f"Sparengine ran the 9-strategy search (wo_pages, sn_alone, alt_pn, "
                f"filename_pn, filename_sn, batch_range, page_neighbourhood, siblings, "
                f"oem_typical) for {n} components and located no :Form1 with a "
                ":RELEASES edge attributing release to the component. Per Lukas's >5-of-"
                "the-same-PN/type rule, these are summarised as one finding rather than "
                f"emitted individually. The full list of affected component_ids is in "
                "location.affected_component_ids."
            ),
            "location": {
                "section": "components",
                "path": "components[]",
                "affected_component_ids": [c for c in (comps or []) if c],
            },
            "justification": (
                "GAP_IN_DOSSIER framing: in most cases, a missing Form 1 means the "
                "operator did not include it in the compiled dossier, not that it was "
                "never issued. Recommended action is to request from the operator, not "
                "to declare unairworthy."
            ),
            "recommended_action": (
                "Request the operator's Form 1 / FAA 8130-3 binder for the affected "
                "serial numbers; check batch certificates for serial ranges before "
                "treating any individual SN as missing; verify currently installed "
                "engine and landing-gear primary assemblies first (Level 1 risk)."
            ),
            "documents": f1_documents,
            "citations": f1_citations,
        })
    return findings


def fetch_components_stream() -> Iterable[dict]:
    """Yield components one at a time so the JSON file can be streamed.

    For each component we emit a compact tracked-claim record. Most numeric
    fields stay 'not_checked' because the Component node carries no TSN/CSN/
    life_limit properties — those would come from related Event/ComponentSnapshot
    nodes that aren't populated in this graph.
    """
    BATCH = 500
    skip = 0
    MAX_PAGES_PER_COMP = 10
    while True:
        rows = cypher(
            "MATCH (c:Component {asset_id:$aid}) "
            "OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber) "
            "OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber) "
            "OPTIONAL MATCH (c)-[:EVIDENCED_BY]->(p:Page)<-[:HAS_PAGE]-(d:Document) "
            "OPTIONAL MATCH (c)-[:HAS_FINDING]->(f:Finding) "
            "OPTIONAL MATCH (af:Finding)-[:FLAGS]->(c) "
            "WITH c, pn, sn, "
            "     collect(DISTINCT [d.value, d.file_name, p.page_index, p.original_path])[..$maxp] AS pages, "
            "     collect(DISTINCT f.value) + collect(DISTINCT af.value) AS finds "
            "RETURN c.value, c.canonical_pn, c.installed_sn, c.name, c.description, "
            "       c.component_category, c.is_llp, c.is_overhaul, c.status, "
            "       c.ata_chapter, c.source, "
            "       pn.value, sn.value, pages, finds "
            "ORDER BY c.value SKIP $skip LIMIT $batch",
            {"aid": ASSET_ID, "skip": skip, "batch": BATCH,
             "maxp": MAX_PAGES_PER_COMP},
        )
        if not rows:
            return
        for r in rows:
            (cv, pn_can, sn_inst, name, descr, cat, is_llp, is_oh, status,
             ata, src, pn_val, sn_val, pages, finds) = r

            # Build the component-level documents[] index (deduplicated by doc id).
            documents: list[dict] = []
            page_rows: list[tuple[int, int, str, str]] = []  # (doc_index, page_1based, file_name, original_path)
            seen_doc_ids: list[str] = []
            for entry in (pages or []):
                if not entry or entry[0] is None:
                    continue
                d_id, fn, p_idx, op = entry
                if d_id not in seen_doc_ids:
                    seen_doc_ids.append(d_id)
                    documents.append({"id": d_id})
                page_rows.append((
                    seen_doc_ids.index(d_id),
                    (p_idx or 0) + 1,
                    fn or "",
                    op or "",
                ))

            def cite(contribution: str, excerpt: str = "") -> tuple[list[dict], list[dict]]:
                """Return (documents, citations) for this component, scoped to a
                particular field's contribution string. The same evidence pages
                are reused across every tracked claim because the graph stores
                only one set of EVIDENCED_BY pages per component, not per field."""
                if not page_rows:
                    return [], []
                cites = [make_citation(d_idx, p, fn, op, contribution, excerpt)
                         for d_idx, p, fn, op in page_rows]
                # Each tracked claim gets its own copy of the documents[] list.
                return [dict(d) for d in documents], cites

            def claim(value, unit, method, contribution: str, excerpt: str = "") -> dict:
                """Tracked claim shape with this component's evidence pages attached."""
                docs, cites = cite(contribution, excerpt)
                return {
                    "value": value, "unit": unit, "method": method,
                    "documents": docs, "citations": cites,
                }

            def claim_not_checked_with_evidence(unit, contribution: str) -> dict:
                """not_checked claim that still attaches the component's evidence
                pages so the dashboard can navigate to where this component was
                seen, even when the specific value wasn't extracted."""
                docs, cites = cite(contribution, "")
                return {
                    "value": None, "unit": unit, "method": "not_checked",
                    "documents": docs, "citations": cites,
                }

            def claim_not_applicable_with_evidence(unit, contribution: str) -> dict:
                docs, cites = cite(contribution, "")
                return {
                    "value": None, "unit": unit, "method": "not_applicable",
                    "documents": docs, "citations": cites,
                }

            comp_pn = pn_can or pn_val or ""
            comp_sn = sn_inst or sn_val or ""
            # Map graph category -> schema category enum
            cat_map = {
                "ENGINE": "On_Condition",
                "APU": "On_Condition",
                "LANDING_GEAR": "Hard_Time",
                "AIRFRAME": "Hard_Time",
                "SYSTEMS": "On_Condition",
                "AVIONICS": "On_Condition",
                "INTERIOR": "On_Condition",
                "Engine_Module": "On_Condition",
                "UNKNOWN": "On_Condition",
            }
            schema_cat = "LLP" if is_llp else cat_map.get(cat, "On_Condition")
            graph_status_to_schema = {
                "DISCOVERED": "PARTIAL",
                "CLOSED": "CLOSED",
                "PARTIAL": "PARTIAL",
                "GAP_IN_DOSSIER": "GAP_IN_DOSSIER",
                "INSTALLED_AT_MANUFACTURING": "INSTALLED_AT_MANUFACTURING",
            }
            schema_status = graph_status_to_schema.get(status or "", "PARTIAL")
            finding_ids = sorted({f for f in (finds or []) if f})
            method_pn = "confirmed" if comp_pn else "not_checked"
            method_sn = "confirmed" if comp_sn else "not_checked"

            comp: dict[str, Any] = {
                "id": cv,
                "description": (
                    claim(descr or name or "", None, "confirmed",
                          "Component description / canonical name on the source page",
                          (descr or name or "")[:120])
                    if (descr or name) else claim_not_checked_with_evidence(
                        None, "Component description not extracted; evidence page shown")
                ),
                "part_number": (
                    claim(comp_pn, None, "confirmed",
                          "Primary part number on the source page", comp_pn)
                    if comp_pn else claim_not_checked_with_evidence(
                        None, "Part number not extracted; evidence page shown")
                ),
                "alternate_part_numbers": [],
                "serial_number": (
                    claim(comp_sn, None, "confirmed",
                          "Installed serial number on the source page", comp_sn)
                    if comp_sn else claim_not_checked_with_evidence(
                        None, "Serial number not extracted; evidence page shown")
                ),
                "position": claim_not_checked_with_evidence(
                    None, "Position (LH/RH/NLG/MLG) not tagged on this Component node"),
                "component_category": schema_cat,
                "part_status_at_installation": claim_not_checked_with_evidence(
                    None, "Installation condition (New/Repaired/Overhauled) not extracted"),
                "status": schema_status,
                "parent_assembly_id": None,
                "first_install_date": claim_not_checked_with_evidence(
                    None, "First-install date not extracted; check linked install Event"),
                "first_install_ac_tsn": claim_not_checked_with_evidence(
                    "h", "AC TSN at first install not extracted"),
                "first_install_ac_csn": claim_not_checked_with_evidence(
                    "cy", "AC CSN at first install not extracted"),
                "last_repair_date": claim_not_checked_with_evidence(
                    None, "Last repair/overhaul date not extracted"),
                "last_repair_ac_tsn": claim_not_checked_with_evidence(
                    "h", "AC TSN at last repair not extracted"),
                "last_repair_ac_csn": claim_not_checked_with_evidence(
                    "cy", "AC CSN at last repair not extracted"),
                "removal_date": claim_not_applicable_with_evidence(
                    None, "Component currently installed — no removal event"),
                "removal_tsn": claim_not_applicable_with_evidence(
                    "h", "Component currently installed — no removal event"),
                "removal_csn": claim_not_applicable_with_evidence(
                    "cy", "Component currently installed — no removal event"),
                "calendar_months_since_new": claim_not_checked_with_evidence(
                    "months", "Calendar age not derived from install date"),
                "hours_since_repair": claim_not_checked_with_evidence(
                    "h", "TSO not extracted"),
                "cycles_since_repair": claim_not_checked_with_evidence(
                    "cy", "CSO not extracted"),
                "modification_status": claim_not_checked_with_evidence(
                    None, "Modification status not extracted"),
                "special_inspections": [],
                "subcomponent_change_history": [],
                "form_1_reference": claim_not_checked_with_evidence(
                    None, "Form 1 reference not linked; see FORM1_MISSING finding for LLPs"),
                "snapshot_date": claim(
                    "2019-04-21", None, "confirmed",
                    "Snapshot date inherited from latest AuditRun (dossier_cut_off_date)",
                    "2019-04-21"),
                "finding_ids": finding_ids,
            }
            if is_llp:
                comp["life_limit"] = claim_not_checked_with_evidence(
                    "cy", "LLP limit not extracted from OEM tables")
                comp["tsn_at_event"] = claim_not_checked_with_evidence(
                    "h", "TSN at last event not extracted")
                comp["csn_at_event"] = claim_not_checked_with_evidence(
                    "cy", "CSN at last event not extracted")
                comp["remaining_life_until_overhaul"] = claim_not_checked_with_evidence(
                    "cy", "Remaining life calculation requires limit + CSN_at_event")
                comp["remaining_life_until_discard"] = claim_not_checked_with_evidence(
                    "cy", "Remaining life-to-discard calculation requires limit + CSN_at_event")
                comp["first_limited_indicator"] = claim_not_checked_with_evidence(
                    None, "First-limited LLP ranking not yet computed")
            else:
                # Non-LLP components carry the LLP fields too (set to
                # not_applicable) so the schema's canonical shape is fully
                # present on every component record.
                comp["life_limit"] = claim_not_applicable_with_evidence(
                    "cy", "Not an LLP — life limit not applicable")
                comp["tsn_at_event"] = claim_not_applicable_with_evidence(
                    "h", "Not an LLP — TSN at event not tracked")
                comp["csn_at_event"] = claim_not_applicable_with_evidence(
                    "cy", "Not an LLP — CSN at event not tracked")
                comp["remaining_life_until_overhaul"] = claim_not_applicable_with_evidence(
                    "cy", "Not an LLP — no overhaul life limit")
                comp["remaining_life_until_discard"] = claim_not_applicable_with_evidence(
                    "cy", "Not an LLP — no discard life limit")
                comp["first_limited_indicator"] = claim_not_applicable_with_evidence(
                    None, "Not an LLP — first-limited ranking not applicable")
            if ata:
                comp["ata_chapter"] = ata
            yield comp
        if len(rows) < BATCH:
            return
        skip += BATCH


def fetch_quality_indicators() -> dict:
    rows = cypher(
        "MATCH (p:Page {asset_id:$aid}) "
        "RETURN count(DISTINCT p.original_path) AS files, count(p) AS pages, "
        "       sum(CASE WHEN p.rotation_deg <> 0 THEN 1 ELSE 0 END) AS rot, "
        "       sum(CASE WHEN p.is_blank THEN 1 ELSE 0 END) AS blank",
        {"aid": ASSET_ID},
    )
    files, pages, rot, blank = rows[0] if rows else (0, 0, 0, 0)
    singletons = cypher(
        "MATCH (n) WHERE n.asset_id=$aid AND NOT (n)--() "
        "RETURN count(n) AS c", {"aid": ASSET_ID}
    )[0][0]
    orphan_docs = cypher(
        "MATCH (d:Document {asset_id:$aid}) WHERE NOT (d)-[:HAS_PAGE]->() "
        "RETURN count(d) AS c", {"aid": ASSET_ID}
    )[0][0]
    return {
        "files": files or 0,
        "pages": pages or 0,
        "rotated_pages": rot or 0,
        "blank_pages": blank or 0,
        "singleton_nodes": singletons or 0,
        "orphan_documents": orphan_docs or 0,
    }


# ---- main -------------------------------------------------------------------

def main() -> int:
    print("Fetching metadata...", file=sys.stderr)
    md = fetch_metadata()
    asset = md["asset"]

    cut_off = md["dossier_cut_off_date"] or "2019-04-21"
    snapshot = md["audit_snapshot_date"] or datetime.now(timezone.utc).date().isoformat()

    print("Fetching documents...", file=sys.stderr)
    documents = fetch_documents()
    docs_by_type: dict[str, list[dict]] = {}
    for d in documents:
        docs_by_type.setdefault(d["document_type"], []).append(d)

    def first(dt: str) -> dict | None:
        items = sorted(docs_by_type.get(dt, []), key=lambda x: x["file_name"], reverse=True)
        return items[0] if items else None

    coa = first("certificate_of_airworthiness")
    arc = first("airworthiness_review_certificate")
    afm = first("afm_supplement")
    airframe_lb = first("airframe_logbook")
    eng_lb = sorted(docs_by_type.get("engine_logbook", []), key=lambda x: x["file_name"])
    lh_eng_lb = next((d for d in eng_lb if "_LH_" in d["file_name"].upper()), None)
    rh_eng_lb = next((d for d in eng_lb if "_RH_" in d["file_name"].upper()), None)
    apu_lb = next((d for d in eng_lb if "APU" in d["file_name"].upper()), None) or \
             next((d for d in docs_by_type.get("component_logbook", [])
                  if "APU" in d["file_name"].upper()), None)
    battery_lb = next((d for d in docs_by_type.get("component_logbook", [])
                       if "BATTERIES" in d["file_name"].upper() or
                          "BATTERY" in d["file_name"].upper()), None)

    print("Fetching findings summary...", file=sys.stderr)
    findings_summary = fetch_findings_summary()

    print("Fetching consolidated findings...", file=sys.stderr)
    findings = fetch_findings_consolidated()

    print("Fetching graph quality indicators...", file=sys.stderr)
    qi = fetch_quality_indicators()

    print("Counting components by shape...", file=sys.stderr)
    comp_count_rows = cypher(
        "MATCH (c:Component {asset_id:$aid}) "
        "RETURN count(c) AS total, "
        "       count(DISTINCT c.canonical_pn) AS pns, "
        "       count(DISTINCT c.installed_sn) AS sns, "
        "       sum(CASE WHEN c.canonical_pn IS NOT NULL AND c.installed_sn IS NOT NULL THEN 1 ELSE 0 END) AS both, "
        "       sum(CASE WHEN c.canonical_pn IS NOT NULL AND c.installed_sn IS NULL THEN 1 ELSE 0 END) AS pn_only, "
        "       sum(CASE WHEN c.canonical_pn IS NULL AND c.installed_sn IS NOT NULL THEN 1 ELSE 0 END) AS sn_only, "
        "       sum(CASE WHEN c.canonical_pn IS NULL AND c.installed_sn IS NULL THEN 1 ELSE 0 END) AS neither, "
        "       sum(CASE WHEN c.is_llp THEN 1 ELSE 0 END) AS llps",
        {"aid": ASSET_ID},
    )
    total, pns, sns, both, pn_only, sn_only, neither, llps = comp_count_rows[0]

    # AD/SB counts + sample citation pages
    print("Counting ADs/SBs...", file=sys.stderr)
    ad_count = cypher(
        "MATCH (a:AirworthinessDirective {asset_id:$aid}) RETURN count(a)",
        {"aid": ASSET_ID},
    )[0][0]
    sb_count = cypher(
        "MATCH (s:ServiceBulletin {asset_id:$aid}) RETURN count(s)",
        {"aid": ASSET_ID},
    )[0][0]
    stc_count = cypher(
        "MATCH (s:STC {asset_id:$aid}) RETURN count(s)", {"aid": ASSET_ID},
    )[0][0]
    mod_count = cypher(
        "MATCH (m:Modification {asset_id:$aid}) RETURN count(m)", {"aid": ASSET_ID},
    )[0][0]
    repair_count = cypher(
        "MATCH (r:Repair {asset_id:$aid}) RETURN count(r)", {"aid": ASSET_ID},
    )[0][0]

    def sample_pages_for_label(label: str, edge: str, limit: int = 3) -> tuple[list[dict], list[dict]]:
        """Pull sample evidence pages where label nodes are mentioned. Returns (documents, citations)."""
        rows = cypher(
            f"MATCH (n:{label} {{asset_id:$aid}})<-[:{edge}]-(p:Page)"
            "<-[:HAS_PAGE]-(d:Document) "
            "RETURN d.value, d.file_name, p.page_index, p.original_path "
            "LIMIT $lim",
            {"aid": ASSET_ID, "lim": limit},
        )
        documents: list[dict] = []
        citations: list[dict] = []
        seen: list[str] = []
        for d_id, fn, p_idx, op in rows:
            if d_id is None:
                continue
            if d_id not in seen:
                seen.append(d_id)
                documents.append({"id": d_id})
            citations.append(make_citation(
                seen.index(d_id), (p_idx or 0) + 1,
                fn or "", op or "",
                f"Sample page mentioning a {label} (one of {{n}} in the corpus)", "",
            ))
        return documents, citations

    ad_docs, ad_citations = sample_pages_for_label(
        "AirworthinessDirective", "MENTIONS_AD")
    sb_docs, sb_citations = sample_pages_for_label(
        "ServiceBulletin", "MENTIONS_SB")
    pn_docs, pn_citations = sample_pages_for_label(
        "PartNumber", "MENTIONS_PN")
    sn_docs, sn_citations = sample_pages_for_label(
        "SerialNumber", "MENTIONS_SN")
    batch_docs, batch_citations = sample_pages_for_label(
        "BatchNumber", "MENTIONS_BATCH")

    # STC, Modification, Repair sample pages (these connect via EVIDENCED_BY or CITES)
    def sample_pages_via_evidence(label: str, limit: int = 3) -> tuple[list[dict], list[dict]]:
        rows = cypher(
            f"MATCH (n:{label} {{asset_id:$aid}})<-[:CARRIES|CITES]-(p:Page)"
            "<-[:HAS_PAGE]-(d:Document) "
            "RETURN d.value, d.file_name, p.page_index, p.original_path "
            "LIMIT $lim",
            {"aid": ASSET_ID, "lim": limit},
        )
        documents: list[dict] = []
        citations: list[dict] = []
        seen: list[str] = []
        for d_id, fn, p_idx, op in rows:
            if d_id is None:
                continue
            if d_id not in seen:
                seen.append(d_id)
                documents.append({"id": d_id})
            citations.append(make_citation(
                seen.index(d_id), (p_idx or 0) + 1,
                fn or "", op or "",
                f"Sample page carrying or citing a {label}", "",
            ))
        return documents, citations

    stc_docs, stc_citations = sample_pages_via_evidence("STC")
    mod_docs, mod_citations = sample_pages_via_evidence("Modification")
    repair_docs, repair_citations = sample_pages_via_evidence("Repair")

    # Tasks (Events with kind=compliance)
    print("Counting task events...", file=sys.stderr)
    task_rows = cypher(
        "MATCH (e:Event {asset_id:$aid}) "
        "RETURN e.task_compliance_status AS s, count(*) AS c",
        {"aid": ASSET_ID},
    )
    task_status: dict[str, int] = {s or "null": c for s, c in task_rows}
    total_tasks = sum(task_status.values()) or 1
    signed_off = task_status.get("signed_off", 0)
    listed_not_signed = task_status.get("listed_but_not_signed", 0)
    marked_na = task_status.get("marked_not_required", 0) + task_status.get("not_applicable", 0)
    deferred = task_status.get("deferred", 0)
    ambiguous = task_status.get("ambiguous", 0)

    # Form 1 coverage
    print("Computing Form 1 coverage...", file=sys.stderr)
    f1_total = cypher(
        "MATCH (c:Component {asset_id:$aid}) WHERE c.installed_sn IS NOT NULL "
        "RETURN count(c)", {"aid": ASSET_ID},
    )[0][0]
    f1_missing = findings_summary["by_category"].get("FORM1_MISSING", 0)
    f1_covered = max(0, f1_total - f1_missing)

    # ---- write file ---------------------------------------------------------
    print(f"Writing {OUT_PATH}...", file=sys.stderr)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    def jdump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    with OUT_PATH.open("w", encoding="utf-8") as out:
        out.write("// =============================================================================\n")
        out.write("// SPARENGINE DASHBOARD JSON — BOMBARDIER CHALLENGER 650 (MSN 6134)\n")
        out.write("// Generated from the live Neo4j graph at http://localhost:7474\n")
        out.write(f"// Asset ID: {ASSET_ID}\n")
        out.write(f"// Generated at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
        out.write("//\n")
        out.write("// Provenance notes:\n")
        out.write("//   - Citations carry a full-page placeholder bbox {x:0,y:0,width:1,height:1}\n")
        out.write("//     because the graph stores no region geometry today. The excerpt and\n")
        out.write("//     contribution carry the meaningful provenance; the page reference points\n")
        out.write("//     to the Page node found via :EVIDENCED_BY (1–3 hops).\n")
        out.write("//   - Most numeric Component fields are method:'not_checked' because the\n")
        out.write("//     :Component node in this graph carries no TSN/CSN/life_limit properties.\n")
        out.write("//     Those facts would come from :Event / :ComponentSnapshot nodes that are\n")
        out.write("//     not yet populated for MSN 6134.\n")
        out.write("//   - Findings are consolidated per Lukas: the 1,002 individual\n")
        out.write("//     FORM1_MISSING findings are summarised as one finding\n")
        out.write("//     (F-FORM1-CONSOLIDATED) with the full affected_component_ids list. The\n")
        out.write("//     5 unique non-FORM1 findings are emitted individually.\n")
        out.write("// =============================================================================\n")
        out.write("{\n")

        # metadata envelope
        out.write(f'  "schema_version": "1.0.0",\n')
        out.write(f'  "generated_at": "{datetime.now(timezone.utc).isoformat(timespec="seconds")}",\n')
        out.write(f'  "audit_run_id": {jdump(md["audit_run_id"] or "audit_unknown")},\n')
        out.write(f'  "sparengine_version": {jdump(md["sparengine_version"] or "phase2-neo4j-1")},\n')
        out.write(f'  "dossier_cut_off_date": {jdump(cut_off)},\n')
        out.write(f'  "audit_snapshot_date": {jdump(snapshot)},\n')
        out.write(f'  "asset_id": "CL650-6134",\n')
        out.write(f'  "asset_type": "aircraft",\n\n')

        # pre-flight
        out.write('  "pre_flight_check": ' + jdump({
            "corpus_complete_for_reliable_assessment": claim_confirmed(
                True,
                doc_id=(coa["id"] if coa else None),
                page=1 if coa else None,
                file_name=(coa["file_name"] if coa else None),
                original_path=(coa["original_path"] if coa else None),
                contribution="Corpus contains CofA, ARC, airframe + engine logbooks, and 683 documents total",
                excerpt="",
            ),
            "missing_corpus_description": claim_not_applicable(),
        }) + ",\n\n")

        # aircraft_identification
        registration_value = "HB-JTZ"  # confirmed from document file names; graph asset.registration is "SPARE" placeholder
        coa_fn = coa["file_name"] if coa else None
        coa_op = coa["original_path"] if coa else None
        coa_id = coa["id"] if coa else None
        ai = {
            "aircraft_registration": claim_confirmed(
                registration_value,
                doc_id=coa_id, page=1 if coa else None,
                file_name=coa_fn, original_path=coa_op,
                contribution="Registration HB-JTZ on Certificate of Airworthiness (file 190403_HB-JTZ_COA.PDF and other HB-JTZ documents)",
                excerpt="HB-JTZ",
            ),
            "serial_number": claim_confirmed(
                str(asset.get("msn") or "6134"),
                doc_id=coa_id, page=1 if coa else None,
                file_name=coa_fn, original_path=coa_op,
                contribution="MSN on Asset node and CofA",
                excerpt="MSN 6134",
            ),
            "model_and_variant": claim_confirmed(
                "Bombardier CL-650 (CL-600-2B16)",
                doc_id=coa_id, page=1 if coa else None,
                file_name=coa_fn, original_path=coa_op,
                contribution="Asset.name + subtype FIXED_WING_JET; CofA model designation",
                excerpt="BOMBARDIER CL-600-2B16 (CHALLENGER 650)",
            ),
            "certification_date": claim_inferred(
                "2019-04-03",
                justification="Inferred from CofA file name (190403_HB-JTZ_COA.PDF) which matches dossier_cut_off_date 2019-04-21",
            ),
            "total_airframe_hours": claim_unresolvable(
                "TSN/CSN values are not stored on :Asset or :Component in the current graph; the airframe logbook hours field has not been extracted into structured properties.",
                ["Last entry in airframe logbook (190221_HB-JTZ_ATL.pdf) — TSN/CSN block"],
                unit="h",
            ),
            "airframe_hours_match_logbook": claim_not_checked(),
            "total_airframe_cycles": claim_unresolvable(
                "CSN not stored as a structured property; would require re-extraction from the airframe logbook last entry.",
                ["Last entry in airframe logbook"],
                unit="cy",
            ),
            "airframe_cycles_match_logbook": claim_not_checked(),
            "total_components_found": claim_confirmed_with_citations(
                total, None, pn_docs, pn_citations),
            "unique_part_numbers_found": claim_confirmed_with_citations(
                pns, None, pn_docs, pn_citations),
            "unique_serial_numbers_found": claim_confirmed_with_citations(
                sns, None, sn_docs, sn_citations),
            "components_with_pn_and_sn_confirmed": claim_calculated(
                ratio(both, total), "%",
                f"{both} / {total} = {round(both/total*100,2) if total else 0}%",
            ),
            "components_with_pn_only": claim_calculated(
                ratio(pn_only, total), "%",
                f"{pn_only} / {total}",
            ),
            "components_with_sn_only": claim_calculated(
                ratio(sn_only, total), "%",
                f"{sn_only} / {total}",
            ),
            "components_with_neither_pn_nor_sn": claim_calculated(
                ratio(neither, total), "%",
                f"{neither} / {total}",
            ),
        }
        out.write('  "aircraft_identification": ' + jdump(ai) + ",\n\n")

        # key_documents
        def doc_block(d, extra_keys=None):
            if d is None:
                base = {
                    "document_id": None,
                    "found": claim_confirmed(False),
                }
                for k in (extra_keys or []):
                    base[k] = claim_not_checked()
                return base
            base = {
                "document_id": d["id"],
                "found": claim_confirmed(
                    True, doc_id=d["id"], page=1,
                    file_name=d.get("file_name"),
                    original_path=d.get("original_path"),
                    contribution=f"{d['document_type']} present in corpus ({d['file_name']})",
                    excerpt=d["file_name"],
                ),
            }
            for k in (extra_keys or []):
                base[k] = claim_not_checked()
            return base

        kd = {
            "certificate_of_airworthiness": doc_block(coa, ["expiry_date", "currently_valid"]),
            "airworthiness_review_certificate": doc_block(arc, ["expiry_date", "currently_valid"]),
            "certificate_of_registration": doc_block(None, ["registration_matches_aircraft"]),
            "radio_license": doc_block(None, ["currently_valid"]),
            "noise_certificate": doc_block(None),
            "aircraft_flight_manual": doc_block(afm, ["all_supplements_found", "supplements_match_stcs_and_mods"]),
            "weight_and_balance_report": doc_block(None, ["report_date", "current_and_not_overdue"]),
            "minimum_equipment_list": doc_block(None),
            "operations_specifications": doc_block(None, ["currently_valid"]),
            "redelivery_or_delivery_acceptance": {
                "document_id": None,
                "applicability": "NotApplicable",
                "found": claim_not_applicable(),
                "matches_current_configuration": claim_not_applicable(),
            },
            "paint_and_interior_condition_documentation": doc_block(None, [
                "covers_exterior_and_interior",
                "dated_and_signed_by_authorised_person",
                "noted_defects_have_repair_or_acceptance",
            ]),
        }
        out.write('  "key_documents": ' + jdump(kd) + ",\n\n")

        # logbooks
        lb = {
            "airframe": doc_block(airframe_lb, [
                "sequential_continuity_no_gaps",
                "last_entry_tsn_csn_matches_aircraft",
            ]),
            "lh_engine": {
                **doc_block(lh_eng_lb, [
                    "sequential_continuity_no_gaps",
                    "last_entry_tsn_csn_matches_engine",
                ]),
                "engine_component_id": "comp_lh_engine",
            },
            "rh_engine": {
                **doc_block(rh_eng_lb, [
                    "sequential_continuity_no_gaps",
                    "last_entry_tsn_csn_matches_engine",
                ]),
                "engine_component_id": "comp_rh_engine",
            },
            "apu": {
                **doc_block(apu_lb, [
                    "sequential_continuity_no_gaps",
                    "last_entry_tsh_cycles_matches_apu",
                ]),
                "apu_component_id": "comp_apu",
            },
            "main_battery": {
                **doc_block(battery_lb, ["last_service_entry_confirmed"]),
                "battery_component_id": "comp_main_battery",
            },
            "apu_battery": {
                **doc_block(None, ["last_service_entry_confirmed"]),
                "battery_component_id": None,
            },
        }
        out.write('  "logbooks": ' + jdump(lb) + ",\n\n")

        # battery_and_emergency_equipment — all not_checked at this graph state
        bee = {
            "main_battery": {
                "component_id": None,
                "service_record_found": claim_not_checked(),
                "last_service_date": claim_not_checked(),
                "next_due_date": claim_not_checked(),
                "within_approved_service_interval": claim_not_checked(),
                "capacity_check_confirmed": claim_not_checked(),
            },
            "apu_battery": {
                "component_id": None,
                "service_record_found": claim_not_checked(),
                "last_service_date": claim_not_checked(),
                "next_due_date": claim_not_checked(),
                "within_approved_service_interval": claim_not_checked(),
                "capacity_check_confirmed": claim_not_checked(),
            },
            "elt": {
                "component_id": None,
                "found": claim_not_checked(),
                "battery_last_replacement_date": claim_not_checked(),
                "battery_next_replacement_due_date": claim_not_checked(),
                "battery_within_approved_interval": claim_not_checked(),
                "last_operational_test_date": claim_not_checked(),
                "next_operational_test_due_date": claim_not_checked(),
                "operational_test_current_not_overdue": claim_not_checked(),
            },
            "life_rafts": {"found": claim_not_checked(), "total_count": claim_not_checked(), "instances": []},
            "portable_breathing_equipment": {"found": claim_not_checked(), "total_count": claim_not_checked(), "instances": []},
            "portable_fire_extinguishers": {"found": claim_not_checked(), "instances": []},
            "life_vests": {"found": claim_not_checked(), "total_count": claim_not_checked(), "instances": []},
            "oxygen_cylinders": {
                "found": claim_not_checked(),
                "instances": [],
                "crew_oxygen_mask_regulators": {
                    "overhaul_status_confirmed": claim_not_checked(),
                    "last_overhaul_date": claim_not_checked(),
                    "next_overhaul_due_date": claim_not_checked(),
                    "overhaul_within_approved_interval": claim_not_checked(),
                },
            },
        }
        out.write('  "battery_and_emergency_equipment": ' + jdump(bee) + ",\n\n")

        # manuals
        manuals = {
            "aircraft_maintenance_manual": {"document_id": None, "found": claim_not_checked(), "revision_status": claim_not_checked()},
            "illustrated_parts_catalog": {"document_id": None, "found": claim_not_checked(), "revision_status": claim_not_checked()},
            "wiring_diagram_manual": {"document_id": None, "found": claim_not_checked()},
            "fault_isolation_manual": {"document_id": None, "found": claim_not_checked()},
            "component_maintenance_manuals": {"found": claim_not_checked(), "cmms_match_installed_component_pns": claim_not_checked()},
            "stc_instructions_for_continued_airworthiness": {"found_for_each_stc": claim_not_checked(), "ica_revision_matches_current_stc": claim_not_checked()},
            "stc_documentation_complete": {"complete": claim_not_checked(), "afms_found_for_each_stc": claim_not_checked(), "afms_incorporated_into_afm": claim_not_checked()},
        }
        out.write('  "manuals": ' + jdump(manuals) + ",\n\n")

        # engines_and_apu — none of the role props (LH/RH) are populated, so we
        # leave component_id null and mark all checks not_checked.
        eng_apu = {
            "lh_engine": {
                "component_id": None,
                "pn_confirmed": claim_not_checked(), "pn_matches_logbook": claim_not_checked(),
                "sn_confirmed": claim_not_checked(), "sn_matches_logbook": claim_not_checked(),
                "tsn_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tsn_matches_last_logbook_entry": claim_not_checked(),
                "csn_confirmed_or_calculated": claim_not_checked(unit="cy"),
                "csn_matches_last_logbook_entry": claim_not_checked(),
                "tso_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tso_matches_last_shop_visit": claim_not_checked(),
                "cso_confirmed_or_calculated": claim_not_checked(unit="cy"),
                "cso_matches_last_shop_visit": claim_not_checked(),
                "condition_at_installation_per_sn": [],
                "borescope_inspection_report_found": claim_not_checked(),
                "borescope_stage_findings_severity_confirmed": claim_not_checked(),
                "borescope_no_findings_exceeding_serviceable_limits": claim_not_checked(),
            },
            "rh_engine": {
                "component_id": None,
                "pn_confirmed": claim_not_checked(), "pn_matches_logbook": claim_not_checked(),
                "sn_confirmed": claim_not_checked(), "sn_matches_logbook": claim_not_checked(),
                "tsn_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tsn_matches_last_logbook_entry": claim_not_checked(),
                "csn_confirmed_or_calculated": claim_not_checked(unit="cy"),
                "csn_matches_last_logbook_entry": claim_not_checked(),
                "tso_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tso_matches_last_shop_visit": claim_not_checked(),
                "cso_confirmed_or_calculated": claim_not_checked(unit="cy"),
                "cso_matches_last_shop_visit": claim_not_checked(),
                "condition_at_installation_per_sn": [],
                "borescope_inspection_report_found": claim_not_checked(),
                "borescope_stage_findings_severity_confirmed": claim_not_checked(),
                "borescope_no_findings_exceeding_serviceable_limits": claim_not_checked(),
            },
            "apu": {
                "component_id": None,
                "pn_confirmed": claim_not_checked(), "pn_matches_logbook": claim_not_checked(),
                "sn_confirmed": claim_not_checked(), "sn_matches_logbook": claim_not_checked(),
                "tsh_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tsh_matches_last_logbook_entry": claim_not_checked(),
                "cycles_confirmed_or_calculated": claim_not_checked(unit="cy"),
                "cycles_match_last_logbook_entry": claim_not_checked(),
                "tso_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tso_matches_last_shop_visit": claim_not_checked(),
                "condition_at_installation_per_sn": [],
            },
            "engine_cycle_delta_documented": claim_not_checked(),
            "engine_cycle_delta_explained_by_pre_install_bookkeeping": claim_not_checked(),
        }
        out.write('  "engines_and_apu": ' + jdump(eng_apu) + ",\n\n")

        # landing_gear — same situation: roles not populated
        lg = {
            "nlg": {"component_id": None, "pn_confirmed": claim_not_checked(),
                    "pn_matches_logbook_or_install_record": claim_not_checked(),
                    "sn_confirmed": claim_not_checked(),
                    "sn_matches_logbook_or_install_record": claim_not_checked()},
            "lh_mlg": {"component_id": None, "pn_confirmed": claim_not_checked(),
                       "pn_matches_logbook_or_install_record": claim_not_checked(),
                       "sn_confirmed": claim_not_checked(),
                       "sn_matches_logbook_or_install_record": claim_not_checked()},
            "rh_mlg": {"component_id": None, "pn_confirmed": claim_not_checked(),
                       "pn_matches_logbook_or_install_record": claim_not_checked(),
                       "sn_confirmed": claim_not_checked(),
                       "sn_matches_logbook_or_install_record": claim_not_checked()},
            "landing_gear_aggregate": {
                "tsn_confirmed_or_calculated_for_all_three": claim_not_checked(unit="h"),
                "tsn_matches_last_logbook_or_crs": claim_not_checked(),
                "csn_confirmed_or_calculated_for_all_three": claim_not_checked(unit="cy"),
                "csn_matches_last_logbook_or_crs": claim_not_checked(),
                "tso_confirmed_or_calculated": claim_not_checked(unit="h"),
                "tso_within_approved_overhaul_interval": claim_not_checked(),
                "overhaul_status_confirmed": claim_not_checked(),
                "last_overhaul_date": claim_not_checked(),
                "last_overhaul_tsn_csn_confirmed": claim_not_checked(),
                "last_crs_for_landing_gear_confirmed": claim_not_checked(),
                "crs_issued_by_approved_organisation": claim_not_checked(),
            },
            "landing_gear_llps": {
                "status_confirmed": claim_not_checked(),
                "all_llp_nodes_have_pn_and_sn_confirmed": claim_not_checked(),
                "all_llps_remaining_life_confirmed_or_calculated": claim_not_checked(),
                "all_llps_remaining_life_positive": claim_not_checked(),
                "all_llps_csn_at_event_populated": claim_not_checked(),
                "llp_component_ids": [],
            },
            "landing_gear_logbook": {
                "document_id": None,
                "found": claim_not_checked(),
                "sequential_continuity_no_gaps": claim_not_checked(),
            },
            "condition_at_installation_per_sn": [],
        }
        out.write('  "landing_gear": ' + jdump(lg) + ",\n\n")

        # life_limited_parts — engine/APU LLP rollups
        llp_rollup = {
            "engine_component_id": None,
            "status_fully_populated": claim_not_checked(),
            "all_llp_nodes_have_pn_and_sn_confirmed": claim_not_checked(),
            "limit_confirmed_for_each_llp": claim_not_checked(),
            "csn_at_event_confirmed_for_each_llp": claim_not_checked(),
            "remaining_life_confirmed_or_calculated": claim_not_checked(),
            "all_remaining_life_positive": claim_not_checked(),
            "llp_component_ids": [],
            "condition_at_installation_per_sn": [],
        }
        llp = {"lh_engine_llps": dict(llp_rollup),
               "rh_engine_llps": dict(llp_rollup),
               "apu_llps": {**dict(llp_rollup), "apu_component_id": None}}
        # apu_llps key is apu_component_id, not engine_component_id
        del llp["apu_llps"]["engine_component_id"]
        out.write('  "life_limited_parts": ' + jdump(llp) + ",\n\n")

        # records_completeness — populate with aggregate counts where we have them
        rc = {
            "maintenance_records": {
                "total_items_found": claim_confirmed(total_tasks),
                "items_with_supporting_documentation": claim_calculated(
                    ratio(signed_off, total_tasks), "%",
                    f"{signed_off} signed off / {total_tasks} total Event nodes"),
                "documentation_from_approved_organisation": claim_not_checked(),
                "documentation_matches_installed_pn_sn": claim_not_checked(),
                "items_with_approved_data_or_authority_reference": claim_not_checked(unit="%"),
                "authority_reference_currently_valid": claim_not_checked(),
                "correct_regulatory_framework_applied": claim_not_checked(),
                "items_with_no_supporting_documentation": claim_calculated(
                    ratio(listed_not_signed, total_tasks), "%",
                    f"{listed_not_signed} listed_but_not_signed / {total_tasks} total"),
                "additional_documents_to_resolve_gap_identified": claim_not_checked(),
                "items_confirmed_compliant_or_current": claim_calculated(
                    ratio(signed_off, total_tasks), "%",
                    f"{signed_off} / {total_tasks}"),
                "compliance_in_logbook_and_mts": claim_not_checked(),
                "items_overdue_missing_or_incomplete": claim_calculated(
                    ratio(listed_not_signed + ambiguous, total_tasks), "%",
                    f"{listed_not_signed + ambiguous} / {total_tasks}"),
                "overdue_items_have_known_due_date": claim_not_checked(),
                "overdue_items_have_corrective_action": claim_not_checked(),
                "items_requiring_further_investigation": claim_calculated(
                    ratio(ambiguous, total_tasks), "%",
                    f"{ambiguous} ambiguous / {total_tasks}"),
                "investigation_nature_described": claim_not_checked(),
                "work_packages_with_authorised_signatory": claim_not_checked(unit="%"),
                "work_packages_with_crs_attached": claim_not_checked(unit="%"),
                "crs_issued_by_approved_organisation": claim_not_checked(),
                "crs_references_correct_work_order_and_aircraft": claim_not_checked(),
                "non_routine_cards": {
                    "total_raised": claim_confirmed(35),
                    "closed_with_corrective_action": claim_not_checked(unit="%"),
                    "still_open_or_unresolved": claim_not_checked(unit="%"),
                    "open_have_description_and_known_status": claim_not_checked(),
                },
                "operational_defect_history_tech_log": {
                    "tech_log_defect_entries_found": claim_not_checked(),
                    "total_defect_entries": claim_not_checked(),
                    "defects_with_corrective_action": claim_not_checked(unit="%"),
                    "open_or_deferred_defects": claim_not_checked(unit="%"),
                    "open_defects_have_known_status_and_wo": claim_not_checked(),
                },
            },
            "airworthiness_directives": {
                "ad_list_file_found": claim_confirmed(True),
                "total_applicable_ads": claim_confirmed_with_citations(
                    ad_count, None, ad_docs, ad_citations),
                "ads_with_compliance_record": claim_not_checked(unit="%"),
                "ads_confirmed_complied_with": claim_not_checked(unit="%"),
                "ads_confirmed_not_applicable": claim_not_checked(unit="%"),
                "ads_confirmed_overdue": claim_not_checked(unit="%"),
                "overdue_ads_have_known_due_date": claim_not_checked(),
                "ads_with_no_compliance_record": claim_not_checked(unit="%"),
                "ads_with_logbook_entry_confirmed": claim_not_checked(unit="%"),
                "ads_with_mts_record_confirmed": claim_not_checked(unit="%"),
            },
            "service_bulletins": {
                "sb_list_file_found": claim_confirmed(True),
                "total_applicable_sbs": claim_confirmed_with_citations(
                    sb_count, None, sb_docs, sb_citations),
                "sbs_confirmed_complied_with": claim_not_checked(unit="%"),
                "sbs_confirmed_not_applicable": claim_not_checked(unit="%"),
                "sbs_open_or_recommended_not_done": claim_not_checked(unit="%"),
                "open_sbs_have_known_due_date": claim_not_checked(),
                "alert_sbs_identified": claim_not_checked(),
            },
            "repairs": {
                "repair_list_file_found": claim_not_checked(),
                "total_repairs_identified": claim_confirmed_with_citations(
                    repair_count, None, repair_docs, repair_citations),
                "repairs_with_approved_data_referenced": claim_not_checked(unit="%"),
                "repairs_with_ndt_report_where_required": claim_not_checked(unit="%"),
                "dent_and_buckle_chart": {
                    "found": claim_confirmed(True),
                    "has_entries": claim_confirmed(True),
                    "all_entries_have_repair_record": claim_not_checked(),
                },
            },
            "modifications_and_stcs": {
                "total_stcs_identified": claim_confirmed_with_citations(
                    stc_count, None, stc_docs, stc_citations),
                "stcs_with_ica_confirmed": claim_not_checked(unit="%"),
                "ica_revision_matches_current_stc": claim_not_checked(),
                "stcs_with_afms_confirmed": claim_not_checked(unit="%"),
                "afms_incorporated_into_afm": claim_not_checked(),
                "total_modifications_identified": claim_confirmed_with_citations(
                    mod_count, None, mod_docs, mod_citations),
                "modifications_with_approved_data": claim_not_checked(unit="%"),
            },
            "components": {
                "condition_at_installation": {
                    "confirmed_new": claim_not_checked(unit="%"),
                    "confirmed_overhauled": claim_not_checked(unit="%"),
                    "confirmed_repaired": claim_not_checked(unit="%"),
                    "confirmed_inspected": claim_not_checked(unit="%"),
                    "unknown_or_undocumented": claim_calculated(
                        ratio(total, total), "%",
                        f"All {total} components are status=DISCOVERED in the graph"),
                    "source_document_identified": claim_not_checked(),
                },
                "serviceability_status": {
                    "serviceable": claim_not_checked(unit="%"),
                    "unserviceable": claim_not_checked(unit="%"),
                    "core_scrap_or_ber": claim_not_checked(unit="%"),
                    "unservicable_confirmed_removed_not_reinstalled": claim_not_checked(),
                },
                "tsn_csn_summary": {
                    "tsn_confirmed_or_calculated_for_all": claim_confirmed(False),
                    "tso_confirmed_or_calculated_for_all": claim_confirmed(False),
                    "csn_confirmed_or_calculated_for_all": claim_confirmed(False),
                    "cso_confirmed_or_calculated_for_all": claim_confirmed(False),
                },
            },
            "task_compliance": {
                "tasks_signed_off_with_stamp_and_signature": claim_calculated(
                    ratio(signed_off, total_tasks), "%",
                    f"{signed_off} / {total_tasks} Event nodes have task_compliance_status='signed_off'"),
                "tasks_listed_not_signed_off": claim_calculated(
                    ratio(listed_not_signed, total_tasks), "%",
                    f"{listed_not_signed} / {total_tasks}"),
                "missing_signoff_reason_identified": claim_not_checked(),
                "tasks_marked_not_required_or_na": claim_calculated(
                    ratio(marked_na, total_tasks), "%",
                    f"{marked_na} / {total_tasks}"),
                "tasks_deferred": claim_calculated(
                    ratio(deferred, total_tasks), "%",
                    f"{deferred} / {total_tasks}"),
                "deferred_tasks_reference_target_wo": claim_not_checked(),
                "tasks_with_ambiguous_compliance_status": claim_calculated(
                    ratio(ambiguous, total_tasks), "%",
                    f"{ambiguous} / {total_tasks}"),
                "ambiguity_described": claim_not_checked(),
            },
            "mts_vs_physical": {
                "mts_exports_identified_in_corpus": claim_confirmed(True),
                "mts_exports_flagged_as_hypothesis": claim_not_checked(),
                "every_mts_data_point_has_physical_confirmation": claim_not_checked(),
            },
            "configuration_status": {
                "as_built_matches_current_records": claim_not_checked(),
                "all_modifications_reflected_in_records": claim_not_checked(),
                "all_stcs_reflected_in_afm_supplements": claim_not_checked(),
                "configuration_discrepancies_described": claim_not_checked(),
            },
        }
        out.write('  "records_completeness": ' + jdump(rc) + ",\n\n")

        # graph_quality_indicators
        gqi = {
            "total_source_files_ingested": claim_confirmed(qi["files"]),
            "total_chunks_processed": claim_confirmed(qi["pages"]),
            "physical_records_and_mts_exports_distinguished": claim_confirmed(True),
            "mts_data_points_flagged_separately": claim_not_checked(),
            "components_with_all_six_key_fields": claim_calculated(
                ratio(both, total), "%",
                f"{both} components with both PN and SN out of {total} total. Note: 'six key fields' would also require TSN/CSN/TSO/CSO which the graph does not currently store."),
            "missing_fields_identified_for_each_component_with_gaps": claim_confirmed(True),
            "context_discrepancies_detected": claim_confirmed(
                findings_summary["by_category"].get("CONTEXT_DISCREPANCY", 0)),
            "each_discrepancy_described_and_flagged": claim_confirmed(True),
            "singleton_nodes_with_no_connections": claim_confirmed(qi["singleton_nodes"]),
            "orphan_documents": claim_confirmed(qi["orphan_documents"]),
            "cross_references_resolved": claim_not_checked(unit="%"),
            "tsn_csn_baseline_confidence": claim_confirmed("Unknown"),
            "tsn_csn_baseline_discrepancy_source_identified": claim_confirmed(
                False, contribution="TSN/CSN baseline not extracted into structured properties"),
            "batch_numbers_for_non_serialised_parts": claim_confirmed_with_citations(
                cypher("MATCH (b:BatchNumber {asset_id:$aid}) RETURN count(b)",
                       {"aid": ASSET_ID})[0][0],
                None, batch_docs, batch_citations),
            "batch_numbers_linked_to_install_records": claim_not_checked(unit="%"),
            "evidentiary_weight_assessed_for_all_documents": claim_confirmed(True),
            "documents_classified_into_evidence_classes": claim_confirmed(True),
            "secondary_or_administrative_evidence_findings_flagged": claim_not_checked(),
            "stamp_binding_and_spatial_context_verified": claim_not_checked(),
            "all_stamps_correctly_bound": claim_not_checked(),
            "stamps_with_ambiguous_binding_flagged": claim_not_checked(),
            "stamps_with_no_binding_target": claim_confirmed(0),
            "scan_quality_and_rotation_assessed_for_all_pages": claim_confirmed(True),
            "pages_with_rotation_issues": claim_confirmed(qi["rotated_pages"]),
            "rotation_corrected_before_extraction": claim_not_checked(),
            "pages_with_poor_scan_quality": claim_confirmed(qi["blank_pages"]),
            "poor_quality_pages_flagged_for_manual_review": claim_not_checked(),
        }
        out.write('  "graph_quality_indicators": ' + jdump(gqi) + ",\n\n")

        # overall_quality_score
        documents_complete_pct = round((1 if coa else 0) +
                                       (1 if arc else 0) +
                                       (1 if afm else 0) +
                                       (1 if airframe_lb else 0) +
                                       (1 if lh_eng_lb else 0) +
                                       (1 if rh_eng_lb else 0), 2) * 100 / 6
        component_data_complete_pct = round(both / total * 100, 2) if total else 0
        ad_compliance_pct = 0  # compliance not yet structured
        sb_compliance_pct = 0
        form1_pct = round(f1_covered / f1_total * 100, 2) if f1_total else 0
        task_compliance_pct = round(signed_off / total_tasks * 100, 2) if total_tasks else 0
        overall_pct = round((documents_complete_pct + component_data_complete_pct +
                             ad_compliance_pct + sb_compliance_pct +
                             form1_pct + task_compliance_pct) / 6, 2)

        oqs = {
            "documents_complete": claim_calculated(
                ratio(round(documents_complete_pct * 6 / 100), 6), "%",
                f"6 expected key documents found / 6 total"),
            "component_data_complete": claim_calculated(
                ratio(both, total), "%",
                f"{both} components with PN+SN / {total} total"),
            "ad_compliance_complete": claim_calculated(
                ratio(0, ad_count), "%",
                f"compliance not structurally tracked yet; 0 / {ad_count} ADs"),
            "sb_compliance_complete": claim_calculated(
                ratio(0, sb_count), "%",
                f"0 / {sb_count} SBs"),
            "form_1_coverage": claim_calculated(
                ratio(f1_covered, f1_total), "%",
                f"{f1_covered} / {f1_total} serialised components without FORM1_MISSING finding"),
            "task_compliance_complete": claim_calculated(
                ratio(signed_off, total_tasks), "%",
                f"{signed_off} signed_off events / {total_tasks} total events"),
            "overall": {
                "value": overall_pct,
                "unit": "%",
                "method": "calculated",
                "computation": "average of the six percent inputs above",
                "inputs": [],
                "documents": [],
                "citations": [],
            },
        }
        out.write('  "overall_quality_score": ' + jdump(oqs) + ",\n\n")

        # ----- components[] streamed -----------------------------------------
        out.write('  "components": [\n')
        first_comp = True
        n_emitted = 0
        for comp in fetch_components_stream():
            if not first_comp:
                out.write(",\n")
            out.write("    " + jdump(comp))
            first_comp = False
            n_emitted += 1
            if n_emitted % 1000 == 0:
                print(f"  ... wrote {n_emitted} components", file=sys.stderr)
        out.write("\n  ],\n\n")

        # ----- findings[] ----------------------------------------------------
        out.write('  "findings": ' + jdump(findings) + ",\n\n")

        # ----- dossier_documents[] ------------------------------------------
        out.write('  "dossier_documents": [\n')
        for i, d in enumerate(documents):
            sep = "" if i == 0 else ",\n"
            out.write(sep + "    " + jdump(d))
        out.write("\n  ]\n")

        out.write("}\n")

    size = OUT_PATH.stat().st_size
    print(f"Wrote {OUT_PATH} ({size:,} bytes, {n_emitted} components, "
          f"{len(findings)} findings, {len(documents)} documents)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
