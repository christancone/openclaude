// =============================================================================
//  SPARENGINE — NEO4J SCHEMA (replaces phases/schema.sql)
// =============================================================================
//  Replays into any Neo4j 5.x Community instance via:
//    cypher-shell -u neo4j -p "$NEO4J_PASSWORD" -f schema.cypher
//
//  Idempotent: every statement uses IF NOT EXISTS, so running it twice is safe
//  and is in fact the expected pattern (the orchestrator runs it on first
//  startup and at the start of any phase that may need new constraints).
//
//  The shape this file enforces matches the migration plan:
//    - Full per-asset isolation: every node carries `asset_id`.
//    - Composite uniqueness: (asset_id, value) on identifier-bearing nodes,
//      with two exceptions (`:Date` keys on `iso`, `:Reference` keys on
//      (ref_type, value)).
//    - Page-level evidence enforcement: enforced in the DAL, but a fulltext
//      index on :Page.text replaces the old SQLite FTS5 index for Phase 7.5
//      verification searches.
//
//  All constraints in Neo4j Community Edition are uniqueness constraints
//  (Enterprise also has property-existence and node-key constraints, which
//  we don't use). The "no fact node without page evidence" rule is enforced
//  by the DAL writers, not by schema constraints.
// =============================================================================


// -----------------------------------------------------------------------------
//  1. Carrier hierarchy
// -----------------------------------------------------------------------------
//  :Asset is unique on (asset_id) alone — it IS the asset.
CREATE CONSTRAINT asset_uid IF NOT EXISTS
    FOR (n:Asset) REQUIRE (n.asset_id) IS UNIQUE;

CREATE CONSTRAINT fleet_uid IF NOT EXISTS
    FOR (n:Fleet) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT folder_uid IF NOT EXISTS
    FOR (n:Folder) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT box_uid IF NOT EXISTS
    FOR (n:Box) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT binder_uid IF NOT EXISTS
    FOR (n:Binder) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT document_uid IF NOT EXISTS
    FOR (n:Document) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT page_uid IF NOT EXISTS
    FOR (n:Page) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT document_type_uid IF NOT EXISTS
    FOR (n:DocumentType) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  2. Type-cert / model layer
// -----------------------------------------------------------------------------
CREATE CONSTRAINT type_certificate_uid IF NOT EXISTS
    FOR (n:TypeCertificate) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT engine_model_uid IF NOT EXISTS
    FOR (n:EngineModel) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT apu_model_uid IF NOT EXISTS
    FOR (n:APUModel) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT propeller_model_uid IF NOT EXISTS
    FOR (n:PropellerModel) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT rotor_assembly_model_uid IF NOT EXISTS
    FOR (n:RotorAssemblyModel) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT part_family_uid IF NOT EXISTS
    FOR (n:PartFamily) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT country_registration_uid IF NOT EXISTS
    FOR (n:CountryRegistration) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  3. Evidence records (created in Phase 1)
// -----------------------------------------------------------------------------
CREATE CONSTRAINT form1_uid IF NOT EXISTS
    FOR (n:Form1) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT crs_uid IF NOT EXISTS
    FOR (n:CRS) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT work_package_uid IF NOT EXISTS
    FOR (n:WorkPackage) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT job_card_uid IF NOT EXISTS
    FOR (n:JobCard) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT non_routine_card_uid IF NOT EXISTS
    FOR (n:NonRoutineCard) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT repair_uid IF NOT EXISTS
    FOR (n:Repair) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT modification_uid IF NOT EXISTS
    FOR (n:Modification) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT stc_uid IF NOT EXISTS
    FOR (n:STC) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT dent_buckle_entry_uid IF NOT EXISTS
    FOR (n:DentBuckleEntry) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT borescope_report_uid IF NOT EXISTS
    FOR (n:BorescopeReport) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT ndt_report_uid IF NOT EXISTS
    FOR (n:NDTReport) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  4. External standards
// -----------------------------------------------------------------------------
CREATE CONSTRAINT service_bulletin_uid IF NOT EXISTS
    FOR (n:ServiceBulletin) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT airworthiness_directive_uid IF NOT EXISTS
    FOR (n:AirworthinessDirective) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT engineering_order_uid IF NOT EXISTS
    FOR (n:EngineeringOrder) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT regulatory_ref_uid IF NOT EXISTS
    FOR (n:RegulatoryRef) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT ata_chapter_uid IF NOT EXISTS
    FOR (n:ATAChapter) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  5. Connector identifiers (kept as distinct labels per Q10)
// -----------------------------------------------------------------------------
CREATE CONSTRAINT part_number_uid IF NOT EXISTS
    FOR (n:PartNumber) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT serial_number_uid IF NOT EXISTS
    FOR (n:SerialNumber) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT certificate_number_uid IF NOT EXISTS
    FOR (n:CertificateNumber) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT purchase_order_uid IF NOT EXISTS
    FOR (n:PurchaseOrder) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT drawing_number_uid IF NOT EXISTS
    FOR (n:DrawingNumber) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT batch_number_uid IF NOT EXISTS
    FOR (n:BatchNumber) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT tech_log_page_uid IF NOT EXISTS
    FOR (n:TechLogPage) REQUIRE (n.asset_id, n.value) IS UNIQUE;

