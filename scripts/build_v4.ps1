$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# Load snapshots
# ============================================================================
$stage1 = Get-Content "D:\work\openclaude\dumps\jsonc\.v4_stage1.json" -Raw | ConvertFrom-Json
$stage2 = Get-Content "D:\work\openclaude\dumps\jsonc\.v4_stage2.json" -Raw | ConvertFrom-Json
$stage2b = Get-Content "D:\work\openclaude\dumps\jsonc\.v4_stage2b.json" -Raw | ConvertFrom-Json
$docPathMap = @{}
foreach ($p in $stage2b.docPathMap.PSObject.Properties) { $docPathMap[$p.Name] = $p.Value }
"Loaded: $($stage1.meta.PSObject.Properties.Name.Count) components, $($docPathMap.Count) docs"

# ============================================================================
# Asset snapshot (Lukas manual override)
# ============================================================================
$assetSnapshot = [ordered]@{
    as_of_date     = "2022-12-29"
    airframe_tsn   = [ordered]@{
        value = "559:25"; unit = "h"; method = "manual_override"
        outcome = "snapshot_manual_override"
        reasoning = "Aircraft TSN value of 559:25h taken from Lukas Weiss correspondence dated 2026-05-07. The technical journey log (Box A_Logbooks/Binder b_Airframe Logbook) carries this figure, but the automatic snapshot extractor returned an outlier value (4:22h)."
    }
    airframe_csn   = [ordered]@{
        value = 231; unit = "cy"; method = "manual_override"
        outcome = "snapshot_manual_override"
        reasoning = "Aircraft CSN value of 231 landings taken from Lukas Weiss correspondence."
    }
    aircraft_manufacture_date = [ordered]@{
        value = "2019-02-20"; method = "inferred"
        outcome = "manufacture_date_inferred"
        reasoning = "Aircraft manufacture date inferred from earliest install-since-new events in the dossier. The Certificate of Airworthiness header (Box A_Manuals/Binder a_Completion Data) would provide the authoritative value but is not ingested as structured data."
    }
}

# ============================================================================
# Helpers
# ============================================================================
function ParseTSN($desc) {
    if (-not $desc) { return $null }
    $m = [regex]::Match($desc, '(?:TSN[:\s=]+([\d]+(?:[:.]\d+)?))|(?:([\d]+(?:[:.]\d+)?)\s*(?:h\s*)?TSN)', 'IgnoreCase')
    if ($m.Success) {
        if ($m.Groups[1].Success) { return $m.Groups[1].Value }
        if ($m.Groups[2].Success) { return $m.Groups[2].Value }
    }
    return $null
}
function ParseCSN($desc) {
    if (-not $desc) { return $null }
    $m = [regex]::Match($desc, '(?:CSN[:\s=]+([\d]+))|(?:([\d]+)\s*CSN)|(?:([\d]+)\s*(?:ldg|landings?|cycles?))', 'IgnoreCase')
    if ($m.Success) {
        if ($m.Groups[1].Success) { return [int]$m.Groups[1].Value }
        if ($m.Groups[2].Success) { return [int]$m.Groups[2].Value }
        if ($m.Groups[3].Success) { return [int]$m.Groups[3].Value }
    }
    return $null
}
function IsZeroNum($v) {
    if ($null -eq $v) { return $false }
    $s = "$v"
    return ($s -eq "0" -or $s -eq "0:00" -or $s -eq "0.0" -or $s -eq "0:0" -or $s -eq "00:00")
}
function CycleCounter($ata) {
    if (-not $ata) { return "airframe" }
    $a = "$ata"
    $major = $a.Split('-')[0]
    if ($major -eq "49") { return "apu" }
    if ($major -match "^7[0-9]$" -or $major -eq "80") { return "engine" }
    return "airframe"
}
function ExcerptFn($s, $max=180) {
    if (-not $s) { return $null }
    $s2 = $s -replace "[\r\n]+", " "
    if ($s2.Length -gt $max) { return $s2.Substring(0,$max) + '...' } else { return $s2 }
}
function ResolveDoc($docId) {
    if (-not $docId) { return $null }
    if ($docPathMap.ContainsKey($docId)) { return $docPathMap[$docId] }
    return @{ name = "unknown"; path = "unknown" }
}
function MakeCitation($page, $contribution, $excerpt, $confidence='event_affected') {
    if (-not $page -or -not $page.doc_id) { return $null }
    $doc = ResolveDoc $page.doc_id
    return [ordered]@{
        document_id = $page.doc_id
        document_path = $doc.path
        page_number = $page.page_index + 1
        contribution = $contribution
        excerpt = (ExcerptFn $excerpt 200)
        confidence = $confidence
    }
}
function MakeCitationFromCoMention($page, $contribution, $excerpt=$null, $confidence='singleton_match') {
    if (-not $page -or -not $page.doc_id) { return $null }
    $doc = ResolveDoc $page.doc_id
    return [ordered]@{
        document_id = $page.doc_id
        document_path = $doc.path
        page_number = $page.idx + 1
        contribution = $contribution
        excerpt = $excerpt
        confidence = $confidence
    }
}

