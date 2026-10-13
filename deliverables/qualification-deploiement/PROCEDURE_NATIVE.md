# Procédure d’essai — installation native Debian/Ubuntu

**Statut :** procédure écrite. L’exécution sur machine vierge est le job
GitHub Actions `Qualification native` (`ubuntu-24.04`), pas le poste
opérateur, pas Docker Compose, pas une homologation production.

## Ce que cette procédure est

- Un essai technique **autorisé** sur un Linux jetable (runner CI ou VM
  d’essai Debian/Ubuntu).
- PostgreSQL, Redis, MinIO, API, worker et frontend tournent en
  **processus hôtes**. Aucun `docker compose up`.
- MinIO est le binaire officiel compilé depuis les sources archivées
  (tag `RELEASE.2025-09-07T16-13-09Z`, même épingle que
  `docker-compose.yml`).
- OCR : paquets `tesseract-ocr`, `tesseract-ocr-fra`, `tesseract-ocr-eng`.
- Corpus : fixtures synthétiques / anonymisées du dépôt. Pas 50 000
  documents imposés.

## Ce que cette procédure n’est pas

- Une installation sur le PC de l’opérateur.
- Une installation entreprise / production.
- Un substitut de `scripts/recette_locale.sh` (Docker).
- Un `pip install` de pytest : les tests d’intégration CI ne prouvent pas
  le démarrage d’exploitation.

## Commande

Sur Debian/Ubuntu avec `sudo`, Git, et (pour MinIO) Go 1.24 :

```bash
bash scripts/qualification_native.sh
```

Le script :

1. installe PostgreSQL + contrib, compile ou installe **pgvector**, Redis,
   Tesseract fra+eng, poppler ;
2. crée `seamtech_search` et `seamtech_restore` ;
3. démarre Redis (AOF) et MinIO **hôte**, provisionne les identités
   restreintes (`scripts/provisionner_stockage.sh`, mode
   `SEAMTECH_MINIO_MC`) ;
4. installe le Python applicatif, écrit `config.json` (`enable_ocr=true`,
   `web_worker_enabled=false`, file Redis obligatoire) ;
5. joue les migrations, crée un compte nominatif ;
6. construit le frontend (`pnpm build`) ;
7. démarre API, worker séparé, `next start` ;
8. exécute `scripts/qualification_native_controles.py` : santé, connexion,
   import + worker, recherche, OCR, téléchargement, sauvegarde/restauration
   **conjointe** (PG + fichiers + objets, sources masquées) ;
9. tue et relance l’API (PG/Redis/MinIO restent) et revérifie `/search`.

Rapport : `/tmp/seamtech-natif/backups/rapport-natif-*.txt`
(`CONTROLE|<id>|PASS|…` / `FAIL`).

## Limite si la machine manque

Sans runner/VM Debian/Ubuntu autorisé : D reste **PRÉPARÉ NON EXÉCUTÉ**.
Le sandbox de développement n’installe pas les paquets apt ; ce n’est pas
cette procédure.
