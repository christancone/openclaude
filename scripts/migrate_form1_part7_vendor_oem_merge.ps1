$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# migrate_form1_part7_vendor_oem_merge.ps1
#
# Vendor<->OEM PartNumber merge using the Form 1 multi-PN signal.
#
# Background: Part 6 merged duplicate Components when their PNs were linked
# via high-confidence OCR aliases (incomplete_pn / ocr_variant). That catches
# `024453` <-> `024453-000` but misses vendor<->OEM pairs like
# `AV16B2177-3` (ITT) <-> `601-62900-7` (Bombardier catalog) which represent
# the SAME physical valve but have no common substring.
#
# The signal we trust: when block 8 of a Form 1 lists multiple PNs in one
# cell for a single SN ("AV16B2177-3\n601-62900-7" + SN AA441157), the OEM
# is explicitly stating these are the same physical part. Part 5 wrote
# RELEASES_PN_STRUCT for both with the same form_tracking_no.
#
# Detection rule: two PartNumbers are vendor-OEM aliases if:
#   - The same Form 1 has RELEASES_PN_STRUCT to both
#   - AND the same Form 1 has RELEASES_SN_STRUCT to a common SN
#   - AND no existing high-confidence alias links them (avoid double-counting)
#
# Action:
#   - MERGE :ALIAS_OF {kind:'vendor_oem_form1_signal', confidence:'high'}
#   - Re-run the Component dedup rule from part 6 with this new alias
#     included in the path predicate.
#
# Sections:
#   38. Write vendor-OEM ALIAS_OF edges from Form 1 multi-PN signal
#   39. Re-detect duplicate Components (now including vendor-OEM aliases)
#   40. Label new dupes + re-route RELEASES_STRUCT
#   41. Verification: ITT valve should show 1 effective Component
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw    = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
$tag   = 'migration_form1_csv_2026_05_19'

function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== migrate_form1_part7_vendor_oem_merge.ps1 ===="
""

# ============================================================================
# 38. Vendor-OEM ALIAS_OF from Form 1 multi-PN signal
# ============================================================================
"---- 38. Write vendor-OEM ALIAS_OF from Form 1 multi-PN cells ----"
$q = @"
MATCH (f:Form1)-[r1:RELEASES_PN_STRUCT]->(pn_a:PartNumber)
MATCH (f)-[r2:RELEASES_PN_STRUCT]->(pn_b:PartNumber)
WHERE id(pn_a) < id(pn_b)
  AND r1.form_tracking_no IS NOT NULL
  AND r1.form_tracking_no = r2.form_tracking_no
  AND r1.source = 'csv_table_row'
  AND r2.source = 'csv_table_row'
// Both must release a common SN on the same cert (confirms same physical part)
MATCH (f)-[s1:RELEASES_SN_STRUCT]->(sn:SerialNumber)
WHERE NOT EXISTS { (pn_a)-[a:ALIAS_OF]-(pn_b) }
WITH pn_a, pn_b, f, sn,
     CASE WHEN size(pn_a.value) >= size(pn_b.value) THEN pn_a ELSE pn_b END AS primary,
     CASE WHEN size(pn_a.value) >= size(pn_b.value) THEN pn_b ELSE pn_a END AS alias_pn
MERGE (alias_pn)-[r:ALIAS_OF]->(primary)
ON CREATE SET r.phase            = '$tag',
              r.kind             = 'vendor_oem_form1_signal',
              r.confidence       = 'high',
              r.via_form1        = f.value,
              r.via_form_tracking_no = coalesce(f.block_3_form_tracking_no, ''),
              r.via_sn           = sn.value
RETURN count(DISTINCT r) AS vendor_oem_aliases;
"@
Cy $q
""

# ============================================================================
# 39. Re-detect duplicate Components (now also via vendor_oem_form1_signal)
# ============================================================================
"---- 39. Detect duplicates - now including vendor-OEM aliases ----"
$q = @"
MATCH (c1:Component)-[:HAS_SN]->(sn:SerialNumber)<-[:HAS_SN]-(c2:Component)
WHERE id(c1) < id(c2) AND NOT c1:Quarantined AND NOT c2:Quarantined
MATCH (c1)-[:HAS_PRIMARY_PN]->(pn1:PartNumber)
MATCH (c2)-[:HAS_PRIMARY_PN]->(pn2:PartNumber)
MATCH path = (pn1)-[:ALIAS_OF*1..3]-(pn2)
WHERE all(rel IN relationships(path)
          WHERE rel.kind IN ['ocr_variant','incomplete_pn','vendor_oem_form1_signal'])
