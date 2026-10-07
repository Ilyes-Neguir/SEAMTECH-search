# Rapport complet de session — 2026-10-06

Objectif de la session : « finir SEAMTECH Search pour une mise en service locale
fiable ». Ce document recense **tout** ce qui a été fait, **tout** ce qui passe,
et **tout** ce qui ne passe pas — y compris ce qui n'a pas pu être exécuté ici et
pourquoi. Aucun « sans doute bon ».

Documents liés :

* `docs/verite_terrain/ECARTS_ET_CORRECTIFS_2026-10-06.md` — écarts E-1…E-21, un par un ;
* `docs/verite_terrain/RAPPORT_RELEASE_READINESS_2026-10-06.md` — verdicts séparés et portes restantes ;
* `docs/FILE_DURABLE.md` — contrat de la file durable (promesses et non-promesses).

---

## 1. Verdicts

| Verdict | Résultat | Raison en une ligne |
|---|---|---|
| Prêt pour essais développeur | **OUI** | 1 073 tests passent ici sur services réels (PostgreSQL 16 + pgvector, Redis 7.2.5, vrai processus worker tué) |
| Prêt pour pilote atelier contrôlé | **NON** | 4 portes : image MinIO reconstruite, comptes S3 restreints, recette navigateur 3 postes, import + recherche réels jugés par un humain |
| Prêt pour production | **NON** | Tout ce qui précède + restauration indépendante, sauvegarde hors site, hors-ligne prouvé réseau coupé, échelle mesurée, calibration |

---

## 2. Ce qui passe (mesuré, ce jour, dans ce bac à sable)

Environnement : PostgreSQL 16 + pgvector 0.8.0 (pgserver, port 5433), Redis
7.2.5 réel (port 6379), Python 3.11, `.venv` du dépôt.

| Suite | Sélection | Résultat |
|---|---|---|
| 1 — sans service (SQLite) | `-m "not postgres and not s3 and not perf and not recette_corpus and not integration_docker and not redis_queue"` | **822 passés, 3 sautés** (0 échec) |
| 2 — PostgreSQL réel | `-m "postgres and not perf and not redis_queue"` | **226 passés, 2 sautés** (0 échec) |
| 3 — file durable | `-m "redis_queue"` (Redis + PostgreSQL réels) | **25 passés, 0 sauté** (0 échec) |
| Lint | `ruff check .` | aucun diagnostic |
| Compilation | `python -m compileall -q seamtech_search` | OK |

**Total** : 1 111 tests collectés, **1 073 exécutés et passés ici**, 5 sautés
(raisons ci-dessous), 33 non exécutés ici (raisons ci-dessous).

### 2.1 Les 5 sauts, avec leur raison exacte

| Test | Raison du saut |
|---|---|
| `test_ocr_etages.py` (2 tests de qualité OCR) | `tesseract` absent du bac à sable — mesuré en CI |
| `test_ocr_etages.py` (1 test de disponibilité) | idem |
| `test_modele_maison.py:356` | poids e5 absents (`SEAMTECH_ML_E5_DIR` non positionné) — la CI les télécharge explicitement |
| `test_sauvegarde_restauration.py:348` | aucun endpoint S3 réel ici — réservé au job CI `sauvegarde` (MinIO réel) |

Aucun de ces sauts ne masque un échec : ce sont des dépendances externes, et la
CI refuse un job où ils seraient sautés alors que les services sont annoncés.

### 2.2 Scénarios d'échec RÉELLEMENT joués (ils passent)

