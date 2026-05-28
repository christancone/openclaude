$ErrorActionPreference = 'Stop'
$inFile = "D:\work\openclaude\dumps\jsonc\component_history_v5.jsonl"

# Pull all component records
$comps = New-Object System.Collections.ArrayList
foreach ($line in [System.IO.File]::ReadLines($inFile)) {
    if ($line -notmatch '"type":"component_') { continue }
    [void]$comps.Add(($line | ConvertFrom-Json))
}
"Loaded $($comps.Count) component records"
""

# ============================================================================
# Gap analysis: per Lukas's 10 fields
# ============================================================================
$g = [ordered]@{
    total = $comps.Count
}

# Field 1: First install date
$f1_confirmed = 0; $f1_inferred_mfd = 0; $f1_inferred_table = 0; $f1_inferred = 0; $f1_unresolvable = 0
foreach ($c in $comps) {
    switch ($c.first_install_date.method) {
        'confirmed' { $f1_confirmed++ }
        'inferred' {
            if ($c.first_install_date.outcome -like '*manufacture*') { $f1_inferred_mfd++ }
            elseif ($c.first_install_date.outcome -like '*table*') { $f1_inferred_table++ }
            else { $f1_inferred++ }
        }
        default { $f1_unresolvable++ }
    }
}
"### Field 1: First install date"
"  confirmed (real event):                  $f1_confirmed"
"  inferred (manufacture-date placeholder): $f1_inferred_mfd"
"  inferred (earliest table date):          $f1_inferred_table"
"  inferred (other):                        $f1_inferred"
"  unresolvable:                            $f1_unresolvable"
""

# Field 2: TSN/CSN at first install
$f2tsn_ok = 0; $f2tsn_zero = 0; $f2tsn_null = 0
$f2csn_ok = 0; $f2csn_zero = 0; $f2csn_null = 0
foreach ($c in $comps) {
    $tv = $c.first_install_ac_tsn.value
    $cv = $c.first_install_ac_csn.value
    if ($tv -eq '0:00') { $f2tsn_zero++ }
    elseif ($tv -and $tv -ne '0:00') { $f2tsn_ok++ }
    else { $f2tsn_null++ }
    if ($cv -eq 0 -or $cv -eq '0') { $f2csn_zero++ }
    elseif ($cv -or $cv -eq 0) { $f2csn_ok++ }
    else { $f2csn_null++ }
}
"### Field 2: TSN/CSN at first install"
"  TSN = 0:00 (since-new):                  $f2tsn_zero"
"  TSN = real non-zero value:               $f2tsn_ok"
"  TSN = null (unresolvable):               $f2tsn_null"
"  CSN = 0 (since-new):                     $f2csn_zero"
"  CSN = real non-zero value:               $f2csn_ok"
"  CSN = null (unresolvable):               $f2csn_null"
""

# Field 3-4: Overhaul + Repair history
$ovh_pop = 0; $ovh_na_since_new = 0; $ovh_no_records = 0
$rep_pop = 0; $rep_no_records = 0
foreach ($c in $comps) {
    if ($c.overhaul_history.entries -and $c.overhaul_history.entries.Count -gt 0) { $ovh_pop++ }
    elseif ($c.overhaul_history.outcome -eq 'not_applicable_since_new') { $ovh_na_since_new++ }
    else { $ovh_no_records++ }
    if ($c.repair_history.entries -and $c.repair_history.entries.Count -gt 0) { $rep_pop++ }
    else { $rep_no_records++ }
}
"### Field 3: Overhaul history"
"  with entries:                            $ovh_pop"
"  not applicable (since-new):              $ovh_na_since_new"
"  no records found:                        $ovh_no_records"
""
"### Field 4: Repair history"
"  with entries:                            $rep_pop"
"  no records found:                        $rep_no_records"
""

# Field 5: Modification history
$mod_pop = 0; $mod_specific_sb = 0; $mod_specific_ad = 0; $mod_none = 0
foreach ($c in $comps) {
    if ($c.modification_history.outcome -like '*found*' -or $c.modification_history.outcome -like '*present*') { $mod_pop++ }
    if ($c.modification_history.specific_sb_refs -and $c.modification_history.specific_sb_refs.Count -gt 0) { $mod_specific_sb++ }
    if ($c.modification_history.specific_ad_refs -and $c.modification_history.specific_ad_refs.Count -gt 0) { $mod_specific_ad++ }
    if ($c.modification_history.outcome -eq 'no_modifications_recorded') { $mod_none++ }
}
"### Field 5: Modification history"
"  outcome = found/present in dossier:      $mod_pop"
"  has specific SB references:              $mod_specific_sb"
"  has specific AD references:              $mod_specific_ad"
"  no modifications recorded:               $mod_none"
""

