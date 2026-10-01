# Installation « propre » de SEAMTECH Search sur un poste Windows.
#
#   Clic droit sur « Installer SEAMTECH Search.cmd » -> Executer
#   (equivalent : powershell -NoProfile -ExecutionPolicy Bypass -File scripts\installer_poste_windows.ps1)
#
# Aucun droit administrateur n'est necessaire, et RIEN n'est installe a la place
# de l'utilisateur : le script MESURE le poste (lecture seule), prepare, lance
# une premiere fois pour de vrai, puis depose l'icone.
#
# Etapes, dans l'ordre :
#   1. controle que le dossier contient une copie COMPLETE du depot ;
#   2. mesure du poste : Windows, RAM, disque, Docker, Node.js, pnpm, Python, bash ;
#   3. preparation des services de donnees : .env (7 secrets locaux aleatoires,
#      jamais dans Git), image MinIO locale, conteneurs postgres/minio/redis ;
#   4. preparation locale : .venv Python + dependances, config/config.json
#      (dossier a indexer, MinIO, Redis) — jamais dans Git ;
#   5. PREMIER LANCEMENT reel : construction de l'interface, demarrage, sante HTTP ;
#   6. premier compte nominatif (mot de passe masque, jamais affiche ni ecrit) ;
#   7. depot de l'ICONE : raccourci « SEAMTECH Search » sur le Bureau et dans le
#      menu Demarrer, plus « Arreter SEAMTECH Search » dans le menu Demarrer ;
#   8. recapitulatif : adresse, connexion, arret, sauvegarde, journaux.
#
# Rejouable : relancer l'installation ne perd aucune donnee (.env existant
# reutilise, volumes Docker conserves, raccourcis reecrits). Code sortie :
# 0 = installation terminee, 1 = au moins un point a corriger.

param(
    [switch]$SansCompte,        # ne pas proposer la creation du premier compte
    [switch]$SansLancement,     # installer sans lancer l'application
    [switch]$DemarrageAutomatique,  # demarrer SEAMTECH Search a l'ouverture de session
    [switch]$Oui                # ne pose aucune question (reponses par defaut)
)

$ErrorActionPreference = "Stop"

# Les mots de passe saisis sont pipes vers l'outil metier : sans ce reglage,
# Windows PowerShell 5.1 encodait le tube en ASCII et mutilait tout caractere
# non ASCII. Aucun secret n'est imprime par ce script.
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$script:Problemes = 0
$script:UrlApp = "http://127.0.0.1:3000"
$script:Identifiant = $null
$NomApp = "SEAMTECH Search"
$Icone = Join-Path $ProjectRoot "SEAMTECH Search.ico"
$LanceurCmd = Join-Path $ProjectRoot "SEAMTECH Search.cmd"
$LanceurPs1 = Join-Path $PSScriptRoot "start_seamtech_search.ps1"
$ArretPs1 = Join-Path $PSScriptRoot "arreter_seamtech.ps1"
$ConfigPath = Join-Path $ProjectRoot "config\config.json"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Ligne([string]$etat, [string]$libelle, [string]$detail) {
    $couleur = "Gray"
    if ($etat -eq "OK") { $couleur = "Green" }
    elseif ($etat -eq "!!") { $couleur = "Red" }
    elseif ($etat -eq "--") { $couleur = "DarkGray" }
    Write-Host ("{0,-3} {1,-30} {2}" -f $etat, $libelle, $detail) -ForegroundColor $couleur
    if ($etat -eq "!!") { $script:Problemes++ }
}

function Question([string]$invite, [string]$defaut) {
    if ($Oui) { return $defaut }
    $suffixe = if ($defaut) { " [$defaut]" } else { "" }
    $reponse = Read-Host ($invite + $suffixe)
    if ([string]::IsNullOrWhiteSpace($reponse)) { return $defaut }
    return $reponse.Trim()
}