# ============================================================================
# Reasoning template renderer (Style 3)
# ============================================================================
function Render($code, $ctx) {
    if ($null -eq $ctx) { $ctx = @{} }
    $woStr = if ($ctx.wo) { " under work order $($ctx.wo)" } else { '' }
    $replStr = if ($ctx.replaced_by) { ", replaced by S/N $($ctx.replaced_by)$woStr" } else { '' }

    switch ($code) {
        'since_new_no_event' { "No installation entry was found in the dossier for this S/N. Per Lukas's FCC No. 1 rule, the part is defaulted to installed since new aircraft delivery. Confirmation requires cross-checking the aircraft serialization listing (Box A_Manuals/Binder a_Completion Data), which is not ingested as structured data." }
        'since_new_at_zero_install' { "The installation entry on $($ctx.date) explicitly records AC TSN=0:00 and CSN=0, confirming the part was installed at aircraft manufacturing." }
        'install_event_recorded' { "Installation recorded on $($ctx.date)$woStr." }
        'install_event_no_tsn' { "Installation entry on $($ctx.date) does not record TSN or CSN at install; the source entry stamps the date but leaves the AC totals fields blank." }
        'form1_not_required_since_new' { "Form 1 not required. Part appears as installed since new aircraft delivery - the aircraft serialization listing serves as the OEM release document." }
        'form1_found_on_comention_page' { "A Form 1 ($($ctx.kind), $($ctx.form1_value)) appears on a page co-mentioning this PN+SN. Block 13 date: $($ctx.block_13_date). Located in $($ctx.doc_path)." }
        'form1_not_in_dossier_post_oem' { "No Form 1 / FAA 8130-3 / TCCA Form 1 covering this PN+SN was located. Searched $($ctx.n_pages) pages co-mentioning the PN+SN. A Form 1 should exist for any part not installed at aircraft manufacturing." }
        'no_overhaul_records' { "The relevant logbook covers the operating period but records no overhaul shop visit for this S/N. No engine or component logbook overhaul entries, shop visit reports, or overhaul Form 1s were found in the dossier." }
        'overhaul_event_recorded' { "Shop visit recorded on $($ctx.date)$woStr." }
        'no_repair_records' { "No repair orders, non-routine cards, NDT reports, or borescope reports referencing this PN+SN were located in the dossier." }
        'repair_evidence_found' { "Repair/non-routine activity referenced on page $($ctx.page) of $($ctx.doc_path)." }
        'no_modifications_recorded' { "No service bulletin compliance entries, modification records, or STC references for this PN+SN were located in the dossier." }
        'sb_referenced' { "Service Bulletin compliance referenced for this S/N: $($ctx.sb_value)." }
        'mod_referenced' { "Modification recorded: $($ctx.mod_value)." }
        'stc_referenced' { "STC referenced: $($ctx.stc_value)." }
        'mpd_not_provided_with_dossier' { "Maintenance program data (MPD / TLMC ATA Chapter 4) was not provided with the dossier. Remaining-time calculations require these limits." }
        'never_overhauled_since_new' { "Not applicable - part has never been overhauled (installed since new aircraft delivery, no shop visit on record)." }
        'tsn_calc_since_new' { "TSN equals current aircraft TSN ($($ctx.value)). Since-new installation: AC_TSN(now) minus AC_TSN(at install) = $($ctx.value) minus 0:00 = $($ctx.value)." }
        'csn_calc_since_new_airframe' { "CSN equals current aircraft CSN ($($ctx.value)) - part follows the airframe cycle counter per ATA $($ctx.ata)." }
        'csn_engine_counter_unavailable' { "Cycle count cannot be reported: part is engine-mounted (ATA $($ctx.ata)). The per-engine cycle counter would apply, but the latest engine snapshot has not been extracted from the engine logbook (Box A_Logbooks/Binder e_RH Engine Logbook or equivalent)." }
        'csn_apu_counter_unavailable' { "Cycle count cannot be reported: part is APU-mounted (ATA $($ctx.ata)). The APU cycle counter would apply, but the latest APU snapshot has not been extracted from the APU logbook." }
        'anomaly_removal_no_install' { "Anomaly: this S/N has a removal entry recorded but no corresponding install entry in the dossier. This indicates either the install record was misfiled / missing, or the removal entry was misattributed to this S/N. Manual review recommended." }
        'currently_installed_no_subsequent_removal' { "Currently installed. No removal entry post-dates the last install entry for this S/N." }
        'currently_installed_since_new_no_removal' { "Currently installed since new aircraft delivery. No removal entry on record." }
        'historical_replaced' { "No longer installed. Removed on $($ctx.removal_date)$replStr." }
        default { "[no template: $code]" }
    }
}

