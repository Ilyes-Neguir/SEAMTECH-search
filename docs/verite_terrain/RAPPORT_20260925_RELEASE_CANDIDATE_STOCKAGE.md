# Rapport — Préparation release candidate + audit stockage S3 / MinIO

**Date** : 2026-09-25 · **Branche** : `arena/01a0d9a7-seamtech-search`
**Base** : `origin/main` = `f8befb5da800c10d793e25addf9519cc63f5fd6c`
**Périmètre** : audit en lecture seule + documentation + tests. **Aucun code applicatif
modifié**, aucun backend de stockage changé, aucun test existant supprimé, aucun seuil de
couverture abaissé, aucun `pragma: no cover` ajouté, aucun vrai secret, aucun PDF réel,
aucun appel à un service distant, aucun téléchargement.

> **Ce qui reste bloqué (rappel imposé, non contournable)**
> - **Lot G réel bloqué** : l'archive de production n'est pas livrée → aucun traitement
>   de masse, aucune volumétrie, aucune durée réelle d'import ou de migration.
> - **F-2 bloqué** : les 300 à 500 fiches validées manquent → calibration verrouillée.
> - **Métriques de production indisponibles** : les seuls chiffres réels cités viennent
>   de la CI et de cette machine de développement.
> - **Fixtures du dépôt non représentatives** : `sample_data/` = 1 fiche client réelle +
>   quelques fichiers synthétiques ; ni l'échelle, ni la variété de noms, ni les scans de
>   l'archive réelle.

---

## 1. État post-fusion vérifié (point 1)

| Contrôle | Commande | Résultat réel |
|---|---|---|
| Branche de travail | `git rev-parse --abbrev-ref HEAD` | `arena/01a0d9a7-seamtech-search` |
| Base = `main` | `git rev-parse HEAD` / `git rev-parse origin/main` | identiques : `f8befb5da800c10d793e25addf9519cc63f5fd6c` |
| CI post-fusion | `gh api …/actions/runs/36168258103/jobs` | **9/9 verts** : `backend (3.11/3.12/3.13)`, `frontend`, `docker`, `e2e`, `integration`, `ocr`, `sauvegarde` — `head_sha` = `f8befb5` |
| Dépôt propre | `git status --porcelain` | seulement les 4 fichiers ajoutés par ce lot |
| Migration 017 | `grep -rn "017_ocr_etage3" seamtech_search/…` | `indexer.py` l.822/867, `schema_metier.py` l.29 (`VERSION_SCHEMA_METIER`) |
| Protections Lot M | `ls tests/test_ocr_*.py tests/test_migrations_metier.py` | `test_ocr_comportement.py`, `test_ocr_etages.py`, `test_migrations_metier.py` présents |
| Audit projet | `PYTHONPATH=. python scripts/audit_projet.py --rapide` | **BILAN : 12/12 contrôles verts** |

Restauration de sauvegarde mesurée par la CI du même commit (annotation du job
`sauvegarde`, run `36168258103`) :
`restauration de 50000 fiches en 1.33 s (dump 579312 octets)` — chiffre **CI**, pas une
mesure de production.

## 2. Fichiers du lot (4 ajoutés, 1 modifié, 0 supprimé)

| Fichier | Nature | Contenu |
|---|---|---|
| `tests/test_stockage_release_candidate.py` | test (nouveau) | 20 tests, aucun réseau, aucun vrai secret |
| `docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md` | doc (nouveau) | audit complet du stockage objet (11 sections) |
| `docs/RELEASE_CANDIDATE_CHECKLIST.md` | doc (nouveau) | checklist de mise en service, 18 rubriques |
| `docs/verite_terrain/RAPPORT_20260925_RELEASE_CANDIDATE_STOCKAGE.md` | doc (nouveau) | le présent rapport |
| `CHANGELOG.md` | doc (modifié) | entrée `Unreleased` du lot, en tête — **seul fichier préexistant touché** |

`git diff` sur les sources : **vide** (seul `CHANGELOG.md` est modifié). `seamtech_search/`, `frontend/`,
`docker-compose.yml`, `.github/workflows/ci.yml`, `scripts/coverage_gate.py` :
inchangés.

## 3. Audit stockage (points 2 et 3)

