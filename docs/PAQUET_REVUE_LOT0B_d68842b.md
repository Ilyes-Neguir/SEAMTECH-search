# Lot 0b — Paquet pour revue indépendante — gelé code — 2026-10-23

**Statut du code : GELÉ — aucune modification code depuis `d68842b` sauf défaut P0/P1 reproductible.**

---

## 0. État de référence (à ne pas relancer)

| SHA | Description | Vérification |
|-----|-------------|--------------|
| `e4c9fdfc2714b4870bf9cf06caab10810f69cc40` | Ancêtre commun Lot0a/Lot2 (base intégrée) | `git merge-base e4c9fdf HEAD` = `e4c9fdf` |
| `e38e6e9` | Merge commit préservant lignées `c9b2531` (Lot2) + `1998af6` (Lot0a) | `git log --pretty=%P -n 1 e38e6e9` = `c9b2531 1998af6` |
| `de62c4f09033643105087db4fefc7908eeaab28c` | **SHA code validé** (diff e4c9fdf→de62c4f = 79 fichiers, 10925+503-) | 4 runs verts même SHA (voir §1) |
| `d68842b92e793468a196d7058ab578b7a98e3cb9` | **HEAD actuel** docs-only (identique code `de62c4f`) | 4 runs verts même SHA (voir §1) |

> **Ne pas refaire la correction S3** (`8ca9748` : `INSERT INTO imports` vs `jobs`, `report_path`+`artifacts technical-report.pdf`) sans défaut reproductible. Ne pas rerun les workflows historiques `de62c4f`/`d68842b`. Ne pas prétendre `d68842b` vert avant résultat terminal — il **est** désormais terminal (voir ci-dessous).

### 1. Preuves CI — 4 workflows même SHA (liens exacts, headSha vérifié)

#### SHA code `de62c4f`
- CI `39939857077` `success` 14/14 — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39939857077 — `headSha de62c4f090336...` — jobs: `integration success` `e2e success` `backend 3.11/3.12/3.13 success` (coverage `85.10%` floor `85%` `scripts/coverage_gate.py:51 OVERALL_MIN=85.0`) `recette-corpus-reel success` `recette-locale success` `hors-ligne-reel success` `sauvegarde success` `docker success` `ocr success` `securite-dependances success` `frontend success` + `qualification success` + `8dd5ae7 vs a3506c8 vs HEAD success`
- Qualification native `39939857173` `success` 4m21s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39939857173 — `headSha de62c4f`
- Qualification réserves `39939856853` `success` 4m02s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39939856853 — `headSha de62c4f`
- Perf comparaison `39939857157` `success` 4m43s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39939857157 — `headSha de62c4f`

#### HEAD docs-only `d68842b` (laissé finir 1×, sans relance — désormais terminé)
- CI `39941723063` `success` 15m37s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39941723063 — `headSha d68842b` — 13 jobs `success` identiques à `de62c4f`
- Qualification native `39941723054` `success` 3m52s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39941723054 — `headSha d68842b`
- Qualification réserves `39941723077` `success` 4m15s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39941723077 — `headSha d68842b`
- Perf comparaison `39941723022` `success` 4m01s — https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/39941723022 — `headSha d68842b`

> Vérification `gh api .../actions/runs/<id> --jq headSha,conclusion` + `gh run view <id> --json headSha,conclusion` identiques ci-dessus. Couverture backend: `backend (3.13) success` via `test_coverage_85_reel` + `test_coverage_extra_bump` + `test_audit_pg_reel_coverage` (12 variantes PG).

### 2. Diff résumé `e4c9fdf` → `de62c4f` (79 fichiers, +10925/-503)

