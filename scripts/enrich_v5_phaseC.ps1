$ErrorActionPreference = 'Stop'
$sw = [Diagnostics.Stopwatch]::StartNew()

# ============================================================================
# Load Annex 6 snapshot (full table) + v5 file
# ============================================================================
$annex6 = Get-Content "D:\work\openclaude\dumps\jsonc\.v5_annex6_snapshot.json" -Raw | ConvertFrom-Json
$snap = @{}
foreach ($r in $annex6.rows) {
    $label = "$($r[0])".ToUpper()
    if ($label -like 'AIRCRAFT*') { $snap.aircraft = $r }
    elseif ($label -like 'LH ENG*') { $snap.lh = $r }
    elseif ($label -like 'RH ENG*') { $snap.rh = $r }
    elseif ($label -eq 'APU') { $snap.apu = $r }
}
"=== Annex 6 snapshot rows loaded ==="
"  AIRCRAFT:  TSN=$($snap.aircraft[4])  CSN=$($snap.aircraft[5])"
"  LH ENGINE: SN=$($snap.lh[3])  TSN=$($snap.lh[4])  CSN=$($snap.lh[5])"
"  RH ENGINE: SN=$($snap.rh[3])  TSN=$($snap.rh[4])  CSN=$($snap.rh[5])"
"  APU:       SN=$($snap.apu[3])  TSH=$($snap.apu[4])  CSN=$($snap.apu[5])"
""

# Fetch the Annex 6 Summary-of-Findings rows again (need them for post-OEM install parsing)
$path = "D:\work\openclaude\csvs\Full challenger dossier.csv"
$reader = [System.IO.StreamReader]::new($path, [System.Text.UTF8Encoding]::new($false))
$null = $reader.ReadLine()
$annex6Findings = $null
while (-not $reader.EndOfStream) {
    $line = $reader.ReadLine()
    if ($line -like '*Annex 6*Summary of Findings*' -and $line -like '*Aircraft/Engine*Details*') {
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
        $obj = $cols[8] | ConvertFrom-Json
        $annex6Findings = @{
            doc_id = $cols[1]; path = $cols[3]; page = ([int]$cols[2] + 1)
            findings_table = ($obj.tables | Where-Object { $_.name -like '*Summary*Findings*' } | Select-Object -First 1)
        }
        break
    }
}
$reader.Close()

# Parse the post-OEM install entries from the findings table
$postOemInstalls = New-Object System.Collections.ArrayList
if ($annex6Findings -and $annex6Findings.findings_table) {
    foreach ($row in $annex6Findings.findings_table.rows) {
        $text = ($row -join ' ')
        # Pattern: "PN: <PN> SN: <SN>, installed <date> under <WO source>"
        $m = [regex]::Match($text, '(.+?)\s+PN[:\s]+([\w\-/.]+)\s+SN[:\s]+([\w\-/.]+),?\s+installed\s+(.+?)\s+under\s+(.+?)(?:\.|\s*$)', 'IgnoreCase')
        if ($m.Success) {
            [void]$postOemInstalls.Add([ordered]@{
                description = $m.Groups[1].Value.Trim()
                pn          = $m.Groups[2].Value
                sn          = $m.Groups[3].Value
                date_text   = $m.Groups[4].Value
                wo_text     = $m.Groups[5].Value
                citation = @{
                    document_id = $annex6Findings.doc_id
                    document_path = $annex6Findings.path
                    page_number = $annex6Findings.page
                    table_name = 'Summary of Findings'
                    confidence = 'singleton_match'
                }
            })
        }
    }
}
"=== Post-OEM install entries from Annex 6 Summary of Findings ==="
foreach ($p in $postOemInstalls) {
    "  [{0}] PN={1}  SN={2}  date={3}  WO={4}" -f $p.description, $p.pn, $p.sn, $p.date_text, $p.wo_text
}
""

# Convert date text to ISO
function ToIsoDate($txt) {
    if (-not $txt) { return $null }
    # "Feb 19th 2022", "Mar 09th 2021", "Dec 23rd 2021"
    $m = [regex]::Match($txt, '(\w{3})\s+(\d+)\w*\s+(\d{4})')
    if ($m.Success) {
        $months = @{ 'JAN'='01';'FEB'='02';'MAR'='03';'APR'='04';'MAY'='05';'JUN'='06';'JUL'='07';'AUG'='08';'SEP'='09';'OCT'='10';'NOV'='11';'DEC'='12' }
        $mo = $months[$m.Groups[1].Value.ToUpper()]
        if ($mo) {
            $day = "{0:D2}" -f [int]$m.Groups[2].Value
            $yr = $m.Groups[3].Value
            return "$yr-$mo-$day"
        }
    }
    return $null
}

