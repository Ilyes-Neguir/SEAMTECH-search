# Complément 2026-10-13 — Assertions contraignantes scan-browser-ocr — SHA ebdb1a2

**Date : 2026-10-13**
**Branche : arena/c154b2fa-seamtech-search**
**SHA code fonctionnel : ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d**
**CI verte : 38479810898 (CI), 38479810943 (reserves), 38479810991 (native), 38479810965 (perf)**

## Relecture directe au commit 0bbad2d

Le dépôt via DataTransfer exerce maintenant les handlers navigateur (simulation dragenter/dragover/drop avec DataTransfer natif dans vrai navigateur, pas geste manuel OS). Conservé.

Problèmes relevés :

1. Téléchargement et SHA-256 restaient facultatifs (fallback count)
2. Recherche ne garantissait pas que document importé est retrouvé (GENOA|scan-en|upload générique, importId via-ui, GENOA pas unique par exécution, pas de capture import_id réel)
3. Tests négatifs ne prouvaient pas absence texte inventé (recherche mot arbitraire, pas de vérif extraction du document concerné, branche Fichiers détectés visible sans assertion supplémentaire)

## Corrections assertions restantes (ebdb1a2)

### Produit

- `frontend/components/result-item.tsx` : ajout `data-testid="result-item"` sur div root — correction produit pour testabilité (permet `locator('[data-testid="result-item"]')` visible). À elle seule, `data-testid="drop-zone"` (ImportPanel) ne constitue pas correction métier dépôt, c'est amélioration harnais.
- `frontend/components/import-panel.tsx` : `data-testid="drop-zone"` déjà présent (amélioration harnais)

### Harnais

- `tests/helpers_ocr_scan.py` : nouvelle fonction `gen_pdf_custom(tmp_dir, unique_term, lang, filename)` qui génère PDF image-only avec terme fourni par appelant, absent nom fichier/metadata
- `frontend/e2e/generate_scan_pdf.py` : accepte 3e arg `unique_term`, appelle `gen_pdf_custom` si présent
- `frontend/e2e/scan-browser-ocr.spec.ts` :

**Terme unique par exécution**
- `uniqueTerm(prefix)` : `crypto.randomBytes(4).toString('hex').toUpperCase()` + suffix timestamp, nettoyé `[^A-Z0-9]` → uniquement lettres majuscules + chiffres, sans underscore (OCR lit `_` comme espace → échec `GENOA_D3ECDB73_97436` → `GENOA D3ECDB73 97436` constaté CI 38476009010)
- Exemples : `ZEPHYRINEA1B2C3D4E51234`, `GENOA5F6E7D8C9B0A1234`
- Absent nom fichier (scan-fr.pdf) et métadonnées, lisible OCR

**Téléchargement obligatoire**
- Supprime fallback `else { expect(resultsCount).toBeGreaterThan(0) }`
- Exige : bouton visible `Télécharger le PDF source` (import panel) + download event + fichier récupéré + SHA-256 identique original + preuve jointe ; si manque → fail
- Exige aussi téléchargement pièce `/api/pieces/{id}/telecharger` SHA identique (si visible, obligatoire ; si non visible, déjà fail sur import download)

**Lien import, OCR et résultat**
- Capture vrai import_id depuis réponse réseau `POST /api/imports/confirm` via `page.waitForResponse(r=>url.includes('/api/imports/confirm') && method POST)` → `json.import_id || job_id || id`, expect truthy, log
- Attend statut attendu pour CET import précis : `État : completed|needs_review` (needs_review documenté comme nominal possible si extraction partielle, mais on exige quand même OCR/indexation/téléchargement)
- Vérifie extraction effective via `GET /api/imports/{id}` → payload.data.raw_text normalisé `replace(/[^A-Z0-9]/gi,'').toUpperCase()` contient terme unique normalisé (tolérance OCR ponctuation→espace)
- Recherche directe API obligatoire : `page.request.get('/api/search?q=uniq')` count>0, results[0].id truthy
- Recherche UI : fill uniq + Enter + click bouton Rechercher + waitForResponse `/api/search?q=uniq` count>0 + `locator('[data-testid="result-item"]').first()` visible 15000ms
- Vérifie identité résultat : terme unique par exécution garantit que résultat appartient à CET import (count idéal 1, sinon au moins 1 avec id), ouverture result-item + visionneuse-piece visible, téléchargement pièce SHA identique
- Consigne vrais identifiants dans preuve, pas via-ui : `{originalSha, downloadedSha, fileName, importId, uniqueTerm, recherche, etat, rawTextContainsUniq, methode}`

**PDF invalide et blanc**
- Pour chaque branche, exige issue explicite : rejet avant import avec message vérifié OU import identifié avec statut et motif conformes contrat, aucune branche ne réussit seulement parce que Fichiers détectés visible
- Invalid : upload POST `/api/imports/upload` → si !ok → 400/413/500 + p.text-destructive message >5 chars ; si ok → attend Fichiers détectés OU Aucun PDF technique OU p.text-destructive ; si Aucun PDF technique → issue explicite ; si Fichiers détectés + bouton import visible → confirm capture import_id → État failed|needs_review + GET /api/imports/{id} status failed/needs_review + extraction_status != success ; recherche API `/api/search?q=INVALIDE_sha8` count 0 obligatoire
- Blanc : upload OK → Fichiers détectés → import → capture import_id → État needs_review|failed pas completed, GET /api/imports/{id} extraction_status != success, raw_text len <20, À vérifier visible si Données extraites visible sinon absence Données extraites = preuve absence extraction inventée, recherche API BLANC_SANS_TEXTE_9999 count 0

**Prouver assertions contraignantes**
- Tests échouent si : téléchargement non disponible (expect visible fail), résultat autre document (count 0 fail car terme unique), terme non extrait (raw_text normalisé fail + API search count 0 fail), PDF invalide sans rejet explicite ni état erreur (expect fail)
- Contrôles négatifs isolés du harnais conservés, parcours positifs avec vrais services (PostgreSQL pgvector:pg16, Redis 7-alpine, MinIO RELEASE.2025-09-07, Tesseract 5.x fra+eng)

## Historique CI

- 38468805294 fail selector :has-text invalid
- 38470099513 fail mock items
- 38471404314 success natif mais assertions permissives
- 38476009010 fail OCR underscore GENOA_D3ECDB73_97436 → GENOA D3ECDB73 97436
- 38477938099 fail result-item missing
- 38479810898 success contraignant (ebdb1a2)

## Preuves

- Code : ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d
- CI : 38479810898 tous jobs success, e2e success, backend 3.11/3.12/3.13 success, frontend, docker, ocr, sauvegarde, integration, hors-ligne, recette-corpus, recette-locale, reserves, native, perf success
- JUnit : 2026-10-13_junit_preuves.xml 16 tests, nature synthèse documentaire
- Matrice : SCAN-01a/b, SCAN-05a/b mis à jour avec terme unique par exécution, import_id réel, téléchargement obligatoire, lien OCR/recherche
- Preuves SHA : attach JSON avec vrais importId et uniqueTerm

## Limites et distinctions

- Geste décrit comme `simulation dragenter/dragover/drop avec DataTransfer natif dans un vrai navigateur` (pas geste manuel explorateur système)
- Distinction correction produit (ResultItem data-testid) vs amélioration harnais (drop-zone data-testid)
- Préserve résultats valides déjà obtenus (recherche, sauvegarde, etc.)
- Ne fusionne rien sans autorisation

## SHA final unique

- SHA code : ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d
- Identifiant : qualif-2026-10-13-ebdb1a2
- CI verte : 38479810898
