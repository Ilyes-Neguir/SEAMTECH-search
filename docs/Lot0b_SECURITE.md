# Lot 0b — Socle Sécurité P0 — Rapport d'avancement

**Branche**: `arena/c7c86aff-seamtech-search`  
**SHA pré-fusion Lot 0a+Lot 2**: `e38e6e9` (merge commit préservant lignées)  
**SHA HEAD actuel**: `2980f49` (parent `6eabcc9`, audit strict + logout/révocation/piece/artefact/ACCOUNT_* + docs sync)
**Date**: 2026-10-22 (CI `39749157839` failure `NotNullViolation documents.modified_at` sur `test_audit_piece_et_artifact`, native `39749157859` succès, réserves `39749157883` succès, perf `39749157986` succès — tous exact-SHA `2980f49`)

## 1. Prévol (exigence 0)

- Fusion Lot 0a `1998af6` (Lot 0a revue4, sessions Redis, 30min inactivité, 8h absolue, CSRF mutations, fail-closed Redis, rejet cookies v1.*, logout global, no-store) + Lot 2 `c9b2531` (ancêtre commun `e4c9fdf`) réalisée via commit `e38e6e9` (merge commit, parents conservés, `git log --graph` vérifiable).
- Comportement Lot 0a inchangé (valeurs `30min/8h`, CSRF, fail-closed, `no-store`, `v1.*` rejet) : vérifié par non-modification des fichiers `seamtech_search/sessions_redis.py` et `seamtech_search/comptes/routes.py` pour les parties auth critiques (sauf instrumentation audit additive).
- Lot 2 non régressé : `TABLES_METIER` 38 (33+1 ocr_etage3+1 audit_logs+4 boat_models), `VERSION 026_boat_models_and_aliases` conservée, migration `026` intacte. CI Lot2 acceptée au même SHA `c9b2531` : 4 runs verts `39571041240/185/162/147` vérifiés (f6b91045). HEAD `2980f49` : CI `39749157839` **failure** `psycopg2.errors.NotNullViolation: null value in column "modified_at" of relation "documents" violates not-null constraint` sur `tests/test_audit_events_suite.py::test_audit_piece_et_artifact` (backend 3.11/3.12/3.13, insertion `documents` sans `modified_at`), native `39749157859` succès, réserves `39749157883` succès, perf `39749157986` succès — tous exact-SHA `2980f49`.

## 2. Garde-fous

- Aucun modèle 4 rôles/UI ajouté.
- Aucune SQL concat/f-string : toutes les requêtes d'audit utilisent `execute(sql, params)` avec `%s` lié. Aucun nouveau fragment dynamique ajouté ; fragments hérités signalés dans `docs/SECURITE_SQL.md`.
- Tests d'intégration PostgreSQL/Redis/MinIO/Playwright réels : suites critiques 0 skip quand DB disponible (CI Lot2: 288 passed/0 skipped PostgreSQL, 84 tests réserves 0 skipped) ; `test_secours_desactivation_apres_admin` est volontairement `skip` (politique en attente, 1 skip attendu) — ne pas revendiquer 0 skip global. Qualification locale reproduit `init/migration` rôle limité, `pglast` insuffisant pour DDL réel.
- Rollback documenté/testé : `docs/ROLLBACK_LOT0B.md`.

## 3. Migration 025 (audit_logs)

- Fichier unique `seamtech_search/schema_metier.py` : `SQL_024_INVALIDATION_SESSIONS` (1998af6), `SQL_025_AUDIT_LOGS` (12 colonnes : `id BIGSERIAL PK`, `created_at TIMESTAMPTZ default now()`, `actor_user_id FK utilisateur ON DELETE SET NULL`, `actor_username/role`, `action`, `target_type/target_id`, `ip_address/user_agent`, `outcome CHECK success/failure`, `details_json JSONB`; 5 index `created_at DESC/action/actor/target/actor_role`; `COMMENT`; `IF NOT EXISTS` idempotente), `SQL_026_FONCTION_UNACCENT_ETENDUE+REPLI+BOAT_MODELS`.
- `TABLES_METIER` 37→38, `MIGRATIONS` 024_invalidation_sessions/025_audit_logs/026, header `006→026`.
- `seamtech_search/indexer.py` : `_migration_025_audit_logs` (PG-only, warning SQLite) exécute `SQL_025_AUDIT_LOGS` ; idempotente via `IF NOT EXISTS`/`IF NOT EXISTS` index, testée sur base réelle vide et re-jeu. Aucun SAVEPOINT nécessaire (contrairement à 026 qui utilise `SAVEPOINT seamtech_026_unaccent` pour la fonction unaccent).
- Préservation `audit_log` ancien (verrouillage connexions) : l'ancien `audit_log` (événements `connexion_refusee`) est conservé tel quel ; `audit_logs` est une nouvelle table indépendante. Les deux coexistent, aucun renommage n'est effectué (vérifié `pg_tables`).
- Pas de secrets, JSON borné 4ko (troncature), échec d'écriture journalisé `LOGGER.warning` sans bloquer la requête principale.