| Scénario | Ce qui est mesuré |
|---|---|
| Worker tué en plein import (`SIGKILL` sur un vrai PID) | Job `running` conservé, revendication expirée, second worker reprend, job `completed`, `attempts ≥ 2`, **6 documents indexés une fois** (0 doublon) |
| Redémarrage brutal de Redis (serveur réel dédié, AOF) | La tâche en attente ET la revendication de la tâche en cours survivent |
| Lot de 3 dossiers interrompu après 1 dossier | Reprise : 3 dossiers traités, 2 fiches (2 contenus distincts), 0 doublon, lot `termine` |
| Livraison dupliquée du même lot | `duplicate_delivery: true`, aucune fiche créée |
| Redis injoignable + `require_durable_queue` | HTTP 503, **aucun** job créé, message exploitable |
| Tentatives épuisées | Lettre morte avec raison lisible en base |
| Base antérieure à la migration 020 | Démarre, se migre, colonnes + index présents |
| Vérification d'intégrité du stockage | Empreinte SHA-256 comparée ; objet non conforme ⇒ `failed`, purge locale interdite |
| `--verifier` sans Redis | Code de sortie 2 et raison explicite (superviseur visible) |

---

## 3. Ce qui ne passait PAS et a été corrigé (défauts réels)

| # | Défaut | Détecté par | Correction |
|---|---|---|---|
| 1 | Le prédicat d'annulation était **inversé** : tout lot durable était annulé au premier dossier | Nouveau `tests/test_file_durable_postgres.py` (le lot sortait `cancelled`) | `doit_continuer` renvoie `not is_job_cancelled(...)` |
| 2 | `initialize()` créait un index sur `heartbeat_at` **avant** la migration 020 ⇒ une base existante ne redémarrait plus (`UndefinedColumn`) | Suite PostgreSQL (6 échecs) | Index créé sous garde `information_schema`, la migration ajoute colonne puis index |
| 3 | La vérification après envoi S3 ne prouvait que l'**existence** (R-10 de l'audit) | Nouveau test d'intégrité | Métadonnées `seamtech-sha256`/`seamtech-taille` + relecture taille+empreinte (jamais l'ETag) |
| 4 | `worker.py` importait `socket` (interdit RG14) | Garde-fou `test_garde_fous_preparation` | `platform.node()` |
| 5 | Le chemin relatif d'origine n'était pas conservé avec l'objet | Revue de l'exigence « conserver le chemin d'origine » | Métadonnée `seamtech-chemin` (encodée en pourcentage) + test |
| 6 | `get_job()` n'exposait pas l'état durable d'un job | Nouveaux tests de file durable | Projection étendue (SQLite + PostgreSQL) |
| 7 | `recover_stale_jobs` marquait « échoué » un job simplement en attente dans la file | Nouveaux tests | Liste d'abord, écarte ce qui est encore en file, ne marque que les vrais orphelins |

### 3.1 Échecs de tests rencontrés pendant la session (tous clos)

| Moment | Échecs | Cause | Traitement |
|---|---|---|---|
| Suite sans service, 1ʳᵉ passe | 4 | pin 019 (2 tests), `socket` (1), mock de `recover_stale_jobs` obsolète (1) | corrigés (code + attentes) |
| Après le durcissement du stockage | 3 | doubles de test renvoyant `object_exists=True` au lieu d'un en-tête réaliste | doubles mis à jour |
| `test_stockage_release_candidate` | 3 | assertions figées sur l'ancien `ExtraArgs` | mises à jour (empreinte, échec partiel) |
| Suite PostgreSQL v1 | 6 | index sur colonne absente sur une base ancienne | garde `DO $$ … information_schema` ⇒ 226 passés |
| `test_file_durable.py` (première écriture) | 6 | **hypothèses de test fausses** (dossier sans PDF technique, `task_id` manquant, `durability` non exposé) | fixtures réelles + projection étendue |
| `test_file_durable_postgres` | 1 puis 1 | défaut produit n°1, puis comptage de fiches erroné (contenus identiques) | code corrigé, puis attente corrigée |

Aucun test n'a été supprimé, désactivé, ni rendu vert en le sautant.

---

## 4. Ce qui n'a PAS été exécuté ici (et pourquoi)

**33 tests** ne tournent pas dans ce bac à sable :

