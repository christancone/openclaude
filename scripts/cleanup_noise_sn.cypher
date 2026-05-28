// cleanup_noise_sn.cypher
//
// Live-graph remediation for the noise-SN fanout discovered post-Phase 7.5
// in the CL650-6134 rebuild. The phantom :SerialNumber {value:'N/A'} node
// attracted 90 :Component nodes that share that fake SN. Per Lukas
// Cheatsheet §13, sentinel values like "N/A" are OCR placeholders for
// non-serialised parts (bulk hardware, qty 348 oxygen hoses, qty 390
// placards, etc.) — the cert is correct, but the graph model must not
// treat the sentinel as a real node.
//
// This pass:
//   1. Identifies noise :SerialNumber and :PartNumber nodes (matches the
//      blocklist in graph_dal/_normalize.py:is_noise_identifier).
//   2. Identifies :Component nodes whose installed_sn is noise (population/
//      bulk certs should not have a serialised :Component representation).
//   3. Cascades: for each Form 1 affected, set is_batch_cert=true and clear
//      block_10_sn to NULL (it was wrongly stored as "N/A").
//   4. Tracks every change with a phase property for rollback.
//
// Run via: docker exec sparengine-neo4j cypher-shell -u neo4j -p $PASS -f /tmp/cleanup_noise_sn.cypher
//
// Rollback (none possible — DELETE is permanent; the next clean Phase 1+
// rebuild will repopulate correctly from the CSV with the new noise gates).
// Take a snapshot first via: pre_wipe_snapshot.txt

// ----------------------------------------------------------------------------
// 1. Pre-snapshot counts (run these and save the output before changing anything)
// ----------------------------------------------------------------------------
MATCH (sn:SerialNumber)
WHERE coalesce(sn.value,'') = ''
   OR toUpper(sn.value) IN ['N/A','NA','N\\A','N.A.','N.A','NONE','NIL','NULL',
                            'TBD','TBA','UNKNOWN','UNK','-','--','---','_','__',
                            'VARIOUS','SEE BLOCK 12','SEE REMARKS','SEE REVERSE',
                            'NOT APPLICABLE','NOT AVAILABLE','NOT ASSIGNED',
                            'X','XX','XXX','?','??','???','0','00','000']
   OR size(coalesce(sn.value,'')) <= 1
   OR sn.value =~ '(?i)\\s+'
   OR sn.value =~ '\\d{4}-\\d{2}-\\d{2}'
   OR sn.value =~ '\\d{2}/\\d{2}/\\d{2,4}'
   OR sn.value =~ '(19|20)\\d{2}'
RETURN 'pre' AS phase,
       'noise_serialnumbers' AS metric, count(sn) AS n
UNION ALL
MATCH (c:Component)
OPTIONAL MATCH (c)-[:HAS_SN]->(sn:SerialNumber)
WITH c, sn
WHERE coalesce(sn.value, c.installed_sn, '') = ''
   OR toUpper(coalesce(sn.value, c.installed_sn,'')) IN ['N/A','NA','NONE','TBD','UNK','UNKNOWN','-','--','VARIOUS']
RETURN 'pre' AS phase,
       'components_on_noise_sn' AS metric, count(c) AS n
UNION ALL
MATCH (f:Form1)
WHERE toUpper(coalesce(f.block_10_sn,'')) IN ['N/A','NA','NONE','TBD','UNK','UNKNOWN','-','--','VARIOUS']
RETURN 'pre' AS phase,
       'form1_with_noise_block_10' AS metric, count(f) AS n;

// ----------------------------------------------------------------------------
// 2. Promote affected Form 1s to is_batch_cert=true + clear block_10_sn
// ----------------------------------------------------------------------------
MATCH (f:Form1)
WHERE toUpper(coalesce(f.block_10_sn,'')) IN ['N/A','NA','NONE','TBD','UNK','UNKNOWN','-','--','VARIOUS','NIL']
   OR f.block_10_sn IS NULL
SET f.is_batch_cert = true,
    f.batch_cert_reason = coalesce(f.batch_cert_reason, 'block_10_sentinel_post_build'),
    f.block_10_sn_was = f.block_10_sn,   // preserve original value for audit
    f.block_10_sn = null,
    f.cleanup_phase = 'noise_sn_2026_05_19'
