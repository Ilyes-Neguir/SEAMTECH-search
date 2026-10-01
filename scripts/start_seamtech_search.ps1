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

# Journal de démarrage et identifiants de processus. Le journal permet de lire
# un échec APRÈS coup (data\logs\, hors Git) au lieu de le voir disparaître avec
# la fenêtre ; les identifiants permettent à scripts\arreter_seamtech.ps1
# d'arrêter exactement les processus lancés ici — et jamais un autre.
$JournalDir = Join-Path $ProjectRoot "data\logs"
$PidsDir = Join-Path $ProjectRoot "data\pids"
New-Item -ItemType Directory -Force -Path $JournalDir | Out-Null
New-Item -ItemType Directory -Force -Path $PidsDir | Out-Null
$Journal = Join-Path $JournalDir ("lancement-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

Set-Location $ProjectRoot

try { Start-Transcript -Path $Journal -Force -ErrorAction SilentlyContinue | Out-Null } catch { }

$script:CodeSortie = 0
try {
    . (Join-Path $PSScriptRoot "ensure_postgres.ps1")

    try {
        Invoke-WebRequest -Uri "$BackendUrl/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
    } catch {
        $Processus = Start-Process `
            -FilePath $Python `
            -ArgumentList @("-m", "seamtech_search", "serve", "--config", $Config) `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -PassThru
        Set-Content -Path (Join-Path $PidsDir "backend.pid") -Value $Processus.Id -Encoding ascii

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
        $Processus = Start-Process `
            -FilePath "cmd.exe" `
            -ArgumentList @("/c", "pnpm start") `
            -WorkingDirectory $FrontendRoot `
            -WindowStyle Hidden `
            -PassThru
        Set-Content -Path (Join-Path $PidsDir "frontend.pid") -Value $Processus.Id -Encoding ascii
        Set-Content -Path (Join-Path $PidsDir "frontend.port") -Value $FrontendPort -Encoding ascii

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
} catch {
    $script:CodeSortie = 1
    try { Stop-Transcript | Out-Null } catch { }
    Add-Content -Path $Journal -Value ("ERREUR|{0}|{1}" -f (Get-Date -Format o), $_.Exception.Message) -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "SEAMTECH Search n'a pas demarre : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Detail complet : $Journal" -ForegroundColor Red
    # Lancé sans fenêtre depuis l'icône du bureau : l'échec doit rester visible.
    $Notification = New-Object -ComObject WScript.Shell
    $Notification.Popup(("SEAMTECH Search n'a pas demarre.`n`n{0}`n`nDetail : {1}" -f $_.Exception.Message, $Journal), 0, "SEAMTECH Search", 0 + 16) | Out-Null
    exit 1
}

try { Stop-Transcript | Out-Null } catch { }
exit $script:CodeSortie
