$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

$inPath = "D:\work\openclaude\dumps\jsonc\component_history_v4.jsonl"
$outPath = "D:\work\openclaude\dumps\jsonc\component_history_v4_enriched.jsonl"
$indexPath = "D:\work\openclaude\dumps\jsonc\.v4_dossier_index.json"

# Load dossier index
$idx = Get-Content $indexPath -Raw | ConvertFrom-Json
"Index loaded: $($idx.PSObject.Properties.Name.Count) SNs"

# Helpers
function ParseTSN($s) {
    if (-not $s) { return $null }
    $m = [regex]::Match($s, '(?:TSN[:\s=]+([\d]+(?:[:.]\d+)?))|(?:([\d]+(?:[:.]\d+)?)\s*(?:h\s*)?TSN)', 'IgnoreCase')
    if ($m.Success) {
        if ($m.Groups[1].Success) { return $m.Groups[1].Value }
        if ($m.Groups[2].Success) { return $m.Groups[2].Value }
    }
    $null
}
function ParseCSN($s) {
    if (-not $s) { return $null }
    $m = [regex]::Match($s, '(?:CSN[:\s=]+([\d]+))|(?:([\d]+)\s*CSN)|(?:([\d]+)\s*(?:ldg|landings?|cycles?))', 'IgnoreCase')
    if ($m.Success) {
        if ($m.Groups[1].Success) { return [int]$m.Groups[1].Value }
        if ($m.Groups[2].Success) { return [int]$m.Groups[2].Value }
        if ($m.Groups[3].Success) { return [int]$m.Groups[3].Value }
    }
    $null
}
function Excerpt($s, $n=180) {
    if (-not $s) { return $null }
    $s2 = $s -replace "[\r\n]+", " "
    if ($s2.Length -gt $n) { $s2.Substring(0,$n) + '...' } else { $s2 }
}