# ============================================================================
# Build per-component record
# ============================================================================
function BuildComponentRecord($cv) {
    $row = $stage1.meta.$cv
    # row indices: 0=cv 1=pn 2=sn 3=isn 4=descr 5=cat 6=is_llp 7=is_overhaul 8=status 9=name 10=ata 11=pis 12=has_evidence
    $pn = $row[1]; $sn = $row[2]
    $cat = $row[5]; $is_llp = $row[6]; $is_overhaul = $row[7]; $status = $row[8]
    $ata = $row[10]; $pis = $row[11]; $has_evidence = $row[12]
    $cycCtr = CycleCounter $ata

    $events = @()
    $evProp = $stage1.eventsByComp.PSObject.Properties[$cv]
    if ($evProp) { $events = @($evProp.Value) }
    # Only keep events that have a date (drop null-date noise events)
    $installs = @($events | Where-Object { $_.kind -eq 'install' -and $_.date } | Sort-Object { $_.date })
    $removals = @($events | Where-Object { $_.kind -eq 'removal' -and $_.date } | Sort-Object { $_.date })
    $shopVisits = @($events | Where-Object { $_.kind -eq 'shop_visit' -and $_.date } | Sort-Object { $_.date })
    $undatedInstalls = @($events | Where-Object { $_.kind -eq 'install' -and -not $_.date })

    # Capture firsts/lasts as named scalars to avoid PowerShell indexing weirdness
    $firstInstall = $null; $lastInstall = $null; $lastRemoval = $null
    if ($installs.Count -gt 0) { $firstInstall = $installs[0]; $lastInstall = $installs[$installs.Count - 1] }
    if ($removals.Count -gt 0) { $lastRemoval = $removals[$removals.Count - 1] }

    $coPages = @()
    $cpProp = $stage2.PSObject.Properties[$cv]
    if ($cpProp) { $coPages = @($cpProp.Value) }
    # Defensive: filter out null entries from the array
    $coPages = @($coPages | Where-Object { $_ -and $_.doc_id })
    # Augment with event-evidence pages (works around incomplete MENTIONS edges)
    $eventPageDocs = @{}
    foreach ($p in $coPages) { if ($p.doc_id) { $eventPageDocs[$p.doc_id + ':' + $p.idx] = $true } }
    foreach ($ev in $events) {
        if ($ev.pages) {
            foreach ($p in @($ev.pages)) {
                $key = if ($p.doc_id) { $p.doc_id + ':' + $p.page_index } else { '' }
                if ($key -and -not $eventPageDocs.ContainsKey($key)) {
                    $eventPageDocs[$key] = $true
                }
            }
        }
    }
    $mentionCoPagesCount = (@($cpProp.Value | Where-Object { $_ })).Count

    # --- since-new classification ---
    $sn_value = $null; $sn_method = $null; $sn_code = $null; $sn_cit = $null
    $anomaly = $false
    if ($installs.Count -gt 0) {
        $zeroEvent = $null; $nonZeroEvent = $null; $anyEvent = $installs[0]
        foreach ($e in $installs) {
            $t = ParseTSN $e.descr; $c = ParseCSN $e.descr
            if ((IsZeroNum $t) -and (IsZeroNum $c)) { if (-not $zeroEvent) { $zeroEvent = $e } }
            if (($null -ne $t -and -not (IsZeroNum $t)) -or ($null -ne $c -and -not (IsZeroNum $c))) { if (-not $nonZeroEvent) { $nonZeroEvent = $e } }
        }
        if ($zeroEvent) {
            $sn_value = $true; $sn_method = 'confirmed'; $sn_code = 'since_new_at_zero_install'
            if ($zeroEvent.pages -and $zeroEvent.pages.Count -gt 0) {
                $sn_cit = MakeCitation $zeroEvent.pages[0] "Since-new confirmation via AC=0 install record" $zeroEvent.descr 'event_affected'
            }
        } elseif ($nonZeroEvent) {
            $sn_value = $false; $sn_method = 'confirmed'; $sn_code = 'install_event_recorded'
        } else {
            $sn_value = $true; $sn_method = 'inferred'; $sn_code = 'install_event_no_tsn'
        }
    } elseif ($removals.Count -gt 0) {
        $sn_value = $null; $sn_method = 'unresolvable'; $sn_code = 'anomaly_removal_no_install'
        $anomaly = $true
    } else {
        $sn_value = $true; $sn_method = 'inferred'; $sn_code = 'since_new_no_event'
    }

    $ctx = @{}
    if ($firstInstall) { $ctx.date = $firstInstall.date; $ctx.wo = $firstInstall.wo }
    $snReasoning = Render $sn_code $ctx

    # --- currently_installed ---
    $currentlyInstalled = $true; $replacementChain = @()
    $ci_code = $null
    if ($installs.Count -eq 0 -and $removals.Count -eq 0) {
        $currentlyInstalled = $true; $ci_code = 'currently_installed_since_new_no_removal'
    } elseif ($installs.Count -gt 0 -and $removals.Count -eq 0) {
        $currentlyInstalled = $true; $ci_code = 'currently_installed_no_subsequent_removal'
    } elseif ($installs.Count -gt 0 -and $removals.Count -gt 0) {
        $lastIns = $installs[-1].date; $lastRem = $removals[-1].date
        if ($lastIns -ge $lastRem) {
            $currentlyInstalled = $true; $ci_code = 'currently_installed_no_subsequent_removal'
        } else {
            $currentlyInstalled = $false; $ci_code = 'historical_replaced'
        }
    } else {
        # Removal only - anomaly
        $currentlyInstalled = $false; $ci_code = 'anomaly_removal_no_install'
    }
    $ciCtx = @{}
    if ($lastRemoval) { $ciCtx.removal_date = $lastRemoval.date; $ciCtx.wo = $lastRemoval.wo }
    $ciReasoning = Render $ci_code $ciCtx

    # --- Form 1 search ---
    $form1Hits = @()
    foreach ($p in $coPages) {
        if ($p.form1s -and $p.form1s.Count -gt 0) {
            foreach ($f in $p.form1s) {
                $doc = ResolveDoc $p.doc_id
                $form1Hits += [ordered]@{
                    form1_value = $f.value; kind = $f.kind; block_13_date = $f.block_13_date
                    document_id = $p.doc_id; document_path = $doc.path; page_number = ($p.idx + 1)
                    confidence = 'form1_binding'
                }
            }
        }
    }
    $form1Required = $false; $form1Outcome = $null; $form1Codes = @(); $form1Reasoning = $null
    if ($sn_value -eq $true) {
        $form1Required = $false; $form1Outcome = 'not_required_since_new'; $form1Codes = @('form1_not_required_since_new')
        $form1Reasoning = Render 'form1_not_required_since_new' @{}
    } elseif ($sn_value -eq $false) {
        $form1Required = $true
        if ($form1Hits.Count -gt 0) {
            $form1Outcome = 'form1_located_via_comention'; $form1Codes = @('form1_found_on_comention_page')
            $h = $form1Hits[0]
            $form1Reasoning = Render 'form1_found_on_comention_page' @{ kind=$h.kind; form1_value=$h.form1_value; block_13_date=$h.block_13_date; doc_path=$h.document_path }
        } else {
            $form1Outcome = 'dossier_gap_form1_missing'; $form1Codes = @('form1_not_in_dossier_post_oem')
            $form1Reasoning = Render 'form1_not_in_dossier_post_oem' @{ n_pages = $coPages.Count }
        }
    } else {
        $form1Outcome = 'unresolvable_anomaly'; $form1Codes = @('anomaly_removal_no_install')
        $form1Reasoning = Render 'anomaly_removal_no_install' @{}
    }

    # --- Mod / SB / STC / Repair / NDT search via co-mention pages ---
    $sbHits = @(); $modHits = @(); $stcHits = @(); $repHits = @(); $nrcHits = @(); $ndtHits = @(); $bsHits = @()
    foreach ($p in $coPages) {
        $doc = ResolveDoc $p.doc_id
        $citBase = @{ document_id = $p.doc_id; document_path = $doc.path; page_number = ($p.idx + 1); confidence = 'singleton_match' }
        if ($p.sbs) { foreach ($x in $p.sbs) { $sbHits += ($citBase + @{ sb_value = $x.value; sb_name = $x.name }) } }
        if ($p.mods) { foreach ($x in $p.mods) { $modHits += ($citBase + @{ mod_value = $x.value; mod_name = $x.name }) } }
        if ($p.stcs) { foreach ($x in $p.stcs) { $stcHits += ($citBase + @{ stc_value = $x.value; stc_name = $x.name }) } }
        if ($p.reps) { foreach ($x in $p.reps) { $repHits += ($citBase + @{ rep_value = $x.value; rep_name = $x.name }) } }
        if ($p.nrcs) { foreach ($x in $p.nrcs) { $nrcHits += ($citBase + @{ nrc_value = $x.value; nrc_name = $x.name }) } }
        if ($p.ndts) { foreach ($x in $p.ndts) { $ndtHits += ($citBase + @{ ndt_value = $x.value }) } }
        if ($p.bs_reps) { foreach ($x in $p.bs_reps) { $bsHits += ($citBase + @{ bs_value = $x.value }) } }
    }

    # --- Overhaul history ---
    $overhaulOutcome = $null; $overhaulCodes = @(); $overhaulReasoning = $null
    $overhaulEntries = @()
    if ($shopVisits.Count -gt 0) {
        foreach ($sv in $shopVisits) {
            $overhaulEntries += [ordered]@{
                date = $sv.date; description = (ExcerptFn $sv.descr); work_order = $sv.wo
                confidence = 'event_affected'
                citation = (MakeCitation $sv.pages[0] "Shop visit on $($sv.date)" $sv.descr)
            }
        }
        $overhaulOutcome = 'overhaul_events_present'; $overhaulCodes = @('overhaul_event_recorded')
        $overhaulReasoning = Render 'overhaul_event_recorded' @{ date = $shopVisits[0].date; wo = $shopVisits[0].wo }
    } else {
        if ($sn_value -eq $true) {
            $overhaulOutcome = 'not_applicable_since_new'; $overhaulCodes = @('never_overhauled_since_new')
            $overhaulReasoning = Render 'never_overhauled_since_new' @{}
        } else {
            $overhaulOutcome = 'no_overhaul_records'; $overhaulCodes = @('no_overhaul_records')
            $overhaulReasoning = Render 'no_overhaul_records' @{}
        }
    }

    # --- Repair history ---
    $repairOutcome = $null; $repairCodes = @(); $repairReasoning = $null
    if ($repHits.Count -gt 0 -or $nrcHits.Count -gt 0 -or $ndtHits.Count -gt 0 -or $bsHits.Count -gt 0) {
        $repairOutcome = 'repair_evidence_present'; $repairCodes = @('repair_evidence_found')
        $hit = if ($repHits.Count -gt 0) { $repHits[0] } elseif ($nrcHits.Count -gt 0) { $nrcHits[0] } else { @{ document_path='unknown'; page_number=0 } }
        $repairReasoning = Render 'repair_evidence_found' @{ doc_path = $hit.document_path; page = $hit.page_number }
    } else {
        $repairOutcome = 'no_repair_records'; $repairCodes = @('no_repair_records')
        $repairReasoning = Render 'no_repair_records' @{}
    }

    # --- Modification history ---
    $modOutcome = $null; $modCodes = @(); $modReasoning = $null
    $modEntries = @()
    if ($sbHits.Count -gt 0 -or $modHits.Count -gt 0 -or $stcHits.Count -gt 0) {
        $modOutcome = 'modifications_present'; $modCodes = @()
        if ($sbHits.Count -gt 0) {
            $modCodes += 'sb_referenced'
            foreach ($h in $sbHits) { $modEntries += [ordered]@{ type='service_bulletin'; value=$h.sb_value; name=$h.sb_name; citation=$h } }
        }
        if ($modHits.Count -gt 0) {
            $modCodes += 'mod_referenced'
            foreach ($h in $modHits) { $modEntries += [ordered]@{ type='modification'; value=$h.mod_value; name=$h.mod_name; citation=$h } }
        }
        if ($stcHits.Count -gt 0) {
            $modCodes += 'stc_referenced'
            foreach ($h in $stcHits) { $modEntries += [ordered]@{ type='stc'; value=$h.stc_value; name=$h.stc_name; citation=$h } }
        }
        $modReasoning = ($modCodes | ForEach-Object { Render $_ @{ sb_value=($sbHits[0].sb_value); mod_value=($modHits[0].mod_value); stc_value=($stcHits[0].stc_value) } }) -join ' '
    } else {
        $modOutcome = 'no_modifications_recorded'; $modCodes = @('no_modifications_recorded')
        $modReasoning = Render 'no_modifications_recorded' @{}
    }

    # --- Totals ---
    $tsnObj = $null; $csnObj = $null
    if ($sn_value -eq $true) {
        $tsnObj = [ordered]@{
            value = $assetSnapshot.airframe_tsn.value; unit = 'h'; method = 'calculated'
            outcome = 'tsn_calc_since_new'
            reasoning = Render 'tsn_calc_since_new' @{ value = $assetSnapshot.airframe_tsn.value }
            computation = "asset_snapshot.airframe_tsn ($($assetSnapshot.airframe_tsn.value)) - 0:00 (since-new)"
            inputs = @('asset_snapshot.airframe_tsn')
        }
        if ($cycCtr -eq 'airframe') {
            $csnObj = [ordered]@{
                value = $assetSnapshot.airframe_csn.value; unit = 'cy'; method = 'calculated'
                outcome = 'csn_calc_since_new_airframe'
                reasoning = Render 'csn_calc_since_new_airframe' @{ value = $assetSnapshot.airframe_csn.value; ata = $ata }
            }
        } elseif ($cycCtr -eq 'engine') {
            $csnObj = [ordered]@{
                value = $null; unit = 'cy'; method = 'unresolvable'
                outcome = 'csn_engine_counter_unavailable'
                reasoning = Render 'csn_engine_counter_unavailable' @{ ata = $ata }
            }
        } else {
            $csnObj = [ordered]@{
                value = $null; unit = 'cy'; method = 'unresolvable'
                outcome = 'csn_apu_counter_unavailable'
                reasoning = Render 'csn_apu_counter_unavailable' @{ ata = $ata }
            }
        }
    } else {
        $tsnObj = [ordered]@{ value = $null; method = 'unresolvable'; outcome = 'tsn_unresolvable_post_oem'; reasoning = "Post-OEM installation: TSN requires the install AC_TSN plus current AC_TSN. Install AC_TSN is not stamped in the install entry." }
        $csnObj = [ordered]@{ value = $null; method = 'unresolvable'; outcome = 'csn_unresolvable_post_oem'; reasoning = "Same as TSN." }
    }

    # --- Scope signals ---
    $scopeSig = @()
    if ($is_llp -eq $true) { $scopeSig += 'is_llp' }
    if ($is_overhaul -eq $true) { $scopeSig += 'is_overhaul' }
    if ($pis -and $pis.Count -gt 0) { $scopeSig += 'has_priority_item' }
    if ($has_evidence) { $scopeSig += 'has_evidence' }

    # --- installation_history ---
    $history = @()
    foreach ($e in $installs) {
        $history += [ordered]@{
            kind = 'install'; date = $e.date; work_order = $e.wo
            ac_tsn_at_event = (ParseTSN $e.descr); ac_csn_at_event = (ParseCSN $e.descr)
            description = (ExcerptFn $e.descr)
            citation = (MakeCitation $e.pages[0] "Install on $($e.date)" $e.descr 'event_affected')
        }
    }
    foreach ($e in $removals) {
        $history += [ordered]@{
            kind = 'removal'; date = $e.date; work_order = $e.wo
            ac_tsn_at_event = (ParseTSN $e.descr); ac_csn_at_event = (ParseCSN $e.descr)
            description = (ExcerptFn $e.descr)
            citation = (MakeCitation $e.pages[0] "Removal on $($e.date)" $e.descr 'event_affected')
        }
    }

    # --- finding_ids (resolved later) ---
    $rawFindings = @()
    if ($stage1.findingsByComp.PSObject.Properties.Name -contains $cv) {
        $rawFindings = $stage1.findingsByComp.$cv
    }

    # Compose record
    $rec = [ordered]@{
        id = "comp::$cv"; graph_id = $cv
        identity = [ordered]@{
            part_number = $pn; serial_number = $sn
            description = $row[4]; name = $row[9]
            ata_chapter = $ata; component_category = $cat
            cycle_counter = $cycCtr
            is_llp = ($is_llp -eq $true); is_overhaul_tracked = ($is_overhaul -eq $true)
            status = $status; priority_items = $pis
        }
        scope_signals = $scopeSig
        installed_since_new = [ordered]@{
            value = $sn_value; method = $sn_method; outcome = $sn_code
            rationale_codes = @($sn_code); reasoning = $snReasoning
            citation = $sn_cit
            recommended_documents = $(if ($sn_method -eq 'inferred') { @("Aircraft serialization listing entry for S/N $sn") } else { @() })
        }
        currently_installed = [ordered]@{
            value = $currentlyInstalled; outcome = $ci_code
            reasoning = $ciReasoning
        }
        first_install_date = $(
            if ($firstInstall) {
                $fiPage = $null
                if ($firstInstall.pages -and @($firstInstall.pages).Count -gt 0) { $fiPage = @($firstInstall.pages)[0] }
                [ordered]@{
                    value=$firstInstall.date
                    method='confirmed'
                    outcome='install_event_recorded'
                    reasoning=(Render 'install_event_recorded' @{date=$firstInstall.date; wo=$firstInstall.wo})
                    citation=(MakeCitation $fiPage "Install on $($firstInstall.date)" $firstInstall.descr 'event_affected')
                }
            } elseif ($sn_value -eq $true) {
                [ordered]@{
                    value=$assetSnapshot.aircraft_manufacture_date.value
                    method='inferred'
                    outcome='derived_from_asset_manufacture_date'
                    reasoning="Inherits from aircraft manufacture date (since-new defaulted)."
                }
            } else {
                [ordered]@{ value=$null; method='unresolvable'; outcome='anomaly_removal_no_install'; reasoning=(Render 'anomaly_removal_no_install' @{}) }
            }
        )
        first_install_ac_tsn = $(if ($sn_value -eq $true) {
            [ordered]@{ value='0:00'; method='inferred'; outcome='since_new_rule_zero'; reasoning="Since-new installation: TSN at install is 0:00 by definition." }
        } else {
            [ordered]@{ value=$null; method='unresolvable'; outcome='tsn_csn_not_recorded'; reasoning="The install entry does not stamp AC TSN." }
        })
        first_install_ac_csn = $(if ($sn_value -eq $true) {
            [ordered]@{ value=0; method='inferred'; outcome='since_new_rule_zero'; reasoning="Since-new installation: CSN at install is 0 by definition." }
        } else {
            [ordered]@{ value=$null; method='unresolvable'; outcome='tsn_csn_not_recorded'; reasoning="The install entry does not stamp AC CSN." }
        })
        form_1 = [ordered]@{
            required = $form1Required; outcome = $form1Outcome
            rationale_codes = $form1Codes; reasoning = $form1Reasoning
            hits = $form1Hits
        }
        overhaul_history = [ordered]@{
            outcome = $overhaulOutcome; rationale_codes = $overhaulCodes; reasoning = $overhaulReasoning
            entries = $overhaulEntries
        }
        repair_history = [ordered]@{
            outcome = $repairOutcome; rationale_codes = $repairCodes; reasoning = $repairReasoning
            entries = $(@($repHits + $nrcHits + $ndtHits + $bsHits))
        }
        modification_history = [ordered]@{
            outcome = $modOutcome; rationale_codes = $modCodes; reasoning = $modReasoning
            entries = $modEntries
        }
        totals = [ordered]@{
            current_tsn = $tsnObj; current_csn = $csnObj
            current_tso = $(if ($sn_value -eq $true) {
                [ordered]@{ value=$null; method='not_applicable'; outcome='never_overhauled_since_new'; reasoning=(Render 'never_overhauled_since_new' @{}) }
            } else {
                [ordered]@{ value=$null; method='unresolvable'; outcome='depends_on_overhaul_data'; reasoning="Cannot compute: no shop-visit data available for this S/N." }
            })
            current_cso = $(if ($sn_value -eq $true) {
                [ordered]@{ value=$null; method='not_applicable'; outcome='never_overhauled_since_new'; reasoning=(Render 'never_overhauled_since_new' @{}) }
            } else {
                [ordered]@{ value=$null; method='unresolvable'; outcome='depends_on_overhaul_data'; reasoning="Same as TSO." }
            })
            calendar_months_since_new = [ordered]@{ value=$null; method='calculated'; outcome='requires_real_manufacture_date'; reasoning="Requires confirmed aircraft manufacture date; currently inferred." }
            remaining_until_overhaul = [ordered]@{ value=$null; method='unresolvable'; outcome='mpd_not_provided'; reasoning=(Render 'mpd_not_provided_with_dossier' @{}) }
            remaining_until_scrap    = [ordered]@{ value=$null; method='unresolvable'; outcome='mpd_not_provided'; reasoning=(Render 'mpd_not_provided_with_dossier' @{}) }
        }
        installation_history = $history
        finding_graph_ids = @($rawFindings | ForEach-Object { $_.gid })
        co_mention_pages = $coPages.Count
        evidence_pages_total = $eventPageDocs.Count
        undated_install_events = $undatedInstalls.Count
        data_quality = [ordered]@{
            anomaly_removal_without_install = $anomaly
            parent_assembly = "unresolved"
            parent_assembly_reasoning = "The aircraft serialization listing showing this part's parent assembly fit is not ingested as structured data; parent-assembly binding cannot be derived from graph state alone."
            since_new_confirmed_by_serialization_listing = $false
            mention_writer_gap = $(if ($mentionCoPagesCount -eq 0 -and $events.Count -gt 0) {
                "The PN/SN mention writer did not record co-mention edges for this part, despite event evidence showing the PN+SN appears on multiple dossier pages. Form 1 / SB / Mod search via co-mention is therefore limited; reasoning instead falls back to event-attached evidence pages."
            } else { $null })
            undated_events_in_graph = $(if ($undatedInstalls.Count -gt 0) {
                "$($undatedInstalls.Count) install event(s) attached to this S/N have no recorded date in the dossier; these are excluded from history calculations but may indicate misattribution by the event extractor."
            } else { $null })
        }
    }
    return $rec
}

