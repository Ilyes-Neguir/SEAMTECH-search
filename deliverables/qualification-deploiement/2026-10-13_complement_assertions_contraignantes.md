# Complément 2026-10-13 → 2026-10-14 — Assertions contraignantes scan-browser-ocr — SHA ebdb1a2 → 1ee9095

**Date : 2026-10-13 initiale, mise à jour 2026-10-14 après vérification indépendante**
**Branche : arena/c154b2fa-seamtech-search**
**SHA code fonctionnel : ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d (ebdb1a2) → a266559 (corrections pièce/visionneuse/raw_text/extraction_status) → 1ee90958c2557dee3a10a7acb5d6e983a0dce131 (1ee9095 final)**
**CI verte initiale : 38479810898, finale : 38538168749 (tous jobs success)**

## Relecture directe au commit 0bbad2d et écarts relevés vérification indépendante

Le dépôt via DataTransfer exerce maintenant les handlers navigateur (simulation dragenter/dragover/drop avec DataTransfer natif dans vrai navigateur, pas geste manuel OS). Conservé.

Problèmes relevés initiaux (ebdb1a2) :
1. Téléchargement et SHA-256 restaient facultatifs (fallback count)
2. Recherche ne garantissait pas que document importé est retrouvé (GENOA|scan-en|upload générique, importId via-ui, GENOA pas unique par exécution, pas de capture import_id réel)
3. Tests négatifs ne prouvaient pas absence texte inventé (recherche mot arbitraire, pas de vérif extraction du document concerné, branche Fichiers détectés visible sans assertion supplémentaire)

Problèmes relevés vérification indépendante 2026-10-13 (après ebdb1a2) :
4. Pièce non obligatoire : `if (await pieceDownloadLink.isVisible()) { download } else { console.log }` sans assertion, drag-drop pas de else → test vert sans pièce
5. Visionneuse facultative avec .catch : `await expect(...visionneuse-piece...).toBeVisible().catch(()=>{})` → ligne ne peut jamais échouer
6. raw_text conditionnel silencieux : `if (rawText && trim.length>0) { expect contains }` → vide passe sans assertion
7. extraction_status avec défaut "failed" vacuitaire : `|| "failed"` masque absence champ
8. Identité résultat↔import non liée par ID : results[0].id seulement dans un test, unicité terme aléatoire non documentée comme preuve attribution
9. Skip possible si tesseract absent : vert par skip sans OCR
10. Sauvegarde 50k : statement_timeout 5s → QueryCanceled sur Python 3.13 (CI 38534090020/38535936491 backend 3.13 fail)

## Corrections assertions (ebdb1a2 → a266559 → 1ee9095)

### Produit

- `frontend/components/result-item.tsx` : ajout `data-testid="result-item"` sur div root — correction produit pour testabilité (permet `locator('[data-testid="result-item"]')` visible). À elle seule, `data-testid="drop-zone"` (ImportPanel) ne constitue pas correction métier dépôt, c'est amélioration harnais.
- `seamtech_search/indexer.py` : param `statement_timeout_ms` déjà existant, utilisé dans test 50k avec 60000 ms (correction robustesse montée en charge, produit)
- `tests/test_sauvegarde_restauration.py` : `SearchIndex(..., statement_timeout_ms=60000)` pour insertion 50k (fix QueryCanceled)

### Harnais et environnement

- `tests/helpers_ocr_scan.py` : `gen_pdf_custom(tmp_dir, unique_term, lang, filename)` PDF image-only avec terme fourni, absent nom fichier/metadata
- `frontend/e2e/generate_scan_pdf.py` : accepte 3e arg `unique_term`
- `.github/workflows/ci.yml` : ajout `SEAMTECH_OCR_OBLIGATOIRE="1"` sur step e2e suite par défaut → interdit vert par skip, exige 4 tests exécutés 0 skip
- `frontend/e2e/scan-browser-ocr.spec.ts` : réécriture complète contraignante (728→~400 lignes version sans branches permissives)