WITH c1, c2, pn1, pn2, sn
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
              r.alias_reason     = 'incl_vendor_oem_form1_signal',
              r.merge_rule       = 'longer_pn_wins_v2'
RETURN count(DISTINCT r) AS new_merged_pairs;
"@
Cy $q
""

# ============================================================================
# 40. Label new dupes + re-route RELEASES_STRUCT
# ============================================================================
"---- 40a. Label new duplicates with :DuplicateOf ----"
$q = @"
MATCH (dup:Component)-[r:MERGED_INTO {phase:'$tag'}]->(primary:Component)
WHERE NOT dup:DuplicateOf
SET dup:DuplicateOf,
    dup.merged_phase = '$tag',
    dup.merged_into_uid = primary.value
RETURN count(dup) AS newly_labeled;
"@
Cy $q

"---- 40b. Mirror RELEASES_STRUCT edges from new dupes to their primaries ----"
$q = @"
MATCH (f:Form1)-[r_dup:RELEASES_STRUCT]->(dup:Component:DuplicateOf {merged_phase:'$tag'})
MATCH (dup)-[:MERGED_INTO]->(primary:Component)
WHERE NOT EXISTS { (f)-[:RELEASES_STRUCT]->(primary) }
MERGE (f)-[r_pri:RELEASES_STRUCT]->(primary)
ON CREATE SET r_pri.source            = r_dup.source,
              r_pri.confidence        = r_dup.confidence,
              r_pri.disposition       = r_dup.disposition,
              r_pri.form_tracking_no  = r_dup.form_tracking_no,
              r_pri.block             = r_dup.block,
              r_pri.phase             = r_dup.phase,
              r_pri.rerouted_from     = dup.value
RETURN count(DISTINCT r_pri) AS rerouted;
"@
Cy $q

"---- 40c. Flag the dup-target edges ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES_STRUCT]->(dup:Component:DuplicateOf {merged_phase:'$tag'})
WHERE r.target_is_merged IS NULL
SET r.target_is_merged = true,
    r.target_merged_into = dup.merged_into_uid
RETURN count(r) AS flagged;
"@
Cy $q
""

# ============================================================================
# 41. Verification
# ============================================================================
"==== Verification ===="

"-- ITT valve case after vendor-OEM merge --"
$q = @"
MATCH (f:Form1 {value:'form1::BV4R090M'})-[r:RELEASES_STRUCT]->(c:Component)
WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
RETURN pn.value AS primary_pn, sn.value AS sn, c.value AS effective_component
ORDER BY primary_pn;
"@
Cy $q

"-- ITT valve PN alias chain (AV16B2177-3 vs 601-62900-7) --"
$q = @"
MATCH (a:PartNumber {value:'AV16B2177-3'})
OPTIONAL MATCH (a)-[r:ALIAS_OF]-(b:PartNumber {value:'601-62900-7'})
RETURN a.value, b.value, r.kind, r.confidence, r.via_form1, r.via_sn;
"@
Cy $q

"-- Overall vendor-OEM aliases created --"
$q = @"
MATCH ()-[r:ALIAS_OF {kind:'vendor_oem_form1_signal'}]->()
RETURN count(r) AS n;
"@
Cy $q

"-- Total MERGED_INTO + total effective Components --"
$q = @"
MATCH ()-[r:MERGED_INTO {phase:'$tag'}]->() WITH count(r) AS merged_total
MATCH (c:Component) WHERE NOT c:Quarantined AND NOT EXISTS { (c)-[:MERGED_INTO]->() }
RETURN merged_total, count(c) AS effective_components;
"@
Cy $q

"-- NiCad case still correct (regression check) --"
$q = @"
MATCH (f:Form1 {value:'form1::c608f9ce-aade-468c-9e46-1a5a5686899f'})-[r:RELEASES_STRUCT]->(c:Component)
WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
RETURN pn.value AS primary_pn, sn.value AS sn, c.value AS effective_component;
"@
Cy $q

"-- Bombardier B0462730 still 3 (regression check) --"
$q = @"
MATCH (f:Form1 {value:'form1::B0462730 / 12 - 58'})-[r:RELEASES_STRUCT]->(c:Component)
WHERE NOT EXISTS { (c)-[:MERGED_INTO]->() }
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
RETURN pn.value AS primary_pn, sn.value AS sn ORDER BY sn;
"@
Cy $q

"==== part7 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