# ============================================================================
# Build all records
# ============================================================================
$currentlyInstalledComps = New-Object System.Collections.ArrayList
$historicalComps = New-Object System.Collections.ArrayList
$allFindings = @{}   # gid -> short id mapping
$i = 0
foreach ($cv in $stage1.meta.PSObject.Properties.Name) {
    $i++
    if (($i % 100) -eq 0) { Write-Host "  built $i / $($stage1.meta.PSObject.Properties.Name.Count)  elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s" }
    $rec = BuildComponentRecord $cv
    if ($rec.currently_installed.value -eq $false) {
        [void]$historicalComps.Add($rec)
    } else {
        [void]$currentlyInstalledComps.Add($rec)
    }
    # Collect findings
    foreach ($f in $stage1.findingsByComp.$cv) {
        if ($f.gid -and -not $allFindings.ContainsKey($f.gid)) {
            $allFindings[$f.gid] = $f
        }
    }
}
"Built $($currentlyInstalledComps.Count + $historicalComps.Count) records: $($currentlyInstalledComps.Count) currently installed + $($historicalComps.Count) historical"
"Findings collected: $($allFindings.Count)"
"Build elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"

# ============================================================================
# Build findings list (auto-downgrade FORM1_MISSING for since-new)
# ============================================================================
$allCompsByGid = @{}
foreach ($c in $currentlyInstalledComps) {
    foreach ($g in $c.finding_graph_ids) { if ($g) { $allCompsByGid[$g] = $c } }
}
foreach ($c in $historicalComps) {
    foreach ($g in $c.finding_graph_ids) { if ($g) { $allCompsByGid[$g] = $c } }
}
$findingsOut = New-Object System.Collections.ArrayList
$fidx = 0
$f_downgraded = 0; $f_kept = 0
foreach ($gid in $allFindings.Keys) {
    $f = $allFindings[$gid]
    $shortId = "F-{0:D5}" -f $fidx; $fidx++
    $comp = $allCompsByGid[$gid]
    $sinceNew = ($comp -and $comp.installed_since_new.value -eq $true)
    $isF1 = ($f.category -eq 'FORM1_MISSING')
    $sev = $f.severity
    $downgrade = $null
    if ($sinceNew -and $isF1) {
        $sev = 'level_3'
        $downgrade = @{ from = $f.severity; to = 'level_3'; reason = "Component installed since-new per Lukas FCC No. 1 rule - no Form 1 required." }
        $f_downgraded++
    } else {
        $f_kept++
    }
    [void]$findingsOut.Add([ordered]@{
        id = $shortId; graph_id = $gid; severity = $sev; category = $f.category
        title = $f.title; description = (ExcerptFn $f.description 280)
        recommended_action = $f.recommended_action
        affected_component_graph_id = $(if ($comp) { $comp.graph_id } else { $null })
        lukas_rule_downgrade = $downgrade
    })
}
"Findings: $($findingsOut.Count) total ($f_downgraded downgraded, $f_kept kept)"

