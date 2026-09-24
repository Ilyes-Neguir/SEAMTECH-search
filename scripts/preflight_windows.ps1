# =============================================================================
# SEAMTECH Search — diagnostic du poste Windows (préparation mise en service)
# =============================================================================
# LECTURE SEULE. Ce script :
#   - ne modifie RIEN sur le système (aucune installation, aucun paramètre) ;
#   - ne télécharge RIEN (aucun appel réseau) ;
#   - ne lit AUCUN secret (.env non ouvert, aucun mot de passe demandé) ;
#   - ne démarre PAS Docker de force (état observé seulement) ;
#   - n'accède PAS à l'archive réelle (l'emplacement prévu est affiché tel quel,
#     jamais ouvert, jamais testé) ;
#   - écrit UNIQUEMENT dans la console (redirectez avec Tee-Object si besoin) .
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File scripts\preflight_windows.ps1
#   powershell -File scripts\preflight_windows.ps1 | Tee-Object -FilePath data\rapports\preflight_windows.txt
#
# Code de sortie : 0 = poste prêt (avertissements possibles), 1 = problèmes détectés.
# Voir docs/CHECKLIST_POSTE_WINDOWS.md pour les résultats attendus et les
# actions correctives.
# =============================================================================

$ErrorActionPreference = "Continue"
$Problemes = 0

function Ecrire-Resultat {
    param(
        [string]$Etat,      # OK | AVERTISSEMENT | PROBLEME
        [string]$Libelle,
        [string]$Detail
    )
    Write-Output ("[{0,-11}] {1} — {2}" -f $Etat, $Libelle, $Detail)
    if ($Etat -eq "PROBLEME") { $script:Problemes = $script:Problemes + 1 }
}

Write-Output "=== SEAMTECH — diagnostic poste Windows ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) ==="
Write-Output "Lecture seule : aucune modification, aucun téléchargement, aucun secret."
Write-Output ""

# --- 1. Version Windows et architecture -------------------------------------
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    Ecrire-Resultat "OK" "Version Windows" ("{0} — version {1} (build {2})" -f $os.Caption, $os.Version, $os.BuildNumber)
    $arch = $os.OSArchitecture
    if ($arch -match "64") {
        Ecrire-Resultat "OK" "Architecture 64 bits" $arch
    } else {
        Ecrire-Resultat "PROBLEME" "Architecture 64 bits" "trouvée : $arch (64 bits requis)"
    }
} catch {
    Ecrire-Resultat "PROBLEME" "Version Windows" "WMI indisponible : $($_.Exception.Message)"
}

# --- 2. RAM totale (8 Go minimum — décision matérielle 21/09) ---------------
try {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $go = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    if ($go -ge 8) {
        Ecrire-Resultat "OK" "RAM totale" "$go Go (minimum 8 Go)"
    } elseif ($go -ge 4) {
        Ecrire-Resultat "AVERTISSEMENT" "RAM totale" "$go Go — sous le minimum 8 Go, traitement OCR ralenti"
    } else {
        Ecrire-Resultat "PROBLEME" "RAM totale" "$go Go — insuffisant (minimum 8 Go)"
    }
} catch {
    Ecrire-Resultat "PROBLEME" "RAM totale" "non mesurable : $($_.Exception.Message)"
}

# --- 3. Espace disque libre (par volume logique) ----------------------------
try {
    $disques = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType = 3"
    foreach ($d in $disques) {
        $libreGo = [math]::Round($d.FreeSpace / 1GB, 1)
        $nom = $d.DeviceID
        if ($libreGo -ge 50) {
            Ecrire-Resultat "OK" "Espace disque libre $nom" "$libreGo Go libres (50 Go minimum conseillés)"
        } elseif ($libreGo -ge 20) {
            Ecrire-Resultat "AVERTISSEMENT" "Espace disque libre $nom" "$libreGo Go — juste (prévoir 50 Go+ avant traitement complet)"
        } else {
            Ecrire-Resultat "PROBLEME" "Espace disque libre $nom" "$libreGo Go — insuffisant avant arrivée de l'archive"
        }
    }
} catch {
    Ecrire-Resultat "PROBLEME" "Espace disque" "non mesurable : $($_.Exception.Message)"
}