| Groupe | Nombre | Pourquoi |
|---|---|---|
| `tests/test_recette_corpus_reel.py` | 24 | Recette du corpus réel : exige MinIO vivant, `SEAMTECH_RECETTE_CORPUS=1` et le corpus extrait — job CI dédié |
| Tests de performance (`perf`) | 6 | Mesures p95 sur corpus chargé — job CI/`recette` dédié |
| `tests/test_integration_docker.py` | 2 | Exige Docker + compose complet — **Docker absent ici** |
| `test_storage.py::test_live_minio_s3_integration` | 1 | Exige un endpoint S3 réel |

Deux épreuves de sabotage (`ci_guard_dimension`, `ci_guard_attribution`) ne sont
pas non plus rejouées ici : elles modifient le code source, cassent
volontairement, vérifient le rouge, restaurent, vérifient le vert. Elles
s'exécutent en CI.

---

## 5. Ce qui reste ouvert (aucune de ces lignes n'est « résolue »)

1. **MinIO réel jamais exécuté ici** : ni Docker, ni toolchain Go, registres
   bloqués. Toute la preuve stockage de cette session est faite sur doubles
   boto3 ; la CI exécute les épreuves réelles.
2. **Image MinIO à reconstruire** sur le poste cible
   (`bash scripts/construire_image_minio.sh`) — case 1.6 de la checklist.
3. **Comptes S3 applicatifs restreints** (R-7) : les identifiants restent ceux de
   root MinIO tant qu'un MinIO vivant n'existe pas ; `/health` affiche
   `s3_credentials: root_like`.
4. **Recette navigateur à 3 postes** : aucun navigateur, aucun second poste ici.
5. **Import réel + recherche jugée par un humain** : le corpus du dépôt n'est pas
   l'archive de l'atelier.
6. **Restauration indépendante** et **destination de sauvegarde hors site** :
   l'aller-retour automatisé passe, l'exercice humain n'a pas eu lieu.
7. **Hors-ligne prouvé** : à tester réseau coupé sur le poste cible.
8. **Dimensionnement** : à mesurer sur l'archive réelle.
9. **Calibration ML** : toujours verrouillée (`calibre: false`), faute de
   300–500 fiches validées — décision assumée, pas un défaut du logiciel.

---

## 6. Fichiers touchés

**Code (16)** : `api.py`, `config.py`, `etat_exploitation.py` (nouveau),
`fiches/depot.py`, `fiches/routes.py`, `indexer.py`, `jobs.py`, `redis_store.py`,
`schema_metier.py`, `storage.py`, `worker.py`, `worker_service.py` (nouveau),
plus `pyproject.toml`, `docker-compose.yml`, `.env.example`,
`.github/workflows/ci.yml`.

**Tests (7 modifiés, 3 nouveaux)** :
`test_jobs_postgres_mock.py`, `test_ocr_comportement.py`,
`test_recette_corpus_reel.py`, `test_selection_ci.py`,
`test_stockage_release_candidate.py`, `test_storage.py`,
`test_storage_coverage.py` ;
nouveaux : `test_file_durable.py` (521 l.), `test_file_durable_postgres.py`
(382 l.), `test_worker_process.py` (241 l.).

**Documentation (6)** : `docs/FILE_DURABLE.md` (nouveau),
`docs/verite_terrain/ECARTS_ET_CORRECTIFS_2026-10-06.md` (nouveau),
`docs/verite_terrain/RAPPORT_RELEASE_READINESS_2026-10-06.md` (nouveau),
`docs/verite_terrain/RAPPORT_SESSION_COMPLET_2026-10-06.md` (ce fichier),
`README.md`, `docs/DEPLOYMENT.md`, `docs/RELEASE_CANDIDATE_CHECKLIST.md`.

Volume : **24 fichiers modifiés, 2 066 insertions**, + 8 nouveaux fichiers.
