param(
    [string]$Config = "config/config.json",
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir ("indexing-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot
Start-Transcript -Path $LogFile -Append | Out-Null

try {
    $args = @("-m", "seamtech_search", "index", "--config", $Config)
    if ($Rebuild) {
        $args += "--rebuild"
    }
    python @args
    if ($LASTEXITCODE -ne 0) {
        throw "Indexing failed with exit code $LASTEXITCODE"
    }
} finally {
    Stop-Transcript | Out-Null
}