# ---------------------------------------------------------------------------
# 1. Le dossier est-il une copie complete du depot ?
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Installation de $NomApp ===" -ForegroundColor Cyan
Write-Host "Dossier installe : $ProjectRoot"
Write-Host ""

$Requis = @(
    @("docker-compose.yml", "pile Docker"),
    @("SEAMTECH Search.cmd", "lanceur quotidien"),
    @("SEAMTECH Search.ico", "icone"),
    @("scripts\start_seamtech_search.ps1", "lanceur PowerShell"),
    @("scripts\ensure_postgres.ps1", "preparation des services"),
    @("scripts\arreter_seamtech.ps1", "arret propre"),
    @("frontend\package.json", "interface"),
    @("requirements.txt", "dependances Python")
)
$Manquants = @()
foreach ($element in $Requis) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $element[0]))) { $Manquants += $element[1] }
}
if ($Manquants.Count -gt 0) {
    Ligne "!!" "copie du depot" ("elements absents : " + ($Manquants -join ", "))
    Write-Host ""
    Write-Host "Ce dossier n'est pas une copie complete de SEAMTECH Search."
    Write-Host "Solution : supprimer ce dossier, recopier le depot ENTIER"
    Write-Host "(tous les sous-dossiers compris), puis relancer l'installation."
    exit 1
}
Ligne "OK" "copie du depot" "complete : icone, lanceur, pile, interface, dependances"

# ---------------------------------------------------------------------------
# 2. Mesure du poste (lecture seule : rien n'est installe ici)
# ---------------------------------------------------------------------------
$Systeme = Get-CimInstance Win32_OperatingSystem
Ligne "OK" "Windows" ("{0} — {1}" -f $Systeme.Caption, $Systeme.OSArchitecture)
if ($Systeme.OSArchitecture -notlike "*64*") {
    Ligne "!!" "architecture" "Windows 64 bits obligatoire (Docker, onnxruntime)"
}

$RamGo = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
if ($RamGo -lt 8) { Ligne "!!" "memoire (RAM)" "$RamGo Go — 8 Go minimum" }
else { Ligne "OK" "memoire (RAM)" "$RamGo Go" }

$Lecteur = (Split-Path -Qualifier $ProjectRoot).TrimEnd(":", " ")
$Disque = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID = '{0}:'" -f $Lecteur) -ErrorAction SilentlyContinue
if ($Disque) {
    $LibreGo = [math]::Round($Disque.FreeSpace / 1GB, 1)
    if ($LibreGo -lt 20) { Ligne "!!" "espace disque ($Lecteur)" "$LibreGo Go libres — 20 Go minimum" }
    else { Ligne "OK" "espace disque ($Lecteur)" "$LibreGo Go libres" }
} else {
    Ligne "--" "espace disque ($Lecteur)" "non mesure"
}

# --- Docker Desktop -------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Ligne "!!" "Docker" "commande docker introuvable — installer Docker Desktop"
} else {
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Ligne "!!" "Docker Compose" "plugin compose v2 absent — mettre Docker Desktop a jour"
    } else {
        docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            $DockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
            if (Test-Path -LiteralPath $DockerDesktop) {
                Write-Host "       Docker Desktop est arrete — demarrage (jusqu'a 90 secondes)..."
                Start-Process -FilePath $DockerDesktop | Out-Null
                for ($essai = 0; $essai -lt 90; $essai++) {
                    Start-Sleep -Seconds 1
                    docker info *> $null
                    if ($LASTEXITCODE -eq 0) { break }
                }
            }
        }
        docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            Ligne "OK" "Docker" ((docker --version 2>&1 | Select-Object -First 1) + " ; " + (docker compose version 2>&1 | Select-Object -First 1))
        } else {
            Ligne "!!" "Docker" "installe mais le moteur ne repond pas — lancer Docker Desktop puis relancer"
        }
    }
}