## 4. Événements + GET /api/admin/audit-logs

- Helper `seamtech_search/audit_logs.py` : `record_audit_log(index, actor_user_id, actor_username, actor_role, action, target_type, target_id, outcome, details, ip_address, user_agent)` — troncature 4ko, filtrage récursif secrets (`mot_de_passe`/`MDP`/`password`/`token`/`sid`/nested), requêtes paramétrées, erreurs non bloquantes. `ACTIONS_AUTORISEES` n'est qu'une documentation des actions prévues, non appliquée à l'écriture (point 9).
- `get_audit_logs_filtered(index, limit 1-200, offset 0-1M, sort in [created_at,id,action], order asc/desc, filtres)` — pagination bornée, tri whitelist (`created_at/id/action` × `asc/desc`), `outcome` whitelist (`success/failure`), 12 requêtes statiques allowlistées (`PG/SQLite×3 tris×2 ordres`), paramètres liés, `no-store`. Le vérificateur `scripts/verif_sql_parametrise.py` porte uniquement sur ce fichier.
- Événements instrumentés (identité depuis session serveur validée) :
  - `AUTH_LOGIN_SUCCESS` / `AUTH_LOGIN_FAILURE` (`/auth/connexion`, `/auth/connexion-secours`)
  - `AUTH_LOGOUT` / `AUTH_LOGOUT_ALL` / `SESSION_REVOKED` (`/auth/deconnexion`, `/auth/deconnexion-tout`)
  - `ACCOUNT_CREATE` / `ACCOUNT_DISABLE` / `ACCOUNT_PASSWORD_CHANGE` / `ACCOUNT_PASSWORD_RESET` (`/auth/utilisateurs*`)
  - `PIECE_DOWNLOAD` (`/pieces/{id}/aperçu,telecharger` — `seamtech_search/fiches/routes.py:servir_piece`)
  - `ARTIFACT_DOWNLOAD` (`/imports/{id}/artifacts/*` — `seamtech_search/api.py:get_import_artifact`)
  - `IMPORT_CREATE` (`/imports` — `seamtech_search/api.py:create_import` sync `wait=true` et async `wait=false` + erreurs 507/403/400/503, tous via `record_audit_log` avec identité validée via `X-SEAMTECH-SESSION-SID` si présent, sinon empreinte service; `details_json` sans chemin client ni secret)
- Endpoint `GET /api/admin/audit-logs` (`seamtech_search/comptes/routes.py:route_audit_logs`) :
  - RBAC `_exiger_administrateur` (403 pour non-admin y compris `operateur`)
  - `Cache-Control: no-store`, validation `limit/offset/sort/order/outcome`, `verifier_auth` préalable.
  - Vérifié : `/audit` historique (ancien journal `audit_log`) ne doit pas exposer `audit_logs` nominatif — test `tests/test_admin_audit_logs_403_operateur.py` (section non-contournement) + `tests/test_audit_events_suite.py`

## 5. Anti-IDOR — BLOQUÉ (en attente coordinateur)

**Périmètre** : `pieces/{id}/{preview,telecharger}` et `imports/{id}/artifacts/*`.

**État actuel** : audit ajouté, mais **contrôle d'accès propriétaire/rôle non implémenté** faute de matrice validée. La spec exige : *trouver une matrice propriétaire/rôle validée, sinon renvoyer question précise au coordinateur*.

**Question bloquante envoyée au coordinateur (2026-10-11)** :
> Pour `pieces/{id}` et `imports/{id}/artifacts/*`, qui est habilité ?
> - L'auteur du dépôt initial est-il seul habilité, ou tout `operateur` authentifié peut-il lire toute pièce (hypothèse actuelle : lecture libre si authentifié) ?
> - Un `administrateur` peut-il tout voir ou seulement les imports de son périmètre ?
> - Faut-il masquer l'existence (404 uniforme) ou 403 explicite pour ID inconnu ?
> - Exemple concret attendu : jeu de données A→B (utilisateur A crée lot → B tente `GET /pieces/{id}`) : 200 ou 403 ?

