# Revue technique du diff (pas une approbation humaine indépendante)

Revueur : agent de session. Date : 2026-10-13.
Commits : `be0f6d2` → `3e4beb5` → (ce lot).

## Recherche (023)

- `normaliser_cle_recherche` : ligatures + NFD + casefold. **Distinct** de
  `sans_accents` (dédup / extraction, `lower()`, pas de ligatures) — régression
  CI `test_normalisation_titre_parite_avec_le_sql` corrigée.
- SQL `seamtech_texte_recherche` : `IMMUTABLE PARALLEL SAFE SET search_path =
  public, pg_temp`. Dépend de `public.unaccent(regdictionary, text)` ou du
  repli `translate`. Index GIN d'expression recréés (DROP des index nus 007).
- `GET /recherche` et suggestions : deux opérandes trigrammes pliés.
- `GET /search` : termes FTS pliés côté Python (ligatures) ; tsvector
  `seamtech_unaccent` déjà en place (005).
- Réindexation : CREATE INDEX d'expression, pas de rewrite des colonnes.
  `titre` original conservé.

Limite : à 2000 fiches le planificateur peut rester en Seq Scan malgré
`idx_fiche_titre_trgm_norm` (coût trigramme mal estimé — déjà documenté
dans `recherche.py` via `SET LOCAL enable_seqscan = off` pour la source
trigrammes).

## Sauvegarde

- `--avec-references` inchangé (local sans forcer S3).
- Tests pg_dump sans module `pgserver` (échec CI backend 3.13).
- Aller-retour objets : test marqué `sauvegarde`, MinIO officiel via
  endpoint d'environnement (job CI / workflow complémentaire).

## OCR

- Extracteur v5 inchangé. Tests de parcours sur fixtures. Job CI `ocr`
  historique a déjà exécuté tesseract 5.3.4 (taux propre 1.000).
