"""Phase 1 — Corpus Indexing (Neo4j edition).

Streams the dossier CSV row by row, parses ``extracted_json``, and
writes every page-level fact into the per-asset Neo4j graph through
the ``graph_dal`` chokepoint. No raw Cypher in this file.

What this phase produces (per the migration plan, Q12):
    - :Document, :Page                            — one each per document/CSV row
    - :Folder, :Box, :Binder                      — when carrier hierarchy is known
    - :Stamp + :HAS_STAMP                          — every stamp found
    - :DocumentType                                — taxonomy node
    - All 11 evidence records (Form1/CRS/JobCard/NRC/Repair/Modification/
      STC/WorkPackage/BorescopeReport/NDTReport/DentBuckleEntry) extracted
      from page document_type via ``_doctype_to_record``
    - All connector identifiers (PartNumber, SerialNumber, CertificateNumber,
      PurchaseOrder, DrawingNumber, BatchNumber, TechLogPage, :Reference)
    - All :Date nodes referenced by any of the above
    - All MENTIONS_*/REFS/COVERS_ATA/CITES/HAS_STAMP/CARRIES/ON_DATE edges
    - The :Page.text fulltext index is already created by schema.cypher.

What this phase does NOT produce:
    - :Component, :Event, :ComponentSnapshot — Phase 4 / 5
    - :Asset secondary class label (:Aircraft, :Engine, ...) — Phase 2
    - :Tier — killed in Q6; ATA replaces top-down grouping
    - Cross-doc connector relationships (INCLUDES, COMPLIES_WITH, ...) — Phase 6
    - :Finding — Phases 7-9

Output:
    Counts written to ``progress.log``. The phase fails (raises
    ``VerificationFailed``) if any of the Phase 1 mandatory rules are
    violated — see ``graph_dal.verify.verify_phase_1``.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

import orjson
import pandas as pd


# -----------------------------------------------------------------------------
#  graph_dal path bootstrap (works in Docker container AND on local dev host)
# -----------------------------------------------------------------------------

def _bootstrap_graph_dal() -> None:
    """Locate the sparengine-export/ directory and put it on sys.path.

    Walks up from this file looking for ``sparengine-export/graph_dal/``.
    Works in two layouts:
      - Docker:    /app/csvs/<asset>/phase1.py + /app/sparengine-export/graph_dal/
      - Local:     <repo>/csvs/<asset>/phase1.py + <repo>/sparengine-export/graph_dal/
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "sparengine-export" / "graph_dal"
        if candidate.is_dir():
            sys.path.insert(0, str(candidate.parent))
            return
    raise RuntimeError(
        "phase1.py: could not locate sparengine-export/graph_dal/. "
        "Set PYTHONPATH to the directory containing graph_dal/, "
        "or run from inside a checkout that has sparengine-export/graph_dal/ as a sibling."
    )


_bootstrap_graph_dal()

from graph_dal import connect, database_name                        # noqa: E402
from graph_dal.asset import write_asset                              # noqa: E402
from graph_dal.connector import (                                    # noqa: E402
    REFERENCE_TYPES,
    link_mentions_batch,
    link_mentions_cert,
    link_mentions_drawing,
    link_mentions_pn,
    link_mentions_po,
    link_mentions_sn,
    link_mentions_techlog_page,
    link_refs,
    write_batch_number,
    write_certificate_number,
    write_drawing_number,
    write_part_number,
    write_purchase_order,
    write_reference,
    write_serial_number,
    write_tech_log_page,
)
from graph_dal.document import (                                     # noqa: E402
    write_document,
    write_document_type,
    write_page,
)
from graph_dal.errors import GoldenRuleViolation, VerificationFailed  # noqa: E402
from graph_dal.evidence import (                                     # noqa: E402
    write_borescope_report,
    write_crs,
    write_dent_buckle_entry,
    write_form1,
    write_job_card,
    write_modification,
    write_ndt_report,
    write_non_routine_card,
    write_repair,
    write_stc,
    write_work_package,
)
from graph_dal.external_standards import (                           # noqa: E402
    link_cites,
    link_covers_ata,
    link_mentions_ad,
    link_mentions_eo,
    link_mentions_sb,
    write_airworthiness_directive,
    write_ata_chapter,
    write_engineering_order,
    write_regulatory_ref,
    write_service_bulletin,
)
from graph_dal.stamp import write_stamp                              # noqa: E402
from graph_dal.verify import verify_phase_1, verify_schema           # noqa: E402
from graph_dal._doctype_to_record import derive_evidence_record_kinds  # noqa: E402


