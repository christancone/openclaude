-- Canonical SQLite schema for graph.db. Copy verbatim into tools.py init_db().
-- Every NOT NULL column is intentional — they enforce the golden rule that
-- every fact in the graph traces back to (file_name, page_index, evidence_quote).

PRAGMA foreign_keys = ON;

-- The asset itself (1 row)
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    asset_kind TEXT NOT NULL,        -- AIRCRAFT | ENGINE | PROPELLER | LANDING_GEAR_ASSEMBLY | APU | ROTOR_SYSTEM | GEARBOX | COMPONENT
    subtype TEXT,
    type_designation TEXT,
    tcds TEXT,
    yom INTEGER,
    msn TEXT,
    registration TEXT,
    registration_history TEXT,       -- JSON array of { reg, country, from_date, to_date }
    operator TEXT,
    owner TEXT,
    primary_serial TEXT,
    state TEXT,                      -- active | preserved | shop_visit | parted_out | lease_return
    tsn REAL, csn INTEGER,
    tsn_confidence TEXT, csn_confidence TEXT,
    dossier_date TEXT,
    profile_json TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS components (
    id TEXT PRIMARY KEY,             -- "component::{canonical_pn}::{installed_sn}"
    asset_id TEXT,
    canonical_pn TEXT,
    alternate_pns TEXT,              -- JSON array
    installed_sn TEXT,
    description TEXT,
    ata_chapter TEXT,
    tier TEXT,
    position TEXT,
    parent_component_id TEXT,
    status TEXT,                     -- CLOSED | PARTIAL | GAP | INSTALLED_AT_MFG | DISCOVERED
    is_llp INTEGER DEFAULT 0,
    is_overhaul INTEGER DEFAULT 0,
    tsn REAL, tsn_confidence TEXT,
    csn INTEGER, csn_confidence TEXT,
    tso REAL, cso INTEGER,
    limit_cycles INTEGER, limit_hours REAL,
    remaining_cycles INTEGER, remaining_hours REAL,
    last_form1_file TEXT, last_form1_page INTEGER, last_form1_date TEXT,
    last_overhaul_file TEXT, last_overhaul_page INTEGER, last_overhaul_date TEXT,
    last_mro TEXT,
    notes TEXT,
    FOREIGN KEY (asset_id) REFERENCES assets(id),
    FOREIGN KEY (parent_component_id) REFERENCES components(id)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    component_id TEXT,
    asset_id TEXT,
    event_type TEXT,
    task_compliance_status TEXT,
    compliance_status_reason TEXT,
    event_date TEXT,
    work_order_id TEXT,
    work_package_id TEXT,
    mro TEXT,
    tsn_at_event REAL,
    csn_at_event INTEGER,
    description TEXT,
    task_reference TEXT,
    file_name TEXT NOT NULL,
    page_index INTEGER NOT NULL,
    chunk_id TEXT,
    text_evidence TEXT NOT NULL,
    confidence TEXT,
    evidentiary_weight TEXT,
    FOREIGN KEY (component_id) REFERENCES components(id),
    FOREIGN KEY (asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    document_type TEXT,
    evidentiary_weight TEXT,
    is_mis_export INTEGER DEFAULT 0,
    mis_system TEXT,
    title TEXT,
    issue_date TEXT,
    issuer TEXT,
    work_order_id TEXT,
    work_package_id TEXT,
    page_count INTEGER,
    original_path TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    document_id TEXT,
    page_index INTEGER,
    document_type TEXT,
    evidentiary_weight TEXT,
    is_blank INTEGER DEFAULT 0,
    is_template_empty INTEGER DEFAULT 0,
    rotation_hint INTEGER DEFAULT 0,
    is_mis_export INTEGER DEFAULT 0,
    mis_system TEXT,
    title TEXT,
    date TEXT,
    work_order_id TEXT,
    enhanced_s3_key TEXT,
    text_content TEXT,
    ata_chapters TEXT,               -- JSON array
    part_numbers TEXT,               -- JSON array
    serial_numbers TEXT,             -- JSON array
    reference_numbers TEXT,          -- JSON array of {type, value}
    regulatory_references TEXT,      -- JSON array
    context_discrepancy TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS stamps (
    id TEXT PRIMARY KEY,             -- "{page_id}::{stamp_id}"
    page_id TEXT,
    stamp_local_id TEXT,
    type TEXT,
    text TEXT,
    person_name TEXT,
    title_role TEXT,
    date TEXT,
    certificate_number TEXT,
    location_context TEXT,
    binds_to_target_kind TEXT,
    binds_to_target_ref TEXT,
    binding_confidence TEXT,
    binding_reason TEXT,
    FOREIGN KEY (page_id) REFERENCES pages(id)
);

CREATE TABLE IF NOT EXISTS work_orders (
    id TEXT PRIMARY KEY,
    work_package_id TEXT,
    description TEXT,
    open_date TEXT,
    close_date TEXT,
    mro TEXT,
    has_crs INTEGER DEFAULT 0,
    crs_file_name TEXT,
    crs_page_index INTEGER,
    component_count INTEGER DEFAULT 0,
    is_administrative INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS work_packages (
    id TEXT PRIMARY KEY,
    description TEXT,
    start_date TEXT,
    end_date TEXT,
    mro TEXT,
    asset_id TEXT,
    inferred INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS asset_relations (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    from_kind TEXT NOT NULL,         -- COMPONENT | ASSET | WORK_ORDER
    to_id TEXT NOT NULL,
    to_kind TEXT NOT NULL,
    relation_type TEXT NOT NULL,     -- parent_of | replaced_by | installed_on | shop_visit_at | wo_chain
    valid_from TEXT,
    valid_to TEXT,
    confidence TEXT,
    evidence_file TEXT NOT NULL,
    evidence_page INTEGER NOT NULL,
    evidence_chunk_id TEXT,
    evidence_quote TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_relations_from  ON asset_relations(from_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_relations_to    ON asset_relations(to_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_relations_valid ON asset_relations(valid_from, valid_to);

CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,             -- "{kind}::{number}::{revision}"
    kind TEXT,                       -- AD | SB | EO | STC | ICA | TASK | LIMIT
    number TEXT,
    revision TEXT,
    title TEXT,
    issuer TEXT,                     -- EASA | FAA | TCCA | DGCA | FOCA | CAAC | ANAC | OEM
    applicability TEXT,
    superseded_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS stakeholders (
    id TEXT PRIMARY KEY,
    name TEXT,
    kind TEXT,                       -- OPERATOR | OWNER | LESSOR | MRO | OEM | REGULATOR
    country TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT,
    stamp TEXT,
    license_no TEXT,
    role TEXT,
    organisation_id TEXT
);

CREATE TABLE IF NOT EXISTS part_types (
    id TEXT PRIMARY KEY,             -- canonical PN
    alternate_pns TEXT,              -- JSON array
    description TEXT,
    ata_chapter TEXT,
    is_llp INTEGER DEFAULT 0,
    is_overhaul INTEGER DEFAULT 0,
    typical_tier TEXT
);

CREATE TABLE IF NOT EXISTS serials (
    id TEXT PRIMARY KEY,             -- "{canonical_pn}::{sn}"
    part_type_id TEXT,
    serial_number TEXT,
    component_id TEXT,
    FOREIGN KEY (part_type_id) REFERENCES part_types(id),
    FOREIGN KEY (component_id) REFERENCES components(id)
);

CREATE TABLE IF NOT EXISTS ata_chapters (
    id TEXT PRIMARY KEY,             -- e.g. "ATA32"
    chapter_number TEXT,
    title TEXT,
    tier TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    target_kind TEXT,                -- COMPONENT | EVENT | DOCUMENT | ASSET | REQUIREMENT | STAMP
    target_id TEXT,
    finding_type TEXT,
    severity INTEGER,                -- 1 | 2 | 3
    original_severity INTEGER,
    severity_downgrade_reason TEXT,
    description TEXT,
    what_auditor_needs TEXT,
    file_name TEXT,
    page_index INTEGER,
    chunk_id TEXT,
    status TEXT DEFAULT 'open',      -- open | provisional | closed | false_positive
    discipline_complete INTEGER DEFAULT 0,
    verification_strategy TEXT,
    resolution TEXT,
    resolution_file TEXT,
    resolution_page INTEGER,
    resolution_chunk_id TEXT,
    resolution_quote TEXT
);

CREATE TABLE IF NOT EXISTS priority_items (
    id TEXT PRIMARY KEY,
    rank INTEGER,
    component_id TEXT,
    reason TEXT,
    urgency TEXT,                    -- critical | high | medium
    metric REAL,
    evidence_file TEXT,
    evidence_page INTEGER,
    notes TEXT,
    FOREIGN KEY (component_id) REFERENCES components(id)
);

CREATE TABLE IF NOT EXISTS lease_return_state (
    asset_id TEXT PRIMARY KEY,
    is_lease_return INTEGER DEFAULT 0,
    window_start TEXT,
    window_end TEXT,
    wo_count_in_window INTEGER,
    dummy_tag_count INTEGER,
    notes TEXT,
    FOREIGN KEY (asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    confidence TEXT,
    evidence_file TEXT,
    evidence_page INTEGER,
    evidence_chunk_id TEXT,
    evidence_quote TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_edges_source     ON edges(source_id, source_kind);
CREATE INDEX IF NOT EXISTS idx_edges_target     ON edges(target_id, target_kind);
CREATE INDEX IF NOT EXISTS idx_edges_type       ON edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_pages_doc        ON pages(document_id);
CREATE INDEX IF NOT EXISTS idx_events_component ON events(component_id);
CREATE INDEX IF NOT EXISTS idx_events_wo        ON events(work_order_id);
CREATE INDEX IF NOT EXISTS idx_components_asset ON components(asset_id);
CREATE INDEX IF NOT EXISTS idx_components_tier  ON components(tier);
CREATE INDEX IF NOT EXISTS idx_stamps_page      ON stamps(page_id);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    page_id,
    text_content,
    file_name,
    document_type,
    content='pages',
    content_rowid='rowid'
);
