# Défauts et corrections — 2026-10-13 — SHA 5438b9e → ebdb1a2

**SHA code fonctionnel : `5438b9e22d0a2d0e7bad58580db92fc016be9db7`**
**SHA final avec livrables : `ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d`**
**CI verte : `38479810898` (CI), `38479810943` (reserves), `38479810991` (native), `38479810965` (perf) — assertions contraignantes**

**Journal drag-drop et assertions :**
- `6eaa714` fix selector `:has-text` invalide → CI `38468805294` e2e fail `getByText(/fichier.*prêt.*envoyer/) not found`
- `5fd668f` mock webkitGetAsEntry → CI `38470099513` même échec
- `0bbad2d` DataTransfer natif `items.add(file)` + `data-testid="drop-zone"` → CI `38471404314` e2e success (mais assertions encore permissives : téléchargement facultatif, recherche générique GENOA|scan-en|upload)
- `8a5b3ff` fix assertions : téléchargement obligatoire, terme unique par exécution, import_id réel, lien OCR/recherche → CI `38476009010` e2e fail OCR underscore `GENOA_D3ECDB73_97436` → `GENOA D3ECDB73 97436` (underscore mal lu)
- `07228b4` supprime underscores, normalisation alphanum → CI `38477938099` e2e fail result-item missing (pas de data-testid dans ResultItem)
- `ebdb1a2` ajoute data-testid result-item (produit) + API search direct count>0 obligatoire + UI result-item visible 15000ms → CI `38479810898` e2e success contraignant

**Distinction produit vs harnais :**
- Produit : `ResultItem` data-testid result-item (correction produit pour testabilité), `ImportPanel` data-testid drop-zone (amélioration harnais, pas correction métier dépôt)
- Harnais : simulation dragenter/dragover/drop avec DataTransfer natif dans vrai navigateur (exerce handlers, pas geste manuel OS), terme unique sans underscore, normalisation OCR, import_id réel, téléchargement SHA obligatoire, recherche liée import

## Catégorisation (revue 2026-10-13)

Le rapport distingue :
- **Produit** : code `seamtech_search/` ou `frontend/` qui affecte l'utilisateur final
- **Environnement** : `Dockerfile`, `ci.yml`, dépendances système (tesseract, poppler), outillage CI
- **Harnais** : tests, sélecteurs Playwright, garde-fous, organisation des marqueurs

Les 7 premiers défauts mêlaient ces catégories. La revue demande de les séparer.

## Défaut #1 — [Harnais] Frontend TS1005 `import os` invalide

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts:7`
- **Catégorie :** Harnais (test e2e)
- **Symptôme CI :** `frontend failure` — `TS1005 '=' expected` à `import os`
- **Reproduction :** `cd frontend && pnpm exec tsc --noEmit` → erreur ligne 7
- **Cause :** Syntaxe ES invalide `import os` au lieu de `import * as os from "os"`
- **Test échouant avant correction :** `frontend` job CI (type check)
- **Correction :** `import * as os from "os"` + refactor génération PDF via `generate_scan_pdf.py`
- **Rejeu :** `pnpm exec tsc --noEmit` → OK, `pnpm build` → Compiled successfully Next.js 16.3.8, frontend success `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ (harnais)

## Défaut #2 — [Environnement/Harnais] Backend postgres suite skipped guard

- **Fichier :** `tests/test_scan_browser_upload_ocr.py` pytestmark `[postgres, ocr_suite]`, `.github/workflows/ci.yml`
- **Catégorie :** Environnement (sélection CI) + Harnais (marqueur)
- **Symptôme CI :** `backend (3.11) failure` — garde `skipped==0` échoue, `1 skipped`
- **Reproduction :** `pytest -m "postgres and not perf and not sauvegarde and not redis_queue" -v` collecte `test_scan_browser_upload_ocr` qui `skip("tesseract absent")` car backend job n'installe pas tesseract
- **Cause :** Test OCR exige tesseract mais marqué postgres, collecté sans binaire → skip
- **Correction :** pytestmark `[ocr_suite]` seul, ci.yml ajoute `and not ocr_suite` dans postgres et sqlite suites
- **Rejeu :** 278 passed 0 skipped, backend success
- **Statut :** CORRIGÉ ET TESTÉ (environnement)

