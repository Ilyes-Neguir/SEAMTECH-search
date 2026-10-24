# Défauts et corrections — 2026-10-13 → 2026-10-14 — SHA ebdb1a2 → 1ee9095

**SHA code fonctionnel avant vérif : `ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d`**
**SHA après corrections contraignantes : `a266559`**
**SHA final avec fix sauvegarde 50k : `1ee90958c2557dee3a10a7acb5d6e983a0dce131`**
**CI verte finale : `38538168749` (CI), `38538168702` (reserves), `38538168740` (native), `38538168577` (perf) — tous jobs success**

**Journal drag-drop et assertions :**
- `6eaa714` fix selector `:has-text` invalide → CI `38468805294` e2e fail
- `5fd668f` mock webkitGetAsEntry → CI `38470099513` fail
- `0bbad2d` DataTransfer natif `items.add(file)` + `data-testid="drop-zone"` → CI `38471404314` e2e success mais permissif (téléchargement facultatif, recherche générique)
- `8a5b3ff` fix assertions : téléchargement obligatoire, terme unique, import_id réel, lien OCR/recherche → CI `38476009010` fail OCR underscore `GENOA_D3ECDB73_97436` → `GENOA D3ECDB73 97436`
- `07228b4` supprime underscores, normalisation alphanum → CI `38477938099` fail result-item missing
- `ebdb1a2` ajoute data-testid result-item + API search direct → CI `38479810898` e2e success contraignant mais avec écarts (pièce conditionnelle, visionneuse catch, raw_text if, extraction_status défaut)
- `a266559` corrections contraignantes vérif indépendante : pièce obligatoire, visionneuse obligatoire sans catch, raw_text obligatoire, extraction_status défini, OCR obligatoire, results[0].id dans drag-drop → CI `38534090020` e2e success mais backend 3.13 fail QueryCanceled 50k
- `1ee9095` fix sauvegarde 50k timeout 60s → CI `38538168749` success complet

**Distinction produit vs harnais vs environnement :**
- Produit : `ResultItem` data-testid result-item, `SearchIndex` statement_timeout_ms, `seamtech_search/` métier
- Harnais : simulation dragenter/dragover/drop avec DataTransfer natif, terme unique sans underscore, normalisation OCR, import_id réel, téléchargement SHA obligatoire, recherche liée import, scan-browser-ocr.spec.ts contraignant
- Environnement : Dockerfile tesseract eng, ci.yml tesseract install, SEAMTECH_OCR_OBLIGATOIRE, MinIO reconstruit, provisionner stockage

## Défaut #1 — [Harnais] Frontend TS1005 `import os` invalide
- Fichier : `frontend/e2e/scan-browser-ocr.spec.ts:7`
- Symptôme : frontend failure TS1005
- Cause : syntaxe ES invalide
- Correction : `import * as os from "os"`
- Statut : CORRIGÉ ET TESTÉ

## Défaut #2 — [Environnement/Harnais] Backend postgres suite skipped guard
- Fichier : `tests/test_scan_browser_upload_ocr.py` + ci.yml
- Symptôme : backend failure skipped==0 guard fail
- Cause : test OCR marqué postgres collecté sans tesseract
- Correction : pytestmark `[ocr_suite]` seul, ci.yml `and not ocr_suite`
- Statut : CORRIGÉ ET TESTÉ

## Défaut #3 — [Environnement] Dockerfile tesseract eng manquant
- Fichier : Dockerfile
- Symptôme : scan anglais échoue en conteneur
- Correction : ajout `tesseract-ocr-eng`
- Statut : CORRIGÉ ET TESTÉ

## Défaut #4 — [Harnais] Ruff lint E402/I001/F401
- Fichiers : generate_scan_pdf.py, helpers_ocr_scan.py
- Symptôme : Ruff lint failure
- Correction : ruff --fix + noqa
- Statut : CORRIGÉ ET TESTÉ

## Défaut #5 — [Harnais] Garde-fou sélection CI test_selection_ci.py
- Symptôme : sqlite-suite-failed
- Correction : maj constantes avec `and not ocr_suite`
- Statut : CORRIGÉ ET TESTÉ

## Défaut #6 — [Harnais] E2E strict mode violation duplicate text
- Symptôme : getByText resolved to 2 elements
- Correction : ajout .first()
- Statut : CORRIGÉ ET TESTÉ