**Tests préparés** : `tests/test_injection_reelle.py` (paramétrisation) ; **aucun test IDOR n'encode de matrice** avant décision Lass (conforme consigne). Audit nominatif déjà en place (`_resolve_piece_actor`, `_resolve_audit_actor`). **Ne pas étendre l'usage de fragments dynamiques hérités**.

## 6. Fermeture /docs /redoc /openapi.json

- `seamtech_search/config.py` : `expose_docs: bool = False` + `SEAMTECH_EXPOSE_DOCS` (`1/true/yes/on` = `True`).
- `seamtech_search/api.py:235` : `docs_enabled = bool(getattr(config, "expose_docs", False))` (fermé par défaut).
- Tests `tests/test_docs_fermes_par_defaut.py` : `SEAMTECH_EXPOSE_DOCS=false` → 404 sur `/docs`, `/redoc`, `/openapi.json` ; `true` → 200 ; par défaut → 404.

## 7. 500 génériques + request ID

- Handler global `api.py:generic_exception_handler` : toute `Exception` non-`HTTPException` → `500 {"detail":"Erreur interne."}` avec `X-Request-ID`, log `LOGGER.exception` côté serveur avec `request_id` et `path`.
- Corrections minimales : `seamtech_search/fiches/routes.py:2098` (`Échec génération brouillon: {exc}` → `Erreur interne lors de la génération du brouillon.`), `seamtech_search/bateaux/routes.py:441/456` (`Réindexation échouée : {exc}` / `Comparatif échoué : {exc}` → génériques).
- Vérification `test_500_sans_details.py` : aucune 500 ne contient `stack`, `traceback`, `pg_`, `SELECT`, `path`, `/tmp`, `minio`.

## 8. scrypt + injection

- `seamtech_search/comptes/securite.py` vérifié : `hash_scrypt` format `$scrypt$n=...$r=...$p=...$...` + `verifier_scrypt` constant-time. Test existant `tests/test_comptes.py::test_empreinte_scrypt_format_et_verification` vert (vérifie format, vérification à temps constant, mauvais mot de passe 401, empreinte malformée). Aucun mot de passe en clair stocké (vérifié dans `utilisateur.empreinte_mot_de_passe`).
- Audit filtres/recherches : `tests/test_injection_reelle.py` (PostgreSQL réel, pas SQLite, 0 ligne + table intacte) + vérification statique (`scripts/verif_sql_parametrise.py` : scope `seamtech_search/audit_logs.py` seul, 12 définitions, aucune `f"`/`%`/`format` avec `execute`).

## 9. Compte secours SEAMTECH_UI_PASSWORD — BLOQUÉ

**État** : `SEAMTECH_UI_PASSWORD` (compte secours `secours`, fail-closed Redis) conservé tel quel Lot 0a. Le mot de passe est vérifié par le **frontend** Next.js (variable `SEAMTECH_UI_PASSWORD`); la route backend `POST /auth/connexion-secours` **ignore le corps** (`del corps`) et ne vérifie aucun mot de passe côté serveur — elle ne journalise `AUTH_LOGIN_FAILURE` (`secours`) que lorsque **Redis est indisponible** (`SessionStoreIndisponible` → 503), pas pour un mauvais mot de passe (aucun `AUTH_LOGIN_FAILURE` backend pour mauvais mdp secours).

**Question bloquante envoyée à Lass (2026-10-11)** :
> Après création du premier `administrateur` nominatif en production, faut-il :
> - **Option A** : désactiver immédiatement le compte secours (`SEAMTECH_UI_PASSWORD` vide → 503) ?
> - **Option B** : le conserver jusqu'à confirmation manuelle via `POST /auth/admin/bloquer-secours` ?
> - **Option C** : le conserver indéfiniment mais journaliser chaque usage comme `AUTH_LOGIN_SUCCESS` avec `actor=secours` ?
> Réponse attendue avant de coder le blocage/vider et le test avant/après.

**Travail indépendant avancé** : `SEAMTECH_UI_PASSWORD` jamais journalisé (filtre `audit_logs.py`), test `test_secours_non_journalise.py` vert. Documentation rollback mise à jour (`docs/ROLLBACK_LOT0B.md` § secours).

## 10. Acceptation