# --- Node.js et pnpm (construction de l'interface) ------------------------
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Ligne "!!" "Node.js" "commande node introuvable — installer Node.js 20 LTS"
} else {
    Ligne "OK" "Node.js" ((node --version 2>&1 | Select-Object -First 1))
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Ligne "!!" "pnpm" 'commande pnpm introuvable — apres Node.js : npm install -g pnpm@9.15.9'
} else {
    Ligne "OK" "pnpm" ((pnpm --version 2>&1 | Select-Object -First 1))
}

# --- Python ---------------------------------------------------------------
$PythonSystem = $null
if (Test-Path -LiteralPath $VenvPython) {
    $PythonSystem = $VenvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonSystem = "python"
} else {
    Ligne "!!" "Python" "commande python introuvable — installer Python 3.11"
}
if ($PythonSystem) {
    $VersionPython = (& $PythonSystem --version 2>&1 | Select-Object -First 1)
    if ($VersionPython -match "Python (\d+)\.(\d+)") {
        $Majeur = [int]$Matches[1]
        $Mineur = [int]$Matches[2]
        if (($Majeur -lt 3) -or (($Majeur -eq 3) -and ($Mineur -lt 10))) {
            Ligne "!!" "Python" "$VersionPython — Python 3.10 ou plus recent requis"
        } else {
            Ligne "OK" "Python" $VersionPython
        }
    } else {
        Ligne "!!" "Python" "version illisible : $VersionPython"
    }
}

# --- bash (construction de l'image MinIO locale) --------------------------
$Bash = $null
foreach ($candidat in @("bash", (Join-Path $env:ProgramFiles "Git\bin\bash.exe"), (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe"))) {
    if (Get-Command $candidat -ErrorAction SilentlyContinue) { $Bash = $candidat; break }
    if (Test-Path -LiteralPath $candidat) { $Bash = $candidat; break }
}
if ($Bash) { Ligne "OK" "bash (Git)" $Bash }
else { Ligne "!!" "bash (Git)" "introuvable — installer Git for Windows (image MinIO locale)" }

if ($script:Problemes -gt 0) {
    Write-Host ""
    Write-Host "=== Installation interrompue : $($script:Problemes) point(s) a corriger ===" -ForegroundColor Red
    Write-Host "Corrigez les lignes marquees !! ci-dessus, puis relancez l'installation."
    exit 1
}

# ---------------------------------------------------------------------------
# 3. Services de donnees : .env, image MinIO, conteneurs postgres/minio/redis
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Preparation des services de donnees ---" -ForegroundColor Cyan
try {
    & (Join-Path $PSScriptRoot "ensure_postgres.ps1")
    Ligne "OK" "services de donnees" ".env pret ; image MinIO locale ; postgres, minio, redis demarres"
} catch {
    Ligne "!!" "services de donnees" $_.Exception.Message
    Write-Host ""
    Write-Host "La preparation Docker a echoue. Verifiez Docker Desktop, puis relancez l'installation."
    exit 1
}

# ---------------------------------------------------------------------------
# 4. Preparation locale : environnement Python, dependances, configuration
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Preparation locale ---" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "       Creation de l'environnement Python (.venv)..."
    & $PythonSystem -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        Ligne "!!" "environnement Python" "python -m venv a echoue"
        exit 1
    }
}
Ligne "OK" "environnement Python" (Join-Path $ProjectRoot ".venv")

Write-Host "       Installation des dependances Python (plusieurs minutes la premiere fois)..."
& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Ligne "!!" "dependances Python" "pip install -r requirements.txt a echoue"
    exit 1
}
Ligne "OK" "dependances Python" "requirements.txt installe dans .venv"

$ConfigExistait = Test-Path -LiteralPath $ConfigPath
if (-not $ConfigExistait) {
    & $VenvPython (Join-Path $PSScriptRoot "bootstrap.py") | Out-Null
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        Ligne "!!" "configuration" "scripts\bootstrap.py n'a pas cree config\config.json"
        exit 1
    }
    Ligne "OK" "configuration" "config\config.json cree depuis l'exemple"
}