# Field 6: Form 1
$f6_not_required = 0; $f6_specific = 0; $f6_pending = 0; $f6_required_no_doc = 0
foreach ($c in $comps) {
    if ($c.form_1.outcome -eq 'not_required_since_new') { $f6_not_required++ }
    if ($c.form_1.specific_form1_refs -and $c.form_1.specific_form1_refs.Count -gt 0) { $f6_specific++ }
    if ($c.form_1.outcome -eq 'required_pending_form1_in_dossier') { $f6_pending++ }
    if ($c.form_1.required -eq $true -and -not ($c.form_1.specific_form1_refs -and $c.form_1.specific_form1_refs.Count -gt 0)) { $f6_required_no_doc++ }
}
"### Field 6: Form 1 / Parts Certificate"
"  not required (since-new):                $f6_not_required"
"  with specific Form 1 / 8130 refs:        $f6_specific"
"  required, pending search (post-OEM):     $f6_pending"
"  required but no specific cert located:   $f6_required_no_doc"
""

# Field 7-8: Last shop visit + TSO
$f7_pop = 0
$f8_na = 0; $f8_unr = 0
foreach ($c in $comps) {
    if ($c.totals.current_tso.value) { $f7_pop++ }
    if ($c.totals.current_tso.method -eq 'not_applicable') { $f8_na++ }
    elseif ($c.totals.current_tso.method -eq 'unresolvable') { $f8_unr++ }
}
"### Fields 7-8: TT at last shop visit / TSO"
"  populated (calculated TSO):              $f7_pop"
"  not applicable (since-new):              $f8_na"
"  unresolvable:                            $f8_unr"
""

# Field 9: Current TSN / CSN
$f9tsn_calc = 0; $f9tsn_unr = 0
$f9csn_airframe = 0; $f9csn_engine = 0; $f9csn_apu = 0; $f9csn_unr = 0
foreach ($c in $comps) {
    if ($c.totals.current_tsn.method -eq 'calculated' -or $c.totals.current_tsn.method -eq 'confirmed') { $f9tsn_calc++ } else { $f9tsn_unr++ }
    switch ($c.totals.current_csn.outcome) {
        'csn_from_ppi_per_engine_snapshot' { $f9csn_engine++ }
        'csn_from_ppi_apu_snapshot' { $f9csn_apu++ }
        default {
            if ($c.totals.current_csn.method -eq 'calculated') { $f9csn_airframe++ }
            else { $f9csn_unr++ }
        }
    }
}
"### Field 9: Current TSN/CSN"
"  TSN calculated/confirmed:                $f9tsn_calc"
"  TSN unresolvable:                        $f9tsn_unr"
"  CSN from airframe snapshot:              $f9csn_airframe"
"  CSN from per-engine snapshot:            $f9csn_engine"
"  CSN from APU snapshot:                   $f9csn_apu"
"  CSN unresolvable:                        $f9csn_unr"
""

# Field 10: Remaining life
$f10_unr = 0
foreach ($c in $comps) {
    if ($c.totals.remaining_until_overhaul.method -eq 'unresolvable') { $f10_unr++ }
}
"### Field 10: Remaining until OH / scrap"
"  unresolvable (MPD not in dossier):       $f10_unr / $($comps.Count)"
""

# Currently installed split
$ci = 0; $hist = 0
foreach ($c in $comps) { if ($c.type -eq 'component_currently_installed') { $ci++ } else { $hist++ } }
"### Currently installed status"
"  currently installed:                     $ci"
"  historical:                              $hist"
""

# Identify the components that are MOST gappy (no dossier_evidence, no install date, no findings, etc.)
$mostGappy = New-Object System.Collections.ArrayList
foreach ($c in $comps) {
    $score = 0
    if (-not ($c.dossier_evidence -and $c.dossier_evidence.pages_referencing_sn -gt 0)) { $score += 3 }
    if ($c.first_install_date.method -ne 'confirmed') { $score += 1 }
    if (-not ($c.finding_graph_ids -and $c.finding_graph_ids.Count -gt 0)) { $score += 1 }
    if (-not ($c.modification_history.specific_sb_refs -or $c.modification_history.specific_ad_refs)) { $score += 1 }
    if (-not $c.identity.serial_number -or $c.identity.serial_number.Length -lt 3) { $score += 2 }
    [void]$mostGappy.Add(@{ score=$score; obj=$c })
}
$top = $mostGappy | Sort-Object { -$_.score } | Select-Object -First 8
"### Top gappy components"
foreach ($g in $top) {
    "  score=$($g.score)  pn=$($g.obj.identity.part_number) sn=$($g.obj.identity.serial_number) cat=$($g.obj.identity.component_category) is_llp=$($g.obj.identity.is_llp)"
}
""

# Components with NO dossier_evidence at all
$noDossier = @($comps | Where-Object { -not ($_.dossier_evidence -and $_.dossier_evidence.pages_referencing_sn -gt 0) })
"### Components with ZERO dossier evidence: $($noDossier.Count)"
foreach ($c in $noDossier | Select-Object -First 10) {
    "  pn=$($c.identity.part_number)  sn=$($c.identity.serial_number)  ata=$($c.identity.ata_chapter)  cat=$($c.identity.component_category)  is_llp=$($c.identity.is_llp)"
}
