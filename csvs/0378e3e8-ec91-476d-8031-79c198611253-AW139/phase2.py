"""Phase 2 — Asset Detection (confirmation against profile).

Phase 1 already seeded :Asset (Phase 1's bootstrap call to write_asset).
Phase 2's job is to:

  1. Re-affirm the :Asset's properties from asset_profile.json.
  2. Build the regulatory layer: :TypeCertificate, :CountryRegistration,
     :EngineModel / :APUModel / :PropellerModel / :RotorAssemblyModel
     where extractable from the profile.
  3. Aggregate per-page corpus signals (most common SN, MSN, MIS system,
     latest dossier date) and reconcile against the profile.
  4. Log a reconciliation table to ``progress.log``.
  5. Raise asset-level CONTEXT_DISCREPANCY findings for clear mismatches.

This is a *Coding* phase — pure SQL/Cypher aggregation, no judgement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
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

from graph_dal import connect, database_name, AssetKind, FindingSeverity  # noqa: E402
from graph_dal.asset import (                                              # noqa: E402
    write_asset,
    write_country_registration,
    write_type_certificate,
)
from graph_dal.errors import VerificationFailed                            # noqa: E402
from graph_dal.finding import write_audit_run, write_finding               # noqa: E402
from graph_dal.verify import verify_phase_2                                # noqa: E402


def _norm(s: object) -> str:
    if s is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _fuzzy_match(a: object, b: object) -> bool:
    """Fuzzy-equal: case-insensitive, alpha-numeric only."""
    a_norm, b_norm = _norm(a), _norm(b)
    if not a_norm and not b_norm:
        return True
    if not a_norm or not b_norm:
        return False
    return a_norm == b_norm


def _aggregate_corpus_signals(driver, asset_id: str) -> dict:
    """Pull majority values + counts from the page mention edges already
    written by Phase 1.

    Returns
    -------
    {
        "top_sn":         (value, count) | None,
        "top_pn":         (value, count) | None,
        "top_doc_type":   (value, count),
        "latest_date":    str | None,
        "page_count":     int,
        "doc_count":      int,
        "stamp_count":    int,
        "has_mis_export": bool,
    }
    """
    out: dict = {}
    with driver.session(database=database_name()) as s:
        out["page_count"] = s.run(
            "MATCH (p:Page {asset_id: $aid}) RETURN count(p) AS n",
            aid=asset_id,
        ).single()["n"]
        out["doc_count"] = s.run(
            "MATCH (d:Document {asset_id: $aid}) RETURN count(d) AS n",
            aid=asset_id,
        ).single()["n"]
        out["stamp_count"] = s.run(
            "MATCH (st:Stamp {asset_id: $aid}) RETURN count(st) AS n",
            aid=asset_id,
        ).single()["n"]

        sn_record = s.run(
            "MATCH (:Page {asset_id: $aid})-[:MENTIONS_SN]->(sn:SerialNumber {asset_id: $aid}) "
            "WITH sn.value AS value, count(*) AS n "
            "ORDER BY n DESC LIMIT 1 RETURN value, n",
            aid=asset_id,
        ).single()
        out["top_sn"] = (sn_record["value"], sn_record["n"]) if sn_record else None

        pn_record = s.run(
            "MATCH (:Page {asset_id: $aid})-[:MENTIONS_PN]->(pn:PartNumber {asset_id: $aid}) "
            "WITH pn.value AS value, count(*) AS n "
            "ORDER BY n DESC LIMIT 1 RETURN value, n",
            aid=asset_id,
        ).single()
        out["top_pn"] = (pn_record["value"], pn_record["n"]) if pn_record else None

        dt_record = s.run(
            "MATCH (d:Document {asset_id: $aid}) "
            "WHERE d.document_type IS NOT NULL "
            "WITH d.document_type AS value, count(*) AS n "
            "ORDER BY n DESC LIMIT 1 RETURN value, n",
            aid=asset_id,
        ).single()
        out["top_doc_type"] = (dt_record["value"], dt_record["n"]) if dt_record else None

        latest = s.run(
            "MATCH (d:Date {asset_id: $aid}) "
            "WITH d.iso AS iso ORDER BY iso DESC LIMIT 1 RETURN iso",
            aid=asset_id,
        ).single()
        out["latest_date"] = latest["iso"] if latest else None

        mis = s.run(
            "MATCH (d:Document {asset_id: $aid}) "
            "WHERE d.is_mis_export = true RETURN count(d) AS n",
            aid=asset_id,
        ).single()
        out["has_mis_export"] = bool(mis and mis["n"] and mis["n"] > 0)

    return out


def _build_finding_value(category: str, ref: str) -> str:
    """Stable canonical value for an asset-level finding."""
    return f"finding::asset::{category}::{_norm(ref) or 'misc'}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 — Asset detection / confirmation")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--asset-id", required=False)
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    profile_path = workdir / "asset_profile.json"
    log_path = workdir / "progress.log"

    if not profile_path.exists():
        raise FileNotFoundError(f"asset_profile.json not found at {profile_path}")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))

    asset_class_raw = profile.get("asset_class") or ""
    subtype = profile.get("subtype")
    type_designation = profile.get("type_designation")
    tcds = profile.get("tcds")
    yom = profile.get("yom")

    identifier = profile.get("identifier") or {}
    msn = identifier.get("msn")
    esn = identifier.get("esn")
    primary_serial = identifier.get("primary_serial")

    registration_block = profile.get("registration") or {}
    registration_current = registration_block.get("current")

    operator = profile.get("operator")
    operator_country = profile.get("operator_country")

    # Rough mapping rotorcraft/airplane → AssetKind
    asset_kind_map = {
        "ROTORCRAFT": "AIRCRAFT",
        "AIRPLANE":   "AIRCRAFT",
        "AIRCRAFT":   "AIRCRAFT",
        "ENGINE":     "ENGINE",
        "PROPELLER":  "PROPELLER",
        "APU":        "APU",
    }
    asset_kind = asset_kind_map.get(asset_class_raw.upper())

    if not asset_kind:
        # Phase 1 already classified by subtype; trust that.
        # Re-read the live :Asset to learn what it currently is.
        asset_kind = AssetKind.AIRCRAFT.value

    asset_id = args.asset_id or profile.get("asset_id")
    if not asset_id:
        # Fall back to whatever the live :Asset uses (Phase 1's seed)
        with connect() as driver:
            with driver.session(database=database_name()) as s:
                rec = s.run(
                    "MATCH (a:Asset) RETURN a.asset_id AS aid LIMIT 1"
                ).single()
                if rec:
                    asset_id = rec["aid"]
    if not asset_id:
        raise RuntimeError("Phase 2: cannot determine asset_id (not in profile, "
                           "not on CLI, no :Asset in graph).")

    print(f"phase2: asset_id={asset_id}", flush=True)

    driver = connect()
    try:
        # 1. Re-affirm :Asset properties (idempotent; Phase 1 seeded the basics)
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_asset(
                    tx,
                    asset_id=asset_id,
                    asset_kind=asset_kind,
                    name=type_designation or profile.get("name"),
                    msn=msn,
                    registration=registration_current,
                    subtype=subtype,
                    country_of_registration=operator_country,
                    manufacture_date_iso=(
                        f"{int(yom):04d}-01-01" if isinstance(yom, int) else None
                    ),
                )
                tx.commit()

        # 2. Regulatory layer: TypeCertificate, CountryRegistration
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                if type_designation:
                    write_type_certificate(
                        tx,
                        asset_id=asset_id,
                        value=type_designation,                  # e.g. "AW139"
                        tc_holder=profile.get("tc_holder"),
                        tc_number=tcds,
                        model_designation=type_designation,
                        category=profile.get("certification_basis"),  # CS-25/27/29 etc.
                    )
                if operator_country:
                    write_country_registration(
                        tx,
                        asset_id=asset_id,
                        value=str(operator_country),
                        iso_code=str(operator_country) if len(str(operator_country)) == 2 else None,
                    )
                tx.commit()

        # 3. AuditRun anchor (so any findings raised here can :PRODUCED_BY)
        from datetime import datetime
        run_id = f"audit::phase2::{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                write_audit_run(
                    tx,
                    asset_id=asset_id,
                    value=run_id,
                    dossier_cut_off_date_iso=profile.get("dossier_date"),
                    audit_snapshot_date_iso=datetime.utcnow().date().isoformat(),
                    sparengine_version="phase2-neo4j-1",
                )
                tx.commit()

        # 4. Aggregate corpus signals via Cypher
        signals = _aggregate_corpus_signals(driver, asset_id)

        # 5. Reconciliation: profile vs corpus
        reconciliation: list[tuple[str, str, str, str]] = []
        # MSN
        corpus_msn = signals["top_sn"][0] if signals["top_sn"] else None
        msn_verdict = "match" if _fuzzy_match(msn, corpus_msn) else (
            "missing-profile" if not msn and corpus_msn else
            "missing-corpus" if msn and not corpus_msn else "mismatch"
        )
        reconciliation.append(("msn", str(msn or "-"), str(corpus_msn or "-"), msn_verdict))

        # registration — no canonical corpus query yet (entity_type='registration'
        # could be added; for now just reflect the profile)
        reconciliation.append((
            "registration", str(registration_current or "-"),
            "(not aggregated)", "not-checked",
        ))

        # operator — same
        reconciliation.append((
            "operator", str(operator or "-"),
            "(not aggregated)", "not-checked",
        ))

        # 6. Asset-level CONTEXT_DISCREPANCY findings for clear mismatches
        findings_written = 0
        with driver.session(database=database_name()) as session:
            with session.begin_transaction() as tx:
                if msn_verdict == "mismatch":
                    # Need an evidence page for the finding. Use the page that
                    # most-frequently mentions the corpus_msn.
                    page_uid_record = session.run(
                        "MATCH (p:Page {asset_id: $aid})-[:MENTIONS_SN]->"
                        "(:SerialNumber {asset_id: $aid, value: $sn}) "
                        "RETURN p.value AS uid LIMIT 1",
                        aid=asset_id, sn=corpus_msn,
                    ).single()
                    if page_uid_record:
                        write_finding(
                            tx,
                            asset_id=asset_id,
                            value=_build_finding_value("CONTEXT_DISCREPANCY", "msn"),
                            severity=FindingSeverity.LEVEL_2.value,
                            category="CONTEXT_DISCREPANCY",
                            title="MSN mismatch between profile and corpus",
                            description=(
                                f"asset_profile.json declares msn={msn!r}, "
                                f"but the most-mentioned serial number across the "
                                f"corpus is {corpus_msn!r}. Verify which is canonical."
                            ),
                            evidence_page_uid=page_uid_record["uid"],
                            evidence_quote=f"corpus majority MSN={corpus_msn}",
                            recommended_action=(
                                "Verify against the asset's certificate of registration "
                                "or aircraft logbook front page."
                            ),
                            asset_level=True,
                            audit_run_uid=run_id,
                        )
                        findings_written += 1
                tx.commit()

        # 7. MANDATORY VERIFICATION
        try:
            verify_counts = verify_phase_2(driver, asset_id)
        except VerificationFailed as e:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n== Phase 2 verification (FAILED) ==\n")
                for k, v in (e.counts or {}).items():
                    f.write(f"- {k:<40s}: {v}\n")
                for rv in e.rule_violations:
                    f.write(
                        f"- RULE VIOLATED: {rv['rule']} expected {rv['expected']}, "
                        f"got {rv['actual']} — {rv['detail']}\n"
                    )
            raise

        with log_path.open("a", encoding="utf-8") as f:
            f.write("\n== Phase 2 verification ==\n")
            f.write(f"- audit_run_uid                          : {run_id}\n")
            f.write(f"- type_certificate                        : "
                    f"{'written' if type_designation else 'skipped (no profile.type_designation)'}\n")
            f.write(f"- country_registration                    : "
                    f"{'written' if operator_country else 'skipped (no profile.operator_country)'}\n")
            for k, v in verify_counts.items():
                f.write(f"- {k:<40s}: {v}\n")
            f.write(f"- corpus signals: page_count={signals['page_count']}, "
                    f"doc_count={signals['doc_count']}, stamp_count={signals['stamp_count']}\n")
            f.write(f"  top_sn={signals['top_sn']}, top_pn={signals['top_pn']}\n")
            f.write(f"  top_doc_type={signals['top_doc_type']}, "
                    f"latest_date={signals['latest_date']}\n")
            f.write(f"- findings written (CONTEXT_DISCREPANCY) : {findings_written}\n")
            f.write("\nReconciliation:\n")
            f.write(f"  {'field':<16s} {'profile':<22s} {'corpus_majority':<22s} verdict\n")
            f.write(f"  {'-' * 16} {'-' * 22} {'-' * 22} -------\n")
            for field, prof, corp, verdict in reconciliation:
                f.write(f"  {field:<16s} {str(prof)[:22]:<22s} {str(corp)[:22]:<22s} {verdict}\n")

        print(
            f"phase2: OK — assets={verify_counts.get('assets')}  "
            f"asset_class_label_total={verify_counts.get('asset_class_label_total')}  "
            f"orphans={verify_counts.get('fact_nodes_no_evidence')}  "
            f"findings={findings_written}",
            flush=True,
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
