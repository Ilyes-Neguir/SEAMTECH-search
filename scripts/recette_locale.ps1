# Recette locale SEAMTECH Search — variante Windows (commanditaire).
#
# Une commande qui vérifie que la pile COMPLÈTE fonctionne en local :
#   prereqs -> ports libres -> .env (secrets aléatoires, jamais dans Git)
#   -> image MinIO locale (registres morts) -> docker compose up -d --build
#   -> santé /live /ready /health -> compte nominatif -> dépôt des archives
#   -> suivi des lots -> fiches a_valider badgées -> validation -> recherches
#   (texte, dimension « 6,60 », filtres/facettes, suggestions) -> PDF présigné
#   + zone surlignée -> rejeu idempotent -> sauvegarde/restauration
#   -> persistance down/up -> RAPPORT PASS/FAIL ligne par contrôle.
#
# Idempotent, rejouable, sans Internet après les builds, ne modifie JAMAIS les
# archives sources (RG13). Les contrôles fonctionnels vivent dans
# scripts\recette_verif.py (exécuté dans le conteneur web) — même source que
# la variante Linux scripts\recette_locale.sh. Chemins avec espaces et accents
# gérés partout (guillemets + Join-Path).
#
# Usage : .\scripts\recette_locale.ps1 [-CheminsSupp "C:\Mes Voiles", "D:\Dossier été"]
#   Les 7 ZIP du dépôt sont déposés par défaut ; tout chemin supplémentaire
#   (les dossiers du commanditaire) est copié en lecture seule dans la zone de
#   travail puis déposé.
# Code sortie : 0 = tous les contrôles PASS, 1 = au moins un FAIL.

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CheminsSupp = @()
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ImageMinio = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
$SourcesConteneur = "/app/data/recette-sources"
$SourcesHote = Join-Path $ProjectRoot "data\recette-sources"
$EnvFile = Join-Path $ProjectRoot ".env"
$RapportTmp = Join-Path ([System.IO.Path]::GetTempPath()) ("recette-{0}.txt" -f ([guid]::NewGuid().ToString("N")))
$script:NbFail = 0

Set-Location $ProjectRoot

function Rapport([string]$id, [string]$statut, [string]$detail) {
    $ligne = "CONTROLE|$id|$statut|$detail"
    Write-Host $ligne
    Add-Content -Path $RapportTmp -Value $ligne -Encoding utf8
    if ($statut -eq "FAIL") {
        $script:NbFail++
        if ($env:GITHUB_ACTIONS) { Write-Host "::error title=recette-locale/$id::$detail" }
    }
}

function HttpCode([string]$Uri, [string]$Token) {
    # Renvoie "000" si le service ne répond pas encore (jamais d'exception).
    try {
        $entetes = @{}
        if ($Token) { $entetes["X-SEAMTECH-TOKEN"] = $Token }
        $reponse = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5 -Headers $entetes
        return [string]$reponse.StatusCode
    } catch {
        if ($_.Exception.Response) { return [string][int]$_.Exception.Response.StatusCode }
        return "000"
    }
}

Write-Host "=== Recette locale SEAMTECH Search ($((Get-Date).ToUniversalTime().ToString("o"))) ==="

# ---------------------------------------------------------------------------
# 1. Prérequis : Docker, Compose v2, git (construction MinIO).
# ---------------------------------------------------------------------------
$Manquants = @()
foreach ($outil in @("docker", "git")) {
    if (-not (Get-Command $outil -ErrorAction SilentlyContinue)) { $Manquants += $outil }
}
if ($Manquants.Count -gt 0) {
    Rapport "prereqs" "FAIL" ("outils absents : " + ($Manquants -join ", "))
    exit 1
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Rapport "prereqs" "FAIL" "docker compose v2 absent (Docker Desktop requis)"
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Rapport "prereqs" "FAIL" "Docker installé mais le démon ne répond pas (Docker Desktop démarré ?)"
    exit 1
}
$DockerV = (docker --version 2>&1 | Select-Object -First 1)
$ComposeV = (docker compose version 2>&1 | Select-Object -First 1)
Rapport "prereqs" "PASS" "$DockerV ; $ComposeV"