Document : **`docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md`** (architecture, 12
opérations S3 avec numéros de ligne, compatibilités AWS attendues, dépendances MinIO
M-1→M-8, tests supposant MinIO, variables d'environnement, replis mesurés, statut du
projet MinIO archivé, risques D-1/R-1→R-14, preuve mesurée de D-1, stratégie de test à
3 niveaux + suite de conformité en 11 points, grille de choix d'alternative, plan de
migration en 10 étapes, décisions D-1→D-9).

Trois constats structurants :

1. **Le code est portable.** Un seul module (`seamtech_search/storage.py`) parle à boto3,
   via 12 opérations S3 standard (`HeadBucket`, `CreateBucket`, `Put/GetBucketVersioning`,
   `HeadObject` ×2, `upload_file`, `put_object`, `download_file`,
   `generate_presigned_url`, `DeleteObject`, `ListObjectsV2` paginé). Aucune particularité
   MinIO dans l'applicatif ; le seul réglage à basculer serait
   `SEAMTECH_S3_FORCE_PATH_STYLE`. Object lock, lifecycle, réplication, ACL, tagging, SSE
   explicite, `CopyObject` : **jamais appelés** — donc pas à exiger d'une alternative.
2. **La dépendance MinIO est dans l'infrastructure et la CI, pas dans le code** : service
   compose épinglé, healthcheck `mc ready local`, sonde `/minio/health/live`, ports
   9000/9001, `MINIO_ROOT_*` réutilisés comme clés S3, `scripts/construire_image_minio.sh`.
   Sans MinIO, ce sont des tests d'infrastructure qui rougissent
   (`test_construire_image_minio.py`, `test_compose_hardening.py`,
   `test_compose_web_boot.py`, `test_integration_docker.py`, job CI `sauvegarde`), pas la
   logique métier.
3. **MinIO n'a plus d'amont.** Édition communautaire en « maintenance mode » (déc. 2025),
   README « no longer maintained » (févr. 2026), dépôt **archivé le 25 avril 2026**,
   images retirées de Docker Hub puis de quay.io, `dl.min.io` en 410 Gone. Le dépôt
   recompile déjà l'image depuis les sources archivées — correctif d'urgence de CI, jamais
   décidé comme architecture. Une installation existante continue de tourner : c'est
   exactement le piège, il n'y a plus de canal de correctifs de sécurité.

### 3.1 Défaut confirmé par exécution — D-1

`api.py` (l.477 `/open`, l.988 artefacts) appelle
`get_presigned_url(object_key, expires_in=900)` alors que la signature réelle est
`get_presigned_url(self, remote_key, expiration_seconds=3600)`. Le `TypeError` est avalé
par un `except Exception` : **la redirection 302 vers une URL présignée n'est jamais
servie**. Sortie brute obtenue en exécutant la route avec le vrai `S3StorageClient` et un
boto3 doublé :

```
seamtech_search.api WARNING Failed to generate presigned URL for IMP-1/abc/technical-report.pdf:
    S3StorageClient.get_presigned_url() got an unexpected keyword argument 'expires_in'
STATUT MESURE : 200 | Location: None
generate_presigned_url appelé côté boto3 : False
```

Ce n'est ni une perte de données ni une panne (le repli documenté sert le fichier), mais
c'est une **divergence documentation/réalité** (README et `docs/API.md` annoncent
« 302s to presigned URL, 900s expiry ») et un surcoût de bande passante. Correctif de
deux mots-clés **volontairement non appliqué** ici : il change un code de statut
observable → décision commanditaire D-1.

### 3.2 Défaut de dispositif de test découvert en cours de route — R-14

La CI désélectionne **par sous-chaîne du nom** (`pytest -k "not postgres and not s3"`,
`.github/workflows/ci.yml` l.91 ; idem l.479 pour la couverture). Mesure du jour :

```
$ pytest -q -k "s3 and not postgres and not perf"
10 passed, 1 skipped, 889 deselected
```

→ **10 tests existants** (chaos S3, configuration du client, `/open` avec `object_key`,
rétention symétrique…) passent en local et **ne sont exécutés nulle part en CI** ; seul
`test_live_minio_s3_integration` a réellement besoin d'un endpoint (marqueur `s3`).
Conséquence immédiate pour ce lot : les tests ajoutés ont été **renommés** pour ne pas
contenir `s3` (sinon 5 des 20 auraient été silencieusement ignorés en CI — vérifié :
`20 passed` désormais contre `15 passed, 5 deselected` avant renommage). Le correctif de
fond (passer à `-m "not s3"`) touche le périmètre de la CI et la couverture mesurée : il
est proposé comme décision **D-9**, non appliqué.

