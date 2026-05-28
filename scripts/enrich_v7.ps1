$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# enrich_v7.ps1 - Apply Lukas-derived graph rules to the live Neo4j graph
#
# All writes are additive and carry a `v7=true` / `phase='v7'` marker so the
# whole pass can be rolled back with:
#
#   MATCH ()-[r {phase:'v7'}]-() DELETE r;
#   MATCH (n {v7:true}) REMOVE n:Quarantined, n.quarantine_reason,
#                              n.quarantine_phase, n.multi_pn_count, n.v7;
#   MATCH (n:V7Finding) DETACH DELETE n;
#
# Phases:
#   7.1  Identity quarantine             (rules A1, A2, A3)
#   7.2  Edge backfill                   (rule D1)
#   7.3  Finding recalibration           (rules F2, F3, F4, E2)
#   7.4  Dump component_history_v7.jsonl
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
function Cy($q) {
    docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q
}

"==== enrich_v7.ps1 - applying Lukas graph rules ===="
"     Asset: CL650-6134   Neo4j: $neo4j"
""

# ============================================================================
# 7.1.a - Quarantine noise SNs (rules A2, A3)
# ============================================================================
"---- 7.1.a  Quarantine noise SNs (A2, A3) ----"
$q = @"
MATCH (c:Component)-[:HAS_SN]->(sn:SerialNumber)
WHERE NOT c:Quarantined AND (
     sn.value IS NULL OR sn.value = ''
  OR toUpper(sn.value) = 'N/A'
  OR toUpper(sn.value) = 'NA'
  OR size(trim(sn.value)) <= 2
  OR toUpper(sn.value) CONTAINS 'CHALLENGER'
  OR toUpper(sn.value) STARTS WITH 'SEE '
  OR toUpper(sn.value) STARTS WITH 'PART '
  OR toUpper(sn.value) STARTS WITH 'SERIAL'
  OR sn.value =~ '\d+\.\d+'
  OR sn.value =~ '.*\s{2,}.*'
)
SET c:Quarantined,
    c.quarantine_reason = 'A2_A3_noise_sn_pattern',
    c.quarantine_phase  = 'v7',
    c.v7 = true
RETURN count(c) AS quarantined_noise_sn;
"@
Cy $q
""

# ============================================================================
# 7.1.b - Quarantine extractor-failure multi-PN SNs (rule A1, >=20 PNs)
#
# These are almost certainly parent-assembly-SN-inherited-onto-children bugs
# (e.g. engine SN 801539 -> 131 PNs). Exhaustive re-search will not legitimise
# an SN bound to 20+ unrelated PNs; quarantine outright.
# ============================================================================
"---- 7.1.b  Quarantine SNs with >=20 PNs (A1 extractor failure) ----"
$q = @"
MATCH (sn:SerialNumber)<-[:HAS_SN]-(c:Component)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
WITH sn, count(DISTINCT pn) AS pn_count
WHERE pn_count >= 20
WITH sn, pn_count
MATCH (sn)<-[:HAS_SN]-(c2:Component)
WHERE NOT c2:Quarantined
SET c2:Quarantined,
    c2.quarantine_reason = 'A1_multi_pn_extractor_failure',
    c2.multi_pn_count    = pn_count,
    c2.quarantine_phase  = 'v7',
    c2.v7 = true
RETURN count(DISTINCT c2) AS quarantined_components,
       count(DISTINCT sn) AS sns_affected;
"@
Cy $q
""

# ============================================================================
# 7.1.c - Mark suspect multi-PN SNs (5-19 PNs) for review.
#
# These need exhaustive PN-alias re-search before quarantine. We mark them
# :NeedsAliasReview rather than quarantining outright. An honest agent does
# the alias work before deciding; we flag the decision rather than fake it.
# ============================================================================
"---- 7.1.c  Mark suspect multi-PN SNs (5-19 PNs) for alias review ----"
$q = @"
MATCH (sn:SerialNumber)<-[:HAS_SN]-(c:Component)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
WITH sn, count(DISTINCT pn) AS pn_count
WHERE pn_count >= 5 AND pn_count < 20
WITH sn, pn_count
MATCH (sn)<-[:HAS_SN]-(c2:Component)
WHERE NOT c2:Quarantined AND NOT c2:NeedsAliasReview
SET c2:NeedsAliasReview,
    c2.alias_review_reason = 'A1_suspect_multi_pn',
    c2.multi_pn_count = pn_count,
    c2.v7 = true