# --- 4. Docker / Docker Desktop (présence et état — démarrage JAMAIS forcé) --
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCmd) {
    $v = & docker version --format "{{.Client.Version}}" 2>$null
    if ($v) {
        Ecrire-Resultat "OK" "Docker (client)" "version $v"
    } else {
        Ecrire-Resultat "AVERTISSEMENT" "Docker (client)" "présent mais 'docker version' sans réponse"
    }
} else {
    Ecrire-Resultat "PROBLEME" "Docker / Docker Desktop" "docker introuvable dans le PATH"
}

# --- 5. Statut des services Docker (lecture seule) --------------------------
$servicesDocker = Get-Service -Name "*docker*" -ErrorAction SilentlyContinue
if ($servicesDocker) {
    foreach ($s in $servicesDocker) {
        Ecrire-Resultat "OK" ("Service " + $s.Name) ("statut : " + $s.Status + " (jamais démarré ni arrêté par ce script)")
    }
} else {
    Ecrire-Resultat "AVERTISSEMENT" "Services Docker" "aucun service docker enregistré (Docker Desktop non installé ?)"
}

# --- 6. Python (si traitement hors Docker) ----------------------------------
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) { $pyCmd = Get-Command python3 -ErrorAction SilentlyContinue }
if ($pyCmd) {
    $pyv = & $pyCmd.Source --version 2>&1
    Ecrire-Resultat "OK" "Python" "$pyv ($($pyCmd.Source))"
} else {
    Ecrire-Resultat "AVERTISSEMENT" "Python" "absent du PATH — requis uniquement pour les scripts locaux (bootstrap, OCR)"
}

# --- 7. Tesseract + langue française ----------------------------------------
$tessCmd = Get-Command tesseract -ErrorAction SilentlyContinue
if ($tessCmd) {
    $tessv = & tesseract --version 2>&1 | Select-Object -First 1
    Ecrire-Resultat "OK" "Tesseract" "$tessv ($($tessCmd.Source))"
    $langues = & tesseract --list-langs 2>$null
    if ($langues -match "(?m)^fra$") {
        Ecrire-Resultat "OK" "Langue française (tesseract fra)" "paquet de langue 'fra' installé"
    } else {
        Ecrire-Resultat "PROBLEME" "Langue française (tesseract fra)" "langue 'fra' absente — installer tesseract-ocr-fra (voir checklist)"
    }
} else {
    Ecrire-Resultat "PROBLEME" "Tesseract" "introuvable dans le PATH — requis pour l'OCR du fonds ancien (Lot G)"
}

# --- 8. pdftoppm / poppler --------------------------------------------------
$popplerCmd = Get-Command pdftoppm -ErrorAction SilentlyContinue
if ($popplerCmd) {
    Ecrire-Resultat "OK" "pdftoppm (poppler)" $popplerCmd.Source
} else {
    $cairoCmd = Get-Command pdftocairo -ErrorAction SilentlyContinue
    if ($cairoCmd) {
        Ecrire-Resultat "AVERTISSEMENT" "pdftoppm (poppler)" "absent mais pdftocairo présent (équivalent accepté)"
    } else {
        Ecrire-Resultat "PROBLEME" "pdftoppm (poppler)" "ni pdftoppm ni pdftocairo — rendu PDF impossible"
    }
}

