$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# migrate_form1_to_phase_pipeline.ps1
#
# ONE-TIME backfill for the existing CL650-6134 live graph. Applies the
# Form1-edges and SN-alias work that lives in the new phase pipeline
# (Phase 1 / 5 / 6 / 7) but was never run on this asset. Re-OCR is not
# attempted; edges are derived from page co-mentions, stamps, and the
# already-populated PN alias graph (v8).
#
# This script is DEPRECATED on first commit. Once a fresh Phase 1+ build of
# CL650-6134 lands, this script and its tagged edges are removed.
#
# All MERGEs carry phase='migration_form1_2026_05_18'. Rollback:
#   MATCH ()-[r {phase:'migration_form1_2026_05_18'}]-() DELETE r;
#   MATCH (n {migration_form1_2026_05_18:true})
#       REMOVE n:BatchInferred, n:AmbiguousSN, n.migration_form1_2026_05_18,
#              n.normalized_sn, n.normalized_pn;
#
# Sections:
#   1.  SN normalisation property (Phase 1 forward-port)
#   2.  PN normalisation property (Phase 1 forward-port)
#   3.  SN alias Tier 1 (separator-fold)
#   4.  SN alias Tier 3 (zero-padding numeric tail)
#   5.  Batch-bracket SN materialisation (SN-alias Tier 4)
#   6.  Form1 -> PartNumber  RELEASES_PN (page co-mention, alias-aware)
#   7.  Form1 -> SerialNumber RELEASES_SN (page co-mention, alias-aware)
#   8.  Form1 -> BatchNumber COVERS_RANGE
#   9.  Batch-cert RELEASES_SN fanout across sn_range
#  10.  WorkPackage -> Form1 INCLUDES (same-Document)
#  11.  Stamp -> Form1 BINDS_TO (single-Form1-on-page fallback)
#  12.  Form1 -> Person SIGNED_BY (via bound stamp)
#  13.  Form1 -> ServiceBulletin POST_SB_RELEASE (page co-mention + block_12)
#  14.  CRS <-> Form1 PAIRED_WITH (shared WP + Component)
#  15.  Disposition property on existing RELEASES edges (from block_11_status)
#  16.  Verification block
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw    = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
$tag   = 'migration_form1_2026_05_18'

function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== migrate_form1_to_phase_pipeline.ps1 ===="
"     Asset: CL650-6134   Neo4j: $neo4j   tag: $tag"
""

# Unicode hyphen literals (U+2010, U+2013, U+2014)
$h10 = [char]0x2010
$h13 = [char]0x2013
$h14 = [char]0x2014

# ============================================================================
# 1. SN normalisation property (Phase 1 forward-port)
# ============================================================================
"---- 1.  Compute SerialNumber.normalized ----"
$q = @"
MATCH (sn:SerialNumber)
WHERE sn.normalized IS NULL
WITH sn,
  toUpper(
    replace(
      replace(
        replace(
          replace(
            replace(
              replace(
                replace(sn.value, ' ', ''),
              '$h10', ''),
            '$h13', ''),
          '$h14', ''),
        '_', ''),
      '/', ''),
    '-', '')
  ) AS norm
SET sn.normalized = norm,
    sn.migration_form1_2026_05_18 = true
RETURN count(sn) AS sns_normalised;
"@
Cy $q
""

# ============================================================================
# 2. PN normalisation property
# ============================================================================
"---- 2.  Compute PartNumber.normalized ----"
$q = @"
MATCH (pn:PartNumber)
WHERE pn.normalized IS NULL
WITH pn,
  toUpper(
    replace(
      replace(
        replace(
          replace(
            replace(pn.value, ' ', ''),
          '$h10', '-'),
        '$h13', '-'),
      '$h14', '-'),
    '/', '-')
  ) AS norm
SET pn.normalized = norm,
    pn.migration_form1_2026_05_18 = true
RETURN count(pn) AS pns_normalised;
"@
Cy $q
""

# ============================================================================
# 3. SN alias Tier 1 - separator-fold (high confidence)
# ============================================================================
"---- 3.  SN alias Tier 1 (separator-fold, high) ----"
$q = @"
MATCH (sn:SerialNumber)
WHERE NOT sn:Quarantined
  AND sn.normalized IS NOT NULL
  AND size(sn.normalized) >= 3
WITH sn.normalized AS norm, collect(sn) AS sns
WHERE size(sns) > 1
UNWIND sns AS sn_x
OPTIONAL MATCH (sn_x)<-[r]-()
WITH norm, sns, sn_x, count(r) AS edges
ORDER BY norm, edges DESC
WITH norm, sns, collect(sn_x)[0] AS primary
UNWIND sns AS alias_sn
WITH primary, alias_sn WHERE alias_sn <> primary
  AND NOT (alias_sn)-[:ALIAS_OF]->(primary)