# Dossier(s) a indexer : proposes a l'utilisateur, jamais devines.
$Defaut = Join-Path $ProjectRoot "sample_data"
if ($ConfigExistait) {
    try {
        $Actuels = @((Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json).root_paths)
        if ($Actuels.Count -gt 0) { $Defaut = ($Actuels -join ";") }
    } catch { }
}
$Chemins = Question "Dossier(s) a indexer (chemins separes par des points-virgules)" $Defaut
$Valides = @()
$Ignores = @()
foreach ($chemin in ($Chemins -split ";")) {
    $chemin = $chemin.Trim().Trim('"')
    if (-not $chemin) { continue }
    if (Test-Path -LiteralPath $chemin) { $Valides += $chemin }
    else { $Ignores += $chemin }
}
if ($Valides.Count -eq 0) {
    Ligne "!!" "dossier a indexer" "aucun dossier valide (introuvables : " + ($Ignores -join ", ") + ") — installation interrompue"
    exit 1
}
if ($Ignores.Count -gt 0) {
    Ligne "--" "dossier a indexer" "ignore (introuvable) : " + ($Ignores -join ", ")
}

# MinIO et Redis locaux : les memes identifiants que ceux de .env (jamais
# affiches, jamais journalises). Le moteur lance en local les lit dans
# config\config.json ; la pile Docker, elle, les lit dans .env.
$Valeurs = @{}
foreach ($ligne in (Get-Content -LiteralPath (Join-Path $ProjectRoot ".env"))) {
    if ($ligne -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") { $Valeurs[$Matches[1]] = $Matches[2].Trim() }
}
$Conf = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$Conf.root_paths = @($Valides)
$Conf.s3_endpoint_url = "http://127.0.0.1:9000"
$Conf.s3_bucket = $(if ($Valeurs["SEAMTECH_S3_BUCKET"]) { $Valeurs["SEAMTECH_S3_BUCKET"] } else { "seamtech-documents" })
$Conf.s3_access_key = $Valeurs["MINIO_ROOT_USER"]
$Conf.s3_secret_access_key = $Valeurs["MINIO_ROOT_PASSWORD"]
if ($Valeurs["REDIS_PASSWORD"]) {
    $Conf.redis_url = ("redis://:{0}@127.0.0.1:6379/0" -f $Valeurs["REDIS_PASSWORD"])
}
# JSON sans BOM : Python ouvre config.json en UTF-8 strict, un BOM le ferait
# echouer (UTF8Encoding($false)).
$utf8SansBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, ($Conf | ConvertTo-Json -Depth 12), $utf8SansBom)
Ligne "OK" "configuration" ("indexe : " + ($Valides -join " ; ") + " ; MinIO et Redis renseignes")

