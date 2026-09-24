# Checklist du poste Windows — préparation à la mise en service

**Objet** : préparer le poste de production Windows qui accueillera
l'archive réelle et la pile SEAMTECH Search (Docker Desktop). Ce document est
exploitable tel quel le jour de l'arrivée ; le script
`scripts/preflight_windows.ps1` automatise les mesures **en lecture seule**.

**Règles du diagnostic** (`scripts/preflight_windows.ps1`) :

- lecture seule : aucune modification système automatique ;
- aucun téléchargement ;
- aucun secret ni mot de passe dans les logs (`.env` jamais ouvert) ;
- aucun démarrage forcé de Docker (état observé seulement) ;
- aucun accès à l'archive réelle (son emplacement prévu est affiché, jamais
  vérifié ni ouvert).

Tests de non-destruction du script : `tests/test_preflight_windows.py`
(contrôle statique du contenu — commandes destructives, réseau, secrets,
installation forcée).

---

## 1. Prérequis minimaux

| Élément | Minimum | Cible conseillée | Pourquoi |
|---|---|---|---|
| Version Windows | Windows 10 22H2 ou Windows 11 | Windows 11 | Docker Desktop supporté |
| Architecture | **64 bits** | 64 bits | Docker + onnxruntime |
| RAM | **8 Go** | 16 Go | décision matérielle 21/09 : CPU seul, 8 Go |
| Espace disque libre | 50 Go | 100 Go+ | archive + OCR + sauvegardes |
| Docker Desktop | 4.x avec WSL 2 | dernière stable | pile compose (front, API, Postgres, Redis, MinIO) |
| Python | 3.10+ | 3.11 | scripts locaux (bootstrap, OCR, inventaire) |
| Tesseract | 5.x + langue **fra** | 5.x | OCR du fonds ancien (Lot G) |
| Poppler (pdftoppm) | présent | poppler-utils | rendu PDF → image pour OCR |
| Compte Windows | utilisateur standard | — | pas de droits admin requis au quotidien |

---

## 2. Commandes PowerShell et résultats attendus

Lancer d'abord le diagnostic complet (lecture seule) :

```powershell
cd C:\chemin\SEAMTECH-search
powershell -ExecutionPolicy Bypass -File scripts\preflight_windows.ps1 |
  Tee-Object -FilePath data\rapports\preflight_windows.txt
```

Résultat attendu : `[OK]` sur chaque ligne, bilan `poste prêt`, code de
sortie `0`. Toute ligne `[PROBLEME]` renvoie au §4.

Puis les mesures unitaires (sorties attendues entre parenthèses) :

| # | Commande PowerShell | Résultat attendu |
|---|---|---|
| 1 | `(Get-CimInstance Win32_OperatingSystem).Caption` | « Microsoft Windows 10/11 … » |
| 2 | `(Get-CimInstance Win32_OperatingSystem).OSArchitecture` | « 64-bit » |
| 3 | `[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)` | ≥ 8 |
| 4 | `Get-CimInstance Win32_LogicalDisk -Filter "DriveType = 3" \| Select DeviceID, @{n='LibreGo';e={[math]::Round($_.FreeSpace/1GB,1)}}` | ≥ 50 Go libres sur le volume du dépôt |
| 5 | `docker version --format "{{.Client.Version}}"` | un numéro de version (client) |
| 6 | `Get-Service *docker* \| Select-Name, Status` | services Docker `Running` (après démarrage manuel de Docker Desktop) |
| 7 | `python --version` | Python 3.10+ |
| 8 | `tesseract --version` | tesseract 5.x |
| 9 | `tesseract --list-langs \| Select-String "^fra$"` | `fra` |
| 10 | `Get-Command pdftoppm, pdftocairo` | au moins l'un des deux |
| 11 | `Get-NetTCPConnection -State Listen \| Where-Object { $_.LocalPort -in 3000,8000,9000,9001,5433,6379 }` | vide si la pile SEAMTECH n'est pas lancée |
| 12 | `Test-Path data\ocr_travail` | `True` après le premier run OCR |
| 13 | `Test-Path data\backups` | `True` (créé par les scripts de sauvegarde) |
| 14 | `$env:SEAMTECH_ARCHIVE_SOURCE` | emplacement prévu de l'archive (affiché, jamais ouvert par le diagnostic) |

Ports de la pile (README) : **3000** front, **8000** API, **9000/9001** MinIO
(API/console), **5433** Postgres, **6379** Redis — tous liés à `127.0.0.1`.

---

## 3. Vérification de l'espace disque

- Avant l'arrivée : `Get-CimInstance Win32_LogicalDisk -Filter "DriveType = 3"`
  → **50 Go minimum** libres sur le volume qui recevra l'archive et les
  traitements, 100 Go+ conseillés (archive 50+ Go attendue, copie de travail,
  rapports, sauvegardes).
- Le préflight Python applique le même garde-fou (`--min-libre-o`, défaut
  1 Go pour le strict minimum machine, à relever le jour J : voir
  `docs/ARRIVEE_ARCHIVE.md`).
- Si l'espace manque : déplacer les sauvegardes anciennes hors du poste
  (clé/chiffre), purger `data/ocr_travail/rapports` (reproductibles), jamais
  l'archive.

---

## 4. Problèmes fréquents et actions correctives

