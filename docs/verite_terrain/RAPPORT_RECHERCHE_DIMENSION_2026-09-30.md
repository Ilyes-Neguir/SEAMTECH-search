# Rapport Phase 1 — recherche par dimension (migration 019)

**Date :** 2026-09-30
**Branche :** `arena/01a0f1ee-seamtech-search`
**Base :** `origin/main` = `02e4589c9fe5abb8f954b126ba320d021176f4ff` (PR #33 fusionnée, CI post-merge 11/11)
**Objet :** « je tape une dimension dans la barre et je vois toutes les voiles correspondantes ».

---

## 1. Test de tokenisation RÉEL (exigence : AVANT la conception)

**Environnement :** PostgreSQL **16.2** réel (binaire officiel, compilation contrib
`unaccent` + `pg_trgm` du tag `REL_16_2`), config `seamtech_unaccent` créée
exactement par le code produit (`COPY = simple`, puis
`ALTER MAPPING FOR asciiword, word, numword, uint, int, float WITH unaccent, simple`).

**Commande** (psycopg2 sur la base `tokenlab`) :

```sql
SELECT websearch_to_tsquery('seamtech_unaccent', '6,60')::text;
SELECT websearch_to_tsquery('seamtech_unaccent', '6.60')::text;
SELECT to_tsvector('seamtech_unaccent', 'slu 6.60') @@ websearch_to_tsquery('seamtech_unaccent', '6,60');
-- … croisements des deux formes, ts_debug, configs de repli
```

**Sorties brutes (verbatim) :**

```text
=== websearch_to_tsquery('seamtech_unaccent', ...) ===
  '6,60'         -> "'6' <-> '60'"
  '6.60'         -> "'6.60'"
  '6,6'          -> "'6' <-> '6'"
  '6.6'          -> "'6.6'"
  '660'          -> "'660'"
  '660 cm'       -> "'660' & 'cm'"
  '6,60 m'       -> "'6' <-> '60' & 'm'"
  '6600 mm'      -> "'6600' & 'mm'"
  '6 60'         -> "'6' & '60'"
  'SLU 6,60'     -> "'slu' & '6' <-> '60'"
  'spi 6,60'     -> "'spi' & '6' <-> '60'"
  '6.60m'        -> "'6.60' <-> 'm'"
  '6.5'          -> "'6.5'"

=== to_tsvector('seamtech_unaccent', ...) ===
  'SLU 6.60'               -> "'6.60':2 'slu':1"
  'SLU 6,60'               -> "'6':2 '60':3 'slu':1"
  '6.60'                   -> "'6.60':1"
  '6,60'                   -> "'6':1 '60':2"

=== Croisement vecteur × requête ===
  vec_pt ~ q_virg  -> False     # « 6,60 » ne matche JAMAIS « 6.60 »
  vec_pt ~ q_pt    -> True
  vec_virg ~ q_virg -> True
  vec_virg ~ q_pt  -> False     # « 6.60 » ne matche JAMAIS « 6,60 »

=== ts_debug('seamtech_unaccent', '6,60') ===
     token='6'        alias=uint       dicts={unaccent,simple} lexemes=['6']
     token=','        alias=blank      dicts={} lexemes=None
     token='60'       alias=uint       dicts={unaccent,simple} lexemes=['60']

=== ts_debug('seamtech_unaccent', '6.60') ===
     token='6.60'     alias=float     dicts={unaccent,simple} lexemes=['6.60']

=== websearch_to_tsquery '6,60' dans d'autres configs (piège virgule) ===
  french   -> "'6' <-> '60'"
  simple   -> "'6' <-> '60'"
  english  -> "'6' <-> '60'"

=== Double forme indexée ('slu 6.60 6,60') ===
  requête '6,60'  -> True
  requête '6.60'  -> True
  requête '6.6'   -> False      # la variante 1 décimale ne matche ni l'une ni l'autre
```

**Conclusions expérimentales :**

1. La virgule est un **séparateur** : « 6,60 » devient la phrase `'6' <-> '60'`.
   Le point est un **float** : « 6.60 » reste le lexème unique `'6.60'`.
2. Les deux formes **ne se croisent jamais** (faux dans les deux sens) — et ce
   quel que soit le dictionnaire (`french`, `simple`, `english`, `seamtech_unaccent`) :
   c'est le parseur par défaut qui découpe, avant toute normalisation.
3. « 6,6 » (1 décimale) ne matche ni « 6.60 » ni « 6,60 » → la **normalisation
   côté requête** est obligatoire, pas seulement l'indexation des deux formes.
4. « 6,60m » collé produit `'6' <-> '60m'` (piège : le `m` s'accroche au `60`).

Le garde `tests/test_recherche_dimension.py::TestTokenisationReelle` fige ces
résultats et les rejoue à chaque CI sur serveur réel.

## 2. Conception (découlant de l'essai)

| Exigence | Réalisation |
|---|---|
| Migration 019 idempotente, 7 cotes dans le texte pondéré, DEUX formes, poids adapté | `SQL_019_RECHERCHE_DIMENSION` : `CREATE OR REPLACE` des **deux** définitions (`rafraichir_texte_recherche_fiche` héritée de 007→013 et `rafraichir_texte_recherche_toutes` héritée de 012→013), chaque cote écrite en `round(x,2)::text` (« 6.60 » — sortie numérique locale-indépendante, vérifiée) **et** `replace(..., '.', ',')` (« 6,60 »), agrégées au **poids B** (champs métier). Backfill global `PERFORM rafraichir_texte_recherche_toutes()` (à la 018 : rattrapage des fiches déjà posées). Enregistrée `019_recherche_dimension`, `VERSION_SCHEMA_METIER` à jour. |
| Normalisation requête : « 6,6 », « 6.60 », « 6,60 m », « 660 cm », « 6600 mm » → même valeur | `analyser_requete_dimension` + `RequeteDimension.valeur_convergente()` (réutilise `vers_metres` du module d'extraction). Un seul essai unitaire prouve la convergence des cinq formes. |
| Requête numérique seule → valeur dans TOUTES les cotes ±0,5 % par le chemin numérique existant, pas via tsvector | `sources_actives == ["dimension"]` — bornes sur les 7 colonnes de `v_fiche_recherche` (même mécanique que `cote=&min=&max=`), `BETWEEN` ORé, tolérance `TOLERANCE_DIMENSION = 0.005`. Ordonné par écart à la valeur. |
| « spi 6,60 » / « SLU 6,60 » → orientation cote nommée | Alias de cotes (`slu`, `sle`, `sf`, `shw`, `spa`, `tetiere`/`têtière`, `poids`, `guindant`) → filtre sur la cote seule, valeur convertie dans son unité métier (« tetiere 150 mm » → 15 cm). « spi » n'est pas un alias (type de voile) : les mots restent textuels, la valeur filtre sur toutes les cotes. |
| Frontière années/codes | Un **entier nu** sans unité ni cote nommée n'est jamais une dimension : « spi sailonet 2026 », « 7792-SO », « 0701-GV-001 », « 29er » restent purement textuels (garde de non-régression dédié). « tetiere 15 » fait exception car l'alias désambiguïse. |

## 3. Mesures — RÉEL / SYNTHÉTIQUE / NON MESURÉ

| Catégorie | Mesure (commande + sortie brute) |
|---|---|
| **RÉEL** | Suite PostgreSQL complète sur serveur 16.2 réel : `pytest -m postgres -q` → **228 passed, 2 skipped**. Dont rappel **50/50** (`[mesure Lot E] 50 requêtes : rappel@10 = 50/50`) et **13/13** (`[mesure Tâche 3 — fonds réel] 13/13 requêtes retrouvent 7792-SO au rang 1`) — aucune régression. Suite sans service : `pytest -m "not postgres and not s3 and not perf" -q` → **787 passed, 3 skipped, 257 deselected** (baseline pré-Phase 1 : 771 passed — 771 conservés, 16 ajoutés, 0 perdu). |
| **RÉEL (latence locale sandbox)** | `pytest -m perf` → p95 = 15,7 ms (50 requêtes synthétiques), 46,8 ms (1 500 fiches), 13,8 ms (fonds réel), **15,2 ms** (recherche par dimension, 6 formes × 10) — toutes < 100 ms. Contexte : sandbox, base jetable — pas le VPS01 ni le poste atelier. |
| **SYNTHÉTIQUE** | Scale-bench 10 000 fiches : rerun du workflow après 019 à déclencher sur la PR (le workflow se déclenche sur `schema_metier.py`/`recherche.py`), avant/après archivés dans `docs/benchmarks/`. |
| **NON MESURÉ** | Poste Windows du commanditaire ; parcours live complet (la CI e2e live le prouve sur son service, pas sur sa machine) ; chronos humains REF-001..007. |

## 4. Épingles et inventaires mis à jour

- `VERSION_SCHEMA_METIER` = `019_recherche_dimension` ; séquence **001..019**
  (garde `test_migrations_sequentielles_001_a_017_sur_base_vide`, identifiant
  historique conservé, assertions `range(1, 20)`).
- `tests/test_ocr_comportement.py` : version + nouveau `test_migration_019_existe`.
- `tests/test_recette_corpus_reel.py::test_03_services_et_migrations_018_base_neuve` :
  inventaire `attendues` complété (018 + 019) — identifiant conservé.
- `docs/RELEASE_CANDIDATE_CHECKLIST.md` §5 : 19 lignes, `019_recherche_dimension 33`.
- `docs/API.md` : contrat « Recherche par dimension (Phase 1) » + `dimension_active`.
- **Garde E2E live : 8 → 10 tests** (`frontend/e2e/recherche-dimension.spec.ts`,
  2 tests : « 6,60 »/« 6.60 »/« 660 cm » convergent sur la vraie 7792-SO, et
  « SLU 6,60 » oriente la cote nommée). La porte CI exige **10 passés au
  premier essai, 0 flaky, 0 saut** (composantes : 3 validation + 1 badge +
  4 Phase 2.2 + **2 recherche par dimension**).

## 5. Ce qui reste cassé ou non prouvé

- Le **rerun scale-bench** et la CI 11/11 ne sont prouvés qu'après push/PR
  (voir §6) ; les chiffres de performance « p95 < 100 ms » ci-dessus sont
  mesurés en sandbox, le seuil applicatif reste celui de la CI.
- Le parcours **recette_locale** (Phase 2) n'existe pas encore ; le comportement
  sur Windows (chemins, accents) n'est pas mesuré ici.
- Les deux formes indexées allongent le vecteur B : l'ordre exact entre fiches
  de titres équivalents peut bouger (dilution mesurée) — les rappels 50/50 et
  13/13 restent intacts (c'était le garde demandé).

## 6. Livraison

PR dédiée Phase 1 sur `arena/01a0f1ee-seamtech-search`, CI 11/11 exigée verte,
**aucun merge sans accord explicite**. Résultats CI et scale-bench ajoutés en
commentaire de PR dès qu'ils sortent.
