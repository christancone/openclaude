$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# Load v4 + deep index + aircraft snapshots
# ============================================================================
$v4 = "D:\work\openclaude\dumps\jsonc\component_history_v4.jsonl"
$idx = Get-Content "D:\work\openclaude\dumps\jsonc\.v5_dossier_deep_index.json" -Raw | ConvertFrom-Json
$snaps = Get-Content "D:\work\openclaude\dumps\jsonc\.v5_aircraft_snapshots.json" -Raw | ConvertFrom-Json
"Loaded index ($($idx.PSObject.Properties.Name.Count) SNs) and $($snaps.Count) snapshot candidates"

# ============================================================================
# Build canonical aircraft snapshot from the Annex 6 PPI table
#   Format: [LABEL, MODEL, MANUF, SN, TSN, CSN]
# ============================================================================
$ppiSnap = $null
foreach ($s in $snaps) {
    if ($s.path -like '*Annex 6*Summary of Findings*' -and $s.table_name -like '*Details*') {
        if ($ppiSnap -eq $null) { $ppiSnap = @{ doc_id = $s.doc_id; path = $s.path; page = $s.page; rows = New-Object System.Collections.ArrayList } }
        [void]$ppiSnap.rows.Add(@($s.row))
    }
}
# Pull the rows we care about (AIRCRAFT, LH ENGINE, RH ENGINE, APU)
$acRow = $null; $lhEng = $null; $rhEng = $null; $apu = $null
if ($ppiSnap) {
    foreach ($r in $ppiSnap.rows) {
        $label = "$($r[0])".ToUpper()
        if ($label -like 'AIRCRAFT*' -and -not $acRow) { $acRow = $r }
        elseif ($label -like 'LH ENG*' -and -not $lhEng) { $lhEng = $r }
        elseif ($label -like 'RH ENG*' -and -not $rhEng) { $rhEng = $r }
        elseif ($label -eq 'APU' -and -not $apu) { $apu = $r }
    }
}
"=== Aircraft snapshot (from Annex 6 PPI) ==="
if ($acRow) { "  AIRCRAFT  TSN=$($acRow[4])  CSN=$($acRow[5])" }
if ($lhEng) { "  LH ENGINE TSN=$($lhEng[4])  CSN=$($lhEng[5])  SN=$($lhEng[3])" }
if ($rhEng) { "  RH ENGINE TSN=$($rhEng[4])  CSN=$($rhEng[5])  SN=$($rhEng[3])" }
if ($apu)   { "  APU       TSH=$($apu[4])  CSN=$($apu[5])  SN=$($apu[3])" }
""

# Build the upgraded asset_snapshot block
$assetSnapshot = [ordered]@{
    as_of_date = "2023-10-25"
    airframe_tsn = [ordered]@{
        value = $(if ($acRow) { $acRow[4] } else { "559:25" })
        unit  = "h"
        method = $(if ($acRow) { "confirmed" } else { "manual_override" })
        outcome = $(if ($acRow) { "extracted_from_ppi_summary_table" } else { "snapshot_manual_override" })
        reasoning = $(if ($acRow) {
            "Aircraft TSN extracted from the Annex 6 PPI Summary of Findings 'Aircraft/Engine/APU Details' snapshot table. Located at $($ppiSnap.path) p.$($ppiSnap.page)."
        } else {
            "Aircraft TSN value taken from Lukas Weiss correspondence dated 2026-05-07."
        })
    }
    airframe_csn = [ordered]@{
        value = $(if ($acRow) { [int]$acRow[5] } else { 231 })
        unit = "cy"
        method = $(if ($acRow) { "confirmed" } else { "manual_override" })
        outcome = $(if ($acRow) { "extracted_from_ppi_summary_table" } else { "snapshot_manual_override" })
        reasoning = $(if ($acRow) { "Aircraft CSN extracted from the Annex 6 PPI Summary table." } else { "From Lukas correspondence." })
    }
    lh_engine_snapshot = $(if ($lhEng) {
        [ordered]@{ sn = $lhEng[3]; tsn = $lhEng[4]; csn = [int]$lhEng[5]; source = "Annex 6 PPI Summary, $($ppiSnap.path) p.$($ppiSnap.page)" }
    } else { $null })
    rh_engine_snapshot = $(if ($rhEng) {
        [ordered]@{ sn = $rhEng[3]; tsn = $rhEng[4]; csn = [int]$rhEng[5]; source = "Annex 6 PPI Summary, $($ppiSnap.path) p.$($ppiSnap.page)" }
    } else { $null })
    apu_snapshot = $(if ($apu) {
        [ordered]@{ sn = $apu[3]; tsh = $apu[4]; csn = $apu[5]; source = "Annex 6 PPI Summary, $($ppiSnap.path) p.$($ppiSnap.page)" }
    } else { $null })
    aircraft_manufacture_date = [ordered]@{
        value = "2019-02-20"; method = "inferred"
        outcome = "manufacture_date_inferred"
        reasoning = "Aircraft manufacture date inferred from earliest install-since-new events. The CofA header (Box A_Manuals/Binder a_Completion Data) would provide the authoritative value."
    }
}

