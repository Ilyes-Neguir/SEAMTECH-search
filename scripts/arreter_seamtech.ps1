# Arrêt propre de SEAMTECH Search sur un poste Windows.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\arreter_seamtech.ps1
#   ... -File scripts\arreter_seamtech.ps1 -AvecServices
#
# 1. arrête l'interface (front) et le moteur (back) lancés par
#    scripts\start_seamtech_search.ps1 : par identifiant de processus
#    (data\pids\*.pid), avec repli sur les processus qui écoutent les ports
#    8000 / 3000 / 3001. Les processus enfants sont arrêtés avec leur parent,
#    sinon un node fantôme continuerait à tenir le port 3000.
# 2. avec -AvecServices : arrête AUSSI les conteneurs postgres, minio et redis.
#
# Aucune donnée n'est supprimée : jamais `docker compose down -v`, jamais
# `docker system prune`, aucune suppression de fichier du projet. Les volumes
# Docker et le dossier data\ sont intacts ; le prochain démarrage les retrouve.
#
# Le nom du processus est vérifié avant tout arrêt : un PID recyclé par Windows
# ne doit jamais être tué par erreur.

param(
    [switch]$AvecServices
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$PidsDir = Join-Path $ProjectRoot "data\pids"

function Descendants([int]$Pid) {
    $Resultat = @()
    $Enfants = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = {0}" -f $Pid) -ErrorAction SilentlyContinue)
    foreach ($Enfant in $Enfants) {
        $Resultat += [int]$Enfant.ProcessId
        $Resultat += Descendants ([int]$Enfant.ProcessId)
    }
    return $Resultat
}

function Lire-Pid([string]$chemin) {
    try {
        $brut = (Get-Content -LiteralPath $chemin -Raw).Trim()
        $valeur = 0
        if ([int]::TryParse($brut, [ref]$valeur)) { return $valeur }
    } catch { }
    return 0
}

# Arrête un processus et ses enfants. $Noms attendus : liste blanche de noms de
# processus ; vide = pas de vérification (repli par port).
function Arreter-Processus([int]$Pid, [string[]]$Noms) {
    if ($Pid -le 0) { return $false }
    $Processus = Get-Process -Id $Pid -ErrorAction SilentlyContinue
    if (-not $Processus) { return $false }
    if ($Noms -and ($Noms -notcontains $Processus.ProcessName)) {
        Write-Host ("       PID {0} ignoré : processus « {1} » inattendu (PID recyclé ?)" -f $Pid, $Processus.ProcessName)
        return $false
    }
    $Arbre = @($Pid) + @(Descendants $Pid)
    foreach ($Cible in ($Arbre | Sort-Object -Unique)) {
        Stop-Process -Id $Cible -Force -ErrorAction SilentlyContinue
    }
    return $true
}

Write-Host "Arrêt de SEAMTECH Search..."

$Arretes = 0

$FichierMoteur = Join-Path $PidsDir "backend.pid"
if (Test-Path -LiteralPath $FichierMoteur) {
    $Pid = Lire-Pid $FichierMoteur
    if (Arreter-Processus $Pid @("python", "python3")) {
        Write-Host "       moteur arrêté (PID $Pid)"
        $Arretes++
    }
    Remove-Item -LiteralPath $FichierMoteur -Force -ErrorAction SilentlyContinue
}

$FichierInterface = Join-Path $PidsDir "frontend.pid"
if (Test-Path -LiteralPath $FichierInterface) {
    $Pid = Lire-Pid $FichierInterface
    if (Arreter-Processus $Pid @("cmd", "node", "pnpm")) {
        Write-Host "       interface arrêtée (PID $Pid)"
        $Arretes++
    }
    Remove-Item -LiteralPath $FichierInterface -Force -ErrorAction SilentlyContinue
}

# Repli : aucun identifiant enregistré (lancement d'une version antérieure) —
# on arrête alors ce qui écoute sur les ports de l'application.
if ($Arretes -eq 0) {
    foreach ($Port in @(8000, 3000, 3001)) {
        $Connexions = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        foreach ($Connexion in $Connexions) {
            $Pid = [int]$Connexion.OwningProcess
            if (Arreter-Processus $Pid @()) {
                Write-Host "       processus du port $Port arrêté (PID $Pid)"
                $Arretes++
            }
        }
    }
}

if ($Arretes -eq 0) {
    Write-Host "       SEAMTECH Search n'était pas en cours d'exécution."
}

if ($AvecServices) {
    Write-Host "       Arrêt des conteneurs postgres, minio, redis (les données restent)..."
    docker compose stop postgres minio redis
    if ($LASTEXITCODE -ne 0) {
        Write-Host "       docker compose stop a échoué — vérifiez Docker Desktop." -ForegroundColor Red
    } else {
        Write-Host "       conteneurs arrêtés."
    }
}

# Lancé sans fenêtre depuis le raccourci : la confirmation passe par une bulle.
$Notification = New-Object -ComObject WScript.Shell
if ($Arretes -gt 0) {
    $Notification.Popup("SEAMTECH Search est arrêté. Les données sont conservées.", 5, "SEAMTECH Search", 0 + 64) | Out-Null
} else {
    $Notification.Popup("SEAMTECH Search n'était pas démarré.", 5, "SEAMTECH Search", 0 + 64) | Out-Null
}
exit 0