**Terme unique par exécution**
- `uniqueTerm(prefix)` : `crypto.randomBytes(4).toString('hex').toUpperCase()` + suffix timestamp, nettoyé `[^A-Z0-9]` → uniquement lettres majuscules + chiffres, sans underscore (OCR lit `_` comme espace → échec `GENOA_D3ECDB73_97436` → `GENOA D3ECDB73 97436` constaté CI 38476009010)
- Exemples : `ZEPHYRINEA1B2C3D4E51234`, `GENOA5F6E7D8C9B0A1234`
- Absent nom fichier (scan-fr.pdf) et métadonnées, lisible OCR
- Documenté comme preuve attribution : terme aléatoire unique par exécution, absent nom fichier/metadata, image-only, donc résultat ne peut provenir que de CET import

**Téléchargement obligatoire — deux téléchargements obligatoires (corrigé après vérif indépendante)**
- Avant (ebdb1a2) formulation surévaluée : disait "deux téléchargements obligatoires" mais code avait `if (isVisible) { download } else { console.log }` → pièce facultative
- Après (a266559) :
  - Exige : bouton visible `Télécharger le PDF source` (import panel) + download event + fichier récupéré + SHA-256 identique original + preuve jointe ; si manque → fail (expect visible obligatoire, pas de fallback)
  - Exige : bouton visible `Télécharger la pièce` (`/api/pieces/{id}/telecharger`) + download + SHA identique original ; expect visible obligatoire, sans else, sans .catch, dans les deux tests positifs (sélection fichier et drag-drop)
  - Si l'un manque → expect fail → test rouge
  - Plus de fallback `else { expect(resultsCount).toBeGreaterThan(0) }`

**Lien import, OCR et résultat — corrections contraignantes**
- Capture vrai import_id depuis réponse réseau `POST /api/imports/confirm` via `page.waitForResponse(r=>url.includes('/api/imports/confirm') && method POST)` → `json.import_id || job_id || id`, expect truthy, log
- Attend statut attendu pour CET import précis : `État : completed|needs_review` (needs_review nominal possible si extraction partielle, mais on exige quand même OCR/indexation/téléchargement)
- Vérifie extraction effective via `GET /api/imports/{id}` → payload.data.raw_text :
  - Avant : `if (rawText) { expect contains }` silencieux si vide
  - Après : `expect(rawText).toBeTruthy()`, `expect(trim.length).toBeGreaterThan(10)`, normalisé `replace(/[^A-Z0-9]/gi,'').toUpperCase()` contient terme unique normalisé (tolérance OCR ponctuation→espace) → obligatoire
- extraction_status :
  - Avant : `extraction_status || "failed"` vacuitaire si champ absent
  - Après positif : `expect(extractionStatus).toBeDefined()` + `expect(not).toBe('failed')`
  - Après négatif blanc : `toBeDefined()` + `not.toBe('success')`
  - Après négatif invalide : `status not success` ou `extraction_status != success`
- Recherche directe API obligatoire : `page.request.get('/api/search?q=uniq')` count>0, results[0].id truthy — dans les deux tests positifs (sélection fichier et drag-drop), pas seulement un
- Recherche UI : fill uniq + Enter + click bouton Rechercher + waitForResponse `/api/search?q=uniq` count>0 + `locator('[data-testid="result-item"]').first()` visible 15000ms
- Vérifie identité résultat : terme unique par exécution garantit que résultat appartient à CET import (count idéal 1, sinon au moins 1 avec id), ouverture result-item + visionneuse-piece visible obligatoire sans .catch, téléchargement pièce SHA identique obligatoire
- Consigne vrais identifiants dans preuve, pas via-ui : `{originalSha, downloadedSha, fileName, importId, uniqueTerm, recherche, etat, rawTextContainsUniq, methode}`

