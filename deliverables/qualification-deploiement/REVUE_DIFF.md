# Revue technique du diff (pas une approbation humaine indépendante)

Revueur : agent de session. Date : 2026-10-13.
Commits : `be0f6d2` → `3e4beb5` → `0665a68` → lot 4.

## Recherche (023)

- `normaliser_cle_recherche` : ligatures + NFD + casefold. **Distinct** de
  `sans_accents` (dédup / extraction, `lower()`, pas de ligatures).
- SQL `seamtech_texte_recherche` : `IMMUTABLE PARALLEL SAFE SET search_path =
  public, pg_temp`.
- Volume 800/2000 : mesures **indicatives**. Un Seq Scan à 2000 lignes n'est
  pas déclaré automatiquement comme un défaut (coût trigramme vs lecture
  séquentielle courte).

## Sauvegarde

- CI `0665a68` job `sauvegarde` : échec pytest (exit 1). Cause produit
  identifiée dans le code : `copier_fichiers_locaux` traitait `s3://…` comme
  fichier local manquant, donc `complet=false` sur B-03.
- Lot 4 : objet-only ignoré côté copies locales ; `restaurer_objets_s3`
  utilise `avoid_overwrite=False` (clé catalogue). Inventaire S3 complété.

## OCR

- Extracteur v6 : langue `fra+eng` si les deux paquets tesseract sont là.
- Parcours crawl → upsert → GET `/search` → POST `/open` (qualification-reserves).
- Job `ocr` : toujours sans PostgreSQL ; le parcours complet s'y saute.

## D

Hors périmètre exécuté : procédure documentée = Docker ; cible d'essai = Linux natif.
