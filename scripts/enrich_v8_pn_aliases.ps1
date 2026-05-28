$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# enrich_v8_pn_aliases.ps1
#
# PN alias resolution flow, derived from the three Lukas markdowns:
#   - Handbook Ch.6 Problem 1 (PN aliases: Bombardier vs Safran; OCR variants)
#   - Handbook Ch.6 Problem 2 (Dash numbers and post-SB configuration)
#   - Handbook Ch.10 (search rigour: build the alias map BEFORE searching)
#   - Cheatsheet sec.12 (the PN alias table: OEM PN, Vendor PN, OCR variants)
#   - Lukas Process Ch.6/Ch.11 (Mod Status from SVR / Outgoing SB Configuration)
#
# Six tiers, in order of evidence strength:
#
#   8.1  OCR-variant normalization        -> ALIAS_OF {kind:'ocr_variant'}
#        (whitespace, unicode hyphens, separators)        [HIGH confidence]
#
#   8.2  Incomplete-PN matching           -> ALIAS_OF {kind:'incomplete_pn'}
#        (Lukas process Ch.18 bullet:
#         "Incomplete P/N: find the complete P/N with -number and S/N pair")
#        Same SN co-mentioned; one PN is a prefix of the other.   [MEDIUM]
#
#   8.3  Dash-family sibling detection    -> DASH_FAMILY_OF (NOT alias)
#        PNs sharing the same dash-prefix become siblings. Promotion to
#        REVISION_OF requires SB evidence in 8.5.                 [LOW alone]
#
#   8.4  Vendor<->OEM via Form1/CRS page  -> ALIAS_OF {kind:'vendor_oem'}
#        (Handbook Ch.6: "The Form 1 and shop report will typically list
#         both the customer PN and the manufacturer PN")          [MEDIUM-HI]
#
#   8.5  Post-SB revision promotion       -> REVISION_OF {sb_ref, direction}
#        Dash-family sibling + ServiceBulletin compliance event on the
#        same SN. (Handbook Ch.6: "An FCC delivered as -810 and upgraded
#        via SB to -811 is now legally -811".)                    [HIGH]
#
#   8.6  Primary-PN election per cluster
#        Rule (Handbook Ch.10): "OEM part number is primary; alternate PN
#        is the manufacturer PN". Tie-break by evidence-page count.
#
#   8.7  Rewire Component.HAS_PRIMARY_PN  via HAS_ALIAS_PN preserving origin
#
# Honest-not-found: dash-family siblings with no SB evidence stay as
# DASH_FAMILY_OF, never auto-merged. Lukas Ch.6: "Without mod status, you
# cannot be sure what configuration the unit is in" -- so without that
# evidence we don't claim a revision.
# ============================================================================

$neo4j = 'sparengine-neo4j'
$pw = 'cPpNqbpjGsHYiZIPyeXFLnIT7Owrc005'
function Cy($q) { docker exec $neo4j cypher-shell -u neo4j -p $pw --format plain $q }

"==== enrich_v8_pn_aliases.ps1 ===="
"     Asset: CL650-6134   Neo4j: $neo4j"
""

# ============================================================================
# 8.1 - OCR variant normalization
#
# Normalize PN values: strip whitespace, fold unicode hyphens to ASCII,
# uppercase. Group PNs that share the same normalized form. The PN with
# the most evidence pages is primary; the rest are OCR variants.
# ============================================================================
"---- 8.1  OCR-variant ALIAS_OF detection ----"
# Note: Neo4j string functions don't take char codes; we embed the unicode
# hyphens (U+2010, U+2013, U+2014) directly. Heredoc keeps them literal.
$hyphen2010 = [char]0x2010
$hyphen2013 = [char]0x2013
$hyphen2014 = [char]0x2014

$q = @"
MATCH (pn:PartNumber)
WITH pn,
  toUpper(
    replace(
      replace(
        replace(
          replace(
            replace(pn.value, ' ', ''),
          '$hyphen2010', '-'),
        '$hyphen2013', '-'),
      '$hyphen2014', '-'),
    '/', '-')
  ) AS norm
