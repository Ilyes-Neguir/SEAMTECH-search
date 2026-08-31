param(
    [string]$Config = "config/config.json"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot $Config
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$Url = "http://127.0.0.1:8000"

Set-Location $ProjectRoot

try {
    Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
} catch {
    Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "seamtech_search", "serve", "--config", $ConfigPath) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
}

Start-Process $Url