## 4. Checklist de release (point 4)

Document : **`docs/RELEASE_CANDIDATE_CHECKLIST.md`** — 18 rubriques, chacune avec
commande, sortie attendue, case à cocher et preuve à archiver : prérequis machine ·
variables obligatoires · génération et protection des secrets · lancement Docker ·
migrations · compte administrateur · healthchecks · import de test · recherche de test ·
validation · sauvegarde · restauration · arrêt-redémarrage · contrôle des logs ·
rollback · permissions · RG13/RG14 · décision de mise en service.

Elle **référence** `docs/verite_terrain/MISE_EN_SERVICE.md`,
`docs/verite_terrain/RUNBOOK_RESTAURATION.md`, `docs/verite_terrain/QUE_FAIRE_SI.md`,
`docs/CHECKLIST_POSTE_WINDOWS.md` et `docs/ARRIVEE_ARCHIVE.md` au lieu de les dupliquer,
et porte en tête les quatre blocages ci-dessus. Points signalés explicitement comme non
cochables aujourd'hui : chemin Windows (non exécuté depuis un environnement Linux),
chronomètre de validation humaine (0/3 fiches), échelle réelle, réentraînement.
Rappel intégré : le dépôt ne contient que `config/config.example.json` — aucun
`config.json` de production n'existe et il ne doit pas être versionné.

## 5. Tests ajoutés (point 5) — 20 tests, aucun supprimé

`tests/test_stockage_release_candidate.py`, 7 sections couvrant exactement la liste
demandée :

| Thème demandé | Tests |
|---|---|
| configuration S3 complète | `test_configuration_stockage_objet_complete_depuis_les_variables_d_environnement`, `…_incomplete_est_consideree_configuree` (R-2), `test_storage_backend_local_ne_desactive_pas_le_client_objet` (R-3) |
| absence de secrets dans les logs | `test_envoi_reussi_ne_journalise_aucun_secret`, `test_url_presignee_n_est_jamais_journalisee` |
| erreurs S3 sans fuite de credentials | `test_erreurs_stockage_objet_ne_fuient_pas_les_identifiants` (4 chemins `ClientError`) |
| URL présignée | `test_url_presignee_transmet_bucket_cle_et_expiration`, `test_signature_publique_de_l_url_presignee_est_le_contrat_des_appelants`, `test_ecart_d1_api_appelle_l_url_presignee_avec_un_mot_cle_inexistant`, `test_url_presignee_n_applique_pas_le_prefixe_contrairement_a_l_envoi` (R-4), `test_route_artefact_ne_tombe_jamais_en_500_ni_ne_fuit_les_identifiants` |
| `object_key` espaces et accents | `test_cle_objet_conserve_espaces_et_accents`, `test_cle_avec_espaces_et_accents_transmise_telle_quelle_a_boto3`, `test_normalisation_unicode_differente_produit_des_cles_differentes` (R-5) |
| fallback local documenté | `test_sans_stockage_objet_configure_l_envoi_renvoie_not_configured`, `test_echec_partiel_interdit_la_purge_locale` |
| démarrage base vide / restauration après redémarrage | `test_demarrage_avec_une_base_vide`, `test_references_objets_survivent_au_redemarrage` |
| migration séquentielle jusqu'à 017 | `test_migrations_sequentielles_001_a_017_sur_base_vide`, `test_version_schema_metier_est_la_derniere_migration_declaree` |

Deux précisions d'honnêteté :

- `test_ecart_d1_…` **documente le défaut D-1 tel qu'il est aujourd'hui** ; il est écrit
  pour devenir rouge le jour où le correctif est appliqué, et doit alors être remplacé par
  son inverse. C'est dit dans son docstring.
- Le test de migrations porte sur `MIGRATIONS_METIER` et sur l'application réelle des 17
  migrations sur une base **vide** (SQLite) : `tests/test_migrations_metier.py` est
  intégralement marqué `postgres` et ne peut pas s'exécuter sans serveur ici.

## 6. Vérifications locales (point 6) — commandes et sorties réelles

Interpréteur : `/home/user/.venv/bin/python` (venv **hors dépôt**, pytest 9.1.1, ruff,
boto3, pglast, PyYAML).