MERGE (alias_sn)-[r:ALIAS_OF]->(primary)
ON CREATE SET r.phase='$tag', r.kind='sn_ocr_separator', r.confidence='high'
RETURN count(r) AS sn_separator_aliases;
"@
Cy $q
""

# ============================================================================
# 4. SN alias Tier 3 - zero-padding (high confidence, fixed-format families)
# ============================================================================
"---- 4.  SN alias Tier 3 (zero-padding leading-zero, high) ----"
$q = @"
MATCH (sn_short:SerialNumber), (sn_long:SerialNumber)
WHERE sn_short <> sn_long
  AND NOT sn_short:Quarantined
  AND NOT sn_long:Quarantined
  AND sn_short.normalized IS NOT NULL
  AND sn_long.normalized IS NOT NULL
  AND sn_long.normalized =~ '0+\d+'
  AND sn_short.normalized = ltrim(sn_long.normalized, '0')
  AND size(sn_short.normalized) >= 2
  AND NOT (sn_short)-[:ALIAS_OF]-(sn_long)
WITH sn_short, sn_long
WHERE EXISTS {
  MATCH (sn_short)<-[:HAS_SN]-(c1:Component)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8]->(pn:PartNumber)
  MATCH (sn_long)<-[:HAS_SN]-(c2:Component)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8]->(pn)
}
MERGE (sn_long)-[r:ALIAS_OF]->(sn_short)
ON CREATE SET r.phase='$tag', r.kind='sn_zero_padding', r.confidence='high'
RETURN count(r) AS sn_zero_padding_aliases;
"@
# Note: ltrim with character set is non-standard Cypher; fall back to APOC or
# string manipulation. Test if it errors; if so use trimStart()/regex approach.
Cy $q 2>&1 | Tee-Object -Variable t4_out | Out-Null
$t4_out

# If the ltrim trick failed, run a simpler regex-based variant
if ($t4_out -match 'Invalid input|Unknown function') {
    "---- 4b. Retry with regex-based zero-padding match ----"
    $q = @"
MATCH (sn_long:SerialNumber)
WHERE NOT sn_long:Quarantined
  AND sn_long.normalized IS NOT NULL
  AND sn_long.normalized =~ '0+[1-9]\d*'
WITH sn_long, replace(sn_long.normalized, '0', '') AS attempt1
WITH sn_long, attempt1
WHERE size(attempt1) < size(sn_long.normalized)
MATCH (sn_short:SerialNumber)
WHERE sn_short <> sn_long
  AND NOT sn_short:Quarantined
  AND sn_short.normalized IS NOT NULL
  AND substring(sn_long.normalized, size(sn_long.normalized) - size(sn_short.normalized)) = sn_short.normalized
  AND left(sn_long.normalized, size(sn_long.normalized) - size(sn_short.normalized)) =~ '0+'
WITH sn_short, sn_long
WHERE EXISTS {
  MATCH (sn_short)<-[:HAS_SN]-(c1:Component)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8]->(pn:PartNumber)
  MATCH (sn_long)<-[:HAS_SN]-(c2:Component)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8]->(pn)
}
MERGE (sn_long)-[r:ALIAS_OF]->(sn_short)
ON CREATE SET r.phase='$tag', r.kind='sn_zero_padding', r.confidence='high'
RETURN count(r) AS sn_zero_padding_aliases;
"@
    Cy $q
}
""

# ============================================================================
# 5. Batch-bracket SN materialisation (SN-alias Tier 4)
# ============================================================================
"---- 5.  Materialise BatchInferred SerialNumbers from sn_range_* ----"
$q = @"
MATCH (bn:BatchNumber)
WHERE bn.sn_range_start IS NOT NULL
  AND bn.sn_range_end IS NOT NULL
  AND bn.sn_range_start =~ '\d+'
  AND bn.sn_range_end =~ '\d+'
  AND toInteger(bn.sn_range_end) >= toInteger(bn.sn_range_start)
  AND toInteger(bn.sn_range_end) - toInteger(bn.sn_range_start) <= 1000
WITH bn,
     toInteger(bn.sn_range_start) AS start_i,
     toInteger(bn.sn_range_end) AS end_i,
     size(bn.sn_range_start) AS pad
UNWIND range(start_i, end_i) AS n
WITH bn, toString(n) AS bare, pad
WITH bn, CASE WHEN size(bare) < pad
              THEN substring('0000000000', 0, pad - size(bare)) + bare
              ELSE bare END AS sn_value, bare AS unpadded