function EnrichComponent($obj) {
    $sn = $obj.identity.serial_number
    $pn = $obj.identity.part_number
    if (-not $sn) { return $obj }
    $hits = $idx.PSObject.Properties[$sn]
    if (-not $hits) { return $obj }
    $hitList = @($hits.Value)
    if ($hitList.Count -eq 0) { return $obj }

    # Aggregate dossier signals
    $form1Pages = New-Object System.Collections.ArrayList
    $sbPages    = New-Object System.Collections.ArrayList
    $modPages   = New-Object System.Collections.ArrayList
    $tsnPages   = New-Object System.Collections.ArrayList
    $installEvents = New-Object System.Collections.ArrayList
    $overhaulEvents = New-Object System.Collections.ArrayList
    $repairEvents   = New-Object System.Collections.ArrayList
    $modEvents      = New-Object System.Collections.ArrayList
    $allEvents      = New-Object System.Collections.ArrayList

    foreach ($h in $hitList) {
        $cit = [ordered]@{
            document_id = $h.document_id
            document_path = $h.original_path
            page_number = ($h.page_index + 1)
            title = $h.title
            confidence = 'proximity'
        }
        if ($h.signals.form1) { [void]$form1Pages.Add($cit) }
        if ($h.signals.sb)    { [void]$sbPages.Add($cit) }
        if ($h.signals.mod)   { [void]$modPages.Add($cit) }
        if ($h.signals.tsn)   { [void]$tsnPages.Add($cit) }
        foreach ($e in $h.events_extracted) {
            $ev = [ordered]@{
                date = $e.date; type = $e.type; description = (Excerpt $e.description 200)
                citation = $cit
                ac_tsn = (ParseTSN $e.description); ac_csn = (ParseCSN $e.description)
            }
            [void]$allEvents.Add($ev)
            $t = "$($e.type)".ToLower()
            $d = "$($e.description)".ToLower()
            if ($t -match 'install' -or $d -match '\binitial\s+installation\b|\binstalled\b') { [void]$installEvents.Add($ev) }
            elseif ($t -match 'overhaul|shop_visit' -or $d -match '\boverhaul') { [void]$overhaulEvents.Add($ev) }
            elseif ($t -match 'repair' -or $d -match '\brepair') { [void]$repairEvents.Add($ev) }
            elseif ($t -match 'mod|sb|stc' -or $d -match '\bmod\b|\bsb-|\bservice\s+bulletin') { [void]$modEvents.Add($ev) }
        }
    }

    # Enrich Form 1 outcome if since-new is False (post-OEM)
    if ($obj.installed_since_new.value -eq $false -and $form1Pages.Count -gt 0) {
        $obj.form_1.outcome = 'form1_located_in_dossier'
        $obj.form_1.rationale_codes = @('form1_found_on_comention_page')
        $top = $form1Pages | Select-Object -First 1
        $obj.form_1.reasoning = "Form 1 / release certificate evidence located on page $($top.page_number) of $($top.document_path). $($form1Pages.Count) dossier pages reference both this PN+SN and Form-1-class language (CRS / 8130 / EASA Form 1 / TCCA Form 1)."
        $obj.form_1 | Add-Member -NotePropertyName dossier_form1_pages -NotePropertyValue (@($form1Pages | Select-Object -First 5)) -Force
    } elseif ($form1Pages.Count -gt 0 -and $obj.installed_since_new.value -eq $true) {
        # Still since-new, but worth recording that Form 1 evidence exists (probably the initial OEM cert appearing in serialization listing)
        $obj.form_1 | Add-Member -NotePropertyName dossier_form1_pages -NotePropertyValue (@($form1Pages | Select-Object -First 3)) -Force
    }

    # Enrich modification_history if SB / Mod evidence
    if ($sbPages.Count -gt 0 -or $modPages.Count -gt 0) {
        $obj.modification_history.outcome = 'modifications_found_in_dossier'
        $obj.modification_history.rationale_codes = @('sb_or_mod_referenced_in_dossier')
        $top = if ($sbPages.Count -gt 0) { $sbPages[0] } else { $modPages[0] }
        $obj.modification_history.reasoning = "Modification / Service Bulletin references located in the dossier. SB pages: $($sbPages.Count); Mod/STC pages: $($modPages.Count). Sample reference: page $($top.page_number) of $($top.document_path)."
        $obj.modification_history | Add-Member -NotePropertyName dossier_sb_pages -NotePropertyValue (@($sbPages | Select-Object -First 5)) -Force
        $obj.modification_history | Add-Member -NotePropertyName dossier_mod_pages -NotePropertyValue (@($modPages | Select-Object -First 5)) -Force
        if ($modEvents.Count -gt 0) {
            $obj.modification_history | Add-Member -NotePropertyName events -NotePropertyValue (@($modEvents | Select-Object -First 10)) -Force
        }
    }

    # Enrich overhaul_history if overhaul events found
    if ($overhaulEvents.Count -gt 0) {
        $obj.overhaul_history.outcome = 'overhaul_events_in_dossier'
        $obj.overhaul_history.rationale_codes = @('overhaul_event_found_in_dossier')
        $top = $overhaulEvents | Select-Object -First 1
        $obj.overhaul_history.reasoning = "Overhaul event referenced in dossier. First entry: $($top.date) - $($top.description)."
        $obj.overhaul_history.entries = @($overhaulEvents | Select-Object -First 10)
    }

    # Enrich repair_history if repair events found
    if ($repairEvents.Count -gt 0) {
        $obj.repair_history.outcome = 'repair_events_in_dossier'
        $obj.repair_history.rationale_codes = @('repair_event_found_in_dossier')
        $top = $repairEvents | Select-Object -First 1
        $obj.repair_history.reasoning = "Repair / non-routine activity referenced in dossier. First entry: $($top.date) - $($top.description)."
        $obj.repair_history.entries = @($repairEvents | Select-Object -First 10)
    }

    # Enrich install events if no events were in graph but dossier has them
    if ($installEvents.Count -gt 0) {
        # Find the earliest install event with a date
        $datedInstalls = @($installEvents | Where-Object { $_.date })
        if ($datedInstalls.Count -gt 0) {
            $sorted = $datedInstalls | Sort-Object date
            $first = $sorted[0]
            # If first_install_date is currently inferred (from manufacture date), upgrade it
            if ($obj.first_install_date.method -eq 'inferred' -and $first.date) {
                $obj.first_install_date.value = $first.date
                $obj.first_install_date.method = 'confirmed'
                $obj.first_install_date.outcome = 'install_event_in_dossier'
                $obj.first_install_date.reasoning = "Installation entry located in dossier: $($first.date) - $($first.description). Source: page $($first.citation.page_number) of $($first.citation.document_path)."
                $obj.first_install_date | Add-Member -NotePropertyName citation -NotePropertyValue $first.citation -Force
            }
            # If we have parsed TSN/CSN, upgrade those too
            if ($first.ac_tsn -and $obj.first_install_ac_tsn.method -eq 'inferred') {
                $obj.first_install_ac_tsn.value = $first.ac_tsn
                $obj.first_install_ac_tsn.method = 'confirmed'
                $obj.first_install_ac_tsn.outcome = 'tsn_parsed_from_dossier_event'
                $obj.first_install_ac_tsn.reasoning = "TSN at install ($($first.ac_tsn)) parsed from dossier event description on page $($first.citation.page_number) of $($first.citation.document_path)."
            }
            if ($null -ne $first.ac_csn -and $obj.first_install_ac_csn.method -eq 'inferred') {
                $obj.first_install_ac_csn.value = $first.ac_csn
                $obj.first_install_ac_csn.method = 'confirmed'
                $obj.first_install_ac_csn.outcome = 'csn_parsed_from_dossier_event'
                $obj.first_install_ac_csn.reasoning = "CSN at install ($($first.ac_csn)) parsed from dossier event description on page $($first.citation.page_number) of $($first.citation.document_path)."
            }
            # Add a dossier_install_events array
            $obj | Add-Member -NotePropertyName dossier_install_events -NotePropertyValue (@($sorted | Select-Object -First 10)) -Force
        }
    }

    # Record overall enrichment stats on the component
    $obj | Add-Member -NotePropertyName dossier_evidence -NotePropertyValue ([ordered]@{
        pages_referencing_sn = $hitList.Count
        form1_signal_pages   = $form1Pages.Count
        sb_signal_pages      = $sbPages.Count
        mod_signal_pages     = $modPages.Count
        tsn_signal_pages     = $tsnPages.Count
        events_extracted     = $allEvents.Count
        install_events       = $installEvents.Count
        overhaul_events      = $overhaulEvents.Count
        repair_events        = $repairEvents.Count
        mod_events           = $modEvents.Count
    }) -Force

    # Update mention_writer_gap reasoning if dossier filled in
    if ($obj.data_quality.mention_writer_gap -and $hitList.Count -gt 0) {
        $obj.data_quality.mention_writer_gap = "$($obj.data_quality.mention_writer_gap) Dossier-side scan of the source CSV located $($hitList.Count) pages referencing this S/N; evidence above is sourced from those pages."
    }

    return $obj
}