- Branche : `arena/c7c86aff-seamtech-search` (HEAD `2980f49` → correctif en cours : SESSION_REVOKED obligatoire, target_id logout exact, format import strict, secrets imbriqués, preuve MinIO réelle)
- Fichiers modifiés : `seamtech_search/schema_metier.py`, `seamtech_search/indexer.py`, `seamtech_search/audit_logs.py` (nouveau, 12 requêtes allowlistées, aucun concat, filtre récursif `mdp` inclus), `seamtech_search/comptes/routes.py`, `seamtech_search/fiches/routes.py`, `seamtech_search/api.py`, `seamtech_search/config.py`, `seamtech_search/bateaux/routes.py`, `tests/test_migrations_metier.py`, `tests/test_stockage_release_candidate.py` (syntaxe+comptage 44) + nouveaux tests `test_admin_audit_logs_403_operateur.py`, `test_docs_fermes_par_defaut.py` (health avec token), `test_500_sans_details.py`, `test_injection_reelle.py` (vacuité + table intacte), `test_secours_non_journalise.py` (sentinelle par concat), `test_audit_events_suite.py` (AUTH_LOGIN success/failure nominatif, IMPORT_CREATE strict, + logout/révocation/pièce/artefact/ACCOUNT_* nominatifs) + script `scripts/verif_sql_parametrise.py` (12 définitions) + docs `docs/Lot0b_SECURITE.md`, `docs/ROLLBACK_LOT0B.md`, `docs/MATRICE_EXIGENCE_CODE_TEST.md`.
- Compatibilité : Lot 0a comportement conservé, Lot 2 `026` conservé, `37→38` additive, `IF NOT EXISTS` idempotente, anciens `audit_log` préservés.
- Commandes : `python -m pytest tests/test_migrations_metier.py tests/test_comptes.py::test_empreinte_scrypt_* tests/test_docs* tests/test_500* tests/test_injection* tests/test_audit_events* tests/test_ocr_comportement.py tests/test_stockage_release_candidate.py -q` (suites critiques 0 skip avec DB, +1 skip attendu secours, CI doit être verte sur les 3 backends)
- Liens CI headSha exact : `2980f49` → CI `39749157839` **failure** `NotNullViolation modified_at` (backend 3.11/3.12/3.13), native `39749157859` succès, réserves `39749157883` succès, perf `39749157986` succès — 4 runs même SHA, `merge-base e4c9fdf HEAD` préservé (`e38e6e9` parents). Correctif `documents` + renforcements à rejouer sur prochain SHA.
- Points bloqués : (1) politique compte secours après admin nominatif (attente Lass), (2) matrice anti-IDOR pièces/artefacts (attente coordinateur), (3) filiation Lot0a+Lot2 via `e38e6e9` à valider par revue indépendante — **ILY-10 non-Done**.

## 11. Matrice exigence→code→test (extraits)

| Exigence | Code | Test |
|----------|------|------|
| Migration 025 idempotente PG réel | `schema_metier.py:SQL_025_AUDIT_LOGS`, `indexer.py:_migration_025_audit_logs` | `test_migrations_metier.py::test_migrations_sur_base_vide` + `test_idempotence_rejeu_sans_erreur` (44 pg_tables) |
| Audit nominatif session serveur | `audit_logs.py:record_audit_log` + `comptes/routes.py:AUTH_*` + `api.py:IMPORT_CREATE` + `fiches/routes.py:PIECE` | `tests/test_audit_events_suite.py` (AUTH_SUCCESS/FAILURE, secours, IMPORT_CREATE nominatif, détails assainis) + `test_admin_audit_logs_403_operateur.py` |
| GET /api/admin/audit-logs 403 opérateur | `comptes/routes.py:route_audit_logs` + `_exiger_administrateur` | `test_admin_audit_logs_403_operateur.py` (403 opérateur, 200 admin, no-store, 422, injection, non-contournement /audit) |
| Docs fermés par défaut | `config.py:expose_docs`, `api.py:docs_enabled` | `test_docs_fermes_par_defaut.py` (false/true/default + AppConfig.load via monkeypatch) |
| 500 génériques | `api.py:generic_exception_handler`, `fiches/routes.py:2098`, `bateaux/routes.py:441/456` | `test_500_sans_details.py` (génériques sans fuite, X-Request-ID) |
| scrypt clair/mauvais | `comptes/securite.py:hash_scrypt` | `test_comptes.py::test_empreinte_scrypt_format_et_verification` (format, constant-time, mauvais mdp) |
| Rollback testé | `docs/ROLLBACK_LOT0B.md` | `scripts/qualification_native_controles.py` + `test_migrations_metier.py` (idempotence) |

## 12. Prochaines étapes

1. Obtenir réponses bloquantes (secours, IDOR) → coder les deux derniers tickets.
2. Rejouer qualification native complète (PostgreSQL rôle limité, chemin exact `init/migration`) → 0 skip sur suites critiques (hors skip secours en attente).
3. Pousser branche + ouvrir PR → fournir 4 liens CI même SHA head.
4. Revue indépendante → déclarer ILY-10 Done.
