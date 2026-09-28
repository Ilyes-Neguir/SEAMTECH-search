# Rapport final — corpus réel des 7 ZIP : recette CI, gabarits atelier, guide de validation (2026-09-28)

> **Périmètre impératif :** ce rapport ne concerne QUE les 7 ZIP clients fournis dans
> le dépôt (REF-001 à REF-007). Aucune extrapolation à une archive complète non
> fournie. Aucune valeur métier n'y figure : uniquement des comptes, des codes
> REF-00x, des statuts HTTP, des empreintes et des mesures agrégées.

## Décision synthétique

**Recette CI du flux réel : VERTE (voir preuves ci-dessous).** Le dépôt passe
dendroit en dendroit sur base PostgreSQL neuve : extraction des 7 ZIP vers un
répertoire temporaire du runner (empreintes vérifiées avant/après), migrations
001→017, import Phase 0 (upload REF-001, scan/confirm REF-002..007), dépôt Lot C
(fiche + pièces jointes en une transaction par dossier), classement et champs
proposés (7 fiches `a_valider`, jamais validées automatiquement), OCR par étages,
recherches, ouverture PDF par URL présignée réelle, rapport PDF, puis
sauvegarde → restauration en base neuve avec revérifications.

**La mise en production reste SOUMISE À VALIDATION HUMAINE** : les 7 fiches sont
`a_valider` (RG3 — aucune validation automatique, calibration verrouillée). Le
guide de validation humaine (Lot 3) décrit le parcours nominatif quatre yeux.

## 1. Points de départ et d'arrivée

- Branche de travail : `arena/01a0e98a-seamtech-search` (base `main`
  `0706733`).
- SHA de départ : `0706733418f10a1429af90e44fb3ff7e8577f0f6`.
- SHA final : `cf5c68e` (recette verte) puis le commit du présent rapport.
- Runs CI de référence : voir §5 (preuves).

### 1.1 État « avant » (constat de départ, mesuré dans les rapports précédents)

Reprise des mesures de `RAPPORT_FINAL_CORPUS_REEL_2026-09-28.md` et
`RAPPORT_FLUX_COMPLET_CORPUS_REEL_2026-09-28.md` :

| Mesure | Avant |
| --- | --- |
| Dossiers candidats fiche détectés par le parcours upload/scan | 0/7 |
| Fiches avec gabarit reconnu par le parseur | 1/7 |
| Champs proposés par fiche (moyenne) | ~5 (partiels) |
| Imports API restés `needs_review` faute de fiche | 7/7 |
| Recette CI du flux réel sur base PostgreSQL neuve | inexistante |

### 1.2 État « après » (mesuré, agrégats régénérés — aucune valeur métier)

| Mesure | Après |
| --- | --- |
| Dossiers candidats fiche détectés (Phase 0 + Lot C) | 7/7 |
| Fiches avec gabarit reconnu (6 × `FICHE_JADE_V1`, 1 × `FICHE_GV_FULLBATTEN_V1`) | 7/7 |
| Champs proposés (total / par fiche) | 173 / 22-22-22-22-23-40-22 |
| Confiance moyenne par fiche (min-max) | 0,836 - 0,920 |
| Statut des fiches après recette | 7 × `a_valider` (jamais validées) |
| Classification automatique du corpus (14 PDF uniques) | 14/14 cohérente |
| Anomalies bloquantes détectées à l'extraction | 0 |
| Job CI `recette-corpus-reel` (flux complet réel) | vert |

Mesures détaillées par REF (comptes uniquement) : cf. la matrice d'exécution du
job CI et l'annexe A du guide de validation (aucune valeur métier, uniquement
clés de champs, pages, zones, confiances).

## 2. Ce qui a été livré

### Lot 1 — gabarits atelier et classification (commit `49876b1`)

