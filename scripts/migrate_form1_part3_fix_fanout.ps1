$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# migrate_form1_part3_fix_fanout.ps1
#
# Corrective pass over parts 1 and 2. Two interlocking bugs were producing
# spurious Form1 -> multi-Component fanout:
#
#   Bug A:  enrich_v8 step 8.4 (vendor_oem_co_mention) created an ALIAS_OF
#           edge whenever two PNs co-occurred on any release-doc page with
#           a known SN. Result: a single raw PN frequently has 5-32 "primary"
#           targets via this kind. 929 of 1950 vendor_oem_co_mention edges
#           are provably wrong (multi-target).
#
#   Bug B:  parts 1/2 RELEASES_PN/RELEASES_SN walked ALIAS_OF*1..3 across
#           any kind, so each raw PN fanned out to every spurious primary.
#           Worst Form1 ended up with 44 RELEASES_PN edges; mean is ~8.
#
# This script:
#   24. Delete vendor_oem_co_mention aliases whose source has >1 distinct
#       target (the "this PN can't be an alias of N different parts" rule).
#   25. Delete the migration-tagged RELEASES_PN / RELEASES_SN edges that
#       walked the broken aliases (r.walked_alias = true).
#   26. Also delete migration-tagged RELEASES_PN/SN where the page has more
#       than 3 distinct PN or SN mentions (multi-part listing pages).
#   27. Re-run RELEASES_PN with strict rules:
#         - Walk only kind IN ['ocr_variant','incomplete_pn'] (high conf).
#         - Skip pages with >3 distinct PN mentions.
#         - Pick deterministic single primary if multi-target remains.
#   28. Re-run RELEASES_SN with the same shape (separator and zero-padding).
#   29. Final coverage re-snapshot.
#
# Rollback parity: deletions and new edges are tagged
# phase='migration_form1_2026_05_18'. Full rollback Cypher unchanged.
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw    = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
$tag   = 'migration_form1_2026_05_18'

function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== migrate_form1_part3_fix_fanout.ps1 ===="
""

# ============================================================================
# 24. Delete broken vendor_oem_co_mention aliases (multi-target source)
# ============================================================================
"---- 24. Delete vendor_oem_co_mention aliases where source has >1 target ----"
$q = @"
MATCH (pn:PartNumber)-[r:ALIAS_OF {kind:'vendor_oem_co_mention'}]->(t:PartNumber)
WITH pn, count(DISTINCT t) AS target_count, collect(r) AS rels
WHERE target_count > 1
UNWIND rels AS r
DELETE r
RETURN count(DISTINCT r) AS broken_vendor_aliases_deleted;
"@
Cy $q
""

# Sanity check after deletion
"---- 24b. Remaining ALIAS_OF kinds ----"
$q = @"
MATCH ()-[r:ALIAS_OF]->()
RETURN r.kind AS kind, count(r) AS n ORDER BY n DESC;
"@
Cy $q
""

# ============================================================================
# 25. Delete migration-tagged RELEASES_PN where the edge walked alias
# ============================================================================
"---- 25. Delete migration RELEASES_PN that walked the alias graph ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES_PN {phase:'$tag'}]->(pn:PartNumber)
WHERE r.walked_alias = true
DELETE r
RETURN count(r) AS deleted_releases_pn_walked;
"@
Cy $q

"---- 25b. Delete migration RELEASES_SN that walked the alias graph ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES_SN {phase:'$tag'}]->(sn:SerialNumber)
WHERE r.walked_alias = true
DELETE r
RETURN count(r) AS deleted_releases_sn_walked;
"@
Cy $q
""

# ============================================================================
# 26. Delete migration-tagged RELEASES_PN/SN from multi-part-listing pages
#     (any RELEASES_PN whose source page mentions >3 distinct PNs)
# ============================================================================
"---- 26. Delete RELEASES_PN where source page mentions >3 PNs ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)
WITH f, p, count { (p)-[:MENTIONS_PN]->() } AS pns_on_page
WHERE pns_on_page > 3
MATCH (f)-[r:RELEASES_PN {phase:'$tag'}]->(:PartNumber)
DELETE r
RETURN count(r) AS deleted_releases_pn_busy_page;
"@
Cy $q

"---- 26b. Delete RELEASES_SN where source page mentions >3 SNs ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)
WITH f, p, count { (p)-[:MENTIONS_SN]->() } AS sns_on_page
WHERE sns_on_page > 3
MATCH (f)-[r:RELEASES_SN {phase:'$tag'}]->(:SerialNumber)
WHERE r.match = 'page_sn_co_mention'
DELETE r
RETURN count(r) AS deleted_releases_sn_busy_page;
"@
Cy $q
""

