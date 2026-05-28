$ErrorActionPreference = 'Stop'
$inFile = "D:\work\openclaude\dumps\jsonc\component_history_v6.jsonl"
$fi = Get-Item $inFile
"=" * 78
"   component_history_v6.jsonl - OVERVIEW"
"=" * 78
"File:   $inFile"
$mb = [math]::Round($fi.Length / 1MB, 2)
"Size:   $mb MB"
$lc = (Get-Content $inFile | Measure-Object -Line).Lines
"Lines:  $lc"
""

# Read everything
$header = $null
$docs = New-Object System.Collections.ArrayList
$findings = New-Object System.Collections.ArrayList
$currentlyInstalled = New-Object System.Collections.ArrayList
$historical = New-Object System.Collections.ArrayList
$quarantined = New-Object System.Collections.ArrayList
foreach ($line in [System.IO.File]::ReadLines($inFile)) {
    $o = $line | ConvertFrom-Json
    switch ($o.type) {
        'header'   { $header = $o }
        'document' { [void]$docs.Add($o) }
        'finding'  { [void]$findings.Add($o) }
        'component_currently_installed' { [void]$currentlyInstalled.Add($o) }
        'component_historical' { [void]$historical.Add($o) }
        'component_scope_review_needed' { [void]$quarantined.Add($o) }
    }
}

"-" * 78
"  LINE COMPOSITION"
"-" * 78
"  header:                          1"
"  document:                        $($docs.Count)"
"  finding:                         $($findings.Count)"
"  component_currently_installed:   $($currentlyInstalled.Count)"
"  component_historical:            $($historical.Count)"
"  component_scope_review_needed:   $($quarantined.Count)"
""

"-" * 78
"  HEADER: ASSET SNAPSHOT"
"-" * 78
"  asset_id:               $($header.asset_id)"
"  schema_version:         $($header.schema_version)"
"  as_of_date:             $($header.asset_snapshot.as_of_date)"
"  airframe_tsn:           $($header.asset_snapshot.airframe_tsn.value) [$($header.asset_snapshot.airframe_tsn.method)]"
"  airframe_csn:           $($header.asset_snapshot.airframe_csn.value) [$($header.asset_snapshot.airframe_csn.method)]"
"  manufacture_date:       $($header.asset_snapshot.aircraft_manufacture_date.value) [$($header.asset_snapshot.aircraft_manufacture_date.method)]"
"  LH engine:              SN=$($header.asset_snapshot.lh_engine_snapshot.sn) TSN=$($header.asset_snapshot.lh_engine_snapshot.tsn) CSN=$($header.asset_snapshot.lh_engine_snapshot.csn)"
"  RH engine:              SN=$($header.asset_snapshot.rh_engine_snapshot.sn) TSN=$($header.asset_snapshot.rh_engine_snapshot.tsn) CSN=$($header.asset_snapshot.rh_engine_snapshot.csn)"
"  APU:                    SN=$($header.asset_snapshot.apu_snapshot.sn) TSH=$($header.asset_snapshot.apu_snapshot.tsh)"
"  MPD/TLMC search found:  $($header.mpd_tlmc_search.found)"
""

"-" * 78
"  COMPONENTS - IDENTITY BREAKDOWN"
"-" * 78
$allComps = @($currentlyInstalled) + @($historical)
"  In-scope total:         $($allComps.Count)"
"  Currently installed:    $($currentlyInstalled.Count)"
"  Historical (removed):   $($historical.Count)"
"  Quarantined:            $($quarantined.Count)"
""
"  By cycle_counter:"
$byCyc = $allComps | Group-Object { $_.identity.cycle_counter } | Sort-Object Count -Descending
foreach ($g in $byCyc) {
    $name = "$($g.Name)"
    $line = "    {0,-12}  {1}" -f $name, $g.Count
    $line
}
""
"  By component_category (top 10):"
$byCat = $allComps | Group-Object { $_.identity.component_category } | Sort-Object Count -Descending
foreach ($g in ($byCat | Select-Object -First 10)) {
    $name = "$($g.Name)"
    if ([string]::IsNullOrWhiteSpace($name)) { $name = '(empty)' }
    "    {0,-25}  {1}" -f $name, $g.Count
}
""
$llp = (@($allComps | Where-Object { $_.identity.is_llp -eq $true })).Count
$oh = (@($allComps | Where-Object { $_.identity.is_overhaul_tracked -eq $true })).Count
"  is_llp = true:          $llp"
"  is_overhaul_tracked:    $oh"
""

"-" * 78
"  LUKAS 10-FIELD COVERAGE"
"-" * 78

$f1c = (@($allComps | Where-Object { $_.first_install_date.method -eq 'confirmed' })).Count
$f1i = (@($allComps | Where-Object { $_.first_install_date.method -eq 'inferred' })).Count
$f1u = (@($allComps | Where-Object { $_.first_install_date.method -eq 'unresolvable' })).Count
"  1.  first_install_date:        confirmed=$f1c  inferred=$f1i  unresolvable=$f1u"

$tz = (@($allComps | Where-Object { $_.first_install_ac_tsn.value -eq '0:00' })).Count
$tn = (@($allComps | Where-Object { -not $_.first_install_ac_tsn.value })).Count
"  2.  TSN_CSN_at_install:        since-new(0:00)=$tz  null/unresolvable=$tn"

