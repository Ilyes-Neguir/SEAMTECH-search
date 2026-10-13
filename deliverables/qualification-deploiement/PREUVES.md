# Preuves d'exécution (2026-10-13)

Environnement : voir `preuves/environnement.json`.

Commit de départ : `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09`.
Commit applicatif PR #37 (lot 1) : `be0f6d2e170145a8491ebf065ef2f464fd639a63`.
Lot 3 : `0665a684133f31e5c5742faeeb1fe3104ab2a721`.
Branche de session : `arena/e7f33785-seamtech-search`.

## CI SHA `0665a68` (consultée via `gh` + annotations, pas les zips CDN)

- CI push [38402755760](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402755760) **failure**
  - backend (3.12) sqlite : `INVENTAIRE_S3_REEL` (annotation `sqlite-suite-failed`)
  - sauvegarde job 115314326192 : étape pytest exit 1 (pas de détail pytest dans les annotations)
  - ocr job 115314326716 : **SUCCESS** (taux propre 1.000, dégradé 0.955, tesseract 5.3.4)
- Qualification reserves push [38402755908](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402755908) **failure**
  (MinIO démarré ; échec étape tests ; artefacts zip CDN inaccessibles ici)

Lot 4 local (hors MinIO/tesseract) : `52 passed, 10 skipped` sur
`test_sauvegarde_references` + `test_selection_ci` + normalisation +
extractors + ocr_parcours.

## SHA applicatif `a3506c84892e97a8993fe6a9657cc93019f002d3`

Fichier : `preuves/ci-a3506c8.json`.