# -----------------------------------------------------------------------------
#  Reference-type alias map (entity.entity_type → :Reference.ref_type)
# -----------------------------------------------------------------------------
#
# The OCR's ``content.metadata.reference_numbers[].type`` and
# ``content.entities[].entity_type`` use a wider vocabulary than the
# closed-enum REFERENCE_TYPES we collapsed to (Q10). This map normalises
# the loose vocabulary onto our closed set, OR routes well-known types
# to their dedicated label (PN, SN, etc.) instead of :Reference.

# Routes that go to typed connector labels (NOT :Reference).
TYPED_CONNECTOR_BY_OCR_TYPE = {
    "part_number":         "pn",
    "serial_number":       "sn",
    "esn":                 "sn",       # engine SN is still an SN
    "msn":                 "sn",       # aircraft serial — modeled as SN per dossier
    "certificate_number":  "cert",
    "approval_number":     "cert",     # treated as a cert
    "task_card_number":    "tc_card",   # routes to :JobCard mention path (handled differently)
    "nrc_number":          "nrc_card",  # routes to :NonRoutineCard mention path
    "work_order":          "wo",        # routes to :WorkPackage mention path
    "ata_chapter":         "ata",
    "sb_number":           "sb",
    "ad_number":           "ad",
    "batch_number":        "batch",
    "drawing_number":      "drawing",
}

# Routes that go to :Reference {ref_type, value}.
REFERENCE_TYPE_BY_OCR_TYPE = {
    # Direct passthrough where REFERENCE_TYPES contains the same string
    "approval":   "approval",
    "tracking":   "tracking",
    "report":     "report",
    "amendment":  "amendment",
    "doc_control": "doc_control",
    "config":     "config",
    "project":    "project",
    "docket":     "docket",
    "invoice":    "invoice",
}


# -----------------------------------------------------------------------------
#  Per-record value derivation
# -----------------------------------------------------------------------------

def _stable_record_value(kind: str, page_uid: str, fallback_seed: str | None = None) -> str:
    """Derive the canonical natural-key for an evidence-record node.

    For records where the OCR didn't extract a canonical number (or the
    upstream pipeline didn't surface one), we fall back to a per-page key
    so the record is still uniquely identifiable. Phase 6 may later resolve
    these fallback values to canonical numbers via cross-doc matching.
    """
    if fallback_seed:
        return fallback_seed
    return f"{kind}::{page_uid}"


def _find_canonical_value(entities: list[dict], entity_type: str) -> str | None:
    """Pick the highest-confidence value of a given entity_type from a page."""
    candidates = [
        e for e in entities
        if e.get("entity_type") == entity_type and e.get("value")
    ]
    if not candidates:
        return None
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda e: confidence_rank.get(e.get("confidence"), 9))
    return str(candidates[0]["value"]).strip()


# -----------------------------------------------------------------------------
#  Page-level evidence-record extraction
# -----------------------------------------------------------------------------

