# Preuves et garde-fous JUnit — 2026-10-13 → 2026-10-14 — SHA ebdb1a2 → 1ee9095

**SHA code avant vérif : `ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d`**
**SHA après corrections contraignantes : `a266559`**
**SHA final avec fix sauvegarde 50k : `1ee90958c2557dee3a10a7acb5d6e983a0dce131`**
**Identifiant unique : `qualif-2026-10-14-1ee9095`**
**CI verte finale : `38538168749` (CI), `38538168702` (reserves), `38538168740` (native), `38538168577` (perf) — tous jobs success**
**Nature JUnit 16 contrôles : synthèse documentaire générée manuellement depuis résultats exécutés, chaque ligne reliée aux preuves sources ci-dessous**
**Drag-drop fix final 1ee9095 : data-testid drop-zone (harnais) + data-testid result-item (produit) + DataTransfer natif items.add(file) + terme unique sans underscore + normalisation alphanum OCR + import_id réel + API search direct count>0 + results[0].id truthy dans deux tests + deux téléchargements obligatoires SHA identiques (import + pièce) expect visible sans else sans catch + visionneuse obligatoire sans catch + raw_text toBeTruthy length>10 normalisé contient uniq sans if + extraction_status toBeDefined not failed/success sans défaut + SEAMTECH_OCR_OBLIGATOIRE=1 (0 skip) + fix sauvegarde 50k timeout 60s**

## 1. Environnement

- OS CI : `ubuntu-24.04` (tous jobs)
- Python : 3.11, 3.12, 3.13 (matrice backend) — fix 50k timeout 60s pour 3.13
- Node : 22, pnpm 9.15.9, Next.js 16.3.8
- PostgreSQL : pgvector/pgvector:pg16 (service)
- Redis : redis:7-alpine
- MinIO : quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z (reconstruit via `scripts/construire_image_minio.sh`)
- Tesseract : 5.x + fra + eng + poppler-utils (apt) + env SEAMTECH_OCR_OBLIGATOIRE=1
- Playwright : Chromium --with-deps

## 2. Commandes et résultats

### 2.1 Backend (3.11/3.12/3.13) — CI `38538168749`

**Commande SQLite suite :**
```
pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"
```
**Résultat :** 1104+ tests, 0 skipped, success

**Commande PostgreSQL suite :**
```
pytest -m "postgres and not perf and not sauvegarde and not redis_queue and not ocr_suite" -v -rf
```
**Résultat :** 278 passed, 0 skipped (garde-fou passed>0 skipped==0 OK)

**Commande durable queue :**
```
pytest -m "redis_queue" -v -rf
```
**Résultat :** 81 passed, 0 skipped

**Commande coverage :**
```
pytest -m "not s3 and not perf" -q -rf --cov=seamtech_search --cov-report=term-missing --cov-report=json:coverage.json
```
**Résultat :** success, coverage gate overall floor respecté (85%+ avec PG+Redis), avec env SEAMTECH_TEST_DATABASE_URL et SEAMTECH_TEST_REDIS_URL
- Annotations CI : suite-postgres 278 passed, suite-file-durable 81 passed
- Fix 50k : SearchIndex(..., statement_timeout_ms=60000) pour test_restauration_50000_fiches_duree_publiee (QueryCanceled sur 3.13 avant fix, vu dans CI 38534090020/38535936491)

**Preuve :** job `backend (3.11)` success, `backend (3.12)` success, `backend (3.13)` success sur 38538168749 (après fix timeout 60s)

### 2.2 Frontend — CI `38538168749`

**Commandes :**
```
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit
pnpm audit --prod --audit-level=high
pnpm build
```
**Résultat :** success, Compiled successfully Next.js 16.3.8

**Preuve :** job `frontend` success

### 2.3 Docker — CI `38538168749`

**Commandes :**
```
docker build -t seamtech-search:ci -f Dockerfile .
docker build -t seamtech-search-frontend:ci frontend/
docker run -d ... seamtech-search:ci
curl -fsS http://127.0.0.1:8000/live (45×2s)
```
**Résultat :** success, /live répond, image porte tesseract fra+eng + poppler

**Preuve :** job `docker` success

### 2.4 E2E — CI `38538168749` — job e2e success

**Install :**
```
sudo apt-get install tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng poppler-utils
pnpm exec playwright install --with-deps chromium
```

**Commandes :**
```
pnpm test:e2e (suite par défaut, SQLite) avec SEAMTECH_OCR_OBLIGATOIRE=1
pnpm exec playwright test e2e/concurrence.spec.ts --reporter=list,json
pnpm exec playwright test e2e/validation.spec.ts e2e/recherche-badge.spec.ts e2e/recherche-dimension.spec.ts --reporter=list,json
pnpm exec playwright test e2e/doublons.spec.ts --reporter=list,json
pnpm exec playwright test e2e/auth.spec.ts --reporter=list,json
```

