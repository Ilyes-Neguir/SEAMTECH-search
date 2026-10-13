# Statuts de clôture A–D

Les quatre points **ne sont pas tous corrigés**. PR #37 **non fusionnée**.
Aucun PV humain. Recette historique intacte.

## Identité

| | |
|---|---|
| Départ | `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| Lot 1 | `be0f6d2e170145a8491ebf065ef2f464fd639a63` |
| Lot 2 | `3e4beb5e702b6e33bcbc7d5f9c2e663f2f682320` |
| Lot 3 | `0665a684133f31e5c5742faeeb1fe3104ab2a721` |
| Lot 4 (ce tour) | commit suivant sur la même branche |
| Recette historique | préservée |

## CI observée sur `0665a68` (pas un PASS global)

SHA : `0665a684133f31e5c5742faeeb1fe3104ab2a721`.

| Run | Workflow | Conclusion |
|---|---|---|
| [38402755760](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402755760) | CI push | **failure** |
| [38402762213](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402762213) | CI pull_request | **failure** |
| [38402755908](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402755908) | Qualification reserves push | **failure** |
| [38402762587](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402762587) | Qualification reserves pull_request | **failure** |
| [38402762347](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38402762347) | Scale benchmark | success |

Jobs verts sur ce SHA (CI push) : frontend, docker, e2e, recette-locale, recette-corpus-reel, hors-ligne-reel, integration, ocr, securite-dependances.

Échecs **identifiés** (annotations API, pas les zips de logs CDN) :

- **backend sqlite** : `test_selection_ci.py` — `tests/test_sauvegarde_references.py` lit `SEAMTECH_S3_ENDPOINT_URL` hors `INVENTAIRE_S3_REEL`. **Défaut de harnais.**
- **sauvegarde** / **qualification** : pytest exit 1. Cause produit la plus probable : un document **objet-only** (`s3://…`) était compté comme fichier local manquant → `complet=false`. Les journaux zip/artefacts CDN sont inaccessibles depuis ce sandbox (`results-receiver` / `blob.core.windows.net` EOF).

Lot 4 corrige ces points. Les résultats lot 4 sont ceux du SHA de ce commit, pas de `0665a68`.

## A — Recherche

**Statut : CORRIGÉ ET TESTÉ** sous périmètre sandbox PostgreSQL 16.2 +
chemins HTTP, **EN ATTENTE DE VALIDATION HUMAINE** (revue indépendante /
fonds réel).

`normaliser_cle_recherche` conserve le dépliage œ→oe ; `sans_accents` (dédup)
ne le fait plus.

Mesures 800/2000 : **indicatives**. À 2000 lignes le plan observé peut rester
un Seq Scan malgré `idx_fiche_titre_trgm_norm` (coût trigramme) — ce n'est
pas déclaré ici comme un défaut.

## B — Sauvegarde

**Statut : PARTIEL** jusqu'à exécution CI du test B-03 **sans skip**.

- PG + fichiers locaux : **CORRIGÉ ET TESTÉ** sandbox.
- Produit : document objet sans copie locale ≠ manquant local ; restore objet
  réécrit la clé d'origine (`avoid_overwrite=False`).
- B-03 MinIO officiel : exécuté par le job `sauvegarde` et
  `qualification-reserves.yml` sur le SHA lot 4.

## C — OCR

**Statut : PARTIEL**.

- Sandbox local : **BLOQUÉ** (pas de binaire tesseract).
- Job CI `ocr` historique : tesseract réel. Parcours
  import→index→recherche→téléchargement : job `ocr` **sans PostgreSQL**
  (skip honnête) ; exécution visée : `qualification-reserves.yml`
  (PG + tesseract fra/eng + MinIO).

## D — Déploiement de référence

**Statut : PRÉPARÉ NON EXÉCUTÉ.**

La procédure d'exploitation documentée (`docs/DEPLOYMENT.md`,
`GUIDE_DEMARRAGE_LOCAL.md`) est **Docker Compose / poste Windows**. La
cible d'essai autorisée pour A–C est **Linux Debian/Ubuntu natif**. Les
jobs CI `recette-locale` / `integration` (Docker) **ne sont pas** D.

Prérequis manquant : une procédure d'exploitation **native** (hors Docker)
approuvée, exécutable sur un runner vierge.

**EN ATTENTE DE VALIDATION HUMAINE** pour l'acceptation métier.