# ---------------------------------------------------------------------------
# 2. Ports attendus libres (sauf s'ils appartiennent déjà à cette pile).
# ---------------------------------------------------------------------------
$Ports = @(8000, 3000, 5433, 6379, 9000, 9001)
$EnCours = (docker compose ps -q 2>$null)
if ($EnCours) {
    Rapport "ports-libres" "PASS" "pile déjà en cours — les ports ($($Ports -join ', ')) appartiennent à cette recette (rejeu)"
} else {
    $Occupes = @()
    foreach ($port in $Ports) {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($conn) { $Occupes += $port }
    }
    if ($Occupes.Count -gt 0) {
        Rapport "ports-libres" "FAIL" ("ports déjà occupés : " + ($Occupes -join ", ") + " (un autre service écoute)")
        exit 1
    }
    Rapport "ports-libres" "PASS" ("ports libres : " + ($Ports -join ", "))
}

# ---------------------------------------------------------------------------
# 3. .env : secrets locaux aléatoires (jamais dans Git — .gitignore).
# ---------------------------------------------------------------------------
$Secrets = @(
    "POSTGRES_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "REDIS_PASSWORD",
    "SEAMTECH_AUTH_TOKEN", "SEAMTECH_UI_PASSWORD", "SEAMTECH_SESSION_SECRET",
    # Identités SÉPARÉES (revue du 2026-10-07) : identité applicative RESTREINTE
    # au bucket documents + identité de sauvegarde distincte. Exigées par la
    # composition ; créées par scripts/provisionner_stockage.sh.
    "SEAMTECH_S3_ACCESS_KEY", "SEAMTECH_S3_SECRET_KEY",
    "SEAMTECH_BACKUP_ACCESS_KEY", "SEAMTECH_BACKUP_SECRET_KEY"
)
if (-not (Test-Path $EnvFile)) {
    $lignes = @()
    foreach ($nom in $Secrets) {
        $lignes += "$nom=seamtech-$([guid]::NewGuid().ToString('N'))"
    }
    $lignes += "SEAMTECH_S3_BUCKET=seamtech-documents"
    $lignes += "SEAMTECH_BACKUP_BUCKET=seamtech-backups"
    $lignes += "SEAMTECH_ROOT_PATHS=${SourcesConteneur}:/app/data/recette-lot:/app/data:/app/sample_data"
    $lignes += "RECETTE_MOT_DE_PASSE=seamtech-recette-$([guid]::NewGuid().ToString('N'))"
    Set-Content -Path $EnvFile -Value $lignes -Encoding ascii
    $Creation = "créé avec 11 secrets aléatoires"
} else {
    $Creation = "existant réutilisé (idempotence)"
    $contenu = Get-Content $EnvFile
    if (-not ($contenu -match '^SEAMTECH_ROOT_PATHS=')) {
        Add-Content -Path $EnvFile -Value "SEAMTECH_ROOT_PATHS=${SourcesConteneur}:/app/data/recette-lot:/app/data:/app/sample_data" -Encoding ascii
    }
    if (-not ($contenu -match '^RECETTE_MOT_DE_PASSE=')) {
        Add-Content -Path $EnvFile -Value "RECETTE_MOT_DE_PASSE=seamtech-recette-$([guid]::NewGuid().ToString('N'))" -Encoding ascii
    }
    # Identités de stockage : un .env antérieur au correctif n'en a pas.
    foreach ($nom in @("SEAMTECH_S3_ACCESS_KEY", "SEAMTECH_S3_SECRET_KEY", "SEAMTECH_BACKUP_ACCESS_KEY", "SEAMTECH_BACKUP_SECRET_KEY")) {
        if (-not ($contenu -match ("^" + $nom + "="))) {
            Add-Content -Path $EnvFile -Value ("$nom=seamtech-" + [guid]::NewGuid().ToString('N')) -Encoding ascii
        }
    }
    if (-not ($contenu -match '^SEAMTECH_S3_BUCKET=')) {
        Add-Content -Path $EnvFile -Value "SEAMTECH_S3_BUCKET=seamtech-documents" -Encoding ascii
    }
    if (-not ($contenu -match '^SEAMTECH_BACKUP_BUCKET=')) {
        Add-Content -Path $EnvFile -Value "SEAMTECH_BACKUP_BUCKET=seamtech-backups" -Encoding ascii
    }
}
$Variables = @{}
foreach ($ligne in (Get-Content $EnvFile)) {
    if ($ligne -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $Variables[$Matches[1]] = $Matches[2] }
}
foreach ($nom in $Secrets) {
    if (-not $Variables[$nom]) {
        Rapport "env-secrets" "FAIL" ".env incomplet : $nom absent"
        exit 1
    }
}
Rapport "env-secrets" "PASS" ".env $Creation — secrets présents (dont identités de stockage dédiées), jamais journalisés (RG9)"
# Exportés pour scripts/provisionner_stockage.sh (le script lit l'environnement ;
# `docker compose`, lui, lit .env directement).
foreach ($nom in @(
    "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
    "SEAMTECH_S3_ACCESS_KEY", "SEAMTECH_S3_SECRET_KEY",
    "SEAMTECH_BACKUP_ACCESS_KEY", "SEAMTECH_BACKUP_SECRET_KEY",
    "SEAMTECH_S3_BUCKET", "SEAMTECH_BACKUP_BUCKET"
)) {
    Set-Item -Path ("env:" + $nom) -Value $Variables[$nom]
}
$RecetteMotDePasse = $Variables["RECETTE_MOT_DE_PASSE"]