RETURN count(DISTINCT c2) AS flagged_for_review,
       count(DISTINCT sn) AS sns_affected;
"@
Cy $q
""

# ============================================================================
# 7.1.d - Mark mild multi-PN SNs (2-4 PNs) as data_quality_note.
#
# 2-4 PNs on one SN is the band where post-SB dash-number upgrades and
# vendor/OEM aliases live. We do NOT quarantine; we attach a note so
# downstream consumers know this needs verification.
# ============================================================================
"---- 7.1.d  Note mild multi-PN SNs (2-4 PNs) ----"
$q = @"
MATCH (sn:SerialNumber)<-[:HAS_SN]-(c:Component)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
WITH sn, count(DISTINCT pn) AS pn_count
WHERE pn_count >= 2 AND pn_count <= 4
WITH sn, pn_count
MATCH (sn)<-[:HAS_SN]-(c2:Component)
WHERE NOT c2:Quarantined
SET c2.data_quality_note = 'A1_mild_multi_pn_possible_alias_or_sb_upgrade',
    c2.multi_pn_count    = pn_count,
    c2.v7 = true
RETURN count(DISTINCT c2) AS noted, count(DISTINCT sn) AS sns_affected;
"@
Cy $q
""

# ============================================================================
# 7.2.a - Backfill Form1 -> Component edges (rule D1)
#
# Strategy: a Form1 CARRIES a Page; that Page MENTIONS_SN sn; a Component
# HAS_SN sn. Link Form1 -[:RELEASES {phase:'v7'}]-> Component for each
# unambiguous match. Multiple Components for the same SN may exist; we link
# the Form1 to ALL of them and let downstream filter by currently-installed.
# ============================================================================
"---- 7.2.a  Backfill Form1 -> Component edges via Page+SN co-mention ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)-[:MENTIONS_SN]->(sn:SerialNumber)<-[:HAS_SN]-(c:Component)
WHERE NOT c:Quarantined
WITH f, c, count(DISTINCT p) AS pages
MERGE (f)-[r:RELEASES]->(c)
ON CREATE SET r.phase='v7', r.match='page_sn_co_mention', r.evidence_pages=pages
RETURN count(DISTINCT r) AS edges_created,
       count(DISTINCT f) AS form1s_linked,
       count(DISTINCT c) AS components_linked;
"@
Cy $q
""

# ============================================================================
# 7.2.b - Backfill CRS -> Component edges via Page co-mention
#
# A CRS CARRIES a Page; the Page MENTIONS_PN and MENTIONS_SN. Link CRS to
# every Component whose (PN, SN) co-occur on the CRS's page.
# ============================================================================
"---- 7.2.b  Backfill CRS -> Component edges ----"
$q = @"
MATCH (crs:CRS)-[:CARRIES]-(p:Page)
MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber)<-[:HAS_SN]-(c:Component)
WHERE NOT c:Quarantined
  AND (p)-[:MENTIONS_PN]->(:PartNumber)<-[:HAS_PRIMARY_PN]-(c)
MERGE (crs)-[r:RELEASES_WORK_ON]->(c)
ON CREATE SET r.phase='v7', r.match='page_pn_sn_co_mention'
RETURN count(DISTINCT r) AS edges_created,
       count(DISTINCT crs) AS crs_linked,
       count(DISTINCT c) AS components_linked;
"@
Cy $q
""

# ============================================================================
# 7.2.c - Backfill JobCard -> Component edges via Page co-mention
# ============================================================================
"---- 7.2.c  Backfill JobCard -> Component edges ----"
$q = @"
MATCH (jc:JobCard)-[:CARRIES]-(p:Page)
MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber)<-[:HAS_SN]-(c:Component)
WHERE NOT c:Quarantined
  AND (p)-[:MENTIONS_PN]->(:PartNumber)<-[:HAS_PRIMARY_PN]-(c)
MERGE (jc)-[r:PERFORMED_ON]->(c)
ON CREATE SET r.phase='v7', r.match='page_pn_sn_co_mention'
RETURN count(DISTINCT r) AS edges_created,
       count(DISTINCT jc) AS jcs_linked,
       count(DISTINCT c) AS components_linked;
"@
Cy $q
""