## Défaut #3 — [Environnement] Dockerfile tesseract eng manquant

- **Fichier :** `Dockerfile`
- **Catégorie :** Environnement (image produit)
- **Symptôme :** Scan anglais `GENOA SAIL ATLANTIC` échoue en conteneur (eng absent) alors que CI e2e l'installe
- **Reproduction :** `docker build` puis `tesseract --list-langs` → fra présent, eng absent
- **Cause :** Dockerfile installait seulement fra, pas eng
- **Correction :** Ajout `tesseract-ocr-eng` + vérif `grep -q eng`
- **Rejeu :** docker success `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ (produit, car image = cible déploiement)

## Défaut #4 — [Harnais] Ruff lint E402/I001/F401

- **Fichiers :** `frontend/e2e/generate_scan_pdf.py`, `tests/helpers_ocr_scan.py`, `tests/test_scan_browser_upload_ocr.py`
- **Catégorie :** Harnais
- **Symptôme CI :** `Ruff lint check failure`
- **Reproduction :** `ruff check .` → 10 erreurs
- **Cause :** `sys.path.insert` puis import (E402), imports non triés, unused
- **Correction :** `ruff --fix` + `noqa: E402`, tri imports, suppression unused
- **Rejeu :** `ruff check .` → All checks passed
- **Statut :** CORRIGÉ ET TESTÉ (harnais)

## Défaut #5 — [Harnais] Garde-fou sélection CI `test_selection_ci.py`

- **Fichier :** `tests/test_selection_ci.py`
- **Catégorie :** Harnais (garde-fou)
- **Symptôme CI :** `sqlite-suite-failed` — `AssertionError: commande de sélection absente du workflow`
- **Reproduction :** ci.yml a évolué pour exclure `ocr_suite`, mais constantes du garde-fou attendaient ancienne commande
- **Cause :** Contrat explicite non mis à jour après ajout exclusion ocr_suite
- **Correction :** Maj constantes `COMMANDE_SUITE_SANS_SERVICE` et `COMMANDE_POSTGRES` avec `and not ocr_suite`
- **Rejeu :** `pytest tests/test_selection_ci.py -q` → 13 passed
- **Statut :** CORRIGÉ ET TESTÉ (harnais)

## Défaut #6 — [Harnais] E2E strict mode violation duplicate text

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts:70`
- **Catégorie :** Harnais
- **Symptôme CI :** `e2e failure` — `strict mode violation: getByText(/Choisir le PDF technique/i) resolved to 2 elements`
- **Cause :** Locator non discriminant
- **Correction :** Ajout `.first()` sur tous les `getByText` ambigus
- **Rejeu :** tsc OK, e2e success après fix #7
- **Statut :** CORRIGÉ ET TESTÉ (harnais)

## Défaut #7 — [Harnais] E2E cas erreur invalide sans message (première version trop permissive)

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts` test "PDF invalide"
- **Catégorie :** Harnais (assertion trop permissive)
- **Symptôme CI :** `e2e failure` — `getByText(/Erreur|échec|.../i).first() not found`
- **Cause :** Backend `scan_folder` retourne 1 candidate même pour invalid.pdf (plan_pdf, anchor 0) avec warning "No technical PDF found" → UI affiche "Fichiers détectés", pas erreur immédiate
- **Correction initiale (insuffisante) :** Attente flexible erreur OU Fichiers détectés OU Aucun PDF technique, puis si Fichiers détectés → clique Import → attend Erreur|État|Warning
- **Limite revue :** Cette correction initiale acceptait encore des textes génériques comme "État", "Fichiers détectés", "Choisir le PDF" qui ne prouvent pas un échec signalé
- **Correction finale (ciblée, voir #8/#9) :** Définir contrat explicite, vérifier statut terminal failed/needs_review, message utilisateur, absence faux résultat
- **Statut :** CORRIGÉ ET TESTÉ mais jugé trop permissif → repris en #8/#9

## Défaut #8 — [Harnais] setInputFiles n'est pas preuve de glisser-déposer réel + assertions téléchargement/recherche non contraignantes (revue 0bbad2d)

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts` au commit `0bbad2d`
- **Catégorie :** Harnais (preuve demandée) + Produit (data-testid result-item manquant)
- **Écarts relevés revue 0bbad2d :**
  1. `setInputFiles` teste sélection fichier, ne démontre pas que les événements `dragEnter/dragover/drop` fonctionnent
  2. Téléchargement et SHA-256 restaient facultatifs : `if (await downloadBtn.isVisible()) { download + SHA } else { expect(resultsCount).toBeGreaterThan(0) }` → test peut être vert sans télécharger
  3. Recherche ne garantit pas que document importé est retrouvé : `getByText(/GENOA|scan-en|upload/i)` peut correspondre à nom fichier ou UI, pas au résultat OCR de CET import ; `GENOA` pas unique par exécution ; `importId: "via-ui"` au lieu de vrai id
  4. data-testid drop-zone seul ne constitue pas correction métier dépôt ; data-testid result-item manquant → `locator('[data-testid="result-item"]')` not found