WITH bn, sn_value, unpadded
MERGE (sn:SerialNumber {asset_id: bn.asset_id, value: sn_value})
ON CREATE SET sn:BatchInferred,
              sn.normalized = sn_value,
              sn.batch_value = bn.value,
              sn.phase = '$tag',
              sn.migration_form1_2026_05_18 = true
RETURN bn.value AS batch, count(sn) AS sns_touched;
"@
Cy $q
""

# ============================================================================
# 6. Form1 -> PartNumber  RELEASES_PN (page co-mention, alias-aware)
# ============================================================================
"---- 6.  RELEASES_PN via Page co-mention (walks ALIAS_OF) ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)-[:MENTIONS_PN]->(pn_raw:PartNumber)
OPTIONAL MATCH (pn_raw)-[:ALIAS_OF*1..3]->(pn_primary:PartNumber)
WITH f, pn_raw, coalesce(pn_primary, pn_raw) AS pn, count(DISTINCT p) AS pages
MERGE (f)-[r:RELEASES_PN]->(pn)
ON CREATE SET r.phase='$tag',
              r.match='page_pn_co_mention',
              r.evidence_pages=pages,
              r.block='6_inferred',
              r.raw_pn=pn_raw.value,
              r.walked_alias=(pn<>pn_raw),
              r.confidence='medium'
RETURN count(DISTINCT r) AS releases_pn_edges,
       count(DISTINCT f) AS form1s_touched;
"@
Cy $q
""

# ============================================================================
# 7. Form1 -> SerialNumber RELEASES_SN (page co-mention, alias-aware)
# ============================================================================
"---- 7.  RELEASES_SN via Page co-mention (walks ALIAS_OF) ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)-[:MENTIONS_SN]->(sn_raw:SerialNumber)
WHERE NOT sn_raw:Quarantined
OPTIONAL MATCH (sn_raw)-[:ALIAS_OF*1..3]->(sn_primary:SerialNumber)
WITH f, sn_raw, coalesce(sn_primary, sn_raw) AS sn, count(DISTINCT p) AS pages
MERGE (f)-[r:RELEASES_SN]->(sn)
ON CREATE SET r.phase='$tag',
              r.match='page_sn_co_mention',
              r.evidence_pages=pages,
              r.block='7_inferred',
              r.raw_sn=sn_raw.value,
              r.walked_alias=(sn<>sn_raw),
              r.confidence='medium'
RETURN count(DISTINCT r) AS releases_sn_edges,
       count(DISTINCT f) AS form1s_touched;
"@
Cy $q
""

# ============================================================================
# 8. Form1 -> BatchNumber COVERS_RANGE
# ============================================================================
"---- 8.  COVERS_RANGE via Page co-mention ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)-[:MENTIONS_BATCH]->(bn:BatchNumber)
WITH f, bn, count(DISTINCT p) AS pages
MERGE (f)-[r:COVERS_RANGE]->(bn)
ON CREATE SET r.phase='$tag',
              r.match='page_batch_co_mention',
              r.evidence_pages=pages,
              r.sn_range_start=bn.sn_range_start,
              r.sn_range_end=bn.sn_range_end
RETURN count(DISTINCT r) AS covers_range_edges;
"@
Cy $q
""

# ============================================================================
# 9. Batch-cert RELEASES_SN fanout (across the materialised SN range)
# ============================================================================
"---- 9.  Batch-cert RELEASES_SN fanout ----"
$q = @"
MATCH (f:Form1)-[:COVERS_RANGE]->(bn:BatchNumber)
WHERE bn.sn_range_start IS NOT NULL
  AND bn.sn_range_end IS NOT NULL
  AND bn.sn_range_start =~ '\d+'
  AND bn.sn_range_end =~ '\d+'
MATCH (sn:SerialNumber {asset_id: f.asset_id})
WHERE NOT sn:Quarantined
  AND sn.value =~ '\d+'
  AND toInteger(sn.value) >= toInteger(bn.sn_range_start)
  AND toInteger(sn.value) <= toInteger(bn.sn_range_end)
MERGE (f)-[r:RELEASES_SN]->(sn)
ON CREATE SET r.phase='$tag',
              r.match='batch_range_expansion',
              r.batch=bn.value,
              r.confidence='high',
              r.block='7_batch'
RETURN count(DISTINCT r) AS releases_sn_from_batch;
"@
Cy $q
""

