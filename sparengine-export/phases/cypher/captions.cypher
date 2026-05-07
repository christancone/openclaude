// =============================================================================
//  SPARENGINE — Browser caption patch
// =============================================================================
//  Adds a `name` property to every node so Neo4j Browser's auto-caption logic
//  picks it up instead of falling back to `asset_id`. Idempotent — re-running
//  just refreshes the names from the latest property values.
//
//  Apply with:
//      cypher-shell -u neo4j -p "$NEO4J_PASSWORD" -f /import/captions.cypher
//
//  This is also baked into the DAL writers; the patch exists so existing
//  graphs (built before the DAL update) get fixed without re-running phases.
// =============================================================================


// ---- Layer 0 — Asset / Fleet / TC / Country -------------------------------
MATCH (a:Asset)
SET a.name = coalesce(
    a.name, a.registration, a.msn, a.value, a.asset_id
);

MATCH (f:Fleet)
SET f.name = coalesce(f.name, f.value);

MATCH (tc:TypeCertificate)
SET tc.name = coalesce(tc.model_designation, tc.tc_number, tc.value);

MATCH (cr:CountryRegistration)
SET cr.name = coalesce(cr.prefix, cr.iso_code, cr.value);


// ---- Carrier hierarchy -----------------------------------------------------
MATCH (d:Document)
SET d.name = coalesce(d.file_name, d.title, d.value);

MATCH (p:Page)
OPTIONAL MATCH (doc:Document)-[:HAS_PAGE]->(p)
WITH p, doc
SET p.name = 'p' + toString(coalesce(p.page_index, 0)) +
             CASE WHEN doc IS NOT NULL THEN ' · ' + coalesce(doc.file_name, '?')
                  ELSE '' END;

MATCH (n:Folder)        SET n.name = coalesce(n.name, n.value);
MATCH (n:Box)           SET n.name = coalesce(n.name, n.value);
MATCH (n:Binder)        SET n.name = coalesce(n.name, n.value);
MATCH (n:DocumentType)  SET n.name = coalesce(n.name, n.value);


// ---- Evidence records ------------------------------------------------------
MATCH (n:Form1)
SET n.name = 'Form 1 ' + coalesce(n.kind + '·', '') + n.value;

MATCH (n:CRS)              SET n.name = 'CRS ' + n.value;
MATCH (n:WorkPackage)      SET n.name = 'WO ' + n.value;
MATCH (n:JobCard)
SET n.name = 'JC ' + n.value +
             CASE WHEN n.ata IS NOT NULL THEN ' · ATA ' + n.ata ELSE '' END;
MATCH (n:NonRoutineCard)   SET n.name = 'NRC ' + n.value;
MATCH (n:Repair)
SET n.name = 'Repair' +
             CASE WHEN n.kind IS NOT NULL     THEN ' · ' + n.kind ELSE '' END +
             CASE WHEN n.location IS NOT NULL THEN ' @ ' + n.location ELSE '' END;
MATCH (n:Modification)     SET n.name = 'Mod ' + n.value;
MATCH (n:STC)              SET n.name = 'STC ' + n.value;
MATCH (n:BorescopeReport)  SET n.name = 'Borescope ' + coalesce(n.engine_position, '');
MATCH (n:NDTReport)
SET n.name = 'NDT' +
             CASE WHEN n.method IS NOT NULL THEN ' · ' + n.method ELSE '' END;
MATCH (n:DentBuckleEntry)
SET n.name = 'D&B' +
             CASE WHEN n.location IS NOT NULL THEN ' @ ' + n.location ELSE '' END;


// ---- External standards ----------------------------------------------------
MATCH (n:ATAChapter)             SET n.name = 'ATA ' + n.value;
MATCH (n:ServiceBulletin)         SET n.name = 'SB ' + n.value;
MATCH (n:AirworthinessDirective)  SET n.name = 'AD ' + n.value;
MATCH (n:EngineeringOrder)        SET n.name = 'EO ' + n.value;
MATCH (n:RegulatoryRef)           SET n.name = n.value;


// ---- Connector identifiers (the value IS the caption) ---------------------
MATCH (n:PartNumber)         SET n.name = 'PN ' + n.value;
MATCH (n:SerialNumber)        SET n.name = 'SN ' + n.value;
MATCH (n:CertificateNumber)   SET n.name = 'Cert ' + n.value;
MATCH (n:PurchaseOrder)       SET n.name = 'PO ' + n.value;
MATCH (n:DrawingNumber)       SET n.name = 'DWG ' + n.value;
MATCH (n:BatchNumber)         SET n.name = 'Batch ' + n.value;
MATCH (n:TechLogPage)         SET n.name = 'TLP ' + n.value;
MATCH (n:Reference)           SET n.name = coalesce(n.ref_type, '?') + ': ' + n.value;
MATCH (n:PartFamily)          SET n.name = 'Family ' + n.value;
MATCH (n:EngineModel)         SET n.name = 'Engine ' + n.value;
MATCH (n:APUModel)            SET n.name = 'APU ' + n.value;
MATCH (n:PropellerModel)      SET n.name = 'Prop ' + n.value;
MATCH (n:RotorAssemblyModel)  SET n.name = 'Rotor ' + n.value;