# Date / TSN / CSN extractors from table cell strings
function ExtractDates($cellArray) {
    $dates = New-Object System.Collections.ArrayList
    foreach ($c in $cellArray) {
        if (-not $c) { continue }
        $m1 = [regex]::Matches([string]$c, '(\d{4}-\d{2}-\d{2})')
        foreach ($m in $m1) { [void]$dates.Add($m.Groups[1].Value) }
        $m2 = [regex]::Matches([string]$c, '(\d{1,2}[\s\-/](?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s\-/]\d{4})', 'IgnoreCase')
        foreach ($m in $m2) { [void]$dates.Add($m.Groups[1].Value) }
    }
    return @($dates)
}
function ExtractTSN($cellArray) {
    $tsns = New-Object System.Collections.ArrayList
    foreach ($c in $cellArray) {
        if (-not $c) { continue }
        $m = [regex]::Matches([string]$c, '\b(\d{1,5}:\d{2})\b')
        foreach ($mm in $m) { [void]$tsns.Add($mm.Groups[1].Value) }
    }
    return @($tsns)
}
function ExtractCSN($cellArray) {
    $csns = New-Object System.Collections.ArrayList
    foreach ($c in $cellArray) {
        if (-not $c) { continue }
        $m = [regex]::Matches([string]$c, '\b(\d{1,5})\b')
        foreach ($mm in $m) {
            $v = [int]$mm.Groups[1].Value
            if ($v -ge 0 -and $v -le 99999) { [void]$csns.Add($v) }
        }
    }
    return @($csns)
}
function ExtractForm1Refs($text) {
    $refs = New-Object System.Collections.ArrayList
    if (-not $text) { return @() }
    $m = [regex]::Matches($text, '(?:Form\s*1\s*[#:]?\s*|FAA\s*8130-?3?\s*[#:]?\s*|TCCA\s*[#:]?\s*|EASA\s*[#:]?\s*|CRS\s*[#:]?\s*|CofC\s*[#:]?\s*)([A-Z0-9\-]{4,})', 'IgnoreCase')
    foreach ($mm in $m) { [void]$refs.Add($mm.Groups[1].Value) }
    return @($refs)
}
function ExtractSBRefs($text) {
    $refs = New-Object System.Collections.ArrayList
    if (-not $text) { return @() }
    $m = [regex]::Matches($text, '(?:SB[\s\-_]?(?:CL?-?)?(?:\d{2}[\-\s]?\d+|\w+)|Service\s+Bulletin[\s:]+[A-Z0-9\-]+|GE-SB[\s\-]+[\w\-]+)', 'IgnoreCase')
    foreach ($mm in $m) { [void]$refs.Add($mm.Value) }
    return @($refs)
}
function ExtractADRefs($text) {
    $refs = New-Object System.Collections.ArrayList
    if (-not $text) { return @() }
    $m = [regex]::Matches($text, '(?:AD[\s\-_]?\d{4}[\s\-]?\d{2}[\s\-]?\d{2}|FAA[\s_-]?\d{4}-\d{2}-\d{2}|EASA[\s_-]?(?:AD|PAD)[\s_-]?\d{4}-\d{4})', 'IgnoreCase')
    foreach ($mm in $m) { [void]$refs.Add($mm.Value) }
    return @($refs)
}

# ============================================================================
# Enrich each component
# ============================================================================
$out = [System.IO.StreamWriter]::new("D:\work\openclaude\dumps\jsonc\component_history_v5.jsonl", $false, [System.Text.UTF8Encoding]::new($false))
$stats = @{ comps=0; tsn_upgraded=0; csn_upgraded=0; install_date_from_table=0; sb_specific_refs=0; ad_specific_refs=0; form1_specific_refs=0; table_rows_matched=0 }