# Map gid -> shortId so we can replace finding_graph_ids in comp records
$fidMap = @{}
foreach ($f in $findingsOut) { $fidMap[$f.graph_id] = $f.id }
foreach ($c in @($currentlyInstalledComps + $historicalComps)) {
    $c.finding_graph_ids = @($c.finding_graph_ids | ForEach-Object { $fidMap[$_] } | Where-Object { $_ })
}

# ============================================================================
# Write JSONL
# ============================================================================
$outPath = "D:\work\openclaude\dumps\jsonc\component_history_v4.jsonl"
$out = [System.IO.StreamWriter]::new($outPath, $false, [System.Text.UTF8Encoding]::new($false))

$stats = [ordered]@{
    total_components = $currentlyInstalledComps.Count + $historicalComps.Count
    currently_installed = $currentlyInstalledComps.Count
    historical = $historicalComps.Count
    since_new_confirmed = (@($currentlyInstalledComps | Where-Object { $_.installed_since_new.method -eq 'confirmed' -and $_.installed_since_new.value -eq $true })).Count
    since_new_inferred = (@($currentlyInstalledComps | Where-Object { $_.installed_since_new.method -eq 'inferred' })).Count
    real_post_oem = (@($currentlyInstalledComps | Where-Object { $_.installed_since_new.value -eq $false })).Count
    findings_total = $findingsOut.Count
    findings_downgraded_since_new = $f_downgraded
    findings_kept = $f_kept
    documents_referenced = $docPathMap.Count
}

