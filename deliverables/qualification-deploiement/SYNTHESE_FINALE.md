# Synthèse finale — PR #37

**SHA applicatif retenu : `d202f0b0c371ab5659e7615d7fee80c99f0ae0dd`**

Branche `arena/e7f33785-seamtech-search`. PR [#37](https://github.com/Ilyes-Neguir/SEAMTECH-search/pull/37) **ouverte, non fusionnée**. Aucun PV. Recette historique (`deliverables/recette-virtuelle/`) intacte. Pas une acceptation métier ni une mise en production.

Ce document est la **seule** synthèse de clôture technique. Les runs cités sont ceux de **`d202f0b`**, sauf mention explicite d’un SHA historique avec justification.

## 1. Filiation

```
8dd5ae7 (départ)
  → … → a3506c8   sauvegarde objet-only, inventaire S3, OCR parcours
  → 1fc66c2       preuves CI a3506c8 (docs)
  → 51d2772       alignement SQL trigrammes / index 023 + amorce native/perf
  → 69de605       exception RG14 outillage natif
  → ee776d0       (même recherche.py que 51d2772)
  → 550d549       OCR natif tesseract hôte
  → 6ccc383       restore CLI vers base neuve (natif PASS)
  → f731b70       docs + PYTHONPATH comparaison
  → d202f0b       HEAD PR : docs + job perf (échec seulement si HEAD ≥ 100 ms)
```

`d202f0b` **contient** `51d2772`, `ee776d0`, `6ccc383` et `a3506c8` (ancêtres).

| SHA cité plus tôt | Relation à `d202f0b` | Code produit (`seamtech_search/`) |
|---|---|---|
| `51d2772` | ancêtre | **identique** (`recherche.py` inchangé ensuite) |
| `ee776d0` | ancêtre | identique |
| `6ccc383` | ancêtre | identique |
| `a3506c8` | ancêtre | diff = uniquement `recherche.py` (CTE `q` + LHS d’index 023) |

Après `51d2772`, hors docs/scripts d’essai : `tests/test_garde_fous_preparation.py` (+ exception RG14 pour l’outillage natif). Pas de nouveau changement du moteur.

`ee776d0` n’est **pas** le SHA de revue : c’est un intermédiaire dont le `recherche.py` est celui de `51d2772`, rejoué sur `d202f0b`.

## 2. Tableau A / B / C / D

Preuves **exécutées sur `d202f0b`** (push [38427660899](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38427660899) et workflows associés).

| | Statut technique | Preuve sur `d202f0b` | Limite |
|---|---|---|---|
| **A Recherche** | CORRIGÉ ET TESTÉ | postgres **279 passed / 0 skipped** ; qualification-reserves **74 / 0 / 0 / 0** | Corpus synthétique. Attente humaine. |
| **A Perf** | CORRIGÉ ET TESTÉ (CI) | backend 3.12 p50 6–13 ms, p95 8–42 ms ; same-runner HEAD p50 4,7–9,3 / p95 6,0–29,7 ms | Pas un PASS atelier. Seuil CI 250 ms étiqueté, **non relevé**. |
| **B Sauvegarde** | CORRIGÉ ET TESTÉ | job `sauvegarde` SUCCESS ; notices `…_obj` et `…_restauree` | MinIO CI, pas R2 / bucket atelier. |
| **C OCR** | CORRIGÉ ET TESTÉ (chemins nommés) | Qual 74 skip=0 (C-03/C-04) ; job `ocr` tesseract 5.3.4 taux 1.000 / 0.955 ; natif tesseract hôte | **Pas** le dépôt navigateur de scans. |
| **D Natif** | CORRIGÉ ET TESTÉ (essai) | Qualification native SUCCESS, notice `native-summary` tous CONTROLE PASS | Runner `ubuntu-24.04` jetable, pas le serveur entreprise, pas la prod. |

Les quatre points **ne sont pas** une homologation production.

## 3. Runs `d202f0b` (push sauf mention)

| Workflow | Run | Conclusion |
|---|---|---|
| CI | [38427660899](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38427660899) | **success** (backend 3.11/3.12/3.13, sauvegarde, ocr, e2e, integration, recette-locale, hors-ligne-reel, recette-corpus-reel, docker, frontend, securite) |
| Qualification reserves | [38427660983](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38427660983) | **success** `tests=74 skipped=0 failures=0 errors=0` |
| Qualification native | [38427660929](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38427660929) | **success** notice `native-summary` |
| Perf comparaison | [38427660962](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38427660962) | **success** (HEAD sous 100 ms ; warning historique a3506c8) |
| Scale bench synthétique | [38427667498](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38427667498) | **success** (PR) |

`gh pr checks 37` : **tous pass**, aucun échec requis.

## 4. Recherche — non-régression fonctionnelle (`d202f0b`)

Correction : `_source_trigrammes` compare `seamtech_texte_recherche(colonne)` à une clé CTE (plus de `coalesce` autour de la colonne — l’index GIN 023 est `seamtech_texte_recherche(titre)`).

Couvert par la suite postgres 279/0 **et** les 74 tests qualification skip=0, notamment :

- parité IDs+ordre casse/accents (`test_recherche_normalisation`, volumes) ;
- ligatures œ/æ via `normaliser_cle_recherche` (clé de **recherche**, distincte de `sans_accents` dédup) ;
- données pré-023 (`test_donnees_existantes_avant_023_deviennent_paritaires`) ;
- mutation UPDATE (`test_mutation_document_existant_et_fonction_immutable`) ;
- filtres, pagination, non-latin (`test_filtres_codes_pagination_non_latin`) ;
- GET `/recherche` et `/search` (`test_http_recherche_et_search_memes_identifiants`) ;
- garde-fou dédup **ROUGE puis VERT** (job backend 3.12).

## 5. Performance — protocole et mesures `d202f0b`

- Tests : `pytest -m perf` (chauffe jetée, sans `--cov`).
- Jeux : 50 requêtes synthétiques ; fonds réel 7792-SO (13×5) ; dimension (6 formes×10) ; 1 500 fiches (12×5).
- Produit : p50 &lt; 100 ms (architecture) et p95 &lt; 100 ms. CI : `SEAMTECH_PERF_P95_CI_MS=250` **étiqueté**.

**Backend 3.12** job 115401189155 :

| Jeu | p50 | p95 | max |
|---|---|---|---|
| 50 requêtes synthétique | 6,4 | 9,0 | 10,6 |
| fonds réel 7792-SO | 7,5 | 7,9 | 8,1 |
| dimension | 7,2 | 13,3 | 13,6 |
| 1 500 fiches | 12,7 | 42,2 | 42,5 |

**Same-runner** 3 runs (job 115401188643), médianes :

| SHA | fonds réel p50 | 50 req. p50 / p95 |
|---|---|---|
| `8dd5ae7` | 5,3 ms | 5,5 / 6,7 |
| `a3506c8` | **231 ms (×43)** | 4,4 / ~270 (queue) |
| **HEAD `d202f0b`** | **5,3 ms** | **4,7 / 6,6** |

HEAD same-runner : 1 500 p50 9,3 / p95 29,7 ; dimension p50 5,9 / p95 11,0. Tous **&lt; 100 ms**.

La régression `a3506c8` est **historique et prouvée** (p50 fonds réel, pas un flake). Elle n’est plus dans HEAD.

## 6. OCR — parcours démontrés seulement

| ID | Parcours | `d202f0b` | Pas ça |
|---|---|---|---|
| C-03 | crawl → upsert → GET `/search` → POST `/open` | Qual 74 skip=0 `test_parcours_import_ocr_index_recherche_telechargement` | Import navigateur |
| C-04 | scan anglais indexé | `test_scan_anglais_retrouve_les_mots` | — |
| job `ocr` | extracteurs tesseract 5.3.4, sans PostgreSQL | taux propre 1.000 / dégradé 0.955 | Parcours import |
| D natif | tesseract **hôte** fra+eng (`ocr-tesseract-hote`) + crawl d’index | notice native-summary | Lot worker de **scans** |

**Dépôt de scans via navigateur** (`POST /imports/upload` + file + écran jusqu’au texte OCR et téléchargement) : **non démontré**, non déclaré validé.

## 7. Sauvegarde — procédure exercée

Joint **PostgreSQL + fichiers locaux + objets MinIO officiel**, restauration vers **cibles distinctes**, sources indisponibles, SHA-256.

- CI `sauvegarde` : `…_obj restaurée`, `…_restauree`.
- Qual : `test_aller_retour_objets_minio_sources_indisponibles` dans les 74 skip=0.
- Natif : `python -m seamtech_search.sauvegarde restaurer` crée la base cible (refus si elle existe déjà).

Procédure d’essai : `scripts/qualification_native.sh` + `PROCEDURE_NATIVE.md`.

## 8. Installation native — procédure

`bash scripts/qualification_native.sh` sur Debian/Ubuntu vierge : apt PostgreSQL+pgvector, Redis, MinIO officiel compilé (tag épinglé), API + worker + frontend **processus hôtes**, compte nominatif, lot worker 7792-SO, OCR tesseract hôte, `/recherche`, `/open`, sauvegarde/restore, kill+relance API.

Exécuté sur `d202f0b` : run 38427660929. Docker Compose (`recette-locale`) **≠** D.

## 9. Checks PR #37

Tous les checks listés par `gh pr checks 37` sont **pass** (CI matrice, qualification, native, perf comparaison, e2e, sauvegarde, ocr, …). Rien à affaiblir, aucun seuil relevé.

## 10. Limites restantes (hors clôture technique de ce lot)

- Validation humaine / métier / PV.
- Serveur entreprise, R2, bucket atelier, fonds réel opérateur.
- Dépôt navigateur de scans.
- Perf atelier (poste, volume réel) — la CI n’est pas l’atelier.
- Fusion et production : **non autorisées ici**.
