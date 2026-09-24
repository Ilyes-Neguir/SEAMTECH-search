# Rapport — préparation sans archive réelle (2026-09-24)

Branche : `arena/01a0d36d-seamtech-search` (base `main` = `4b799a63904b847214fff7c6604b5942b30ae4d6`)
Lot : **préparation opérationnelle** (arrivée archive, poste Windows, validation
humaine, vérification fixtures, garde-fous) — **Lot G NON VALIDÉ : l'archive
réelle et les PDF de production ne sont PAS arrivés.**

> RÈGLE ABSOLUE DE CE RAPPORT : chaque affirmation est adossée à une commande
> et sa sortie brute. Toute mesure obtenue sur `sample_data`, `tests/fixtures`
> ou `fake_tesseract` est une **preuve synthétique** (catégorie A) et ne vaut
> JAMAIS pour l'archive réelle (catégorie B).

---

## 0. Environnement d'exécution (limites)

| Élément | État dans ce bac à sable | Conséquence |
|---|---|---|
| Python | 3.11.2, venv `.venv-seamtech`, `requirements-dev.txt` installé | OK |
| PostgreSQL | **absent** (ni serveur ni client) | tests `-m postgres` non exécutés ici (174 skips sur la commande CI), couverture globale sous le plancher — CI avec PostgreSQL = juge de référence |
| Docker | **absent** | `test_integration_docker` skipped (2) |
| Tesseract | **absent** | 3 skips qualité OCR ; chemins heureux couverts par `fake_tesseract` |
| Node / pnpm | node + pnpm 9.15.9 (corepack) | build frontend possible |
| Playwright navigateurs | non installés | E2E inventoriés seulement (34 tests listés, 0 exécuté) |

---

## 1. Commandes exécutées et sorties réelles

### 1.1 Suite complète hors PostgreSQL / perf / s3 / sauvegarde

```
$ python -m pytest -q -m "not postgres and not perf and not s3 and not sauvegarde"
...
641 passed, 5 skipped, 221 deselected, 1 warning in 35.74s
```

Non-régression : **avant** ce lot, même commande → `568 passed, 5 skipped,
221 deselected` (sortie capturée en début de session). Après : `641` = 568
existants + **73 nouveaux** tests (39 préflight + 14 script Windows + 20
garde-fous) — **0 échec, 0 régression**.

Tests ignorés (5) — `pytest -rs` :

```
SKIPPED tests/test_integration_docker.py:67: Docker not available in this environment
SKIPPED tests/test_integration_docker.py:210: Docker not available in this environment
SKIPPED tests/test_ocr_etages.py:136: tesseract absent — OCR optionnel au runtime, statut unavailable attendu
SKIPPED tests/test_ocr_etages.py:236: tesseract absent — qualité non mesurable localement, sera mesurée en CI
SKIPPED tests/test_ocr_etages.py:257: tesseract absent
```

### 1.2 Commande CI de couverture (identique à `.github/workflows/ci.yml`)

```
$ python -m pytest -q -k "not s3" -m "not perf" --cov=seamtech_search --cov-report=json:coverage.json
675 passed, 174 skipped, 18 deselected, 1 warning in 39.93s
$ python scripts/coverage_gate.py coverage.json
Overall coverage: 69.6% (floor: >= 85%)
seamtech_search/api.py: 87.4% (gate: >= 87%) [ok]
seamtech_search/import_pipeline.py: 90.7% (gate: >= 90%) [ok]
seamtech_search/indexer.py: 89.9% (gate: >= 90%) [FAIL]
seamtech_search/jobs.py: 94.0% (gate: >= 94%) [ok]
seamtech_search/ocr/cli.py: 79.8% (gate: >= 40%) [ok]
seamtech_search/ocr/etat.py: 77.2% (gate: >= 55%) [ok]
seamtech_search/ocr/inventaire.py: 84.9% (gate: >= 80%) [ok]
seamtech_search/ocr/pipeline.py: 79.4% (gate: >= 54%) [ok]
seamtech_search/redis_store.py: 92.2% (gate: >= 92%) [ok]
seamtech_search/storage.py: 97.5% (gate: >= 97%) [ok]
seamtech_search/worker.py: 92.5% (gate: >= 92%) [ok]
Coverage gate FAILED:
  - overall coverage 69.6% is below the 85% floor
  - seamtech_search/indexer.py coverage 89.9% is below the 90% gate
```