- **Corrections ciblées successives :**
  - `6eaa714` : remplace `querySelector('div:has-text(...)')` invalide par `Array.from(div).find(textContent includes)`
  - `5fd668f` : mock webkitGetAsEntry
  - `0bbad2d` : DataTransfer natif `items.add(file)` + `data-testid="drop-zone"` → e2e success mais assertions permissives
  - `8a5b3ff` : fix assertions contraignantes : téléchargement obligatoire (supprime fallback), terme unique par exécution via `gen_pdf_custom` + `uniqueTerm()` sans underscore (OCR lisible), capture import_id depuis `/api/imports/confirm`, vérif raw_text via `/api/imports/{id}`, recherche directe API `/api/search?q=uniq` count>0 obligatoire + UI result-item, téléchargement pièce obligatoire SHA, preuves avec vrais ids → CI `38476009010` fail OCR underscore `GENOA_D3ECDB73_97436` → `GENOA D3ECDB73 97436`
  - `07228b4` : supprime underscores, normalisation alphanum `replace(/[^A-Z0-9]/gi,'').toUpperCase()` → CI `38477938099` fail result-item missing
  - `ebdb1a2` (final) : ajoute `data-testid="result-item"` dans `ResultItem` (correction produit pour testabilité) + API search direct obligatoire + UI result-item visible 15000ms + téléchargements obligatoires → CI `38479810898` e2e success contraignant, tous jobs success
- **Détails finaux ebdb1a2 :**
  - Génération terme unique : `ZEPHYRINE{HEX}{suffix}` et `GENOA{HEX}{suffix}` via `crypto.randomBytes(4).toString('hex').toUpperCase()` sans underscore, absent nom fichier/metadata
  - `gen_pdf_custom` dans `helpers_ocr_scan.py` + `generate_scan_pdf.py` accepte 3e arg terme unique
  - Capture import_id : `page.waitForResponse(r=>r.url().includes('/api/imports/confirm'))` → `json.import_id || job_id || id`, expect truthy
  - Vérif OCR effective : GET `/api/imports/{id}` → payload.data.raw_text normalisé alphanum majuscule contient terme unique
  - Recherche : GET `/api/search?q=uniq` direct via `page.request.get` count>0 obligatoire + UI fill + Enter + click Rechercher + waitForResponse `/api/search?q=uniq` + `locator('[data-testid="result-item"]').first()` visible 15000ms, ouverture + visionneuse
  - Téléchargement obligatoire : `getByRole(link, Télécharger le PDF source)` visible → download → sha256 compare original==téléchargé ; pièce `/api/pieces/{id}/telecharger` visible → download SHA identique ; si manquant → fail (pas de fallback count)
  - Preuve attachée avec vrais identifiants `{originalSha, downloadedSha, fileName, importId, uniqueTerm, recherche, etat, rawTextContainsUniq, methode: "simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur"}`
  - Geste décrit comme `simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur` (pas geste manuel OS)
- **Rejeu final :** CI `38479810898` e2e success, backend 3.11/3.12/3.13 success, frontend, docker, ocr, sauvegarde, integration, hors-ligne, recette-corpus, recette-locale, reserves, native, perf success
- **Statut :** CORRIGÉ ET TESTÉ (produit+harnais) — CI verte `38479810898`