- Qualification reserves push **38405546902** : **74 tests, 0 skipped, 0 failure** (PG + MinIO officiel + tesseract fra/eng). Inclut B-03 et C-03/C-04.
- CI push **38405546959** : job `sauvegarde` SUCCESS (garde-fou 0 skip, notice `…_obj` = restore objets + PG + fichiers, sources coupées) ; job `ocr` SUCCESS tesseract 5.3.4 (extracteurs, **pas** le parcours import) ; postgres `279 passed / 0 skipped`. Conclusion globale CI **failure** sur le job **perf** :
  - hybride p95 280,9 ms ≥ 250 ; dimension p95 326,7 ms ≥ 250 ; 1 500 fiches p95 324,4 ms ≥ 250 ;
  - fonds réel **p50 275,2 ms ≥ 100 ms** (critère d'architecture, aucune tolérance CI).
  Baseline **même job** sur `8dd5ae7` (run 38267250173) : p50 8–14 ms / p95 11–47 ms. Machines différentes → comparaison identique demandée au job `Perf comparaison`. Seuil 250 ms **non relevé**.
- C-03/C-04 : crawl+upsert+`GET /search`+`POST /open` dans les 74 tests skip=0 — **pas** l'import navigateur.

## SHA `ee776d0` / `6ccc383` (2026-10-13, après alignement index 023)

- CI push **38422805119** backend (3.12) **SUCCESS** : postgres 279/0 ;
  perf p50 5,0 / 6,1 / 6,0 / 13,7 ms et p95 7,0 / 6,3 / 11,1 / 34,9 ms
  (50 req., fonds réel, dimension, 1 500) — sous 100 ms. SHA applicatif
  recherche = `51d2772` (inchangé ensuite).
- Qualification native push **38425712093** SHA `6ccc383` **SUCCESS** :
  notice `native-summary` tous les contrôles PASS (PostgreSQL apt +
  pgvector, Redis, MinIO binaire officiel, API+worker+frontend, lot
  worker 7792-SO, tesseract hôte, restore CLI vers base neuve).
- Job `Perf comparaison` : pas encore de `resume.json` (pip install -e
  d'archive) — PYTHONPATH ensuite.

## Commandes

```text
SEAMTECH_TEST_DATABASE_URL=postgresql://postgres:@/postgres?host=/tmp/seamtech-pgdata
SEAMTECH_PG_BINDIR=.venv/lib/python3.11/site-packages/pgserver/pginstall/bin

python scripts/reproduire_normalisation_recherche.py
# avant (8dd5ae7) → preuves/A-03-avant.json  defaut_observe=true
# après (code 023) → preuves/A-03-apres.json defaut_observe=false

pytest tests/test_normalisation_cle_recherche.py \
       tests/test_recherche_normalisation.py \
       tests/test_sauvegarde_references.py \
       tests/test_extractors.py \
       tests/test_postgres_sql_grammar.py \
       tests/test_stockage_release_candidate.py \
       tests/test_recherche_hybride.py \
       tests/test_facettes_et_suggestions.py \
       tests/test_recherche_dimension.py \
       tests/test_ocr_qualification.py
```

## Comptes

| Lot | Résultat |
|---|---|
| Unitaires ciblés (hors postgres/s3/perf) | **119 passed**, 4 skipped (OCR), 21 deselected |
| Postgres ciblé (normalisation + hybride + facettes + dimension + grammar + RC + sauvegarde références) | **139 passed** (run précédent hors aller-retour distinct) puis aller-retour **1 passed** |
| OCR qualification | **4 skipped** — tesseract absent (motif explicite) |
| `test_sauvegarde_restauration` historique | 3 failed — harnais DSN socket, **non reclôturé** |
| `test_migrations_metier` | erreurs de connexion DSN socket, **non reclôturé** |

## Lot 2 (chemins réels, volume, outillage)

```text
pytest tests/test_qualification_chemins_volume.py
bash scripts/preparer_outillage_qualification.sh
```

- Volume : 800 fiches synthétiques, parité d’identifiants, GET /recherche + GET /search.
  Mesure : p50 **11,5 ms**, p95 **17,8 ms**, max 20,3 ms (n=40) — indicatif
  sandbox, fichier `preuves/volume-800.json`. Pas un PASS atelier.
- Migration 022 → 023 sur données déjà écrites (titres inchangés).
- tessdata_fast cloné (fra/eng/osd). tesseract/MinIO Releases : CDN bloqué
  (`release-assets.githubusercontent.com`). 4 tests OCR skipped.

Le champ `phase` des JSON A-03 reste libellé `avant_correction` (script
de reproduction unique) : se fier à `defaut_observe` et à l'ordre des
identifiants.

## Extrait A-03 — classement GENOIS

Avant (`8dd5ae7`) : `GENOIS` commençait par `N-GENOIS_T-009` ;
`GÉNOIS` par `N-NOEL-004`.

Après : les six variantes (`GENOIS`, `GÉNOIS`, `génois`, `Génois`,
`GeNoIs`, NFD) partagent **le même ordre** commençant par
`N-NOEL-004`, `N-MULLER-006`, `N-FACADE-001`, `N-GENOIS_T-009`, …

Familles `facade`, `reparation`, `ete`, `cable`, `muller`, `sao`,
`coeur` : un seul ID attendu, identique dans les deux sens.

## Migration 023

- `VERSION_SCHEMA_METIER = "023_recherche_normalisee"`
- Fonction `seamtech_texte_recherche(text)` IMMUTABLE (unaccent 2
  arguments si l'extension est là, sinon `translate` de repli)
- Index GIN `idx_fiche_*_trgm_norm` + `idx_chunk_contenu_trgm_norm`
- L'index 016 `idx_fiche_titre_trgm` est conservé

## Sauvegarde — contrat local

```text
python -m seamtech_search.sauvegarde sauver \
  --base-url URL --archive RACINE --dossier data/backups \
  --sans-s3 --avec-references
# exit 0 si complet, 2 si références manquantes

python -m seamtech_search.sauvegarde restaurer \
  --manifeste data/backups/….manifest.json \
  --base-cible URL_NEUVE \
  --fichiers-cible /cible/distincte
```