## Défaut #7 — [Harnais] E2E cas erreur invalide sans message (première version permissive)
- Fichier : scan-browser-ocr.spec.ts test PDF invalide
- Cause : backend scan_folder retourne 1 candidate même pour invalid.pdf avec warning "No technical PDF found" → UI affiche Fichiers détectés, pas erreur immédiate
- Correction initiale insuffisante : attente flexible erreur OU Fichiers détectés
- Limite revue : acceptait textes génériques "État", "Fichiers détectés"
- Correction finale voir #8/#9
- Statut : CORRIGÉ ET TESTÉ mais jugé trop permissif → repris

## Défaut #8 — [Harnais] setInputFiles n'est pas preuve de glisser-déposer réel + assertions téléchargement/recherche non contraignantes (revue 0bbad2d)

- Fichier : scan-browser-ocr.spec.ts au commit 0bbad2d
- Écarts relevés :
  1. setInputFiles ne démontre pas dragEnter/dragover/drop
  2. Téléchargement facultatif : `if (visible) { download } else { expect(count>0) }` → vert sans télécharger
  3. Recherche générique `GENOA|scan-en|upload` peut correspondre nom fichier/UI, pas résultat OCR de CET import ; GENOA pas unique ; importId "via-ui"
  4. data-testid drop-zone seul ne constitue pas correction métier, result-item manquant
- Corrections successives :
  - 6eaa714 : remplace `:has-text` invalide
  - 5fd668f : mock webkitGetAsEntry
  - 0bbad2d : DataTransfer natif + data-testid drop-zone → e2e success mais permissif
  - 8a5b3ff : téléchargement obligatoire (supprime fallback), terme unique via gen_pdf_custom + uniqueTerm() sans underscore, capture import_id depuis /api/imports/confirm, vérif raw_text via /api/imports/{id}, API search direct count>0 + UI result-item, téléchargement pièce obligatoire SHA → CI 38476009010 fail underscore
  - 07228b4 : sans underscore + normalisation alphanum → CI 38477938099 fail result-item missing
  - ebdb1a2 : ajoute data-testid result-item (produit) + API search direct + UI result-item 15000ms → CI 38479810898 success mais avec écarts restants (pièce conditionnelle, visionneuse catch, raw_text if, extraction_status défaut)
  - a266559 (final contraignant) : pièce obligatoire sans if/else, visionneuse obligatoire sans catch, raw_text toBeTruthy length>10 sans if, extraction_status toBeDefined not failed/success sans défaut "failed", results[0].id dans les deux tests, unicité terme documentée comme preuve attribution, SEAMTECH_OCR_OBLIGATOIRE=1 → CI 38534090020 e2e success
- Détails finaux a266559/1ee9095 :
  - Terme unique : `ZEPHYRINE{HEX}{suffix}` et `GENOA{HEX}{suffix}` via crypto.randomBytes(4) sans underscore, absent nom fichier/metadata, image-only
  - gen_pdf_custom + generate_scan_pdf.py 3e arg
  - Capture import_id : waitForResponse /api/imports/confirm → json.import_id || job_id || id, expect truthy
  - Vérif OCR : GET /api/imports/{id} → raw_text toBeTruthy length>10 normalisé alphanum majuscule contient terme unique
  - extraction_status : toBeDefined + not failed (positif) / not success (négatif)
  - Recherche : GET /api/search?q=uniq direct count>0 + results[0].id truthy obligatoire (deux tests) + UI fill + Enter + click Rechercher + waitForResponse + result-item visible 15000ms, ouverture + visionneuse obligatoire sans catch
  - Deux téléchargements obligatoires : import panel /api/imports/{id}/artifacts/source_pdf visible + download SHA identique + pièce /api/pieces/{id}/telecharger visible + download SHA identique, sans else, sans catch, si manquant → fail
  - Preuve attachée avec vrais ids {originalSha, downloadedSha, fileName, importId, uniqueTerm, recherche, etat, rawTextContainsUniq, methode: "simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur"}
- Rejeu final : CI 38538168749 e2e success, tous jobs success
- Statut : CORRIGÉ ET TESTÉ (produit+harnais) — CI verte 38538168749