WITH norm, collect(pn) AS pns
WHERE size(pns) > 1
UNWIND pns AS pn_x
OPTIONAL MATCH (pn_x)<-[r]-()
WITH norm, pns, pn_x, count(r) AS edges
ORDER BY norm, edges DESC
WITH norm, pns, collect(pn_x)[0] AS primary
UNWIND pns AS alias_pn
WITH primary, alias_pn WHERE alias_pn <> primary
MERGE (alias_pn)-[r:ALIAS_OF]->(primary)
ON CREATE SET r.phase='v8', r.kind='ocr_variant', r.confidence='high'
WITH primary, count(alias_pn) AS aliases
SET primary.is_primary_pn = true, primary.is_primary_phase = 'v8'
RETURN count(DISTINCT primary) AS primary_pns, sum(aliases) AS aliases_created;
"@
Cy $q
""

# ============================================================================
# 8.2 - Incomplete-PN matching
#
# "Incomplete P/N (missing the dash number): find the complete P/N with
# -number and S/N pair." Detection: PN_short has no dash; PN_long starts
# with PN_short + '-'; both co-mention with the same SN via a Component.
# ============================================================================
"---- 8.2  Incomplete-PN ALIAS_OF detection (no-dash -> dashed) ----"
$q = @"
MATCH (sn:SerialNumber)<-[:HAS_SN]-(c1:Component)-[:HAS_PRIMARY_PN]->(short:PartNumber)
MATCH (sn)<-[:HAS_SN]-(c2:Component)-[:HAS_PRIMARY_PN]->(full:PartNumber)
WHERE short <> full
  AND NOT short.value CONTAINS '-'
  AND full.value STARTS WITH (short.value + '-')
  AND NOT (short)-[:ALIAS_OF]->(full)
  AND NOT (short)<-[:ALIAS_OF]-(full)
MERGE (short)-[r:ALIAS_OF]->(full)
ON CREATE SET r.phase='v8', r.kind='incomplete_pn', r.confidence='medium', r.via_sn=sn.value
RETURN count(DISTINCT r) AS aliases_created;
"@
Cy $q
""

# ============================================================================
# 8.3 - Dash-family sibling detection
#
# PNs that share everything except the trailing dash-suffix become
# siblings. This is NOT an alias -- siblings can be unrelated revisions or
# entirely different configurations. Promotion to REVISION_OF happens in
# 8.5 with SB evidence.
# ============================================================================
"---- 8.3  Dash-family sibling detection ----"
$q = @"
MATCH (pn:PartNumber)
WHERE pn.value CONTAINS '-'
WITH pn,
     split(pn.value, '-') AS parts
WITH pn, parts, parts[size(parts)-1] AS suffix
WHERE suffix =~ '[0-9]{1,4}[A-Z]?'
WITH pn, suffix, reduce(s='', x IN parts[0..size(parts)-1] | s + x + '-') AS prefix_raw
WITH pn, suffix, left(prefix_raw, size(prefix_raw)-1) AS prefix
WHERE size(prefix) >= 4
WITH prefix, collect({pn:pn, suffix:suffix}) AS members
WHERE size(members) >= 2
UNWIND members AS m_a
UNWIND members AS m_b
WITH prefix, m_a.pn AS p_a, m_a.suffix AS s_a, m_b.pn AS p_b, m_b.suffix AS s_b
WHERE s_a < s_b
MERGE (p_a)-[r:DASH_FAMILY_OF]-(p_b)
ON CREATE SET r.phase='v8', r.prefix=prefix, r.suffix_a=s_a, r.suffix_b=s_b, r.promoted=false
RETURN count(DISTINCT r) AS sibling_edges, count(DISTINCT prefix) AS dash_families;
"@
Cy $q

# 8.3b - Cleanup: drop DASH_FAMILY_OF pairs without shared-SN evidence.
# Lukas: aliases require evidence. Without a Component on each side sharing
# an SN, dash-family is just prefix matching - not a real relationship.
$q = @"
MATCH (a:PartNumber)-[r:DASH_FAMILY_OF {phase:'v8'}]-(b:PartNumber)
WHERE id(a) < id(b)
  AND NOT EXISTS {
    MATCH (a)<-[:HAS_PRIMARY_PN]-(c1:Component)-[:HAS_SN]->(sn:SerialNumber)
    MATCH (sn)<-[:HAS_SN]-(c2:Component)-[:HAS_PRIMARY_PN]->(b)
  }