$header = [ordered]@{
    type = "header"; schema_version = "component_history_v4"
    generated_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    asset_id = "CL650-6134"
    note = "Per-component back-to-birth history report. Lukas rules applied: since-new defaulting, Form 1 not-required for since-new, cycle-counter classified by ATA. Layered confidence model for evidence (form1_binding > event_affected > singleton_match > stamp_context > proximity). Reasoning rendered from structured outcome codes in dossier/auditor POV."
    asset_snapshot = $assetSnapshot
    scope_methodology = "Auto-derived from graph signals: is_llp OR is_overhaul OR has_priority_item OR has_evidence (events|findings). In a Lukas/W5 production workflow this set would be replaced by a customer-supplied parts list."
    stats = $stats
}
$out.WriteLine(($header | ConvertTo-Json -Depth 30 -Compress))

# Documents
foreach ($docId in $docPathMap.Keys) {
    $d = $docPathMap[$docId]
    $rec = [ordered]@{ type = "document"; id = $docId; original_path = $d.path; name = $d.name }
    $out.WriteLine(($rec | ConvertTo-Json -Depth 10 -Compress))
}

# Findings
foreach ($f in $findingsOut) {
    $rec = [ordered]@{ type = "finding" } + $f
    # PowerShell hash + ordered concat doesn't work directly; rebuild
    $newrec = [ordered]@{ type = "finding" }
    foreach ($k in $f.Keys) { $newrec[$k] = $f[$k] }
    $out.WriteLine(($newrec | ConvertTo-Json -Depth 10 -Compress))
}

# Components
foreach ($c in $currentlyInstalledComps) {
    $rec = [ordered]@{ type = "component_currently_installed" }
    foreach ($k in $c.Keys) { $rec[$k] = $c[$k] }
    $out.WriteLine(($rec | ConvertTo-Json -Depth 30 -Compress))
}
foreach ($c in $historicalComps) {
    $rec = [ordered]@{ type = "component_historical" }
    foreach ($k in $c.Keys) { $rec[$k] = $c[$k] }
    $out.WriteLine(($rec | ConvertTo-Json -Depth 30 -Compress))
}

$out.Flush(); $out.Close()
$fi = Get-Item $outPath
$lc = (Get-Content $outPath | Measure-Object -Line).Lines
"=== WROTE $outPath ==="
"size: {0:N2} MB, lines: {1}" -f ($fi.Length/1MB), $lc
"total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
""
"=== Final stats ==="
$stats.GetEnumerator() | ForEach-Object { "  {0,-40}  {1}" -f $_.Key, $_.Value }