| # | Commande | Sortie réelle |
|---|---|---|
| 1 | `ruff check .` | `All checks passed!` |
| 2 | `git diff --check` | (vide, `rc=0`) |
| 3 | `PYTHONPATH=. python scripts/audit_projet.py --rapide` | `BILAN : 12/12 contrôles verts` |
| 4 | `pytest -q -k "not postgres and not s3 and not perf"` (avant ce lot, rappel) | `637 passed, 5 skipped` |
| 5 | `pytest -q -k "not postgres and not s3"` (après, **filtre exact de la CI**) | **`657 passed, 5 skipped, 238 deselected`** |
| 6 | `pytest -q tests/test_stockage_release_candidate.py -k "not postgres and not s3"` | **`20 passed`** (aucun désélectionné après renommage) |
| 7 | `pytest -q -k "s3 and not postgres and not perf"` | `10 passed, 1 skipped, 889 deselected` → preuve de R-14 |
| 8 | stockage : `pytest tests/test_storage.py tests/test_storage_coverage.py tests/test_object_keys.py tests/test_stockage_release_candidate.py -k "not s3"` | `32 passed, 11 deselected` |
| 9 | sauvegarde : `pytest tests/test_sauvegarde_unites.py tests/test_sauvegarde_restauration.py -k "not postgres"` | `16 passed, 7 deselected` |
| 10 | configuration : `pytest tests/test_config.py tests/test_compose_hardening.py tests/test_compose_web_boot.py tests/test_construire_image_minio.py tests/test_requirements_consistency.py` | `44 passed` |
| 11 | sécurité : `pytest tests/test_garde_fous_preparation.py tests/test_scan_safety.py tests/test_api_gate.py tests/test_comptes.py -k "not postgres"` | `33 passed, 18 deselected` |
| 12 | couverture (commande CI) : `pytest -k "not s3" -m "not perf" --cov=seamtech_search --cov-report=json:coverage.json` puis `python scripts/coverage_gate.py` | `708 passed, 174 skipped` ; **gate FAILED en local** : global 69.7 % (plancher 85 %), `indexer.py` 89.9 % (porte 90 %) |

**Sur le point 12, pas d'ambiguïté** : la porte échoue **sans PostgreSQL**, et elle
échouait déjà **à l'identique avant ce lot**. Mesure de comparaison faite ce jour, même
commande, en excluant seulement le fichier de tests ajouté :

```
global   : sans le nouveau fichier 69.6 %  ->  avec 69.7 %
seamtech_search/storage.py : 97.5 % -> 97.8 %   (porte 97 %)
seamtech_search/config.py  : 86.0 % -> 89.5 %
seamtech_search/indexer.py : 89.9 % -> 89.9 %   (inchangé)
```

Les 174 tests ignorés sont les tests métier PostgreSQL. Le juge reste la CI backend, qui
dispose de PostgreSQL et était verte sur ce commit. **Aucun seuil n'a été touché.**

### 6.1 Ce qui n'a PAS été exécuté, et pourquoi

| Non exécuté | Raison |
|---|---|
| Build frontend (`pnpm build`) | `frontend/node_modules` absent et `pnpm` non installé dans cet environnement ; **aucun fichier frontend n'est modifié** par ce lot → couvert par le job CI `frontend` (vert sur `f8befb5`) |
| Suite PostgreSQL (`-m postgres`) | pas de serveur PostgreSQL ici ; 174 tests s'auto-ignorent — couverts par le job CI `backend` |
| `pytest -m s3` (endpoint réel) | exige un endpoint S3 ; interdiction de service distant, et MinIO ne peut plus être tiré d'un registre |
| `scripts/construire_image_minio.sh`, `docker compose up` | build Docker + réseau requis ; hors périmètre autorisé |
| E2E Playwright | nécessite la pile complète démarrée |
| Tout ce qui touche l'archive réelle (Lot G, F-2) | archive et fiches validées non livrées |

## 7. Risques restants (détail : audit §5)

**Bloquants avant production** : **D-1** (redirection présignée jamais servie, mesurée) ·
**R-1** (MinIO archivé, plus de correctifs, image recompilée depuis des sources figées).