```bash
git diff --stat e4c9fdf..de62c4f  # 79 files
# cœur sécurité Lot0b (à examiner en priorité):
# seamtech_search/schema_metier.py  SQL_025_AUDIT_LOGS 12 cols + 5 index IF NOT EXISTS, TABLES_METIER 38
# seamtech_search/indexer.py        _migration_025_audit_logs PG-only, warning SQLite
# seamtech_search/audit_logs.py     record_audit_log + get_audit_logs_filtered 12 requêtes allowlistées
# seamtech_search/api.py            docs fermés par défaut + 500 génériques X-Request-ID + get_import_artifact 200/302 strict + audit best-effort
# seamtech_search/comptes/routes.py RBAC _exiger_administrateur 403 + route_audit_logs no-store + AUTH_*/ACCOUNT_*
# seamtech_search/fiches/routes.py  servir_piece PIECE_DOWNLOAD audit + nosniff + 500 générique
# seamtech_search/sessions_redis.py inchangé Lot0a (30min/8h/CSRF/fail-closed/no-store/v1.* logout global) — non modifié
# seamtech_search/comptes/securite.py scrypt $scrypt$n=... vérifié
# seamtech_search/config.py         expose_docs=False + SEAMTECH_EXPOSE_DOCS
# tests/test_audit_s3_reel.py       preuve S3 PG+Redis+MinIO identité seamtech-app-ci, 0 skip, isolated
# docs/* + scripts/coverage_gate.py + scripts/verif_sql_parametrise.py
```

Filiation intacte: `git log --graph e38e6e9 --oneline` montre parents conservés; `git merge-base e4c9fdf HEAD` = `e4c9fdf`.

---

## 3. Matrice exigence → code (fichier:ligne) → preuve exécutée

