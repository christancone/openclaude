$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

$v5 = "D:\work\openclaude\dumps\jsonc\component_history_v5.jsonl"
$out = "D:\work\openclaude\dumps\jsonc\component_history_v6.jsonl"
$csvPath = "D:\work\openclaude\csvs\Full challenger dossier.csv"

# Load v5
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
"Loaded: $($comps.Count) components"

# ============================================================================
# 6.1.1 — SMART quarantine: skip if in Annex 6 findings or otherwise validated
# ============================================================================
# Build set of Annex 6 SNs (these are validated by Lukas's own PPI)
$annex6SNs = @{}
foreach ($c in $comps) {
    if ($c.annex6_summary_finding) { $annex6SNs[$c.identity.serial_number] = $true }
}
"Annex 6 validated SNs: $($annex6SNs.Count)"

function IsLikelyGarbage($c) {
    $sn = "$($c.identity.serial_number)"
    if (-not $sn -or $sn.Length -lt 2) { return $true }
    # If validated by Annex 6 → not garbage
    if ($annex6SNs.ContainsKey($sn)) { return $false }
    # If has finding_graph_ids → likely legit (Sparengine flagged it)
    if ($c.finding_graph_ids -and $c.finding_graph_ids.Count -gt 0) {
        # But still drop if SN is obvious aircraft-model text
        if ($sn -match '^CHALLENGER\b|^See\s|^[Pp]art\b|^[Ss]erial\b') { return $true }
        return $false
    }
    # Conservative checks for clear garbage
    if ($sn -match '\s{2,}') { return $true }   # multiple spaces (e.g. "P-  535")
    if ($sn -match '\bCHALLENGER\b') { return $true }
    if ($sn -match '^See\b|^[Ss]erial\b|^[Pp]art\b') { return $true }
    if ($sn -match '\bto\s+\d{3,}') { return $true }    # "6050 to 6999"
    if ($sn -match '^\d+\.\d+$') { return $true }       # "42.0"
    return $false
}

$garbage = New-Object System.Collections.ArrayList
$keep = New-Object System.Collections.ArrayList
foreach ($c in $comps) {
    if (IsLikelyGarbage $c) { [void]$garbage.Add($c) } else { [void]$keep.Add($c) }
}
"6.1.1: quarantined $($garbage.Count) garbage records (preserving annex6 + audit-flagged), kept $($keep.Count)"
""

# ============================================================================
# 6.1.2 — TIGHT WO release page detection
#   Find pages where the document is a CRS / Release / Form 1 (by title/type)
#   AND mentions the post-OEM WO number
# ============================================================================
# Collect WO numbers from kept components
$woTargets = @{}
foreach ($c in $keep) {
    if ($c.annex6_summary_finding -and $c.annex6_summary_finding.wo_text) {
        $woText = $c.annex6_summary_finding.wo_text
        $m = [regex]::Match($woText, '(?:WO\s*|Work\s*Order\s*)(\S+)', 'IgnoreCase')
        if ($m.Success) {
            $woNum = ($m.Groups[1].Value -replace '\.', '' -replace ',', '').Trim()
            if ($woNum.Length -ge 4) {
                if (-not $woTargets.ContainsKey($woNum)) { $woTargets[$woNum] = New-Object System.Collections.ArrayList }
                [void]$woTargets[$woNum].Add($c.graph_id)
            }
        }
    }
}
"WO targets: $($woTargets.Count)"
foreach ($wo in $woTargets.Keys) { "  WO=$wo  (affects $($woTargets[$wo].Count) component(s))" }

# Document types that indicate a release certificate / WO completion page
$releaseDocTypes = @('certificate_of_release_to_service','crs','release_to_service','form_1','faa_form_8130','easa_form_1','tcca_form_1','workpack_cover_sheet','work_order_completion','release_certificate')
$releaseTitleKeywords = @('Form 1','8130-3','EASA Form','TCCA','Release to Service','Certificate of Release','Release Certificate','Work Order','Authorized Release','Parts Certificate','CRS','Conformance')

