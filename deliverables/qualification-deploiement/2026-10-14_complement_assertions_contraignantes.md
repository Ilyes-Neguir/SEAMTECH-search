# Complément 2026-10-14 — Corrections après vérification indépendante — SHA 1ee9095 → c158edd (exactitude 100%)

**Date : 2026-10-14 initiale, mise à jour finale même jour après micro-écarts**
**Branche : arena/c154b2fa-seamtech-search**
**SHA avant vérif : ebdb1a28e3b43efcd7c15c7918a68e7ef03c8b7d**
**SHA après corrections contraignantes : a266559 (fix pièce/visionneuse/raw_text/extraction_status/OCR_OBLIGATOIRE)**
**SHA final avec fix sauvegarde 50k : 1ee90958c2557dee3a10a7acb5d6e983a0dce131 (CI 38538168749 verte)**
**SHA après micro-écarts doc↔code 100% : 74928de (CI 38540975618 e2e failure race)**
**SHA final avec fix race Promise.all : c158eddfd35c41c39e98f62703be153d7a40c802 (CI 38542285340 verte complète)**
**CI verte finale : 38542285340 (CI), 38542285324 (reserves), 38542285312 (native), 38542285299 (perf) — tous jobs success**

## Vérification indépendante 2026-10-13 relevés

CI 38479810898 confirmée verte, terme unique OCR-only confirmé, import_id réel confirmé, téléchargement source obligatoire confirmé, diff produit 1 ligne result-item. Restent 5 écarts :

1. Pièce non obligatoire : if visible else log sans assertion
2. Visionneuse facultative avec .catch
3. raw_text conditionnel silencieux
4. extraction_status avec défaut "failed" vacuitaire
5. Identité résultat↔import non liée par ID, unicité non documentée
6. Skip possible si tesseract absent

## Corrections a266559

- Pièce obligatoire : expect visible sans catch/else + download + SHA identique, dans deux tests
- Visionneuse obligatoire : expect visible sans catch
- raw_text obligatoire : toBeTruthy + length>10 + normalisé contient uniq, sans if
- extraction_status : toBeDefined + not failed/success, sans défaut
- Identité : API search direct count>0 + results[0].id truthy dans deux tests, unicité documentée comme preuve attribution
- OCR obligatoire : SEAMTECH_OCR_OBLIGATOIRE=1

## Vérification indépendante finale (après 1ee9095) — micro-écarts non bloquants relevés

1. Test drag-drop : pas de length>10 sur raw_text (test 1 l'a) — doc dit length>10 pour deux
2. Assertions réseau recherche UI encore en .catch(()=>null) + if — identité portée par API obligatoire + result-item obligatoire, pas faux positif, mais à ne pas présenter comme obligatoire côté UI
3. Négatifs : contrôles count 0 conditionnels (if searchResp, if importId, if apiResp.ok) — sur chemin rejet HTTP 400 sans importId, contrôle API sauté. Doc dit obligatoires.
4. Doc vs code : INVALIDE_sha8 vs INVALIDE${sha8} ; BLANC_SANS_TEXTE_9999 vs BLANCSANSTEXTE9999
5. Commit a266559 message mentionne Produit: ResultItem alors que ce commit ne touche aucun fichier produit (reliquat ebdb1a2)
6. Panneau import n'affiche État : qu'une fois polling completed — chemin needs_review accepté mais jamais démontré

## Corrections finales c158edd — exactitude doc↔code 100%

- **Test drag-drop length>10** : ajouté `expect(rawText.length).toBeGreaterThan(10)` dans drag-drop (était seulement dans sélection fichier)
- **Recherche UI obligatoire sans catch, sans race** : utilise `Promise.all([waitForResponse, actions])` pour éviter TimeoutError 10000ms (vu dans CI 38540975618 e2e failure : `page.waitForResponse: Timeout 10000ms exceeded` sur recherche UI). Avant : `await fill; await press Enter; await click; await waitForResponse.catch(()=>null); if(resp){expect}` → race + conditionnel. Après : `await fill; const [resp] = await Promise.all([waitForResponse({timeout:15000}), press Enter + click]) ; expect(count>0)` obligatoire dans deux tests positifs.
- **Sélecteur pièce durci** : `a[href*="/api/pieces/"][href*="/telecharger"]` au lieu de `a[href*="/telecharger"]` pour exclure collision avec lien import `/api/imports/{id}/artifacts/source_pdf` (import-panel.tsx : import = `/api/imports/{id}/artifacts/source_pdf`, pièce = `/api/pieces/{id}/telecharger` — distincts, vérifié)
- **Négatifs count 0 non conditionnels** :
  - Invalide : `const apiInvalidResp = await page.request.get('/api/search?q=INVALIDE${sha8}'); expect(ok); expect(count).toBe(0)` obligatoire toujours, puis UI search count 0 obligatoire avec Promise.all si input visible
  - Blanc : `const apiBlankResp = await page.request.get('/api/search?q=BLANCSANSTEXTE9999'); expect(count).toBe(0)` obligatoire toujours (BLANCSANSTEXTE9999 sans underscore, doc vs code aligné), puis UI search count 0 obligatoire
  - Supprime `if(importId)` et `if(apiResp.ok)` conditionnels qui sautaient contrôle sur chemin rejet HTTP 400 sans importId
- **Doc vs code aligné** : INVALIDE${sha8} (sans underscore, code = `INVALIDE${sha8.slice(0,8)}`), BLANCSANSTEXTE9999 (sans underscore)
- **CI** : 38540975618 e2e failure (race) → 38542285340 e2e success (fix Promise.all)

## Historique CI après corrections

- 38479810898 success ebdb1a2 (avec écarts pièce conditionnelle, visionneuse catch, raw_text if, extraction_status défaut)
- 38534090020 e2e success a266559 mais backend 3.13 fail QueryCanceled 50k (annotation coverage-suite-failed: FAILED test_restauration_50000_fiches_duree_publiee - QueryCanceled)
- 38538168749 success complet 1ee9095 (fix timeout 60s)
- 38540975618 e2e failure 74928de (micro-écarts rendus obligatoires mais race waitForResponse → TimeoutError 10000ms)
- 38542285340 success complet c158edd (fix race Promise.all)

## Preuves finales c158edd

- Code : c158eddfd35c41c39e98f62703be153d7a40c802
- CI : 38542285340 tous jobs success (13 jobs), e2e success, backend 3.11/3.12/3.13 success (avec fix 50k timeout 60s), frontend, docker, ocr, sauvegarde, integration, hors-ligne, recette-corpus, recette-locale, securite, reserves 38542285324, native 38542285312, perf 38542285299
- e2e : 4 tests verts sans skip, visionneuse obligatoire sans catch, deux téléchargements obligatoires SHA identiques avec sélecteur durci /api/pieces/, raw_text toBeTruthy length>10 dans deux tests, extraction_status toBeDefined, API search direct count>0 + results[0].id truthy dans deux tests, UI search obligatoire avec Promise.all (pas de catch), count 0 obligatoire API pour invalide/blanc, SEAMTECH_OCR_OBLIGATOIRE=1
- Vérifié directement : import-panel.tsx lien import = /api/imports/{id}/artifacts/source_pdf, pièce = /api/pieces/{id}/telecharger → sélecteurs distincts, deux téléchargements bien distincts

## SHA final unique

- SHA code : c158eddfd35c41c39e98f62703be153d7a40c802
- Identifiant : qualif-2026-10-14-c158edd
- CI verte : 38542285340
