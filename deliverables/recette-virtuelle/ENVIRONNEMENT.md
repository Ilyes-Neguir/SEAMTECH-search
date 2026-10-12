# Environnement de recette — SEAMTECH Search

Document de traçabilité de l'environnement **réellement** utilisé pour exécuter la recette
fonctionnelle. Toute case non exécutée est marquée comme telle ; aucun niveau de preuve n'est
confondu avec un autre (§12 du protocole).

## 1. Version testée

| Élément | Valeur |
| --- | --- |
| Commit candidat | `3304b9105dff9929011f5c299a83d6fc36ab5132` (pull request #36) |
| État de la PR #36 | **MERGED** le 2026-10-12T15:01:58Z, merge commit `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| Arbre testé | clone du dépôt du 2026-10-12, `HEAD = 8dd5ae7` (checkout branche `arena/c5279d06-seamtech-search`), arbre **identique** à celui du commit candidat ; **aucun fichier de code n'a été modifié** pour tester. Seul écart, purement typographique : `scripts/recette_locale.ps1` est présent au travail avec des fins de ligne CRLF, comme l'impose `.gitattributes` du dépôt (`*.ps1 text eol=crlf`) ; le blob du candidat contient une ligne isolée en CRLF. Ce fichier **PowerShell n'a pas été exécuté** et n'est pas modifié par la recette |
| Relevé de l'écart | `git log -1 --format=%H` puis comparaison `git diff --stat 3304b910 HEAD` (0 fichier de code différent) |
| Branche de recette | `arena/c5279d06-seamtech-search` (les scripts et preuves de recette y sont ajoutés, jamais dans `main`) |

## 2. Machine et socle

| Élément | Valeur mesurée |
| --- | --- |
| Hôte | bac à sable Linux isolé, Debian GNU/Linux 12 (bookworm), noyau `6.1.158+`, x86_64 |
| Ressources | 2 vCPU, 3 939 Mo de RAM, disque racine 21 Go (≈ 11 Go libres au lancement) |
| Réseau sortant | autorisé vers `github.com`, `codeload.github.com`, `api.github.com`, `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org` ; **injoignables** : `deb.debian.org`, `archive.ubuntu.com`, `dl.min.io`, `storage.googleapis.com`, `golang.org`, `proxy.golang.org`, `cdn.playwright.dev` et tous les registres de conteneurs |
| Conteneurs | **aucun** : ni Docker, ni Podman, ni registre joignable |

Conséquence directe : la pile du `docker-compose.yml` du dépôt a été **transposée en
processus natifs** sur cette même machine. Aucun niveau de preuve « Docker Desktop Windows »
n'existe dans cette recette, et le parcours PowerShell `scripts/recette_locale.ps1` n'a **pas
été exécuté** (voir §7).

## 3. Transposition de `docker-compose.yml`

| Service du compose | Image prévue | Ce qui a été exécuté ici | Preuve |
| --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` | **PostgreSQL 16.2** compilé depuis `postgres-REL_16_2.tar.gz` (codeload), extensions `vector 0.8.0`, `unaccent 1.1`, `pg_trgm 1.6` compilées sur place ; écoute `127.0.0.1:5433`, base `seamtech_search`, migrations `001` → `022` | `LOGS/migrations.log`, `PREUVES/environnement.json` |
| `redis` | `redis:7-alpine` | **Redis 7.4.2** compilé (`src/redis-7.4.2`), `requirepass`, AOF activé, `127.0.0.1:6379` | `LOGS/redis.log` |
| `minio` | `minio/minio` | **Substitut déclaré : `s3rver` 3.7.1** (serveur S3 de test, Node) sur `127.0.0.1:9000`. Le binaire MinIO n'était atteignable par aucun canal (voir §7 et `ANOMALIES.md` **A-01**) | `PREUVES/provisionnement_stockage.json` |
| `web` (API) | `uvicorn seamtech_search.api:app` | `python -m seamtech_search serve --config config/config.json`, `127.0.0.1:8000` | `LOGS/api.log` |
| `worker` | process de traitement | `python -m seamtech_search.worker_service`, file `seamtech:queue:imports`, `claim TTL 300 s` | `LOGS/worker.log` (4 000 dernières lignes) |
| `frontend` | build Next.js | `next start` du build *standalone* (`frontend/.next/standalone`), `0.0.0.0:3000` | `LOGS/frontend.log` |

## 4. Versions exactes

| Composant | Version |
| --- | --- |
| Python (environnement de recette) | 3.11.2 (`rv/env/venv`) |
| PostgreSQL | 16.2 (client `psql` inclus) |
| Extensions PostgreSQL | `vector 0.8.0`, `unaccent 1.1`, `pg_trgm 1.6`, `plpgsql 1.0` |
| Redis | 7.4.2 (mode `standalone`, AOF) |
| Substitut S3 | `s3rver` 3.7.1 (Node 22.22.3) |
| Node.js | 22.22.3 |
| Next.js (frontend) | 16.3.8 |
| Playwright (pilote) | 1.63.0 |
| Navigateur réel | **Chromium 153.0.8010.0** (extrait de `@sparticuz/chromium` 153.0.0) |
| Bibliothèque de PDF | `pdfplumber`, `reportlab` (génération du corpus synthétique) |
| Bibliothèque Excel | `openpyxl` |

## 5. Isolation de la recette (aucune donnée de production)

| Ressource | Valeur dédiée |
| --- | --- |
| Base de données | `seamtech_search` sur l'instance locale (aucune connexion externe) |
| Base jetable de la suite e2e | `e2e_<run_id>` créée/semée/détruite par `frontend/e2e/seed-live-pg.py` |
| Buckets | `seamtech-documents` (documents) et `seamtech-backups` (sauvegardes), créés par `rv/harnais/provisionner_stockage.py` |
| Racines autorisées | `SEAMTECH_ROOT_PATHS=/home/user/rv/corpus/sources:/home/user/rv/corpus/depots:/home/user/rv/corpus/recette_sources:/home/user/rv/data` |
| Comptes | `recette` (opérateur), `recette-b` (opérateur), `recette-c` (administrateur) — mots de passe **générés** dans `rv/secrets/`, jamais ceux d'une exploitation |
| Jeton de service, secrets de session, identifiants S3 | tous **générés pour la recette** (`rv/recette.env`, `rv/secrets/`), aucun secret de production, aucun secret recopié dans les livrables |
| Zone de travail des dépôts | `rv/data/recette-lot/` — **distincte** des volumes de données (`rv/data/objets`, `rv/data/pgdata`, `rv/data/redis`) que les essais de panne arrêtent ou manipulent |
| Configuration résolue | `rv/config/recette.config.json` (copie conforme dans `config/config.json` du dépôt), **contrôlée** : `PREUVES/config_resolue.json` — 0 écart avec `recette.env` |
| Exposition réseau | tous les services écoutent en `127.0.0.1` (loopback), sauf le frontend qui écoute aussi sur l'interface du bac à sable pour l'aperçu utilisateur ; aucun service n'est exposé sur Internet |

## 6. Corpus de recette

| Élément | Valeur |
| --- | --- |
| Emplacement | `rv/corpus/sources/` (zone jetable ; les essais travaillent sur des **copies**) |
| Volumétrie | **453 fichiers**, 2 156 313 450 octets, 26 répertoires |
| Manifeste | `MANIFESTES/MANIFESTE_CORPUS.csv` (chemin relatif, taille, SHA-256, catégorie, résultat attendu) |
| Contrôle pré/post | `PREUVES/integrite_corpus.{json,csv}` — 453 fichiers identiques au manifeste, 0 modifié, 0 ajouté, 0 supprimé |
| Origine | copies **anonymisées** fournies avec le dépôt (7 affaires, 429 fichiers) → fixtures du dépôt (6 fichiers) → synthétiques créés ici (18 fichiers) |

Couverture exigée par le protocole, dossier par dossier :

| Cas exigé | Dossier du corpus | Contenu |
| --- | --- | --- |
| PDF à texte | `03_synthetique/SYNTH-PDF-TEXTE` | fiche PDF à couche texte |
| PDF scanné (OCR) | `03_synthetique/SYNTH-PDF-SCANNE` | PDF image sans couche texte |
| Fichier non reconnu | `03_synthetique/SYNTH-CAS-DEGRADES/facture syndic inconnue.pdf` | document hors gabarits |
| Fichier illisible | `03_synthetique/SYNTH-CAS-DEGRADES/illisible corrompu.pdf` | PDF corrompu |
| Fichier vide | `03_synthetique/SYNTH-CAS-DEGRADES/fichier vide.pdf` | 0 octet |
| Excel | `03_synthetique/SYNTH-EXCEL`, `02_fixtures_depot/CLIENT-123/nomenclature-voile.xlsx` | nomenclatures |
| Plans et images | `03_synthetique/SYNTH-ACCENTS …/sous-dossier plans et images` | `.dxf`, `.png` |
| Accents et espaces | `03_synthetique/SYNTH-ACCENTS ÉTÉ 2026 (essai)` | dossier et fichier accentués, espaces et parenthèses |
| Sous-dossiers | `SYNTH-ACCENTS…/sous-dossier …`, `01_reels_anonymises/BAVARIA 32 …/TISSU` | pièces en sous-répertoire |
| Homonymes / collisions | `03_synthetique/SYNTH-COLLISIONS/atelier {A,B}/fiche.pdf` | deux fichiers de même nom, contenu distinct |
| Doublons | `03_synthetique/SYNTH-DOUBLONS` | fiche + copie conforme |
| Fiche inexploitable | `03_synthetique/SYNTH-SANS-FICHE/notes diverses/reunion.txt` | dossier sans fiche |
| Dépassement de taille | `03_synthetique/SYNTH-SURDIMENSIONNE/archive enorme.bin` | 2 147 483 648 octets (limite produit : 512 Mio) |
| Dossiers métier réels | `01_reels_anonymises/` | 7 affaires, 429 fichiers, PDF + plans + Excel |
| Multi-documents | `03_synthetique/SYNTH-MULTI-PDF` | fiche + annexe + plan |
| Dossier sans PDF | `02_fixtures_depot/CLIENT-456` | note texte seule |

## 7. Ce qui n'a **pas** été exécuté (et pourquoi)

| Élément du protocole | État | Raison |
| --- | --- | --- |
| VM Windows + `scripts/recette_locale.ps1` + parcours PowerShell | **NON EXÉCUTÉ** | aucune VM Windows n'est disponible dans ce bac à sable Linux ; le script a seulement été relu, jamais exécuté. Aucune conclusion Windows ne peut être tirée de cette recette |
| Docker Desktop Windows, `docker compose up` | **NON EXÉCUTÉ** | aucun moteur de conteneur ni registre joignable |
| MinIO officiel | **NON EXÉCUTÉ** | binaire introuvable par tous les canaux accessibles (releases sans binaire, CDN et registres bloqués). Substitut `s3rver` **déclaré** : versioning, politiques IAM et vérification de signature ne sont pas couverts |
| OCR (`tesseract`, `ocrmypdf`) | **NON EXÉCUTÉ** | binaires absents et non installables (APT injoignable) ; `enable_ocr=false` dans la configuration de recette. Le cas « PDF scanné » est donc évalué sans OCR |
| Modèle d'embeddings `e5` | **NON EXÉCUTÉ** | modèle absent du dépôt ; la recherche par dimension fonctionne par cotes et unités, pas par vecteurs |
| « Trois salariés » (§7) | **SIMULATION TECHNIQUE** | trois **sessions navigateur** simultanées (2 opérateurs + 1 administrateur) sur la même machine. Ce n'est ni une acceptation humaine, ni un test d'utilisabilité |
| Sauvegarde/restauration vers un stockage objet externe réel | **NON EXÉCUTÉ** | seule la copie objet locale du substitut S3 a été vérifiée (`PREUVES/pannes_et_reprises.json`, volet stockage) |
| Charge volumétrique (dizaines de dossiers simultanés) | **NON EXÉCUTÉ** | hors périmètre du protocole ; aucune extrapolation de performance n'est faite ici |

## 8. Emplacement des preuves

| Objet | Chemin |
| --- | --- |
| Preuves machine | `PREUVES/environnement.json`, `PREUVES/config_resolue.json`, `PREUVES/integrite_corpus.*` |
| Recette officielle du dépôt | `PREUVES/recette_verif.log` (19/19, `controles_echoues\|aucun`) |
| Recette API + navigateur | `PREUVES/recette_api.json`, `PREUVES/navigateur/` |
| Pannes et reprises | `PREUVES/pannes_et_reprises.json` + `LOGS/pannes_final.log` — **run définitif du 2026-10-12 : 23/23 PASS** (l'historique des passes intermédiaires reste dans `rv/logs/`, hors dépôt) |
| Essai ciblé import + panne du stockage | `PREUVES/essai_panne_stockage_import.json` |
| Copie locale rétablie / pièces orphelines | `PREUVES/reparation_copies_locales.json`, `PREUVES/verification_telechargements.json` |
| Suite e2e du dépôt | `PREUVES/e2e-rapport_passe4.json` → `_passe9.json`, `LOGS/e2e_depot_passe{4..9}.log` (mode nominal : passe 9 ; hors ligne isolé : passe 8) |
| Stockage objet | `PREUVES/provisionnement_stockage.json` |
| Journaux d'exécution | `LOGS/` (voir `LOGS/LISEZMOI.md` pour la politique de troncature, `LOGS/MANIFESTE_LOGS.json` pour les lignes conservées) |
| Verdict | `PV_RECETTE_TECHNIQUE.md`, `MATRICE_RECETTE.csv`, `ANOMALIES.md` |
| Harnais exécuté | `HARNAIS/` — les scripts réellement lancés (aucun identifiant en clair ; la configuration vient d'un `recette.env` local non versionné) |
| Secrets | `PREUVES/MANIFESTE_MASQUAGE.json` — les valeurs d'identifiants de l'environnement de recette ont été remplacées à la copie ; les preuves d'origine (non masquées) restent hors dépôt |