- `seamtech_search/fiches/extraction.py` : extraction pilotée gabarit enrichie
  (tissu sous la ligne DATE du bloc central, goussets par paires, BDF par bord,
  points d'ancrage, zigzag, lattes GV, mesures libres RG6, anomalies honnêtes).
- `seamtech_search/fiches/gabarits.py` : gabarits embarqués
  `FICHE_JADE_V1` (fiches atelier standard) et `FICHE_GV_FULLBATTEN_V1`
  (grand voile lattée, sections Ris 1-3 et lattes).
- `seamtech_search/anchors.py` : ancres de détection génériques.
- `config/lexique_fiches.json` v3 : libellés de voilerie observés (type de
  voile, point d'ancrage, BDF, ralingue, sangle, amure, drisse, écoute, penons,
  zig zag, lattes, ris) — aucune valeur métier.
- Tests : `tests/test_detection_fiches.py` (classification 14/14),
  `tests/test_gabarits_corpus_atelier.py` (champs, zones, honnêteté RG16,
  absence d'invention).

### Lot 2 — job CI `recette-corpus-reel` (commits `425964d`, `e9cbbdf`, `9e1a424`, `e6aefc5`, `17870aa`, `316eecb`, `43789a1`, `17af0ee`, `cf5c68e`)

- `.github/workflows/ci.yml` : nouveau job dédié (services PostgreSQL
  `pgvector/pgvector:pg16` + Redis, MinIO construit depuis les sources
  archivées, buckets documents/backups) exécutant
  `tests/test_recette_corpus_reel.py` : 24 tests ordonnés 01→11 couvrant le
  flux réel complet, dont :
  - extraction des 7 ZIP vers `${{ runner.temp }}/corpus-recette` (jamais
    commités, jamais retouchés) ;
  - SHA-256 des ZIP avant/après, `if: always()` (RG13) ;
  - migrations 001→017 sur base neuve ;
  - Phase 0 réelle (upload multipart REF-001 avec chemins relatifs ;
    scan/confirm REF-002..007) ;
  - dépôt Lot C `/imports/dossier` × 7 (fiche + pièces, transaction unique) ;
  - classement + champs proposés : 7 fiches `a_valider`, gabarits, seuils de
    champs (≥ 20) et de confiance moyenne (≥ 0,8) par fiche ;
  - OCR par étages : 30 pages natives NON océrisées (motif « texte natif
    présent »), page rendue en image à 300 dpi océrisée par Tesseract `fra`
    avec mot retrouvé ;
  - recherches par référence, client, bateau, type, matière, accents (accentué
    vs sans accent) et multi-termes — chaque terme rattaché au document
    technique de SA fiche d'origine ; `/recherche` fiches `inclure_a_valider` ;
  - ouverture PDF : POST `/open` → 302 vers URL présignée MinIO RÉELLE
    (`X-Amz-Expires` ≤ 900 s), contenu téléchargé SHA-identique ;
  - rapport PDF de l'import téléchargé via `/imports/{id}/artifact/report_pdf` ;
  - sauvegarde (dump + SHA-256 + envoi bucket backups) → restauration en base
    NEUVE → fiches/object_key/recherche/`/open` revérifiés sur la restaurée ;
  - échec explicite par étape, aucune valeur métier dans les journaux
    (assertions du module sans valeur par construction), aucun artefact publié.
- `tests/test_selection_ci.py` : inventaire S3 réel (24 ids) + interdiction des
  sélections `-k "not …"`.
- `tests/test_construire_image_minio.py` : garde d'ordre des appels (3).
- Correctifs découverts par la CI elle-même (chaque échec diagnostiqué via
  annotations, jamais par affaiblissement de test) :
  1. `${{ runner.temp }}` dans `env:` au niveau JOB invalide le workflow
     (contexte `runner` indisponible) → passé en env niveau ÉTAPE (`e9cbbdf`).
  2. Table `gabarit` vide sur base CI neuve (l'amorçage n'était fait que par le
     CLI) → `initialiser_gabarits` dans la fixture `app_client` — geste
     opérateur du CLI, sans changement de backend (`e6aefc5`).
  3. `UniqueViolation fiche_galon (id_fiche, bande)` : le handler BDF créait
     une ligne par bande de force du même bord → dédup par bande, les traces
     distinctes portent le détail (`17870aa`).
  4. `float()` aveugle sur la valeur normalisée de toute mesure libre
     (persistance) : la ligne LATTES complète de la fiche GV (REF-006),
     stockée en texte par la voie RG6, faisait échouer son dépôt.
     `_valeur_numerique_libre` : la colonne numérique ne reçoit un nombre QUE
     si la valeur en contient exactement un ; sinon NULL, le texte intégral
     reste en `valeur_texte` — jamais d'exception, jamais d'invention
     (`316eecb`, régressions unitaires + intégration PostgreSQL).
  5. Trois défauts du module de recette lui-même : table d'accents
     `maketrans` inégale (ValueError), client boto3 nu au lieu du
     `S3StorageClient` du projet (TypeError dans la sauvegarde), et URL
     d'artefact au singulier alors que la route réelle est
     `/imports/{id}/artifacts/{artifact}` — le 404 du rapport PDF était un
     bug de TEST, pas un écart produit : repro locale complète
     (upload → scan → confirm → GET) confirme que le rapport est généré,
     persisté et servi (200, `application/pdf`, signature `%PDF-`)
     (`316eecb`).
  6. Confidentialité CI : les messages d'exception recopient parfois le texte
     des documents (la ligne LATTES avait fui dans les annotations du dépôt
     public) → tout contenu entre guillemets est retiré des annotations et
     résumés publiés, troncature systématique, raison de refus assainie à la
     source dans le test ; résumé de diagnostic garanti par une étape dédiée
     `if: always()` (`316eecb`).
  7. Alignement du module sur deux contrats produits, prouvé sur le corpus
     réel (`43789a1`, `17af0ee`, `cf5c68e`) :
     - un dossier peut porter PLUSIEURS fiches (le scan Phase 0 et le dépôt
       Lot C ne retiennent pas forcément la même) : l'attendu d'une recherche
       est le PDF SOURCE DE LA FICHE DÉPOSÉE (exposé par
       `/fiches/{code}/pieces`), comparé par nom de fichier (l'exemplaire
       indexé de la REF importée par upload est la copie stagée) ;
     - le classement du produit met les correspondances de NOM avant celles
       de contenu : ~170 fichiers de découpe préfixés du type de voile
       classent la fiche 141e sur 143 pour ce terme — le test pagine les
       résultats (l'exigence de retrouvaille est inchangée) ;
     - RG3 en lecture : une fiche A_VALIDER n'est PAS cherchable par texte
       (son search_vector n'est rempli qu'à la validation humaine,
       migration 007 ; preuve du raccordement : test_validation_rend_cherchable).
       La recette vérifie désormais cette NON-cherchabilité (contrat réel) ;
     - ouverture sur base restaurée : l'app restaurée doit garder les MÊMES
       racines de recherche que la session d'origine (le technique de REF-001
       est la copie stagée — sinon /open répond 403 « outside configured
       search roots »).

### Lot 3 — guide de validation humaine (commit `2235c24`)

- `docs/verite_terrain/GUIDE_VALIDATION_HUMAINE_CORPUS.md` : stack locale
  docker compose, comptes nominatifs obligatoires, dépôt des 7 dossiers,
  contrôle par champs (vigilances unités décimales françaises, accents
  « Génois », zones, goussets par paires), limites connues (bande UV hors
  périmètre, confiance 0,70 du type, tissu honnêtement absent pour certaines
  fiches), correction avec attribution nominative + contrôle secondaire
  quatre yeux, passage `a_valider` → `validé`, rappel que
  `SUIVI_VALIDATION_REFERENCE.csv` reste le modèle contractuel, et annexe A :
  récapitulatif anonymisé des propositions machine par REF (clés, pages,
  zones, confiances — aucune valeur métier).

## 3. Fichiers modifiés (base `0706733` → `cf5c68e` + rapport)

```
.github/workflows/ci.yml                       | 243 +++++++++++++
config/lexique_fiches.json                     |  41 +-
docs/verite_terrain/GUIDE_VALIDATION_HUMAINE_CORPUS.md | 396 +++++++++
pyproject.toml                                 |   1 +
seamtech_search/anchors.py                     |  43 ++
seamtech_search/fiches/extraction.py           | 632 +++++++++++++++-
seamtech_search/fiches/gabarits.py             | 100 ++-
seamtech_search/fiches/persistance.py          |  32 + (valeur numérique libre défensive)
tests/test_construire_image_minio.py           |   8 +-
tests/test_detection_fiches.py                 |  47 +-
tests/test_fiches_persistance.py               |  52 + (régression mesures libres textuelles)
tests/test_gabarits_corpus_atelier.py          | 570 +++++ (dont 4 tests de la règle numérique)
tests/test_recette_corpus_reel.py              | 810 +++++++++ (24 tests, flux réel complet)
tests/test_selection_ci.py                     |  28 +
```

Aucun PDF extrait, aucun manifeste détaillé, aucune valeur métier commités.
Les 7 ZIP originaux n'ont jamais été retouchés (RG13 vérifié à chaque run).

## 4. Limites

- **Corpus : 7 ZIP fournis uniquement** (14 PDF uniques, 30 pages, ~30 000
  caractères natifs, aucun PDF image natif) — aucune extrapolation.
- **0 validation humaine réalisée** : les 7 fiches restent `a_valider` ; ce
  rapport ne vaut pas validation. Le guide du Lot 3 décrit le parcours.
- L'OCR d'étage 3 est prouvé sur une page du corpus rendue en image (le corpus
  ne contient pas de PDF image natif) — pas de preuve OCR sur un scan client
  réel non fourni.
- Les recherches par mots-clés sont éprouvées sur les familles présentes en
  base (référence, client, bateau, type, matière, accents, multi-termes) ;
  d'autres vocabulaires ne sont pas extrapolés.
- La restauration est prouvée base neuve dans le même environnement CI ; les
  procédures de restauration d'exploitation restent dans `RUNBOOK_RESTAURATION.md`.

## 5. Preuves

- Suite locale : **770 passed / 0 failed** (ruff clean, gardes
  `test_selection_ci` et `test_construire_image_minio` vertes).
- Empreintes SHA-256 des 7 ZIP : identiques avant/après chaque run CI (étapes
  RG13 `if: always()`, visibles dans chaque run du job `recette-corpus-reel`).
- **Run final 36495120848 (commit `cf5c68e`) : 10/10 jobs VERTS**, dont
  `recette-corpus-reel` — extraction gardée (429 fichiers, 7 dossiers),
  empreintes ZIP identiques avant/après (RG13, étapes vertes), migrations
  001→017, flux réel complet, purge. Les jobs existants (backend ×3,
  integration, e2e, ocr, frontend, docker, sauvegarde) restent verts à
  CHAQUE itération.
- Itérations de diagnostic (chacune par annotations, jamais par affaiblissement
  de test) : 36484436278 (workflow invalide), 36485707670 (gabarits vides),
  36487210069 (doublon galons), 36488649403 (5 échecs, 4 causes),
  36492288670 (2 échecs), 36493512241 (2 échecs), 36494469146 (1 échec),
  36495120848 (VERT).
- Diagnostics intermédiaires : les échecs des runs précédents sont documentés
  dans les messages de commit (`e9cbbdf`, `e6aefc5`, `17870aa`, `316eecb`) —
  chaque correctif a suivi une cause racine identifiée, jamais un
  contournement. Trois de ces causes étaient de VRAIS défauts moteur que
  seules les données réelles et une vraie base pouvaient révéler (table
  gabarits non amorcée par create_app, doublon de lignes fiche_galon par
  bande, float() sur une valeur textuelle) — c'est la preuve d'utilité du
  job de recette.

## 6. Décision demandée au commanditaire

La PR reste **ouverte, non fusionnée** (aucune fusion sans décision explicite).
Deux décisions restent au commanditaire :

1. fusionner la branche de recette ;
2. lancer la validation humaine des 7 fiches selon le guide du Lot 3 (comptes
   nominatifs, quatre yeux, `SUIVI_VALIDATION_REFERENCE.csv`).