## Défaut #9 — [Harnais] PDF blanc et PDF invalide — assertions non contraignantes, absence preuve absence texte inventé (revue 0bbad2d)

- **Fichiers :** `frontend/e2e/scan-browser-ocr.spec.ts` tests "PDF invalide" et "fichier sans texte exploitable" au commit `0bbad2d`
- **Catégorie :** Harnais (assertion trop permissive)
- **Écarts relevés revue 0bbad2d :**
  1. Rechercher `BLANC_SANS_TEXTE_9999` ou `INVALIDE_<hash>` puis vérifier que premier résultat ne contient pas cette chaîne ne vérifie pas le contenu réellement extrait du document concerné
  2. Pour PDF invalide, chemin où Fichiers détectés visible, bouton import absent, et aucune assertion supplémentaire n'exige rejet explicite → branche peut réussir seulement parce que Fichiers détectés visible
  3. Pas de vérif données/artefacts d'extraction du document concerné (absence texte exploitable, absence données métier inventées)
- **Contrat attendu défini :**
  - **Document invalide** : backend doit refuser ou signaler échec avec issue explicite (rejet avant import avec message vérifié OU import identifié avec statut et motif conformes), pas de fiche créée avec texte inventé, statut terminal `failed` ou `needs_review` avec message, pas de résultat recherche avec terme du fichier invalide, absence traitement réussi ou fiche métier attribuable à cet envoi
  - **Document valide sans texte exploitable (blanc)** : produit peut conserver document comme fichier sans extraction (upload_status uploaded, extraction_status != success), statut terminal `needs_review` ou `failed` (pas `completed` avec extraction réussie), aucun texte métier inventé, champs "À vérifier" ou absence "Données extraites", vérif données/artefacts extraction du document concerné
- **Correction ciblée ebdb1a2 :**
  - **Invalid** : capture import_id depuis confirm si existe, upload POST `/api/imports/upload` → si !ok → expect 400/413/500 + `p.text-destructive` visible message >5 chars ; si ok → attend `Fichiers détectés` OU `Aucun PDF technique` OU `p.text-destructive` (pas texte générique État seul) ; si `Aucun PDF technique` → issue explicite vérifiée ; si `Fichiers détectés` + bouton import visible → confirm → capture import_id → attend `État : failed|needs_review` + GET `/api/imports/{id}` vérif status failed/needs_review + extraction_status != success ; si pas de bouton import → expect `Aucun PDF technique` ou erreur destructive ; recherche directe API `/api/search?q=INVALIDE_<sha8>` count 0 obligatoire (pas seulement vérif premier résultat ne contient pas chaîne)
  - **Blanc** : upload OK → Fichiers détectés → import → capture import_id → `État : needs_review|failed` pas completed, GET `/api/imports/{id}` extraction_status != success, raw_text len <20, vérif UI `À vérifier` visible si `Données extraites` visible, sinon absence Données extraites = preuve absence extraction inventée ; recherche API `/api/search?q=BLANC_SANS_TEXTE_9999` count 0 obligatoire
  - Supprime assertions génériques seules, aucune branche ne réussit seulement parce que Fichiers détectés visible
  - Recherche mot arbitraire absent ne remplace pas contrôles extraction
- **Rejeu :** CI `38479810898` e2e success, tests invalide et blanc verts avec branches explicites
- **Statut :** CORRIGÉ ET TESTÉ (harnais) — CI verte `38479810898`

## Défaut #10 — [Produit] ResultItem sans data-testid → recherche UI non testable

- **Fichier :** `frontend/components/result-item.tsx`
- **Catégorie :** Produit (testabilité)
- **Symptôme CI :** `38477938099` e2e fail `locator('[data-testid="result-item"]').first() Expected visible Timeout 10000ms element not found`
- **Cause :** `ResultItem` n'avait pas `data-testid="result-item"`, seul `archive-browser` avait `archive-piece-{id}` ; le harnais cherchait `result-item` qui n'existait pas
- **Correction :** Ajout `data-testid="result-item"` sur div root de `ResultItem`
- **Rejeu :** CI `38479810898` e2e success, result-item visible 15000ms, ouverture visionneuse, téléchargements obligatoires
- **Statut :** CORRIGÉ ET TESTÉ (produit) — amélioration testabilité, pas correction métier dépôt

