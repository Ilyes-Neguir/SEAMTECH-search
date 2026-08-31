param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$DatabaseUrl = "postgresql://seamtech:seamtech@localhost:5432/seamtech_search"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}

pg_restore --clean --if-exists --no-owner --dbname=$DatabaseUrl $BackupFile
Write-Host "Restore completed from $BackupFile"