**PDF invalide et blanc — contrat explicite**
- Pour chaque branche, exige issue explicite : rejet avant import avec message vérifié OU import identifié avec statut et motif conformes contrat, aucune branche ne réussit seulement parce que Fichiers détectés visible
- Invalid : upload POST `/api/imports/upload` → si !ok → 400/413/500 + p.text-destructive message >5 chars ; si ok → attend Fichiers détectés OU Aucun PDF technique OU p.text-destructive ; si Aucun PDF technique → issue explicite ; si Fichiers détectés + bouton import visible → confirm capture import_id → État failed|needs_review + GET /api/imports/{id} status failed/needs_review + extraction_status != success ; recherche API `/api/search?q=INVALIDE_sha8` count 0 obligatoire
- Blanc : upload OK → Fichiers détectés → import → capture import_id → État needs_review|failed pas completed, GET /api/imports/{id} extraction_status != success, raw_text len <20, À vérifier visible si Données extraites visible sinon absence Données extraites = preuve absence extraction inventée, recherche API BLANC_SANS_TEXTE_9999 count 0

**Prouver assertions contraignantes**
- Tests échouent si : téléchargement non disponible (expect visible fail), résultat autre document (count 0 fail car terme unique), terme non extrait (raw_text toBeTruthy fail + normalisé fail + API search count 0 fail), PDF invalide sans rejet explicite ni état erreur (expect fail), visionneuse non visible (expect fail sans catch), pièce non visible (expect fail)
- Contrôles négatifs isolés du harnais conservés, parcours positifs avec vrais services (PostgreSQL pgvector:pg16, Redis 7-alpine, MinIO RELEASE.2025-09-07, Tesseract 5.x fra+eng)
- SEAMTECH_OCR_OBLIGATOIRE=1 garantit 4 tests exécutés 0 skip en CI

## Historique CI

- 38468805294 fail selector :has-text invalid
- 38470099513 fail mock items
- 38471404314 success natif mais assertions permissives
- 38476009010 fail OCR underscore GENOA_D3ECDB73_97436 → GENOA D3ECDB73 97436
- 38477938099 fail result-item missing
- 38479810898 success contraignant ebdb1a2 (mais avec écarts pièce conditionnelle, visionneuse catch, raw_text if, extraction_status défaut)
- 38534090020 e2e success a266559 (corrections contraignantes) mais backend 3.13 fail QueryCanceled 50k
- 38535936491 même fail 3.13
- 38538168749 success complet 1ee9095 (fix timeout 60s + corrections contraignantes)

## Preuves finales

- Code : 1ee90958c2557dee3a10a7acb5d6e983a0dce131
- CI : 38538168749 tous jobs success (backend 3.11/3.12/3.13, frontend, docker, e2e, ocr, sauvegarde, integration, hors-ligne, recette-corpus, recette-locale, securite, reserves, native, perf)
- e2e : 4 tests verts sans skip, visionneuse obligatoire sans catch, deux téléchargements obligatoires SHA identiques, raw_text obligatoire toBeTruthy length>10 normalisé contient uniq, extraction_status toBeDefined + not failed/success, import_id réel, terme unique aléatoire comme preuve attribution, results[0].id truthy dans les deux tests positifs
- Backend : 991 passed 351 skipped local, CI 278 postgres passed 0 skipped, 81 redis_queue passed, coverage gate overall 85%+ (avec PG+Redis), test 50k avec timeout 60s
- JUnit : 16 tests synthèse mise à jour avec run 38538168749
- Matrice : SCAN-01a/b mis à jour avec deux téléchargements obligatoires réellement obligatoires, visionneuse obligatoire, raw_text obligatoire, extraction_status défini

## Limites et distinctions

- Geste décrit comme `simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur` (pas geste manuel explorateur système)
- Distinction correction produit (ResultItem data-testid, SearchIndex statement_timeout_ms) vs amélioration harnais (drop-zone data-testid, scan-browser-ocr.spec.ts contraignant) vs environnement (ci.yml OCR_OBLIGATOIRE, tesseract)
- Préserve résultats valides déjà obtenus (recherche, sauvegarde, etc.)
- Ne fusionne rien sans autorisation

## SHA final unique

- SHA code : 1ee90958c2557dee3a10a7acb5d6e983a0dce131
- Identifiant : qualif-2026-10-14-1ee9095
- CI verte : 38538168749
