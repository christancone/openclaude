$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# Load SN list from v4
# ============================================================================
$jsonl = "D:\work\openclaude\dumps\jsonc\component_history_v4.jsonl"
$snSet = @{}
$snToPn = @{}
$snToCv = @{}
$lineNo = 0
foreach ($line in [System.IO.File]::ReadLines($jsonl)) {
    $lineNo++
    if ($line -notmatch '"type":"component_') { continue }
    $obj = $line | ConvertFrom-Json
    $sn = $obj.identity.serial_number
    $pn = $obj.identity.part_number
    if ($sn -and $sn.Length -ge 3) {
        $snSet[$sn] = $true
        $snToPn[$sn] = $pn
        $snToCv[$sn] = $obj.graph_id
    }
}
"Loaded $($snSet.Count) SNs from v4. elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"

# ============================================================================
# Stream the CSV, find rows mentioning any SN, parse extracted_json
# ============================================================================
$csvPath = "D:\work\openclaude\csvs\Full challenger dossier.csv"
$reader = [System.IO.StreamReader]::new($csvPath, [System.Text.UTF8Encoding]::new($false))
$header = $reader.ReadLine()
$headers = $header.Split(',')
# extracted_json is column index 8
"CSV header read. elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"

$rowsScanned = 0
$rowsMatched = 0
$snHits = @{}   # SN -> ArrayList of evidence records

# Tokenize function: extract alphanumeric+hyphen tokens
$tokenRx = New-Object System.Text.RegularExpressions.Regex '[A-Z0-9][A-Z0-9\-/_.]{2,}', 'IgnoreCase,Compiled'

while (-not $reader.EndOfStream) {
    $line = $reader.ReadLine()
    $rowsScanned++
    if (($rowsScanned % 2000) -eq 0) { Write-Host "  scanned $rowsScanned / 16482  matched=$rowsMatched  elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s" }

    # Fast filter: tokenize the line, check tokens against SN set
    $tokens = $tokenRx.Matches($line)
    $matchedSNs = New-Object System.Collections.Generic.HashSet[string]
    foreach ($t in $tokens) {
        $v = $t.Value
        if ($snSet.ContainsKey($v)) { [void]$matchedSNs.Add($v) }
    }
    if ($matchedSNs.Count -eq 0) { continue }
    $rowsMatched++

    # Parse this row's columns (manual CSV-aware parse: respect quoted fields with doubled-quote escapes)
    $cols = New-Object System.Collections.ArrayList
    $cur = New-Object System.Text.StringBuilder
    $inQ = $false; $i = 0
    while ($i -lt $line.Length) {
        $ch = $line[$i]
        if ($inQ) {
            if ($ch -eq '"' -and $i + 1 -lt $line.Length -and $line[$i + 1] -eq '"') {
                [void]$cur.Append('"'); $i += 2; continue
            }
            if ($ch -eq '"') { $inQ = $false; $i++; continue }
            [void]$cur.Append($ch); $i++
        } else {
            if ($ch -eq ',') { [void]$cols.Add($cur.ToString()); $cur.Length = 0; $i++; continue }
            if ($ch -eq '"' -and $cur.Length -eq 0) { $inQ = $true; $i++; continue }
            [void]$cur.Append($ch); $i++
        }
    }
    [void]$cols.Add($cur.ToString())

    if ($cols.Count -lt 9) { continue }
    $docId = $cols[1]; $pageIdx = $cols[2]; $origPath = $cols[3]; $extracted = $cols[8]

    # Lightly extract from JSON — title, events, key signals — without full parse
    $title = $null
    $titleM = [regex]::Match($extracted, '"title"\s*:\s*"([^"]+)"')
    if ($titleM.Success) { $title = $titleM.Groups[1].Value }

    # Extract events array (rough): match top-level "events": [ ... ] section
    $events = @()
    $eventsBlockM = [regex]::Match($extracted, '"events"\s*:\s*\[(.*?)\](?=\s*[,}])', 'Singleline')
    if ($eventsBlockM.Success) {
        $eb = $eventsBlockM.Groups[1].Value
        # Capture each event object {... }
        $evMatches = [regex]::Matches($eb, '\{[^{}]*\}')
        foreach ($em in $evMatches) {
            $eobj = $em.Value
            $eDate = ([regex]::Match($eobj, '"date"\s*:\s*"([^"]+)"')).Groups[1].Value
            $eType = ([regex]::Match($eobj, '"event_type"\s*:\s*"([^"]+)"')).Groups[1].Value
            $eDesc = ([regex]::Match($eobj, '"description"\s*:\s*"([^"]+)"')).Groups[1].Value
            if ($eDate -or $eType -or $eDesc) {
                $events += @{ date = $eDate; type = $eType; description = $eDesc }
            }
        }
    }

    # Look for TSN/CSN / Form 1 / SB / Mod signals in the raw extracted JSON
    $tsnHit = $false; $csnHit = $false; $form1Hit = $false; $sbHit = $false; $modHit = $false
    if ($extracted -match '(?i)\bTSN\b') { $tsnHit = $true }
    if ($extracted -match '(?i)\bCSN\b|\bldg\b|\blandings?\b|\bcycles?\b') { $csnHit = $true }
    if ($extracted -match '(?i)form\s*1|form\s*8130|EASA\s+Form|TCCA\s+Form|certificate\s+of\s+release|CRS') { $form1Hit = $true }
    if ($extracted -match '(?i)\bSB[\s\-]*\d|Service\s+Bulletin') { $sbHit = $true }
    if ($extracted -match '(?i)\bmodification\b|\bmod\s+status\b|\bSTC\b') { $modHit = $true }

    # Stash for each matched SN
    foreach ($snHit in $matchedSNs) {
        if (-not $snHits.ContainsKey($snHit)) { $snHits[$snHit] = New-Object System.Collections.ArrayList }
        [void]$snHits[$snHit].Add(@{
            document_id = $docId
            page_index = [int]$pageIdx
            original_path = $origPath
            title = $title
            events_extracted = $events
            signals = @{ tsn = $tsnHit; csn = $csnHit; form1 = $form1Hit; sb = $sbHit; mod = $modHit }
        })
    }
}
$reader.Close()

"Done scanning. rows=$rowsScanned matched=$rowsMatched SNs_hit=$($snHits.Count)  elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
"Coverage: $($snHits.Count) / $($snSet.Count) SNs have at least one dossier page"

# Hit-count distribution
$hits_buckets = @{ '0'=0; '1'=0; '2-5'=0; '6-20'=0; '21+'=0 }
foreach ($k in $snSet.Keys) {
    if (-not $snHits.ContainsKey($k)) { $hits_buckets['0']++ }
    else {
        $n = $snHits[$k].Count
        if ($n -eq 1) { $hits_buckets['1']++ }
        elseif ($n -le 5) { $hits_buckets['2-5']++ }
        elseif ($n -le 20) { $hits_buckets['6-20']++ }
        else { $hits_buckets['21+']++ }
    }
}
"=== Dossier page hits per SN ==="
foreach ($k in '0','1','2-5','6-20','21+') { "  {0,-5}  {1}" -f $k, $hits_buckets[$k] }

# Save the index
$snHits | ConvertTo-Json -Depth 30 -Compress | Set-Content "D:\work\openclaude\dumps\jsonc\.v4_dossier_index.json" -Encoding utf8
$fi = Get-Item "D:\work\openclaude\dumps\jsonc\.v4_dossier_index.json"
"Index written: {0:N2} MB" -f ($fi.Length/1MB)
"Total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
