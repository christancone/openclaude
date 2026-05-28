$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# migrate_form1_part2.ps1
#
# Follow-up to migrate_form1_to_phase_pipeline.ps1. Fixes the three sections
# that came back 0 on the first run:
#
#   17. Widen Stamp -> Form1 BINDS_TO to include stamps WITH person_name
#       (was filtered out by binding_status='bound' check)
#   18. Wire WorkPackage -> CRS INCLUDES (same-Document pattern)
#   19. Re-run CRS <-> Form1 PAIRED_WITH (now that WP->CRS exists)
#   20. Re-run Form1 -> Person SIGNED_BY via newly-bound stamps
#   21. Best-effort BatchNumber sn_range extraction from page text
#       (regex over `S/N S: \d+, ..., \d+` Bombardier-style remarks)
#   22. Re-run batch SN materialisation + Form1 RELEASES_SN fanout
#   23. Final verification re-snapshot
#
# All MERGEs carry phase='migration_form1_2026_05_18' for parity with part 1.
# Same rollback applies.
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw    = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
$tag   = 'migration_form1_2026_05_18'

function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== migrate_form1_part2.ps1 ===="
""

# ============================================================================
# 17. Widen Stamp -> Form1 BINDS_TO (stamps with person_name, single Form1)
# ============================================================================
"---- 17. Widen BINDS_TO to stamps with person_name (single Form1 on page) ----"
$q = @"
MATCH (s:Stamp)<-[:HAS_STAMP]-(p:Page)-[:CARRIES]->(f:Form1)
WHERE s.person_name IS NOT NULL
  AND NOT EXISTS { (s)-[:BINDS_TO]->() }
WITH p, s, collect(DISTINCT f) AS forms_on_page
WHERE size(forms_on_page) = 1
WITH s, forms_on_page[0] AS f
MERGE (s)-[r:BINDS_TO]->(f)
ON CREATE SET r.phase='$tag',
              r.confidence='medium',
              r.rule='single_form1_on_page_with_person'
RETURN count(DISTINCT r) AS new_stamp_form1_bindings;
"@
Cy $q
""

# ============================================================================
# 18. WorkPackage -> CRS INCLUDES (same-Document pattern)
# ============================================================================
"---- 18. WorkPackage-[:INCLUDES]->CRS (same-Document) ----"
$q = @"
MATCH (wp:WorkPackage)<-[:CARRIES]-(p1:Page)<-[:HAS_PAGE]-(d:Document)
MATCH (d)-[:HAS_PAGE]->(p2:Page)-[:CARRIES]->(crs:CRS)
WHERE crs.value <> wp.value AND crs.asset_id = wp.asset_id
MERGE (wp)-[r:INCLUDES]->(crs)
ON CREATE SET r.phase='$tag', r.match='same_document'
RETURN count(DISTINCT r) AS wp_includes_crs;
"@
Cy $q
""

# ============================================================================
# 19. Re-run CRS <-> Form1 PAIRED_WITH
# ============================================================================
"---- 19. CRS-[:PAIRED_WITH]->Form1 (shared WP + Component) ----"
$q = @"
MATCH (wp:WorkPackage)-[:INCLUDES]->(f:Form1)-[:RELEASES]->(c:Component)
MATCH (wp)-[:INCLUDES]->(crs:CRS)
WHERE (
  EXISTS { (crs)-[:RELEASES_WORK_ON]->(c) }
  OR EXISTS { (crs)-[:CERTIFIES]->(c) }
)
AND NOT EXISTS { (crs)-[:PAIRED_WITH]->(f) }
MERGE (crs)-[r:PAIRED_WITH]->(f)
ON CREATE SET r.phase='$tag',
              r.via='work_package_and_component',
              r.confidence='high'
RETURN count(DISTINCT r) AS crs_paired_with_form1_strong;
"@
Cy $q

