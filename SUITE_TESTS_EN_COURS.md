# SUITE DES TESTS — état exact (mise à jour en continu)

Branche : `arena/a4dbb7f0-seamtech-search` — **SHA évalué `2340826`**
(`f9b3235` → `b38ee39` → `7eb70fb` → `dc0021a` → `2340826`).

## État : CAMPAGNE TERMINÉE

Rapport final : `docs/RAPPORT_PASSE_FINALE_2026-10-29.md`.
CI verte sur `2340826` (tous les jobs + `qualification-native`,
`qualification-reserves`, `perf-comparaison`).

## Bac à sable du 29/10 (reconstruit deux fois — l'instantané est purgé)

- `.venv` + `requirements-local.txt` + `pgserver`, `redislite`, `ninja`, `gyp-next`
- PostgreSQL **16.2** réel (`UTF8`, port 5432, trust), **sans `contrib`** :
  `unaccent.so` et `pg_trgm.so` compilés depuis les sources `REL_16_2`
  (en-têtes générés à la main : `errcodes.h`, `nodetags.h`, `lwlocknames.h`,
  `fmgrprotos.h`, `pg_ts_dict_d.h`, `pg_type_d.h`)
- Redis 6.2.14 (`redislite`) 127.0.0.1:6390
- Chromium 153.0.8010.0 (`@sparticuz/chromium`) + NSPR/NSS compilés
- OCR : shim `tesseract.js` (WASM) `/home/user/tools/shim-tesseract.js`
  via le wrapper `/home/user/sbx/bin/tesseract` — **pas** tesseract natif

## Mesures finales sur `2340826`

| Commande | Résultat |
|---|---|
| `.venv/bin/ruff check .` | `All checks passed!` |
| suite SQLite | **1128 passés, 27 sautés**, 619 désélectionnés (104 s) |
| suite PostgreSQL | **418 passés, 3 sautés** (217 s) |
| suite OCR (shim) | **88 passés, 5 sautés** (19 s) |
| frontend unit (`node --test lib/*.test.ts`) | **10 passés** |
| `npx tsc --noEmit` | 0 erreur |
| `pnpm build` | rc=0 |
| e2e mode CI (SQLite) | **49 passés, 64 sautés, 0 échec** |
| e2e live PostgreSQL + Redis + OCR | **112 passés, 1 sauté, 0 échec** |
| `pip-audit` / `pnpm audit --prod` | aucune vulnérabilité |
| axe-core | **73 écrans, 6 rôles, 0 violation** |

## Modules d'épreuve par fonction (141 tests, tous verts)

`tests/test_passe_finale_ingestion_dossiers.py` 22 ·
`…recherche_variantes.py` 31 · `…confiance_seuils.py` 13 ·
`…matrice_roles.py` 36 · `…extraction_pdf.py` 9 · `…sessions.py` 6 ·
`…comparaison_accessoires.py` 20 · `…repli_recherche.py` 4

## Défauts corrigés cette passe

1. `POST /lots/{id}/annuler` → 500 sur lot inconnu, désormais 404 (`b38ee39`).
2. Repli `unaccent` non honoré par le dépôt de fiches (`2340826`).
3. `e2e/validation.spec.ts:99` instable (révision non attendue) (`b38ee39`).
4. Garde-fou CI « live validation » 13 → 14 tests (`7eb70fb`).
5. Docstring `test_pdf_orientation.py` : provenance `xfail` invérifiable (`dc0021a`).

## Reste à faire

Rien en bac à sable. Les points non prouvables (Windows/Docker Desktop,
PostgreSQL natif de production, MinIO/R2, Chrome/Edge natifs, volumétrie,
endurance, coupure d'alimentation, restauration RG13, tesseract natif) sont
listés au §6 du rapport final avec leur procédure de qualification réelle.
