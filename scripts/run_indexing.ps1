param(
    [string]$Config = "config/config.json",
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir ("indexing-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
$EnvFile = Join-Path $ProjectRoot ".env"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^([^#=][^=]*)=(.*)$") {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
}
if (-not $env:SEAMTECH_DATABASE_URL) {
    throw "SEAMTECH_DATABASE_URL is required for scheduled indexing. Run the desktop launcher once or configure a managed PostgreSQL URL."
}
Start-Transcript -Path $LogFile -Append | Out-Null

try {
    $args = @("-m", "seamtech_search", "index", "--config", $Config)
    if ($Rebuild) {
        $args += "--rebuild"
    }
    $IndexerPython = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
    & $IndexerPython @args
    if ($LASTEXITCODE -ne 0) {
        throw "Indexing failed with exit code $LASTEXITCODE"
    }
} finally {
    Stop-Transcript | Out-Null
}
