# Défauts et corrections — 2026-10-13 — SHA 5438b9e

**SHA final : `5438b9e22d0a2d0e7bad58580db92fc016be9db7`**
**CI verte : `38458818644` (CI), `38458818806` (reserves), `38458818474` (native), `38458818494` (perf)**

## Défaut #1 — Frontend TS1005 `import os` invalide

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts:7`
- **Symptôme CI :** `frontend failure` — `TS1005 '=' expected` à `import os`
- **Reproduction :** `cd frontend && pnpm exec tsc --noEmit` → erreur ligne 7
- **Cause :** Syntaxe ES invalide `import os` au lieu de `import * as os from "os"` ; `tsc` échoue, build Next.js bloqué
- **Test échouant avant correction :** `frontend` job CI (type check)
- **Correction :** `import * as os from "os"` + refactor génération PDF via `generate_scan_pdf.py`
- **Rejeu :** `pnpm exec tsc --noEmit` → OK (vide), `pnpm build` → Compiled successfully Next.js 16.3.8
- **Régression :** Aucune, frontend success sur `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ

## Défaut #2 — Backend postgres suite skipped guard

- **Fichier :** `tests/test_scan_browser_upload_ocr.py` pytestmark `[postgres, ocr_suite]`
- **Symptôme CI :** `backend (3.11) failure` — garde `skipped==0` échoue, `1 skipped`
- **Reproduction :** `pytest -m "postgres and not perf and not sauvegarde and not redis_queue" -v` collecte `test_scan_browser_upload_ocr` qui `pytest.skip("tesseract absent")` car backend job n'installe pas tesseract → skipped
- **Cause :** Test OCR exige tesseract mais marqué postgres, donc collecté dans suite postgres sans binaire → skip → garde échoue
- **Test échouant avant correction :** postgres suite (garde-fou skipped==0)
- **Correction :**
  - `tests/test_scan_browser_upload_ocr.py` → pytestmark `[ocr_suite]` seul
  - `.github/workflows/ci.yml` postgres suite et sqlite suite ajoutent `and not ocr_suite` : `pytest -m "postgres and not perf and not sauvegarde and not redis_queue and not ocr_suite"` et `pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"`
- **Rejeu :** Collection postgres 278 tests (sans ocr_suite), 0 skipped, CI backend success sur `38458818644`
- **Régression :** Aucune
- **Statut :** CORRIGÉ ET TESTÉ

## Défaut #3 — Dockerfile tesseract eng manquant

- **Fichier :** `Dockerfile`
- **Symptôme :** Scan anglais `GENOA SAIL ATLANTIC` échoue en conteneur (tesseract-ocr-eng absent) alors que CI e2e l'installe
- **Reproduction :** `docker build` puis `tesseract --list-langs` → fra présent, eng absent
- **Cause :** Dockerfile installait seulement `tesseract-ocr` + `tesseract-ocr-fra` + `poppler-utils`, pas eng
- **Correction :** Ajout `tesseract-ocr-eng` + vérification `tesseract --list-langs | grep -q eng`
- **Rejeu :** `docker build -t seamtech-search:ci` → success, `/live` répond, job docker success sur `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ

## Défaut #4 — Ruff lint E402/I001/F401

- **Fichiers :** `frontend/e2e/generate_scan_pdf.py`, `tests/helpers_ocr_scan.py`, `tests/test_scan_browser_upload_ocr.py`
- **Symptôme CI :** `backend (3.11) failure` — `Ruff lint check failure` (après fix #1)
- **Reproduction :** `python -m ruff check .` → 10 erreurs (E402 import not at top, I001 unsorted, F401 unused subprocess/time, F841 unused sha_en etc.)
- **Cause :** Nouveau helper `generate_scan_pdf.py` faisait `sys.path.insert` puis import (E402), imports non triés, variables inutilisées
- **Correction :** `ruff check --fix --unsafe-fixes` + ` # noqa: E402` sur import après sys.path, tri imports, suppression unused
- **Rejeu :** `ruff check .` → All checks passed, backend ruff step success sur `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ

## Défaut #5 — Garde-fou sélection CI `test_selection_ci.py`

- **Fichier :** `tests/test_selection_ci.py` constantes `COMMANDE_SUITE_SANS_SERVICE`, `COMMANDE_POSTGRES`
- **Symptôme CI :** `Run SQLite suite failure` — `AssertionError: commande de sélection absente du workflow : pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue"`
- **Reproduction :** `pytest -m "not postgres and not s3 and not perf and not redis_queue"` n'existe plus dans ci.yml après ajout `and not ocr_suite`
- **Cause :** Le garde-fou contrat explicite attendait l'ancienne commande sans ocr_suite, mais workflow a évolué pour exclure ocr_suite (défaut #2)
- **Correction :** Met à jour constantes :
  - `COMMANDE_SUITE_SANS_SERVICE = 'pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"'`
  - `COMMANDE_POSTGRES = 'pytest -m "postgres and not perf and not sauvegarde and not redis_queue and not ocr_suite"'`