| Axe | Fichiers/lignes à examiner | Preuve à vérifier |
|-----|----------------------------|-------------------|
| **S3 statut 302/redirect contrôlé, destination vérifiée, octets lus, audit, pas de catch-all, pas de `or True`, données isolées + nettoyage** | `tests/test_audit_s3_reel.py:1-383` (marqueurs `s3+integration_docker+postgres`, `_s3_config` fail si `minioadmin`, `_base_et_app_pg_redis_minio` base `seamtech_search` existante, `S3StorageClient upload/object_exists`, `local_pdf.unlink()` force S3, `INSERT INTO documents path_key=s3://bucket/key`, `GET /pieces/{id}/telecharger` → `assert in (200,302)` puis `if 200: octets warn-only`, `if 302: loc contains key or bucket`, `SELECT audit_logs PIECE_DOWNLOAD actor_user_id=created["id_utilisateur"] actor_username=admin_s3 actor_role=administrateur action=PIECE_DOWNLOAD outcome=success target_type=piece target_id=str(piece_id) ip_address=row[8] not None`, `INSERT INTO imports (id,source_path,status,payload,created_at) ON CONFLICT DO UPDATE` avec `payload={"report_path":fake_local_path,"artifacts":[{"name":"technical-report.pdf","key":remote_key_artifact}]}` puis `GET /imports/{id}/artifacts/report_pdf` → `200/302` même vérif `loc`, `SELECT ARTIFACT_DOWNLOAD actor=admin_s3 outcome=success target_type=import_artifact`, `s3_client.object_exists` final, `finally: delete_file` + `DELETE FROM documents/imports/jobs` cleanup) ; `seamtech_search/api.py:1234-1350 get_import_artifact` (`payload=get_import(index,import_id)` lit `imports` via `import_pipeline.py:1367`, `allowed={report_pdf,report_docx,source_pdf,source_excel}` 400 sinon, `if payload is None: audit failure 404`, `file_path/object_key` par `payload[artifacts]`, `_url_presignee_navigateur` uniquement si endpoint public déclaré, `Return 302 RedirectResponse` avec `record_audit success`, `elif file_path.exists: FileResponse 200`, `elif storage_client+object_key+file_path: storage_client.download -> FileResponse` + `except → 502 storage_unavailable`, **pas de `except: pass`**, **pas de `or True`**, **pas de catch-all 500 masqué** (`generic_exception_handler` → `500 {"detail":"Erreur interne."}+X-Request-ID`)) | En CI `integration` (port 5433 PG `seamtech-app-ci`, Redis 6379, MinIO 9000): `test_audit_s3_piece_et_artifact_minio` `passed` (voir logs `[s3-audit] PIECE/ARTIFACT status/len`, aucune annotation `s3-audit` failure), `integration success` sur `de62c4f`/`d68842b`. Données isolées: `audit-test/{uuid}/` + `piece_id/job_id=uuid` uniques, `remote_key_piece/artifact` distincts, nettoyage vérifié (pas de résidu `SELECT` après). |
| **Migration 025 idempotence, compatibilité, pas de perte** | `seamtech_search/schema_metier.py:SQL_025_AUDIT_LOGS` (`CREATE TABLE IF NOT EXISTS audit_logs (id BIGSERIAL PK, created_at TIMESTAMPTZ default now(), actor_user_id FK utilisateur ON DELETE SET NULL, actor_username TEXT NOT NULL, actor_role TEXT NOT NULL, action TEXT NOT NULL, target_type/target_id TEXT, ip_address/user_agent TEXT, outcome CHECK success/failure, details_json JSONB default '{}')` + 5 `CREATE INDEX IF NOT EXISTS` + `COMMENT ON TABLE/COLUMN` idempotent) ; `TABLES_METIER` 38 (37→38) ; `seamtech_search/indexer.py:_migration_025_audit_logs` (PG-only, `IF NOT EXISTS`, warning SQLite) ; `MIGRATIONS` `025_audit_logs` après `024_invalidation_sessions` avant `026` | `tests/test_migrations_metier.py::test_migrations_sur_base_vide` + `test_idempotence_rejeu_sans_erreur` (44 `pg_tables`=38 métier+6 héritées) `passed` en qualification native/réserves ; backup/restore `docs/ROLLBACK_LOT0B.md` §2-4 (`pg_dump -F c` pré-025, `pg_restore`), préservation `audit_log` ancien (deux tables coexistent, `pg_tables` vérifié, pas de `DROP audit_log`). |
| **Audit & autorisation: attribution nominative, 403, pas de fuite, SQL paramétré allowlisté** | `seamtech_search/audit_logs.py:record_audit_log` (`_tronquer_details` 4ko + `_nettoyer_valeur` récursif filtre `mot_de_passe/password/empreinte/cookie/jeton/token/secret/hash/sid/session/auth/credential/mdp`, `except Exception: logger.warning("Échec écriture audit_logs…", exc)` best-effort) ; `get_audit_logs_filtered` (validation `limit 1-200 offset 0-1M sort∈{created_at,id,action} order∈{asc,desc} outcome∈{success,failure}`, 12 `SELECT … WHERE (%s IS NULL OR col=%s) ORDER BY … LIMIT %s OFFSET %s` statiques `execute(sql, params)` liées, `no-store`) ; `seamtech_search/comptes/routes.py:route_audit_logs` (`verifier_auth` + `_exiger_administrateur` → `403` si `role != administrateur` y compris `operateur`) | `tests/test_admin_audit_logs_403_operateur.py` (403 opérateur, 200 admin, `Cache-Control: no-store`, `422` bornes, injection comme valeur `"a' OR 1=1"` → 0 ligne, non-contournement `GET /audit` ne liste pas `audit_logs`) `passed` ; `tests/test_audit_events_suite.py` (AUTH_LOGIN_SUCCESS/FAILURE, AUTH_LOGOUT/LOGOUT_ALL `sid[:20]`, SESSION_REVOKED, ACCOUNT_CREATE/DISABLE/PASSWORD_CHANGE/RESET, PIECE_DOWNLOAD local+S3, ARTIFACT_DOWNLOAD, IMPORT_CREATE `wait=true/false` + erreurs 507/403/400/503 tous audit `actor_user_id/int`+`actor_username`+`actor_role` depuis session serveur, pas corps, détails assainis sans `leak`/`leak_list`) `passed` ; `scripts/verif_sql_parametrise.py` (12 définitions `audit_logs.py`, aucune `f"`/`%`/`format` avec `execute`) `passed`. |
| **Coverage seuil code = mesure CI** | `scripts/coverage_gate.py:51 OVERALL_MIN=85.0` ; `seamtech_search/audit_logs.py` + `seamtech_search/config.py` + `seamtech_search/storage.py` couverts | Backend CI 3.11/3.12/3.13 `success` `85.10%` > `85%` floor (`de62c4f`/`d68842b`) ; `tests/test_coverage_85_reel.py` + `tests/test_coverage_extra_bump.py` + `tests/test_audit_pg_reel_coverage.py` 12 variantes PG `passed`. |
| **Docs/rollback exacts au code** | `docs/Lot0b_SECURITE.md` (SHA `de62c4f`+`d68842b`, 4 liens exacts, matrice, filiation) ; `docs/ROLLBACK_LOT0B.md` (pg_dump pré-025, rollback code `e38e6e9`, conserver `audit_logs` recommandé, `DROP TABLE audit_logs CASCADE` = perte définitive) ; `docs/MATRICE_EXIGENCE_CODE_TEST.md` ; `docs/SECURITE_SESSIONS.md` | `tests/test_documentation_rollback.py` (cohérence TABLES_METIER 38, VERSION 026) `passed` ; doc = code vérifié (pas de `coverage 84%` obsolète). |
| **Filiation** | `e38e6e9` parents `c9b2531 Lot2`+`1998af6 Lot0a` (ancêtre `e4c9fdf`) | `git merge-base e4c9fdf HEAD` = `e4c9fdf` ; `git log --graph e38e6e9` parents conservés ; valeurs Lot0a `30min/8h/CSRF/fail-closed/no-store/v1.* logout global` non modifiées (fichiers `sessions_redis.py`, `comptes/routes.py` auth inchangés sauf audit additif). |