# ============================================================================
# 10. WorkPackage -> Form1 INCLUDES (same-Document)
# ============================================================================
"---- 10. WorkPackage-[:INCLUDES]->Form1 (same-Document) ----"
$q = @"
MATCH (wp:WorkPackage)<-[:CARRIES]-(p1:Page)<-[:HAS_PAGE]-(d:Document)
MATCH (d)-[:HAS_PAGE]->(p2:Page)-[:CARRIES]->(f:Form1)
WHERE f.value <> wp.value AND f.asset_id = wp.asset_id
MERGE (wp)-[r:INCLUDES]->(f)
ON CREATE SET r.phase='$tag', r.match='same_document'
RETURN count(DISTINCT r) AS wp_includes_form1;
"@
Cy $q
""

# ============================================================================
# 11. Stamp -> Form1 BINDS_TO (single-Form1-on-page fallback)
# ============================================================================
"---- 11. Stamp-[:BINDS_TO]->Form1 (single-Form1-on-page) ----"
$q = @"
MATCH (s:Stamp)<-[:HAS_STAMP]-(p:Page)-[:CARRIES]->(f:Form1)
WHERE coalesce(s.binding_status, 'unbound') IN ['unbound','ambiguous']
  AND NOT EXISTS { (s)-[:BINDS_TO]->() }
WITH p, s, collect(DISTINCT f) AS forms_on_page
WHERE size(forms_on_page) = 1
WITH s, forms_on_page[0] AS f
MERGE (s)-[r:BINDS_TO]->(f)
ON CREATE SET r.phase='$tag',
              r.confidence='medium',
              r.rule='single_form1_on_page'
RETURN count(DISTINCT r) AS stamp_form1_bindings;
"@
Cy $q
""

# ============================================================================
# 12. Form1 -> Person SIGNED_BY (via bound stamp -> stamped person)
# ============================================================================
"---- 12. Form1-[:SIGNED_BY]->Person via Stamp ----"
$q = @"
MATCH (s:Stamp)-[:BINDS_TO]->(f:Form1)
MATCH (s)-[:STAMPED_BY]->(person:Person)
WITH f, person, s
ORDER BY f.value, person.value, s.date DESC
WITH f, person, collect(s)[0] AS s_pick
MERGE (f)-[r:SIGNED_BY]->(person)
ON CREATE SET r.phase='$tag',
              r.block='13b_inferred',
              r.confidence=coalesce(s_pick.binding_status, 'medium'),
              r.rule='stamp_binds_to_form1',
              r.date=s_pick.date
RETURN count(DISTINCT r) AS form1_signed_by_edges;
"@
Cy $q
""

# ============================================================================
# 13. Form1 -> ServiceBulletin POST_SB_RELEASE (page co-mention + block_12 hint)
# ============================================================================
"---- 13. Form1-[:POST_SB_RELEASE]->ServiceBulletin ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)-[:MENTIONS_SB]->(sb:ServiceBulletin)
WHERE (f.block_12_text IS NULL OR toLower(coalesce(f.block_12_text,'')) CONTAINS toLower(sb.value))
   OR EXISTS { (p)-[:CARRIES]->(:Modification) }
MERGE (f)-[r:POST_SB_RELEASE]->(sb)
ON CREATE SET r.phase='$tag',
              r.match='page_sb_co_mention',
              r.confidence='medium',
              r.source='page_co_mention'
RETURN count(DISTINCT r) AS post_sb_release_edges;
"@
Cy $q
""

# ============================================================================
# 14. CRS <-> Form1 PAIRED_WITH (shared WorkPackage + shared Component)
# ============================================================================
"---- 14. CRS-[:PAIRED_WITH]->Form1 (shared WP + Component) ----"
$q = @"
MATCH (wp:WorkPackage)-[:INCLUDES]->(f:Form1)-[:RELEASES]->(c:Component)
MATCH (wp)-[:INCLUDES]->(crs:CRS)
WHERE EXISTS { (crs)-[:RELEASES_WORK_ON]->(c) } OR EXISTS { (crs)-[:CERTIFIES]->(c) }
MERGE (crs)-[r:PAIRED_WITH]->(f)
ON CREATE SET r.phase='$tag',
              r.via='work_package_and_component',
              r.confidence='high'
RETURN count(DISTINCT r) AS crs_paired_with_form1;
"@
Cy $q

"---- 14b. CRS-[:PAIRED_WITH]->Form1 fallback (shared WP only) ----"
$q = @"
MATCH (wp:WorkPackage)-[:INCLUDES]->(f:Form1)
MATCH (wp)-[:INCLUDES]->(crs:CRS)
WHERE NOT EXISTS { (crs)-[:PAIRED_WITH]->(f) }
MERGE (crs)-[r:PAIRED_WITH]->(f)
ON CREATE SET r.phase='$tag',
              r.via='work_package_only',
              r.confidence='medium'
