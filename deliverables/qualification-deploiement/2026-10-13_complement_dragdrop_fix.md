# Complément 2026-10-13 — Fix drag-drop réel navigateur — SHA 0bbad2d → d08f29f

**Date : 2026-10-13**
**Branche : arena/c154b2fa-seamtech-search**
**SHA code fonctionnel : 0bbad2d338b661af20d6b6fff9ce2683902bb72a**
**SHA final avec livrables : d08f29f (en cours, CI 38473153801 e2e success, backend 3.11 success, 3.12/3.13 en cours identique à 0bbad2d)**
**CI verte de référence : 38471404314 (code seul) — tous jobs success**

## Contexte

La revue a relevé que `setInputFiles` ne prouve pas le geste réel glisser-déposer. Le test initial utilisait `querySelector('div:has-text("déposez ici...")')` qui lève `SyntaxError: ':has-text(...)' is not a valid selector` dans `page.evaluate` (Playwright selector, pas CSS). Ensuite, un mock `DataTransfer` avec `items` custom et `files [file]` ne déclenchait pas `filesFromDrop` (qui préfère `items.webkitGetAsEntry` et retournait vide → `dropped` non mis → `fichiers prêts à envoyer` non visible).

## Corrections

### Produit
- `frontend/components/import-panel.tsx` : ajout `data-testid="drop-zone"` sur la div `border-dashed` qui porte `onDragOver/onDrop`. Permet sélection fiable sans dépendre du texte accentué.

### Harnais e2e
- `frontend/e2e/scan-browser-ocr.spec.ts` :
  - Helper `dragDropFileViaEvents` réécrit :
    - Lecture fichier Node `fs.readFileSync → base64`
    - `page.evaluate` : `atob → Uint8Array → new File([byteArray], name, type pdf)`
    - `DataTransfer` natif : `const dt = new DataTransfer(); dt.items.add(file); dt.dropEffect='copy'; dt.effectAllowed='all'`
    - Sélection zone : `[data-testid="drop-zone"]` sinon fallback texte `déposez ici un dossier de référence`
    - Dispatch `dragenter`, `dragover`, petit délai 80ms, puis `drop`
    - Attente 1500ms pour `filesFromDrop` async (`readEntry` Promise)
  - Regex accents : utilise `/fichier.*prêt.*envoyer/i`, `/Fichiers détectés dans/i`, `/État :/i`, `/Télécharger le PDF source/i` (accents conservés, UTF-8)
  - 4 tests :
    1. sélection fichier `setInputFiles` → OCR → recherche `ZEPHYRINE_ÉLÉGANCE_42` → SHA attach `preuve-sha`
    2. véritable glisser-déposer `dragenter/dragover/drop` → OCR → recherche `GENOA` → SHA attach `preuve-sha-dragdrop`
    3. PDF invalide contrat échec signalé
    4. Blanc sans texte exploitable contrat conservé sans extraction

## Historique CI

- `6eaa714` : fix selector `:has-text` → `Array.from(div).find(textContent includes)` — CI `38468805294` e2e fail `getByText(/fichier.*prêt.*envoyer/) not found` (filesFromDrop vide)
- `5fd668f` : mock `webkitGetAsEntry` → CI `38470099513` même fail
- `0bbad2d` : DataTransfer natif + data-testid → CI `38471404314` e2e success, tous jobs success (backend 3.11/3.12/3.13, frontend, docker, ocr, sauvegarde, integration, hors-ligne, recette-corpus, recette-locale, securite-dependances, reserves, native, perf)
- `d08f29f` : docs MAJ matrice/preuves/defauts/junit — CI `38473153801` e2e success (backend 3.11 success, 3.12/3.13 en cours, identique code 0bbad2d donc attendu success)

## Preuves

- **Code** : `frontend/components/import-panel.tsx` ligne `data-testid="drop-zone"`
- **Test** : `frontend/e2e/scan-browser-ocr.spec.ts` fonction `dragDropFileViaEvents` + 4 tests
- **CI** : runs `38468805294` (fail selector), `38470099513` (fail mock), `38471404314` (success natif), `38473153801` (success docs)
- **Annotations e2e** : `gh api .../check-runs/{id}/annotations` montrait `SyntaxError :has-text invalid selector` puis `expect toBeVisible getByText(/fichier.*prêt.*envoyer/) timeout 8000ms element not found` → corrigé
- **SHA** : `sha256(filePath)` original == téléchargé, attach Playwright `preuve-sha` et `preuve-sha-dragdrop` JSON `{originalSha, downloadedSha, fileName, methode}`
- **Termes uniques** : `ZEPHYRINE_ÉLÉGANCE_42` (fr) et `GENOA` (en) hors noms fichiers/métadonnées, générés via `generate_scan_pdf.py` PDF image sans couche texte (pdfplumber 0)
- **Statuts** : `État : completed|needs_review|failed` vérifié avec valeur, pas seulement présence texte, pour invalide/blanc `failed|needs_review` pas `completed`

## Matrice mise à jour

- SCAN-01b CORRIGÉ ET TESTÉ (produit+harnais) avec preuve CI verte 38471404314
- SCAN-05a/b CORRIGÉ ET TESTÉ avec contrat explicite (pas de textes génériques "État" seul comme preuve)
- Tous contrôles PASS sur 0bbad2d, 0 ÉCHEC NON RÉSOLU dans périmètre testé
- Distinction CORRIGÉ ET TESTÉ / TESTÉ SANS CORRECTION / BLOQUÉ EXTERNE / HORS PÉRIMÈTRE conservée

## Limites

- Tesseract absent localement → tests e2e skip explicite `SEAMTECH_OCR_OBLIGATOIRE` (pas PASS falsifié)
- Modèle sémantique e5-small : contrat RRF double vote mesuré, qualité humaine HORS PÉRIMÈTRE
- Hors-ligne : processus couverts uid `seamtech-app`, non couverts Chromium/Playwright (documenté)
- Performance : critères existants conservés, seuils non relevés, perf synthétique signalée comme telle

## Procédure reproductible

```bash
git checkout arena/c154b2fa-seamtech-search
git log --oneline -3  # 0bbad2d code, d08f29f docs
pnpm --filter frontend exec tsc --noEmit
gh run list --branch arena/c154b2fa-seamtech-search --limit 5
# Attendre CI 38471404314 ou 38473153801 → tous jobs success, e2e success
# Vérifier annotations e2e-tail si fail
```

## SHA final unique

- **SHA code fonctionnel** : `0bbad2d338b661af20d6b6fff9ce2683902bb72a`
- **SHA final avec livrables** : `d08f29f...` (commit docs, code identique à 0bbad2d)
- **Identifiant** : `qualif-2026-10-13-0bbad2d`
- **CI verte** : `38471404314` (code), `38473153801` (docs, e2e success, backend 3.11 success, reste en cours identique)