def _write_evidence_records_for_page(
    tx,
    *,
    asset_id: str,
    page_uid: str,
    document_type: str | None,
    title: str | None,
    text_content: str,
    entities: list[dict],
    metadata: dict,
) -> int:
    """Write one or more evidence-record nodes anchored to this page.

    Returns the count of records written (0 if document_type doesn't map).

    Quote: the text used as the verbatim quote on the :CARRIES edge.
    Prefer ``content.title`` when present; else fall back to a bounded
    head of ``text_content``; else "(unknown — see page)" as last resort.
    """
    kinds = derive_evidence_record_kinds(document_type)
    if not kinds:
        return 0

    quote = title or text_content[:240].strip() or f"(see {page_uid}, doctype={document_type!r})"
    written = 0

    for kind in kinds:
        if kind == "form1":
            value = _stable_record_value(
                "form1", page_uid,
                _find_canonical_value(entities, "approval_number"),
            )
            kind_str = None
            if document_type == "easa_form_one":
                kind_str = "easa"
            elif document_type == "faa_form_8130":
                kind_str = "faa"
            elif document_type == "tcca_form_one":
                kind_str = "tcca"
            elif document_type == "dual_release_certificate":
                kind_str = "dual"
            block_13 = (metadata.get("dates") or [None])[0]
            write_form1(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
                kind=kind_str, block_13_date_iso=block_13,
            )
        elif kind == "crs":
            value = _stable_record_value("crs", page_uid)
            date_iso = (metadata.get("dates") or [None])[0]
            write_crs(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
                date_iso=date_iso,
            )
        elif kind == "work_package":
            wo = _find_canonical_value(entities, "work_order")
            value = _stable_record_value("wp", page_uid, wo)
            date_iso = (metadata.get("dates") or [None])[0]
            write_work_package(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
                date_iso=date_iso,
            )
        elif kind == "job_card":
            tc = _find_canonical_value(entities, "task_card_number")
            value = _stable_record_value("jc", page_uid, tc)
            ata_list = metadata.get("ata_chapters") or []
            ata = ata_list[0] if ata_list else None
            write_job_card(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
                ata=ata,
            )
        elif kind == "non_routine_card":
            nrc = _find_canonical_value(entities, "nrc_number")
            value = _stable_record_value("nrc", page_uid, nrc)
            write_non_routine_card(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
            )
        elif kind == "repair":
            value = _stable_record_value("repair", page_uid)
            write_repair(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
            )
        elif kind == "modification":
            value = _stable_record_value("mod", page_uid)
            write_modification(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
            )
        elif kind == "stc":
            value = _stable_record_value("stc", page_uid)
            write_stc(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
            )
        elif kind == "borescope_report":
            value = _stable_record_value("bs", page_uid)
            date_iso = (metadata.get("dates") or [None])[0]
            write_borescope_report(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
                date_iso=date_iso,
            )
        elif kind == "ndt_report":
            value = _stable_record_value("ndt", page_uid)
            write_ndt_report(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
            )
        elif kind == "dent_buckle_entry":
            # One per page in Phase 1; per-table-row entries can be derived later.
            value = _stable_record_value("dbe", page_uid)
            write_dent_buckle_entry(
                tx, asset_id=asset_id, value=value,
                evidence_page_uid=page_uid, evidence_quote=quote,
            )
        written += 1
    return written


# -----------------------------------------------------------------------------
#  Mention-edge writing per page
# -----------------------------------------------------------------------------

