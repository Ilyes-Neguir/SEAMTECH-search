# Rapport — Correctifs D-1 et R-14, audit stockage S3/MinIO, checklist release candidate

**Date** : 2026-09-25 · **Branche** : `arena/01a0d9a7-seamtech-search`
**Base** : `origin/main` = `f8befb5da800c10d793e25addf9519cc63f5fd6c` (CI post-fusion 9/9,
run `36168258103`)

**Deux corrections obligatoires demandées par le commanditaire ont été faites** :
**D-1** (bug des URL présignées) et **R-14** (sélection des tests en CI). Le reste du
lot — audit du stockage objet, checklist de mise en service, tests de garde — est
inchangé sur le fond.

> **Ce qui reste bloqué (rappel, non contournable)**
> - **Lot G réel bloqué** : l'archive de production n'est pas livrée → aucun traitement
>   de masse, aucune volumétrie, aucune durée réelle.
> - **F-2 bloqué** : les 300 à 500 fiches validées manquent → calibration verrouillée.
> - **Métriques de production indisponibles** : les seuls chiffres réels viennent de la
>   CI et de cette machine de développement.
> - **Fixtures du dépôt non représentatives** : `sample_data/` = 1 fiche client réelle +
>   quelques fichiers synthétiques.

---

## 1. D-1 — corrigé, et prouvé par le comportement

### 1.1 Le défaut