RETURN count(DISTINCT r) AS crs_paired_with_form1_wp_only;
"@
Cy $q
""

# ============================================================================
# 15. Disposition property on RELEASES edges (from block_11_status)
# ============================================================================
"---- 15. Disposition on RELEASES (from block_11_status) ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->(c:Component)
WHERE f.block_11_status IS NOT NULL
  AND r.disposition IS NULL
WITH r, toLower(f.block_11_status) AS s
SET r.disposition = CASE
  WHEN s CONTAINS 'overhaul'    THEN 'overhauled'
  WHEN s CONTAINS 'repair'      THEN 'repaired'
  WHEN s CONTAINS 'inspect'     THEN 'inspected'
  WHEN s CONTAINS 'as-removed'  THEN 'as_removed'
  WHEN s CONTAINS 'as removed'  THEN 'as_removed'
  WHEN s CONTAINS 'serviceable' THEN 'new'
  WHEN s CONTAINS 'new'         THEN 'new'
  ELSE 'unknown'
END,
r.disposition_phase = '$tag'
RETURN count(r) AS releases_dispositioned;
"@
Cy $q
""

# ============================================================================
# 16. Verification block
# ============================================================================
"==== Verification ===="

"-- Form1 outgoing edge type counts --"
$q = @"
MATCH (f:Form1)-[r]->()
RETURN type(r) AS edge, count(r) AS n
ORDER BY n DESC;
"@
Cy $q

"-- Form1 incoming edge type counts --"
$q = @"
MATCH (f:Form1)<-[r]-()
RETURN type(r) AS edge, count(r) AS n
ORDER BY n DESC;
"@
Cy $q

"-- Coverage diagnostics --"
$q = @"
MATCH (f:Form1) WITH count(f) AS total_form1
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES]->(:Component) }
WITH total_form1, count(f) AS has_component
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES_PN]->() }
WITH total_form1, has_component, count(f) AS has_pn
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES_SN]->() }
WITH total_form1, has_component, has_pn, count(f) AS has_sn
MATCH (f:Form1) WHERE EXISTS { (f)-[:SIGNED_BY]->(:Person) }
WITH total_form1, has_component, has_pn, has_sn, count(f) AS has_signer
MATCH (f:Form1) WHERE EXISTS { (f)<-[:INCLUDES]-(:WorkPackage) }
WITH total_form1, has_component, has_pn, has_sn, has_signer, count(f) AS has_wp
MATCH (f:Form1) WHERE EXISTS { (f)<-[:PAIRED_WITH]-(:CRS) }
WITH total_form1, has_component, has_pn, has_sn, has_signer, has_wp, count(f) AS has_crs
MATCH (f:Form1) WHERE EXISTS { (f)-[:POST_SB_RELEASE]->() }
WITH total_form1, has_component, has_pn, has_sn, has_signer, has_wp, has_crs, count(f) AS has_sb
MATCH (f:Form1) WHERE EXISTS { (f)-[:COVERS_RANGE]->() }
RETURN total_form1, has_component, has_pn, has_sn, has_signer, has_wp, has_crs, has_sb, count(f) AS has_batch;
"@
Cy $q

"-- Lukas worked-example assertions --"
"(c) MAY18-4688 wheel disposition (expect REPAIRED):"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->(c:Component)
MATCH (c)-[:HAS_SN]->(:SerialNumber {value:'MAY18-4688'})
RETURN f.value AS form1, f.block_11_status AS block_11, r.disposition AS disposition
LIMIT 5;
"@
Cy $q

"(α) PN vendor alias 604-85001-28 vs 19090-110:"
$q = @"
MATCH (pn:PartNumber) WHERE pn.value IN ['604-85001-28','19090-110']
OPTIONAL MATCH (pn)-[a:ALIAS_OF]-(:PartNumber)
OPTIONAL MATCH (pn)<-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8|HAS_ALIAS_PN]-(c:Component)
RETURN pn.value AS pn, count(DISTINCT a) AS alias_edges, count(DISTINCT c) AS components
ORDER BY pn;
"@
Cy $q

"(d) FCC 4FM4CT born-on-aircraft - should have NO migration-tagged RELEASES_SN:"
$q = @"
MATCH (sn:SerialNumber {value:'4FM4CT'})<-[r:RELEASES_SN]-(f:Form1)
WHERE r.phase = '$tag'
RETURN count(*) AS spurious_migration_edges, collect(f.value)[0..3] AS sample_forms;
"@
Cy $q

"==== migration complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