**Lecture honnête** : ce résultat est **attendu dans cet environnement** — les
174 tests sautés (couche métier PostgreSQL-only, plan §17.1 : fiches, comptes,
dédup, recherche, assistant…) sont ceux qui portent la couverture des modules
`indexer.py` (chemins PG) et du reste du paquet. La mesure de référence reste
la CI `backend` AVEC PostgreSQL (historique : 86.68 % puis ≥ 85.0 % — voir
commentaires de `scripts/coverage_gate.py`). **Aucun seuil n'a été abaissé,
aucun pragma ajouté** — le gate reste en échec local tant que PostgreSQL n'est
pas disponible, c'est documenté ici comme limite d'environnement, pas comme
régression.

### 1.3 Ruff

```
$ ruff check .
All checks passed!
```

### 1.4 Audit projet

```
$ python scripts/audit_projet.py --rapide
...
6. Source unique de vérité terrain (74 cibles)
  [OK  ] seamtech_search/fiches/verite_7792.py présent
  [OK  ] source unique contient 74 cibles — 74 cibles
  [OK  ] docs/ ré-exporte sans dérive — égalité stricte
  [OK  ] JSON attendu == VERITE_7792 (égalité stricte 74 cibles) — 74 cibles conformes
==========================================================================
BILAN : 12/12 contrôles verts
==========================================================================
```

### 1.5 Préflight d'arrivée d'archive — démonstration sur fixtures (SYNTHÉTIQUE)

Refus propres (chaque code, vraie sortie) :

```
$ python3 scripts/preflight_archive.py preflight --travail … --sortie …        # pas de --source
code=2        # refus : chemin source absent ou vide — sample_data JAMAIS substitué
$ python3 scripts/preflight_archive.py preflight --source "" …
code=2        # refus : chemin vide
$ python3 scripts/preflight_archive.py preflight --source /tmp/…/inexistante …
code=3        # refus : source inexistante
$ python3 scripts/preflight_archive.py preflight --source sample_data … --sortie sample_data/rapports_interdits
code=4        # refus : sortie située dans la source
$ python3 scripts/preflight_archive.py preflight --source sample_data … --echantillon-pdfs 25 --echantillon-dossiers 6
code=5        # refus : configuration ambiguë (2 modes d'échantillon)
$ … sans tesseract/pdftoppm …
code=6        # refus : prérequis outils manquants
$ … --min-libre-o 1000000000000000000 …
code=7        # refus : espace disque insuffisant
$ … --staging (sans postgres) …
code=8        # refus : PostgreSQL demandé mais absent
$ … empreintes (SHA/size modifié) …
code=9        # refus : empreintes non conformes (mtime seul → code 0 + avertissement)
```