# Stream input, write enriched output
$inFI = Get-Item $inPath
"Reading $inPath ({0:N2} MB)" -f ($inFI.Length/1MB)
$out = [System.IO.StreamWriter]::new($outPath, $false, [System.Text.UTF8Encoding]::new($false))

$stats = @{ headers=0; documents=0; findings=0; components=0; enriched=0; not_enriched=0 }
$enrichmentDelta = @{
    install_date_upgraded = 0
    tsn_upgraded = 0
    csn_upgraded = 0
    form1_located = 0
    mod_located = 0
    overhaul_located = 0
    repair_located = 0
}

foreach ($line in [System.IO.File]::ReadLines($inPath)) {
    $obj = $line | ConvertFrom-Json
    $type = $obj.type
    switch ($type) {
        'header'   { $stats.headers++; $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress)) }
        'document' { $stats.documents++; $out.WriteLine(($obj | ConvertTo-Json -Depth 10 -Compress)) }
        'finding'  { $stats.findings++; $out.WriteLine(($obj | ConvertTo-Json -Depth 10 -Compress)) }
        default {
            $stats.components++
            $before_install_date = $obj.first_install_date.method
            $before_tsn = $obj.first_install_ac_tsn.method
            $before_csn = $obj.first_install_ac_csn.method
            $before_form1 = $obj.form_1.outcome
            $before_mod = $obj.modification_history.outcome
            $before_oh = $obj.overhaul_history.outcome
            $before_rep = $obj.repair_history.outcome

            $obj = EnrichComponent $obj

            if ($obj.dossier_evidence -and $obj.dossier_evidence.pages_referencing_sn -gt 0) {
                $stats.enriched++
                if ($obj.first_install_date.method -ne $before_install_date) { $enrichmentDelta.install_date_upgraded++ }
                if ($obj.first_install_ac_tsn.method -ne $before_tsn) { $enrichmentDelta.tsn_upgraded++ }
                if ($obj.first_install_ac_csn.method -ne $before_csn) { $enrichmentDelta.csn_upgraded++ }
                if ($obj.form_1.outcome -ne $before_form1) { $enrichmentDelta.form1_located++ }
                if ($obj.modification_history.outcome -ne $before_mod) { $enrichmentDelta.mod_located++ }
                if ($obj.overhaul_history.outcome -ne $before_oh) { $enrichmentDelta.overhaul_located++ }
                if ($obj.repair_history.outcome -ne $before_rep) { $enrichmentDelta.repair_located++ }
            } else {
                $stats.not_enriched++
            }
            $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        }
    }
}
$out.Flush(); $out.Close()

$fi = Get-Item $outPath
"=== Enriched file written ==="
"  path:  $outPath"
"  size:  {0:N2} MB" -f ($fi.Length/1MB)
"  lines: $((Get-Content $outPath | Measure-Object -Line).Lines)"
""
"=== Stats ==="
$stats.GetEnumerator() | ForEach-Object { "  {0,-30} {1}" -f $_.Key, $_.Value }
""
"=== Enrichment deltas (components where field upgraded after dossier scan) ==="
$enrichmentDelta.GetEnumerator() | ForEach-Object { "  {0,-30} {1}" -f $_.Key, $_.Value }
"Total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