## Défaut #11 — [Harnais] OCR terme unique avec underscore mal lu

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts` + `helpers_ocr_scan.py`
- **Catégorie :** Harnais (génération PDF)
- **Symptôme CI :** `38476009010` e2e fail `expect(received).toContain(expected) Expected "GENOA_D3ECDB73_97436" Received "GENOA D3ECDB73 97436; Atlantic... Unique term GENOA D3ECDB73_ 97436..."`
- **Cause :** Tesseract transforme `_` en espace, terme avec underscores non robuste OCR
- **Correction :** `uniqueTerm()` génère uniquement lettres majuscules + chiffres sans underscore (`ZEPHYRINE{HEX}{suffix}`), `gen_pdf_custom` accepte terme unique, normalisation `replace(/[^A-Z0-9]/gi,'').toUpperCase()` pour comparer raw_text
- **Rejeu :** CI `38479810898` e2e success, raw_text normalisé contient terme unique
- **Statut :** CORRIGÉ ET TESTÉ (harnais)

## Défauts antérieurs déjà corrigés (référence, catégorie Produit)

- Recherche trigrammes `_source_trigrammes` vs index GIN 023 (SHA `51d2772`) — [Produit] CORRIGÉ ET TESTÉ (279 passed, 74 reserves)
- Sauvegarde objet-only → sauvegarde conjointe PG+fichiers+MinIO (SHA `a3506c8`) — [Produit] CORRIGÉ ET TESTÉ
- Perf régression `a3506c8` p50 fonds réel 231ms vs 5ms (SHA `d202f0b`) — [Produit] CORRIGÉ ET TESTÉ (p50 5.3ms)
- OCR étage 3, garde-fou ROUGE→VERT, etc. — [Produit] CORRIGÉ ET TESTÉ

## Précisions preuves existantes (revue)

- **Sémantique** : contrat e5-small ONNX, RRF — le test réellement exécuté est `tests/test_recherche_hybride.py::test_rrf_double_vote_classe_devant` et `test_rrf_branches_vecteurs_dormante_puis_active`, avec modèle réel téléchargé via `seamtech_search.ml.telecharger` (cache `/home/runner/.cache/seamtech-e5`, job CI `Cache e5-small ONNX weights` + `Download e5-small ONNX`), requêtes synthétiques et documents attendus (ex. double vote classe devant). Si qualité sémantique non mesurée humainement, réserve conservée HORS PÉRIMÈTRE, pas présentée comme validée.
- **JUnit synthèse 13 contrôles** : fichier `2026-10-13_junit_preuves.xml` est **synthèse documentaire** générée manuellement depuis résultats exécutés (pas un journal pytest brut). Chaque testcase référence run ID et commande source (ex. `Run 38458818644 job 115508339412 success`). Les vrais journaux pytest sont dans `/tmp/*.log` et artifacts Playwright. Le fichier est nommé comme synthèse et relié aux preuves d'exécution sources dans `2026-10-13_preuves.md`.
- **Corrections séparées** : #1, #4, #5, #6, #7, #8, #9 = Harnais ; #2 = Environnement/Harnais ; #3 = Environnement (image produit)

## Non-régressions vérifiées (sur ebdb1a2, CI 38479810898)

- backend 3.11 success, 3.12 success, 3.13 success, frontend success, docker success, e2e success (après fix assertions contraignantes : téléchargement obligatoire, terme unique sans underscore, result-item data-testid, import_id réel, API search direct), ocr success, sauvegarde success, recette-corpus-reel success, recette-locale success, integration success, hors-ligne-reel success, securite-dependances success, Qualification native `38479810991` success, reserves `38479810943` success, Perf comparaison `38479810965` success
- Tous jobs verts sur ebdb1a2, 0 ÉCHEC NON RÉSOLU dans périmètre testé, assertions contraignantes vérifiées (tests échouent si téléchargement non disponible, résultat autre document, terme non extrait, PDF invalide sans rejet explicite)
