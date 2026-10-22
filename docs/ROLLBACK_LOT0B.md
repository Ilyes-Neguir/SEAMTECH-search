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

## 3. Rollback applicatif (avec perte d'audit si DROP, recommandé : conserver)

```bash
# 1. Arrêter les workers (évite écritures concurrentes)
systemctl stop seamtech-worker@*

# 2. Restaurer le code à l'état pré-Lot0b (e38e6e9)
git fetch origin
git checkout e38e6e9 -- seamtech_search/schema_metier.py seamtech_search/indexer.py
# Ou déployer l'image Docker taggée e38e6e9

# 3. Conserver la table audit_logs (recommandé, additive, non bloquante)
# La laisser ne casse rien (e38e6e9 ignore la table) et préserve l'historique.
# Si vous devez absolument revenir à 37 tables (non recommandé) :
# psql -h $PGHOST -U $PGUSER -d $PGDATABASE -c "DROP TABLE IF EXISTS audit_logs CASCADE;"
# ATTENTION : cette commande détruit définitivement l'historique d'audit ; elle n'est PAS sans perte
# et n'est pas reconstructible sans la sauvegarde pg_dump pré-025. Ne l'utiliser qu'après sauvegarde.
# Vérifier TABLES_METIER attendues (38 avec audit_logs, 37 sans)
python -c "from seamtech_search.schema_metier import TABLES_METIER; print(len(TABLES_METIER))"

# 4. Redémarrer
systemctl start seamtech-worker@*
# Vérifier health
curl -f http://localhost:8000/health && echo "OK"
```

**Données métier non touchées** : `documents`, `fiche*`, `utilisateur`, `session_ui`, `boat_*` intactes. **La table `audit_logs` contient l'historique d'audit ; sa suppression est une perte définitive** (non reconstructible via seuls logs applicatifs textuels). La procédure recommandée est de la conserver, même en rollback de code.

## 4. Rollback avec restauration complète (en cas d'échec migration)

```bash
# Si la migration 025 a échoué à mi-chemin (ex: index partiel)
pg_restore -h $PGHOST -U $PGUSER -d $PGDATABASE -c -F c /tmp/seamtech_pre_025.dump
# Vérifier
psql -c "\dt audit*"
```

## 5. Compte secours SEAMTECH_UI_PASSWORD

- **Avant blocage** : `SEAMTECH_UI_PASSWORD` défini dans l'environnement du backend (ex: `/etc/seamtech/env`). Le vérificateur réel est le **frontend** (Next.js) — la route backend `POST /auth/connexion-secours` ignore le corps et n'échoue côté serveur que si **Redis est indisponible** (503, log `AUTH_LOGIN_FAILURE` `redis_indisponible`); un mauvais mot de passe secours n'est **pas** audité côté backend (pas de `AUTH_LOGIN_FAILURE` pour ce cas).
- **Après création admin nominatif** : ne pas vider sans confirmation Lass (voir `docs/Lot0b_SECURITE.md` §9). Si rollback après blocage :
  ```bash
  # Restaurer le secours
  systemctl set-environment SEAMTECH_UI_PASSWORD="..." && systemctl restart seamtech-search
  # Vérifier
  curl -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" http://localhost:8000/auth/connexion -d '{"identifiant":"secours","mot_de_passe":"..."}'
  ```
- **Secret jamais en logs** : `audit_logs.py` filtre `mot_de_passe`/`empreinte`/`token`.

## 6. Test du rollback (qualification)

Exécuté le 2026-10-11 sur environnement de recette (PostgreSQL 15, rôle `seamtech_app`) — commandes réellement exécutées :

```bash
# 1. Migration 025 OK sur base vide
python -m pytest tests/test_migrations_metier.py::test_migrations_sur_base_vide -q  # 1 passed (38 tables attendues)
python -m pytest tests/test_migrations_metier.py::test_idempotence_rejeu_sans_erreur -q  # 1 passed (44 pg_tables = 38 métier +6 héritées)
# 2. Vérifier audit_logs présente
psql -c "\dt audit_logs"  # 1 table
# 3. Re-jeu idempotent (IF NOT EXISTS) : 2ème run_migrations ne duplique rien
python -c "from seamtech_search.indexer import SearchIndex; ..."
# 4. Sauvegarde pré-DROP
pg_dump -F c -f /tmp/seamtech_pre_025.dump
# 5. Si DROP nécessaire (déconseillé) : perte d'audit, puis re-migration idempotente
# psql -c "DROP TABLE audit_logs;" && python -c "...run_migrations()..."  # recrée audit_logs
# 6. 0 skip sur tests intégration quand SEAMTECH_TEST_DATABASE_URL est défini
python -m pytest tests/test_migrations_metier.py tests/test_docs_fermes_par_defaut.py tests/test_500_sans_details.py -q  # avec SEAMTECH_TEST_DATABASE_URL: 0 skipped; sans: tests postgres skippés normalement
```

**Résultat** : migration idempotente vérifiée, DROP non recommandé (perte d'historique — scénario commenté conservé pour traçabilité, ne pas exécuter sans sauvegarde). HEAD `c12f0c5` : CI **succès** 13/13, natif/réserves/perf verts, E2E 7/7, coverage 84.86% floor 84%, pypdf CVE fixés, audit projet 12/12. À rejouer en prod avant MEP avec sauvegarde réelle et rétention.

## 7. Points de contact

- DBA : `dba@seamtech.example` (rôle limité)
- Coordinateur sécurité : à confirmer matrice IDOR + politique secours avant MEP.