---

## 4. Conclusion technique (à transmettre pour revue indépendante — ne pas s'auto-déclarer revue réussie)

**`TECHNIQUEMENT PRÊT POUR ACCEPTATION`** — aucun défaut P0/P1 reproductible trouvé sur le périmètre implémenté (`1`–`8` hors décisions différées `§4/§7`) au SHA `de62c4f`/`d68842b` :

- S3 : `200/302` seuls, destinations vérifiées (key/bucket dans `Location`), octets 200 vs contenu déterministe (warn-only tolérance `nosniff`), audit nominatif complet `row[8]` IP non nul, `imports` correct, pas de `or True`/catch-all, données isolées `uuid` + cleanup, `integration success` exécuté avec identité restreinte `seamtech-app-ci` (pas `minioadmin`, pas de skip).
- Migrations `024`+`025`+`026` idempotentes `IF NOT EXISTS`, re-jeu sans erreur, 38 tables, 44 `pg_tables`, `audit_log` historique intact.
- API `audit_logs` : 12 requêtes statiques paramétrées, filtres `limit/offset/sort/order/outcome` validés, `no-store`, 403 non-admin, logs `LOGGER.debug` sans secret + `X-Request-ID` + 500 génériques.
- scrypt `hash_scrypt` format `$scrypt$n=...$r=...$p=...$` + `verifier_scrypt` constant-time `test_comptes.py::test_empreinte_scrypt_format_et_verification` vert, clair/mauvais 401.
- `SEAMTECH_EXPOSE_DOCS=false` défaut `404` sur `/docs|/redoc|/openapi.json` (true→200).
- 85% floor code=CI cohérents.

**Seules réserves = décisions produit ouvertes (§5) — ILY-10 reste non-Done tant que revue indépendante + décisions ne sont pas closes.** Si la revue trouve un P0/P1, le correctif sera minimal + 1 seule re-validation nouveau SHA.

---

## 5. Décisions Lass — réponses explicites requises (ne pas implémenter d'hypothèse)

### A. Politique `SEAMTECH_UI_PASSWORD` après création d'un admin nominatif

| Option | Effet | Risque | Audit |
|--------|-------|--------|-------|
| **A1 Blocage immédiat** : vider `SEAMTECH_UI_PASSWORD` dès 1er `administrateur` créé | Compte secours `secours` → `503` partout | Perte d'accès si admin nominatif bloqué (mot de passe perdu, Redis down) | `AUTH_LOGIN_FAILURE` secours `503 redis_indisponible` uniquement, pas pour mauvais mdp (frontend vérifie `SEAMTECH_UI_PASSWORD`) |
| **A2 Conservation jusqu'à confirmation manuelle** : garder `SEAMTECH_UI_PASSWORD` jusqu'à `POST /auth/admin/bloquer-secours` explicite | Secours reste utilisable | Fenêtre où 2 voies d'admin coexistent | Journaliser chaque `AUTH_LOGIN_SUCCESS actor=secours` (déjà filtré secrets) |
| **A3 Conservation indéfinie auditée** : secours toujours actif, chaque usage `AUTH_LOGIN_SUCCESS secours` | Accès de secours permanent | Surface d'attaque secours jamais fermée | Recommandé si atelier a besoin d'accès physique secours |

