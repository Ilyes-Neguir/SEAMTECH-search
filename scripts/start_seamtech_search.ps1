param(
    [string]$Config = "config/config.json"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot $Config
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$BackendUrl = "http://127.0.0.1:8000"
$FrontendPort = 3000
$FrontendUrl = "http://127.0.0.1:$FrontendPort"
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$StandaloneRoot = Join-Path $FrontendRoot ".next\standalone"
$StandaloneServer = Join-Path $StandaloneRoot "server.js"

Set-Location $ProjectRoot

. (Join-Path $PSScriptRoot "ensure_postgres.ps1")

try {
    Invoke-WebRequest -Uri "$BackendUrl/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
} catch {
    Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "seamtech_search", "serve", "--config", $Config) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden

    $backendReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-WebRequest -Uri "$BackendUrl/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $backendReady = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $backendReady) {
        throw "SEAMTECH backend did not become healthy at $BackendUrl."
    }
}

if (-not (Test-Path $StandaloneServer)) {
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw "pnpm is required for the first frontend build. Install Node.js and pnpm, then run the launcher again."
    }

    Push-Location $FrontendRoot
    try {
        pnpm install --frozen-lockfile
        pnpm build
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $StandaloneServer)) {
    throw "The Next.js standalone build did not produce $StandaloneServer."
}

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is required to start the frontend. Install Node.js and pnpm, then run the launcher again."
}

$FrontendStatic = Join-Path $FrontendRoot ".next\static"
$FrontendPublic = Join-Path $FrontendRoot "public"
$StandaloneStatic = Join-Path $StandaloneRoot ".next\static"
$StandalonePublic = Join-Path $StandaloneRoot "public"

New-Item -ItemType Directory -Path $StandaloneStatic -Force | Out-Null
Copy-Item -Path (Join-Path $FrontendStatic "*") -Destination $StandaloneStatic -Recurse -Force
New-Item -ItemType Directory -Path $StandalonePublic -Force | Out-Null
Copy-Item -Path (Join-Path $FrontendPublic "*") -Destination $StandalonePublic -Recurse -Force

try {
    Invoke-WebRequest -Uri "$FrontendUrl/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
} catch {
    if (Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue) {
        $FrontendPort = 3001
        $FrontendUrl = "http://127.0.0.1:$FrontendPort"
    }
    $env:SEAMTECH_API_URL = $BackendUrl
    $env:PORT = "$FrontendPort"
    Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/c", "pnpm start") `
        -WorkingDirectory $FrontendRoot `
        -WindowStyle Hidden

    $frontendReady = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Invoke-WebRequest -Uri "$FrontendUrl/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $frontendReady = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $frontendReady) {
        throw "SEAMTECH frontend did not become healthy at $FrontendUrl."
    }
}

Start-Process $FrontendUrl