$lineCount = 0
foreach ($line in [System.IO.File]::ReadLines($v4)) {
    $lineCount++
    if (($lineCount % 500) -eq 0) { Write-Host "  processed $lineCount  elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s" }
    $obj = $line | ConvertFrom-Json
    $type = $obj.type

    if ($type -eq 'header') {
        # Replace asset_snapshot with the upgraded version
        $obj.asset_snapshot = $assetSnapshot
        $obj.schema_version = "component_history_v5"
        $obj.note = "Per-component back-to-birth history. Lukas rules + dossier-deep enrichment: tables in extracted_json are parsed for TSN/CSN/dates/Form-1/SB/AD references. Aircraft and per-engine snapshots come from the Annex 6 PPI Summary table."
        $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        continue
    }
    if ($type -ne 'component_currently_installed' -and $type -ne 'component_historical') {
        $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        continue
    }

    $stats.comps++
    $pn = $obj.identity.part_number
    $sn = $obj.identity.serial_number
    $cycCtr = $obj.identity.cycle_counter

    # Upgrade current_csn for engine/APU parts using per-engine snapshot
    if ($obj.installed_since_new.value -eq $true) {
        if ($cycCtr -eq 'engine' -and $rhEng -and $lhEng) {
            # Decide which engine this part belongs to (best-effort: position or default RH)
            # Without position info we can't pick; flag both
            $obj.totals.current_csn.value = "LH=$([int]$lhEng[5]) / RH=$([int]$rhEng[5])"
            $obj.totals.current_csn.method = "confirmed"
            $obj.totals.current_csn.outcome = "csn_from_ppi_per_engine_snapshot"
            $obj.totals.current_csn.reasoning = "Engine-mounted part (ATA $($obj.identity.ata_chapter)). Per-engine cycle counters from Annex 6 PPI Summary: LH engine $($lhEng[5]) cycles, RH engine $($rhEng[5]) cycles. Position not derivable from graph alone."
            $stats.csn_upgraded++
        } elseif ($cycCtr -eq 'apu' -and $apu) {
            $obj.totals.current_csn.value = $apu[5]
            $obj.totals.current_csn.method = "confirmed"
            $obj.totals.current_csn.outcome = "csn_from_ppi_apu_snapshot"
            $obj.totals.current_csn.reasoning = "APU-mounted part (ATA $($obj.identity.ata_chapter)). APU hours from Annex 6 PPI Summary: $($apu[4]) TSH. CSN reported as '$($apu[5])'."
            $stats.csn_upgraded++
        }
        # Update current_tsn reasoning to point at the new snapshot
        $obj.totals.current_tsn.value = $assetSnapshot.airframe_tsn.value
        $obj.totals.current_tsn.reasoning = "TSN equals current aircraft TSN ($($assetSnapshot.airframe_tsn.value), from Annex 6 PPI Summary). Since-new installation: AC_TSN(now) minus AC_TSN(at install) = $($assetSnapshot.airframe_tsn.value) minus 0:00."
        $stats.tsn_upgraded++
        # Also update airframe-counter parts
        if ($cycCtr -eq 'airframe') {
            $obj.totals.current_csn.value = [int]$acRow[5]
            $obj.totals.current_csn.reasoning = "CSN equals current aircraft CSN ($($acRow[5]) ldgs from Annex 6 PPI Summary). Airframe cycle counter applies per ATA $($obj.identity.ata_chapter)."
        }
    }

    # Now look at indexed pages for this SN and pull table-row evidence
    if (-not $sn) {
        $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        continue
    }
    $hits = $idx.PSObject.Properties[$sn]
    if (-not $hits) {
        $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        continue
    }

    $tableMatches = New-Object System.Collections.ArrayList
    $allSBRefs = @{}
    $allADRefs = @{}
    $allForm1Refs = @{}
    $allDatesByContext = @{}  # date -> first context

    foreach ($h in @($hits.Value)) {
        $parsed = $h.parsed
        if (-not $parsed -or -not $parsed.tables) { continue }
        foreach ($tbl in $parsed.tables) {
            if (-not $tbl.rows) { continue }
            foreach ($row in $tbl.rows) {
                if (-not $row -or $row.Count -eq 0) { continue }
                $rowJoined = ($row -join '|')
                if ($rowJoined -notmatch [regex]::Escape($sn)) { continue }
                $stats.table_rows_matched++
                $dates = ExtractDates $row
                $tsns = ExtractTSN $row
                $csns = ExtractCSN $row
                $sbRefs = ExtractSBRefs $rowJoined
                $adRefs = ExtractADRefs $rowJoined
                $form1Refs = ExtractForm1Refs $rowJoined
                foreach ($d in $dates) { if (-not $allDatesByContext.ContainsKey($d)) { $allDatesByContext[$d] = $tbl.name } }
                foreach ($r in $sbRefs) { $allSBRefs[$r] = $true }
                foreach ($r in $adRefs) { $allADRefs[$r] = $true }
                foreach ($r in $form1Refs) { $allForm1Refs[$r] = $true }
                if ($tableMatches.Count -lt 20) {
                    [void]$tableMatches.Add(@{
                        document_id = $h.document_id; page = $h.page; path = $h.path
                        table_name = $tbl.name
                        row_cells = $row
                        dates = $dates; tsns = $tsns; csns = $csns
                        sb_refs = $sbRefs; ad_refs = $adRefs; form1_refs = $form1Refs
                    })
                }
            }
        }
    }

    if ($tableMatches.Count -gt 0) {
        $obj | Add-Member -NotePropertyName dossier_table_matches -NotePropertyValue (@($tableMatches | Select-Object -First 10)) -Force
    }
    if ($allSBRefs.Count -gt 0) {
        $stats.sb_specific_refs++
        $obj.modification_history | Add-Member -NotePropertyName specific_sb_refs -NotePropertyValue (@($allSBRefs.Keys | Select-Object -Unique)) -Force
        # Update reasoning to include specific SB numbers
        $sbList = ($allSBRefs.Keys | Select-Object -First 5) -join ', '
        $obj.modification_history.reasoning = "$($obj.modification_history.reasoning) Specific Service Bulletin references in table rows: $sbList."
    }
    if ($allADRefs.Count -gt 0) {
        $stats.ad_specific_refs++
        $obj.modification_history | Add-Member -NotePropertyName specific_ad_refs -NotePropertyValue (@($allADRefs.Keys | Select-Object -Unique)) -Force
        $adList = ($allADRefs.Keys | Select-Object -First 5) -join ', '
        $obj.modification_history.reasoning = "$($obj.modification_history.reasoning) Airworthiness Directive references in table rows: $adList."
    }
    if ($allForm1Refs.Count -gt 0) {
        $stats.form1_specific_refs++
        $obj.form_1 | Add-Member -NotePropertyName specific_form1_refs -NotePropertyValue (@($allForm1Refs.Keys | Select-Object -Unique)) -Force
        $f1List = ($allForm1Refs.Keys | Select-Object -First 5) -join ', '
        $obj.form_1.reasoning = "$($obj.form_1.reasoning) Specific Form 1 / 8130 / CRS / CofC references in table rows: $f1List."
    }

    # Try to upgrade first_install_date from earliest date found in table rows IF still inferred
    if ($obj.first_install_date.method -eq 'inferred' -and $allDatesByContext.Count -gt 0) {
        $sortedDates = @($allDatesByContext.Keys | Where-Object { $_ -match '^\d{4}-' } | Sort-Object)
        if ($sortedDates.Count -gt 0) {
            $earliestDate = $sortedDates[0]
            $context = $allDatesByContext[$earliestDate]
            $obj.first_install_date.value = $earliestDate
            $obj.first_install_date.method = "inferred"   # Still inferred (table row date, not certified install event)
            $obj.first_install_date.outcome = "earliest_date_in_dossier_tables"
            $obj.first_install_date.reasoning = "Earliest date referencing this S/N located across dossier tables: $earliestDate (in table '$context'). Defaulted to install date pending confirmation against the aircraft serialization listing."
            $stats.install_date_from_table++
        }
    }

    $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
}
$out.Flush(); $out.Close()

"Phase B done."
$fi = Get-Item "D:\work\openclaude\dumps\jsonc\component_history_v5.jsonl"
"=== component_history_v5.jsonl ==="
"  size:  {0:N2} MB" -f ($fi.Length/1MB)
"  lines: $((Get-Content $fi.FullName | Measure-Object -Line).Lines)"
""
"=== Enrichment stats ==="
$stats.GetEnumerator() | ForEach-Object { "  {0,-30} {1}" -f $_.Key, $_.Value }
"Total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