# Build lookup by SN
$postOemByPnSn = @{}
foreach ($p in $postOemInstalls) {
    $key = "$($p.pn)::$($p.sn)"
    if (-not $postOemByPnSn.ContainsKey($key)) { $postOemByPnSn[$key] = New-Object System.Collections.ArrayList }
    [void]$postOemByPnSn[$key].Add($p)
}
$postOemBySn = @{}
foreach ($p in $postOemInstalls) {
    if (-not $postOemBySn.ContainsKey($p.sn)) { $postOemBySn[$p.sn] = New-Object System.Collections.ArrayList }
    [void]$postOemBySn[$p.sn].Add($p)
}

# ============================================================================
# Enrich v5
# ============================================================================
$inFile = "D:\work\openclaude\dumps\jsonc\component_history_v5.jsonl"
$outFile = "D:\work\openclaude\dumps\jsonc\component_history_v5_final.jsonl"
$out = [System.IO.StreamWriter]::new($outFile, $false, [System.Text.UTF8Encoding]::new($false))

$stats = @{ post_oem_upgraded = 0; engine_csn_upgraded = 0; apu_csn_upgraded = 0 }

foreach ($line in [System.IO.File]::ReadLines($inFile)) {
    $obj = $line | ConvertFrom-Json
    if ($obj.type -eq 'header') {
        # Upgrade asset_snapshot with full per-engine/APU data
        $obj.asset_snapshot.lh_engine_snapshot = [ordered]@{
            sn = $snap.lh[3]; tsn = $snap.lh[4]; csn = [int]$snap.lh[5]
            source = "Annex 6 PPI Summary, $($annex6.source_path) p.$($annex6.source_page)"
        }
        $obj.asset_snapshot.rh_engine_snapshot = [ordered]@{
            sn = $snap.rh[3]; tsn = $snap.rh[4]; csn = [int]$snap.rh[5]
            source = "Annex 6 PPI Summary, $($annex6.source_path) p.$($annex6.source_page)"
        }
        $obj.asset_snapshot.apu_snapshot = [ordered]@{
            sn = $snap.apu[3]; tsh = $snap.apu[4]; csn = $snap.apu[5]
            source = "Annex 6 PPI Summary, $($annex6.source_path) p.$($annex6.source_page)"
        }
        $obj.schema_version = "component_history_v5_final"
        $obj.note = "$($obj.note) Phase C added per-engine/APU snapshots and Annex 6 Summary of Findings explicit post-OEM install events (FCC chain, life raft, cooling turbine, etc.)."
        $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        continue
    }
    if ($obj.type -ne 'component_currently_installed' -and $obj.type -ne 'component_historical') {
        $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
        continue
    }

    $sn = $obj.identity.serial_number
    $pn = $obj.identity.part_number
    $cyc = $obj.identity.cycle_counter

    # Engine/APU CSN upgrade
    if ($obj.installed_since_new.value -eq $true) {
        if ($cyc -eq 'engine') {
            $obj.totals.current_csn.value = "LH=$([int]$snap.lh[5]) / RH=$([int]$snap.rh[5])"
            $obj.totals.current_csn.method = "confirmed"
            $obj.totals.current_csn.outcome = "csn_from_ppi_per_engine_snapshot"
            $obj.totals.current_csn.reasoning = "Engine-mounted part (ATA $($obj.identity.ata_chapter)). Per-engine cycle counters from Annex 6 PPI Summary: LH engine $($snap.lh[5]) cycles, RH engine $($snap.rh[5]) cycles. Engine assignment (LH/RH) is not derivable from graph alone - both values provided."
            $stats.engine_csn_upgraded++
        } elseif ($cyc -eq 'apu') {
            $obj.totals.current_csn.value = $snap.apu[5]
            $obj.totals.current_csn.method = "confirmed"
            $obj.totals.current_csn.outcome = "csn_from_ppi_apu_snapshot"
            $obj.totals.current_csn.reasoning = "APU-mounted part (ATA $($obj.identity.ata_chapter)). APU snapshot from Annex 6 PPI Summary: TSH $($snap.apu[4]), CSN $($snap.apu[5])."
            $stats.apu_csn_upgraded++
        }
    }

    # Check for post-OEM install match
    $key = "$pn::$sn"
    $hit = $null
    if ($postOemByPnSn.ContainsKey($key)) { $hit = $postOemByPnSn[$key][0] }
    elseif ($postOemBySn.ContainsKey($sn)) { $hit = $postOemBySn[$sn][0] }
    if ($hit) {
        $stats.post_oem_upgraded++
        $isoDate = ToIsoDate $hit.date_text
        # Flip installed_since_new -> false (real post-OEM install)
        $obj.installed_since_new = [ordered]@{
            value = $false
            method = 'confirmed'
            outcome = 'post_oem_install_recorded_in_annex6'
            reasoning = "Post-OEM installation recorded in Annex 6 PPI Summary of Findings (row from 'Summary of Findings' table). Installed $($hit.date_text) under work order $($hit.wo_text). Part: $($hit.description)."
            citation = $hit.citation
            rationale_codes = @('post_oem_install_recorded_in_annex6')
            recommended_documents = @("Work order $($hit.wo_text) work package (typically Box 1 or Box 2)")
        }
        # Upgrade first_install_date
        $obj.first_install_date = [ordered]@{
            value = $isoDate
            method = 'confirmed'
            outcome = 'install_event_in_annex6'
            reasoning = "Installed on $isoDate ($($hit.date_text)) under work order $($hit.wo_text). Recorded in Annex 6 Summary of Findings."
            citation = $hit.citation
        }
        # Reset first_install_ac_tsn/csn (since-new rule no longer applies)
        $obj.first_install_ac_tsn = [ordered]@{
            value = $null; method = 'unresolvable'
            outcome = 'tsn_at_post_oem_install_not_in_summary_table'
            reasoning = "Post-OEM install on $isoDate under WO $($hit.wo_text). The Annex 6 Summary row records date and WO but does not stamp the AC TSN at the install moment. The TSN would be on the CRS or work card for WO $($hit.wo_text) (typically in Box 1 Binder 2 or Box 2 Binder 6 of the dossier)."
        }
        $obj.first_install_ac_csn = [ordered]@{
            value = $null; method = 'unresolvable'
            outcome = 'csn_at_post_oem_install_not_in_summary_table'
            reasoning = "Same as TSN: the Summary row identifies the WO but the AC CSN at install lives on the WO's CRS / work card."
        }
        # Form 1 status: required, since post-OEM
        $obj.form_1.required = $true
        $obj.form_1.outcome = 'required_pending_form1_in_dossier'
        $obj.form_1.reasoning = "Form 1 required (post-OEM install). The release certificate covering this PN+SN should accompany the install record for WO $($hit.wo_text). Search the WO's work package in Box 1 or Box 2 of the dossier."
        # Add the post-OEM install event to installation_history
        if (-not $obj.installation_history) { $obj.installation_history = @() }
        $event = [ordered]@{
            kind = 'install'; date = $isoDate; work_order = $hit.wo_text
            ac_tsn_at_event = $null; ac_csn_at_event = $null
            description = "Post-OEM install: $($hit.description). Installed $($hit.date_text) under work order $($hit.wo_text)."
            citation = $hit.citation
            source = 'annex_6_summary_of_findings'
        }
        $obj.installation_history = @($event) + @($obj.installation_history)
        # Totals: since-new no longer applies
        $obj.totals.current_tsn.method = 'unresolvable'
        $obj.totals.current_tsn.outcome = 'depends_on_install_ac_tsn'
        $obj.totals.current_tsn.reasoning = "Post-OEM install on $isoDate. TSN = current AC TSN ($($snap.aircraft[4])) minus AC TSN at install. The install TSN is not stamped in the Annex 6 summary - see WO $($hit.wo_text) work package."
        $obj.totals.current_csn.method = 'unresolvable'
        $obj.totals.current_csn.outcome = 'depends_on_install_ac_csn'
        $obj.totals.current_csn.reasoning = "Post-OEM install on $isoDate. CSN computation requires the AC CSN at install (not in Summary table)."
        # Mark annex 6 source
        $obj | Add-Member -NotePropertyName annex6_summary_finding -NotePropertyValue $hit -Force
    }

    $out.WriteLine(($obj | ConvertTo-Json -Depth 30 -Compress))
}
$out.Flush(); $out.Close()

$fi = Get-Item $outFile
"=== component_history_v5_final.jsonl ==="
"  size:  {0:N2} MB" -f ($fi.Length/1MB)
"  lines: $((Get-Content $outFile | Measure-Object -Line).Lines)"
""
"=== Phase C stats ==="
$stats.GetEnumerator() | ForEach-Object { "  {0,-30} {1}" -f $_.Key, $_.Value }
"Total elapsed: $($sw.Elapsed.TotalSeconds.ToString('N1'))s"