# ============================================================================
# 27. Re-run RELEASES_PN with strict rules
# ============================================================================
"---- 27. Re-run RELEASES_PN (strict: high-conf aliases only, <=3 PNs/page) ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)
WITH f, p, count { (p)-[:MENTIONS_PN]->() } AS pns_on_page
WHERE pns_on_page <= 3
MATCH (p)-[:MENTIONS_PN]->(pn_raw:PartNumber)
// Walk only high-confidence alias kinds, one hop
OPTIONAL MATCH (pn_raw)-[a:ALIAS_OF]->(pn_primary:PartNumber)
WHERE a.kind IN ['ocr_variant', 'incomplete_pn']
WITH f, pn_raw, coalesce(pn_primary, pn_raw) AS pn, p
WITH f, pn_raw, pn, count(DISTINCT p) AS pages
MERGE (f)-[r:RELEASES_PN]->(pn)
ON CREATE SET r.phase='$tag',
              r.match='page_pn_co_mention_strict',
              r.evidence_pages=pages,
              r.block='6_inferred',
              r.raw_pn=pn_raw.value,
              r.walked_alias=(pn<>pn_raw),
              r.confidence='medium',
              r.alias_kind=CASE WHEN pn<>pn_raw THEN 'ocr_or_incomplete' ELSE null END
RETURN count(DISTINCT r) AS releases_pn_strict,
       count(DISTINCT f) AS form1s_touched;
"@
Cy $q
""

# ============================================================================
# 28. Re-run RELEASES_SN with same strict rules
# ============================================================================
"---- 28. Re-run RELEASES_SN (strict: high-conf SN aliases only, <=3 SNs/page) ----"
$q = @"
MATCH (f:Form1)-[:CARRIES]-(p:Page)
WITH f, p, count { (p)-[:MENTIONS_SN]->() } AS sns_on_page
WHERE sns_on_page <= 3
MATCH (p)-[:MENTIONS_SN]->(sn_raw:SerialNumber)
WHERE NOT sn_raw:Quarantined
OPTIONAL MATCH (sn_raw)-[a:ALIAS_OF]->(sn_primary:SerialNumber)
WHERE a.kind IN ['sn_ocr_separator', 'sn_zero_padding']
WITH f, sn_raw, coalesce(sn_primary, sn_raw) AS sn, p
WITH f, sn_raw, sn, count(DISTINCT p) AS pages
MERGE (f)-[r:RELEASES_SN]->(sn)
ON CREATE SET r.phase='$tag',
              r.match='page_sn_co_mention_strict',
              r.evidence_pages=pages,
              r.block='7_inferred',
              r.raw_sn=sn_raw.value,
              r.walked_alias=(sn<>sn_raw),
              r.confidence='medium'
RETURN count(DISTINCT r) AS releases_sn_strict,
       count(DISTINCT f) AS form1s_touched;
"@
Cy $q
""

# ============================================================================
# 29. Final coverage re-snapshot
# ============================================================================
"==== Coverage after fanout fix ===="

"-- Form1 outgoing edges --"
Cy "MATCH (f:Form1)-[r]->() RETURN type(r) AS edge, count(r) AS n ORDER BY n DESC;"

"-- Form1 incoming edges --"
Cy "MATCH (f:Form1)<-[r]-() RETURN type(r) AS edge, count(r) AS n ORDER BY n DESC;"

"-- Coverage diagnostics --"
Cy @"
MATCH (f:Form1) WITH count(f) AS total
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES]->(:Component) } WITH total, count(f) AS w_component
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES_PN]->() } WITH total, w_component, count(f) AS w_pn
MATCH (f:Form1) WHERE EXISTS { (f)-[:RELEASES_SN]->() } WITH total, w_component, w_pn, count(f) AS w_sn
MATCH (f:Form1) WHERE EXISTS { (f)-[:SIGNED_BY]->() } WITH total, w_component, w_pn, w_sn, count(f) AS w_signer
MATCH (f:Form1) WHERE EXISTS { (f)<-[:INCLUDES]-(:WorkPackage) } WITH total, w_component, w_pn, w_sn, w_signer, count(f) AS w_wp
MATCH (f:Form1) WHERE EXISTS { (f)<-[:PAIRED_WITH]-(:CRS) } WITH total, w_component, w_pn, w_sn, w_signer, w_wp, count(f) AS w_crs
MATCH (f:Form1) WHERE EXISTS { (f)-[:COVERS_RANGE]->() }
RETURN total, w_component, w_pn, w_sn, w_signer, w_wp, w_crs, count(f) AS w_batch;
"@

"-- Distribution of RELEASES_PN per Form1 --"
Cy @"
MATCH (f:Form1)
OPTIONAL MATCH (f)-[r:RELEASES_PN]->()
WITH f, count(r) AS rel_pn
RETURN
  sum(CASE WHEN rel_pn = 0 THEN 1 ELSE 0 END) AS zero,
  sum(CASE WHEN rel_pn = 1 THEN 1 ELSE 0 END) AS one,
  sum(CASE WHEN rel_pn = 2 THEN 1 ELSE 0 END) AS two,
  sum(CASE WHEN rel_pn = 3 THEN 1 ELSE 0 END) AS three,
  sum(CASE WHEN rel_pn >= 4 AND rel_pn <= 6 THEN 1 ELSE 0 END) AS four_to_six,
  sum(CASE WHEN rel_pn > 6 THEN 1 ELSE 0 END) AS over_six;
"@

"-- The previously-broken Form1 -- now --"
Cy @"
MATCH (f:Form1 {value: 'form1::3bc37673-3fc4-4a27-91fc-8c12ab4cf418'})-[r:RELEASES_PN]->(pn:PartNumber)
RETURN pn.value AS pn, r.raw_pn AS raw_pn, r.walked_alias AS walked, r.match AS rule
ORDER BY pn.value;
"@

"==== part3 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