def _write_mentions_for_page(
    tx,
    *,
    asset_id: str,
    page_uid: str,
    metadata: dict,
    entities: list[dict],
    blocked_sn_set: set[str],
) -> dict[str, int]:
    """Create connector-identifier nodes mentioned by this page + the
    typed mention edges. Skips :SerialNumber that match ``blocked_sn_set``.

    Returns per-edge-type counts for diagnostics.
    """
    counts = {
        "pn": 0, "sn": 0, "sn_blocked": 0,
        "cert": 0, "po": 0, "drawing": 0, "batch": 0, "techlog": 0,
        "ata": 0, "sb": 0, "ad": 0, "eo": 0, "regref": 0, "ref": 0,
    }

    # Part numbers
    for pn in metadata.get("part_numbers", []) or []:
        v = str(pn).strip()
        if not v:
            continue
        write_part_number(tx, asset_id=asset_id, value=v)
        link_mentions_pn(
            tx, asset_id=asset_id, source_label="Page",
            source_uid=page_uid, target_value=v, level="page",
        )
        counts["pn"] += 1

    # Serial numbers — check blocklist
    sn_meta = metadata.get("serial_numbers") or []
    sn_single = metadata.get("serial_number")
    if sn_single:
        sn_meta = list(sn_meta) + [sn_single]
    seen_sns: set[str] = set()
    for sn in sn_meta:
        v = str(sn).strip()
        if not v or v in seen_sns:
            continue
        seen_sns.add(v)
        if v.upper() in blocked_sn_set:
            counts["sn_blocked"] += 1
            continue
        write_serial_number(tx, asset_id=asset_id, value=v)
        link_mentions_sn(
            tx, asset_id=asset_id, source_label="Page",
            source_uid=page_uid, target_value=v, level="page",
        )
        counts["sn"] += 1

    # ATA chapters
    for ata in metadata.get("ata_chapters", []) or []:
        v = str(ata).strip()
        if not v:
            continue
        write_ata_chapter(tx, asset_id=asset_id, value=v)
        link_covers_ata(
            tx, asset_id=asset_id, source_label="Page",
            source_uid=page_uid, target_value=v, level="page",
        )
        counts["ata"] += 1

    # Regulatory references
    for rr in metadata.get("regulatory_references", []) or []:
        v = str(rr).strip()
        if not v:
            continue
        write_regulatory_ref(tx, asset_id=asset_id, value=v)
        link_cites(
            tx, asset_id=asset_id, source_label="Page",
            source_uid=page_uid, target_value=v, level="page",
        )
        counts["regref"] += 1

    # Reference numbers (typed list — `metadata.reference_numbers[]`)
    for rn in metadata.get("reference_numbers", []) or []:
        if not isinstance(rn, dict):
            continue
        rt = (rn.get("type") or "").strip()
        rv = str(rn.get("value") or "").strip()
        if not rv:
            continue
        # Route to typed connector label first
        target = TYPED_CONNECTOR_BY_OCR_TYPE.get(rt)
        if target == "wo":
            # WorkPackage will be written separately via doctype path; here
            # we only emit the MENTIONS_WO edge if the WP node exists.
            # Phase 6 will reconcile floating WO mentions against WP nodes.
            continue
        elif target == "tc_card":
            continue   # handled in evidence-record extraction
        elif target == "nrc_card":
            continue
        elif target == "cert":
            write_certificate_number(tx, asset_id=asset_id, value=rv)
            link_mentions_cert(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=rv, level="page",
            )
            counts["cert"] += 1
        elif target == "drawing":
            write_drawing_number(tx, asset_id=asset_id, value=rv)
            link_mentions_drawing(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=rv, level="page",
            )
            counts["drawing"] += 1
        elif target == "batch":
            write_batch_number(tx, asset_id=asset_id, value=rv)
            link_mentions_batch(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=rv, level="page",
            )
            counts["batch"] += 1
        elif target == "sb":
            write_service_bulletin(tx, asset_id=asset_id, value=rv)
            link_mentions_sb(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=rv, level="page",
            )
            counts["sb"] += 1
        elif target == "ad":
            write_airworthiness_directive(tx, asset_id=asset_id, value=rv)
            link_mentions_ad(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=rv, level="page",
            )
            counts["ad"] += 1
        else:
            # Long-tail :Reference
            ref_type = REFERENCE_TYPE_BY_OCR_TYPE.get(rt)
            if not ref_type:
                # Unknown type: best-effort fold into "report"
                ref_type = "report"
            if ref_type in REFERENCE_TYPES:
                write_reference(tx, asset_id=asset_id, ref_type=ref_type, value=rv)
                link_refs(
                    tx, asset_id=asset_id, source_label="Page",
                    source_uid=page_uid, ref_type=ref_type, target_value=rv, level="page",
                )
                counts["ref"] += 1

    # Entities — surface mentions the OCR caught that aren't in metadata.
    # (Defensive: metadata.part_numbers/serial_numbers may be empty even
    # when entities[] has them.)
    for ent in entities:
        et = (ent.get("entity_type") or "").strip()
        ev = str(ent.get("value") or "").strip()
        if not ev:
            continue
        target = TYPED_CONNECTOR_BY_OCR_TYPE.get(et)
        if target == "pn":
            write_part_number(tx, asset_id=asset_id, value=ev)
            link_mentions_pn(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=ev, level="page",
            )
            counts["pn"] += 1
        elif target == "sn":
            if ev.upper() in blocked_sn_set:
                counts["sn_blocked"] += 1
                continue
            write_serial_number(tx, asset_id=asset_id, value=ev)
            link_mentions_sn(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=ev, level="page",
            )
            counts["sn"] += 1
        elif target == "cert":
            write_certificate_number(tx, asset_id=asset_id, value=ev)
            link_mentions_cert(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=ev, level="page",
            )
            counts["cert"] += 1
        elif target == "sb":
            write_service_bulletin(tx, asset_id=asset_id, value=ev)
            link_mentions_sb(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=ev, level="page",
            )
            counts["sb"] += 1
        elif target == "ad":
            write_airworthiness_directive(tx, asset_id=asset_id, value=ev)
            link_mentions_ad(
                tx, asset_id=asset_id, source_label="Page",
                source_uid=page_uid, target_value=ev, level="page",
            )
            counts["ad"] += 1
        # Other entity types are handled in later phases (component
        # construction, event extraction).

    return counts