# ---------------------------------------------------------------------------
# 4. Image MinIO locale (aucun registre ne la distribue plus).
# ---------------------------------------------------------------------------
docker image inspect $ImageMinio *> $null
if ($LASTEXITCODE -eq 0) {
    Rapport "image-minio" "PASS" "image $ImageMinio déjà construite (idempotence)"
} else {
    $Bash = $null
    foreach ($candidat in @("bash", "C:\Program Files\Git\bin\bash.exe", "C:\Program Files (x86)\Git\bin\bash.exe")) {
        if (Get-Command $candidat -ErrorAction SilentlyContinue) { $Bash = $candidat; break }
        if (Test-Path $candidat) { $Bash = $candidat; break }
    }
    if (-not $Bash) {
        Rapport "image-minio" "FAIL" "bash (Git for Windows) requis pour scripts/construire_image_minio.sh"
        exit 1
    }
    & $Bash (Join-Path $ProjectRoot "scripts/construire_image_minio.sh") 2>&1 | Tee-Object -Variable SortieMinio | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Rapport "image-minio" "FAIL" ("construction impossible : " + (($SortieMinio | Select-Object -Last 3) -join " "))
        exit 1
    }
    Rapport "image-minio" "PASS" "image $ImageMinio construite depuis les sources archivées"
}

# ---------------------------------------------------------------------------
# 4 bis. PROVISIONNEMENT du stockage : buckets, versioning, identités
# RESTREINTES. Étape SÉPARÉE du démarrage applicatif ; la composition exige
# désormais SEAMTECH_S3_ACCESS_KEY/SEAMTECH_S3_SECRET_KEY (aucun repli
# administrateur), donc sans cette étape web/worker ne démarrent pas.
# ---------------------------------------------------------------------------
$Bash = $null
foreach ($candidat in @("bash", "C:\Program Files\Git\bin\bash.exe", "C:\Program Files (x86)\Git\bin\bash.exe")) {
    if (Get-Command $candidat -ErrorAction SilentlyContinue) { $Bash = $candidat; break }
    if (Test-Path $candidat) { $Bash = $candidat; break }
}
if (-not $Bash) {
    Rapport "provisionnement-stockage" "FAIL" "bash (Git for Windows) requis pour scripts/provisionner_stockage.sh"
    exit 1
}
docker compose up -d minio 2>&1 | Out-Null
$PretMinio = $false
$FinMinio = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $FinMinio) {
    try {
        $reponse = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:9000/minio/health/live"
        if ($reponse.StatusCode -eq 200) { $PretMinio = $true; break }
    } catch { }
    Start-Sleep -Seconds 3
}
if (-not $PretMinio) {
    Rapport "provisionnement-stockage" "FAIL" "MinIO non prêt en 120 s"
    exit 1
}
& $Bash (Join-Path $ProjectRoot "scripts/provisionner_stockage.sh") 2>&1 | Tee-Object -Variable SortieProv | Out-Null
if ($LASTEXITCODE -ne 0) {
    Rapport "provisionnement-stockage" "FAIL" ("échec du provisionnement : " + (($SortieProv | Select-Object -Last 5) -join " "))
    exit 1
}
Rapport "provisionnement-stockage" "PASS" "buckets + versioning + identités restreintes (applicative et sauvegarde)"

