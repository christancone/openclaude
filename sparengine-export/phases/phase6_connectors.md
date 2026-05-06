# PHASE 6 — Connectors (cross-doc linkage)

**Intent.** Walk the existing graph (post-phases 1, 2, 4, 5) and weave the cross-document connections:

1. **Enrich `:Person`** — Phase 1's stamp-derived Person nodes have only a name; Phase 6 backfills `cert_authority` from the stamp's `certificate_number`.
2. **Wire `:Stamp-[:STAMPED_BY]->:Person`** for every stamp with a person_name.
3. **Promote stamp `certificate_number` to `:CertificateNumber` nodes** + wire `:Stamp-[:CARRIES_CERT]->:CertificateNumber`.
4. **Wire `:WorkPackage-[:INCLUDES]->:JobCard | :NonRoutineCard | :CRS | :Form1`** by matching WO numbers across the dossier.
5. **(Optional, when extractable)** materialise `:Organization` / `:RegulatoryAuthority` / `:DesignOrganization` / `:ProductionOrganization` / `:MaintenanceOrganization` from address blocks + Form 1 issuer fields.
6. **(Optional)** wire `:COMPLIES_WITH` from WorkPackage/Document → SB/AD/EO/STC/Modification by matching reference numbers.
7. **(Optional)** wire `:IMPLEMENTS` from Modification → STC/EO/SB.
8. **(Optional)** wire `:APPLIES_TO` from SB/AD → TypeCertificate/EngineModel/PartFamily.
9. **(If Xlsx ledger present)** materialise `:LogbookEntry`, `:TechLogEntry`, `:Manual`, `:ElectronicDataEntry`.

**Reference files:**
- `csv_and_ocr.md` (entities, sections — address_block, certification_statement)
- `data_quality_rules.md` (organisation name normalisation, CAMP-as-concept rule)

**Style:** Coding. Mechanical loops. Don't fuse them; build one edge type at a time. Use `csvs/.../AW139/phase6.py` as the canonical reference.

---

## What this phase produces

Edges (the bulk of Phase 6):
- `:Stamp-[:STAMPED_BY]->:Person`
- `:Stamp-[:CARRIES_CERT]->:CertificateNumber`
- `:WorkPackage-[:INCLUDES]->:JobCard | :NonRoutineCard | :CRS | :Form1`
- `:Form1|:CRS|:JobCard-[:SIGNED_BY {block, date, role}]->:Person` (when stamps bind)
- `:Form1|:CRS|:SB|:AD|:STC-[:ISSUED_BY {date}]->:Org / :Authority / :DOA / :POA / :MRO`
- `:WorkPackage|:Document-[:COMPLIES_WITH {date_complied, method}]->:SB|:AD|:EO|:STC|:Modification`
- `:Modification-[:IMPLEMENTS]->:STC|:EO|:SB`
- `:SB|:AD-[:APPLIES_TO]->:TypeCertificate|:EngineModel|:PartFamily`

Nodes (richer, only when extractable):
- `:Person` (with `cert_authority`)
- `:Organization`, `:RegulatoryAuthority`, `:DesignOrganization`, `:ProductionOrganization`, `:MaintenanceOrganization`
- `:LogbookEntry`, `:TechLogEntry`, `:Manual`, `:ElectronicDataEntry` (xlsx ledger only)

---

## Steps

### 1. Bootstrap

```python
from graph_dal.connector import write_certificate_number
from graph_dal.organization import (
    write_person, write_organization,
    write_regulatory_authority, write_design_organization,
    write_production_organization, write_maintenance_organization,
    link_signed_by, link_issued_by, link_work_package_includes,
)
from graph_dal.stamp import link_stamp_carries_cert, link_stamped_by
```

### 2. Person enrichment + STAMPED_BY wiring

Walk every `:Stamp` with a non-null `person_name`. For each:

```python
def detect_cert_authority(cert_number: str | None) -> str | None:
    """Heuristic — infer authority from certificate-number prefix."""
    if not cert_number: return None
    s = cert_number.strip().upper()
    if re.match(r"^(IT|DE|GB|FR|ES|NL|SE|DK|FI|NO|PL|CZ|HU|AT|BE|IE|PT)[\.\-]", s):
        return "EASA"
    if "FAA" in s or s.startswith("A&P"): return "FAA"
    if s.startswith("TCCA") or s.startswith("AME-") or s.startswith("AMC-"): return "TCCA"
    if s.isdigit() and 4 <= len(s) <= 8: return "EASA"   # Italian 4-8 digit certs
    return None

with driver.session(database=database_name()) as s:
    stamps = list(s.run("""
        MATCH (st:Stamp {asset_id: $aid})
        WHERE st.person_name IS NOT NULL
        RETURN st.value AS stamp_uid, st.person_name AS name,
               st.certificate_number AS cert
    """, aid=asset_id))

BATCH = 200
with driver.session(database=database_name()) as session:
    for i in range(0, len(stamps), BATCH):
        with session.begin_transaction() as tx:
            for rec in stamps[i:i + BATCH]:
                name = (rec["name"] or "").strip()
                if not name: continue
                person_uid = re.sub(r"\s+", " ", name).upper()
                cert = (rec["cert"] or "").strip()
                authority = detect_cert_authority(cert) if cert else None

                write_person(
                    tx, asset_id=asset_id, value=person_uid,
                    name=name, cert_authority=authority,
                )
                # Wire :Stamp-[:STAMPED_BY]->:Person (Q8 — was the legacy :BY edge)
                link_stamped_by(
                    tx, asset_id=asset_id, stamp_uid=rec["stamp_uid"],
                    person_value=person_uid, person_name=name,
                )

                if cert:
                    write_certificate_number(
                        tx, asset_id=asset_id, value=cert,
                        cert_type="staff_authorisation",
                    )
                    link_stamp_carries_cert(
                        tx, asset_id=asset_id,
                        stamp_uid=rec["stamp_uid"], cert_value=cert,
                    )
            tx.commit()
```