RETURN count(f) AS form1_promoted_to_batch_cert;

// ----------------------------------------------------------------------------
// 3. Detach the Form1 -[:RELEASES_SN]-> :SerialNumber{noise} edges
//    (the Form 1 itself stays; only the false SN linkage is removed).
// ----------------------------------------------------------------------------
MATCH (f:Form1)-[r:RELEASES_SN]->(sn:SerialNumber)
WHERE coalesce(sn.value,'') = ''
   OR toUpper(sn.value) IN ['N/A','NA','NONE','TBD','UNK','UNKNOWN','-','--','VARIOUS','NIL']
   OR size(coaleske(sn.value,'')) <= 1
DELETE r
RETURN count(r) AS releases_sn_edges_removed;

// ----------------------------------------------------------------------------
// 4. DETACH DELETE the spurious :Component nodes that exist only because
//    a phantom (PN, noise_SN) page co-mention pair was promoted.
// ----------------------------------------------------------------------------
MATCH (c:Component)
WHERE toUpper(coalesce(c.installed_sn,'')) IN ['N/A','NA','NONE','TBD','UNK','UNKNOWN','-','--','VARIOUS','NIL']
   OR coalesce(c.installed_sn,'') = ''
   OR size(coalesce(c.installed_sn,'')) <= 1
WITH c
DETACH DELETE c
RETURN count(*) AS spurious_components_deleted;

// ----------------------------------------------------------------------------
// 5. DETACH DELETE the phantom :SerialNumber nodes themselves.
// ----------------------------------------------------------------------------
MATCH (sn:SerialNumber)
WHERE coalesce(sn.value,'') = ''
   OR toUpper(sn.value) IN ['N/A','NA','N\\A','N.A.','N.A','NONE','NIL','NULL',
                            'TBD','TBA','UNKNOWN','UNK','-','--','---','_','__',
                            'VARIOUS','SEE BLOCK 12','SEE REMARKS','SEE REVERSE',
                            'NOT APPLICABLE','NOT AVAILABLE','NOT ASSIGNED',
                            'X','XX','XXX','?','??','???','0','00','000']
   OR size(coalesce(sn.value,'')) <= 1
   OR sn.value =~ '\\d{4}-\\d{2}-\\d{2}'
   OR sn.value =~ '\\d{2}/\\d{2}/\\d{2,4}'
   OR sn.value =~ '(19|20)\\d{2}'
WITH sn
DETACH DELETE sn
RETURN count(*) AS noise_serialnumbers_deleted;

// ----------------------------------------------------------------------------
// 6. Same gate for :PartNumber nodes — noise PNs are rarer but possible.
// ----------------------------------------------------------------------------
MATCH (pn:PartNumber)
WHERE coalesce(pn.value,'') = ''
   OR toUpper(pn.value) IN ['N/A','NA','NONE','TBD','UNK','UNKNOWN','-','--','VARIOUS','NIL']
   OR size(coalesce(pn.value,'')) <= 1
WITH pn
DETACH DELETE pn
RETURN count(*) AS noise_partnumbers_deleted;

// ----------------------------------------------------------------------------
// 7. Post-cleanup verification
// ----------------------------------------------------------------------------
MATCH (sn:SerialNumber) RETURN 'post' AS phase, 'serial_numbers_total' AS metric, count(sn) AS n
UNION ALL
MATCH (sn:SerialNumber) WHERE toUpper(coalesce(sn.value,'')) IN ['N/A','NA','NONE','-']
RETURN 'post' AS phase, 'noise_serialnumbers_remaining' AS metric, count(sn) AS n
UNION ALL
MATCH (c:Component) RETURN 'post' AS phase, 'components_total' AS metric, count(c) AS n
UNION ALL
MATCH (c:Component) WHERE toUpper(coalesce(c.installed_sn,'')) IN ['N/A','NA','NONE','-']
RETURN 'post' AS phase, 'components_on_noise_sn_remaining' AS metric, count(c) AS n
UNION ALL
MATCH (f:Form1 {is_batch_cert: true}) RETURN 'post' AS phase, 'form1_marked_batch_cert' AS metric, count(f) AS n;