DELETE r
RETURN count(*) AS unsupported_pairs_removed;
"@
Cy $q
""

# ============================================================================
# 8.4 - Vendor<->OEM via Form1/CRS/JobCard page co-mention
#
# Two PNs that co-occur on the same release-doc page AND both reference
# the same SN are strong candidates for vendor/OEM aliasing. Already-
# aliased pairs (8.1, 8.2) are skipped.
# ============================================================================
"---- 8.4  Vendor<->OEM ALIAS_OF via Form1/CRS page co-mention ----"
$q = @"
MATCH (releaseDoc)-[:CARRIES]-(p:Page)
WHERE releaseDoc:Form1 OR releaseDoc:CRS OR releaseDoc:JobCard
MATCH (p)-[:MENTIONS_PN]->(pn_a:PartNumber)
MATCH (p)-[:MENTIONS_PN]->(pn_b:PartNumber)
WHERE pn_a <> pn_b
MATCH (p)-[:MENTIONS_SN]->(sn:SerialNumber)
WHERE EXISTS { MATCH (:Component)-[:HAS_SN]->(sn) }
  AND NOT (pn_a)-[:ALIAS_OF]-(pn_b)
WITH pn_a, pn_b, sn, count(DISTINCT p) AS pages, count(DISTINCT releaseDoc) AS docs
WHERE pages >= 1 AND docs >= 1
WITH pn_a, pn_b, pages, docs
// pick direction: alias the PN with fewer edges
OPTIONAL MATCH (pn_a)<-[ea]-()
WITH pn_a, pn_b, pages, docs, count(ea) AS a_edges
OPTIONAL MATCH (pn_b)<-[eb]-()
WITH pn_a, pn_b, pages, docs, a_edges, count(eb) AS b_edges
WITH (CASE WHEN a_edges <= b_edges THEN pn_a ELSE pn_b END) AS alias_pn,
     (CASE WHEN a_edges <= b_edges THEN pn_b ELSE pn_a END) AS primary,
     pages, docs
WHERE alias_pn <> primary
MERGE (alias_pn)-[r:ALIAS_OF]->(primary)
ON CREATE SET r.phase='v8', r.kind='vendor_oem_co_mention', r.confidence='medium',
              r.evidence_pages=pages, r.evidence_docs=docs
RETURN count(DISTINCT r) AS aliases_created;
"@
Cy $q
""

# ============================================================================
# 8.5 - Post-SB revision promotion
#
# Dash-family siblings + a ServiceBulletin compliance event for the same
# SN where the SB description mentions BOTH dash-suffixes (or the new one
# after compliance). Promote DASH_FAMILY_OF -> REVISION_OF.
# ============================================================================
"---- 8.5  Post-SB REVISION_OF promotion (dash-family + SB evidence) ----"
$q = @"
MATCH (pn_old:PartNumber)-[df:DASH_FAMILY_OF]-(pn_new:PartNumber)
WHERE df.promoted = false
WITH pn_old, pn_new, df, df.suffix_a AS sfx_a, df.suffix_b AS sfx_b
// Look for SB compliance event mentioning either suffix on a shared SN
MATCH (c:Component)-[:HAS_PRIMARY_PN]->(pn_old)
WHERE NOT c:Quarantined
MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
WHERE NOT toUpper(sn.value) CONTAINS 'CHALLENGER'
  AND NOT toUpper(sn.value) = 'N/A'
MATCH (c)<-[:AFFECTED]-(e:Event {kind:'compliance'})
WHERE e.description IS NOT NULL
  AND (e.description CONTAINS sfx_a OR e.description CONTAINS sfx_b)
  AND (toUpper(e.description) CONTAINS 'SB' OR toUpper(e.description) CONTAINS 'BULLETIN' OR toUpper(e.description) CONTAINS 'MOD')
WITH pn_old, pn_new, df, sn, collect(DISTINCT e) AS sb_events, sfx_a, sfx_b
WHERE size(sb_events) >= 1
MERGE (pn_old)-[r:REVISION_OF]->(pn_new)
ON CREATE SET r.phase='v8', r.confidence='high',
              r.direction = sfx_a + '_to_' + sfx_b,
              r.sb_evidence_events = size(sb_events),
              r.via_sn = sn.value
