# Cible d'exploitation de référence (proposée, non approuvée)

Cette cible n'a **pas** été autorisée ni installée. Les contrôles qui en
dépendent restent **en attente**. Elle sert de proposition pour un futur
déploiement, pas de constat d'exécution.

## Proposition

| Composant | Proposition | Statut ici |
|---|---|---|
| OS | Debian 12 (bookworm) x86_64, ou équivalent Ubuntu 22.04 LTS | Non installé |
| Runtime | Python 3.11, paquet `seamtech-search` du dépôt | 3.11.2 sandbox |
| PostgreSQL | 16.x avec `unaccent`, `pg_trgm`, `vector` | 16.2 sandbox (pgserver, `--without-icu`) |
| Redis | 7.x (file durable) | 7.4.2 compilé, hors critères A–C |
| Stockage objets | MinIO (auto-hébergé) **ou** Cloudflare R2 — **à décider** | Non testé. s3rver ≠ MinIO |
| OCR | `tesseract-ocr`, `tesseract-ocr-fra`, `poppler-utils`, `ocrmypdf` | Absents |
| Sauvegarde | `postgresql-client` (`pg_dump`/`pg_restore` 16), dossier hors-site + identité S3 **dédiée** | `pg_dump` 16.2 sandbox |
| Front | inchangé (hors périmètre de ces réserves) | — |

## Décisions encore ouvertes

1. Fournisseur objet (MinIO vs R2 vs autre) et identité de sauvegarde
   distincte de l'identité documents.
2. Langues OCR au-delà du français.
3. RPO / RTO chiffrés, horaires, rétention, gel ou non des écrivains
   pendant `sauver`.
4. Windows atelier : hors de cette exécution.
5. Volume et jeu de données réel : hors de cette exécution.

Tant que ces points ne sont pas tranchés par l'exploitation, les
validations « Docker / Windows / MinIO / OCR atelier » restent **non
exécutées**, donc non PASS.
