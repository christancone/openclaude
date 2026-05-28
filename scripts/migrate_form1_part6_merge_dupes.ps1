$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# migrate_form1_part6_merge_dupes.ps1
#
# Non-destructive merge of duplicate Components - the Phase 4 / v8.2 gap
# where the same physical part appears as multiple :Component nodes because
# its PartNumbers exist in OCR variants (`024453` vs `024453-000`,
# `MC10` vs `MC10-04-127`, `GLF (XXX)-(X)` vs `GLF(XXX)-(X)`).
#
# Detection rule: two Components share the same SerialNumber AND their
# primary PNs are linked via ALIAS_OF with kind IN ['ocr_variant',
# 'incomplete_pn'] (the high-confidence kinds; vendor_oem_co_mention is
# excluded - those aliases caused the part-3 fanout damage).
#
# Resolution: pick the Component with the LONGER PN string as primary
# (the incomplete_pn convention says `024453` is the truncated form of
# `024453-000`, so the longer one is canonical). Tiebreak: most incoming
# edges (more evidence = more authoritative).
#
# Non-destructive: write :Component-[:MERGED_INTO]->:Component and tag the
# duplicate with :Component:DuplicateOf. Downstream queries filter via
# `WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }`.
#
# Then re-route RELEASES_STRUCT edges: every Form1 -> duplicate gets an
# additional Form1 -> primary RELEASES_STRUCT edge (MERGE, idempotent).
# The duplicate's RELEASES_STRUCT edges are tagged `r.target_is_merged=true`
# so viz can hide them.
#
# Sections:
#   33. Detect + write MERGED_INTO edges
#   34. Label duplicates with :DuplicateOf
#   35. Re-route RELEASES_STRUCT (add edge to primary, tag duplicate-targets)
#   36. Same for RELEASES_PN_STRUCT / RELEASES_SN_STRUCT (and the older RELEASES)
#   37. Verification: NiCad case should show 1 effective Component
#
# Rollback:
#   MATCH ()-[r:MERGED_INTO {phase:'migration_form1_csv_2026_05_19'}]-() DELETE r;
#   MATCH (c:DuplicateOf {merged_phase:'migration_form1_csv_2026_05_19'})
#     REMOVE c:DuplicateOf, c.merged_phase, c.merged_into_uid;
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw    = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
$tag   = 'migration_form1_csv_2026_05_19'

function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== migrate_form1_part6_merge_dupes.ps1 ===="
""

# ============================================================================
# 33. Detect duplicate pairs + write MERGED_INTO (longer PN wins)
# ============================================================================
"---- 33. Detect duplicates + write MERGED_INTO ----"
$q = @"
MATCH (c1:Component)-[:HAS_SN]->(sn:SerialNumber)<-[:HAS_SN]-(c2:Component)
WHERE id(c1) < id(c2) AND NOT c1:Quarantined AND NOT c2:Quarantined
MATCH (c1)-[:HAS_PRIMARY_PN]->(pn1:PartNumber)
MATCH (c2)-[:HAS_PRIMARY_PN]->(pn2:PartNumber)
MATCH path = (pn1)-[:ALIAS_OF*1..3]-(pn2)
WHERE all(rel IN relationships(path) WHERE rel.kind IN ['ocr_variant','incomplete_pn'])
WITH c1, c2, pn1, pn2, sn
// Pick the Component whose PN is LONGER as primary
WITH
  sn,
  CASE WHEN size(pn1.value) >= size(pn2.value) THEN c1 ELSE c2 END AS primary,
  CASE WHEN size(pn1.value) >= size(pn2.value) THEN c2 ELSE c1 END AS dup,
  CASE WHEN size(pn1.value) >= size(pn2.value) THEN pn1 ELSE pn2 END AS primary_pn,
  CASE WHEN size(pn1.value) >= size(pn2.value) THEN pn2 ELSE pn1 END AS dup_pn
WHERE NOT (dup)-[:MERGED_INTO]->()
MERGE (dup)-[r:MERGED_INTO]->(primary)
ON CREATE SET r.phase            = '$tag',
              r.primary_pn       = primary_pn.value,
              r.dup_pn           = dup_pn.value,
              r.shared_sn        = sn.value,
              r.alias_reason     = 'ocr_variant_or_incomplete_pn',
              r.merge_rule       = 'longer_pn_wins'
RETURN count(DISTINCT r) AS merged_into_edges;
"@
Cy $q
""

# ============================================================================
# 34. Label duplicates with :DuplicateOf
# ============================================================================
"---- 34. Label duplicates with :DuplicateOf ----"
$q = @"
MATCH (dup:Component)-[r:MERGED_INTO {phase:'$tag'}]->(primary:Component)
WHERE NOT dup:DuplicateOf
SET dup:DuplicateOf,
    dup.merged_phase = '$tag',
    dup.merged_into_uid = primary.value
