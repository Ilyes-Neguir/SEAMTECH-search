param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$DatabasePath = "data/search.db"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Target = Join-Path $ProjectRoot $DatabasePath
$TargetDir = Split-Path -Parent $Target

if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
Copy-Item -Path $BackupFile -Destination $Target -Force
Write-Host "Restore completed to $Target"