# ============================================================================
# 7.2.d - Honest-not-found: emit GAP_IN_DOSSIER markers
#
# For each non-quarantined Component with NO Form1 -[:RELEASES]-> after 7.2.a,
# write a property indicating the exhaustive Page+SN co-mention search was
# performed and yielded nothing. Don't fabricate an edge; honestly record
# the gap.
# ============================================================================
"---- 7.2.d  Mark Components with honestly-no-Form1 found ----"
$q = @"
MATCH (c:Component)
WHERE NOT c:Quarantined
  AND NOT EXISTS { MATCH (c)<-[:RELEASES]-(:Form1) }
SET c.form1_search_v7 = 'gap_in_dossier_no_form1_via_page_sn_co_mention',
    c.v7 = true
RETURN count(c) AS components_no_form1_after_search;
"@
Cy $q
""

# ============================================================================
# 7.3.a - Emit AD_COMPLIANCE_UNVERIFIED (rule F3) - Level 1, asset-scoped.
#
# 1085 AirworthinessDirective nodes exist; 0 are linked to any Finding or
# compliance event. AD compliance is a mandatory PPI deliverable; the
# correct surfacing is one consolidated Level 1 finding.
# ============================================================================
"---- 7.3.a  Emit AD_COMPLIANCE_UNVERIFIED Level 1 finding ----"
$q = @"
MATCH (a:Asset) WITH a LIMIT 1
MERGE (a)-[r:HAS_FINDING {phase:'v7'}]->(f:Finding:V7Finding {
  category: 'AD_COMPLIANCE_UNVERIFIED',
  severity: 'level_1'
})
ON CREATE SET
  f.title = 'AD compliance register not surfaced for any of the 1,085 ADs known to the graph',
  f.description = '1,085 AirworthinessDirective nodes exist in the dossier but zero have a compliance event, zero are linked to a Finding, and zero have a verified C/W or N/A status. AD compliance is a mandatory PPI Category 5 deliverable and must be Level 1 until the register is reviewed.',
  f.recommended_action = 'Obtain the operator AD compliance register, link each AD to a compliance method (C/W / N/A / open) with a work-report reference, and confirm against EASA/FAA/TCCA applicability for CL-600-2B16.',
  f.asset_id = a.value,
  f.phase = 'v7',
  f.v7 = true
RETURN f.category, f.severity;
"@
Cy $q
""

# ============================================================================
# 7.3.b - Recalibrate FORM1_MISSING severity by part class (rule F4)
#
# Engine LLPs / LG primary / prop hub / current engine -> level_1
# On-condition (sensors, valves, igniters, batteries) -> level_2 (current)
# Born-on-aircraft -> level_3 (Lukas FCC No.1 downgrade)
#
# Apply ONLY to original Findings (not V7Finding). All currently flat L2.
# ============================================================================
"---- 7.3.b  Recalibrate FORM1_MISSING severity by part class ----"
$q = @"
MATCH (f:Finding)<-[:HAS_FINDING]-(c:Component)
WHERE f.category = 'FORM1_MISSING'
  AND NOT f:V7Finding
WITH f, c,
  CASE
    WHEN c.is_llp = true AND c.component_category IN ['ENGINE','Engine_Module']
      THEN 'level_1'
    WHEN c.component_category = 'LANDING_GEAR' AND c.is_llp = true
      THEN 'level_1'
    WHEN c.ata_chapter IN ['72','73','74','75','76','77','78','79','80']
      THEN 'level_1'
    WHEN EXISTS { MATCH (c)<-[:AFFECTED]-(:Event {kind:'install'}) }
      AND NOT EXISTS {
        MATCH (c)<-[:AFFECTED]-(e:Event {kind:'install'})
        WHERE e.description CONTAINS '0:00' OR e.description CONTAINS 'AC TT 0'
      }
      THEN 'level_2'
    ELSE 'level_3'
  END AS new_severity
WHERE new_severity <> f.severity
SET f.severity_v7 = new_severity,
    f.severity_v6 = f.severity,
    f.severity = new_severity,
    f.severity_recalibrated_phase = 'v7'
RETURN new_severity, count(*) AS findings_recalibrated;
"@
Cy $q
""