### 3. WorkPackage → JobCard / NRC / CRS / Form1

Phase 1 wrote `:WorkPackage` nodes (per `workpack_cover_sheet` page). Phase 6 wires the *logical containment* — a JobCard belongs to a WorkPackage if they share a Document (best-effort) or share a WO number.

Same-document chain (works for AW139 and most physical-packet dossiers):

```python
with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        for tgt in ("JobCard", "NonRoutineCard", "CRS", "Form1"):
            tx.run(f"""
                MATCH (wp:WorkPackage {{asset_id: $aid}})<-[:CARRIES]-(p1:Page)<-[:HAS_PAGE]-(d:Document)
                MATCH (d)-[:HAS_PAGE]->(p2:Page)-[:CARRIES]->(t:{tgt})
                WHERE t.value <> wp.value
                MERGE (wp)-[:INCLUDES]->(t)
            """, aid=asset_id).consume()
        tx.commit()
```

### 4. Form1 / CRS / JobCard → Person via SIGNED_BY (when stamps bind)

For each bound stamp whose `target_type ∈ {form1, crs, job_card}`, wire the SIGNED_BY relationship from the carrier evidence record to the person:

Optional in first-cut Phase 6 — defer to Phase 7 if `binds_to` quality is poor.

### 5. (Optional) Organisation extraction from address_block sections

When the OCR's `content.sections[]` contains an `address_block`, parse the organisation name. Heuristic dispatch:

- Part-145 number / "Part-145" mention → `write_maintenance_organization`
- DOA number / "Design Organisation Approval" → `write_design_organization`
- POA / CAGE code → `write_production_organization`
- "EASA" / "FAA" / "TCCA" / "CAAC" → `write_regulatory_authority`
- Otherwise → `write_organization` with `role` ∈ {"MRO", "CAMO", "Operator", "OEM"} guessed from context

Skip if extraction quality is below `medium` — better no organisation than a wrong one.

### 6. (Optional) `:COMPLIES_WITH`, `:IMPLEMENTS`, `:APPLIES_TO`

```cypher
// JobCard COMPLIES_WITH the SB it mentions on its page
MATCH (jc:JobCard {asset_id: $aid})<-[:CARRIES]-(p:Page)-[:MENTIONS_SB]->(sb:ServiceBulletin)
MERGE (jc)-[:COMPLIES_WITH]->(sb)

// Modification IMPLEMENTS the STC it references
MATCH (mod:Modification {asset_id: $aid})<-[:CARRIES]-(p:Page)-[:MENTIONS_STC]->(stc:STC)
MERGE (mod)-[:IMPLEMENTS]->(stc)

// SB APPLIES_TO TypeCertificate (when SB scope is known)
MATCH (sb:ServiceBulletin {asset_id: $aid})
MATCH (tc:TypeCertificate {asset_id: $aid})
WHERE sb.tc_scope = tc.value
MERGE (sb)-[:APPLIES_TO]->(tc)
```

### 7. (Optional) Xlsx ledger nodes

If the dossier has a sidecar Xlsx (logbook index, manual library), parse and write:
- `:LogbookEntry` per row → `:RECORDS` :Component
- `:TechLogEntry` per row → `:OF_ASSET` :Asset
- `:Manual` per row → `:FOR_ASSET` :Asset
- `:ElectronicDataEntry` per row → `:FOR_ASSET` :Asset

---

## What to log

```
== Phase 6 verification ==
- :Person count (live)                    : <N>
- :Organization / :MaintenanceOrg / ... counts (if extracted)
- persons_enriched_this_phase             : <N>
- certs_promoted_to_nodes                 : <N>
- :STAMPED_BY edges                       : <N>   ← MUST equal stamps_with_person_name
- :CARRIES_CERT edges                     : <N>
- :WorkPackage-[:INCLUDES]-> count        : <N>
- :COMPLIES_WITH edges (if wired)         : <N>
- person cert authorities detected: EASA / FAA / TCCA / unknown
- top stamp location_contexts (first 10)
- fact_nodes_no_evidence                  : 0
```

---

## MANDATORY VERIFICATION

```python
from graph_dal.verify import verify_no_fact_orphans
verify_no_fact_orphans(driver, asset_id, phase="6")
```

Plus:
- `:STAMPED_BY` edge count ≈ count of `:Stamp` nodes with non-null `person_name`. If much lower, the wiring loop missed batches.
- `fact_nodes_no_evidence == 0`.

---

## STOP conditions

- `:STAMPED_BY` edge count dramatically less than stamps with `person_name` — wiring failed silently.
- Any new `:Person` created without a name property (write_person called with empty name).
- `fact_nodes_no_evidence > 0`.

---

## Reference implementation

`csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase6.py` — verified-working. For AW139 it enriches 148 persons, wires 327 STAMPED_BY edges, promotes 130 cert numbers. Optional org-extraction sections are unimplemented for AW139 (the OCR vintage doesn't surface clean org names).
