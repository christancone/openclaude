# Restore dumps/neo4j.dump into the compose neo4j-data volume,
# overwriting the existing graph.
#
# Usage (from repo root):
#   .\scripts\restore_neo4j_dump.ps1
#   .\scripts\restore_neo4j_dump.ps1 -DumpPath .\dumps\neo4j.dump
#
# Requires: docker, the compose stack defined in compose.yaml, and a .env
# with NEO4J_PASSWORD (only needed if you want sparengine to come back up).

[CmdletBinding()]
param(
    [string]$DumpPath = "dumps\neo4j.dump",
    [string]$Database = "neo4j",
    [string]$Image    = "neo4j:5.26-community"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$dumpFull = (Resolve-Path $DumpPath).Path
$dumpDir  = Split-Path -Parent $dumpFull
$dumpFile = Split-Path -Leaf   $dumpFull

Write-Host "Repo root : $repoRoot"
Write-Host "Dump file : $dumpFull"

# 1. Resolve the named volume for neo4j-data. Compose prefixes it with the
#    project name (folder name, lowercased), so look it up rather than guess.
$volume = docker compose config --format json 2>$null |
    ConvertFrom-Json |
    ForEach-Object { $_.volumes.'neo4j-data'.name } |
    Select-Object -First 1

if (-not $volume) {
    $project = (Split-Path -Leaf $repoRoot).ToLower() -replace '[^a-z0-9]',''
    $volume  = "${project}_neo4j-data"
    Write-Host "Falling back to derived volume name: $volume"
}
Write-Host "Volume    : $volume"

# 2. Stop services that hold the database open. neo4j-admin load requires
#    the target DB to be offline.
Write-Host "`n[1/4] Stopping sparengine and neo4j..."
docker compose stop sparengine neo4j-init neo4j 2>&1 | Out-Host

# 3. Run neo4j-admin in a one-shot container with the same data volume
#    mounted. --overwrite-destination wipes the existing 'neo4j' database.
Write-Host "`n[2/4] Loading dump (overwrite-destination=true)..."
docker run --rm `
    -v "${volume}:/data" `
    -v "${dumpDir}:/backups:ro" `
    $Image `
    neo4j-admin database load $Database --from-path=/backups --overwrite-destination=true
if ($LASTEXITCODE -ne 0) { throw "neo4j-admin load failed (exit $LASTEXITCODE)" }

# 4. Bring neo4j back up and let the init container reapply schema.cypher
#    on top of the restored data (constraints/indexes are idempotent).
Write-Host "`n[3/4] Starting neo4j..."
docker compose up -d neo4j 2>&1 | Out-Host

Write-Host "`n[4/4] Reapplying schema and starting sparengine..."
docker compose up -d --force-recreate neo4j-init 2>&1 | Out-Host
docker compose up -d sparengine 2>&1 | Out-Host

Write-Host "`nDone. Neo4j Browser: http://localhost:7474"
