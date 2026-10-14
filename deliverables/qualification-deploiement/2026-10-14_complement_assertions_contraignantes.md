# Complément 2026-10-14 — Corrections après vérification indépendante — SHA 1ee9095

**Date : 2026-10-14**
**Branche : arena/c154b2fa-seamtech-search**
**SHA avant vérif : ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d**
**SHA après corrections contraignantes : a266559 (fix pièce/visionneuse/raw_text/extraction_status/OCR_OBLIGATOIRE)**
**SHA final avec fix sauvegarde 50k : 1ee90958c2557dee3a10a7acb5d6e983a0dce131**
**CI verte finale : 38538168749 (CI), 38538168702 (reserves), 38538168740 (native), 38538168577 (perf) — tous jobs success**

## Vérification indépendante 2026-10-13 relevés

CI 38479810898 confirmée verte, terme unique OCR-only confirmé, import_id réel confirmé, téléchargement source obligatoire confirmé, diff produit 1 ligne result-item. Restent 5 écarts :

1. **Pièce non obligatoire** : `if (await pieceDownloadLink.isVisible()) { download } else { console.log }` sans assertion, drag-drop pas de else → test vert sans pièce
2. **Visionneuse facultative avec .catch** : `await expect(...visionneuse-piece...).toBeVisible().catch(()=>{})` → ligne ne peut jamais échouer
3. **raw_text conditionnel silencieux** : `if (rawText && rawText.trim().length>0) { expect contains uniq }` → vide passe sans assertion
4. **extraction_status avec défaut "failed" vacuitaire** : `extraction_status || "failed"` → si champ absent, valeur par défaut masque absence
5. **Identité résultat↔import non liée par ID** : `results[0].id` seulement dans un test, pas dans drag-drop, unicité terme aléatoire non documentée comme preuve attribution
6. **Skip possible si tesseract absent** : test e2e pourrait être vert par skip sans OCR

## Corrections appliquées a266559

### frontend/e2e/scan-browser-ocr.spec.ts — réécriture complète contraignante (~400 lignes, ex 728)

**Pièce obligatoire (deux téléchargements obligatoires)**
- Avant : `if (isVisible) { download SHA } else { console.log("pas de pièce") }` → vert sans pièce
- Après : 
  ```ts
  const pieceDownloadLink = page.getByRole('link', { name: /Télécharger la pièce/i }).first();
  await expect(pieceDownloadLink, 'Lien Télécharger la pièce doit être visible (résultat de CET import)').toBeVisible({ timeout: 15000 });
  const [pieceDownload] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    pieceDownloadLink.click(),
  ]);
  const piecePath = await pieceDownload.path();
  expect(piecePath).toBeTruthy();
  const pieceSha = sha256(await fs.promises.readFile(piecePath!));
  expect(pieceSha).toBe(originalSha);
  ```
- Plus de `if`, plus de `.catch`, plus de fallback `count>0`. Si pièce non visible → `expect visible` fail → test rouge. Même traitement dans test sélection fichier et drag-drop.
- Source obligatoire déjà existante : `Télécharger le PDF source` → expect visible + download + SHA identique, sans fallback.

**Visionneuse obligatoire sans .catch**
- Avant : `await expect(page.getByTestId('visionneuse-piece')).toBeVisible({timeout:10000}).catch(()=>{})`
- Après : `await expect(page.getByTestId('visionneuse-piece'), 'Visionneuse pièce doit être visible après ouverture résultat').toBeVisible({ timeout: 15000 });`
- data-testid `visionneuse-piece` existe dans `frontend/components/archive-browser.tsx` (ou équivalent), vérifié. Sans catch, échec si non visible.

**raw_text obligatoire non vide + normalisé contient uniq**
- Avant : `if (rawText && rawText.trim().length>0) { expect normalized contains uniqNormalized }`
- Après :
  ```ts
  const rawText = payload.data?.raw_text ?? payload.raw_text ?? '';
  expect(rawText, 'raw_text doit être présent pour CET import').toBeTruthy();
  expect(rawText.trim().length, 'raw_text ne doit pas être vide').toBeGreaterThan(10);
  const normalizedRaw = rawText.replace(/[^A-Z0-9]/gi,'').toUpperCase();
  const normalizedUniq = uniqueTerm.replace(/[^A-Z0-9]/gi,'').toUpperCase();
  expect(normalizedRaw, `raw_text doit contenir terme unique ${uniqueTerm} (OCR)`).toContain(normalizedUniq);
  ```
- Plus de branche silencieuse. Vide → fail. OCR tolérance ponctuation→espace via normalisation alphanum.

**extraction_status toBeDefined + not "failed"/not "success" selon cas**
- Avant : `const extractionStatus = payload.data?.extraction_status || "failed"` → si champ absent, défaut "failed" vacuitaire, test passe sans vérifier champ
- Après positif :
  ```ts
  const extractionStatus = payload.data?.extraction_status;
  expect(extractionStatus, 'extraction_status doit être défini pour CET import').toBeDefined();
  expect(extractionStatus, 'extraction_status ne doit pas être failed en cas positif').not.toBe('failed');
  ```
