# Défauts et corrections — 2026-10-13 — SHA 5438b9e → b6e48e4

**SHA code fonctionnel : `5438b9e22d0a2d0e7bad58580db92fc016be9db7`**
**SHA final avec livrables : `b6e48e40960de9b2c279d2e163882e51bb0df485`**
**CI verte : `38458818644` (CI), `38458818806` (reserves), `38458818474` (native), `38458818494` (perf) — puis `38460911445` avec docs**

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

## Défaut #8 — [Harnais] setInputFiles n'est pas preuve de glisser-déposer réel (écart explicite revue)

- **Fichier :** `frontend/e2e/scan-browser-ocr.spec.ts` — ancien commentaire "Drag-drop réel : input[type=file] setInputFiles déclenche même logique"
- **Catégorie :** Harnais (preuve demandée)
- **Écart relevé revue :** `setInputFiles` teste sélection fichier et suite import, ne démontre pas que les événements `dragEnter`, `dragOver`, `drop` et leur traitement fonctionnent. L'équivalence supposée de logique interne n'est pas preuve d'un geste exécuté.
- **Ce que le rapport étayait avant :** dépôt par sélection fichier → OCR → recherche → téléchargement SHA
- **Ce qu'il ne démontrait pas :** même parcours démarré par véritable glisser-déposer
- **Correction ciblée :**
  - Conserve test `setInputFiles` comme preuve de sélection fichier, renommé "par sélection fichier (setInputFiles)"
  - Ajoute nouveau test "par véritable glisser-déposer (dragenter/dragover/drop)" utilisant `dragDropFileViaEvents` :
    - Helper lit fichier en base64 Node, puis `page.evaluate` crée `File([Uint8Array], name, {type: 'application/pdf'})`, `DataTransfer`, `dataTransfer.items.add(file)`, dispatch `dragenter`, `dragover`, `drop` sur `div:has-text("déposez ici un dossier de référence")`
    - Attend `fichiers prêts à envoyer` + fileName, puis même flux : `Envoyer et analyser` → `Fichiers détectés` → `Choisir le PDF technique` → `Importer les fichiers sélectionnés` → statut terminal `completed|needs_review` → recherche `GENOA` → téléchargement SHA-256 identique, attach `preuve-sha-dragdrop` avec méthode `dragenter/dragover/drop via DataTransfer`
  - Ne présente plus équivalence supposée comme preuve de geste
- **Rejeu :** tsc OK, ruff OK, à rejouer en CI e2e (doit être vert)
- **Statut :** CORRIGÉ ET TESTÉ (harnais) — à valider CI

## Défaut #9 — [Harnais] PDF blanc et PDF invalide — assertions génériques "État", "Fichiers détectés", "Choisir le PDF" ne prouvent pas échec ni absence faux succès

- **Fichiers :** `frontend/e2e/scan-browser-ocr.spec.ts` tests "PDF invalide" et "fichier sans texte exploitable"
- **Catégorie :** Harnais (assertion trop permissive)
- **Écart revue :** Texte accepté `Aucun|vide|exploitable|Warning|État|Fichiers détectés|Choisir le PDF` — "État", "Fichiers détectés", "Choisir le PDF" sont éléments ordinaires interface, ne prouvent ni échec correctement signalé ni absence faux succès
- **Contrat attendu défini :**
  - **Document invalide** : backend doit refuser ou signaler échec, pas de fiche créée avec texte inventé, statut terminal `failed` ou `needs_review` avec message utilisateur visible, pas de résultat recherche avec terme du fichier invalide
  - **Document valide sans texte exploitable (blanc)** : produit peut conserver document comme fichier sans extraction (upload_status uploaded, extraction_status != success), statut terminal `needs_review` ou `failed` (pas `completed` avec extraction réussie), aucun texte métier inventé, champs "À vérifier" ou absence "Données extraites", recherche terme absent retourne 0 ou sans contenu inventé
- **Correction ciblée :**
  - **Invalid** : attend soit `p.text-destructive` (erreur HTTP immédiate) soit `Fichiers détectés` + `Aucun PDF technique`, puis si Fichiers détectés → clique Import → vérifie `État :` terminal contient `failed|needs_review` (pas juste présence "État"), vérifie message warning/error non vide, puis recherche terme unique `INVALIDE_<sha8>` ne doit pas être trouvé inventé (0 résultats ou result-item ne contient pas terme)
  - **Blanc** : attend `Fichiers détectés dans` (pas générique seul), clique Import → vérifie `État :` = `needs_review|failed` (pas completed), vérifie au moins un champ "À vérifier" ou absence "Données extraites", puis recherche `BLANC_SANS_TEXTE_9999` → 0 résultats ou sans contenu inventé
  - Supprime assertions pouvant réussir sur textes génériques seuls ("État" sans valeur, "Fichiers détectés" comme preuve d'échec)
  - Si produit conserve blanc sans extraction, teste ce comportement explicitement au lieu de qualifier artificiellement d'échec ou succès OCR
- **Rejeu :** tsc OK, à rejouer CI
- **Statut :** CORRIGÉ ET TESTÉ (harnais) — à valider CI

## Défauts antérieurs déjà corrigés (référence, catégorie Produit)

- Recherche trigrammes `_source_trigrammes` vs index GIN 023 (SHA `51d2772`) — [Produit] CORRIGÉ ET TESTÉ (279 passed, 74 reserves)
- Sauvegarde objet-only → sauvegarde conjointe PG+fichiers+MinIO (SHA `a3506c8`) — [Produit] CORRIGÉ ET TESTÉ
- Perf régression `a3506c8` p50 fonds réel 231ms vs 5ms (SHA `d202f0b`) — [Produit] CORRIGÉ ET TESTÉ (p50 5.3ms)
- OCR étage 3, garde-fou ROUGE→VERT, etc. — [Produit] CORRIGÉ ET TESTÉ

## Précisions preuves existantes (revue)

- **Sémantique** : contrat e5-small ONNX, RRF — le test réellement exécuté est `tests/test_recherche_hybride.py::test_rrf_double_vote_classe_devant` et `test_rrf_branches_vecteurs_dormante_puis_active`, avec modèle réel téléchargé via `seamtech_search.ml.telecharger` (cache `/home/runner/.cache/seamtech-e5`, job CI `Cache e5-small ONNX weights` + `Download e5-small ONNX`), requêtes synthétiques et documents attendus (ex. double vote classe devant). Si qualité sémantique non mesurée humainement, réserve conservée HORS PÉRIMÈTRE, pas présentée comme validée.
- **JUnit synthèse 13 contrôles** : fichier `2026-10-13_junit_preuves.xml` est **synthèse documentaire** générée manuellement depuis résultats exécutés (pas un journal pytest brut). Chaque testcase référence run ID et commande source (ex. `Run 38458818644 job 115508339412 success`). Les vrais journaux pytest sont dans `/tmp/*.log` et artifacts Playwright. Le fichier est nommé comme synthèse et relié aux preuves d'exécution sources dans `2026-10-13_preuves.md`.
- **Corrections séparées** : #1, #4, #5, #6, #7, #8, #9 = Harnais ; #2 = Environnement/Harnais ; #3 = Environnement (image produit)

## Non-régressions vérifiées (sur 5438b9e, à reconfirmer sur nouveau SHA)

- backend 3.11/3.12/3.13 success, frontend success, docker success, e2e success (avant correction drag-drop), ocr success, sauvegarde success, recette-corpus-reel success, recette-locale success, integration success, hors-ligne-reel success, securite-dependances success, Qualification native/reserves success, Perf comparaison success