$ohEnt = (@($allComps | Where-Object { $_.overhaul_history.entries -and $_.overhaul_history.entries.Count -gt 0 })).Count
$ohNa = (@($allComps | Where-Object { $_.overhaul_history.outcome -eq 'not_applicable_since_new' })).Count
"  3.  overhaul_history:          with_entries=$ohEnt  not_applicable=$ohNa"

$rpEnt = (@($allComps | Where-Object { $_.repair_history.entries -and $_.repair_history.entries.Count -gt 0 })).Count
"  4.  repair_history:            with_entries=$rpEnt"

$modFound = (@($allComps | Where-Object { $_.modification_history.outcome -like '*found*' -or $_.modification_history.outcome -like '*present*' })).Count
$modSb = (@($allComps | Where-Object { $_.modification_history.specific_sb_refs -and $_.modification_history.specific_sb_refs.Count -gt 0 })).Count
$modAd = (@($allComps | Where-Object { $_.modification_history.specific_ad_refs -and $_.modification_history.specific_ad_refs.Count -gt 0 })).Count
"  5.  modification_history:      found=$modFound  with_SB=$modSb  with_AD=$modAd"

$f6NotReq = (@($allComps | Where-Object { $_.form_1.outcome -eq 'not_required_since_new' })).Count
$f6Spec = (@($allComps | Where-Object { $_.form_1.specific_form1_refs -and $_.form_1.specific_form1_refs.Count -gt 0 })).Count
$f6RelPage = (@($allComps | Where-Object { $_.form_1.outcome -eq 'form1_release_page_located_no_cert_number' })).Count
$f6Pending = (@($allComps | Where-Object { $_.form_1.outcome -eq 'required_pending_form1_in_dossier' })).Count
"  6.  form_1:                    not_required=$f6NotReq  specific_certs=$f6Spec  release_page_only=$f6RelPage  pending=$f6Pending"

$tsoNa = (@($allComps | Where-Object { $_.totals.current_tso.method -eq 'not_applicable' })).Count
"  7-8 TSO:                       not_applicable(since-new)=$tsoNa"

$tsnC = (@($allComps | Where-Object { $_.totals.current_tsn.method -eq 'calculated' -or $_.totals.current_tsn.method -eq 'confirmed' })).Count
$csnC = (@($allComps | Where-Object { $_.totals.current_csn.method -eq 'calculated' -or $_.totals.current_csn.method -eq 'confirmed' })).Count
"  9.  current_TSN_CSN:           TSN_populated=$tsnC  CSN_populated=$csnC"

$remUnr = (@($allComps | Where-Object { $_.totals.remaining_until_overhaul.method -eq 'unresolvable' })).Count
"  10. remaining_until_overhaul:  unresolvable(MPD_absent)=$remUnr"
""

"-" * 78
"  POST-OEM INSTALLS FROM ANNEX 6"
"-" * 78
$annex6 = @($allComps | Where-Object { $_.annex6_summary_finding })
"  Post-OEM components:    $($annex6.Count)"
$byWo = $annex6 | Group-Object { $_.annex6_summary_finding.wo_text } | Sort-Object Count -Descending
foreach ($g in $byWo) {
    "    $($g.Count) component(s) -- WO=$($g.Name)"
}
""

"-" * 78
"  FINDINGS (audit flags)"
"-" * 78
"  Total findings:         $($findings.Count)"
$bySev = $findings | Group-Object severity | Sort-Object Count -Descending
foreach ($g in $bySev) { "    {0,-15} {1}" -f $g.Name, $g.Count }
""
"  By category:"
$byCat = $findings | Group-Object category | Sort-Object Count -Descending
foreach ($g in $byCat) { "    {0,-30} {1}" -f $g.Name, $g.Count }
""
$downgraded = (@($findings | Where-Object { $_.lukas_rule_downgrade })).Count
"  Auto-downgraded by Lukas rule (since-new):  $downgraded"
""

"-" * 78
"  DOSSIER COVERAGE"
"-" * 78
"  Documents referenced:   $($docs.Count)"
$compsWithDossier = (@($allComps | Where-Object { $_.dossier_evidence -and $_.dossier_evidence.pages_referencing_sn -gt 0 })).Count
"  Components w/ dossier:  $compsWithDossier of $($allComps.Count)"
$totalDossierPages = ($allComps | Where-Object { $_.dossier_evidence } | ForEach-Object { $_.dossier_evidence.pages_referencing_sn } | Measure-Object -Sum).Sum
"  Total page refs (sum):  $totalDossierPages"
""

"-" * 78
"  STRUCTURE OF A COMPONENT RECORD"
"-" * 78
$sample = $currentlyInstalled[0]
"  Top-level fields each component carries:"
$sample.PSObject.Properties.Name | Sort-Object | ForEach-Object { "    $_" }
""

"-" * 78
"  QUARANTINED EXAMPLES (scope review needed)"
"-" * 78
foreach ($q in ($quarantined | Select-Object -First 8)) {
    "  pn='$($q.identity.part_number)'  sn='$($q.identity.serial_number)'"
}
""
"  Quarantine reason: $($quarantined[0].quarantine_reason)"
""

"=" * 78
"  Done"
"=" * 78
