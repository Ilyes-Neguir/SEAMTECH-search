# Statuts de clôture A–D

Les quatre points **ne sont pas présentés comme corrigés**.
Acceptation humaine : étape séparée, non signée ici.
PR #37 conservée (pas de fusion, pas de déploiement entreprise).

## Identité du code testé

| Élément | Valeur |
|---|---|
| Commit de départ (main) | `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| Commit applicatif PR #37 (1er lot) | `be0f6d2e170145a8491ebf065ef2f464fd639a63` |
| Preuves A-03 avant/après | `preuves/A-03-avant.json` / `A-03-apres.json` |
| Recette historique | `3304b91` / PR #36 — **non réécrite** |
| Branche | `arena/e7f33785-seamtech-search` |

Le commit de ce second lot (chemins HTTP, volume, lanceur) s’ajoute **par-dessus**
`be0f6d2` sans le remplacer.

Périmètre d’exécution : Linux glibc 2.36, Python 3.11.2, PostgreSQL 16.2
(pgserver, socket `/tmp/seamtech-pgdata`). CDN GitHub Releases
(`release-assets.githubusercontent.com`) **injoignable**. Choix d’exploitation
(OS, mode, objets, volume, langues OCR) : **non tranchés** (questionnaire passé).

## A — Normalisation générale de la recherche

**Statut : ÉTENDU SOUS PÉRIMÈTRE — pas une clôture atelier.**

Exécuté sur `be0f6d2` + ce lot :

- Chemins applicatifs : `GET /recherche`, `GET /recherche/suggestions`,
  `GET /search` (fichiers, tsvector `seamtech_unaccent` + clé Python pour
  ligatures côté requête).
- Données **existantes** : fiches écrites sous 022, titres non réécrits,
  migration 023, puis parité GENOIS/GÉNOIS.
- **Nouvel import** (INSERT après 023) : `cœur`/`coeur`.
- Volume **800 fiches** synthétiques accentuées (pas 11, pas 50 k — 50 k
  n’est pas une obligation tant qu’il n’est pas un besoin). Perf **mesurée**
  (p50/p95 sandbox, garde-fou 2 s, **pas un SLA atelier / 100 ms produit**).

Revue humaine des diffs et perf représentative du fonds réel : **encore
nécessaires**. Fonds réel 7792-SO : hors de ce lot.

## B — Sauvegarde / restauration conjointes

**Statut : PARTIEL.**

- PostgreSQL + fichiers locaux : exécuté (PR #37, sources indisponibles
  pendant restore).
- Objets : **non qualifiés**. MinIO officiel existe en binaire GitHub
  (`minio.linux-amd64.RELEASE.2025-09-07T16-13-09Z`) mais le CDN Releases
  est bloqué ; `go` absent pour compiler. **s3rver/moto non utilisés**.
  Lanceur : `scripts/preparer_outillage_qualification.sh`.

## C — OCR réel

**Statut : BLOQUÉ dans cet environnement — pas une impossibilité définitive.**

- `tessdata_fast` (fra/eng) **cloné** via github.com (autorisé).
- Binaire tesseract-static : même CDN bloqué. Pas de cmake/libpng-dev/
  leptonica pour compiler ici.
- Lanceur reproductible : `scripts/preparer_outillage_qualification.sh`.
- Pas de mock, pas de PASS.

Ressource manquante : HTTPS vers `release-assets.githubusercontent.com`
**ou** paquets `tesseract-ocr tesseract-ocr-fra poppler-utils`
**ou** toolchain de compilation (cmake + headers png/leptonica).

## D — Déploiement / acceptation

**Statut : PRÉPARÉ, NON EXÉCUTÉ sur cible approuvée. Non signé.**

Les choix indispensables n’ont pas été fournis. Rien n’est inféré
(Windows, Compose, R2, 50 k ne sont pas des obligations par défaut).