"---- 19b. CRS-[:PAIRED_WITH]->Form1 fallback (shared WP only) ----"
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
# 20. Re-run Form1 -> Person SIGNED_BY via newly-bound stamps
# ============================================================================
"---- 20. Form1-[:SIGNED_BY]->Person via newly bound stamps ----"
$q = @"
MATCH (s:Stamp)-[:BINDS_TO]->(f:Form1)
WHERE s.person_name IS NOT NULL
MATCH (s)-[:STAMPED_BY]->(person:Person)
WITH f, person, s
ORDER BY f.value, person.value, s.date DESC
WITH f, person, collect(s)[0] AS s_pick
MERGE (f)-[r:SIGNED_BY]->(person)
ON CREATE SET r.phase='$tag',
              r.block='13b_inferred',
              r.confidence='medium',
              r.rule='stamp_binds_to_form1',
              r.date=s_pick.date
RETURN count(DISTINCT r) AS form1_signed_by_edges,
       count(DISTINCT f) AS form1s_signed;
"@
Cy $q
""

# ============================================================================
# 21. BatchNumber sn_range extraction from page text (Bombardier style)
#
# Pattern: page text contains `S/N S: 3983, 3984, ..., 4000` or
#          `S/N: 3983 thru 4000` or `Serial Range: 3983-4000`
#
# We attempt three regex shapes; in each, the first integer is the start and
# the last is the end. Stored as strings (Phase 1 contract) but parseable.
# ============================================================================
"---- 21a. Extract sn_range from page text - 'thru' / 'through' / hyphen pattern ----"
$q = @"
MATCH (p:Page)-[:MENTIONS_BATCH]->(bn:BatchNumber)
WHERE bn.sn_range_start IS NULL
  AND p.text IS NOT NULL
WITH bn, p
WHERE p.text =~ '(?si).*(?:S/N|Serial(?: Number)?|SN)(?:\s+S?:?\s*|\s+Range\s*:?\s*)\d+\s*(?:thru|through|to|-|–|—)\s*\d+.*'
WITH bn, p,
     [w IN split(p.text, ' ') WHERE w =~ '\d{3,}'] AS nums
WITH bn, p, nums
WHERE size(nums) >= 2
WITH bn, head(nums) AS start_v, last(nums) AS end_v
WHERE start_v <= end_v
  AND toInteger(end_v) - toInteger(start_v) > 0
  AND toInteger(end_v) - toInteger(start_v) <= 1000
SET bn.sn_range_start = start_v,
    bn.sn_range_end = end_v,
    bn.sn_range_source = 'page_text_thru_pattern',
    bn.migration_form1_2026_05_18 = true
RETURN count(DISTINCT bn) AS batch_ranges_extracted_thru;
"@
Cy $q

"---- 21b. Extract sn_range from comma-listed SNs (Bombardier TCCA pattern) ----"
$q = @"
MATCH (p:Page)-[:MENTIONS_BATCH]->(bn:BatchNumber)
WHERE bn.sn_range_start IS NULL
  AND p.text IS NOT NULL
WITH bn, p
WHERE p.text =~ '(?si).*(?:S/N|Serial(?: Number)?|SN)\s+S?:?\s*\d+\s*,\s*\d+\s*,.*'
WITH bn, p,
     [m IN [s IN split(p.text, ',') | trim(s)] WHERE m =~ '\s*\d{3,}\s*'] AS candidate_nums
WHERE size(candidate_nums) >= 3
WITH bn, candidate_nums, [m IN candidate_nums | trim(m)] AS clean
WITH bn, [c IN clean WHERE c =~ '\d+'] AS nums
WHERE size(nums) >= 3
WITH bn, nums,
     reduce(mn = toInteger(head(nums)), x IN nums | CASE WHEN toInteger(x) < mn THEN toInteger(x) ELSE mn END) AS min_v,
     reduce(mx = toInteger(head(nums)), x IN nums | CASE WHEN toInteger(x) > mx THEN toInteger(x) ELSE mx END) AS max_v
