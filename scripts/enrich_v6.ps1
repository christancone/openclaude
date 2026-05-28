$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

$v5 = "D:\work\openclaude\dumps\jsonc\component_history_v5.jsonl"
$out = "D:\work\openclaude\dumps\jsonc\component_history_v6.jsonl"
$csvPath = "D:\work\openclaude\csvs\Full challenger dossier.csv"

# ============================================================================
# Load v5 records, identify post-OEM WOs and the 32 garbage records
# ============================================================================
$comps = New-Object System.Collections.ArrayList
$headers = New-Object System.Collections.ArrayList
$nonComps = New-Object System.Collections.ArrayList
foreach ($line in [System.IO.File]::ReadLines($v5)) {
    $obj = $line | ConvertFrom-Json
    if ($obj.type -eq 'header') { [void]$headers.Add($obj); continue }
    if ($obj.type -eq 'component_currently_installed' -or $obj.type -eq 'component_historical') {
        [void]$comps.Add($obj)
    } else {
        [void]$nonComps.Add($obj)
    }
}
"Loaded: $($comps.Count) components, $($nonComps.Count) docs+findings, $($headers.Count) header"

# ============================================================================
# 6.1: Identify garbage components
# ============================================================================
$garbagePatterns = @('CHALLENGER 650','See \d','S0000WJ','part numbers','serial numbers','to \d{4}')
function IsGarbage($c) {
    $sn = "$($c.identity.serial_number)"
    if (-not $sn -or $sn.Length -lt 3) { return $true }
    if ($sn -match '\s{2,}|^\s|\s$') { return $true }   # leading/trailing/multiple spaces
    if ($sn -match '\bCHALLENGER\b') { return $true }
    if ($sn -match '^See\b|^[Ss]erial\b|^[Pp]art\b') { return $true }
    if ($sn -match '\bto\s+\d{3,}') { return $true }    # "6050 to 6999" pattern
    if ($sn -match '^\d+\.\d+$') { return $true }       # "42.0" — measurement
    return $false
}
$garbage = New-Object System.Collections.ArrayList
$keep = New-Object System.Collections.ArrayList
foreach ($c in $comps) {
    if (IsGarbage $c) { [void]$garbage.Add($c) } else { [void]$keep.Add($c) }
}
"6.1: $($garbage.Count) components flagged as scope_review_needed (garbage SNs); $($keep.Count) retained"
""

# ============================================================================
# 6.4 (cheap): scan dossier paths for MPD/TLMC files
# ============================================================================
$mpdPaths = New-Object System.Collections.Generic.HashSet[string]
$mpdHeaders = $null
$reader = [System.IO.StreamReader]::new($csvPath, [System.Text.UTF8Encoding]::new($false))
$null = $reader.ReadLine()
while (-not $reader.EndOfStream) {
    $line = $reader.ReadLine()
    if ($line -match '(?i)\b(MPD|TLMC|maintenance\s+program|chapter\s+4|task\s+card)\b') {
        # Parse just the original_path column (index 3)
        $cols = New-Object System.Collections.ArrayList; $cur = New-Object System.Text.StringBuilder; $inQ = $false; $i = 0
        while ($i -lt $line.Length -and $cols.Count -lt 5) {
            $ch = $line[$i]
            if ($inQ) {
                if ($ch -eq '"' -and $i+1 -lt $line.Length -and $line[$i+1] -eq '"') { [void]$cur.Append('"'); $i+=2; continue }
                if ($ch -eq '"') { $inQ = $false; $i++; continue }
                [void]$cur.Append($ch); $i++
            } else {
                if ($ch -eq ',') { [void]$cols.Add($cur.ToString()); $cur.Length=0; $i++; continue }
                if ($ch -eq '"' -and $cur.Length -eq 0) { $inQ = $true; $i++; continue }
                [void]$cur.Append($ch); $i++
            }
        }
        if ($cols.Count -ge 4) {
            $p = $cols[3]
            if ($p -match '(?i)\b(MPD|TLMC|task\s*card|maintenance.{0,12}program|chapter\s*4)\b') {
                [void]$mpdPaths.Add($p)
            }
        }
    }
}
$reader.Close()
"6.4: MPD/TLMC path candidates found: $($mpdPaths.Count)"
foreach ($p in $mpdPaths | Select-Object -First 10) { "    $p" }
""