// ---- Time -----------------------------------------------------------------
MATCH (n:Date)
SET n.name = n.iso;

MATCH (n:Event)
SET n.name = coalesce(n.kind, 'event') +
             CASE WHEN n.event_date IS NOT NULL THEN ' · ' + n.event_date ELSE '' END +
             CASE WHEN n.description IS NOT NULL AND size(n.description) > 0
                  THEN ' · ' + substring(n.description, 0, 40)
                  ELSE '' END;

MATCH (n:ComponentSnapshot)
SET n.name = 'snapshot' +
             CASE WHEN n.date IS NOT NULL THEN ' ' + n.date ELSE '' END +
             CASE WHEN n.tsn IS NOT NULL THEN ' · TSN ' + toString(n.tsn) ELSE '' END;


// ---- Provenance -----------------------------------------------------------
MATCH (n:Stamp)
SET n.name = coalesce(n.type, 'stamp') +
             CASE WHEN n.person_name IS NOT NULL THEN ' · ' + n.person_name
                  WHEN n.text IS NOT NULL AND size(n.text) > 0
                       THEN ' · "' + substring(n.text, 0, 30) + '"'
                  ELSE '' END;


// ---- Domain entities ------------------------------------------------------
MATCH (c:Component)
SET c.name = coalesce(c.canonical_pn, '?') + ' / ' + coalesce(c.installed_sn, '?') +
             CASE WHEN c.description IS NOT NULL AND size(c.description) > 0
                  THEN ' · ' + substring(c.description, 0, 30)
                  ELSE '' END;

MATCH (n:Person)
SET n.name = coalesce(n.name, n.value);

MATCH (n:Organization)
SET n.name = coalesce(n.name, n.value) +
             CASE WHEN n.role IS NOT NULL THEN ' (' + n.role + ')' ELSE '' END;

MATCH (n:RegulatoryAuthority)     SET n.name = coalesce(n.name, n.value);
MATCH (n:DesignOrganization)      SET n.name = coalesce(n.name, n.value);
MATCH (n:ProductionOrganization)  SET n.name = coalesce(n.name, n.value);
MATCH (n:MaintenanceOrganization) SET n.name = coalesce(n.name, n.value);


// ---- Xlsx ledger ----------------------------------------------------------
MATCH (n:LogbookEntry)         SET n.name = 'Logbook · ' + coalesce(n.major_assembly, '?');
MATCH (n:TechLogEntry)         SET n.name = 'TechLog · ' + coalesce(n.value, '?');
MATCH (n:Manual)               SET n.name = 'Manual · ' + coalesce(n.reference, n.value);
MATCH (n:ElectronicDataEntry)  SET n.name = 'EData · ' + coalesce(n.reference, n.value);


// ---- Audit overlay --------------------------------------------------------
MATCH (n:Finding)
SET n.name = coalesce(n.severity, '?') + ' · ' + coalesce(n.category, '?') +
             CASE WHEN n.title IS NOT NULL AND size(n.title) > 0
                  THEN ' · ' + substring(n.title, 0, 40)
                  ELSE '' END;

MATCH (n:AuditRun)
SET n.name = coalesce(n.audit_snapshot_date, n.value);

MATCH (n:PriorityItem)
SET n.name = coalesce(n.urgency, '?') + ' · ' + coalesce(n.title, n.value);


// ---- QA scorecards (Layer B + Layer C) ------------------------------------
MATCH (n:QualityScorecard)
SET n.name = 'QA · v' + coalesce(n.sparengine_version, '?') +
             ' · mech ' + coalesce(toString(n.mechanical_overall), '?') +
             ' · llm '  + coalesce(toString(n.llm_mean), '?');

MATCH (n:BenchmarkRun)
SET n.name = coalesce(n.archetype, '?') + ' · ' + coalesce(n.version, '?') +
             CASE WHEN n.analysis_verdict IS NOT NULL
                  THEN ' · ' + n.analysis_verdict
                  ELSE '' END;

MATCH (n:PhaseScorecard)
SET n.name = coalesce(n.archetype, '?') + ' · ' + coalesce(n.phase_id, '?') +
             ' · v' + coalesce(n.version, '?') +
             CASE WHEN n.analysis_verdict IS NOT NULL
                  THEN ' · ' + n.analysis_verdict
                  ELSE '' END;