`seamtech_search/api.py` appelait `get_presigned_url(object_key, expires_in=900)` alors
que le paramètre du client s'appelle `expiration_seconds`. Le `TypeError` tombait dans
le `except Exception` du repli : **aucune redirection, aucune erreur visible, aucun test
rouge** (les tests d'alors remplaçaient le client par un `MagicMock`, qui accepte
n'importe quel mot-clé).

### 1.2 Le correctif

```diff
-                url = storage_client.get_presigned_url(object_key, expires_in=900)
+                url = storage_client.get_presigned_url(object_key, expiration_seconds=900)
```

Appliqué aux **deux** occurrences — `api.py` l.477 (`/open`) et l.988 (artefacts). Ce
sont les seules du paquet : vérifié par balayage (`test_tous_les_appels_de_get_presigned_url_utilisent_le_mot_cle_reel`),
qui échoue si un troisième appelant apparaît sans être testé.

**Inchangés, comme exigé** : la durée de **900 s**, les noms de buckets, les
`object_key`, le contrat d'API, les replis (fichier local → téléchargement S3 → 404).
Aucun `except` supprimé. Aucune documentation retouchée pour masquer le défaut : au
contraire, `README.md` et `docs/VERIFICATION.md` redeviennent **exacts**, et la commande
de preuve de `VERIFICATION.md` grep désormais `expiration_seconds=900`.

### 1.3 Mesure avant / après (même scénario, même doublure boto3)

```
AVANT
seamtech_search.api WARNING Failed to generate presigned URL for IMP-1/abc/technical-report.pdf:
    S3StorageClient.get_presigned_url() got an unexpected keyword argument 'expires_in'
STATUT MESURE : 200 | Location: None
generate_presigned_url appelé côté boto3 : False

APRÈS
httpx INFO HTTP Request: GET http://testserver/imports/IMP-1/artifacts/report_pdf "HTTP/1.1 302 Found"
STATUT MESURE : 302 | Location: http://minio.invalide:9000/seamtech-documents/IMP-1/abc/technical-report.pdf?X-Amz-Expires=900&X-Amz-Signature=deadbeef
generate_presigned_url appelé côté boto3 : True
ExpiresIn transmis : 900 | Params : {'Bucket': 'seamtech-documents', 'Key': 'IMP-1/abc/technical-report.pdf'}
```

### 1.4 Tests ajoutés — `tests/test_url_presignee_302.py` (12 tests)

Vrai `S3StorageClient`, seul boto3 est doublé : un mot-clé erroné rend ces tests rouges
immédiatement, ce que les tests sur `MagicMock` ne pouvaient pas faire.

| Cas demandé | Vérifié | Routes |
|---|---|---|
| objet présent, présignature disponible | **302**, `Location` = URL présignée, URL réellement produite, **`ExpiresIn=900`** transmis à boto3, `Params={Bucket, Key}` | `/open` **et** `/imports/{id}/artifacts/{artifact}` |
| génération présignée indisponible | repli existant respecté (200, fichier servi), **aucune exception non contrôlée**, échec journalisé, **aucun identifiant** dans le corps, les en-têtes ni les journaux | les deux |
| objet absent | 404 franc, **pas de faux 302**, boto3 jamais appelé | les deux |
| traçabilité | l'audit enregistre l'événement `artifact_download` en `302` | artefacts |
| contrat d'appel | les 2 appelants du paquet n'emploient que des mots-clés existants ; un `expires_in=` réintroduit lève bien un `TypeError` ; les deux appels passent bien `900` | balayage du paquet |

Le test d'écart écrit la veille (`test_ecart_d1_…`), qui **figeait** le défaut, a été
**inversé** — comme son docstring l'annonçait — en
`test_d1_corrige_les_appels_de_l_api_utilisent_le_vrai_nom_de_parametre`. Aucun test
n'a été supprimé.

---

## 2. R-14 — corrigé par des marqueurs, pas par des renommages

### 2.1 Le défaut

La CI excluait par **sous-chaîne du nom** : `pytest -k "not postgres and not s3"`
(ci.yml l.91) et `pytest -k "not s3" -m "not perf"` (couverture, l.479). Conséquence :
**33 tests n'exigeant aucun service n'étaient exécutés nulle part** — chaos S3 sur
doubles, grammaire SQL PostgreSQL vérifiée hors serveur, routes qui répondent 503
*sans* PostgreSQL, configuration du client S3…

### 2.2 Audit des tests touchant MinIO / S3 / boto3 / présignature / compose

| Catégorie | Tests | Décision |
|---|---|---|
| **Exigent un endpoint vivant** | `test_integration_docker.py::test_docker_compose_infra`, `…::test_api_with_real_backends` (compose complet, `ensure_bucket_exists`/`upload_file`/`get_presigned_url`/`delete_file`), `test_sauvegarde_restauration.py::test_aller_retour_via_client_s3_reel` (vrai bucket), `test_storage.py::test_live_minio_s3_integration` (`SEAMTECH_TEST_S3_URL`) | **marqueur `s3` ajouté** (le dernier l'avait déjà) → **4 au total** |
| **Sur doubles boto3** (`MagicMock`, `S3EnMemoire`, `patch("boto3.client")`) | `test_storage.py` (mocké), `test_storage_coverage.py`, `test_object_keys.py`, `test_chaos.py`, `test_api_extra_gate.py`, `test_api_internal_coverage.py`, `test_push_coverage.py`, `test_sauvegarde_unites.py`, `test_stockage_release_candidate.py`, `test_url_presignee_302.py` | **non marqués** — les marquer reviendrait à les cacher, c'est-à-dire à refaire le défaut |
| **Analyse statique de YAML/scripts** (mentionnent MinIO sans le joindre) | `test_compose_hardening.py`, `test_compose_web_boot.py`, `test_construire_image_minio.py` | **non marqués** |

### 2.3 Commandes CI (sélection par catégorie)

| Étape | Avant | Après |
|---|---|---|
| suite sans service | `pytest -q -rf -k "not postgres and not s3"` | `pytest -q -rf -m "not postgres and not s3 and not perf"` |
| couverture | `pytest -k "not s3" -m "not perf" --cov=…` | `pytest -m "not s3 and not perf" --cov=…` |
| PostgreSQL | `pytest -m "postgres and not perf and not sauvegarde"` | **inchangé** |
| perf | `pytest -q -m perf -rf` | **inchangé** |
| jobs `integration` / `sauvegarde` | sélection **par chemin** | **inchangée** — leurs tests marqués `s3` y tournent toujours (vérifié par le garde-fou) |
| job `ocr` | `pytest -k "ocr"` | **inchangé** : c'est une sélection *thématique positive*, pas une exclusion de catégorie |

### 2.4 Garde-fou — `tests/test_selection_ci.py` (13 tests)

- `-m s3` rend **exactement** l'inventaire des tests à service réel (ni oubli, ni test
  sur doubles marqué à tort) ;
- détection **AST** d'un nouveau fichier qui lit `SEAMTECH_TEST_S3_URL` /
  `SEAMTECH_S3_ENDPOINT_URL` ou lance `docker` : il doit être classé et marqué ;
- contrôle nominatif que six tests sur doubles ne portent **pas** le marqueur ;
- **interdiction du motif `-k "not <catégorie>"`** dans tout le workflow ;
- les 4 commandes de sélection attendues sont bien celles du workflow ;
- les jobs dédiés sélectionnent par chemin (donc le marquage ne les ampute pas) ;
- aucun test dont le nom contient « s3 » n'est exécuté nulle part ;
- non-régression nominative des **10 tests autrefois cachés** ;
- la nouvelle sélection **n'enlève rien** à l'ancienne (inclusion stricte vérifiée) ;
- les 4 marqueurs de catégorie sont déclarés dans `pyproject.toml`.

### 2.5 Comparaison avant / après (mesurée, même arbre)

| Mesure | Avant (`-k`) | Après (`-m`) |
|---|---|---|
| tests collectés au total | 900 | **925** (+12 présignée, +13 sélection) |
| sélectionnés par la commande « sans service » | 662 | **718** |
| exécutés | 657 passed, 5 skipped | **715 passed, 3 skipped** |
| désélectionnés | 238 | **207** |
| marqués `s3` | 1 | **4** |
| marqués `postgres` | 204 | **204** (inchangé) |
| marqués `perf` / `sauvegarde` | 5 / 23 | **5 / 23** (inchangés) |
| collectés par la commande de couverture | 901 | **916** |

Les 2 sauts en moins ne sont pas des tests perdus : `test_integration_docker.py` était
*collecté puis sauté* faute de Docker ; il est maintenant *désélectionné explicitement*
par son marqueur, et reste exécuté par le job `integration` qui, lui, fournit le
service.

**Aucun test renommé pour échapper à un filtre** (les renommages de la veille sont
devenus inutiles et n'ont pas été étendus), **aucun test supprimé**, **aucun test réel
transformé en test synthétique**.

---

## 3. Fichiers du lot

| Fichier | Nature | Contenu |
|---|---|---|
| `seamtech_search/api.py` | **code, 2 lignes** | correctif D-1 (`expiration_seconds=900` ×2) |
| `.github/workflows/ci.yml` | CI | 2 commandes passées de `-k` à `-m` + commentaires + annotation de couverture |
| `tests/test_url_presignee_302.py` | test (nouveau) | 12 tests comportementaux du 302 présigné |
| `tests/test_selection_ci.py` | test (nouveau) | 13 tests de garde sur la sélection CI |
| `tests/test_stockage_release_candidate.py` | test (nouveau, veille) | 20 tests de garde du stockage ; test D-1 **inversé** |
| `tests/test_integration_docker.py` | test (marqueur) | `pytest.mark.s3` ajouté au `pytestmark` |
| `tests/test_sauvegarde_restauration.py` | test (marqueur) | `@pytest.mark.s3` sur l'aller-retour à bucket réel |
| `docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md` | doc | audit complet ; §5.2 (R-14 clos) et §6 (D-1 clos, avant/après) |
| `docs/RELEASE_CANDIDATE_CHECKLIST.md` | doc | 18 rubriques ; §8.5 attend désormais le 302 |
| `docs/verite_terrain/RAPPORT_PREPARATION_SANS_ARCHIVE_20260924.md` | doc | **addendum** : deux mesures dont la base a changé |
| `docs/verite_terrain/RAPPORT_20260925_RELEASE_CANDIDATE_STOCKAGE.md` | doc | le présent rapport |
| `CHANGELOG.md`, `README.md`, `docs/VERIFICATION.md` | doc | correctifs, commandes de sélection, commande de preuve D-1 |

Aucune dépendance ajoutée (`requirements*.txt` et `pyproject.toml` inchangés hors
documentation de marqueurs — déjà déclarés). Aucun seuil de `scripts/coverage_gate.py`
touché.

---

## 4. Vérification complète — commandes et sorties réelles

Interpréteur : `/home/user/.venv/bin/python` (venv hors dépôt, pytest 9.1.1).

| # | Commande | Sortie réelle |
|---|---|---|
| 1 | `ruff check .` | `All checks passed!` |
| 2 | `git diff --check` | (vide, `rc=0`) |
| 3 | `PYTHONPATH=. python scripts/audit_projet.py --rapide` | `BILAN : 12/12 contrôles verts` |
| 4 | `pytest tests/test_url_presignee_302.py` | `12 passed` |
| 5 | `pytest tests/test_storage.py tests/test_storage_coverage.py tests/test_object_keys.py tests/test_stockage_release_candidate.py -m "not s3"` | `42 passed, 1 deselected` |
| 6 | `pytest -m s3 -rs` | `4 skipped, 921 deselected` (aucun service ici — sauts explicites et lisibles) |
| 7 | `pytest tests/test_config.py tests/test_compose_hardening.py tests/test_compose_web_boot.py tests/test_construire_image_minio.py tests/test_requirements_consistency.py` | `44 passed` |
| 8 | `pytest tests/test_sauvegarde_unites.py tests/test_sauvegarde_restauration.py -m "not postgres and not s3"` | `16 passed, 7 deselected` |
| 9 | `pytest tests/test_construire_image_minio.py tests/test_integration_docker.py -rs` | `7 passed, 2 skipped` (Docker absent) |
| 10 | `pytest tests/test_selection_ci.py` | `13 passed` |
| 11 | `pytest tests/test_garde_fous_preparation.py tests/test_scan_safety.py tests/test_api_gate.py tests/test_comptes.py -m "not postgres"` | `33 passed, 18 deselected` |
| 12 | **suite locale (commande CI)** `pytest -q -rf -m "not postgres and not s3 and not perf"` | **`715 passed, 3 skipped, 207 deselected`** |
| 13 | couverture (commande CI) `pytest -m "not s3 and not perf" --cov=seamtech_search --cov-report=json:coverage.json` puis `python scripts/coverage_gate.py` | `743 passed, 173 skipped, 9 deselected` ; gate **FAILED en local** : global 69,7 % (plancher 85 %), `indexer.py` 89,9 % (porte 90 %) |

**Couverture — lecture honnête.** La porte échoue **sans PostgreSQL**, et échouait déjà
à l'identique avant ce lot (173 tests métier PostgreSQL se sautent hors serveur). Aucun
module ne régresse ; deux progressent :

```
global      69,7 %  (identique à la veille, structurellement bas sans PostgreSQL)
storage.py  97,5 % → 98,1 %   (porte 97 %)
api.py      87,4 % → 87,7 %   (porte 87 %)
config.py   86,0 % → 89,5 %
indexer.py  89,9 % → 89,9 %   (inchangé, non touché par ce lot)
```

La CI backend, qui dispose de PostgreSQL, reste le juge — et sa mesure porte désormais
sur **916 tests collectés au lieu de 901**, donc sur davantage de code réellement
exercé. **Aucun seuil n'a été modifié.**

### 4.1 Contrôles de non-régression demandés

| Contrôle | Résultat |
|---|---|
| Aucune fuite de secret | 5 tests dédiés (corps, en-têtes, journaux, 4 chemins d'erreur `ClientError`, URL présignée jamais journalisée) ; identifiants de test volontairement fictifs |
| Aucun appel réseau dans les tests unitaires | boto3 doublé partout ; la seule sortie de processus est `pytest --collect-only` (local) dans le garde-fou de sélection |
| RG13 / RG14 inchangés | `tests/test_garde_fous_preparation.py` vert ; aucun fichier RG13/RG14 modifié |
| Aucun seuil de couverture abaissé | `scripts/coverage_gate.py` non modifié (`git diff` vide sur ce fichier) |
| Aucune dépendance ajoutée | `requirements*.txt` non modifiés |
| Aucun test supprimé | 900 → 925 collectés ; les 5 renommages de la veille sont documentés, aucun nouveau |

### 4.2 Ce qui n'a pas été exécuté ici

| Non exécuté | Raison |
|---|---|
| Build frontend (`pnpm build`) | `frontend/node_modules` absent, `pnpm` non installé ; **aucun fichier frontend modifié** → job CI `frontend` |
| Suite PostgreSQL (`-m postgres`, 204 tests) | pas de serveur PostgreSQL ici → job CI `backend` |
| `-m s3` contre un endpoint réel | interdiction de service distant ; MinIO n'est plus distribué → jobs CI `integration` et `sauvegarde` |
| E2E Playwright, Docker | pile complète requise → jobs CI dédiés |
| Tout ce qui touche l'archive réelle | **Lot G bloqué**, **F-2 bloqué** |

---

## 5. Risques restants (audit §5)

**À traiter avant production** : **R-1** (MinIO archivé, plus de correctifs, image
recompilée depuis des sources figées) · R-2 (configuration S3 incomplète acceptée) ·
R-3 (`storage_backend` cosmétique) · R-4 (asymétrie de préfixe) · R-5 (NFC ≠ NFD) ·
R-6 (`CreateBucket`/`PutBucketVersioning` à chaque envoi) · R-7 (identifiants = root
MinIO) · **R-13** (compatibilité jamais prouvée contre un second fournisseur S3 :
`test_live_minio_s3_integration` reste sauté faute de `SEAMTECH_TEST_S3_URL` en CI).

**Clos par ce lot** : **D-1** (302 présigné rétabli et prouvé) et **R-14** (sélection CI
par marqueur, 33 tests rendus à la CI, garde-fou en place).

**À surveiller** : R-8 à R-12.

## 6. Décisions qui restent au commanditaire

| Réf | Décision |
|---|---|
| **D-2** | Backend de stockage cible : statu quo MinIO figé · Garage · SeaweedFS · Ceph RGW · AWS S3 · Cloudflare R2 · fournisseur UE |
| **D-3** | Les documents clients peuvent-ils quitter l'atelier ? (bloque D-2) |
| **D-4** | Compte S3 applicatif restreint + rotation des secrets |
| **D-5** | Le versioning de bucket est-il exigé ? |
| **D-6** | Normalisation Unicode (NFC) imposée à l'inventaire de l'archive réelle |
| **D-7** | TLS vers l'endpoint et chiffrement au repos |
| **D-8** | Échec au démarrage si la configuration S3 est incomplète + `storage_backend` opérant |

*(D-1 et D-9 étaient dans cette liste la veille : tranchées et appliquées ce jour.)*

---

## 7. Conclusion

## **VALIDÉ AVEC RÉSERVES**

Établi par exécution : D-1 corrigé et prouvé (302, `Location`, `ExpiresIn=900`, replis,
404 sans faux 302, audit tracé) ; R-14 corrigé (sélection par marqueur, **4** tests S3
marqués, **718** tests exécutés par la commande CI sans service contre 662 avant,
garde-fou de 13 tests) ; `ruff` propre, `git diff --check` propre, audit projet 12/12 ;
suite locale **715 passed / 3 skipped** ; couverture inchangée globalement, améliorée
sur `storage.py` (98,1 %) et `api.py` (87,7 %), **aucun seuil abaissé** ; aucun test
supprimé, aucune dépendance ajoutée.

Réserves :

1. **R-1** — MinIO est archivé sans canal de correctifs ; la décision de backend
   (D-2/D-3) reste à prendre avant la mise en production.
2. **R-13** — la compatibilité S3 hors MinIO n'est toujours pas prouvée par exécution :
   aucun job ne fournit `SEAMTECH_TEST_S3_URL`. Le test existe et est correctement
   déclaré, il attend un endpoint candidat.
3. **R-2, R-3** — deux réglages restent trompeurs en production (décision D-8).
4. **Non exécutable ici** : build frontend, suite PostgreSQL, endpoint S3 réel, Docker —
   tous couverts par des jobs CI.
5. **Rappel final** : **Lot G réel bloqué** (archive non livrée), **F-2 bloqué** (fiches
   validées manquantes), **métriques de production indisponibles**, **fixtures du dépôt
   non représentatives de l'archive réelle**. Aucun dimensionnement, coût ou durée de
   migration ne peut être chiffré avant l'inventaire de l'archive réelle.