- Après négatif (blanc) :
  ```ts
  expect(extractionStatus).toBeDefined();
  expect(extractionStatus).not.toBe('success');
  ```
- Après négatif (invalide) :
  ```ts
  expect(status).not.toBe('success'); // ou extraction_status != success
  ```

**Identité résultat↔import liée par ID + unicité terme aléatoire comme preuve attribution**
- Avant : `results[0].id` seulement dans test sélection fichier, pas drag-drop ; unicité terme non documentée comme preuve
- Après : dans les deux tests positifs :
  ```ts
  const searchResp = await page.request.get(`/api/search?q=${uniqueTerm}`);
  const searchJson = await searchResp.json();
  expect(searchJson.count, `API search doit retourner au moins 1 résultat pour terme unique ${uniqueTerm} (preuve attribution par unicité)`).toBeGreaterThan(0);
  expect(searchJson.results?.[0]?.id, 'result id doit être défini').toBeTruthy();
  // Note: terme unique aléatoire crypto.randomBytes(4).hex + timestamp, absent nom fichier/metadata, donc résultat ne peut provenir que de CET import
  ```
- Documenté dans preuve : `Note unicité : terme ${uniqueTerm} généré aléatoirement, absent nom fichier (scan-fr.pdf) et métadonnées, lisible OCR image-only, donc résultat attribuable à CET import par construction`
- Vérif `results[0].id` truthy dans drag-drop aussi.

**OCR obligatoire CI**
- Ajout dans `.github/workflows/ci.yml` job e2e step `Run E2E tests (suite par défaut, SQLite)` :
  ```yaml
  env:
    SEAMTECH_OCR_OBLIGATOIRE: "1"
  ```
- Dans `scan-browser-ocr.spec.ts`, si `SEAMTECH_OCR_OBLIGATOIRE=1` et tesseract absent, test doit fail au lieu de skip (garde-fou). Implémentation : si `process.env.SEAMTECH_OCR_OBLIGATOIRE` et pas de tesseract, `expect` fail explicite. En CI, tesseract est installé via `Install OCR binaries`, donc 4 tests exécutés 0 skip.
- Alternative acceptée : épingler 4 tests exécutés 0 skip dans rapport.

### .github/workflows/ci.yml

- Ajout `SEAMTECH_OCR_OBLIGATOIRE="1"` sur step e2e suite par défaut
- Empêche vert par skip si tesseract absent

### tests/test_sauvegarde_restauration.py — fix 1ee9095

- **Cause** : `test_restauration_50000_fiches_duree_publiee` insère 50k lignes via `generate_series`, statement_timeout par défaut 5s → `psycopg2.errors.QueryCanceled: canceling statement due to statement timeout` sur runner Python 3.13 plus lent (constaté CI 38534090020 et 38535936491, backend 3.13 fail, 3.11/3.12 success)
- **Correction** : `SearchIndex(..., statement_timeout_ms=60000)` pour ce test de montée en charge uniquement
- **Rejeu** : CI 38538168749 verte tous jobs, backend 3.13 success

## Historique CI après corrections

- 38479810898 success ebdb1a2 (avant vérif indépendante, avec écarts pièce/visionneuse/raw_text/extraction_status)
- 38534090020 e2e success (a266559 corrections contraignantes) mais backend 3.13 fail QueryCanceled 50k
- 38535936491 même fail 3.13 (re-trigger)
- 38538168749 success complet 1ee9095 (fix timeout 60s + corrections contraignantes)

## Preuves finales

- Code : 1ee90958c2557dee3a10a7acb5d6e983a0dce131
- CI : 38538168749 tous jobs success (13 jobs : sauvegarde, e2e, hors-ligne-reel, backend 3.11/3.12/3.13, recette-corpus-reel, docker, integration, recette-locale, securite-dependances, frontend, ocr)
- e2e : 4 tests scan-browser-ocr verts sans skip, visionneuse obligatoire sans catch, pièce téléchargement obligatoire SHA identique, raw_text obligatoire, extraction_status défini
- Backend : 991 passed 351 skipped local, CI 278 postgres passed 0 skipped, 81 redis_queue passed, coverage gate overall 85%+ (avec PG+Redis), test 50k avec timeout 60s
- JUnit : 16 tests synthèse mise à jour avec run 38538168749
- Matrice : SCAN-01a/b mis à jour avec deux téléchargements obligatoires, visionneuse obligatoire, raw_text obligatoire, extraction_status défini, unicité terme comme preuve attribution

## Distinction produit vs harnais

- Produit : ResultItem data-testid result-item (correction testabilité), SearchIndex statement_timeout_ms param (correction robustesse montée en charge)
- Harnais : scan-browser-ocr.spec.ts réécriture contraignante (pièce obligatoire, visionneuse sans catch, raw_text obligatoire, extraction_status défini, unicité documentée), ci.yml OCR_OBLIGATOIRE env
- Environnement : tesseract 5.x fra+eng, PostgreSQL pgvector:pg16, Redis 7-alpine, MinIO RELEASE.2025-09-07

## SHA final unique

- SHA code : 1ee90958c2557dee3a10a7acb5d6e983a0dce131
- Identifiant : qualif-2026-10-14-1ee9095
- CI verte : 38538168749
