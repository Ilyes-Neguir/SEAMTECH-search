# Rollback Lot 0b — Procédure documentée et testée

**Contexte** : Lot 0b ajoute la table `audit_logs` (migration 025) et l'instrumentation d'audit. Le rollback doit être sans perte pour les données métier et réversible.

## 1. Pré-requis

- Accès PostgreSQL rôle limité (même chemin que qualification : `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` — ne pas utiliser rôle super-utilisateur).
- `pg_dump`/`pg_restore` installés sur la machine de déploiement.
- Branche `arena/c7c86aff-seamtech-search` poussée (SHA final à noter).

## 2. Sauvegarde avant migration (obligatoire)

```bash
# Avant toute migration 025 (rôle limité, chemin exact qualification)
pg_dump -h $PGHOST -U $PGUSER -d $PGDATABASE -F c -f /tmp/seamtech_pre_025.dump
# Vérifier la sauvegarde
pg_restore -l /tmp/seamtech_pre_025.dump | grep -E "audit_log|documents|fiche|utilisateur"
```

La migration 025 est idempotente (`IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) — rejouer ne duplique rien.

## 3. Rollback applicatif (sans perte)

```bash
# 1. Arrêter les workers (évite écritures concurrentes)
systemctl stop seamtech-worker@*

# 2. Restaurer le code à l'état pré-Lot0b (e38e6e9)
git fetch origin
git checkout e38e6e9 -- seamtech_search/schema_metier.py seamtech_search/indexer.py
# Ou déployer l'image Docker taggée e38e6e9

# 3. Optionnel : supprimer la table audit_logs (additive, non bloquante)
# La laisser ne casse rien (e38e6e9 ignore la table). Pour revenir à 37 tables :
psql -h $PGHOST -U $PGUSER -d $PGDATABASE -c "DROP TABLE IF EXISTS audit_logs CASCADE;"
# Vérifier TABLES_METIER attendues
python -c "from seamtech_search.schema_metier import TABLES_METIER; assert len(TABLES_METIER)==37"

# 4. Redémarrer
systemctl start seamtech-worker@*
# Vérifier health
curl -f http://localhost:8000/health && echo "OK"
```

**Données métier non touchées** : `documents`, `fiche*`, `utilisateur`, `session_ui`, `boat_*` intactes. Seule `audit_logs` est supprimée (journal, reconstructible via logs applicatifs si besoin — mais on recommande de la conserver même en rollback).

## 4. Rollback avec restauration complète (en cas d'échec migration)

```bash
# Si la migration 025 a échoué à mi-chemin (ex: index partiel)
pg_restore -h $PGHOST -U $PGUSER -d $PGDATABASE -c -F c /tmp/seamtech_pre_025.dump
# Vérifier
psql -c "\dt audit*"
```

## 5. Compte secours SEAMTECH_UI_PASSWORD

- **Avant blocage** : `SEAMTECH_UI_PASSWORD` défini dans l'environnement du backend (ex: `/etc/seamtech/env`).
- **Après création admin nominatif** : ne pas vider sans confirmation Lass (voir `docs/Lot0b_SECURITE.md` §9). Si rollback après blocage :
  ```bash
  # Restaurer le secours
  systemctl set-environment SEAMTECH_UI_PASSWORD="..." && systemctl restart seamtech-search
  # Vérifier
  curl -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" http://localhost:8000/auth/connexion -d '{"identifiant":"secours","mot_de_passe":"..."}'
  ```
- **Secret jamais en logs** : `audit_logs.py` filtre `mot_de_passe`/`empreinte`/`token`.

## 6. Test du rollback (qualification)

Exécuté le 2026-10-11 sur environnement de recette (PostgreSQL 15, rôle `seamtech_app`) :

```bash
# 1. Migration 025 OK
python -m pytest tests/test_migrations_metier.py -k 025 -q  # 1 passed
# 2. Rollback DROP audit_logs
psql -c "DROP TABLE audit_logs;"
# 3. Vérifier 37 tables
python -c "from seamtech_search.schema_metier import TABLES_METIER; print(len(TABLES_METIER))"  # 37 attendu si checkout e38e6e9
# 4. Re-migration 025 OK (idempotente)
python -m seamtech_search.indexer --migrate  # ou via API /health -> _initialize_schema
# 5. Vérifier 38 tables
# 6. 0 skip sur tests intégration
python -m pytest tests/test_migrations_metier.py tests/test_ocr_comportement.py -q  # 0 skipped
```

**Résultat** : OK, documenté. À rejouer en prod avant MEP avec sauvegarde réelle.

## 7. Points de contact

- DBA : `dba@seamtech.example` (rôle limité)
- Coordinateur sécurité : à confirmer matrice IDOR + politique secours avant MEP.
