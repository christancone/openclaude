$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# PHASE A — Stream CSV, build a richer index
#   - parse extracted_json as real JSON
#   - extract title, events[], tables[], header_fields, document_type
#   - for each in-scope SN, stash the parsed payload (slim — only useful fields)
# ============================================================================
$jsonl = "D:\work\openclaude\dumps\jsonc\component_history_v4.jsonl"
$snSet = @{}
foreach ($line in [System.IO.File]::ReadLines($jsonl)) {
    if ($line -notmatch '"type":"component_') { continue }
    $obj = $line | ConvertFrom-Json
    $sn = $obj.identity.serial_number
    if ($sn -and $sn.Length -ge 3) { $snSet[$sn] = $true }
}
"Loaded $($snSet.Count) SNs"

$csvPath = "D:\work\openclaude\csvs\Full challenger dossier.csv"
$reader = [System.IO.StreamReader]::new($csvPath, [System.Text.UTF8Encoding]::new($false))
$null = $reader.ReadLine()

$snHits = @{}                # SN -> ArrayList<{doc_id,page,path,parsed}>
$aircraftSnapshots = New-Object System.Collections.ArrayList   # candidate aircraft TSN/CSN snapshot pages
$rowsScanned = 0
$rowsMatched = 0
$tokenRx = New-Object System.Text.RegularExpressions.Regex '[A-Z0-9][A-Z0-9\-/_.]{2,}', 'IgnoreCase,Compiled'

while (-not $reader.EndOfStream) {
    $line = $reader.ReadLine()
    $rowsScanned++
    if (($rowsScanned % 2000) -eq 0) { Write-Host "  scanned $rowsScanned / 16482  matched=$rowsMatched  snapshots=$($aircraftSnapshots.Count)  elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s" }

    # Token-filter to SNs
    $tokens = $tokenRx.Matches($line)
    $matchedSNs = New-Object System.Collections.Generic.HashSet[string]
    foreach ($t in $tokens) { $v = $t.Value; if ($snSet.ContainsKey($v)) { [void]$matchedSNs.Add($v) } }
    if ($matchedSNs.Count -eq 0) { continue }
    $rowsMatched++

    # CSV-parse columns
    $cols = New-Object System.Collections.ArrayList
    $cur = New-Object System.Text.StringBuilder
    $inQ = $false; $i = 0
    while ($i -lt $line.Length) {
        $ch = $line[$i]
        if ($inQ) {
            if ($ch -eq '"' -and $i + 1 -lt $line.Length -and $line[$i + 1] -eq '"') { [void]$cur.Append('"'); $i += 2; continue }
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
    $docId = $cols[1]; $pageIdx = [int]$cols[2]; $origPath = $cols[3]; $extracted = $cols[8]

    # Parse JSON (PS 5.1 — no -Depth on ConvertFrom-Json)
    $parsed = $null
    try { $parsed = $extracted | ConvertFrom-Json -ErrorAction Stop } catch { continue }
    if (-not $parsed) { continue }

    # Aircraft snapshot detection (look for table with 'AIRCRAFT' row + TSN/CSN-like cells)
    if ($parsed.tables) {
        foreach ($tbl in $parsed.tables) {
            if ($tbl.rows -and $tbl.rows.Count -gt 0) {
                foreach ($row in $tbl.rows) {
                    if ($row -and $row.Count -ge 4) {
                        $rowJoined = ($row -join '|').ToUpper()
                        # Heuristic: row mentions AIRCRAFT and contains a HH:MM pattern
                        if ($rowJoined -match 'AIRCRAFT' -and $rowJoined -match '\d+:\d{2}') {
                            [void]$aircraftSnapshots.Add(@{
                                doc_id = $docId; page = ($pageIdx + 1); path = $origPath
                                title = $parsed.title; row = $row; table_name = $tbl.name
                            })
                        }
                    }
                }
            }
        }
    }

    # Build a slim payload — keep what's useful for enrichment
    $slim = @{
        title = $parsed.title
        document_type = $parsed.document_type
        events = $parsed.events
        tables = $parsed.tables
    }

    foreach ($snHit in $matchedSNs) {
        if (-not $snHits.ContainsKey($snHit)) { $snHits[$snHit] = New-Object System.Collections.ArrayList }
        [void]$snHits[$snHit].Add(@{
            document_id = $docId; page = ($pageIdx + 1); path = $origPath
            parsed = $slim
        })
    }
}
$reader.Close()
"Phase A done: $rowsScanned rows, $rowsMatched matched, $($snHits.Count) SNs indexed, $($aircraftSnapshots.Count) aircraft-snapshot pages found"
"  elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"

# Save aircraft snapshots
$aircraftSnapshots | ConvertTo-Json -Depth 30 -Compress | Set-Content "D:\work\openclaude\dumps\jsonc\.v5_aircraft_snapshots.json" -Encoding utf8
$snHits | ConvertTo-Json -Depth 30 -Compress | Set-Content "D:\work\openclaude\dumps\jsonc\.v5_dossier_deep_index.json" -Encoding utf8
$fi = Get-Item "D:\work\openclaude\dumps\jsonc\.v5_dossier_deep_index.json"
"Index size: {0:N2} MB" -f ($fi.Length/1MB)
"Phase A elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"

# Show aircraft snapshot candidates
"=== Aircraft snapshot candidate rows (max 10) ==="
foreach ($s in ($aircraftSnapshots | Select-Object -First 10)) {
    "  [$($s.path)] p.$($s.page) table='$($s.table_name)'"
    "    row: $($s.row -join ' | ')"
}