RETURN count(dup) AS labeled;
"@
Cy $q
""

# ============================================================================
# 35. Re-route RELEASES_STRUCT edges
# ============================================================================
"---- 35a. Add RELEASES_STRUCT edges from Form1 to primary (mirror dup edges) ----"
$q = @"
MATCH (f:Form1)-[r_dup:RELEASES_STRUCT]->(dup:Component:DuplicateOf {merged_phase:'$tag'})
MATCH (dup)-[:MERGED_INTO]->(primary:Component)
MERGE (f)-[r_pri:RELEASES_STRUCT]->(primary)
ON CREATE SET r_pri.source            = r_dup.source,
              r_pri.confidence        = r_dup.confidence,
              r_pri.disposition       = r_dup.disposition,
              r_pri.form_tracking_no  = r_dup.form_tracking_no,
              r_pri.block             = r_dup.block,
              r_pri.phase             = r_dup.phase,
              r_pri.rerouted_from     = dup.value
RETURN count(DISTINCT r_pri) AS rerouted_to_primary;
"@
Cy $q

"---- 35b. Flag duplicate-targeted RELEASES_STRUCT edges ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES_STRUCT]->(dup:Component:DuplicateOf {merged_phase:'$tag'})
SET r.target_is_merged = true,
    r.target_merged_into = dup.merged_into_uid
RETURN count(r) AS flagged_dup_edges;
"@
Cy $q
""

# ============================================================================
# 36. Same for the older v7-derived RELEASES and the migration's RELEASES_PN/SN
# ============================================================================
"---- 36a. Tag v7 RELEASES edges pointing at duplicates ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->(dup:Component:DuplicateOf {merged_phase:'$tag'})
SET r.target_is_merged = true,
    r.target_merged_into = dup.merged_into_uid
RETURN count(r) AS flagged_v7_edges;
"@
Cy $q

"---- 36b. Same for Phase 4 / Phase 5 HAS_SN / HAS_PRIMARY_PN (informational only) ----"
$q = @"
MATCH (dup:Component:DuplicateOf {merged_phase:'$tag'})
OPTIONAL MATCH (dup)-[:HAS_SN]->(sn:SerialNumber)
OPTIONAL MATCH (dup)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
RETURN count(DISTINCT dup) AS dup_components,
       count(DISTINCT sn) AS dup_sns,
       count(DISTINCT pn) AS dup_pns;
"@
Cy $q
""

# ============================================================================
# 37. Verification
# ============================================================================
"==== Verification ===="

"-- NiCad case after merge --"
$q = @"
MATCH (f:Form1 {value:'form1::c608f9ce-aade-468c-9e46-1a5a5686899f'})-[r:RELEASES_STRUCT]->(c:Component)
OPTIONAL MATCH (c)-[m:MERGED_INTO]->(primary:Component)
RETURN c.value AS component, labels(c) AS labels, primary.value AS merged_into,
       r.target_is_merged AS target_is_merged;
"@
Cy $q

"-- NiCad effective components (filter out merged duplicates) --"
$q = @"
MATCH (f:Form1 {value:'form1::c608f9ce-aade-468c-9e46-1a5a5686899f'})-[r:RELEASES_STRUCT]->(c:Component)
WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }
RETURN c.value AS component, r.disposition AS disposition;
"@
Cy $q

"-- Bombardier case after merge (should still be 3) --"
$q = @"
MATCH (f:Form1 {value:'form1::B0462730 / 12 - 58'})-[r:RELEASES_STRUCT]->(c:Component)
WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
RETURN pn.value AS pn, sn.value AS sn, c.value AS component
ORDER BY pn, sn;
"@
Cy $q

"-- Overall: how many MERGED_INTO + dup-flagged edges --"
$q = @"
MATCH ()-[r:MERGED_INTO {phase:'$tag'}]->()
WITH count(r) AS merged_pairs
MATCH ()-[r:RELEASES_STRUCT]->()
WITH merged_pairs, count(r) AS releases_struct_total
MATCH ()-[r:RELEASES_STRUCT]->() WHERE r.target_is_merged = true
WITH merged_pairs, releases_struct_total, count(r) AS releases_struct_to_dup
MATCH ()-[r:RELEASES {phase:'v7'}]->() WHERE r.target_is_merged = true
RETURN merged_pairs, releases_struct_total, releases_struct_to_dup, count(r) AS v7_releases_to_dup;
"@
Cy $q

"==== part6 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