**À traiter** : R-2 (configuration S3 incomplète acceptée) · R-3 (`storage_backend`
cosmétique) · R-4 (asymétrie de préfixe entre envoi et présignature/téléchargement) ·
R-5 (NFC ≠ NFD → deux clés, risque de doublons le jour du Lot G) · R-6
(`CreateBucket`/`PutBucketVersioning` à chaque envoi, incompatible avec un IAM de moindre
privilège) · R-7 (identifiants S3 = root MinIO) · R-13 (compatibilité jamais prouvée hors
MinIO) · R-14 (10 tests existants désélectionnés en CI).

**À surveiller** : R-8 (endpoint en clair, pas de chiffrement au repos) · R-9
(`first_free_key` non atomique, jusqu'à 51 `HeadObject`) · R-10 (vérification =
existence, pas intégrité, sauf pour la sauvegarde) · R-11 (aucun lifecycle côté bucket) ·
R-12 (une URL présignée porte `X-Amz-Credential` — vérifié : jamais journalisée).

**Ce qui protège déjà et ne doit pas être cassé** : clés sans collision, refus
d'écrasement (`-2`…`-51`), relecture avant toute purge locale (`all_verified`),
quarantaine jamais balayée par la rétention, sauvegarde relue et comparée en sha256 puis
restaurée par destruction réelle en CI.

## 8. Décisions qui reviennent au commanditaire

| Réf | Décision |
|---|---|
| **D-1** | Corriger `expires_in` → `expiration_seconds` (rétablir le 302 documenté) **ou** garder le repli et corriger la documentation |
| **D-2** | Backend de stockage cible : statu quo MinIO figé · Garage · SeaweedFS · Ceph RGW · AWS S3 · Cloudflare R2 · fournisseur UE |
| **D-3** | Les documents clients peuvent-ils quitter l'atelier ? (bloque D-2) |
| **D-4** | Créer un compte S3 applicatif restreint + rotation des secrets (aujourd'hui : root MinIO) |
| **D-5** | Le versioning de bucket est-il exigé ? (exclut Garage et R2 s'il l'est) |
| **D-6** | Normalisation Unicode (NFC) imposée à l'inventaire de l'archive réelle ? |
| **D-7** | TLS vers l'endpoint et chiffrement au repos : exigés ou non ? |
| **D-8** | Faire échouer le démarrage si la configuration S3 est incomplète + rendre `storage_backend` opérant |
| **D-9** | Remplacer le filtre CI `-k "not s3"` par le marqueur `-m "not s3"` |

Aucune de ces décisions n'a été prise dans ce lot : aucune n'est purement technique
(elles engagent la confidentialité des documents clients, le coût, et le périmètre de la
CI).

---

## 9. Conclusion

## **VALIDÉ AVEC RÉSERVES**

Ce qui est établi par exécution : le dépôt est propre sur `f8befb5`, la CI du commit est
verte 9/9, l'audit projet est à 12/12, `ruff` et `git diff --check` sont propres, la
suite non-PostgreSQL passe à **657 tests verts / 5 ignorés** avec le filtre exact de la
CI, les 20 tests ajoutés passent et couvrent les 9 thèmes demandés, la couverture de
`storage.py` monte de 97,5 % à 97,8 % sans qu'aucun seuil ne bouge.

Réserves, par ordre d'importance :

1. **D-1** — la redirection vers une URL présignée n'est jamais servie (mesuré) : la
   documentation est fausse sur ce point, et tout le trafic des pièces transite par
   l'API. Correctif de deux mots-clés, mais il change un comportement observable →
   décision.
2. **R-1** — MinIO est archivé sans canal de correctifs ; le dépôt compile déjà l'image
   lui-même sans que ce soit un choix assumé. Décision de backend (D-2/D-3) à prendre
   avant la mise en production.
3. **R-13 / R-14** — la compatibilité S3 hors MinIO n'a jamais été prouvée par exécution,
   et 10 tests existants ne s'exécutent nulle part en CI à cause d'un filtre par mot-clé.
4. **Non exécutable ici** : build frontend (aucun fichier frontend touché, job CI vert),
   suite PostgreSQL, endpoint S3 réel, Docker.
5. **Rappel final** : **Lot G réel bloqué** (archive non livrée), **F-2 bloqué** (fiches
   validées manquantes), **métriques de production indisponibles**, **fixtures du dépôt
   non représentatives de l'archive réelle**. Aucun dimensionnement, aucun coût et aucune
   durée de migration ne peuvent être chiffrés avant l'inventaire de l'archive réelle.