# -----------------------------------------------------------------------------
#  Stamp ingestion
# -----------------------------------------------------------------------------

def _write_stamps_for_page(
    tx,
    *,
    asset_id: str,
    page_uid: str,
    page_title: str | None,
    stamps: list[dict],
) -> int:
    """Write all stamps on a page. Returns the count written."""
    written = 0
    for i, st in enumerate(stamps):
        if not isinstance(st, dict):
            continue
        # Real OCR output rarely supplies stamp_id; fall back to position
        # within the page's stamps_and_signatures array. As long as the
        # OCR pass is deterministic, the same stamp lands at the same
        # index across re-runs.
        local_id = st.get("stamp_id") or st.get("id") or f"st_{i}"
        full_id = f"{page_uid}::{local_id}"
        binds = st.get("binds_to") or {}
        binding_status = (
            "bound" if binds.get("binding_confidence") == "high"
            else "ambiguous" if binds.get("binding_confidence") == "ambiguous"
            else "unbound" if not binds.get("target_ref")
            else "ambiguous"
        )
        text = st.get("text") or st.get("person_name") or page_title or "(stamp on page)"
        write_stamp(
            tx,
            asset_id=asset_id,
            value=full_id,
            page_uid=page_uid,
            evidence_quote=text[:240],
            type=st.get("type"),
            text=st.get("text"),
            person_name=st.get("person_name"),
            title_role=st.get("title_role"),
            date_iso=st.get("date"),
            certificate_number=st.get("certificate_number"),
            location_context=st.get("location_context"),
            binding_status=binding_status,
        )
        written += 1
    return written