# --- 9. Ports utilisés par la pile (écoute en cours) ------------------------
# Ports de la pile (voir README) : 3000 front, 8000 API, 9000/9001 MinIO,
# 5433 Postgres, 6379 Redis. On regarde ce qui EST déjà en écoute — jamais de
# connexion ni de réservation.
$portsPile = @(3000, 8000, 9000, 9001, 5433, 6379)
try {
    $ecoutes = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue
    foreach ($port in $portsPile) {
        $ouvert = $ecoutes | Where-Object { $_.LocalPort -eq $port }
        if ($ouvert) {
            Ecrire-Resultat "AVERTISSEMENT" "Port $port" "déjà en écoute (conflit possible si la pile SEAMTECH n'est pas lancée)"
        } else {
            Ecrire-Resultat "OK" "Port $port" "libre"
        }
    }
} catch {
    Ecrire-Resultat "AVERTISSEMENT" "Ports de la pile" "Get-NetTCPConnection indisponible : vérifier manuellement avec netstat -ano"
}

# --- 10. Répertoire de travail OCR ------------------------------------------
$ocrTravail = $env:SEAMTECH_OCR_TRAVAIL_DIR
if (-not $ocrTravail) { $ocrTravail = "data\ocr_travail" }
if (Test-Path -LiteralPath $ocrTravail) {
    Ecrire-Resultat "OK" "Répertoire de travail OCR" "présent : $ocrTravail"
} else {
    Ecrire-Resultat "AVERTISSEMENT" "Répertoire de travail OCR" "absent : $ocrTravail (créé au premier run OCR — hors archive, RG13)"
}

# --- 11. Répertoire de sauvegarde -------------------------------------------
$sauvDir = "data\backups"
if (Test-Path -LiteralPath $sauvDir) {
    Ecrire-Resultat "OK" "Répertoire de sauvegarde" "présent : $sauvDir"
} else {
    Ecrire-Resultat "AVERTISSEMENT" "Répertoire de sauvegarde" "absent : $sauvDir (créé par scripts\backup_sqlite.ps1 / backup_postgres.ps1)"
}
foreach ($scriptSauv in @("scripts\backup_sqlite.ps1", "scripts\backup_postgres.ps1", "scripts\restore_sqlite.ps1", "scripts\restore_postgres.ps1")) {
    if (Test-Path -LiteralPath $scriptSauv) {
        Ecrire-Resultat "OK" "Script $scriptSauv" "présent"
    } else {
        Ecrire-Resultat "PROBLEME" "Script $scriptSauv" "manquant dans le dépôt"
    }
}

# --- 12. Emplacement prévu de l'archive (AFFICHÉ SEULEMENT — jamais ouvert) --
if ($env:SEAMTECH_ARCHIVE_SOURCE) {
    $EmplacementArchive = $env:SEAMTECH_ARCHIVE_SOURCE
} else {
    $EmplacementArchive = "(non configuré — définir SEAMTECH_ARCHIVE_SOURCE au moment de l'arrivée)"
}
Ecrire-Resultat "OK" "Emplacement prévu de l'archive" "$EmplacementArchive (non vérifié : ce script n'accède jamais à l'archive réelle)"

# --- 13. Fichiers de configuration présents (existence seulement) ----------
foreach ($fichierCfg in @(".env.example", "docker-compose.yml", "config\config.example.json")) {
    if (Test-Path -LiteralPath $fichierCfg) {
        Ecrire-Resultat "OK" "Fichier $fichierCfg" "présent (contenu non lu — aucun secret dans les logs)"
    } else {
        Ecrire-Resultat "PROBLEME" "Fichier $fichierCfg" "manquant"
    }
}
if (Test-Path -LiteralPath ".env") {
    Ecrire-Resultat "OK" "Fichier .env" "présent (non ouvert — les secrets ne sortent jamais de ce fichier)"
} else {
    Ecrire-Resultat "AVERTISSEMENT" "Fichier .env" "absent — copier .env.example et renseigner les secrets avant mise en service"
}

Write-Output ""
if ($Problemes -eq 0) {
    Write-Output "=== BILAN : poste prêt ($Problemes problème(s)) — voir docs/CHECKLIST_POSTE_WINDOWS.md ==="
    exit 0
} else {
    Write-Output "=== BILAN : $Problemes problème(s) — actions correctives dans docs/CHECKLIST_POSTE_WINDOWS.md ==="
    exit 1
}