# ============================================================================
# 6.2: Find WO release pages for post-OEM components
# ============================================================================
# Build WO list from kept components' annex6_summary_finding
$woTargets = @{}
foreach ($c in $keep) {
    if ($c.annex6_summary_finding -and $c.annex6_summary_finding.wo_text) {
        # Extract WO number from text like "BBD UK WO 247470" or "AMAC WO CL65HBJTZ018MX"
        $woText = $c.annex6_summary_finding.wo_text
        $m = [regex]::Match($woText, '(?:WO\s*|Work\s*Order\s*)(\S+)', 'IgnoreCase')
        if ($m.Success) {
            $woNum = $m.Groups[1].Value -replace '\.', '' -replace ',', ''
            if (-not $woTargets.ContainsKey($woNum)) { $woTargets[$woNum] = New-Object System.Collections.ArrayList }
            [void]$woTargets[$woNum].Add($c.graph_id)
        }
    }
}
"6.2: WO targets to search for: $($woTargets.Count)"
foreach ($wo in $woTargets.Keys) { "    WO=$wo  (affects $($woTargets[$wo].Count) component(s))" }
""

# Now stream CSV again, looking for pages that mention these WOs
$woHits = @{}  # WO -> list of pages with rich data
$reader = [System.IO.StreamReader]::new($csvPath, [System.Text.UTF8Encoding]::new($false))
$null = $reader.ReadLine()
$rowsScanned = 0
while (-not $reader.EndOfStream) {
    $line = $reader.ReadLine()
    $rowsScanned++
    if (($rowsScanned % 2000) -eq 0) { Write-Host "  6.2 scanned $rowsScanned" }
    $hit = $null
    foreach ($wo in $woTargets.Keys) {
        if ($line -like "*$wo*") { if (-not $hit) { $hit = New-Object System.Collections.ArrayList }; [void]$hit.Add($wo) }
    }
    if (-not $hit) { continue }

    # Parse columns
    $cols = New-Object System.Collections.ArrayList; $cur = New-Object System.Text.StringBuilder; $inQ = $false; $i = 0
    while ($i -lt $line.Length) {
        $ch = $line[$i]
        if ($inQ) {
            if ($ch -eq '"' -and $i+1 -lt $line.Length -and $line[$i+1] -eq '"') { [void]$cur.Append('"'); $i+=2; continue }
            if ($ch -eq '"') { $inQ = $false; $i++; continue }
            [void]$cur.Append($ch); $i++
        } else {
            if ($ch -eq ',') { [void]$cols.Add($cur.ToString()); $cur.Length=0; $i++; continue }
            if ($ch -eq '"' -and $cur.Length -eq 0) { $inQ = $true; $i++; continue }
            [void]$cur.Append($ch); $i++
        }
    }
    [void]$cols.Add($cur.ToString())
    if ($cols.Count -lt 9) { continue }
    $docId = $cols[1]; $pageIdx = [int]$cols[2]; $origPath = $cols[3]; $extracted = $cols[8]

    # Parse JSON
    $parsed = $null
    try { $parsed = $extracted | ConvertFrom-Json -ErrorAction Stop } catch { continue }
    if (-not $parsed) { continue }

    # Extract Form-1-class refs, TSN values, AC TT references from the full text + tables
    $rawText = $extracted   # raw including tables
    $form1Refs = [regex]::Matches($rawText, '(?:EASA\s*Form\s*1[\s#:]*|FAA\s*Form\s*8130[\s\-3]*|TCCA\s*Form\s*1[\s#:]*|Form\s*1[\s#:]+|8130-3[\s#:]+)\s*[A-Z]?[\-]?\s*([A-Z0-9\-/]{4,})', 'IgnoreCase')
    $f1List = @()
    foreach ($m in $form1Refs) { $f1List += $m.Groups[1].Value }
    $f1List = @($f1List | Sort-Object -Unique)
    $tsnMatches = [regex]::Matches($rawText, '\b(\d{1,4}:\d{2})\b')
    $tsnVals = @()
    foreach ($m in $tsnMatches) { $tsnVals += $m.Groups[1].Value }
    $tsnVals = @($tsnVals | Sort-Object -Unique)
    $csnMatches = [regex]::Matches($rawText, '\b(\d{2,4})\s*(?:ldg|cycles?|landings?)\b', 'IgnoreCase')
    $csnVals = @()
    foreach ($m in $csnMatches) { $csnVals += [int]$m.Groups[1].Value }
    $csnVals = @($csnVals | Sort-Object -Unique)

    foreach ($wo in $hit) {
        if (-not $woHits.ContainsKey($wo)) { $woHits[$wo] = New-Object System.Collections.ArrayList }
        if ($woHits[$wo].Count -ge 5) { continue }  # cap per WO
        [void]$woHits[$wo].Add(@{
            document_id = $docId; page_number = ($pageIdx + 1); document_path = $origPath
            title = $parsed.title; document_type = $parsed.document_type
            form1_refs = $f1List
            tsn_candidates = $tsnVals
            csn_candidates = $csnVals
        })
    }
}
$reader.Close()
"6.2: WO hits captured: $(($woHits.Keys | ForEach-Object { $woHits[$_].Count } | Measure-Object -Sum).Sum) pages across $($woHits.Count) WOs"
foreach ($wo in $woHits.Keys) {
    $h = $woHits[$wo]
    "    WO=$wo  pages=$($h.Count)  first_doc='$($h[0].document_path)'"
    if ($h[0].form1_refs.Count -gt 0) { "      form1_refs: $($h[0].form1_refs -join ', ')" }
    if ($h[0].tsn_candidates.Count -gt 0) { "      tsn_candidates: $($h[0].tsn_candidates -join ', ')" }
}
""