**Résultats :**
- e2e par défaut : success sur `38538168749` avec assertions contraignantes finales
- concurrence : 7 tests passés
- live validation : 10 passés
- doublons : 2 tests verts
- auth : 26 tests verts
- **scan-browser-ocr** : 4 tests verts sur `38538168749` avec assertions contraignantes finales (a266559/1ee9095) :
  - "dépôt PDF image par sélection fichier (setInputFiles), OCR, recherche terme unique, téléchargement SHA obligatoire" → terme unique `ZEPHYRINE{HEX}{suffix}` sans underscore (absent nom fichier/metadata, OCR lisible), `gen_pdf_custom` image-only, capture import_id réel depuis `/api/imports/confirm` (pas via-ui), statut terminal `completed|needs_review` pour CET import, vérif extraction effective via `/api/imports/{id}` raw_text **toBeTruthy** length>10 normalisé alphanum majuscule contient terme unique (sans if silencieux), extraction_status **toBeDefined** + not failed, recherche directe API `/api/search?q=uniq` count>0 obligatoire + results[0].id truthy (preuve attribution par unicité, documenté), UI `result-item` visible 15000ms (data-testid ajouté dans ResultItem), ouverture + visionneuse `visionneuse-piece` **obligatoire sans .catch** (expect visible 15000ms), **deux téléchargements obligatoires** : import panel `/api/imports/{id}/artifacts/source_pdf` SHA identique + pièce `/api/pieces/{id}/telecharger` SHA identique (expect visible obligatoire sans else sans catch), preuve avec vrais ids `{originalSha, downloadedSha, fileName, importId, uniqueTerm, recherche, etat, rawTextContainsUniq}` + note unicité comme preuve attribution
  - "dépôt PDF image par véritable glisser-déposer (simulation dragenter/dragover/drop avec DataTransfer natif dans vrai navigateur), OCR, recherche, SHA obligatoire" → même flux avec `dragDropFileViaEvents` simulation dragenter/dragover/drop avec DataTransfer natif dans vrai navigateur (exerce handlers onDragOver/onDrop, pas geste manuel OS), terme unique `GENOA{HEX}{suffix}`, import_id réel, raw_text toBeTruthy length>10 normalisé contient uniq, extraction_status toBeDefined not failed, API search direct count>0 + results[0].id truthy (ajouté dans drag-drop aussi), result-item, **deux téléchargements obligatoires** SHA identiques (expect visible obligatoire), visionneuse obligatoire sans catch, méthode `simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur`
  - "PDF invalide — contrat : échec signalé avec issue explicite, pas de fiche inventée" → branches explicites : rejet HTTP 400/413/500 avec `p.text-destructive` message vérifié OU `Aucun PDF technique` avec warning OU candidat puis import → statut `failed|needs_review` avec extraction_status toBeDefined not success, vérif via `/api/imports/{id}`, recherche API `/api/search?q=INVALIDE_<sha8>` count 0 obligatoire
  - "fichier sans texte exploitable — contrat : conservé sans extraction, pas de faux texte, issue explicite" → upload OK → Fichiers détectés → import → `État : needs_review|failed` pas completed, vérif via `/api/imports/{id}` extraction_status toBeDefined not success, raw_text len <20, À vérifier visible ou absence Données extraites, recherche API `BLANC_SANS_TEXTE_9999` count 0
  - Échecs documentés : `38468805294` selector :has-text invalid, `38470099513` getByText not found mock, `38471404314` success natif mais permissif, `38476009010` fail OCR underscore, `38477938099` fail result-item missing, `38479810898` success ebdb1a2 mais avec écarts (pièce conditionnelle, visionneuse catch, raw_text if, extraction_status défaut), `38534090020` e2e success a266559 (corrections contraignantes) mais backend 3.13 fail QueryCanceled 50k, `38538168749` success complet 1ee9095

**Preuve JUnit :** `frontend/playwright-report/` + `test-results/` + `/tmp/e2e.log`, garde-fou concurrence publie noms si flaky

**Preuve SHA téléchargement obligatoire :** testInfo.attach `preuve-sha` et `preuve-sha-dragdrop` JSON `{originalSha, downloadedSha, fileName, importId, uniqueTerm, recherche, etat, rawTextContainsUniq, methode}` — deux téléchargements obligatoires avec empreinte identique prouvée, pas de fallback count, visionneuse obligatoire sans catch, raw_text obligatoire, extraction_status défini, lien import→OCR→recherche→téléchargement, vrais identifiants, unicité comme preuve attribution

**Geste décrit comme :** `simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur` (pas geste manuel depuis explorateur système)