# -----------------------------------------------------------------------------
#  Main
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 — Corpus indexing into Neo4j")
    parser.add_argument("--csv", required=True, help="Path to the dossier CSV.")
    parser.add_argument("--workdir", required=True, help="Asset workdir (holds asset_profile.json + progress.log).")
    parser.add_argument("--asset-id", required=False, help="Asset UUID. Defaults to the asset_id field on row 0 of the CSV.")
    parser.add_argument("--chunksize", type=int, default=500)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    csv_path = Path(args.csv).resolve()
    profile_path = workdir / "asset_profile.json"
    log_path = workdir / "progress.log"

    if not profile_path.exists():
        raise FileNotFoundError(
            f"asset_profile.json not found at {profile_path}. Run Phase 0 first."
        )
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    blocked_sn_set: set[str] = {
        str(b).strip().upper()
        for b in (profile.get("blocked_sn_list") or [])
        if b
    }
    profile_asset_id = profile.get("asset_id") or args.asset_id

    # Probe asset_id from the CSV if not supplied (defensive — the CSV
    # always carries a single asset_id).
    asset_id = args.asset_id or profile_asset_id
    if not asset_id:
        sample = pd.read_csv(csv_path, nrows=1)
        asset_id = str(sample["asset_id"].iloc[0])
    asset_id = str(asset_id)

    print(f"phase1: asset_id={asset_id}  csv={csv_path}  workdir={workdir}", flush=True)

    # Open driver. Schema must already be applied (run schema.cypher first).
    driver = connect()
    try:
        # Confirm schema is in place; refuse to proceed if not.
        verify_schema(driver)

        # Seed the :Asset node (Phase 0 should have populated asset_profile).
        # The profile shape varies — some fields are nested dicts
        # (e.g. ``registration: {current, history}``, ``identifier: {msn,...}``);
        # others are flat. We dig into the nesting and pass primitives through
        # to write_asset, which coerces anything else to None defensively.
        identifier = profile.get("identifier") if isinstance(profile.get("identifier"), dict) else {}
        registration_block = profile.get("registration") if isinstance(profile.get("registration"), dict) else {}

        # Phase 0 uses ``subtype`` as the granular asset kind (e.g. HELICOPTER,
        # FIXED_WING_TURBOPROP). We map that to AssetKind for the secondary
        # label. Helicopters and fixed-wing all roll up to AIRCRAFT.
        subtype = profile.get("subtype")
        asset_kind_map = {
            "HELICOPTER":            "AIRCRAFT",
            "FIXED_WING_JET":        "AIRCRAFT",
            "FIXED_WING_TURBOPROP":  "AIRCRAFT",
            "FIXED_WING_PISTON":     "AIRCRAFT",
            "TURBOFAN":              "ENGINE",
            "TURBOJET":              "ENGINE",
            "TURBOPROP":             "ENGINE",
            "TURBOSHAFT":            "ENGINE",
            "PISTON":                "ENGINE",
        }
        asset_kind = (
            profile.get("asset_kind")
            or asset_kind_map.get(subtype)
            or "AIRCRAFT"
        )

        # year-of-manufacture → YYYY-01-01 (best we can do without a finer date)
        yom = profile.get("yom")
        manufacture_date_iso = f"{int(yom):04d}-01-01" if isinstance(yom, int) else None

        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_asset(
                    tx,
                    asset_id=asset_id,
                    asset_kind=asset_kind,
                    name=profile.get("type_designation") or profile.get("name"),
                    msn=identifier.get("msn") or profile.get("msn"),
                    registration=registration_block.get("current") or profile.get("registration_current"),
                    subtype=subtype,
                    country_of_registration=profile.get("operator_country"),
                    manufacture_date_iso=manufacture_date_iso,
                    delivery_date_iso=profile.get("delivery_date"),
                )
                tx.commit()

        # Stream the CSV.
        documents_seen: dict[str, list[str]] = {}   # doc_id → list of evidentiary_weight values
        document_types_seen: set[str] = set()
        rows_processed = 0
        rows_failed = 0
        pages_inserted = 0
        evidence_records_written = 0
        stamps_inserted = 0
        mention_totals: Counter[str] = Counter()

        df_iter = pd.read_csv(csv_path, chunksize=args.chunksize)

        with driver.session(database=database_name()) as session:
            for chunk_idx, chunk in enumerate(df_iter):
                with session.begin_transaction() as tx:
                    try:
                        for _, row in chunk.iterrows():
                            rows_processed += 1

                            try:
                                ext = orjson.loads(row["extracted_json"])
                            except Exception:
                                rows_failed += 1
                                continue

                            page_uid = str(row.get("id", ""))
                            doc_uid = str(row.get("document_id", ""))
                            file_name = str(row.get("file_name", ""))
                            page_index = int(row.get("page_index", 0) or 0)
                            original_path = str(row.get("original_path", ""))
                            s3_key = str(row.get("enhanced_s3_key", ""))

                            content = ext.get("content") or {}
                            doc_type = (
                                content.get("document_type")
                                or ext.get("document_type")
                                or ""
                            ) or None
                            evidentiary_weight = (
                                content.get("evidentiary_weight")
                                or ext.get("evidentiary_weight")
                            )
                            title = content.get("title") or ext.get("title")
                            sections = content.get("sections") or ext.get("sections") or []
                            entities = content.get("entities") or ext.get("entities") or []
                            stamps = (
                                content.get("stamps_and_signatures")
                                or ext.get("stamps_and_signatures")
                                or []
                            )
                            metadata = content.get("metadata") or ext.get("metadata") or {}
                            is_blank = bool(ext.get("is_blank") or False)
                            is_template_empty = bool(ext.get("is_template_empty") or False)
                            rotation_hint = int(ext.get("rotation_hint") or 0)

                            # Build text_content (used for fulltext index)
                            text_parts: list[str] = []
                            if title:
                                text_parts.append(str(title))
                            for sec in sections:
                                if isinstance(sec, dict) and "data" in sec:
                                    text_parts.append(str(sec["data"]))
                            text_content = "" if is_blank else "\n".join(text_parts)

                            # :DocumentType
                            if doc_type:
                                document_types_seen.add(doc_type)
                                write_document_type(
                                    tx, asset_id=asset_id, value=doc_type, name=doc_type,
                                )

                            # :Document (once per document_id)
                            if doc_uid not in documents_seen:
                                documents_seen[doc_uid] = []
                                write_document(
                                    tx,
                                    asset_id=asset_id,
                                    value=doc_uid,
                                    file_name=file_name,
                                    document_type=doc_type,
                                    evidence_class=evidentiary_weight,
                                    title=title,
                                    is_mis_export=metadata.get("is_mis_export"),
                                    mis_system=metadata.get("mis_system"),
                                )
                            if evidentiary_weight:
                                documents_seen[doc_uid].append(evidentiary_weight)

                            # :Page (one per CSV row)
                            write_page(
                                tx,
                                asset_id=asset_id,
                                value=page_uid,
                                document_uid=doc_uid,
                                page_index=page_index,
                                text=text_content,
                                title=title,
                                file_type=str(row.get("file_type") or "") or None,
                                is_blank=is_blank,
                                is_template_empty=is_template_empty,
                                rotation_deg=rotation_hint,
                                s3_key=s3_key,
                                original_path=original_path,
                            )
                            pages_inserted += 1

                            # Evidence records anchored to this page (Form1, CRS, ...)
                            er_n = _write_evidence_records_for_page(
                                tx,
                                asset_id=asset_id,
                                page_uid=page_uid,
                                document_type=doc_type,
                                title=title,
                                text_content=text_content,
                                entities=entities,
                                metadata=metadata,
                            )
                            evidence_records_written += er_n

                            # Stamps
                            stamps_inserted += _write_stamps_for_page(
                                tx,
                                asset_id=asset_id,
                                page_uid=page_uid,
                                page_title=title,
                                stamps=stamps if isinstance(stamps, list) else [],
                            )

                            # Connector + external-standard mentions
                            counts = _write_mentions_for_page(
                                tx,
                                asset_id=asset_id,
                                page_uid=page_uid,
                                metadata=metadata,
                                entities=entities if isinstance(entities, list) else [],
                                blocked_sn_set=blocked_sn_set,
                            )
                            for k, v in counts.items():
                                mention_totals[k] += v

                        tx.commit()
                    except GoldenRuleViolation as e:
                        # A golden-rule violation in Phase 1 is a *programming
                        # error in the DAL caller* (not a data issue) — abort.
                        tx.rollback()
                        print(f"phase1: GOLDEN RULE VIOLATION at chunk {chunk_idx}: {e}",
                              file=sys.stderr, flush=True)
                        raise
                    except Exception as e:
                        tx.rollback()
                        print(f"phase1: chunk {chunk_idx} error: {e}", file=sys.stderr)
                        traceback.print_exc()
                        raise

                if (chunk_idx % 10) == 0:
                    print(
                        f"phase1: chunk {chunk_idx}  rows={rows_processed}  "
                        f"pages={pages_inserted}  records={evidence_records_written}  "
                        f"stamps={stamps_inserted}",
                        flush=True,
                    )

        # Document-level evidentiary weight = mode across pages
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                for doc_id, weights in documents_seen.items():
                    if weights:
                        mode_weight = Counter(weights).most_common(1)[0][0]
                        tx.run(
                            "MATCH (d:Document {asset_id: $aid, value: $v}) "
                            "SET d.evidence_class = $w",
                            aid=asset_id, v=doc_id, w=mode_weight,
                        ).consume()
                tx.commit()

        # MANDATORY VERIFICATION
        try:
            verify_counts = verify_phase_1(driver, asset_id)
        except VerificationFailed as e:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n== Phase 1 verification (FAILED) ==\n")
                for k, v in (e.counts or {}).items():
                    f.write(f"- {k:<40s}: {v}\n")
                for rv in e.rule_violations:
                    f.write(f"- RULE VIOLATED: {rv['rule']} expected {rv['expected']}, got {rv['actual']} — {rv['detail']}\n")
            raise

        # Successful run
        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 1 verification ==\n")
            f.write(f"- csv_row_count                          : {rows_processed}\n")
            f.write(f"- rows_failed_parse                       : {rows_failed}\n")
            f.write(f"- documents (unique document_id)          : {len(documents_seen)}\n")
            f.write(f"- distinct document_types                 : {len(document_types_seen)}\n")
            for k, v in verify_counts.items():
                f.write(f"- {k:<40s}: {v}\n")
            f.write("- mention totals:\n")
            for k, v in sorted(mention_totals.items()):
                f.write(f"    - {k:<32s}: {v}\n")

        print(f"phase1: OK — pages={verify_counts.get('pages')}  documents={verify_counts.get('documents')}  "
              f"evidence_records={verify_counts.get('evidence_records')}  stamps={verify_counts.get('stamps')}  "
              f"orphans={verify_counts.get('fact_nodes_no_evidence')}",
              flush=True)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