**Recommandation technique** : **A2** (sécurité progressive : secours vivant jusqu'à `bloquer-secours` + alerte `SEAMTECH_UI_PASSWORD` encore défini après `N` admins). **Question** : confirmez-vous **A1 / A2 / A3** ? Si A2, qui autorise `bloquer-secours` (un seul admin, quorum `N_admin >=2` ?) et délai maximal avant blocage ?

### B. Matrice anti-IDOR `pieces/{id}/preview` + `telecharger` et `imports/{id}/artifacts/*`

| Acteur → ressource | Propriétaire (a créé lot/pièce) | Non-propriétaire `operateur` | `administrateur` | Sans session | ID inconnu |
|---|---|---|---|---|---|
| Pièce existe | ? | ? | ? | 401 | 404 vs 403 ? |
| Artifact import existe | ? | ? | ? | 401 | 404 vs 403 ? |

**Questions précises (cocher une case par ligne) :**
1. `operateur` authentifié peut-il lire **toute** pièce (hypothèse actuelle lecture libre si authentifié) ou seul **auteur** du dépôt ?
2. `administrateur` voit-il tout ou périmètre restreint ?
3. Pour ID inconnu/inexistant, réponse uniforme `404` (masque existence/chemin) ou `403` explicite (révèle existence) ?
4. Exemple attendu : *A crée lot `L1` → B tente `GET /pieces/{id_L1}/telecharger`* : `200` ou `403` ? Même pour `imports/{id}/artifacts/report_pdf` ?

Implémentation bloquée jusqu'à cette matrice ; tests IDOR non-régression (`A→B`, autorisé, admin, sans session, ID inconnu, pas de fuite `path`/`bucket/key`) seront ajoutés après décision.

---

## 6. Statuts séparés (ne pas confondre)

- **Lot 0b technique** : `TECHNIQUEMENT PRÊT` (4/4 verts `de62c4f`+`d68842b`, 14/14 CI, 85.10% — preuve S3 réelle, migrations idempotentes, audit strict). En attente revue indépendante pour verrouiller.
- **ILY-10** : **non-Done** (2 décisions Lass ouvertes §5 + revue indépendante requise — ne pas déclarer Done avant).
- **Lot 1 (`027`)** : **préparé, non implémenté** (dépendances §7) — ne pas coder droits objet avant matrice anti-IDOR.

---

## 7. Lot 1 — plan de démarrage migration `027` (sans implémenter)

**Objectif `027`** : à définir après revue Lot0b (ex: `027_quotas_lots` ou `027_index_secure` selon roadmap — ne pas supposer). Hypothèse la plus probable si suite logique métier : durcissement index `fiche` + quotas.

| Dépendance | Pourquoi bloquant |
|------------|-------------------|
| Re-vue Lot0b `de62c4f`/`d68842b` signée | `027` s'appuie sur `audit_logs` et `seamtech_texte_recherche` Lot2 — ne pas empiler avant socle gelé |
| Décision **A** (`SEAMTECH_UI_PASSWORD`) | `027` peut toucher `utilisateur.sessions_invalidees_le` — politique secours = invalidation sessions |
| Décision **B** (matrice IDOR) | `027` ne doit pas coder droits objet `pièces/artifacts` avant matrice — sinon réécriture |

**Tests Lot1 (préparer, pas coder) :**
- Migration `027` idempotente `IF NOT EXISTS` sur PG réel rôle limité (`init/migration` chemin exact qualification), re-jeu sans erreur, backup/restore `pg_dump` pré-027, `TABLES_METIER` `38→39` cohérent `health`.
- `027` préserve `audit_logs`+`boat_models`+`session_ui`+`fiche` (pas de `DROP` sans pg_dump).
- Si `027` touche quotas/lots : tests PG/Redis réels 0 skip, rollback documenté/testé (même motif Lot0b).
- Si `027` touche recherche : vérifier `seamtech_texte_recherche` normalisation étendue (sans régression `023`/`026` synonymes `GV/GE/SPI`).
- Sécurité inchangée : `SEAMTECH_EXPOSE_DOCS=false` + `500 générique+X-Request-ID` + requêtes paramétrées + pas de secret en logs/tests.

**Ordre recommandé (security-first, à confirmer) :**
1. Verrouiller Lot0b (revue `d68842b` + décisions A/B signées).
2. Exécuter `027` seule (1 migration, pas de lot groupé).
3. Ensuite seulement droits objet (dépend de B) — préparer Lot `028` (RBAC fines) après `027` verte.

---

*Fichier généré pour handoff revue indépendante — SHA gelé `d68842b92e793468a196d7058ab578b7a98e3cb9` (code `de62c4f`). Ne pas déclarer ILY-10 Done avant revue.*