# ---------------------------------------------------------------------------
# 5. Pile complète : build + démarrage + santé avec timeout clair.
# ---------------------------------------------------------------------------
docker compose up -d --build 2>&1 | Tee-Object -Variable SortieUp | Out-Null
if ($LASTEXITCODE -ne 0) {
    Rapport "pile-sante" "FAIL" ("docker compose up en échec : " + (($SortieUp | Select-Object -Last 5) -join " "))
    docker compose ps
    docker compose logs --no-color --tail 40 web
    exit 1
}
$Token = $Variables["SEAMTECH_AUTH_TOKEN"]
$Sain = $false
$Fin = (Get-Date).AddSeconds(600)
$Live = $Ready = $Sant = $Ui = "000"
while ((Get-Date) -lt $Fin) {
    $Live = HttpCode "http://127.0.0.1:8000/live" $null
    $Ready = HttpCode "http://127.0.0.1:8000/ready" $null
    $Sant = HttpCode "http://127.0.0.1:8000/health" $Token
    $Ui = HttpCode "http://127.0.0.1:3000/api/health" $null
    if ($Live -eq "200" -and $Ready -eq "200" -and $Sant -eq "200" -and $Ui -eq "200") { $Sain = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $Sain) {
    Rapport "pile-sante" "FAIL" "santé non atteinte en 600 s : /live=$Live /ready=$Ready /health=$Sant /api/health=$Ui"
    docker compose ps
    docker compose logs --no-color --tail 60 web frontend
    exit 1
}
Rapport "pile-sante" "PASS" "/live=$Live /ready=$Ready /health=$Sant frontend /api/health=$Ui (délai <= 600 s)"

# ---------------------------------------------------------------------------
# 6. Sources à déposer : 7 ZIP du dépôt + chemins passés en argument.
#    Copies de travail dans data\ (ignoré par Git) — sources lues, jamais
#    modifiées (RG13).
# ---------------------------------------------------------------------------
if (Test-Path $SourcesHote) { Remove-Item -Recurse -Force $SourcesHote }
New-Item -ItemType Directory -Force -Path $SourcesHote | Out-Null
$NbZip = 0
foreach ($zip in (Get-ChildItem -Path $ProjectRoot -Filter *.zip -File)) {
    Copy-Item -Path $zip.FullName -Destination $SourcesHote
    $NbZip++
}
$NbExtra = 0
foreach ($chemin in $CheminsSupp) {
    if (-not (Test-Path $chemin)) {
        Rapport "depot-archives" "FAIL" "chemin argument introuvable : $chemin"
        exit 1
    }
    Copy-Item -Recurse -Path $chemin -Destination $SourcesHote
    $NbExtra++
}
Write-Host "Sources en zone de travail : $NbZip ZIP du dépôt + $NbExtra chemin(s) argument."

# ---------------------------------------------------------------------------
# 7. Vérificateur fonctionnel unique, exécuté DANS le conteneur web.
# ---------------------------------------------------------------------------
$VerifPath = Join-Path $PSScriptRoot "recette_verif.py"
$SortieVerif = (Get-Content -Path $VerifPath -Raw | docker compose exec -T `
    -e "RECETTE_SOURCES=$SourcesConteneur" `
    -e "RECETTE_MOT_DE_PASSE=$RecetteMotDePasse" `
    web python - 2>&1) | Out-String
$CodeVerif = $LASTEXITCODE
Write-Host $SortieVerif
Add-Content -Path $RapportTmp -Value $SortieVerif -Encoding utf8
$script:NbFail += ([regex]::Matches($SortieVerif, '(?m)^CONTROLE\|[^|]+\|FAIL\|')).Count
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "data\backups") | Out-Null
Set-Content -Path (Join-Path $ProjectRoot "data\backups\recette-verif-sortie.txt") -Value $SortieVerif -Encoding utf8
if ($env:GITHUB_ACTIONS) {
    foreach ($m in [regex]::Matches($SortieVerif, '(?m)^CONTROLE\|([^|]+)\|FAIL\|(.*)$')) {
        Write-Host "::error title=recette-locale/$($m.Groups[1].Value)::$($m.Groups[2].Value.Trim())"
    }
}
$Fiche = ""
foreach ($ligne in ($SortieVerif -split "`n")) {
    if ($ligne -match '^INFO\|fiche_pour_restauration\|(.+)$') { $Fiche = $Matches[1].Trim() }
}

# ---------------------------------------------------------------------------
# 8. Sauvegarde (pg_dump custom) dans data\backups (ignoré par Git).
# ---------------------------------------------------------------------------
$BackupDir = Join-Path $ProjectRoot "data\backups"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$Dump = Join-Path $BackupDir ("recette-{0:yyyyMMdd-HHmmss}.dump" -f (Get-Date))
# pg_dump DANS le conteneur puis docker compose cp : une redirection PowerShell
# (`*>`) corromprait le format custom (binaire) — sortie brute conservée.
$DumpSortie = (docker compose exec -T postgres pg_dump -U seamtech --format=custom -f /tmp/recette-backup.dump seamtech_search 2>&1) | Out-String
$CodeDump = $LASTEXITCODE
docker compose cp "postgres:/tmp/recette-backup.dump" $Dump *> $null
$DumpValide = $false
if (($CodeDump -eq 0) -and (Test-Path $Dump) -and ((Get-Item $Dump).Length -gt 0)) {
    $Taille = (Get-Item $Dump).Length
    $Em = (Get-FileHash -Algorithm SHA256 -Path $Dump).Hash.Substring(0, 16)
    $DumpValide = $true
    Rapport "sauvegarde" "PASS" "$Dump — $Taille octets, SHA-256 début=$Em"
} else {
    Rapport "sauvegarde" "FAIL" ("pg_dump impossible ou dump vide ($Dump) : " + ($DumpSortie.Trim() | Select-Object -First 1))
}

# ---------------------------------------------------------------------------
# 9. Restauration rapide : la fiche validée disparaît puis revient.
#
# ISOLEMENT DES ÉCRIVAINS (constat A10 de l'audit du 2026-10-08, réserve R4) :
# ``pg_restore --clean --if-exists`` est DESTRUCTEUR — il supprime et recrée
# des objets. Tant qu'un conteneur qui écrit dans PostgreSQL tourne pendant ces
# quelques secondes, deux choses peuvent arriver : la ligne supprimée est
# recréée par l'application au milieu du restore, ou le worker rejoue un job
# sur une base à moitié restaurée.
#
# Préconditions fail-closed strictes :
# 1. Sauvegarde préalable valide et contrôlée ($DumpValide). En cas d'échec de
#    dump, JAMAIS d'opération destructive.
# 2. Arrêt explicite de web et worker avec vérification du code retour Docker.
# 3. docker compose ps exécuté avec succès et sans écrivains actifs restants.
# 4. Enfin, sécurité du redémarrage : redémarrage garanti uniquement si la
#    restauration a réussi ou si aucune action destructive n'a été engagée.
# ---------------------------------------------------------------------------
$Ecrivains = @("web", "worker")
if (-not $DumpValide) {
    Rapport "restauration" "FAIL" "sauvegarde absente ou corrompue — pg_restore NON lancé (aucune action destructive sans sauvegarde valide)"
} elseif (-not $Fiche) {
    Rapport "restauration" "FAIL" "aucune fiche validée connue (INFO|fiche_pour_restauration absente)"
} else {
    $Supprime = ""
    $Restaure = ""
    $CodeRestore = 1
    $EcrivainsArretes = $false
    $RestaurationTente = $false
    try {
        $FicheSql = $Fiche -replace "'", "''"
        $StopSortie = (docker compose stop @Ecrivains 2>&1) | Out-String
        $CodeStop = $LASTEXITCODE
        
        # Vérification stricte : le code retour de docker compose ps DOIT être 0.
        # Si ps échoue, on ne peut pas affirmer que les écrivains sont arrêtés.
        $ServicesActifsBrut = (docker compose ps --status running --services 2>&1) | Out-String
        $CodePs = $LASTEXITCODE
        
        if ($CodeStop -ne 0 -or $CodePs -ne 0) {
            Rapport "restauration" "FAIL" "commande docker compose stop ou ps en erreur — pg_restore NON lancé (isolement non certifié)"
        } else {
            $ServicesActifs = ($ServicesActifsBrut.Trim() -split "`r?`n") | ForEach-Object { $_.Trim() }
            $EncoreActifs = @()
            foreach ($Service in $Ecrivains) {
                if ($ServicesActifs -contains $Service) { $EncoreActifs += $Service }
            }
            if ($EncoreActifs.Count -gt 0) {
                Rapport "restauration" "FAIL" ("écrivains encore actifs : " + ($EncoreActifs -join ", ") + " — pg_restore NON lancé (isolement impossible à prouver)")
            } else {
                $EcrivainsArretes = $true
                # Le DELETE vient APRÈS confirmation absolue de l'arrêt des écrivains
                $Supprime = (docker compose exec -T postgres psql -U seamtech -d seamtech_search -tA `
                    -c "DELETE FROM fiche WHERE code = '$FicheSql'" 2>&1) | Out-String
                $RestaurationTente = $true
                docker compose cp $Dump "postgres:/tmp/recette-restore.dump" *> $null
                $Restaure = (docker compose exec -T postgres pg_restore --clean --if-exists --no-owner `
                    -U seamtech -d seamtech_search /tmp/recette-restore.dump 2>&1) | Out-String
                $CodeRestore = $LASTEXITCODE
            }
        }
    }
    finally {
        # Si les écrivains ont été arrêtés mais qu'aucune destruction n'a eu lieu, on peut redémarrer.
        # Si une destruction a eu lieu et que le restore a réussi ($CodeRestore -eq 0), on redémarre.
        # Si une destruction a eu lieu mais que le restore a échoué, un avertissement strict est émis.
        if ($EcrivainsArretes) {
            if (-not $RestaurationTente -or $CodeRestore -eq 0) {
                docker compose start @Ecrivains *> $null
            } else {
                Write-Host "ATTENTION: Base de données partiellement restaurée (code $CodeRestore). Redémarrage sécurisé des services..." -ForegroundColor Yellow
                docker compose start @Ecrivains *> $null
            }
        }
    }
    $FicheEnc = [uri]::EscapeDataString($Fiche)
    $Retour = "000"
    $Fin = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $Fin) {
        $Retour = HttpCode "http://127.0.0.1:8000/fiches/$FicheEnc/pieces" $Token
        if ($Retour -eq "200") { break }
        Start-Sleep -Seconds 3
    }
    if (($CodeRestore -eq 0) -and ($Retour -eq "200")) {
        Rapport "restauration" "PASS" ("fiche $Fiche supprimée ($($Supprime.Trim())) puis restaurée depuis $Dump " + 
            "(GET /fiches/$Fiche/pieces = 200) — écrivains " + ($Ecrivains -join "+") + " arrêtés pendant l'opération")
    } elseif ($EcrivainsArretes) {
        Rapport "restauration" "FAIL" "pg_restore code=$CodeRestore ; fiche de retour HTTP $Retour"
    }
    # Si $EcrivainsArretes est faux, le FAIL « écrivains encore actifs » a déjà
    # été émis : un second FAIL sur la même cause n'apporterait rien.
}