// :Reference is the collapsed long tail. The same `value` may exist in two
// different `ref_type`s within the same dossier (e.g. an "approval" and a
// "tracking" number that happen to share digits), so the key is composite.
CREATE CONSTRAINT reference_uid IF NOT EXISTS
    FOR (n:Reference) REQUIRE (n.asset_id, n.ref_type, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  6. Domain entities
// -----------------------------------------------------------------------------
CREATE CONSTRAINT component_uid IF NOT EXISTS
    FOR (n:Component) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT person_uid IF NOT EXISTS
    FOR (n:Person) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT organization_uid IF NOT EXISTS
    FOR (n:Organization) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT regulatory_authority_uid IF NOT EXISTS
    FOR (n:RegulatoryAuthority) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT design_organization_uid IF NOT EXISTS
    FOR (n:DesignOrganization) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT production_organization_uid IF NOT EXISTS
    FOR (n:ProductionOrganization) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT maintenance_organization_uid IF NOT EXISTS
    FOR (n:MaintenanceOrganization) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  7. Time
// -----------------------------------------------------------------------------
CREATE CONSTRAINT event_uid IF NOT EXISTS
    FOR (n:Event) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT component_snapshot_uid IF NOT EXISTS
    FOR (n:ComponentSnapshot) REQUIRE (n.asset_id, n.value) IS UNIQUE;

// :Date keys on `iso` (e.g. "2024-03-15") rather than `value` — see Q5.
CREATE CONSTRAINT date_uid IF NOT EXISTS
    FOR (n:Date) REQUIRE (n.asset_id, n.iso) IS UNIQUE;


// -----------------------------------------------------------------------------
//  8. Provenance
// -----------------------------------------------------------------------------
CREATE CONSTRAINT stamp_uid IF NOT EXISTS
    FOR (n:Stamp) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
//  9. Xlsx ledger
// -----------------------------------------------------------------------------
CREATE CONSTRAINT logbook_entry_uid IF NOT EXISTS
    FOR (n:LogbookEntry) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT tech_log_entry_uid IF NOT EXISTS
    FOR (n:TechLogEntry) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT manual_uid IF NOT EXISTS
    FOR (n:Manual) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT electronic_data_entry_uid IF NOT EXISTS
    FOR (n:ElectronicDataEntry) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
// 10. Audit overlay
// -----------------------------------------------------------------------------
CREATE CONSTRAINT finding_uid IF NOT EXISTS
    FOR (n:Finding) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT audit_run_uid IF NOT EXISTS
    FOR (n:AuditRun) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT priority_item_uid IF NOT EXISTS
    FOR (n:PriorityItem) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
// 10b. QA scorecard nodes (Layer B + Layer C — run metadata, not fact-bearing)
// -----------------------------------------------------------------------------
//  :QualityScorecard — one per real-asset agent run. asset_id = real UUID.
//                      value = "scorecard::<run_id>". One scorecard may exist
//                      per run; re-runs of the rubric tool overwrite metrics
//                      in place via ON MATCH (the value stays stable).
//
//  :BenchmarkRun     — one per (version × archetype) meta-run captured by
//                      tools/benchmark_archive.py. asset_id = sentinel
//                      "benchmark::<archetype>" so multi-tenancy invariant
//                      holds without polluting real-asset queries.
//                      value = "benchmark::<version>::<archetype>".
//
//  :PhaseScorecard   — one per (version × archetype × phase_id). Same sentinel
//                      asset_id as :BenchmarkRun. value =
//                      "phase::<version>::<archetype>::<phase_id>".
//                      Linked into the graph via [:PART_OF]->(:BenchmarkRun)
//                      and [:NEXT] chains for trend traversal.
CREATE CONSTRAINT quality_scorecard_uid IF NOT EXISTS
    FOR (n:QualityScorecard) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT benchmark_run_uid IF NOT EXISTS
    FOR (n:BenchmarkRun) REQUIRE (n.asset_id, n.value) IS UNIQUE;

CREATE CONSTRAINT phase_scorecard_uid IF NOT EXISTS
    FOR (n:PhaseScorecard) REQUIRE (n.asset_id, n.value) IS UNIQUE;


// -----------------------------------------------------------------------------
// 11. Fulltext index — replaces SQLite FTS5 (`pages_fts` table)
// -----------------------------------------------------------------------------
//  Phase 7.5 verification re-searches the corpus for evidence of
//  the 9 strategies per finding. In SQLite this used FTS5 MATCH;
//  in Neo4j it uses CALL db.index.fulltext.queryNodes("page_text", $lucene).
//
//  Lucene syntax differs slightly from FTS5 MATCH:
//    SQLite:  WHERE pages_fts MATCH '"DUMMY UNSERVICEABLE"'
//    Lucene:  CALL db.index.fulltext.queryNodes("page_text", '"DUMMY UNSERVICEABLE"')
//
//  Both support phrase search with double quotes. Single-term and AND/OR
//  queries also map cleanly. The DAL's graph_dal/fulltext.py wraps this.
//
//  Indexed fields: :Page.text (the OCR'd page text) plus :Page.title where
//  populated.
CREATE FULLTEXT INDEX page_text IF NOT EXISTS
    FOR (p:Page) ON EACH [p.text, p.title];


// -----------------------------------------------------------------------------
// 12. Verification
// -----------------------------------------------------------------------------
//  After running this file, the orchestrator (or anyone bringing up a fresh
//  Neo4j) should run these to confirm:
//
//    SHOW CONSTRAINTS;          -- expect ~49 entries (was ~46 pre-Layer-B/C)
//    SHOW FULLTEXT INDEXES;     -- expect "page_text" listed
//
//  `graph_dal.verify.verify_schema()` runs the same check programmatically
//  and is called at the start of every phase.