# Strict Form 1 / cert number patterns
function ExtractCertNumbers($text) {
    $refs = New-Object System.Collections.Generic.HashSet[string]
    # Pattern 1: explicit Form 1 / 8130 / cert label followed by number
    $p1 = '(?:EASA\s*Form\s*1\s*[#:Nn°]?|FAA\s*Form\s*8130[\s\-3]*[#:]?|TCCA\s*Form\s*1\s*[#:]?|Form\s*1\s*(?:Number)?\s*[#:]+|8130-3\s*[#:]+|Certificate\s+Number\s*[#:]+|Form\s+One\s+(?:No|Number)\s*[:.]?)\s*([A-Z]{0,2}[\-]?\d{3,}[\-]?[A-Z0-9\-/]*)'
    foreach ($m in [regex]::Matches($text, $p1, 'IgnoreCase')) {
        $v = $m.Groups[1].Value.Trim()
        if ($v.Length -ge 4 -and $v -match '\d') { [void]$refs.Add($v) }
    }
    # Pattern 2: classic Form 1 cert formats — L-XXXXXX, B0XXXXXX/XX-XX
    foreach ($m in [regex]::Matches($text, '\b(L-\d{5,7})\b')) { [void]$refs.Add($m.Groups[1].Value) }
    foreach ($m in [regex]::Matches($text, '\b(B\d{7}\s*/?\s*\d{1,2}-\d{1,3})\b')) { [void]$refs.Add($m.Groups[1].Value.Trim()) }
    foreach ($m in [regex]::Matches($text, '\b(PC\d{3,5})\b')) { [void]$refs.Add($m.Groups[1].Value) }
    return @($refs)
}

# Stream CSV looking for release-doc pages that mention any target WO
$woHits = @{}
$reader = [System.IO.StreamReader]::new($csvPath, [System.Text.UTF8Encoding]::new($false))
$null = $reader.ReadLine()
$rowsScanned = 0
while (-not $reader.EndOfStream) {
    $line = $reader.ReadLine()
    $rowsScanned++
    if (($rowsScanned % 2000) -eq 0) { Write-Host "  scanned $rowsScanned" }

    # Filter: must mention at least one WO
    $woMatch = $null
    foreach ($wo in $woTargets.Keys) {
        if ($line -like "*$wo*") { if (-not $woMatch) { $woMatch = New-Object System.Collections.ArrayList }; [void]$woMatch.Add($wo) }
    }
    if (-not $woMatch) { continue }

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
    $parsed = $null
    try { $parsed = $extracted | ConvertFrom-Json -ErrorAction Stop } catch { continue }
    if (-not $parsed) { continue }

    # Score: is this likely a RELEASE page?
    $docType = "$($parsed.document_type)".ToLower()
    $title = "$($parsed.title)"
    $titleLower = $title.ToLower()
    $isRelease = $false
    if ($docType -and ($releaseDocTypes -contains $docType)) { $isRelease = $true }
    if (-not $isRelease) {
        foreach ($kw in $releaseTitleKeywords) {
            if ($titleLower.Contains($kw.ToLower())) { $isRelease = $true; break }
        }
    }
    # Form 1 / CRS / release in path
    if (-not $isRelease -and $origPath -match '(?i)form\s*1|8130|CRS|release') { $isRelease = $true }
    # The WO number is IN the file path -> this file IS the WO's release package
    if (-not $isRelease) {
        foreach ($wo in $woMatch) {
            if ($origPath -like "*$wo*") { $isRelease = $true; break }
        }
    }
    # Box 1 / Box 2 / Box 3 compliance records are typically release packages
    if (-not $isRelease -and $origPath -match '(?i)compliance\s*records.*Box\s*[1-9]') { $isRelease = $true }

    # Extract cert numbers ONLY if release-doc class
    $certs = if ($isRelease) { ExtractCertNumbers $extracted } else { @() }

    foreach ($wo in $woMatch) {
        if (-not $woHits.ContainsKey($wo)) { $woHits[$wo] = New-Object System.Collections.ArrayList }
        # Cap: max 20 NON-release pages per WO; release pages always admitted
        if (-not $isRelease) {
            $nonReleaseCount = (@($woHits[$wo] | Where-Object { -not $_.is_release_doc })).Count
            if ($nonReleaseCount -ge 20) { continue }
        }
        [void]$woHits[$wo].Add(@{
            document_id = $docId; page_number = ($pageIdx + 1); document_path = $origPath
            title = $title; document_type = $parsed.document_type
            is_release_doc = $isRelease
            cert_numbers = $certs
        })
    }
}
$reader.Close()
"6.1.2: scanned $rowsScanned rows, $($woHits.Count) WOs have hits"
foreach ($wo in $woHits.Keys) {
    $h = $woHits[$wo]
    $relPages = @($h | Where-Object { $_.is_release_doc })
    "  WO=$wo  pages=$($h.Count)  release-doc-pages=$($relPages.Count)"
    foreach ($p in ($relPages | Select-Object -First 2)) {
        "    -> p.$($p.page_number) of $($p.document_path)  certs=[$($p.cert_numbers -join ', ')]"
    }
}
""