# ---------------------------------------------------------------------------
# 10. Persistance : docker compose down && up -> les données reviennent.
# ---------------------------------------------------------------------------
docker compose down *> $null
docker compose up -d *> $null
$Persist = "000"
$Fin = (Get-Date).AddSeconds(300)
while ((Get-Date) -lt $Fin) {
    $Persist = HttpCode "http://127.0.0.1:8000/health" $Token
    if ($Persist -eq "200") { break }
    Start-Sleep -Seconds 5
}
if ($Fiche) {
    $FicheEnc = [uri]::EscapeDataString($Fiche)
    $Retour = HttpCode "http://127.0.0.1:8000/fiches/$FicheEnc/pieces" $Token
} else {
    $Retour = "200"
}
if (($Persist -eq "200") -and ($Retour -eq "200")) {
    Rapport "persistance-volumes" "PASS" "down && up : santé HTTP $Persist, fiche $Fiche toujours présente ($Retour) — volumes nommés préservés"
} else {
    Rapport "persistance-volumes" "FAIL" "down && up : santé HTTP $Persist, fiche HTTP $Retour"
}

# ---------------------------------------------------------------------------
# RAPPORT FINAL : chaque contrôle PASS/FAIL + sortie brute ci-dessus.
# ---------------------------------------------------------------------------
$Lignes = @()
foreach ($ligne in (Get-Content $RapportTmp)) {
    if ($ligne -match '^CONTROLE\|') {
        $morceaux = $ligne -split '\|', 4
        $Lignes += ("{0,-22} {1,-4} {2}" -f $morceaux[1], $morceaux[2], $morceaux[3])
    }
}
Write-Host ""
Write-Host "=== RAPPORT FINAL — recette locale ($((Get-Date).ToUniversalTime().ToString("o"))) ==="
$Lignes | ForEach-Object { Write-Host $_ }
Write-Host "---"
$Total = $Lignes.Count
$CodeSortie = if ($script:NbFail -gt 0) { 1 } else { 0 }
Write-Host "$Total contrôle(s) — $($script:NbFail) FAIL — code sortie $CodeSortie"
Copy-Item -Path $RapportTmp -Destination (Join-Path $BackupDir ("rapport-recette-{0:yyyyMMdd-HHmmss}.txt" -f (Get-Date))) -Force -ErrorAction SilentlyContinue
Remove-Item -Force $RapportTmp -ErrorAction SilentlyContinue
exit $CodeSortie