# ============================================================================
# 6.3: Aggressive SB/AD identifier extraction
# Already covered by Phase B specific_sb_refs / specific_ad_refs.
# Re-run more aggressively: parse dossier_table_matches.row_cells for SB/AD/Mod
# ============================================================================
function ExtractSBs($text) {
    $refs = New-Object System.Collections.Generic.HashSet[string]
    $patterns = @(
        'SB[\s\-_]*[A-Z]{0,3}[\-_]?(?:\d{2,4}[\-\s]?\d{1,4}[A-Z]?\d?)',
        'Service\s+Bulletin\s+[A-Z0-9\-]+',
        '(?:GE|HONEYWELL|GOODRICH|BBD|BAS)[\-\s]+SB[\s\-_]?[A-Z0-9\-]+',
        '\bCL-\d{2,3}-\d{2,3}\b',
        '\bCL\d{3}-\d{2,3}-\d{2,3}\b'
    )
    foreach ($p in $patterns) {
        $matches = [regex]::Matches($text, $p, 'IgnoreCase')
        foreach ($m in $matches) { [void]$refs.Add($m.Value.Trim()) }
    }
    return @($refs)
}
function ExtractADs($text) {
    $refs = New-Object System.Collections.Generic.HashSet[string]
    $patterns = @(
        '\bFAA\s+\d{4}-\d{2}-\d{2}\b',
        '\bAD\s+\d{4}-\d{2}-\d{2}\b',
        '\bEASA\s*PAD\s*\d{4}-\d{4}\b',
        '\bEASA\s*AD\s*\d{4}-\d{4}\b',
        '\bTCCA\s*CF-\d{4}-\d{2,4}\b'
    )
    foreach ($p in $patterns) {
        $matches = [regex]::Matches($text, $p, 'IgnoreCase')
        foreach ($m in $matches) { [void]$refs.Add($m.Value.Trim()) }
    }
    return @($refs)
}

$enrichedSB = 0; $enrichedAD = 0
foreach ($c in $keep) {
    if (-not $c.dossier_table_matches) { continue }
    $sbSet = New-Object System.Collections.Generic.HashSet[string]
    $adSet = New-Object System.Collections.Generic.HashSet[string]
    foreach ($tm in $c.dossier_table_matches) {
        $rowText = ($tm.row_cells -join ' ')
        foreach ($x in (ExtractSBs $rowText)) { [void]$sbSet.Add($x) }
        foreach ($x in (ExtractADs $rowText)) { [void]$adSet.Add($x) }
    }
    # Merge with existing
    $existingSB = @()
    if ($c.modification_history.specific_sb_refs) { $existingSB = @($c.modification_history.specific_sb_refs) }
    $existingAD = @()
    if ($c.modification_history.specific_ad_refs) { $existingAD = @($c.modification_history.specific_ad_refs) }
    $newSB = @()
    foreach ($x in $sbSet) { if ($x -notin $existingSB) { $newSB += $x } }
    $newAD = @()
    foreach ($x in $adSet) { if ($x -notin $existingAD) { $newAD += $x } }
    if ($newSB.Count -gt 0) {
        $c.modification_history | Add-Member -NotePropertyName specific_sb_refs -NotePropertyValue (@($existingSB + $newSB) | Sort-Object -Unique) -Force
        $enrichedSB++
    }
    if ($newAD.Count -gt 0) {
        $c.modification_history | Add-Member -NotePropertyName specific_ad_refs -NotePropertyValue (@($existingAD + $newAD) | Sort-Object -Unique) -Force
        $enrichedAD++
    }
}
"6.3: SB/AD identifier enrichment: $enrichedSB components got new SB refs, $enrichedAD got new AD refs"
""