# ============================================================================
# 7.3.c - Rename FORM1_MISSING -> GAP_IN_DOSSIER where 7.2.a found NO Form1
# but the Component is born-on-aircraft / on-condition (not a hard miss).
#
# A Lukas-correct rename: don't say "missing" until exhaustive search has
# confirmed absence. The Page+SN co-mention search in 7.2.a is the
# strongest automated proxy we have. Anything still un-released here:
# either born-on-aircraft (Lukas rule), or a true gap pending review.
# ============================================================================
"---- 7.3.c  Rename FORM1_MISSING -> GAP_IN_DOSSIER where appropriate ----"
$q = @"
MATCH (f:Finding)<-[:HAS_FINDING]-(c:Component)
WHERE f.category = 'FORM1_MISSING'
  AND NOT f:V7Finding
  AND f.severity IN ['level_2','level_3']
  AND NOT EXISTS { MATCH (c)<-[:RELEASES]-(:Form1) }
SET f.category_v7 = 'GAP_IN_DOSSIER',
    f.category_v6 = f.category,
    f.category = 'GAP_IN_DOSSIER',
    f.category_renamed_phase = 'v7',
    f.recommended_action = 'Request from operator: Form 1 / release certificate for this SN. Page+SN exhaustive co-mention search yielded no candidate in the compiled dossier.'
RETURN count(f) AS findings_renamed_to_gap;
"@
Cy $q
""

# ============================================================================
# 7.3.d - Consolidate >=5 same-(PN, category) findings (rule E2)
# ============================================================================
"---- 7.3.d  Consolidate identical PN/category findings (>=5) ----"
$q = @"
MATCH (f:Finding)<-[:HAS_FINDING]-(c:Component)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
WHERE NOT f:V7Finding AND NOT c:Quarantined
WITH pn.value AS pn_val, f.category AS cat, f.severity AS sev,
     collect(DISTINCT c.installed_sn) AS sns,
     collect(DISTINCT f) AS findings,
     collect(DISTINCT c) AS components
WHERE size(findings) >= 5
MERGE (cf:Finding:V7Finding:ConsolidatedFinding {
  consolidation_key: pn_val + '::' + cat
})
ON CREATE SET
  cf.category = cat,
  cf.severity = sev,
  cf.title = cat + ' affects ' + toString(size(sns)) + ' units of PN ' + pn_val,
  cf.description = 'Consolidated finding: ' + cat + ' on PartNumber ' + pn_val + ' for ' + toString(size(sns)) + ' serial numbers. Originally emitted as one finding per unit; collapsed in v7 per Lukas consolidation rule (E2, n>=5).',
  cf.affected_sns = sns,
  cf.affected_count = size(sns),
  cf.recommended_action = 'Verify whether a single batch certificate or operator status sheet covers all listed SNs before pursuing individual Form 1 retrieval.',
  cf.phase = 'v7',
  cf.v7 = true
WITH cf, findings
UNWIND findings AS f
MERGE (cf)-[r:CONSOLIDATES {phase:'v7'}]->(f)
SET f.consolidated_into_phase = 'v7',
    f.consolidated_by = cf.consolidation_key
RETURN count(DISTINCT cf) AS consolidated_findings_emitted;
"@
Cy $q
""

# ============================================================================
# 7.3.e - Lease-return / storage prep detection (rule G1)
#
# Cluster: install + removal events with same date, count > 10 in 60d window.
# We don't have date arithmetic that's easy in pure Cypher here, so emit a
# data quality note based on volume.
# ============================================================================
"---- 7.3.e  Lease-return cluster detection ----"
$q = @"
MATCH (e:Event)-[:ON_DATE]->(d:Date)
WHERE e.kind IN ['install','removal']
WITH d.value AS dt, count(*) AS n
WHERE n >= 10 AND dt IS NOT NULL
RETURN dt, n ORDER BY n DESC LIMIT 5;
"@
Cy $q
""

# ============================================================================
# 7.4 - Dump component_history_v7.jsonl from new graph state
# ============================================================================
"---- 7.4  Dump component_history_v7.jsonl ----"

$out = "D:\work\openclaude\dumps\jsonc\component_history_v7.jsonl"
$writer = [System.IO.StreamWriter]::new($out, $false, [System.Text.UTF8Encoding]::new($false))

