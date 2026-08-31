param(
    [string]$DatabaseUrl = "postgresql://seamtech:seamtech@localhost:5432/seamtech_search",
    [string]$BackupDir = "data/backups"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TargetDir = Join-Path $ProjectRoot $BackupDir
$BackupFile = Join-Path $TargetDir ("seamtech-search-{0:yyyyMMdd-HHmmss}.dump" -f (Get-Date))

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
pg_dump --format=custom --file=$BackupFile $DatabaseUrl
Write-Host "Backup written to $BackupFile"