# ---------------------------------------------------------------------------
# 5. Premier lancement reel
# ---------------------------------------------------------------------------
if ($SansLancement) {
    Ligne "--" "premier lancement" "ignore (parametre -SansLancement)"
} else {
    Write-Host ""
    Write-Host "--- Premier lancement ---" -ForegroundColor Cyan
    Write-Host "       Construction de l'interface : comptez plusieurs minutes la premiere fois."
    # Le lanceur quotidien est lance comme un double-clic (Start-Process sur le
    # .cmd) : il travaille dans SA propre console — fermer la fenetre de
    # l'installation ne peut pas emporter l'application avec elle — et son code
    # de sortie est lu sans ambiguete.
    $ProcessusLancement = Start-Process -FilePath $LanceurCmd -Wait -PassThru
    $CodeLancement = 0
    if ($ProcessusLancement) {
        try { $CodeLancement = [int]$ProcessusLancement.ExitCode } catch { $CodeLancement = 0 }
    }
    if ($CodeLancement -ne 0) {
        Ligne "!!" "premier lancement" "le lanceur a renvoye le code $CodeLancement — detail dans data\logs"
        exit 1
    }
    foreach ($port in @(3000, 3001)) {
        try {
            $reponse = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $port) -UseBasicParsing -TimeoutSec 10
            if ($reponse.StatusCode -eq 200) { $script:UrlApp = "http://127.0.0.1:$port"; break }
        } catch { }
    }
    try {
        $reponse = Invoke-WebRequest -Uri ("{0}/api/health" -f $script:UrlApp) -UseBasicParsing -TimeoutSec 10
        Ligne "OK" "premier lancement" "interface en ligne sur $($script:UrlApp)"
    } catch {
        Ligne "!!" "premier lancement" "l'interface ne repond pas sur $($script:UrlApp) — detail dans data\logs"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 6. Premier compte nominatif (sinon seul le compte de secours entre)
# ---------------------------------------------------------------------------
if ($SansCompte) {
    Ligne "--" "compte nominatif" "ignore (parametre -SansCompte)"
} else {
    $Comptes = @()
    try {
        $sortie = & $VenvPython -m seamtech_search.comptes.cli lister --json 2>&1
        if ($LASTEXITCODE -eq 0) { $Comptes = @($sortie | ConvertFrom-Json) }
    } catch { $Comptes = @() }
    if ($Comptes.Count -gt 0) {
        Ligne "OK" "compte nominatif" ("$($Comptes.Count) compte(s) deja present(s) — aucun ajout")
    } elseif ($Oui) {
        # Sans question, aucun mot de passe ne peut être saisi de façon masquée :
        # on ne fabrique pas de compte au mot de passe devinable.
        Ligne "--" "compte nominatif" 'installation silencieuse (-Oui) : créer le compte ensuite avec `.venv\Scripts\python -m seamtech_search.comptes.cli creer`'
    } else {
        $Creer = Question "Creer maintenant le premier compte de connexion ? (O/N)" "O"
        if ($Creer -match "^[oOyY]") {
            $Identifiant = Question "Identifiant de connexion" $env:USERNAME
            $Identifiant = ($Identifiant -replace "\s", "").ToLower()
            if (-not $Identifiant) { $Identifiant = "operateur" }
            $NomAffiche = Question "Nom affiche" $env:USERNAME
            $Role = Question "Role (administrateur / operateur)" "administrateur"
            if ($Role -ne "operateur") { $Role = "administrateur" }
            $Saisie1 = Read-Host "Mot de passe (rien ne s'affiche)" -AsSecureString
            $Saisie2 = Read-Host "Confirmation du mot de passe" -AsSecureString
            $Clair1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Saisie1))
            $Clair2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Saisie2))
            if (-not $Clair1) {
                Ligne "!!" "compte nominatif" "mot de passe vide — compte non cree"
            } elseif ($Clair1 -cne $Clair2) {
                Ligne "!!" "compte nominatif" "les deux saisies different — compte non cree"
            } else {
                # Le mot de passe arrive sur l'entree standard de l'outil : jamais
                # en argument de ligne de commande, jamais affiche.
                $Clair1 | & $VenvPython -m seamtech_search.comptes.cli creer --identifiant $Identifiant --nom $NomAffiche --role $Role --mot-de-passe-definitif
                if ($LASTEXITCODE -eq 0) {
                    Ligne "OK" "compte nominatif" "compte « $Identifiant » cree ($Role)"
                    $script:Identifiant = $Identifiant
                } else {
                    Ligne "!!" "compte nominatif" "creation refusee (identifiant deja pris ?) — voir data\logs"
                }
            }
        } else {
            Ligne "--" "compte nominatif" "aucun compte cree — connexion par le compte de secours"
        }
    }
}

# ---------------------------------------------------------------------------
# 7. L'icone : raccourcis Bureau et menu Demarrer
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Depot de l'icone ---" -ForegroundColor Cyan
$Shell = New-Object -ComObject WScript.Shell
# Dossiers système DEMANDÉS À WINDOWS : sur un poste français, le menu Démarrer
# s'appelle « Menu Démarrer\Programmes » — un chemin anglais codé en dur
# déposerait les raccourcis dans un dossier que personne ne regarde.
$Bureau = $Shell.SpecialFolders("Desktop")
$Menu = $Shell.SpecialFolders("Programs")
$DossierMenu = Join-Path $Menu "SEAMTECH Search"
New-Item -ItemType Directory -Force -Path $DossierMenu | Out-Null
$PowerShellExe = Join-Path $PSHOME "powershell.exe"

