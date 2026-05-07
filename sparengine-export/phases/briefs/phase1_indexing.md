# PHASE 1 — Corpus Indexing (Neo4j edition)

**Intent.** Stream every CSV row, parse `extracted_json` once per row, and write all page-level data into the per-asset Neo4j graph through the `graph_dal` chokepoint. **No graph-derived facts yet** (those come in phases 4–6) — Phase 1 is verbatim ingestion of what upstream OCR produced.

**Reference files to load alongside:**
- `references/csv_and_ocr.md`
- `references/document_types.md`
- `cypher/schema.cypher` (already applied at startup; you don't need to re-run it)

**Inputs:**
- `asset_profile.json` (Phase 0 output) — read `blocked_sn_list`.
- The CSV (`asset_pages_*.csv`).

**Style:** Coding. Single Python script, mechanical. Use the canonical AW139 template at `csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase1.py` as your reference implementation.

---

## What this phase produces

Per the schema reference (Q12 of the migration plan), Phase 1 writes:

| Layer | Nodes |
|---|---|
| Carrier hierarchy | `:Document`, `:Page`, `:Folder`, `:Box`, `:Binder`, `:DocumentType` |
| Provenance | `:Stamp` |
| Evidence records (page-level) | `:Form1`, `:CRS`, `:WorkPackage`, `:JobCard`, `:NonRoutineCard`, `:Repair`, `:Modification`, `:STC`, `:BorescopeReport`, `:NDTReport`, `:DentBuckleEntry` |
| Connector identifiers | `:PartNumber`, `:SerialNumber`, `:CertificateNumber`, `:PurchaseOrder`, `:DrawingNumber`, `:BatchNumber`, `:TechLogPage`, `:Reference` |
| External standards (bare-bones) | `:ATAChapter`, `:ServiceBulletin`, `:AirworthinessDirective`, `:EngineeringOrder`, `:RegulatoryRef` |
| Time | `:Date` |

And these edges:

| Edge | Direction |
|---|---|
| `:HAS_PAGE` | `:Document → :Page` |
| `:CARRIES {quote}` | `:Page → evidence-record` (the page-level golden-rule edge for evidence records) |
| `:HAS_STAMP {quote}` | `:Page → :Stamp` (golden-rule for stamps) |
| `:CLASSIFIED_AS` | `:Document → :DocumentType` |
| `:CONTAINS` | `:Folder → :Box`, `:Box → :Binder`, `:Binder → :Document` (when carrier hierarchy is known) |
| `:HAS_FOLDER` | `:Asset → :Folder` |
| `:MENTIONS_PN` / `:MENTIONS_SN` / `:MENTIONS_CERT` / `:MENTIONS_PO` / `:MENTIONS_DRAWING` / `:MENTIONS_BATCH` / `:MENTIONS_TECHLOG_PAGE` / `:MENTIONS_SB` / `:MENTIONS_AD` / `:MENTIONS_EO` | `:Page \| :Document → connector` |
| `:REFS {ref_type}` | `:Page \| :Document → :Reference` |
| `:COVERS_ATA` | `:Page \| :Document → :ATAChapter` |
| `:CITES` | `:Page \| :Document → :RegulatoryRef` |
| `:ON_DATE {role}` | (anything dated) → `:Date` |

This phase also seeds the `:Asset` node with the values Phase 0 discovered (so Phase 2 can confirm them). Phase 1 does NOT add the secondary class label (`:Aircraft`, `:Engine`, …) — that's Phase 2's job.

---

## Steps

### 1. Bootstrap

```python
from __future__ import annotations
import argparse, json, sys
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
    raise RuntimeError("phase1.py: could not locate sparengine-export/graph_dal/")


_bootstrap_graph_dal()

from graph_dal import connect, database_name
from graph_dal.asset import write_asset
from graph_dal.connector import (
    REFERENCE_TYPES,
    link_mentions_batch, link_mentions_cert, link_mentions_drawing,
    link_mentions_pn, link_mentions_po, link_mentions_sn,
    link_mentions_techlog_page, link_refs,
    write_batch_number, write_certificate_number, write_drawing_number,
    write_part_number, write_purchase_order, write_reference,
    write_serial_number, write_tech_log_page,
)
from graph_dal.document import (
    write_document, write_document_type, write_page,
)
from graph_dal.errors import GoldenRuleViolation, VerificationFailed
from graph_dal.evidence import (
    write_borescope_report, write_crs, write_dent_buckle_entry,
    write_form1, write_job_card, write_modification,
    write_ndt_report, write_non_routine_card, write_repair,
    write_stc, write_work_package,
)
from graph_dal.external_standards import (
    link_cites, link_covers_ata, link_mentions_ad, link_mentions_eo,
    link_mentions_sb, write_airworthiness_directive, write_ata_chapter,
    write_engineering_order, write_regulatory_ref, write_service_bulletin,
)
from graph_dal.stamp import write_stamp
from graph_dal.verify import verify_phase_1, verify_schema
from graph_dal._doctype_to_record import derive_evidence_record_kinds
```

### 2. Read profile + open driver + sanity-check schema

```python
profile = json.loads(profile_path.read_text(encoding="utf-8"))
asset_id = profile.get("asset_id") or args.asset_id  # CLI overrides

driver = connect()
verify_schema(driver)   # raises VerificationFailed if constraints not in place
```

### 3. Seed `:Asset` with what Phase 0 discovered

The `asset_profile.json` shape varies — some fields are nested dicts (`registration: {current, history}`, `identifier: {msn, esn, primary_serial}`); others are flat. Dig into the nesting:

```python
identifier = profile.get("identifier") if isinstance(profile.get("identifier"), dict) else {}
registration_block = profile.get("registration") if isinstance(profile.get("registration"), dict) else {}

asset_kind_map = {
    "HELICOPTER":           "AIRCRAFT",
    "FIXED_WING_JET":       "AIRCRAFT",
    "FIXED_WING_TURBOPROP": "AIRCRAFT",
    "FIXED_WING_PISTON":    "AIRCRAFT",
    "TURBOFAN":  "ENGINE", "TURBOJET":  "ENGINE",
    "TURBOPROP": "ENGINE", "TURBOSHAFT": "ENGINE", "PISTON": "ENGINE",
}
asset_kind = profile.get("asset_kind") or asset_kind_map.get(profile.get("subtype")) or "AIRCRAFT"

with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        write_asset(
            tx,
            asset_id=asset_id,
            asset_kind=asset_kind,
            name=profile.get("type_designation") or profile.get("name"),
            msn=identifier.get("msn") or profile.get("msn"),
            registration=registration_block.get("current"),
            subtype=profile.get("subtype"),
            country_of_registration=profile.get("operator_country"),
        )
        tx.commit()
```

`write_asset` defensively coerces nested dicts/lists to None — it never lets a non-primitive value reach the wire.

### 4. Stream the CSV row by row

Use `pandas.read_csv(..., chunksize=500)`. **Do not load 50k+ rows into memory.** One transaction per chunk:

```python
df_iter = pd.read_csv(csv_path, chunksize=args.chunksize)
documents_seen: dict[str, list[str]] = {}   # doc_uid → list of evidentiary_weights

with driver.session(database=database_name()) as session:
    for chunk_idx, chunk in enumerate(df_iter):
        with session.begin_transaction() as tx:
            try:
                for _, row in chunk.iterrows():
                    process_row(tx, row, asset_id, blocked_sn_set, documents_seen)
                tx.commit()
            except GoldenRuleViolation as e:
                tx.rollback()  # programmer error — abort
                raise
            except Exception as e:
                tx.rollback()
                # log + raise
                raise
```

### 5. Per-row processing

For each row, parse `extracted_json` once with `orjson.loads`. Skip if parse fails (log to progress.log; count as `rows_failed_parse`).

The OCR vintage may put fields under `content.X` OR at top level. Read both:

```python
ext = orjson.loads(row["extracted_json"])
content = ext.get("content") or {}

doc_type = content.get("document_type") or ext.get("document_type")
title = content.get("title") or ext.get("title")
sections = content.get("sections") or ext.get("sections") or []
entities = content.get("entities") or ext.get("entities") or []
stamps = content.get("stamps_and_signatures") or ext.get("stamps_and_signatures") or []
metadata = content.get("metadata") or ext.get("metadata") or {}

is_blank = bool(ext.get("is_blank"))
is_template_empty = bool(ext.get("is_template_empty"))
rotation = int(ext.get("rotation_hint") or 0)
```

#### 5a. Build `text_content` for the page (used by fulltext)

```python
text_parts = [str(title)] if title else []
for sec in sections:
    if isinstance(sec, dict) and "data" in sec:
        text_parts.append(str(sec["data"]))
text_content = "" if is_blank else "\n".join(text_parts)
```

#### 5b. Write `:DocumentType` (once per distinct doctype)

```python
if doc_type:
    write_document_type(tx, asset_id=asset_id, value=doc_type, name=doc_type)
```

#### 5c. Write `:Document` (once per `document_id` — track in `documents_seen`)

```python
doc_uid = str(row.get("document_id", ""))
if doc_uid not in documents_seen:
    documents_seen[doc_uid] = []
    write_document(
        tx,
        asset_id=asset_id, value=doc_uid,
        file_name=str(row.get("file_name", "")),
        document_type=doc_type,
        evidence_class=content.get("evidentiary_weight") or ext.get("evidentiary_weight"),
        title=title,
        is_mis_export=metadata.get("is_mis_export"),
        mis_system=metadata.get("mis_system"),
    )
weight = content.get("evidentiary_weight") or ext.get("evidentiary_weight")
if weight:
    documents_seen[doc_uid].append(weight)
```

#### 5d. Write `:Page` (once per CSV row)

```python
write_page(
    tx,
    asset_id=asset_id, value=str(row["id"]),
    document_uid=doc_uid,
    page_index=int(row.get("page_index", 0)),
    text=text_content,         # → indexed by page_text fulltext
    title=title,
    file_type=str(row.get("file_type") or "") or None,
    is_blank=is_blank,
    is_template_empty=is_template_empty,
    rotation_deg=rotation,
    s3_key=str(row.get("enhanced_s3_key", "")),
    original_path=str(row.get("original_path", "")),
)
```

#### 5e. Write evidence-record nodes (Form1, CRS, JobCard, …) for this page

Use `derive_evidence_record_kinds(doc_type)` — it returns the list of evidence-record kinds to emit per the closed mapping in `graph_dal/_doctype_to_record.py`. For example, `dual_release_certificate` returns `["form1", "crs"]` (a dual release counts as both).

```python
quote = (title or text_content[:240].strip()
         or f"(see {page_uid}, doctype={doc_type!r})")

for kind in derive_evidence_record_kinds(doc_type):
    if kind == "form1":
        write_form1(
            tx, asset_id=asset_id,
            value=stable_record_value("form1", page_uid,
                                      _find_canonical(entities, "approval_number")),
            evidence_page_uid=page_uid, evidence_quote=quote,
            kind={"easa_form_one":"easa","faa_form_8130":"faa",
                  "tcca_form_one":"tcca","dual_release_certificate":"dual"}.get(doc_type),
            block_13_date_iso=(metadata.get("dates") or [None])[0],
        )
    elif kind == "crs":
        write_crs(tx, asset_id=asset_id, value=f"crs::{page_uid}",
                  evidence_page_uid=page_uid, evidence_quote=quote,
                  date_iso=(metadata.get("dates") or [None])[0])
    elif kind == "job_card":
        write_job_card(tx, asset_id=asset_id,
                       value=stable_record_value("jc", page_uid,
                                                 _find_canonical(entities, "task_card_number")),
                       evidence_page_uid=page_uid, evidence_quote=quote,
                       ata=(metadata.get("ata_chapters") or [None])[0])
    # ... and similarly for non_routine_card, repair, modification, stc,
    #     borescope_report, ndt_report, dent_buckle_entry, work_package
```

The natural-key value (`value`) follows the rule: prefer a canonical number from the OCR's `entities[]` (e.g. `entity_type='approval_number'` for a Form 1); fall back to `f"{kind}::{page_uid}"` if no canonical number is present. This keeps records uniquely identifiable per-page even when OCR didn't surface their numbers.

#### 5f. Write stamps for this page

```python
for i, st in enumerate(stamps):
    if not isinstance(st, dict):
        continue
    # OCR rarely supplies stamp_id; fall back to position in the array.
    local_id = st.get("stamp_id") or st.get("id") or f"st_{i}"
    full_id = f"{page_uid}::{local_id}"

    binds = st.get("binds_to") or {}
    binding_status = (
        "bound" if binds.get("binding_confidence") == "high"
        else "ambiguous" if binds.get("binding_confidence") == "ambiguous"
        else "unbound" if not binds.get("target_ref")
        else "ambiguous"
    )
    quote = (st.get("text") or st.get("person_name") or title or "(stamp on page)")[:240]

    write_stamp(
        tx, asset_id=asset_id, value=full_id, page_uid=page_uid,
        evidence_quote=quote,
        type=st.get("type"), text=st.get("text"),
        person_name=st.get("person_name"), title_role=st.get("title_role"),
        date_iso=st.get("date"),
        certificate_number=st.get("certificate_number"),
        location_context=st.get("location_context"),
        binding_status=binding_status,
    )
```

`write_stamp` writes the `:Stamp` node, links `:Page-[:HAS_STAMP {quote}]->:Stamp`, and (if `date_iso` is present) MERGEs the `:Date` and `:ON_DATE` edge.

#### 5g. Write connector-identifier nodes + `:MENTIONS_*` edges

Walk `metadata.part_numbers`, `metadata.serial_numbers` (filtered by `blocked_sn_set`), `metadata.ata_chapters`, `metadata.regulatory_references`, and `metadata.reference_numbers[]`. For typed reference entries:

```python
TYPED_CONNECTOR_BY_OCR_TYPE = {
    "part_number": "pn", "serial_number": "sn", "esn": "sn", "msn": "sn",
    "certificate_number": "cert", "approval_number": "cert",
    "drawing_number": "drawing", "batch_number": "batch",
    "sb_number": "sb", "ad_number": "ad",
    # routes that go to dedicated paths handled elsewhere:
    "task_card_number": "tc_card",
    "nrc_number": "nrc_card",
    "work_order": "wo",
    "ata_chapter": "ata",
}

REFERENCE_TYPE_BY_OCR_TYPE = {
    "approval": "approval", "tracking": "tracking", "report": "report",
    "amendment": "amendment", "doc_control": "doc_control",
    "config": "config", "project": "project", "docket": "docket",
    "invoice": "invoice",
}
```

For each PN, SN, etc.: write the node, then link the mention edge from `:Page` (with `level="page"`). Long-tail types route to `:Reference {ref_type}` via `link_refs`.

Also walk `entities[]` to catch things `metadata` missed (defensive — sometimes the OCR populates entities but not metadata).

**Apply `blocked_sn_list`** from `asset_profile.json` to drop blocked SNs **before** writing the `:SerialNumber` node. The universal blocklist (Phase 4 reapplies it) is applied here for completeness:
- year strings 1990..2030 → drop
- single character → drop
- date-like `YYYY-MM-DD` → drop

Track `sn_blocked_total` for the verification log.

### 6. Document-level `evidentiary_weight` rollup

After all rows: each `:Document.evidence_class` gets the most-common per-page weight:

```python
with driver.session(database=database_name()) as session:
    with session.begin_transaction() as tx:
        for doc_uid, weights in documents_seen.items():
            if weights:
                mode = Counter(weights).most_common(1)[0][0]
                tx.run(
                    "MATCH (d:Document {asset_id: $aid, value: $v}) "
                    "SET d.evidence_class = $w",
                    aid=asset_id, v=doc_uid, w=mode,
                ).consume()
        tx.commit()
```

This is the one place Phase 1 may run a small inline `tx.run` — it's a property update, not a node MERGE. Acceptable.

---

## Performance notes

- `INSERT-style writes via DAL` are fine; the DAL uses `MERGE` so re-runs are idempotent.
- Wrap inserts in transactions: one transaction per chunk of ~500 rows.
- Stream the CSV — don't load 50k+ rows into memory.
- The fulltext index `page_text` is updated automatically by Neo4j as you write `:Page.text`. No explicit re-index.
- Use prepared / parameterised Cypher (the DAL does this). String-formatted queries on 50k rows are slow and unsafe.

---

## What to log per row / chunk

Track these counters in memory and emit them at chunk end:

```
- rows processed
- rows skipped (parse fail)
- documents (unique document_id)
- distinct document_types seen
- pages_inserted
- evidence_records (sum across kinds)
- stamps_inserted
- mention totals: pn, sn, sn_blocked, cert, po, drawing, batch, techlog,
                  ata, sb, ad, eo, ref, regref
- dates_materialised (count of distinct :Date nodes after run)
```

---

## MANDATORY VERIFICATION

After Phase 1 finishes, call:

```python
from graph_dal.verify import verify_phase_1
counts = verify_phase_1(driver, asset_id)
```

`verify_phase_1` returns a counts dict and **raises `VerificationFailed` if any rule is violated**. Append the dict verbatim to `progress.log` as:

```
== Phase 1 verification ==
- csv_row_count                          : <N>
- rows_failed_parse                      : <N>
- documents (unique document_id)         : <N>
- distinct document_types                : <N>
- pages                                  : <N>
- documents                              : <N>
- folders / boxes / binders              : <N> / <N> / <N>
- stamps                                 : <N>
- records_form1 / records_crs / ...      : (per-label counts)
- evidence_records                       : <sum>
- ids_partnumber / ids_serialnumber / ...: (per-label counts)
- connector_identifiers                  : <sum>
- dates                                  : <N>
- fact_nodes_no_evidence                 : 0   ← MUST be 0
- page_text_online                       : 1   ← MUST be 1
- mention totals                         : (per-edge-type)
```

### Rules verify_phase_1 enforces

- `count(:Page) > 0`
- `count(:Document) > 0`
- **`fact_nodes_no_evidence == 0`** — golden rule. The query the verifier runs:
  ```cypher
  MATCH (n {asset_id: $aid})
  WHERE any(l IN labels(n) WHERE l IN [
    "Component","Event","Form1","CRS","WorkPackage","JobCard","NonRoutineCard",
    "Repair","Modification","STC","Finding","Stamp","ComponentSnapshot",
    "BorescopeReport","NDTReport","DentBuckleEntry"
  ])
    AND NOT EXISTS { (n)-[:EVIDENCED_BY|CARRIES|HAS_STAMP|CORROBORATED_BY]-(:Page) }
  RETURN count(n)
  ```
- `page_text` fulltext index is `ONLINE`.

---

## STOP conditions — do NOT proceed to Phase 2 if:

- `count(:Page) == 0`.
- `count(:Document) == 0`.
- `count(:Page)` is wildly off from CSV row count (more than 10% mismatch unexplained by parse failures).
- `count(:Stamp) == 0` AND the dossier has any pages with `document_type` in the certificate family — that means stamp hydration was silently skipped.
- `fact_nodes_no_evidence > 0` — golden rule violated.
- `page_text_online == false` — the fulltext index is missing or offline; Phase 7.5 will fail.

The next phase will read from these nodes. If they're empty here, every later phase will be empty too.

---

## Reference implementation

The verified-working AW139 Phase 1 is `csvs/0378e3e8-ec91-476d-8031-79c198611253-AW139/phase1.py`. Treat it as canonical — your Phase 1 should match its structure, with adjustments only for asset-specific data (the AW139 has no Folder/Box/Binder hierarchy in the CSV; some dossiers will).
