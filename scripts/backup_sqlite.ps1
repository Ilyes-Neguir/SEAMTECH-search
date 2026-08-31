param(
    [string]$DatabasePath = "data/search.db",
    [string]$BackupDir = "data/backups"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot $DatabasePath
$TargetDir = Join-Path $ProjectRoot $BackupDir
$BackupFile = Join-Path $TargetDir ("search-{0:yyyyMMdd-HHmmss}.db" -f (Get-Date))

if (-not (Test-Path $Source)) {
    throw "SQLite database not found: $Source"
}

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
Copy-Item -Path $Source -Destination $BackupFile
Write-Host "Backup written to $BackupFile"