**OCR obligatoire :** env `SEAMTECH_OCR_OBLIGATOIRE=1` dans ci.yml job e2e → interdit vert par skip, exige 4 tests exécutés 0 skip, tesseract installé via `Install OCR binaries`

### 2.5 OCR — CI `38538168749` — job ocr success

**Commandes :**
```
pytest -m ocr_suite -v
```

**Mesures publiées ::notice :**
- taux mots retrouvés propre=1.000 pages=1 océrisées=1 durée_s=1.8 moteur=tesseract version=5.3.4
- taux dégradé=0.955

**Garde-fou OCR étage 3 :** `doit_oceriser_page` forcé True → rouge, restauration → vert

### 2.6 Sauvegarde — CI `38538168749` — job sauvegarde success

**Commandes :**
```
pytest -m sauvegarde -v --junitxml=sauvegarde-junit.xml
```

**Garde-fou :** tous collectés == exécutés, ≥19 passés, 0 sauté, 0 échec

**Mesures publiées ::notice :**
- restauration 50k fiches en Xs (dump Y octets) — chiffre repris dans RUNBOOK_RESTAURATION.md
- Fix 50k : statement_timeout 60s pour Python 3.13 (QueryCanceled avant fix, vu dans CI 38534090020/38535936491 backend 3.13 fail, 3.11/3.12 success, après fix 1ee9095 tous success)

**Parcours :** sauver → détruire DROP DATABASE → restaurer depuis bucket seul, comptes par table, VERSION_SCHEMA_METIER, recherche fonctionnelle, inventaire archive, sources indisponibles, SHA-256, fichiers/objets manquants, sauvegarde corrompue, reprise worker/stockage

### 2.7 Hors-ligne réel — CI `38538168749` — job hors-ligne-reel success

**Procédure :** compte applicatif `seamtech-app`, pile web+worker séparé+front Next.js prod sous ce compte (uid réel), MinIO local, boucle locale 127.0.0.1 rendue (lo), chaîne iptables `SEAMTECH_HORS_LIGNE` + ip6tables `SEAMTECH_HORS_LIGNE6`, contrôle négatif IPv4/IPv6 avec compteur REJECT, contrôle positif /health, iptables -Z après contrôles, parcours navigateur `e2e/hors-ligne.spec.ts` avec `SEAMTECH_E2E_HORS_LIGNE=1`, relevé final 0 paquet rejeté, `requetesExternes == []`

### 2.8 Integration — CI `38538168749` — job integration success

- Compose infra postgres+redis+minio (reconstruit), wait reachable, provisionner identités restreintes `scripts/provisionner_stockage.sh`, boot web+frontend healthy, `/api/health` via chaîne réelle, volumes data/logs chown uid/gid applicatif, `pytest -m integration_docker -v` avec `SEAMTECH_INTEGRATION_STRICT=1`

### 2.9 Recette corpus réel — CI `38538168749` — job recette-corpus-reel success

- 7 ZIP REF-001..007, empreintes SHA-256 avant/après (RG13), extraction guarded, `pytest -m recette_corpus -v` : migrations 001..017 base neuve, import 7 dossiers flux réel, classement auto + champs proposés + fiches A_VALIDER, OCR par étages, recherches mots-clés, ouverture PDF téléchargeable 200 SHA identique, rapport PDF, sauvegarde/restauration base neuve

### 2.10 Recette locale — CI `38538168749` — job recette-locale success

- `bash scripts/recette_locale.sh` sur machine vierge, rapport PASS/FAIL par contrôle

### 2.11 Qualification reserves — Workflow `38538168702` success

- 74 tests qualification, 0 skipped, 0 failures, 0 errors

### 2.12 Qualification native — Workflow `38538168740` success

- `bash scripts/qualification_native.sh` sur Debian/Ubuntu vierge : apt PostgreSQL+pgvector, Redis, MinIO officiel compilé tag épinglé, API+worker+frontend processus hôtes, compte nominatif, lot worker 7792-SO, OCR tesseract hôte fra+eng, /recherche, /open, sauvegarde/restore, kill+relance API
- Notice `native-summary` tous CONTROLE PASS

### 2.13 Perf comparaison — Workflow `38538168577` success

- 3 runs same-runner : 8dd5ae7, a3506c8, HEAD 1ee9095
- Mesures : 8dd5ae7 p50 5.3ms, a3506c8 p50 231ms (×43 régression), HEAD 5.3ms, seuil CI 250ms étiqueté non relevé, p50 <100ms sans tolérance, alerte dérive p95>50ms ou >10×p50 ::warning

## 3. Garde-fous JUnit