WHERE max_v - min_v > 0 AND max_v - min_v <= 200
SET bn.sn_range_start = toString(min_v),
    bn.sn_range_end = toString(max_v),
    bn.sn_range_source = 'page_text_comma_list',
    bn.migration_form1_2026_05_18 = true
RETURN count(DISTINCT bn) AS batch_ranges_extracted_comma;
"@
Cy $q
""

# ============================================================================
# 22. Re-run batch SN materialisation + RELEASES_SN fanout
# ============================================================================
"---- 22a. Materialise BatchInferred SerialNumbers (re-run) ----"
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
              ELSE bare END AS sn_value
MERGE (sn:SerialNumber {asset_id: bn.asset_id, value: sn_value})
ON CREATE SET sn:BatchInferred,
              sn.normalized = sn_value,
              sn.batch_value = bn.value,
              sn.phase = '$tag',
              sn.migration_form1_2026_05_18 = true
RETURN bn.value AS batch, count(sn) AS sns_touched
ORDER BY sns_touched DESC LIMIT 10;
"@
Cy $q

"---- 22b. Batch-cert RELEASES_SN fanout (re-run) ----"
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
# 23. Final re-snapshot
# ============================================================================
"==== Final Verification ===="

"-- Form1 outgoing edges --"
Cy "MATCH (f:Form1)-[r]->() RETURN type(r) AS edge, count(r) AS n ORDER BY n DESC;"

"-- Form1 incoming edges --"
Cy "MATCH (f:Form1)<-[r]-() RETURN type(r) AS edge, count(r) AS n ORDER BY n DESC;"

"-- Coverage diagnostics: which Form1 fields are reachable --"
Cy @"
MATCH (f:Form1) WITH count(f) AS total
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES]->(:Component) } WITH total, count(f) AS w_component
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES_PN]->() } WITH total, w_component, count(f) AS w_pn
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES_SN]->() } WITH total, w_component, w_pn, count(f) AS w_sn
MATCH (f:Form1) WHERE EXISTS { (f)-[:SIGNED_BY]->() } WITH total, w_component, w_pn, w_sn, count(f) AS w_signer
MATCH (f:Form1) WHERE EXISTS { (f)<-[:INCLUDES]-(:WorkPackage) } WITH total, w_component, w_pn, w_sn, w_signer, count(f) AS w_wp
MATCH (f:Form1) WHERE EXISTS { (f)<-[:PAIRED_WITH]-(:CRS) } WITH total, w_component, w_pn, w_sn, w_signer, w_wp, count(f) AS w_crs
MATCH (f:Form1) WHERE EXISTS { (f)-[:POST_SB_RELEASE]->() } WITH total, w_component, w_pn, w_sn, w_signer, w_wp, w_crs, count(f) AS w_sb
MATCH (f:Form1) WHERE EXISTS { (f)-[:COVERS_RANGE]->() }
RETURN total, w_component, w_pn, w_sn, w_signer, w_wp, w_crs, w_sb, count(f) AS w_batch;
"@

"-- BatchNumber range extraction --"
Cy "MATCH (bn:BatchNumber) RETURN count(bn) AS total, sum(CASE WHEN bn.sn_range_start IS NOT NULL THEN 1 ELSE 0 END) AS with_range, sum(CASE WHEN bn.sn_range_source IS NOT NULL THEN 1 ELSE 0 END) AS migrated_range;"

"-- BatchInferred SNs --"
Cy "MATCH (sn:SerialNumber:BatchInferred) RETURN count(sn) AS batch_inferred_sns;"

"-- Total Form1 edges (forward and backward) --"
Cy "MATCH (f:Form1) WITH count(f) AS form1_count MATCH (f:Form1)-[r]-() WITH form1_count, type(r) AS edge, count(r) AS n RETURN form1_count, edge, n ORDER BY n DESC;"

"==== part2 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