## Défaut #9 — [Harnais] PDF blanc et PDF invalide — assertions non contraignantes, absence preuve absence texte inventé (revue 0bbad2d)

- Fichiers : scan-browser-ocr.spec.ts tests PDF invalide et blanc au commit 0bbad2d
- Écarts :
  1. Rechercher BLANC_SANS_TEXTE_9999 puis vérifier premier résultat ne contient pas chaîne ne vérifie pas contenu extrait du document concerné
  2. Pour PDF invalide, chemin Fichiers détectés visible, bouton import absent, aucune assertion supplémentaire → branche peut réussir seulement parce que Fichiers détectés visible
  3. Pas de vérif données/artefacts extraction du document concerné
- Contrat attendu :
  - Invalide : backend doit refuser ou signaler échec avec issue explicite (rejet avant import avec message vérifié OU import identifié avec statut et motif conformes), pas de fiche inventée, statut terminal failed/needs_review, pas de résultat recherche
  - Blanc : produit peut conserver document comme fichier sans extraction (upload_status uploaded, extraction_status != success), statut needs_review/failed pas completed, aucun texte métier inventé, champs À vérifier ou absence Données extraites
- Correction ciblée ebdb1a2/a266559 :
  - Invalid : capture import_id depuis confirm si existe, upload POST /api/imports/upload → si !ok → expect 400/413/500 + p.text-destructive message >5 chars ; si ok → attend Fichiers détectés OU Aucun PDF technique OU p.text-destructive (pas État seul) ; si Aucun PDF technique → issue explicite ; si Fichiers détectés + bouton import visible → confirm → capture import_id → État failed|needs_review + GET /api/imports/{id} status failed/needs_review + extraction_status toBeDefined not success ; si pas bouton import → expect Aucun PDF technique ou erreur destructive ; recherche API /api/search?q=INVALIDE_sha8 count 0 obligatoire
  - Blanc : upload OK → Fichiers détectés → import → capture import_id → État needs_review|failed pas completed, GET /api/imports/{id} extraction_status toBeDefined not success, raw_text len <20, UI À vérifier visible si Données extraites visible sinon absence Données extraites = preuve absence extraction inventée ; recherche API BLANC_SANS_TEXTE_9999 count 0
  - Supprime assertions génériques seules, aucune branche ne réussit seulement parce que Fichiers détectés visible
- Rejeu : CI 38538168749 e2e success
- Statut : CORRIGÉ ET TESTÉ

## Défaut #10 — [Produit] ResultItem sans data-testid → recherche UI non testable

- Fichier : frontend/components/result-item.tsx
- Symptôme : 38477938099 e2e fail locator result-item not found Timeout 10000ms
- Cause : ResultItem sans data-testid result-item
- Correction : ajout data-testid="result-item" sur div root
- Rejeu : CI 38538168749 e2e success, result-item visible 15000ms, ouverture visionneuse obligatoire sans catch, deux téléchargements obligatoires
- Statut : CORRIGÉ ET TESTÉ (produit)

## Défaut #11 — [Harnais] OCR terme unique avec underscore mal lu

- Fichier : scan-browser-ocr.spec.ts + helpers_ocr_scan.py
- Symptôme : 38476009010 e2e fail Expected "GENOA_D3ECDB73_97436" Received "GENOA D3ECDB73 97436"
- Cause : Tesseract transforme _ en espace
- Correction : uniqueTerm() génère uniquement lettres majuscules + chiffres sans underscore, normalisation replace(/[^A-Z0-9]/gi,'').toUpperCase()
- Rejeu : CI 38538168749 success
- Statut : CORRIGÉ ET TESTÉ

## Défaut #12 — [Harnais] Pièce non obligatoire, visionneuse facultative avec .catch, raw_text conditionnel, extraction_status défaut "failed" vacuitaire, identité non liée par ID, skip possible si tesseract absent (vérification indépendante 2026-10-13)

- Fichiers : scan-browser-ocr.spec.ts ebdb1a2, .github/workflows/ci.yml
- Écarts relevés vérification indépendante :
  1. Pièce : `if (await pieceDownloadLink.isVisible()) { download SHA } else { console.log("pas de pièce") }` sans assertion, drag-drop pas de else → vert sans pièce
  2. Visionneuse : `await expect(...visionneuse-piece...).toBeVisible().catch(()=>{})` → ne peut jamais échouer
  3. raw_text : `if (rawText && trim.length>0) { expect contains uniq }` → vide passe silencieux
  4. extraction_status : `extraction_status || "failed"` → si champ absent, défaut masque absence
  5. Identité : results[0].id seulement dans un test, pas drag-drop, unicité terme aléatoire non documentée comme preuve attribution
  6. Skip : tesseract absent → skip possible → vert sans OCR