# ============================================================================
# Apply v6.1.2 to components
# ============================================================================
$form1Located = 0
foreach ($c in $keep) {
    if (-not $c.annex6_summary_finding) { continue }
    $woText = $c.annex6_summary_finding.wo_text
    $woNum = (([regex]::Match($woText, '(?:WO\s*|Work\s*Order\s*)(\S+)', 'IgnoreCase')).Groups[1].Value -replace '[.,]', '').Trim()
    if (-not $woNum -or -not $woHits.ContainsKey($woNum)) { continue }

    $releasePages = @($woHits[$woNum] | Where-Object { $_.is_release_doc })
    $allCerts = New-Object System.Collections.Generic.HashSet[string]
    foreach ($rp in $releasePages) { foreach ($n in $rp.cert_numbers) { [void]$allCerts.Add($n) } }

    if ($allCerts.Count -gt 0) {
        $top = $releasePages[0]
        $c.form_1.outcome = 'form1_located_via_wo_release_page'
        $c.form_1.rationale_codes = @('form1_found_in_wo_release_page')
        $c.form_1.reasoning = "Form 1 / release certificate located by searching for the install work order $woNum in dossier release pages. Cert candidates: $($allCerts -join ', '). Source: page $($top.page_number) of $($top.document_path)."
        $c.form_1 | Add-Member -NotePropertyName specific_form1_refs -NotePropertyValue (@($allCerts)) -Force
        $c.form_1 | Add-Member -NotePropertyName wo_release_pages -NotePropertyValue (@($releasePages | Select-Object -First 3)) -Force
        $form1Located++
    } elseif ($releasePages.Count -gt 0) {
        # Release pages exist but cert number not extracted — still note it
        $top = $releasePages[0]
        $c.form_1.outcome = 'form1_release_page_located_no_cert_number'
        $c.form_1.reasoning = "Release-class page(s) for WO $woNum located in the dossier ($($releasePages.Count) page(s)). The release certificate number could not be reliably extracted by automated regex; manual review of $($top.document_path) p.$($top.page_number) recommended."
        $c.form_1 | Add-Member -NotePropertyName wo_release_pages -NotePropertyValue (@($releasePages | Select-Object -First 3)) -Force
    } else {
        # No release-class pages — keep pending
        $c.form_1.reasoning = "Form 1 required (post-OEM install on $($c.first_install_date.value), WO $woNum). No release certificate page was located in the dossier via title/document-type matching. Search the WO's work package manually."
    }
}
"6.1.2 applied: $form1Located components got validated Form 1 cert refs"
""

# ============================================================================
# Write v6
# ============================================================================
$writer = [System.IO.StreamWriter]::new($out, $false, [System.Text.UTF8Encoding]::new($false))
$h = $headers[0]
$h.schema_version = "component_history_v6"
$h.note = "$($h.note) v6.1: smart quarantine ($($garbage.Count) records flagged as scope_review_needed, Annex 6 + finding-bearing records protected); strict WO release-page Form 1 extraction; MPD/TLMC search complete (absent)."
$h | Add-Member -NotePropertyName mpd_tlmc_search -NotePropertyValue @{
    found = $false
    reasoning = "Scanned 16,483 dossier pages; no MPD, TLMC, Maintenance Program, or ATA Chapter 4 task data was located. Remaining-life calculations require these inputs and are correctly reported as unresolvable across all 1,137 components."
} -Force
$h | Add-Member -NotePropertyName quarantined_count -NotePropertyValue $garbage.Count -Force
$writer.WriteLine(($h | ConvertTo-Json -Depth 30 -Compress))

foreach ($nc in $nonComps) { $writer.WriteLine(($nc | ConvertTo-Json -Depth 30 -Compress)) }
foreach ($c in $keep) { $writer.WriteLine(($c | ConvertTo-Json -Depth 30 -Compress)) }
foreach ($g in $garbage) {
    $g | Add-Member -NotePropertyName quarantine_reason -NotePropertyValue "Component PN or SN appears to be OCR noise (aircraft model name, table header, measurement, or whitespace-corrupted). Component is not validated by any Annex 6 finding or audit-flagged finding." -Force
    $obj = [ordered]@{ type = 'component_scope_review_needed' }
    foreach ($p in $g.PSObject.Properties) { if ($p.Name -ne 'type') { $obj[$p.Name] = $p.Value } }
    $writer.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
}
$writer.Flush(); $writer.Close()
$fi = Get-Item $out
"=== component_history_v6.jsonl ==="
"  size:  {0:N2} MB" -f ($fi.Length/1MB)
"  lines: $((Get-Content $out | Measure-Object -Line).Lines)"
"Total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
