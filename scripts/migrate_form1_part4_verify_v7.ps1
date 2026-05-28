$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# migrate_form1_part4_verify_v7.ps1
#
# Tag every v7 RELEASES edge (Form1 -> Component) with pn_verified status by
# cross-checking that the carrier-page's MENTIONS_PN list overlaps with the
# Component's HAS_PRIMARY_PN / HAS_PRIMARY_PN_V8 / HAS_ALIAS_PN. The v7 rule
# only matched on SN, so when an SN is reused across multiple PN families
# (e.g. SN 4005 across window-shade PN AND001/AQM001/ARM001/etc.), every
# Component carrying that SN got linked - even though the Form 1 actually
# lists only one PN.
#
# Confirmed instance: form1::B0462730 / 12 - 58 (Bombardier TCCA, 2019-07-01).
# CSV ground truth: it releases exactly 3 components, all PN 604DX2529029AND001,
# SN 4005/4006/4007. The graph has 20 RELEASES edges (17 false positives).
#
# This script does NOT delete edges - it tags them so consumers/UIs can
# filter. Three values for r.pn_verified:
#   'pn_match'           - page mentions a PN that resolves to the Component
#                          (via direct match or high-confidence alias).
#   'pn_mismatch'        - page mentions PNs, none match the Component.
#   'no_pn_on_page'      - page mentions no PNs at all (cannot verify either way).
#
# Visualization filter: include `r.pn_verified IN ['pn_match','no_pn_on_page']`
# to hide the 17 false positives but keep the 3 correct ones plus PN-less
# pages where we can't say.
#
# Sections:
#   30. Tag v7 RELEASES edges with pn_verified
#   31. Distribution + the B0462730 case before/after
#   32. Tag the migration-tagged RELEASES_PN/RELEASES_SN similarly (sanity)
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw    = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
$tag   = 'migration_form1_2026_05_18'

function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== migrate_form1_part4_verify_v7.ps1 ===="
""

# ============================================================================
# 30. Tag v7 RELEASES edges with pn_verified
# ============================================================================
"---- 30a. Tag v7 RELEASES with no_pn_on_page (page mentions zero PNs) ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->(c:Component)
WHERE r.phase = 'v7' AND r.match = 'page_sn_co_mention' AND r.pn_verified IS NULL
MATCH (f)-[:CARRIES]-(p:Page)
WHERE NOT EXISTS { (p)-[:MENTIONS_PN]->() }
SET r.pn_verified = 'no_pn_on_page',
    r.pn_verified_phase = '$tag'
RETURN count(r) AS no_pn_on_page;
"@
Cy $q

"---- 30b. Tag v7 RELEASES with pn_match (page+component share a PN, direct or via high-conf alias) ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->(c:Component)
WHERE r.phase = 'v7' AND r.match = 'page_sn_co_mention' AND r.pn_verified IS NULL
MATCH (f)-[:CARRIES]-(p:Page)-[:MENTIONS_PN]->(pn_page:PartNumber)
WHERE EXISTS {
  MATCH (c)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8|HAS_ALIAS_PN]->(pn_c:PartNumber)
  WHERE pn_c = pn_page
     OR EXISTS { (pn_page)-[a:ALIAS_OF]->(pn_c) WHERE a.kind IN ['ocr_variant','incomplete_pn'] }
     OR EXISTS { (pn_c)-[a:ALIAS_OF]->(pn_page) WHERE a.kind IN ['ocr_variant','incomplete_pn'] }
}
WITH DISTINCT r
SET r.pn_verified = 'pn_match',
    r.pn_verified_phase = '$tag'
RETURN count(r) AS pn_match;
"@
Cy $q

"---- 30c. Tag remaining v7 RELEASES as pn_mismatch ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->(c:Component)
WHERE r.phase = 'v7' AND r.match = 'page_sn_co_mention' AND r.pn_verified IS NULL
SET r.pn_verified = 'pn_mismatch',
    r.pn_verified_phase = '$tag'
RETURN count(r) AS pn_mismatch;
"@
Cy $q
""

# ============================================================================
# 31. Distribution + the B0462730 case
# ============================================================================
"---- 31a. Overall pn_verified distribution ----"
$q = @"
MATCH ()-[r:RELEASES]->()
WHERE r.phase = 'v7' AND r.match = 'page_sn_co_mention'
RETURN coalesce(r.pn_verified,'untagged') AS pn_verified, count(r) AS n
ORDER BY n DESC;
"@
Cy $q

"---- 31b. The B0462730 case: before (all 20) vs verified (should be 3) ----"
$q = @"
MATCH (f:Form1 {value:'form1::B0462730 / 12 - 58'})-[r:RELEASES]->(c:Component)
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
RETURN coalesce(r.pn_verified,'untagged') AS verified,
       pn.value AS pn, sn.value AS sn
ORDER BY verified, pn, sn;
"@
Cy $q

"---- 31c. The B0462730 case: only pn_match edges (should be 3) ----"
$q = @"
MATCH (f:Form1 {value:'form1::B0462730 / 12 - 58'})-[r:RELEASES {pn_verified:'pn_match'}]->(c:Component)
OPTIONAL MATCH (c)-[:HAS_PRIMARY_PN|HAS_PRIMARY_PN_V8]->(pn:PartNumber)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
RETURN pn.value AS pn, sn.value AS sn, c.value AS component
ORDER BY pn, sn;
"@
Cy $q
""

# ============================================================================
# 32. Per-Form1 rollup: how many of its v7 RELEASES edges are validated
# ============================================================================
"---- 32. Per-Form1 verified-edge counts ----"
$q = @"
MATCH (f:Form1)-[r:RELEASES]->()
WHERE r.phase = 'v7' AND r.match = 'page_sn_co_mention'
WITH f,
     sum(CASE WHEN r.pn_verified = 'pn_match' THEN 1 ELSE 0 END) AS verified,
     sum(CASE WHEN r.pn_verified = 'pn_mismatch' THEN 1 ELSE 0 END) AS mismatch,
     sum(CASE WHEN r.pn_verified = 'no_pn_on_page' THEN 1 ELSE 0 END) AS no_pn,
     count(r) AS total
RETURN
  count(f) AS form1s,
  sum(CASE WHEN verified > 0 AND mismatch = 0 THEN 1 ELSE 0 END) AS form1s_all_clean,
  sum(CASE WHEN verified > 0 AND mismatch > 0 THEN 1 ELSE 0 END) AS form1s_partly_clean,
  sum(CASE WHEN verified = 0 AND mismatch > 0 THEN 1 ELSE 0 END) AS form1s_only_mismatch,
  sum(CASE WHEN verified = 0 AND mismatch = 0 AND no_pn > 0 THEN 1 ELSE 0 END) AS form1s_only_no_pn,
  sum(total) AS total_v7_edges,
  sum(verified) AS total_verified,
  sum(mismatch) AS total_mismatch,
  sum(no_pn) AS total_no_pn;
"@
Cy $q

"==== part4 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