| Symptôme (diagnostic) | Cause fréquente | Action corrective |
|---|---|---|
| `Architecture 64 bits` PROBLEME | Windows 32 bits | poste non conforme : changer de machine |
| `RAM totale` AVERTISSEMENT | machine 4 Go | ajouter de la RAM avant traitement OCR complet |
| `Espace disque libre` PROBLEME | volume presque plein | libérer 50 Go+ (§3) |
| `Docker / Docker Desktop` PROBLEME | Docker non installé ou hors PATH | installer Docker Desktop **manuellement** (aucun téléchargement par les scripts), redémarrer, rouvrir PowerShell |
| `docker version` sans réponse serveur | Docker Desktop pas démarré | le démarrer **manuellement** (le diagnostic ne le démarre jamais) |
| `Services Docker` en `Stopped` | Docker Desktop fermé | idem — démarrage manuel |
| `Tesseract` PROBLEME | Tesseract absent du PATH | installer Tesseract 5.x (dépôt UB Mannheim), cocher « additional language data » ; ajouter son dossier au PATH utilisateur |
| `Langue française` PROBLEME | paquet `fra` absent | copier `fra.traineddata` dans `tessdata` (voir note ci-dessous) |
| `pdftoppm` PROBLEME | poppler absent | installer poppler pour Windows (distribution gswin32/magick ne suffit pas), ou utiliser `pdftocairo` |
| `Port NNNN` AVERTISSEMENT | autre application occupe le port | identifier `Get-NetTCPConnection -State Listen -LocalPort NNNN` + `Get-Process -Id <OwningProcess>` ; soit fermer l'application, soit adapter `docker-compose.yml` (documenté, pas de changement silencieux) |
| `python` AVERTISSEMENT | Python absent | seul nécessaire pour les scripts locaux hors Docker ; installer 3.11+ manuellement |
| `.env` AVERTISSEMENT | première installation | `Copy-Item .env.example .env` puis renseigner les secrets **à la main** (générer : `openssl rand -base64 24/48`) |
| Exécution de script bloquée | politique d'exécution | `powershell -ExecutionPolicy Bypass -File scripts\preflight_windows.ps1` (pas de changement global de politique) |

Note tessdata : `tesseract --list-langs` doit afficher `fra`. Sinon, vérifier
l'emplacement actif avec `tesseract --print-parameters | Select-String tessdata`
et y déposer `fra.traineddata` fourni avec Tesseract.

---

## 5. Vérification avant mise en service

1. `scripts\preflight_windows.ps1` → bilan `poste prêt` (0 problème).
2. `Copy-Item .env.example .env` puis secrets renseignés (jamais commités).
3. Comptes nominatifs créés (Lot L.2) :
   `python -m seamtech_search.comptes.cli creer --identifiant ... --nom "..." --role administrateur`
   (mot de passe lu sur **STDIN**, jamais en argument).
4. `docker compose up -d` puis `curl http://localhost:8000/ready` → `ok`
   (depuis le poste lui-même ; le diagnostic Windows ne fait aucun appel réseau).
5. Front `http://localhost:3000` → écran de connexion répond.
6. Sauvegarde initiale : `scripts\backup_sqlite.ps1` ou `scripts\backup_postgres.ps1`
   → un fichier daté dans `data\backups\`.
7. Préflight d'arrivée d'archive à blanc sur **fixtures synthétiques**
   (`sample_data`) : voir `docs/ARRIVEE_ARCHIVE.md` — ce n'est PAS l'archive
   réelle.

## 6. Vérification après redémarrage de Windows

1. Se reconnecter, ouvrir PowerShell, relancer `scripts\preflight_windows.ps1`.
2. Docker Desktop : démarrage **manuel** si pas de démarrage auto accepté par
   le poste — le script ne le démarre jamais.
3. `Get-Service *docker*` → `Running` ; `docker ps` → 5 conteneurs `Up`.
4. `curl http://localhost:8000/ready` → `ok` (pile remontée `restart: unless-stopped`).
5. Ports 3000/8000/9000/9001/5433/6379 : soit les conteneurs, soit libres —
   jamais occupés par une autre application.
6. Relancer une vérification des sauvegardes (§7).

## 7. Vérification des sauvegardes

1. `Test-Path data\backups` → `True`.
2. Un fichier daté récent (`search-AAAAAAJJ-HHMMSS.db` ou dump Postgres) est
   présent ; âge < 24 h en exploitation.
3. **Épreuve de restauration au moins une fois** avant l'arrivée de l'archive
   (état du projet : non encore prouvée — voir
   `docs/verite_terrain/RUNBOOK_RESTAURATION.md`) :
   `scripts\restore_sqlite.ps1` / `scripts\restore_postgres.ps1` sur copie.
4. Copie hors-site chiffrée : `docs/verite_terrain/RUNBOOK_RESTAURATION.md`.
5. Jamais de sauvegarde dans la source d'archive (RG13) — les sauvegardes
   vivent dans `data\backups\`, hors archive.

---

## Ce que ce document ne prétend pas

- Aucune mesure issue de ce document ne vaut pour l'archive réelle : tout est
  vérifié sur le poste et les fixtures tant que les PDF de production ne sont
  pas arrivés.
- L'épreuve de restauration réelle (R2/S3) reste à réaliser — elle est listée
  comme dépendante des données réelles dans
  `docs/verite_terrain/RAPPORT_PREPARATION_SANS_ARCHIVE_20260924.md`.