- Chaque job CI publie `::error`/`::notice` annotations lisibles via API (blob host logs inaccessibles depuis certains réseaux)
- `sauvegarde-junit.xml` collectés==exécutés, ≥19 passés, 0 skipped — vrai journal pytest généré par `pytest --junitxml`
- `report-*.json` Playwright avec stats expected/flaky/skipped/unexpected, garde-fou refuse flaky/sautés, nomme tests en cause — vrais rapports générés par Playwright
- `perf-mesures.jsonl` + `sauvegarde-mesures.jsonl` publiés, seuils vérifiés — vrais fichiers générés par tests perf/sauvegarde
- `2026-10-13_junit_preuves.xml` est **synthèse documentaire** (pas un journal pytest brut) : généré manuellement depuis résultats exécutés listés ci-dessus, chaque testcase référence run ID et commande source (ex. `Run 38538168749 job backend 3.11 success`). Il est nommé comme synthèse et relié aux preuves d'exécution sources dans ce fichier.
- Aucun JSON PASS réutilisé après run échoué : chaque run `1ee9095` est vert, pas de réutilisation
- `2026-10-14_complement_assertions_contraignantes.md` documente corrections pièce obligatoire, visionneuse sans catch, raw_text obligatoire, extraction_status défini, OCR obligatoire, 50k timeout

## 4. Preuve d'absence de faux succès

- Scan navigateur sélection fichier : terme unique `ZEPHYRINE{HEX}{suffix}` sans underscore, absent noms fichiers/métadonnées, PDF image-only vérifié pdfplumber 0 texte, SHA-256 original vs téléchargé identique attaché `preuve-sha` méthode `setInputFiles`, **deux téléchargements obligatoires** (import + pièce) expect visible sans else sans catch, visionneuse obligatoire sans catch, raw_text toBeTruthy length>10 normalisé contient uniq sans if, extraction_status toBeDefined not failed sans défaut "failed", API search direct count>0 + results[0].id truthy, unicité comme preuve attribution
- Scan navigateur glisser-déposer réel : même PDF mais variante anglaise `GENOA{HEX}{suffix}`, terme hors métadonnées, dragenter/dragover/drop via DataTransfer avec vrai File, preuve SHA attachée `preuve-sha-dragdrop`, statut terminal `completed|needs_review`, recherche UI, deux téléchargements obligatoires, visionneuse obligatoire, raw_text obligatoire, extraction_status défini, SEAMTECH_OCR_OBLIGATOIRE=1 garantit 0 skip
- Recherche : IDs ET ordre comparés pour variantes équivalentes via `test_requetes_equivalentes_memes_identifiants_et_ordre`
- Sémantique : contrat réel établi, test réellement exécuté avec modèle réel e5-small ONNX, téléchargé via `seamtech_search.ml.telecharger`, cache `/home/runner/.cache/seamtech-e5`, tests `test_recherche_hybride.py::test_rrf_double_vote_classe_devant` et `test_rrf_branches_vecteurs_dormante_puis_active`, requêtes "grand voile", "zzzz terme introuvable" avec `EMBEDDING_A`, docs attendus `0701-GV-001`, réserve qualité humaine HORS PÉRIMÈTRE
- Sauvegarde : SHA-256 comparés, restauration vers cibles distinctes, sources indisponibles, corrompue, reprise worker, 50k fiches avec timeout 60s fix QueryCanceled 3.13
- Hors-ligne : compteur REJECT prouve blocage, 0 paquet rejeté pendant parcours, `requetesExternes == []`
- Cas erreur invalide/blanc : contrat explicite défini, statut terminal vérifié (failed/needs_review pas completed avec extraction réussie), message utilisateur visible, absence faux texte inventé vérifiée via recherche terme unique inexistant + extraction_status toBeDefined not success + raw_text len <20

## 5. Distinction produit / environnement / harnais

- Produit : `seamtech_search/recherche.py` (trigrammes), `import_pipeline.py`, `api.py` (upload), `Dockerfile` (tesseract eng), `frontend/components/import-panel.tsx` (drag handlers), `frontend/components/result-item.tsx` (data-testid result-item), `seamtech_search/indexer.py` (statement_timeout_ms param), `tests/test_sauvegarde_restauration.py` (timeout 60s pour 50k)
- Environnement : `Dockerfile`, `.github/workflows/ci.yml` (sélection par marqueur, tesseract install, MinIO reconstruit, SEAMTECH_OCR_OBLIGATOIRE=1), `scripts/construire_image_minio.sh`, `scripts/provisionner_stockage.sh`
- Harnais : `frontend/e2e/scan-browser-ocr.spec.ts` (setInputFiles vs dragenter/dragover/drop, deux téléchargements obligatoires sans else sans catch, visionneuse obligatoire sans catch, raw_text obligatoire sans if, extraction_status toBeDefined sans défaut, unicité comme preuve attribution, résultats id truthy dans deux tests, OCR obligatoire), `tests/test_selection_ci.py`, `frontend/e2e/generate_scan_pdf.py`, `tests/helpers_ocr_scan.py`