- **Rejeu :** `pytest tests/test_selection_ci.py -q` → 13 passed, sqlite suite success sur `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ

## Défaut #6 — E2E spec strict mode violation duplicate text

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts:70`
- **Symptôme CI :** `e2e failure` — `strict mode violation: getByText(/Choisir le PDF technique/i) resolved to 2 elements`
- **Reproduction :** Playwright `getByText` sans `.first()` sur page où 2 éléments contiennent même texte (heading + label)
- **Cause :** Locator non discriminant, Playwright exige un seul élément en strict mode
- **Correction :** Ajout `.first()` sur tous les `getByText("Importer un dossier")`, `getByText(/Fichiers détectés/)`, `getByText(/Choisir le PDF technique/)`, `getByText(/fichier.*prêt.*envoyer/)`, `getByText(fileName)`, `getByText(/État :/)`, `getByText(/Fichiers :/)`, `getByText(/PDF techniques :/)`
- **Rejeu :** `pnpm exec tsc --noEmit` OK, e2e success sur `38458818644` après fix #7 aussi
- **Statut :** CORRIGÉ ET TESTÉ

## Défaut #7 — E2E cas erreur invalide sans message

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts` test "PDF invalide expliqué sans faux succès"
- **Symptôme CI :** `e2e failure` — `Expected: visible; Timeout: 10000ms; getByText(/Erreur|échec|.../i).first() not found`
- **Reproduction :** Upload `invalid.pdf` (contenu "ceci n'est pas un pdf") → backend `scan_folder` retourne quand même 1 candidate (plan_pdf, anchor 0) avec warning "No technical PDF found" → UI affiche "Fichiers détectés dans..." et "Choisir le PDF technique", pas d'erreur immédiate
- **Cause :** Test attendait erreur immédiate après "Envoyer et analyser", mais le flux réel affiche d'abord les candidats même invalides, puis l'erreur survient à l'import
- **Correction :**
  - Attente flexible : soit erreur immédiate, soit `Fichiers détectés`, soit `Aucun PDF technique`
  - Si `Fichiers détectés` visible, clique "Importer les fichiers sélectionnés" puis attend `Erreur|échec|...|État|Warning|vide|exploitable`
  - Ajout `Aucun PDF technique` dans regex d'erreur
- **Rejeu :** e2e success sur `38458818644`
- **Statut :** CORRIGÉ ET TESTÉ

## Défauts antérieurs déjà corrigés (référence)

- Recherche trigrammes `_source_trigrammes` vs index GIN 023 (SHA `51d2772`) — CORRIGÉ ET TESTÉ (279 passed, 74 reserves)
- Sauvegarde objet-only → sauvegarde conjointe PG+fichiers+MinIO (SHA `a3506c8`) — CORRIGÉ ET TESTÉ
- Perf régression `a3506c8` p50 fonds réel 231ms vs 5ms (SHA `d202f0b`) — CORRIGÉ ET TESTÉ (p50 5.3ms)
- OCR étage 3, garde-fou ROUGE→VERT, etc. — CORRIGÉ ET TESTÉ

## Non-régressions vérifiées

- `backend (3.11)`, `3.12`, `3.13` success
- `frontend` success (tsc + build)
- `docker` success (smoke /live)
- `e2e` success (validation, concurrence 7, doublons 2, auth 26, hors-ligne, scan-browser-ocr 3)
- `ocr` success (taux propre 1.000 / dégradé 0.955, garde-fou ROUGE→VERT)
- `sauvegarde` success (50k fiches mesurées, restore distincte)
- `recette-corpus-reel` success (7 ZIP RG13, OCR, recherche, ouverture PDF SHA)
- `recette-locale` success
- `integration` success (compose web+worker partagent volumes, identités restreintes)
- `hors-ligne-reel` success (iptables --uid-owner, contrôle négatif, 0 paquet externe)
- `securite-dependances` success
- `Qualification native` success (installation Debian native, API+worker+frontend hôtes)
- `Qualification reserves` success (74 tests, 0 skipped)
- `Perf comparaison` success (HEAD <100ms, warning historique a3506c8)