function Creer-Raccourci([string]$chemin, [string]$arguments, [string]$description) {
    $raccourci = $Shell.CreateShortcut($chemin)
    $raccourci.TargetPath = $PowerShellExe
    $raccourci.Arguments = $arguments
    $raccourci.WorkingDirectory = $ProjectRoot
    $raccourci.IconLocation = ($Icone + ",0")
    $raccourci.Description = $description
    $raccourci.WindowStyle = 7
    $raccourci.Save()
}

$ArgumentsLancement = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $LanceurPs1)
$RaccourciBureau = Join-Path $Bureau "SEAMTECH Search.lnk"
Creer-Raccourci $RaccourciBureau $ArgumentsLancement "Demarrer SEAMTECH Search et ouvrir l'interface"
Ligne "OK" "icone du bureau" $RaccourciBureau

$RaccourciMenu = Join-Path $DossierMenu "SEAMTECH Search.lnk"
Creer-Raccourci $RaccourciMenu $ArgumentsLancement "Demarrer SEAMTECH Search et ouvrir l'interface"
Ligne "OK" "menu Demarrer" $RaccourciMenu

$ArgumentsArret = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $ArretPs1)
$RaccourciArret = Join-Path $DossierMenu "Arreter SEAMTECH Search.lnk"
Creer-Raccourci $RaccourciArret $ArgumentsArret "Arreter SEAMTECH Search (les donnees sont conservees)"
Ligne "OK" "arret (menu Demarrer)" $RaccourciArret

if ($DemarrageAutomatique) {
    $DossierDemarrage = $Shell.SpecialFolders("Startup")
    $RaccourciAuto = Join-Path $DossierDemarrage "SEAMTECH Search (demarrage automatique).lnk"
    Creer-Raccourci $RaccourciAuto $ArgumentsLancement "Demarre SEAMTECH Search a l'ouverture de session"
    Ligne "OK" "demarrage automatique" $RaccourciAuto
}

# ---------------------------------------------------------------------------
# 8. Recapitulatif
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== Installation terminee ===" -ForegroundColor Green
Write-Host ""
Write-Host "Ouvrir l'application :"
Write-Host "    double-clic sur l'icone « SEAMTECH Search » du bureau"
Write-Host "    (ou directement : $($script:UrlApp) tant que l'application tourne)"
Write-Host ""
Write-Host "Se connecter :"
if ($script:Identifiant) {
    Write-Host "    identifiant : $($script:Identifiant)"
    Write-Host "    mot de passe : celui saisi a l'installation"
} else {
    Write-Host "    compte de secours : identifiant « secours »"
    Write-Host "    mot de passe : SEAMTECH_UI_PASSWORD dans le fichier .env du dossier"
}
Write-Host ""
Write-Host "Arreter proprement :"
Write-Host "    menu Demarrer -> SEAMTECH Search -> « Arreter SEAMTECH Search »"
Write-Host "    (les donnees restent ; le prochain double-clic les retrouve)"
Write-Host ""
Write-Host "Sauvegarde de la base :"
Write-Host "    powershell -File scripts\backup_postgres.ps1 -DatabaseUrl postgresql://seamtech:<POSTGRES_PASSWORD de .env>@127.0.0.1:5433/seamtech_search"
Write-Host ""
Write-Host "Journaux de demarrage :"
Write-Host "    data\logs\lancement-*.log"
Write-Host ""
Write-Host "Reinstallation / mise a jour : recopier le depot puis relancer"
Write-Host "« Installer SEAMTECH Search.cmd » — aucune donnee n'est perdue."
Write-Host ""

if ($script:Problemes -gt 0) { exit 1 }
exit 0
