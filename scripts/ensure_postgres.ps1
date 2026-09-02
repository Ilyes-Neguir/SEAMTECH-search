$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"

function Get-EnvValue([string]$name) {
    if (-not (Test-Path $EnvFile)) {
        return $null
    }
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^$name=(.*)$" } | Select-Object -First 1
    if ($line) {
        return $Matches[1]
    }
    return $null
}

function Set-EnvValue([string]$name, [string]$value) {
    $lines = @(Get-Content $EnvFile)
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$name=") {
            $found = $true
            "$name=$value"
        } else {
            $line
        }
    }
    if (-not $found) {
        $updated += "$name=$value"
    }
    Set-Content -Path $EnvFile -Value $updated -Encoding ascii
}

if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

$password = Get-EnvValue "POSTGRES_PASSWORD"
if (-not $password -or $password -eq "change-me") {
    $password = "seamtech-" + ([guid]::NewGuid().ToString("N"))
    Set-EnvValue "POSTGRES_PASSWORD" $password
}

$authToken = Get-EnvValue "SEAMTECH_AUTH_TOKEN"
if (-not $authToken -or $authToken -eq "change-me") {
    $authToken = "seamtech-token-" + ([guid]::NewGuid().ToString("N"))
    Set-EnvValue "SEAMTECH_AUTH_TOKEN" $authToken
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required for automatic PostgreSQL setup. Install Docker Desktop and run the launcher again."
}

try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not ready."
    }
} catch {
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerDesktop)) {
        throw "Docker Desktop is installed but not running, and its executable could not be found. Start Docker Desktop and run the launcher again."
    }
    Start-Process -FilePath $dockerDesktop | Out-Null
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            docker info *> $null
            if ($LASTEXITCODE -eq 0) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        throw "Docker Desktop did not become ready within 60 seconds."
    }
}

Push-Location $ProjectRoot
try {
    docker compose -f $ComposeFile up -d --wait postgres
} finally {
    Pop-Location
}

$env:SEAMTECH_DATABASE_URL = "postgresql://seamtech:$password@127.0.0.1:5433/seamtech_search"