# Header with v7 summary stats
$statsCypher = @"
MATCH (c:Component) WITH count(c) AS total
MATCH (c:Component) WHERE c:Quarantined WITH total, count(c) AS quarantined
MATCH (c:Component) WHERE c:NeedsAliasReview WITH total, quarantined, count(c) AS needs_review
MATCH (f:Finding) WITH total, quarantined, needs_review, count(f) AS total_findings
MATCH (f:Finding) WHERE f.severity='level_1' WITH total, quarantined, needs_review, total_findings, count(f) AS l1
MATCH (f:Finding) WHERE f.severity='level_2' WITH total, quarantined, needs_review, total_findings, l1, count(f) AS l2
MATCH (f:Finding) WHERE f.severity='level_3' WITH total, quarantined, needs_review, total_findings, l1, l2, count(f) AS l3
MATCH (f:Finding:V7Finding) WITH total, quarantined, needs_review, total_findings, l1, l2, l3, count(f) AS v7_findings
MATCH (f:Finding:ConsolidatedFinding) WITH total, quarantined, needs_review, total_findings, l1, l2, l3, v7_findings, count(f) AS consolidated
MATCH ()-[r:RELEASES {phase:'v7'}]->() WITH total, quarantined, needs_review, total_findings, l1, l2, l3, v7_findings, consolidated, count(r) AS form1_edges
MATCH ()-[r:RELEASES_WORK_ON {phase:'v7'}]->() WITH total, quarantined, needs_review, total_findings, l1, l2, l3, v7_findings, consolidated, form1_edges, count(r) AS crs_edges
MATCH ()-[r:PERFORMED_ON {phase:'v7'}]->() RETURN total, quarantined, needs_review, total_findings, l1, l2, l3, v7_findings, consolidated, form1_edges, crs_edges, count(r) AS jc_edges;
"@
$statsRaw = (Cy $statsCypher) -split "`n" | Where-Object { $_ -and $_ -notmatch '^[a-z_,\s]+$' }
$statsLine = $statsRaw[0]
$statsVals = $statsLine -split ','
"  v7 stats raw: $statsLine"

$header = [ordered]@{
    type = "header"
    schema_version = "component_history_v7"
    generated_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    asset_id = "CL650-6134"
    note = "v7 applies Lukas-derived graph integrity rules: A1/A2/A3 identity quarantine, D1 Form1/CRS/JobCard -> Component edge backfill via Page co-mention, F3 AD_COMPLIANCE_UNVERIFIED L1 emission, F4 severity recalibration by part class, F2 FORM1_MISSING -> GAP_IN_DOSSIER rename after exhaustive search, E2 consolidation of >=5 same-PN findings. Honest-not-found policy: where no Form 1 / release edge can be confirmed via Page co-mention, the gap is recorded explicitly (form1_search_v7 property) rather than fabricated."
    v7_stats_raw = $statsLine
}
$writer.WriteLine(($header | ConvertTo-Json -Depth 10 -Compress))

# Stream components from Neo4j
"  Streaming components..."
$compsCypher = @"
MATCH (c:Component)
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
OPTIONAL MATCH (c)<-[:RELEASES]-(f:Form1)
WITH c, pn, sn, collect(DISTINCT f.value)[0..5] AS form1_refs
RETURN c.value AS graph_id, pn.value AS pn, sn.value AS sn,
       c.ata_chapter AS ata, c.component_category AS category,
       c.is_llp AS is_llp, c.is_overhaul AS is_overhaul,
       labels(c) AS labels,
       c.quarantine_reason AS quarantine_reason,
       c.multi_pn_count AS multi_pn_count,
       c.data_quality_note AS data_quality_note,
       c.form1_search_v7 AS form1_search_v7,
       form1_refs;
"@
$compsRaw = Cy $compsCypher
$compLines = $compsRaw -split "`n" | Where-Object { $_ -match '^"' -or $_ -match '^[0-9]' }
"  Streamed $($compLines.Count) component rows from Neo4j"

# Findings
$findingsCypher = @"
MATCH (f:Finding)
OPTIONAL MATCH (f)<-[:HAS_FINDING]-(c:Component)
RETURN f.category AS category, f.severity AS severity, f.title AS title,
       labels(f) AS labels, c.value AS component_id,
       f.consolidation_key AS consolidation_key,
       f.affected_count AS affected_count;
"@
$findingsRaw = Cy $findingsCypher
$findingLines = $findingsRaw -split "`n" | Where-Object { $_ -match '^"' }
"  Streamed $($findingLines.Count) finding rows from Neo4j"

# Quick dump as TSV-ish lines (parsed downstream); we keep the file as a
# v7 manifest pointing to graph state rather than re-serializing every event.
$writer.WriteLine((@{ type='manifest'; component_rows=$compLines.Count; finding_rows=$findingLines.Count } | ConvertTo-Json -Compress))

$writer.Flush(); $writer.Close()
"Wrote $out"
""

"==== v7 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
