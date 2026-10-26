# Inventaire parcours réels — 2026-10-13 — SHA 5438b9e

**SHA : `5438b9e22d0a2d0e7bad58580db92fc016be9db7`**

## 1. Pages frontend (Next.js app router)

- `/` — accueil / recherche simple
- `/recherche` — recherche avancée, filtres, facettes, suggestions, pagination
- `/validation` — file de validation, tri par confiance, paliers, filtre gabarit
- `/fiches/[code]` — fiche détaillée, champs, corrections, historique, pièces jointes
- `/dossier/[code]` — dossier ?
- `/dossiers` — liste lots
- `/fichiers` — fichiers par ID, téléchargement
- `/gabarits` — gabarits, brouillons
- `/nouveau` — import (ImportPanel)
- `/qualite` — tableau de bord (10 indicateurs : taux_extraction_auto, taux_correction_par_champ, temps_validation, anomalies_frequentes, volume_par_statut, usage_recherches, lots, doublons_detectes, taux_par_utilisateur, ocr_etage3)
- `/login` — authentification

Composants clés :
- `components/import-panel.tsx` — drag & drop réel (DataTransfer, webkitGetAsEntry, file input hidden), scan, confirmation, suivi job polling 500ms, annulation, résultat, téléchargement rapports PDF/Word, source PDF/Excel
- `components/archive-browser.tsx`, `fichiers-page.tsx`, `dossiers-app.tsx`, `fiche-app.tsx`, etc.

## 2. Routes API backend (FastAPI, `seamtech_search/api.py`)

- `GET /live`, `/ready`, `/`, `/health` — vivacité
- `GET /search?q=&filtres=&tri=&offset=&limit=` — recherche principale (full-text, trigrammes, dimensions, RRF hybride)
- `GET /recherche` — alias ? (via frontend proxy)
- `GET /metrics`, `GET /preview?path=`, `POST /open?path=` — ouverture PDF
- `POST /imports` — import rapide depuis chemin serveur
- `POST /imports/scan` — scan dossier serveur → candidates PDF + Excel + warnings
- `POST /imports/confirm` — confirmation PDF technique + Excel facultatif → job
- `POST /imports/upload` — **parcours navigateur prioritaire** : multipart `files` + `folder`, validation collisions (409), staging temporaire, `scan_folder`, audit, retour candidates
- `GET /imports/{import_id}` — suivi job
- `POST /imports/{import_id}/cancel`, `PATCH /imports/{import_id}`, `POST /imports/{import_id}/retry-upload`, `GET /imports/{import_id}/artifacts/{artifact}` — rapports PDF/Word, source PDF/Excel
- `POST /maintenance/replay-deadletters`, `GET /maintenance/deadletters`, `POST /maintenance/cleanup`, `GET /audit`
- `seamtech_search/fiches/routes.py` — `/fiches`, `/fiches/{code}/action` (valider, rejeter, corriger, rouvrir), `/fiches/{code}/champs`, `/etat`, `/historique`, `/pieces`, `/doublons`
- `seamtech_search/dedup/routes.py`, `comptes/routes.py`, `qualite/routes.py`, `ml/routes.py`

## 3. Worker

- `seamtech_search/worker.py` — `process_import_task` (claim, heartbeat, extraction, rapports, upload S3, indexation)
- `seamtech_search/worker_service.py` — service worker séparé (Redis queue)
- `seamtech_search/extraction_worker.py`, `redis_store.py`, `jobs.py` — file durable (pending, running, completed, failed, dead_letter, claim, heartbeat, requeue)
- `seamtech_search/ocr/` — `pipeline.py` (doit_oceriser_page, ocriser_fichier, ocriser_pages, _has_command, _tesseract_version, _texte_natif), `inventaire.py`, `etat.py`, `cli.py` (nuit, inventaire, rapport)
- `seamtech_search/crawler.py` — crawl root_paths, extraction via `extractors.py`
- `seamtech_search/indexer.py` — SearchIndex (SQLite/PostgreSQL, migrations 001..019, pgvector, unaccent, trigrammes, recherche.py)

## 4. Config

- `config/config.example.json` → `config/config.json` (S3 env overrides)
- `seamtech_search/config.py` — AppConfig (root_paths, database_path, database_url, auth_token, enable_ocr bool via env SEAMTECH_ENABLE_OCR, tesseract_command, storage_backend local/s3, min_free_bytes, etc.)
- `docker-compose.yml` — postgres (pgvector), redis, minio (image reconstruite), web, frontend, volumes data/logs, healthchecks, loopback-only ports, secrets UI

