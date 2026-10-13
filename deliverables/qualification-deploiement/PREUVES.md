# Preuves d'exécution (2026-10-13)

Environnement : voir `preuves/environnement.json`.

Commit de départ : `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09`.
Branche de session : `arena/e7f33785-seamtech-search`.

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
