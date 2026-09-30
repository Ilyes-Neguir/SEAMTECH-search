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

# Machine vierge : docker-compose.yml exige SEPT secrets (`${VAR:?}`) — chaque
# variable absente empêche `docker compose up` de démarrer. Tous sont générés
# ici en local aléatoire (jamais dans Git : .env est ignoré), puis réutilisés
# aux exécutions suivantes.
$Secrets = @(
    "POSTGRES_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "SEAMTECH_AUTH_TOKEN",
    "SEAMTECH_UI_PASSWORD",
    "SEAMTECH_SESSION_SECRET"
)
foreach ($nom in $Secrets) {
    $valeur = Get-EnvValue $nom
    if (-not $valeur -or $valeur -eq "change-me") {
        $valeur = "seamtech-" + ([guid]::NewGuid().ToString("N"))
        Set-EnvValue $nom $valeur
    }
}
$password = Get-EnvValue "POSTGRES_PASSWORD"
$authToken = Get-EnvValue "SEAMTECH_AUTH_TOKEN"

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

# L'image MinIO (quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z) n'est plus
# distribuée par aucun registre — elle doit être RECONSTRUITE depuis les
# sources archivées avant le premier `docker compose up`, sinon la pile ne
# démarre jamais sur machine vierge. Idempotent : ne reconstruit pas si
# l'image est déjà présente.
$ImageMinio = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
docker image inspect $ImageMinio *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Image MinIO absente — construction locale depuis les sources archivées..."
    $Bash = $null
    foreach ($candidat in @("bash", "C:\Program Files\Git\bin\bash.exe", "C:\Program Files (x86)\Git\bin\bash.exe")) {
        if (Get-Command $candidat -ErrorAction SilentlyContinue) {
            $Bash = $candidat
            break
        }
        if (Test-Path $candidat) {
            $Bash = $candidat
            break
        }
    }
    if (-not $Bash) {
        throw "bash (Git for Windows) est requis pour construire l'image MinIO via scripts/construire_image_minio.sh. Installez Git for Windows puis relancez."
    }
    & $Bash (Join-Path $ProjectRoot "scripts/construire_image_minio.sh")
    if ($LASTEXITCODE -ne 0) {
        throw "Construction de l'image MinIO impossible (scripts/construire_image_minio.sh)."
    }
}

Push-Location $ProjectRoot
try {
    docker compose -f $ComposeFile up -d --wait postgres minio redis
} finally {
    Pop-Location
}

$env:SEAMTECH_DATABASE_URL = "postgresql://seamtech:$password@127.0.0.1:5433/seamtech_search"
$env:SEAMTECH_S3_ENDPOINT_URL = "http://127.0.0.1:9000"
$env:SEAMTECH_REDIS_URL = "redis://127.0.0.1:6379/0"