## 5. Tests (sélection par marqueurs)

- **Sans service** (`-m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"`) — 1104+ tests (suite SQLite) : extractors, chaînage, normalisation, garde-fous, etc.
- **PostgreSQL** (`-m "postgres and not perf and not sauvegarde and not redis_queue and not ocr_suite"`) — 278 tests : recherche, dimension, fiches, validation, révision optimiste, qualité tableau, etc.
- **File durable** (`-m "redis_queue"`) — Redis réel + PostgreSQL : lot interrompu reprise, redémarrage Redis AOF, supervision jobs
- **OCR** (`-m "ocr_suite"`) — tesseract local : fixtures ocr_propre/degrade, qualité taux mots, inventaire, pipeline, garde-fou OCR étage 3 ROUGE→VERT
- **Sauvegarde** (`-m "sauvegarde"`) — MinIO réel, PG réel : aller-retour via S3, base détruite DROP DATABASE puis reconstruite depuis bucket seul, objets manquants, SHA, corrompue, reprise worker
- **Perf** (`-m perf`) — latence p50/p95/max sans --cov, chauffe jetée, publication JSON
- **S3** (`-m s3`) — MinIO réel : docker compose infra, identités restreintes
- **E2E Playwright** (`frontend/e2e/`) :
  - `validation.spec.ts` — parcours validation vraie fiche 7792-SO, chrono <2min, badge Non vérifiée
  - `concurrence.spec.ts` — 7 scénarios multi-sessions (conflit révision 409, reprise, import pendant recherche/téléchargement, session expirée, refus admin backend, décision liée révision revue, révision non lue bloquée, réponse tardive sans écrasement)
  - `doublons.spec.ts` — bandeau doublon (fiche 7792-SO-BIS même empreinte)
  - `auth.spec.ts` — 26 tests comptes nominatifs, rôles, révocation cookie
  - `hors-ligne.spec.ts` — pile réelle hors ligne, iptables --uid-owner, contrôle négatif DNS + compteur REJECT, 0 paquet externe
  - `scan-browser-ocr.spec.ts` — **prioritaire** : dépôt PDF image via setInputFiles (glisser-déposer réel), termes uniques ZEPHYRINE_ÉLÉGANCE_42 hors métadonnées, fichiers détectés, confirmation, état, OCR, recherche UI, téléchargement SHA-256, cas erreur invalide/blank
- **Recette corpus réel** (`-m recette_corpus`) — 7 ZIP REF-001..007, RG13 SHA avant/après, import flux réel, OCR étages, recherche mots-clés, ouverture PDF téléchargeable, SHA identique, rapport PDF, sauvegarde/restauration base neuve
- **Recette locale** (`scripts/recette_locale.sh`) — machine vierge, Docker, compte nominatif, dépôt 7 ZIP, suivi lot, validation, recherches texte/dimension/filtres/suggestions, PDF présigné + zone surlignée, rejeu idempotent, sauvegarde/restauration, persistance down/up

## 6. Sauvegarde/restauration

- `seamtech_search/sauvegarde.py` — sauver (dump PG + archive fichiers locaux + objets MinIO + config sans secrets), restaurer vers cibles distinctes, vérification SHA, sources indisponibles
- `scripts/provisionner_stockage.sh` — buckets + versioning + identités restreintes applicative/sauvegarde
- `scripts/construire_image_minio.sh` — reconstruction MinIO depuis sources archivées (registries gone)

## 7. Performance

- `tests/test_perf_*` + `seamtech_search/recherche.py` — p50 <100ms, p95 <100ms produit, seuil CI 250ms étiqueté, mesures publiées ::notice
- `scripts/qualification_native.sh` — installation native Debian/Ubuntu, API+worker+frontend hôtes, OCR hôte, sauvegarde/restore, kill+relance

## 8. Sécurité / garde-fous

- `tests/test_selection_ci.py` — sélection par marqueur, pas -k, inventaire S3 réel, garde-fou hors-ligne, concurrence, live e2e
- `tests/test_garde_fous_preparation.py` — RG13, RG14, rapports OCR sans identifiants
- `scripts/audit_projet.py`, `coverage_gate.py`, `audit_dependency_policy.py`

Tous les parcours sont **réels**, non mockés (sauf doubles explicitement marqués comme tels et exclus de la CI sans service).