# ============================================================================
# Apply 6.2 results: upgrade post-OEM components with WO-page evidence
# ============================================================================
$tsnLifted = 0; $form1Located = 0
foreach ($c in $keep) {
    if (-not $c.annex6_summary_finding) { continue }
    $woText = $c.annex6_summary_finding.wo_text
    $woNum = ([regex]::Match($woText, '(?:WO\s*|Work\s*Order\s*)(\S+)', 'IgnoreCase')).Groups[1].Value -replace '[.,]', ''
    if (-not $woNum -or -not $woHits.ContainsKey($woNum)) { continue }
    $woPages = $woHits[$woNum]
    if ($woPages.Count -eq 0) { continue }

    # Collect Form 1 refs across all WO pages
    $allF1s = New-Object System.Collections.Generic.HashSet[string]
    foreach ($wp in $woPages) { foreach ($f in $wp.form1_refs) { [void]$allF1s.Add($f) } }
    if ($allF1s.Count -gt 0) {
        $c.form_1.outcome = 'form1_located_via_wo_search'
        $c.form_1.rationale_codes = @('form1_found_in_wo_work_package')
        $c.form_1.reasoning = "Form 1 / release certificate references located by searching for the install work order $woNum across the dossier. Candidate certificate numbers: $($allF1s -join ', '). Source: $($woPages[0].document_path) p.$($woPages[0].page_number)."
        $c.form_1 | Add-Member -NotePropertyName specific_form1_refs -NotePropertyValue (@($allF1s)) -Force
        $c.form_1 | Add-Member -NotePropertyName wo_search_pages -NotePropertyValue (@($woPages | Select-Object -First 3)) -Force
        $form1Located++
    }

    # TSN candidates near the WO context (heuristic: the WO release page usually has the AC TT)
    $allTSNs = New-Object System.Collections.Generic.HashSet[string]
    foreach ($wp in $woPages) { foreach ($t in $wp.tsn_candidates) { [void]$allTSNs.Add($t) } }
    if ($allTSNs.Count -gt 0) {
        # Don't override if already set; just provide candidates for review
        $c | Add-Member -NotePropertyName wo_tsn_candidates -NotePropertyValue (@{
            wo = $woNum
            candidates = @($allTSNs)
            page = $woPages[0].document_path
            note = "TSN values found on pages mentioning install WO $woNum. Multiple candidates means manual review needed to pick the AC TT at install (typically the value before the 'Total at Installation' label)."
        }) -Force
        $tsnLifted++
    }
}
"6.2 applied: $form1Located components got Form 1 refs from WO search; $tsnLifted got TSN candidates"
""

# ============================================================================
# Write v6
# ============================================================================
$writer = [System.IO.StreamWriter]::new($out, $false, [System.Text.UTF8Encoding]::new($false))

# Header — update with v6 stats and MPD finding
$h = $headers[0]
$h.schema_version = "component_history_v6"
$h.note = "$($h.note) Phase v6: dropped $($garbage.Count) garbage records; located post-OEM WO pages ($form1Located got Form 1 refs, $tsnLifted got TSN candidates); aggressive SB/AD identifier extraction ($enrichedSB SB / $enrichedAD AD upgraded)."
$h | Add-Member -NotePropertyName mpd_tlmc_search -NotePropertyValue @{
    paths_found = $mpdPaths.Count
    sample_paths = @($mpdPaths | Select-Object -First 5)
    reasoning = if ($mpdPaths.Count -eq 0) { "No MPD / TLMC / Maintenance Program / Chapter 4 documents found in the dossier paths. Remaining-life calculations require these inputs and they are not provided." } else { "Candidate MPD / TLMC paths detected; structured ATA Chapter 4 task data still requires parsing to populate remaining-life fields." }
} -Force
$h | Add-Member -NotePropertyName quarantined_count -NotePropertyValue $garbage.Count -Force
$writer.WriteLine(($h | ConvertTo-Json -Depth 30 -Compress))

# documents + findings
foreach ($nc in $nonComps) { $writer.WriteLine(($nc | ConvertTo-Json -Depth 30 -Compress)) }

# kept components
foreach ($c in $keep) { $writer.WriteLine(($c | ConvertTo-Json -Depth 30 -Compress)) }

# garbage as scope_review_needed
foreach ($g in $garbage) {
    $g | Add-Member -NotePropertyName quarantine_reason -NotePropertyValue "Component PN or SN appears to be OCR noise (header text, model name, measurement, or whitespace-corrupted)." -Force
    $obj = [ordered]@{ type = 'component_scope_review_needed' }
    foreach ($p in $g.PSObject.Properties) { if ($p.Name -ne 'type') { $obj[$p.Name] = $p.Value } }
    $writer.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
}

$writer.Flush(); $writer.Close()
$fi = Get-Item $out
"=== component_history_v6.jsonl ==="
"  size:  {0:N2} MB" -f ($fi.Length/1MB)
"  lines: $((Get-Content $out | Measure-Object -Line).Lines)"
""
"Total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
