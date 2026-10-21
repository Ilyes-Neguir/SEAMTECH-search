# Matrice exigence → code → test — Lot 0b

| # | Exigence ILY-10 | Fichier code (ligne) | Fichier test | Statut |
|---|-----------------|----------------------|--------------|--------|
| 0 | Prévol fusion e38e6e9 (Lot0a 1998af6 + Lot2 c9b2531, ancêtre e4c9fdf) | `git log --oneline --graph e38e6e9` | `docs/Lot0b_SECURITE.md` §1 + CI 4 runs même SHA | ✅ fait (à pousser, SHA final à reporter) |
| 1 | Garde-fous : pas de SQL concat/f-string | `seamtech_search/audit_logs.py:record_audit_log` (paramétré), `scripts/verif_sql_parametrise.py` | `tests/test_injection_reelle.py` + `scripts/verif_sql_parametrise.py` | ✅ fait |
| 1b | 0 skip PostgreSQL/Redis/MinIO/Playwright | `seamtech_search/indexer.py:_migration_025_audit_logs` (SAVEPOINT) | `tests/test_*` qualification réelle | ✅ fait (0 skip attendu CI) |
| 2 | Migration 025 audit_logs 12 col + 5 index + IF NOT EXISTS | `seamtech_search/schema_metier.py:SQL_025_AUDIT_LOGS` (ligne ~...) | `tests/test_migrations_metier.py::test_tables_metier_38` | ✅ fait |
| 2b | TABLES_METIER 37→38 | `seamtech_search/schema_metier.py:TABLES_METIER` | `tests/test_migrations_metier.py:93`, `test_ocr_comportement.py:992`, `test_stockage_release_candidate.py:772/774` | ✅ fait |
| 2c | Backup/restore + préservation audit_log ancien | `seamtech_search/indexer.py` + `docs/ROLLBACK_LOT0B.md` | `scripts/qualification_native_controles.py` | ✅ fait (documenté/testé) |
| 2d | Pas de secrets, JSON borné 4ko, échec écriture non bloquant | `seamtech_search/audit_logs.py:_tronquer_details` + filtre secrets | `tests/test_audit_logs_sans_secrets.py` | ✅ fait |
| 3 | Événements AUTH_*/ACCOUNT_*/PIECE_*/ARTIFACT_* | `seamtech_search/comptes/routes.py` (instrumentation), `seamtech_search/fiches/routes.py:servir_piece`, `seamtech_search/api.py:get_import_artifact` | `tests/test_audit_evenements.py` | ✅ partiel (7/9 événements, import déjà couvert) |
| 3b | GET /api/admin/audit-logs admin only 403 opérateur + no-store + paramétré | `seamtech_search/comptes/routes.py:route_audit_logs` | `tests/test_admin_audit_logs.py` | ✅ fait |
| 3c | /audit non contournement | `seamtech_search/comptes/routes.py` (verifier_auth + _exiger_administrateur) | `tests/test_audit_logs_non_contournement.py` | ✅ fait (vérif backend, pas seulement UI) |
| 4 | Anti-IDOR pieces/{id}/{preview,telecharger} + imports/{id}/artifacts/* | **BLOQUÉ** — attente matrice coordinateur | `tests/test_idor_pieces.py` (préparé, 5 cas : A→B, autorisé, admin, sans session, ID inconnu) | ⏳ bloqué (question envoyée 2026-10-11) |
| 5 | /docs fermés par défaut SEAMTECH_EXPOSE_DOCS=false | `seamtech_search/config.py:expose_docs`, `seamtech_search/api.py:docs_enabled` | `tests/test_docs_fermes_par_defaut.py` (false/true/default) | ✅ fait |
| 5b | 500 génériques + request ID | `seamtech_search/api.py:generic_exception_handler`, `fiches/routes.py:2098`, `bateaux/routes.py:441/456` | `tests/test_500_sans_details.py` | ✅ fait |
| 6 | scrypt format + clair/mauvais | `seamtech_search/comptes/securite.py` | `tests/test_comptes.py::test_empreinte_scrypt_format_et_verification` + `test_mot_de_passe_en_clair` | ✅ fait |
| 6b | Filtres/recherches injection réels + vérif statique paramétrisation | `seamtech_search/audit_logs.py` (requêtes liées) | `tests/test_injection_reelle.py` + `scripts/verif_sql_parametrise.py` | ✅ fait |
| 7 | Compte secours SEAMTECH_UI_PASSWORD confirmation Lass avant blocage/vider | **BLOQUÉ** — attente Lass | `tests/test_secours_avant_apres.py` (avant/après admin nominatif) | ⏳ bloqué (question envoyée 2026-10-11) |
| 8 | Acceptation : branche+SHA, fichiers, matrice, commandes, CI 4 runs même SHA, rollback, blocages | `docs/Lot0b_SECURITE.md`, `docs/ROLLBACK_LOT0B.md`, `docs/MATRICE_EXIGENCE_CODE_TEST.md` | CI `39571041240/185/162/147` au SHA `c9b2531` + à rejouer sur HEAD final | ✅ partiel (2 blocages documentés, CI à rejouer après push) |

## Légende

- ✅ fait : codé + testé (ou test préparé) + doc
- ⏳ bloqué : en attente réponse coordinateur/Lass, question précise envoyée, travail indépendant avancé sans bloquer le reste
- ❌ manquant : non commencé (aucun)
