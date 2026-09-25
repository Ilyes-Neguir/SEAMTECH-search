# Audit du stockage objet S3 / MinIO — avant mise en production

**Date** : 2026-09-25 (rédaction) · **Révision** : 2026-09-25, après correctifs D-1 et
R-14 · **Base** : `origin/main` = `f8befb5da800c10d793e25addf9519cc63f5fd6c`
(fusion de la PR #28).

> **Ce lot ne remplace pas MinIO.** Aucun backend de stockage, nom de bucket, schéma
> de clé d'objet, contrat d'API ni test existant n'a été supprimé ou renommé. Ce
> document constate, mesure, liste les risques et prépare une décision de backend ;
> il ne la prend pas.
>
> **Deux défauts constatés par cet audit ont depuis été corrigés**, sur demande du
> commanditaire et dans la même journée : **D-1** (la redirection 302 vers une URL
> présignée n'était jamais servie — §6) et **R-14** (la CI désélectionnait des tests
> par sous-chaîne de leur nom — §5.2). Le texte conserve le constat d'origine **et**
> la mesure d'après correctif : c'est la trace qui vaut preuve, pas la version
> réécrite.

**Limites de cet audit (à lire avant toute conclusion chiffrée)** :

- **Lot G réel bloqué** : l'archive de production n'est pas livrée. Aucun chiffre de
  cet audit ne provient d'un volume réel (ni nombre d'objets, ni octets, ni durée
  d'envoi).
- **F-2 bloqué** : les 300 à 500 fiches validées nécessaires au réentraînement ne sont
  pas disponibles.
- **Métriques de production indisponibles** : aucune mesure sur le bucket de
  production (il n'existe pas encore). Les seules mesures réelles citées ici viennent
  de la CI (MinIO local) et de doubles de test.
- **Fixtures du dépôt non représentatives** : `sample_data/` contient 1 fiche client
  réelle et quelques fichiers synthétiques — ni l'échelle, ni la diversité de noms, ni
  les scans de l'archive réelle.

---

## 1. Architecture actuelle

```
Navigateur ─▶ Next.js (proxy serveur)  ─▶ FastAPI  ─▶ PostgreSQL 16 + pgvector   (index, object_key, audit)
                 /api/open                  /open        Redis 7                 (file d'attente, verrous)
                 /api/imports/…/artifacts   /imports/…   S3 compatible           (MinIO en dev/CI, R2/AWS prévu)
                                             │
                                             └─ worker (thread) : vérifie, met en quarantaine, autorise la purge
```

| Couche | Fichier | Rôle vis-à-vis du stockage objet |
|---|---|---|
| Client S3 | `seamtech_search/storage.py` (626 l.) | **Seul** point du code qui importe `boto3`. Construit les clés, refuse d'écraser, relit après envoi, sonde le versioning. |
| Configuration | `seamtech_search/config.py` | 9 réglages `s3_*` / stockage, tous surchargeables par variables d'environnement. |
| Import | `seamtech_search/import_pipeline.py` | Appelle `upload_artifacts_to_storage()` (import initial, retraitement, ré-essai) ; écrit `object_key`/`object_bucket`/`upload_status` par fichier. |
| Ordonnanceur | `seamtech_search/worker.py` | Porte de purge : `upload_status == uploaded` **et** `all_verified` **et** toutes les clés présentes, sinon `upload_incomplete` → `data/quarantine/` (jamais purgé). |
| Index | `seamtech_search/indexer.py` | Colonnes `object_key`, `object_bucket`, `uploaded_at`, `upload_status` (migration `002_object_storage_columns`). |
| API | `seamtech_search/api.py` | `/health` (état + versioning), `/open`, `/imports/{id}/artifacts/{artifact}` (302 présignée **prévue** → repli fichier local → téléchargement depuis S3 → 404). |
| Frontend | `frontend/app/api/open/route.ts`, `frontend/app/api/imports/[id]/artifacts/[artifact]/route.ts` | `redirect: "manual"` puis relais de la redirection (JSON `{url}` pour `/open`, 302 pour les artefacts) ; sinon proxy du flux. Le navigateur n'appelle jamais S3 directement sans URL signée. |
| Sauvegarde | `seamtech_search/sauvegarde.py` | `pg_dump -Fc` + manifeste → bucket, **relecture + comparaison sha256 après envoi**, rétention N, restauration depuis le bucket seul. |
| Infrastructure | `docker-compose.yml`, `Dockerfile`, `scripts/construire_image_minio.sh` | Service `minio` épinglé, healthcheck `mc ready local`, image reconstruite depuis les sources archivées. |

**Schéma de clé** (inchangé) : `{s3_prefix}/{import_id}/{sha256(chemin relatif)}/{nom de fichier}`
— voir `artifact_object_key()`. Deux imports du même dossier, ou deux fichiers
homonymes dans deux sous-dossiers, ne peuvent pas partager une clé.

**Buckets utilisés** : `seamtech-documents` (pièces et rapports, défaut `config.example.json`
et compose) ; `seamtech-backups` (job CI `sauvegarde`) avec le préfixe d'objet
`backups/seamtech-search-<horodatage>.dump` (+ `.manifest.json`).

---

## 2. Opérations S3 réellement utilisées

Relevé exhaustif (`grep` sur `seamtech_search/storage.py`, 2026-09-25) :

| # | Opération S3 | Appel boto3 | Ligne | Appelée par | Critique ? |
|---|---|---|---|---|---|
| 1 | `HeadBucket` | `s3.head_bucket` | 258 | `ensure_bucket_exists` (avant chaque envoi) | oui |
| 2 | `CreateBucket` | `s3.create_bucket` | 266 | idem, si 404/NoSuchBucket (`LocationConstraint` si région ≠ `us-east-1`/`auto`) | oui au 1ᵉʳ démarrage |
| 3 | `PutBucketVersioning` | `s3.put_bucket_versioning` | 290 | `_enable_versioning_once` (best effort) | non (échec toléré) |
| 4 | `GetBucketVersioning` | `s3.get_bucket_versioning` | ~330 | `versioning_status()` → `/health` (client à timeout court 2 s, cache 60 s) | non |
| 5 | `HeadObject` | `s3.head_object` | 358 | `object_key_taken` → `first_free_key` (anti-écrasement) | **oui** |
| 6 | `HeadObject` | `s3.head_object` | 401 | `object_exists` (relecture après envoi = autorisation de purge) | **oui** |
| 7 | `PutObject` / multipart | `s3.upload_file` | 443 | `upload_file` (transfert managé : bascule multipart au-delà de 8 Mio) | **oui** |
| 8 | `PutObject` | `s3.put_object` | 477 | `upload_bytes` | oui |
| 9 | `GetObject` | `s3.download_file` | 493 | `download_file` (cache froid API, restauration de sauvegarde) | **oui** |
| 10 | Présignature `GetObject` | `s3.generate_presigned_url` | 507 | `get_presigned_url` (SigV4) | oui (voir D-1) |
| 11 | `DeleteObject` | `s3.delete_object` | 521 | `delete_file` (rétention des sauvegardes) | oui |
| 12 | `ListObjectsV2` (paginé) | `s3.get_paginator("list_objects_v2")` | 535 | `list_keys` (rétention des sauvegardes) | oui |

**Réglages du client** (`_make_client`) : `signature_version=s3v4`,
`addressing_style = path` si `s3_force_path_style` (défaut **true**), `retries =
{max_attempts: 3, mode: standard}`, et pour la sonde de santé
`connect_timeout = read_timeout = 2 s`, `max_attempts = 1`.

**Ce qui n'est PAS utilisé** (donc pas à exiger d'une alternative) : object lock,
lifecycle, réplication, notifications, ACL/policy, tagging, SSE-C/SSE-KMS explicite,
`CopyObject`, `ListObjectVersions`, `SelectObjectContent`, transferts accélérés.

### 2.1 Compatibilités AWS S3 attendues

| Attendu | Où | Conséquence si non tenu |
|---|---|---|
| Signature **SigV4** | `_make_client` | aucun appel ne passe |
| **Path-style** (`endpoint/bucket/clé`) par défaut | `s3_force_path_style=True` | sur AWS S3, le style hôte virtuel est recommandé → mettre `SEAMTECH_S3_FORCE_PATH_STYLE=false` |
| `LocationConstraint` pour toute région ≠ `us-east-1`/`auto` | `ensure_bucket_exists` | `CreateBucket` refusé (400) |
| URL présignée `GET` (900 s côté API) | `get_presigned_url` | bien en deçà de la limite SigV4 (7 jours) |
| Nom de bucket DNS-compatible (`seamtech-documents`) | config | conforme aux règles AWS |
| Clés avec espaces/accents acceptées et encodées par le client | `artifact_object_key` | AWS accepte ces caractères mais les classe « à éviter » : ils doivent être encodés dans l'URL — c'est botocore qui s'en charge (test `test_cle_avec_espaces_et_accents_transmise_telle_quelle_a_boto3`) |
| `ListObjectsV2` paginé | `list_keys` | rétention des sauvegardes fausse (page 1 seulement) |
| Versioning **optionnel** | `_versioning_failure` gère `NotImplemented` / `MethodNotAllowed` / 501 | déjà prévu pour Cloudflare R2 |

---

## 3. Dépendances MinIO spécifiques

Ce qui est **propre à MinIO** et ne survivrait pas tel quel à un changement de backend :

| # | Dépendance | Emplacement | Nature |
|---|---|---|---|
| M-1 | Service `minio`, image `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z`, `command: server /data --console-address ":9001"` | `docker-compose.yml` | infrastructure |
| M-2 | Healthcheck `["CMD","mc","ready","local"]` (binaire `mc` + alias `local` embarqués dans l'image) | `docker-compose.yml` | infrastructure |
| M-3 | Sonde HTTP `GET /minio/health/live` | `.github/workflows/ci.yml` (jobs `integration` et `sauvegarde`) | CI |
| M-4 | Ports 9000 (API S3) **et 9001 (console MinIO)** | compose, `docs/CHECKLIST_POSTE_WINDOWS.md`, `docs/PROJECT_REPORT.md` | infrastructure + docs |
| M-5 | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` réutilisés **tels quels** comme `SEAMTECH_S3_ACCESS_KEY` / `SECRET_KEY` | compose, `.env.example` | sécurité (compte root, pas de moindre privilège) |
| M-6 | `scripts/construire_image_minio.sh` : clone `github.com/minio/minio` + `mc` aux tags épinglés, compilation Go, entrypoint officiel | script + 2 étapes CI | chaîne d'approvisionnement |
| M-7 | Création de bucket par l'application au premier envoi (`ensure_bucket_exists`) — acceptable avec des identifiants root MinIO | `storage.py` | droits |
| M-8 | `force_path_style=True` par défaut (MinIO sans domaine virtuel) | `config.py` | protocole |

### 3.1 Tests qui supposent MinIO

| Fichier / job | Ce qu'il suppose | Sans MinIO |
|---|---|---|
| `tests/test_construire_image_minio.py` (7 tests) | le tag du script == le tag de compose, format `RELEASE.*`, alias `local`, entrypoint officiel, clones aux tags, `set -euo pipefail`, build CI avant consommation | **rouge** (tests statiques, aucun réseau) |
| `tests/test_compose_hardening.py` | service `minio` présent, image `quay.io/minio/minio:RELEASE.`, healthcheck `mc ready local`, `MINIO_ROOT_*` déclarés, ports en loopback | **rouge** |
| `tests/test_compose_web_boot.py` | `MINIO_ROOT_USER/PASSWORD` dans l'environnement de compose, `minio` comme nom d'hôte interne | **rouge** |
| `tests/test_integration_docker.py` (marqueur **`s3`** depuis le 25/09) | `docker compose up -d postgres redis minio`, endpoint `http://127.0.0.1:9000`, bucket `seamtech-documents`, mode `STRICT` en CI | rouge en CI ; désormais **désélectionné** des suites sans service au lieu d'être collecté puis sauté |
| `tests/test_storage.py::test_live_minio_s3_integration` (marqueur `s3`) | endpoint réel via `SEAMTECH_TEST_S3_URL` | **sauté** — aucun job ne fournit cette variable (R-13 toujours ouvert) |
| Job CI `sauvegarde` | `docker run … minio server /data`, bucket `seamtech-backups` créé par boto3, sonde `/minio/health/live` | **rouge** |
| `tests/test_sauvegarde_unites.py` / `…_restauration.py` | double `S3EnMemoire` (aucun MinIO requis) ; seul `test_aller_retour_via_client_s3_reel` (marqueurs `sauvegarde` + **`s3`**) exige le vrai bucket du job CI | verts |
| `tests/test_storage*.py`, `tests/test_object_keys.py`, `tests/test_stockage_release_candidate.py` | doubles boto3, aucun réseau | verts |

**Conclusion partielle** : la dépendance MinIO est **concentrée dans
l'infrastructure et la CI**, pas dans le code applicatif. `storage.py` ne contient
aucune particularité MinIO ; le seul réglage à basculer côté code est
`s3_force_path_style`.

### 3.2 Variables d'environnement

| Variable | Défaut | Lue par | Rôle |
|---|---|---|---|
| `SEAMTECH_STORAGE_BACKEND` | `s3` | `config.py` → `/health` | **libellé seulement** (voir R-3) |
| `SEAMTECH_S3_ENDPOINT_URL` | `null` (compose : `http://minio:9000`) | `config.py` | endpoint S3 |
| `SEAMTECH_S3_BUCKET` | `seamtech-documents` | `config.py` | bucket |
| `SEAMTECH_S3_ACCESS_KEY` | `null` | `config.py` | identifiant |
| `SEAMTECH_S3_SECRET_KEY` | `null` | `config.py` | secret |
| `SEAMTECH_S3_REGION` | `us-east-1` (`auto` pour R2) | `config.py` | région / `LocationConstraint` |
| `SEAMTECH_S3_FORCE_PATH_STYLE` | `true` | `config.py` | `path` vs `virtual-hosted` |
| `SEAMTECH_S3_PREFIX` | `""` | `config.py` | préfixe applicatif des clés |
| `SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD` | `false` | `config.py` | purge locale après vérification |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | — (obligatoires, `:?`) | `docker-compose.yml` | identifiants MinIO **et** S3 de l'app |
| `SEAMTECH_TEST_S3_URL` | absent | `tests/test_storage.py` | active le test `s3` réel |
| `SEAMTECH_SAUVEGARDE_JSON` | absent | `sauvegarde.py` | publication des mesures (JSONL) |
| `SEAMTECH_SAUVEGARDE_TMP` | `data/backups` | `sauvegarde.py` | dossier de re-téléchargement du dump |
| `SEAMTECH_PG_BINDIR` | absent | `sauvegarde.py` | découverte de `pg_dump`/`pg_restore` |

### 3.3 Comportements de repli (mesurés)

| Situation | Comportement réel | Preuve |
|---|---|---|
| S3 non configuré | `UploadBatch(status="not_configured")`, aucune exception, fichiers locaux conservés | `test_sans_stockage_objet_configure_l_envoi_renvoie_not_configured` |
| Aucun fichier exploitable | `status="not_applicable"` | idem |
| Un artefact non relu | `status="partial"`, `all_verified=False` → **purge interdite** | `test_echec_partiel_interdit_la_purge_locale` |
| Envoi en échec total | `status="failed"` → worker : `upload_incomplete` + `data/quarantine/` (jamais purgé par la rétention) | `worker.py` l.78-110, `retention.py` l.140 |
| Clé déjà occupée | objet existant **intact**, écriture sur `-2`, `-3`… (`first_free_key`, 50 tentatives) | `test_first_free_key` |
| Sonde d'existence impossible | `object_exists` → `False` (donc pas de purge) ; `object_key_taken` → **lève** (donc pas d'écriture) | `storage.py` l.395-430 |
| Versioning indisponible | `versioning_available=false` (endpoint sans versioning) ou `null` (indéterminé) dans `/health` | `_versioning_failure` |
| URL présignée impossible | journal `warning` + repli fichier local, puis téléchargement S3 → cache, puis 404 | `api.py` l.475-1035 |
| Sauvegarde sans S3 | sauvegarde **locale seule**, `verifiee_apres_envoi: false`, avertissement explicite | annotation CI `sauvegarde` du run 36168258103 : `dump 145521 octets en 0.16 s, archive 2 fichiers, vérifié après envoi : False` |

---

## 4. Statut du projet MinIO archivé

### 4.1 Constats internes au dépôt (datés, reproductibles)

| Date | Constat | Trace |
|---|---|---|
| 2026-04-25 | `github.com/minio/minio` **archivé** (lecture seule ; les tags `RELEASE.*` restent publics) | `CHANGELOG.md` §Unreleased, `scripts/construire_image_minio.sh` |
| 2026-09-11 | `docker.io/minio/minio` **retiré** de Docker Hub | commentaire `docker-compose.yml` |
| 2026-09-24 | `quay.io/minio/minio` → **« Repository not found »** (dépôt supprimé ; `redis`/`nginx` se tiraient en parallèle → pas un rate-limit) | `CHANGELOG.md`, jobs CI `integration`/`sauvegarde` rouges ce jour-là |
| 2026-09-24 | `dl.min.io` → **410 Gone** : « The open-source MinIO Server, MinIO Client (mc) and MinIO KES projects are archived and no longer maintained » | `CHANGELOG.md`, `scripts/construire_image_minio.sh` |
| 2026-09-24 | Contournement livré : reconstruction de l'image depuis les sources archivées du **même tag** | `scripts/construire_image_minio.sh`, 7 tests, 2 étapes CI |

### 4.2 Corroboration externe

La chronologie publique concorde : console d'administration retirée de l'édition
communautaire (mai 2025), arrêt de publication des images et binaires officiels
(octobre 2025), passage en « maintenance mode » (3 décembre 2025), README
« THIS REPOSITORY IS NO LONGER MAINTAINED » (12 février 2026), archivage formel du
dépôt (25 avril 2026) — l'édition communautaire reste AGPLv3 mais n'est plus ni
maintenue ni distribuée, les utilisateurs étant orientés vers le produit commercial
AIStor [1](https://www.cloudhim.com/cloud-infrastructure/minio-community-edition-archived-what-to-do)[2](https://stormdevelopments.ca/blog/minio-s-community-edition-is-archived-what-still-runs-in-2026/)[3](https://blog.elest.io/self-hosted-weekly-week-9-2026-minio-is-dead-open-source-gets-a-00m-endowment-and-ai-slop-hits-maintainers/).

**Ce que cela change pour SEAMTECH, en clair** : une installation MinIO existante
continue de fonctionner — c'est précisément le piège. Ce qui a disparu, c'est le
canal de correctifs : la prochaine CVE de ce code est à trouver, corriger, compiler
et déployer par nous [2](https://stormdevelopments.ca/blog/minio-s-community-edition-is-archived-what-still-runs-in-2026/).
Aujourd'hui le dépôt assume déjà cette charge (compilation depuis les sources
archivées) **sans l'avoir décidée** : c'était un correctif d'urgence de CI, pas un
choix d'architecture.

---

## 5. Risques connus

Sévérité : 🔴 bloquant avant production · 🟠 à traiter · 🟡 à surveiller.

| Réf | Risque | Sévérité | Constat / preuve |
|---|---|---|---|
| **D-1** | ~~`api.py` appelait `get_presigned_url(object_key, expires_in=900)` alors que le paramètre s'appelle `expiration_seconds` → `TypeError` avalé : la redirection 302 n'était **jamais** servie~~ → **CORRIGÉ le 25/09/2026** (`expiration_seconds=900` aux deux appels, durée inchangée) ; 302 + `Location` + `ExpiresIn=900` prouvés de bout en bout | ✅ clos | §6 ; `tests/test_url_presignee_302.py` (12 tests) |
| R-1 | MinIO archivé : plus d'image officielle, plus de correctif de sécurité ; l'image est recompilée depuis des sources figées (dépend de GitHub + d'une chaîne Go) | 🔴 | §4 |
| R-2 | Une configuration S3 **incomplète** (endpoint sans identifiants) est considérée « configurée » : pas d'échec au démarrage, échec au premier envoi → import en quarantaine | 🟠 | `test_configuration_stockage_objet_incomplete_est_consideree_configuree` |
| R-3 | `storage_backend` n'est lu **nulle part** pour choisir un backend : `SEAMTECH_STORAGE_BACKEND=local` n'empêche aucun envoi S3 (simple libellé de `/health`) | 🟠 | `test_storage_backend_local_ne_desactive_pas_le_client_objet` |
| R-4 | Asymétrie du préfixe : `upload_*`/`object_exists`/`list_keys` appliquent `s3_prefix`, `get_presigned_url`/`download_file`/`delete_file` **non** | 🟠 | `test_url_presignee_n_applique_pas_le_prefixe_contrairement_a_l_envoi` |
| R-5 | Normalisation Unicode : `été.pdf` en NFC et en NFD produisent **deux clés différentes** (l'empreinte porte sur les octets du chemin relatif) → doublons silencieux si l'archive transite par macOS | 🟠 | `test_normalisation_unicode_differente_produit_des_cles_differentes` |
| R-6 | `ensure_bucket_exists` exige `HeadBucket`+`CreateBucket`, et tente `PutBucketVersioning`, **à chaque envoi** : un IAM de moindre privilège (AWS) refusera, avec un simple `warning` — le versioning ne serait alors jamais activé | 🟠 | `storage.py` l.252-296 (échec capté par `except Exception` → `logger.warning`) |
| R-7 | Les identifiants S3 sont les identifiants **root** de MinIO (`MINIO_ROOT_USER/PASSWORD`) : pas de compte applicatif restreint, pas de rotation documentée | 🟠 | `docker-compose.yml`, `.env.example` |
| R-8 | Endpoint interne en clair (`http://minio:9000`) et aucun chiffrement au repos demandé (pas de `ServerSideEncryption`) | 🟡 | compose, `storage.py` (`ExtraArgs` = `ContentType` seul) |
| R-9 | `first_free_key` n'est pas atomique : deux workers concurrents peuvent choisir la même clé libre (S3 n'a pas de « écrire si absent »). Coût : jusqu'à 51 `HeadObject` par envoi sur un endpoint distant | 🟡 | `storage.py` l.371-392 ; aujourd'hui un seul thread worker → risque théorique, réel dès qu'un second conteneur est ajouté |
| R-10 | La vérification après envoi des **documents** est une preuve d'**existence** (`HeadObject`), pas d'**intégrité** (pas de comparaison de somme). Seule la **sauvegarde** relit et compare le sha256 | 🟡 | `storage.py` `object_exists` vs `sauvegarde.sauver` |
| R-11 | Aucune politique de cycle de vie côté bucket : la rétention n'est applicative que pour les sauvegardes ; les objets d'imports ne sont jamais purgés | 🟡 | `sauvegarde.appliquer_retention`, `retention.py` |
| R-12 | Une URL présignée porte `X-Amz-Credential` (l'identifiant d'accès) et vaut 900 s : elle ne doit jamais être journalisée ni tracée | 🟡 | vérifié : jamais journalisée (`test_url_presignee_n_est_jamais_journalisee`) |
| R-13 | **Aucune exécution contre un second fournisseur S3** : la compatibilité R2/AWS est documentée, jamais prouvée par exécution (le seul test réel est `s3` et il est sauté) | 🟠 | §3.1 |
| R-14 | ~~La CI écartait les tests par **mot-clé** (`pytest -k "not postgres and not s3"`) : tout test dont le NOM contenait `s3` ou `postgres` était désélectionné, y compris 33 tests n'exigeant aucun service — ils ne s'exécutaient **nulle part**~~ → **CORRIGÉ le 25/09/2026** : sélection par **marqueur** (`-m "not postgres and not s3 and not perf"`, couverture `-m "not s3 and not perf"`), marqueur `s3` posé sur les 4 tests qui exigent un endpoint vivant | ✅ clos | §5.2 ; garde-fou `tests/test_selection_ci.py` (13 tests) |

### 5.1 Ce qui protège déjà (à ne pas casser)

1. **Clés sans collision** (`import_id` + sha256 du chemin relatif).
2. **Jamais d'écrasement** : `first_free_key` + versioning demandé au mieux.
3. **Relecture avant purge** : `all_verified` conditionne toute suppression locale.
4. **Quarantaine** : un envoi incomplet est conservé, jamais balayé par la rétention.
5. **Sauvegarde relue** : dump re-téléchargé et comparé au sha256 du manifeste avant
   d'être déclaré valide ; restauration prouvée par destruction réelle en CI.
6. **Aucun secret journalisé** : vérifié sur les chemins d'envoi, d'erreur et de
   présignature (§7, tests 2/6).

### 5.2 R-14 — la CI sélectionnait par nom : corrigé par les marqueurs

Le défaut : `pytest -k "not postgres and not s3"` exclut par **sous-chaîne du nom**.
Un test s'appelant `test_chaos_s3_down_mid_import` était donc écarté alors qu'il
n'utilise que des doubles. Mesure du 25/09 avant correctif : `pytest -k "s3 and not
postgres and not perf"` → **10 passed, 1 skipped** de tests existants exécutés
**nulle part** ; et au total 33 tests étaient dans ce cas.

Le correctif, en trois gestes :

1. **marquer ce qui exige un service** — `@pytest.mark.s3` posé sur les seuls tests
   qui parlent à un endpoint vivant : `test_integration_docker.py` (2, compose
   complet), `test_sauvegarde_restauration.py::test_aller_retour_via_client_s3_reel`,
   `test_storage.py::test_live_minio_s3_integration`. **Aucun test sur doubles n'a été
   marqué** : le marquer reviendrait à le cacher, c'est-à-dire à refaire le défaut ;
2. **sélectionner par catégorie** — `pytest -m "not postgres and not s3 and not perf"`
   (suite sans service) et `pytest -m "not s3 and not perf"` (couverture) ; les jobs
   `integration` et `sauvegarde` continuent de sélectionner **par chemin**, donc leurs
   tests marqués `s3` y tournent toujours ;
3. **verrouiller** — `tests/test_selection_ci.py` (13 tests) : inventaire des tests à
   service réel, interdiction du motif `-k "not <catégorie>"` dans le workflow,
   contrôle que les 10 tests autrefois cachés sont bien sélectionnés, et preuve que la
   nouvelle sélection **n'enlève rien** à l'ancienne.

Effet mesuré sur la collecte (même arbre, deux commandes) :

| Sélection | Avant (`-k`) | Après (`-m`) |
|---|---|---|
| suite sans service | 680 tests | **718 tests** |
| mesure de couverture | 901 tests | **916 tests** |
| tests marqués `s3` | 1 | **4** |

Contrôle en CI réelle (là où PostgreSQL existe), annotations émises par le workflow
lui-même : la couverture globale passe de **86,49 %** (ancienne commande `-k`,
run `36171596634`) à **86,52 %** (nouvelle commande `-m`, run `36174286450`), `api.py`
de **87,9 %** à **88,2 %**, et la sélection PostgreSQL reste inchangée
(`passed=192 skipped=0`). Élargir la sélection n'a donc **rien coûté** : elle a rendu du
code couvert et n'a retiré aucun test à aucun job.

---

## 6. Écart D-1 — constaté, puis CORRIGÉ (25/09/2026)

### 6.1 Le défaut, tel qu'il a été mesuré

```
$ PYTHONPATH=. python -c "import inspect; from seamtech_search.storage import S3StorageClient; \
    print(inspect.signature(S3StorageClient.get_presigned_url))"
(self, remote_key: 'str', expiration_seconds: 'int' = 3600) -> 'str'

$ grep -n "get_presigned_url" seamtech_search/api.py
477:                url = storage_client.get_presigned_url(object_key, expires_in=900)
988:                url = storage_client.get_presigned_url(object_key, expires_in=900)
```

Exécution de la route `/imports/{id}/artifacts/report_pdf` (vrai `S3StorageClient`,
boto3 doublé) **avant** correctif :

```
seamtech_search.api WARNING Failed to generate presigned URL for IMP-1/abc/technical-report.pdf:
    S3StorageClient.get_presigned_url() got an unexpected keyword argument 'expires_in'
STATUT MESURE : 200 | Location: None
generate_presigned_url appelé côté boto3 : False
```

Le `TypeError` était avalé par le `except Exception` du repli : aucune redirection,
aucune erreur visible, et **aucun test rouge** — les tests d'alors remplaçaient le
client par un `MagicMock`, qui accepte n'importe quel mot-clé.

### 6.2 Le correctif appliqué

```diff
-                url = storage_client.get_presigned_url(object_key, expires_in=900)
+                url = storage_client.get_presigned_url(object_key, expiration_seconds=900)
```

Aux **deux** occurrences (`api.py` l.477 `/open` et l.988 artefacts) — les seules du
paquet, contrôlé par balayage : `test_tous_les_appels_de_get_presigned_url_utilisent_le_mot_cle_reel`.
Inchangés : la durée de **900 s**, les noms de buckets, les `object_key`, le contrat
d'API, les replis (fichier local → téléchargement S3 → 404).

### 6.3 La même mesure, après correctif

```
$ PYTHONPATH=. python /tmp/mesure_d1_apres.py
httpx INFO HTTP Request: GET http://testserver/imports/IMP-1/artifacts/report_pdf "HTTP/1.1 302 Found"
STATUT MESURE : 302 | Location: http://minio.invalide:9000/seamtech-documents/IMP-1/abc/technical-report.pdf?X-Amz-Expires=900&X-Amz-Signature=deadbeef
generate_presigned_url appelé côté boto3 : True
ExpiresIn transmis : 900 | Params : {'Bucket': 'seamtech-documents', 'Key': 'IMP-1/abc/technical-report.pdf'}
```

### 6.4 Ce qui empêche la rechute

`tests/test_url_presignee_302.py` — **12 tests comportementaux**, vrai client,
boto3 doublé, aucun réseau :

| Cas | Ce qui est vérifié | Routes |
|---|---|---|
| objet présent, présignature disponible | **302**, en-tête `Location` = URL présignée, `ExpiresIn=900` transmis à boto3, `Params` = {Bucket, Key} | `/open` **et** artefacts |
| présignature indisponible (`AccessDenied`) | repli existant (200, fichier servi), aucune exception non contrôlée, échec journalisé, **aucun identifiant** dans le corps, les en-têtes ni les journaux | les deux |
| objet absent | 404 franc, **jamais de faux 302**, boto3 jamais appelé | les deux |
| traçabilité | l'audit enregistre bien un événement `artifact_download` en `302` | artefacts |
| contrat d'appel | les 2 appelants du paquet n'utilisent que des mots-clés existants ; un `expires_in=` réintroduit lève bien un `TypeError` | balayage du paquet |

La documentation redevient donc exacte : README (« 302s to presigned URL, 900s
expiry ») et `docs/VERIFICATION.md` décrivent ce que le code fait.

---

## 7. Stratégie de test

### 7.1 Trois niveaux

| Niveau | Où | Réseau | Ce qui est prouvé |
|---|---|---|---|
| **Unitaire (doubles)** | `tests/test_storage.py`, `test_storage_coverage.py`, `test_object_keys.py`, `test_stockage_release_candidate.py` | non | le contrat d'appel S3 : clés, préfixe, anti-écrasement, relecture, repli, absence de secret dans les journaux |
| **Conformité fournisseur** | `tests/test_storage.py::test_live_minio_s3_integration` (marqueur `s3`, `SEAMTECH_TEST_S3_URL`) | oui | qu'un endpoint réel honore envoi / relecture / téléchargement / présignature — **toujours pas exécuté en CI** (aucun job ne fournit `SEAMTECH_TEST_S3_URL`) : R-13 reste ouvert, mais le test est désormais *déclaré* `s3` au lieu d'être écarté par son nom |
| **Intégration** | job CI `integration` (compose complet) + job `sauvegarde` (aller-retour destructif) | oui | que la pile démarre et que la sauvegarde hors-site se restaure depuis le bucket seul |

### 7.2 Tests ajoutés par ce lot (aucun test supprimé)

Trois fichiers, **45 tests**, aucun réseau, aucun conteneur, aucun vrai secret :
`tests/test_stockage_release_candidate.py` (20, garde-fous du stockage),
`tests/test_url_presignee_302.py` (12, comportement des deux routes après le
correctif D-1, détail en §6.4) et `tests/test_selection_ci.py` (13, sélection CI,
§5.2).

| Thème demandé | Tests |
|---|---|
| configuration S3 complète | `test_configuration_stockage_objet_complete_depuis_les_variables_d_environnement`, `test_configuration_stockage_objet_incomplete_est_consideree_configuree`, `test_storage_backend_local_ne_desactive_pas_le_client_objet` |
| absence de secrets dans les logs | `test_envoi_reussi_ne_journalise_aucun_secret`, `test_url_presignee_n_est_jamais_journalisee` |
| erreurs S3 sans fuite d'identifiants | `test_erreurs_stockage_objet_ne_fuient_pas_les_identifiants` (4 chemins d'erreur + message `StorageError`) |
| URL présignée | `test_url_presignee_transmet_bucket_cle_et_expiration`, `test_signature_publique_de_l_url_presignee_est_le_contrat_des_appelants`, `test_d1_corrige_les_appels_de_l_api_utilisent_le_vrai_nom_de_parametre` (ex-test d'écart, **inversé** après correctif), `test_url_presignee_n_applique_pas_le_prefixe_contrairement_a_l_envoi`, `test_route_artefact_ne_tombe_jamais_en_500_ni_ne_fuit_les_identifiants` + les 12 tests de `test_url_presignee_302.py` |
| `object_key` espaces et accents | `test_cle_objet_conserve_espaces_et_accents`, `test_cle_avec_espaces_et_accents_transmise_telle_quelle_a_boto3`, `test_normalisation_unicode_differente_produit_des_cles_differentes` |
| repli local documenté | `test_sans_stockage_objet_configure_l_envoi_renvoie_not_configured`, `test_echec_partiel_interdit_la_purge_locale` |
| démarrage base vide | `test_demarrage_avec_une_base_vide` |
| restauration après redémarrage | `test_references_objets_survivent_au_redemarrage` |
| migration séquentielle jusqu'à 017 | `test_migrations_sequentielles_001_a_017_sur_base_vide`, `test_version_schema_metier_est_la_derniere_migration_declaree` |

### 7.3 Épreuve à exiger AVANT de changer de backend (non exécutée ici)

Suite de conformité à faire tourner contre l'endpoint candidat, en manuel puis en job
CI dédié (`SEAMTECH_TEST_S3_URL=<endpoint candidat> pytest -m s3`) :

1. `CreateBucket` + `HeadBucket` (ou bucket pré-créé si l'IAM l'interdit) ;
2. `PutObject` < 8 Mio **et** > 8 Mio (bascule multipart de boto3) ;
3. `HeadObject` sur clé présente / absente (codes `404`/`NoSuchKey`/`NotFound`) ;
4. anti-écrasement : deux envois sur la même clé → `-2` ;
5. clé avec **espaces, accents, `°`, parenthèses** (nom réel d'atelier) ;
6. `GetObject` → octets identiques (comparaison sha256) ;
7. URL présignée `GET` 900 s : téléchargeable, puis **expirée** après le délai ;
8. `DeleteObject` puis `HeadObject` → absent ;
9. `ListObjectsV2` avec > 1 000 objets (pagination réelle) ;
10. `PutBucketVersioning` / `GetBucketVersioning` : succès **ou** erreur typée
    (`NotImplemented`/`MethodNotAllowed`/501) — pas de pendaison ;
11. aller-retour complet de sauvegarde (`sauver` → `DROP DATABASE` → `restaurer` →
    `verifier`) contre le bucket candidat.

Critère de bascule : 1-9 et 11 verts ; 10 vert **ou** absence de versioning acceptée
par écrit (la protection repose alors uniquement sur `first_free_key`).

---

## 8. Critères de choix d'une alternative (matière à décision, pas une recommandation)

### 8.1 Grille de critères, pondérée par l'usage réel

| Critère | Pourquoi il compte ici | Exigence |
|---|---|---|
| Opérations §2 (12) supportées | c'est tout ce que le code appelle | **obligatoire** (sauf 3 & 4) |
| SigV4 + présignature `GET` | téléchargement navigateur sans exposer le bucket | **obligatoire** |
| Path-style **ou** hôte virtuel | réglable par `SEAMTECH_S3_FORCE_PATH_STYLE` | obligatoire (l'un des deux) |
| Versioning de bucket | 2ᵉ filet anti-écrasement (le 1ᵉʳ est `first_free_key`) | souhaitable |
| Souveraineté / confidentialité | l'archive contient des **documents clients** (RG13) | **décision commanditaire** |
| Maintenance amont vivante | c'est exactement ce qui manque à MinIO | **obligatoire** |
| Exploitation mono-poste atelier | pas d'équipe infra ; un poste Windows + Docker Desktop | fort |
| Coût | volume inconnu tant que l'archive n'est pas livrée | à chiffrer après inventaire |
| Réversibilité | pouvoir revenir en arrière sans réécrire les clés | **obligatoire** |

### 8.2 Options, avec ce qui est établi et ce qui ne l'est pas

| Option | Versioning | Maintenance | Données hors atelier | Points d'attention |
|---|---|---|---|---|
| **MinIO figé (statu quo)** | oui | ❌ archivée, correctifs à notre charge | non | R-1 ; dépend de `construire_image_minio.sh` et de la disponibilité des sources GitHub |
| **Garage** (Deuxfleurs, AGPLv3) | ❌ non (`GetBucketVersioning` = stub, `PutBucketVersioning` absent) ; présignature ✅, path-style ✅ [4](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/) | ✅ active | non | le code gère déjà l'absence de versioning (cas R2) ; très léger (≈128 Mo de RAM) [5](https://www.pistack.xyz/posts/seaweedfs-vs-minio-vs-garage/) |
| **SeaweedFS** (Apache 2.0) | ❌ pas de versioning de bucket selon les comparatifs 2026 [6](https://www.sitepoint.com/local-s3-storage-without-minio-seaweedfs-garage-docker-compose/) | ✅ active | non | passerelle S3 via le *filer* ; plus de pièces à exploiter |
| **Ceph RadosGW** | ✅ | ✅ active | non | 3 nœuds recommandés — hors gabarit d'un atelier [7](https://www.pistack.xyz/posts/2026-05-14-self-hosted-s3-object-storage-garage-vs-seaweedfs-vs-ceph-rgw-guide/) |
| **AWS S3** | ✅ | ✅ | **oui** | mettre `FORCE_PATH_STYLE=false`, IAM de moindre privilège (R-6), coût à l'usage |
| **Cloudflare R2** | ❌ (`PutBucketVersioning` non implémenté — déjà géré dans le code) | ✅ | **oui** | `region=auto`, pas de frais de sortie |
| **Fournisseur européen** (Scaleway, OVH…) | selon l'offre | ✅ | **oui, mais en UE** | à qualifier avec la suite §7.3 |

> Toute option « données hors atelier » suppose une décision explicite du
> commanditaire sur la confidentialité des documents clients (RG13 : l'archive est la
> source de vérité et ne se versionne pas ; elle ne se téléverse pas davantage sans
> accord écrit).

---

## 9. Plan de migration sans perte (à exécuter le jour où la décision est prise)

Principe directeur : **les clés d'objets ne changent pas**. La base contient
`object_key` + `object_bucket` ; si les clés sont copiées à l'identique, aucune
écriture en base n'est nécessaire et le retour arrière est une variable
d'environnement.

| # | Étape | Commande / contrôle | Critère d'arrêt |
|---|---|---|---|
| 1 | Geler les imports | arrêter `web` (`docker compose stop web`) ; vérifier `import_jobs` sans travail en cours | 0 job actif |
| 2 | Sauvegarde complète + vérifiée | `python -m seamtech_search.sauvegarde sauver --base-url … --archive … --dossier data/backups` | manifeste écrit, `verifiee_apres_envoi: true` |
| 3 | Inventaire de l'existant | `SELECT count(*), count(object_key), sum(size) FROM documents;` + `list_keys("")` côté bucket | les deux comptes concordent (écart = liste à expliquer) |
| 4 | Préparer la cible | créer le bucket (même nom : `seamtech-documents`), activer le versioning **si disponible**, créer un compte applicatif restreint (R-7) | `HeadBucket` OK avec les nouvelles clés |
| 5 | Copier | `rclone sync source:seamtech-documents cible:seamtech-documents --checksum` (ou `aws s3 sync`) | 0 erreur ; les clés sont **identiques** |
| 6 | Réconcilier | pour chaque `object_key` de `documents` et chaque clé de sauvegarde : `HeadObject` sur la cible + comparaison de taille (et sha256 pour un échantillon ≥ 100 objets) | **0 objet manquant**, 0 écart de taille |
| 7 | Bascule | changer `SEAMTECH_S3_ENDPOINT_URL` / `ACCESS_KEY` / `SECRET_KEY` / `REGION` / `FORCE_PATH_STYLE` ; `docker compose up -d web frontend` | `/health` : `s3_configured: true`, `versioning_*` conforme à l'attendu |
| 8 | Épreuve fonctionnelle | import de test → recherche → téléchargement d'artefact → `retry-upload` → sauvegarde + **restauration dans une base neuve** | tous verts (§7.3, points 1-11) |
| 9 | Période de double conservation | garder l'ancien bucket **en lecture seule** ≥ 30 jours ; aucune suppression | décision écrite de suppression |
| 10 | Retour arrière | remettre les 5 variables précédentes et redémarrer | `/health` vert, téléchargements OK |

**Interdits pendant la migration** : renommer un bucket, changer `s3_prefix`, modifier
`artifact_object_key`, purger l'ancien bucket avant l'étape 9, écrire dans l'archive
source (RG13).

---

## 10. Décisions qui reviennent au commanditaire

| Réf | Décision | Options | Conséquence de ne pas trancher |
|---|---|---|---|
| ~~**D-1**~~ | ~~Corriger l'appel d'URL présignée ?~~ **TRANCHÉ le 25/09/2026 : option (a)** — correctif appliqué, 302 rétabli, durée 900 s inchangée, 12 tests comportementaux (§6) | — | — |
| **D-2** | Backend de stockage cible | statu quo MinIO figé · Garage · SeaweedFS · Ceph · AWS S3 · R2 · fournisseur UE | on hérite indéfiniment d'une chaîne de compilation MinIO non décidée (R-1) |
| **D-3** | Les documents clients peuvent-ils quitter l'atelier ? | oui (cloud) / non (auto-hébergé) | bloque D-2 |
| **D-4** | Compte applicatif restreint + rotation des secrets | oui / non | les identifiants root MinIO restent les identifiants de l'application (R-7) |
| **D-5** | Le versioning de bucket est-il exigé ? | exigé (exclut Garage/R2) / non exigé (protection par `first_free_key` seule) | critère de choix D-2 non arbitrable |
| **D-6** | Normalisation Unicode des chemins avant l'import de l'archive réelle | NFC imposé à l'inventaire / aucune normalisation assumée | risque de doublons silencieux au Lot G (R-5) |
| **D-7** | TLS vers l'endpoint + chiffrement au repos | exigés / non exigés | R-8 non traité |
| **D-8** | Faire échouer le démarrage si la configuration S3 est incomplète (R-2) et rendre `storage_backend` réellement opérant (R-3) | oui / non | deux réglages trompeurs subsistent en production |
| ~~**D-9**~~ | ~~Remplacer le filtre CI `-k "not s3"` par le marqueur `-m "not s3"` ?~~ **TRANCHÉ le 25/09/2026 : oui** — sélection par marqueur, 4 tests marqués `s3`, garde-fou `tests/test_selection_ci.py` (§5.2) | — | — |

---

## 11. Conclusion de l'audit

**VALIDÉ AVEC RÉSERVES.**

- Le code de stockage est **portable** : un seul module parle à boto3, 12 opérations
  S3 standard, aucune particularité MinIO dans l'applicatif. Une alternative est
  techniquement à portée, sans changer de clés ni de contrats.
- Les garanties anti-perte (clé sans collision, non-écrasement, relecture avant purge,
  quarantaine, sauvegarde relue et restaurée en CI) sont **réelles et testées**.
- **Corrigés depuis la première rédaction de cet audit** (25/09/2026, même journée) :
  **D-1** — la redirection 302 présignée est rétablie et prouvée (§6) ; **R-14** — la
  CI sélectionne par marqueur et 33 tests jusque-là exécutés nulle part le sont de
  nouveau (§5.2).
- Réserves qui subsistent avant mise en production : **R-1** (MinIO sans amont),
  **R-2/R-3** (réglages trompeurs), **R-13** (compatibilité jamais prouvée contre un
  second fournisseur S3), et les décisions **D-2 à D-8**.
- Rappel : **Lot G réel bloqué** (pas d'archive), **F-2 bloqué** (pas de fiches
  validées), **métriques de production indisponibles**, **fixtures du dépôt non
  représentatives** de l'archive réelle. Aucun dimensionnement de bucket, aucun coût
  et aucune durée de migration ne peuvent être chiffrés avant l'inventaire de
  l'archive réelle.