Vert sur fixtures locales (`sample_data`, binaires `tesseract`/`pdftoppm`
fakes pour la démo — **ce n'est PAS du tesseract réel**) :

```
$ python3 scripts/preflight_archive.py preflight --source sample_data --travail … --sortie … --min-libre-o 0
SEAMTECH — préflight preflight — 2026-09-24T13:43:08+00:00
  [OK  ] Chemin source fourni explicitement — --source renseigné
  [OK  ] Configuration non ambiguë — un seul mode d'échantillonnage, source déclarée une seule fois
  [OK  ] Travail et sortie distincts de la source — aucun répertoire inclus dans l'autre
  [OK  ] Source existante (répertoire) — /home/user/SEAMTECH-search/sample_data
  [OK  ] Source lisible — lecture seule accessible
  [OK  ] Présence de fichiers dans la source — 6 fichier(s), 3 PDF
  [AVERT] Absence de droit d'écriture dans la source — … verrouillez les droits si possible
  [OK  ] Espace disque disponible — 19.1 Go libres …
  [OK  ] Python présent — … Python 3.11.2
  [OK  ] Tesseract présent — /tmp/demo_rapport/bin/tesseract            # FAKE
  [OK  ] Langue française (tesseract fra) — langues : eng, fra          # FAKE
  [OK  ] pdftoppm (ou équivalent) présent — /tmp/demo_rapport/bin/pdftoppm  # FAKE
  [N/D  ] PostgreSQL présent (indexation staging) — staging non demandé
  [OK  ] Version du dépôt — commit 4b799a6… (paquet 0.4.0)
  [OK  ] Version du schéma — 017_ocr_etage3
  [OK  ] Configuration OCR utilisée — tesseract 5.3.4-fake, langue fra, 300 dpi, seuil 20
  [AVERT] Échantillon de travail (sans traitement massif) — mode pdf : 3 élément(s) …
Code de sortie : 0 — préflight vert
```

Rapports produits : `preflight_rapport.json`, `preflight_rapport.txt`,
`echantillon.json` (+ `empreintes_rapport.json/txt` pour la re-vérification).

### 1.5 bis Tests sauvegarde / restauration

```
$ python -m pytest tests/test_sauvegarde_restauration.py tests/test_sauvegarde_unites.py -m "not s3" -q -rs
16 passed, 7 skipped in 0.05s
SKIPPED tests/test_sauvegarde_restauration.py: …: Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests  (×7)
```

Les 7 sauts sont les parcours aller-retour PostgreSQL (démarcation →
restauration) qui exigent une base vivante — non exécutables ici, CI comme
juge ; l'épreuve de restauration « réelle » reste en catégorie B.

### 1.6 Scénarios reprise / idempotence / verrou / budget / empreintes

```
$ python -m pytest -k "idempotence or reprise or verrou or budget or empreinte" -q
41 passed, 15 skipped, 811 deselected, 1 warning in 2.57s
```

(les 15 skips = tests `-m postgres` de la sélection ; le noyau OCR/reprise
passe : `test_verrou_stale_et_corrompu`, `test_etat_corrompu_et_sauvegarde`,
`test_reprise_apres_interruption_et_empreinte_inchangee`,
`test_reprise_apres_modification_empreinte`,
`test_reprise_apres_sigterm_simulation`, `test_cli_nuit_budget_atteint`,
`test_idempotence_meme_empreinte_jamais_retraite`, …)

### 1.7 RG13 / RG14

```
$ python -m pytest -k "rg13 or rg14 or reseau or inchangee or identique" -q
19 passed, 4 skipped, 844 deselected, 1 warning in 1.43s
```

- **RG13** (aucune écriture source) : SHA-256 + mtime avant/après sur
  `test_rg13_aucune_ecriture_dossier_source`, `test_rg13_source_intacte_apres_preflight_et_empreintes`,
  `test_rg13_source_fixtures_locales_intacte`, `test_rg13_bout_en_bout_source_intacte`,
  `test_archive_inchangee_apres_inventaire` — égalité stricte exigée.
- **RG14** (aucun réseau) : nouveau contrôle **AST répo-large**
  (`test_rg14_aucune_dependance_reseau_repolarge`) sur `seamtech_search/` +
  `scripts/` : ni `requests`, ni `httpx`, ni `urllib.request`, ni `socket`, ni
  `http.client`… hors 2 exceptions **documentées et testées comme telles** :
  `seamtech_search/ml/telecharger.py` (téléchargement EXPLICITE poids e5,
  commande opérateur, jamais au runtime) et `scripts/mesure_assistant.py`
  (outil de mesure qui prouve l'absence de réseau). `urllib.parse` (analyse
  d'URL sans réseau, `sauvegarde.py`) est admis et testé. Complété par les
  contrôles existants par module (ocr, comptes, dédup, qualité, assistant).

### 1.8 Tests par famille (collecte `--collect-only -q`)

| Famille | Commande | Tests |
|---|---|---|
| OCR | `pytest tests/test_ocr_comportement.py tests/test_ocr_etages.py --collect-only -q` | 55 |
| pipeline/import | `pytest tests/test_import_*.py tests/test_lot_ingestion.py …` | 36 |
| extraction | `pytest tests/test_extractors.py tests/test_extraction_fiche_reference.py tests/test_validate_extraction*.py` | 60 |
| recherche + indexation | `pytest tests/test_recherche_*.py tests/test_indexer*.py` | 54 |
| facettes dimensions | `pytest tests/test_facettes_et_suggestions.py` | 10 |
| déduplication | `pytest tests/test_dedup.py` | 14 |
| comptes | `pytest tests/test_comptes.py` | 18 |
| tableau de bord | `pytest tests/test_qualite_tableau.py tests/test_report_layout.py` | 33 |
| sauvegarde/restauration | `pytest tests/test_sauvegarde_*.py` | 23 |
| migration | `pytest tests/test_migrations_metier.py tests/test_startup_migration_failure.py` | 18 |
| préflight + garde-fous (ceci) | `pytest tests/test_preflight_*.py tests/test_garde_fous_preparation.py` | 73 |

### 1.9 Build frontend

```
$ cd frontend && pnpm install --frozen-lockfile && pnpm build
▲ Next.js 16.3.5 (Turbopack)
✓ Compiled successfully in 300ms
  Finished TypeScript in 2.9s ...
… (routes /login /recherche /validation /qualite … générées)
```

### 1.10 E2E Playwright — inventaire seulement

```
$ cd frontend && pnpm exec playwright test --list
Total: 34 tests in 6 files
```

Non exécutés ici : navigateurs Playwright absents + pile Docker requise
(environnement sans Docker). Ils tournent en CI dédiée quand la pile est levée.

---

## 2. A. Preuves synthétiques reproductibles (ÉTABLI)

| Preuve | Source | Reproductible par |
|---|---|---|
| 641 tests verts hors PG | fixtures du dépôt | §1.1 |
| Préflight vert + échantillon + empreintes | `sample_data` (6 fichiers synthétiques) | §1.5 |
| Refus codes 2→9 | arborescences `tmp_path` synthétiques | `tests/test_preflight_archive.py` (39 tests) |
| Script Windows non destructif | tests de contenu | `tests/test_preflight_windows.py` (14 tests) |
| RG13/RG14/logs/config | corpus synthétique + analyse AST | `tests/test_garde_fous_preparation.py` (20 tests) |
| OCR étages / reprise / verrou / budget | `fake_tesseract`, PDF `ocr_propre/ocr_degrade` (seed 42) | `tests/test_ocr_*.py` |
| Extraction réglée 6/6 | fiche 7792-SO **déjà versionnée** (document client de développement, RG13 : l'archive reste la source de vérité) | `tests/test_extraction_fiche_reference.py`, audit 12/12 |
| Build frontend | code du dépôt | §1.9 |

## 3. B. Preuves encore absentes (BLOQUÉ sur données réelles)

| Preuve manquante | Pourquoi | Quand |
|---|---|---|
| **Lot G validé** | archive réelle et PDF de production **absents** | à leur arrivée |
| Volume réel (50+ Go attendu) | jamais vu | idem |
| OCR réel sur le fonds | `fake_tesseract` ≠ tesseract 5.x sur vrais scans ; débits réels = mesures CI sur 2 échantillons commités uniquement | idem |
| Taux d'extraction réel | 1 seule fiche client réelle à ce jour (7792-SO) ; gabarits du fonds inconnus | idem |
| Validation humaine réelle (chrono 2 min/fiche) | 0/3 fiches mesurées (dernier verrou Phase 1) | idem |
| Épreuve de restauration réelle (R2/S3) | non exécutée | avant mise en service |
| Couverture ≥ 85 % AVEC PostgreSQL | ce bac n'a pas PostgreSQL ; CI = référence | prochain run CI |
| E2E Playwright | navigateurs + stack Docker absents ici | CI dédiée |

---

## 4. Problèmes éventuels rencontrés et sorties

1. **Couverture locale < plancher (§1.2)** : limite d'environnement (pas de
   PostgreSQL) — **seuils intacts**, gate volontairement laissé rouge local.
2. **Fuite potentielle d'identifiants dans les erreurs de staging OCR** :
   `ocr/cli.py` renvoyait `str(exc)` nu sur stderr et dans le compte rendu
   (un message psycopg2 « invalid dsn » peut échoïr la chaîne reçue). Corrigé
   par `_masquer_identifiants()` (redige `scheme://user:**@host`) avec tests
   `test_masquer_identifiants_redige_le_mot_de_passe_url` et
   `test_erreur_staging_base_ne_revele_pas_les_identifiants`. Aucun autre
   comportement touché (comptes rendus OCR inchangés hors message d'erreur).
3. **Droits d'écriture sur `sample_data`** en démo (avertissement préflight) :
   comportement voulu (avertissement, pas refus — RG13 tenu par le code,
   l'idéal opérationnel est une source montée en lecture seule, documenté).

## 5. Tâches restant dépendantes des PDF réels

1. Préflight réel (`docs/ARRIVEE_ARCHIVE.md` §4) sur le support livré.
2. Échantillon 20-30 PDF / 5-10 dossiers, empreintes, validation des empreintes.
3. Inventaire complet (`scripts/inventaire_archive.py`) → familles de gabarits
   réelles.
4. Mesure OCR réelle (débit pages/min sur le fonds) et extraction réelle.
5. Campagne de validation humaine (protocole §1-§12) avec comptes nominatifs.
6. Épreuve de restauration réelle (R2/S3) — `RUNBOOK_RESTAURATION.md`.
7. Décision écrite « traitement complet autorisé » (procédure §7) avant tout
   lot de fonds.

---

## 6. Bilan

**VALIDÉ AVEC RÉSERVES** — réserves = catégorie B uniquement (données
absentes, indépendantes de la qualité de la préparation). Sur périmètre
préparatoire : préflight (refus propres codes 2-9, rapports JSON+texte,
échantillon borné, empreintes SHA-256/taille/mtime), script Windows non
destructif testé, protocole + modèles de validation prêts, 641 tests verts
0 régression (647 après complément CI §7), audit 12/12, Ruff clean, build
front OK, garde-fous RG13/RG14/logs/config étendus **sans supprimer ni
abaisser aucun garde-fou existant**.

---

## 7. Complément — préparation CI (2026-09-24, même jour)

Postérieurement au §1, la validation CI a été préparée (branche poussée, PR vers
`main`, base `4b799a6` = fusion Lot M) :

- CSV de suivi réencodé **utf-8-sig** (BOM EF BB BF présent — exigence
  documentée, désormais testée) ; contenu inchangé.
- Tests dédiés ajoutés : nombre/ordre exact des 15 colonnes, encodage
  utf-8-sig, séparateur `;` (et non la virgule), lecture `csv.DictReader`,
  absence de chemin absolu, sortie dans un **sous-sous-répertoire** de la source.
- Reprise de la suite : `647 passed, 5 skipped, 221 deselected` (641 + 6
  nouveaux tests — 0 échec). Ruff `All checks passed!`, audit `12/12`,
  `git diff --check` propre, build front `✓ Compiled successfully`.
- La couverture AVEC PostgreSQL sera jugée par le job CI `backend` (seuils
  intacts) ; le §1.2 reste valable pour l'environnement local sans PostgreSQL.
- **7.1 — Rupture de distribution MinIO (2026-09-24, pendant l'attente
  CI)** : les jobs `integration` et `sauvegarde` ont d'abord échoué (pull
  quay.io → `minio Error unauthorized: access to the requested resource is
  not authorized`, étape `docker run` en exit 125). Cause : le dépôt
  `quay.io/minio/minio` a été **supprimé dans la journée** —
  `https://quay.io/repository/minio/minio` → « Repository not found »
  (main vert à 12:26 UTC sur les mêmes pulls, échec à 13:55) ; `dl.min.io`
  répond « 410 Gone — … projects are archived and no longer maintained » ;
  `docker.io/minio/minio` était retiré depuis le 2026-09-11 ;
  `github.com/minio/minio` est archivé (lecture seule) depuis le 2026-04-25.
  Correctif : image reconstruite depuis les sources officielles du **même
  tag** (`scripts/construire_image_minio.sh` : minio
  `RELEASE.2025-09-07T16-13-09Z` + mc `RELEASE.2025-08-13T08-35-41Z`),
  consigne des auteurs respectée (« clone the source and build the latest
  container »). Preuves = pages ci-dessus, récupérées le 2026-09-24.
  Lot G réel : toujours bloqué (archive/PDF de production non reçus) —
  indépendant de cette rupture d'infrastructure.