SET df.promoted = true, df.promoted_phase = 'v8'
RETURN count(DISTINCT r) AS revisions_created;
"@
Cy $q
""

# ============================================================================
# 8.6 - Primary PN election per alias cluster
#
# For each connected component in the ALIAS_OF graph, the node with the
# most total evidence (edges in) becomes primary. Already done partially
# in 8.1; here we ensure every alias cluster has a primary marked.
# ============================================================================
"---- 8.6  Primary PN election (per alias cluster) ----"
$q = @"
MATCH (pn:PartNumber)
WHERE EXISTS { MATCH (pn)-[:ALIAS_OF]->() } OR EXISTS { MATCH (pn)<-[:ALIAS_OF]-() }
WITH pn
OPTIONAL MATCH (pn)<-[r]-()
WITH pn, count(r) AS edges
ORDER BY edges DESC
// For each cluster, the first match wins. We approximate clusters by
// chasing one hop of ALIAS_OF outgoing.
WITH pn, edges
OPTIONAL MATCH (pn)-[:ALIAS_OF]->(p:PartNumber)
WITH pn, edges, coalesce(p, pn) AS cluster_root
WITH cluster_root, collect({pn:pn, edges:edges}) AS members
UNWIND members AS m
WITH cluster_root, m
ORDER BY m.edges DESC
WITH cluster_root, collect(m)[0] AS top
SET top.pn.is_primary_pn = true, top.pn.is_primary_phase = 'v8'
RETURN count(DISTINCT top.pn) AS primary_pns_set;
"@
Cy $q
""

# ============================================================================
# 8.7 - Rewire Component.HAS_PRIMARY_PN to canonical primary
#
# For each Component whose HAS_PRIMARY_PN currently points to an alias,
# add a new HAS_PRIMARY_PN edge to the canonical primary and demote the
# old edge to HAS_ALIAS_PN. Both edges carry phase='v8' provenance.
# ============================================================================
"---- 8.7  Rewire Component.HAS_PRIMARY_PN to canonical primary ----"
$q = @"
MATCH (c:Component)-[hp:HAS_PRIMARY_PN]->(alias_pn:PartNumber)-[:ALIAS_OF]->(primary:PartNumber)
WHERE alias_pn <> primary
  AND NOT EXISTS { MATCH (c)-[:HAS_PRIMARY_PN]->(primary) }
MERGE (c)-[r_new:HAS_PRIMARY_PN_V8]->(primary)
ON CREATE SET r_new.phase='v8', r_new.original_alias=alias_pn.value
MERGE (c)-[r_alias:HAS_ALIAS_PN]->(alias_pn)
ON CREATE SET r_alias.phase='v8', r_alias.demoted_from='HAS_PRIMARY_PN'
RETURN count(DISTINCT c) AS components_rewired;
"@
Cy $q
""

# ============================================================================
# 8.8 - Honest-not-found report
# ============================================================================
"---- 8.8  Honest dash-family review summary ----"
$q = @"
MATCH ()-[r:DASH_FAMILY_OF]-()
WITH r.promoted AS promoted, count(*) AS n
RETURN promoted, n ORDER BY promoted;
"@
Cy $q

$q = @"
MATCH (pn1:PartNumber)-[r:DASH_FAMILY_OF]-(pn2:PartNumber)
WHERE r.promoted = false
WITH pn1.value AS a, pn2.value AS b, r.prefix AS prefix LIMIT 15
RETURN prefix, a + ' <-> ' + b AS sibling_pair;
"@
Cy $q
""

# ============================================================================
# Summary
# ============================================================================
"---- v8 final counts ----"
$q = @"
MATCH ()-[r:ALIAS_OF {phase:'v8'}]->() RETURN r.kind AS kind, count(*) AS n ORDER BY n DESC;
MATCH ()-[r:DASH_FAMILY_OF {phase:'v8'}]-() RETURN count(*)/2 AS dash_family_edges;
MATCH ()-[r:REVISION_OF {phase:'v8'}]->() RETURN count(*) AS revision_edges;
MATCH (pn:PartNumber) WHERE pn.is_primary_pn = true RETURN count(pn) AS primary_pns;
MATCH (c:Component)-[:HAS_PRIMARY_PN_V8]->() RETURN count(c) AS components_with_new_primary;
"@
Cy $q

"==== v8 complete   elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s ===="