- Corrections a266559 :
  - Pièce : expect visible obligatoire + download + SHA identique, sans else, sans catch, dans les deux tests positifs
  - Visionneuse : expect visible obligatoire sans .catch, timeout 15000ms
  - raw_text : expect toBeTruthy + length>10 + normalisé contient uniq, sans if
  - extraction_status : expect toBeDefined + not failed (positif) / not success (négatif), sans défaut
  - Identité : API search direct count>0 + results[0].id truthy dans les deux tests, note unicité terme aléatoire absent nom fichier/metadata comme preuve attribution par construction
  - OCR obligatoire : env SEAMTECH_OCR_OBLIGATOIRE=1 dans ci.yml job e2e, interdit vert par skip, exige 4 tests exécutés 0 skip
- Rejeu : CI 38534090020 e2e success (a266559), CI 38538168749 success complet (1ee9095)
- Statut : CORRIGÉ ET TESTÉ (harnais+environnement) — distinction correction produit (ResultItem) vs amélioration harnais (scan-browser-ocr.spec.ts contraignant)

## Défaut #13 — [Produit] Sauvegarde 50k statement_timeout 5s → QueryCanceled sur Python 3.13

- Fichier : tests/test_sauvegarde_restauration.py::test_restauration_50000_fiches_duree_publiee
- Symptôme CI : 38534090020 et 38535936491 backend (3.13) failure, annotation `coverage-suite-failed: FAILED tests/test_sauvegarde_restauration.py::test_restauration_50000_fiches_duree_publiee - psycopg2.errors.QueryCanceled: canceling statement due to statement timeout`, backend 3.11/3.12 success, 3.13 fail
- Cause : insertion 50k lignes via generate_series dépasse statement_timeout par défaut 5000 ms sur runner 3.13 plus lent ; SearchIndex __init__ pool_timeout 30s, statement_timeout_ms 5000ms par défaut
- Correction : SearchIndex(..., statement_timeout_ms=60000) pour ce test de montée en charge uniquement (60s)
- Rejeu : CI 38538168749 backend 3.13 success, tous jobs success, 278 postgres passed 0 skipped, 81 redis_queue passed
- Statut : CORRIGÉ ET TESTÉ (produit)

## Défauts antérieurs déjà corrigés (référence)

- Recherche trigrammes _source_trigrammes vs index GIN 023 (SHA 51d2772) — [Produit] CORRIGÉ ET TESTÉ
- Sauvegarde objet-only → sauvegarde conjointe PG+fichiers+MinIO (SHA a3506c8) — [Produit] CORRIGÉ ET TESTÉ
- Perf régression a3506c8 p50 231ms vs 5ms (SHA d202f0b) — [Produit] CORRIGÉ ET TESTÉ

## Non-régressions vérifiées (sur 1ee9095, CI 38538168749)

- backend 3.11 success, 3.12 success, 3.13 success, frontend success, docker success, e2e success (avec assertions contraignantes : deux téléchargements obligatoires SHA identiques, visionneuse obligatoire sans catch, raw_text obligatoire toBeTruthy length>10 normalisé contient uniq, extraction_status toBeDefined not failed/success, import_id réel, terme unique aléatoire comme preuve attribution, results[0].id truthy dans les deux tests, OCR obligatoire 0 skip), ocr success, sauvegarde success (avec fix timeout 60s), recette-corpus-reel success, recette-locale success, integration success, hors-ligne-reel success, securite-dependances success, Qualification native 38538168740 success, reserves 38538168702 success, Perf comparaison 38538168577 success
- Tous jobs verts sur 1ee9095, 0 ÉCHEC NON RÉSOLU dans périmètre testé, assertions contraignantes vérifiées (tests échouent si téléchargement non disponible, visionneuse non visible, raw_text vide, extraction_status absent, résultat autre document, terme non extrait, PDF invalide sans rejet explicite)
