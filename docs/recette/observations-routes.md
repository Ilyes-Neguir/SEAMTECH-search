# Recette des routes — observations

116 routes déclarées, 472 sondes, **0 réponse(s) 500**.

| Verbe | Route | Sonde | Observé | Extrait |
|---|---|---|---|---|
| GET | `/accessoires/extractions` | nominal | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| GET | `/accessoires/extractions` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/accessoires/extractions` | paramètre invalide | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| GET | `/accessoires/extractions` | ressource absente | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| GET | `/accessoires/extractions/{id_extraction}` | nominal | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| GET | `/accessoires/extractions/{id_extraction}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/accessoires/extractions/{id_extraction}` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| GET | `/accessoires/extractions/{id_extraction}` | ressource absente | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/extractions/{id_extraction}/resoudre` | nominal | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/extractions/{id_extraction}/resoudre` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/accessoires/extractions/{id_extraction}/resoudre` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| POST | `/accessoires/extractions/{id_extraction}/resoudre` | ressource absente | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/extractions/{id_extraction}/lier` | nominal | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/extractions/{id_extraction}/lier` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/accessoires/extractions/{id_extraction}/lier` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| POST | `/accessoires/extractions/{id_extraction}/lier` | ressource absente | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/extractions/{id_extraction}/corriger` | nominal | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/extractions/{id_extraction}/corriger` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/accessoires/extractions/{id_extraction}/corriger` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| POST | `/accessoires/extractions/{id_extraction}/corriger` | ressource absente | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/fiches/{id_fiche}/categorie` | nominal | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| POST | `/accessoires/fiches/{id_fiche}/categorie` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/accessoires/fiches/{id_fiche}/categorie` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_fiche"],"msg":"Input should be a valid  |
| POST | `/accessoires/fiches/{id_fiche}/categorie` | ressource absente | 503 ⚠️ | {"detail":"Accessoires disponibles sur PostgreSQL uniquement : les tables accessory_extrac |
| GET | `/live` | nominal | 200 | {"status":"alive"} |
| GET | `/live` | non autorisé | 200 | {"status":"alive"} |
| GET | `/live` | paramètre invalide | 200 | {"status":"alive"} |
| GET | `/live` | ressource absente | 200 | {"status":"alive"} |
| GET | `/ready` | nominal | 200 | {"status":"ready"} |
| GET | `/ready` | non autorisé | 200 | {"status":"ready"} |
| GET | `/ready` | paramètre invalide | 200 | {"status":"ready"} |
| GET | `/ready` | ressource absente | 200 | {"status":"ready"} |
| GET | `/` | nominal | 200 | {"service":"seamtech-search-api","health":"/health","docs":"/docs"} |
| GET | `/` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/` | paramètre invalide | 200 | {"service":"seamtech-search-api","health":"/health","docs":"/docs"} |
| GET | `/` | ressource absente | 200 | {"service":"seamtech-search-api","health":"/health","docs":"/docs"} |
| GET | `/health` | nominal | 200 | {"status":"ok","backend":"sqlite","database_path":"/tmp/recette-routes-jglg9xo7/search.db" |
| GET | `/health` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/health` | paramètre invalide | 200 | {"status":"ok","backend":"sqlite","database_path":"/tmp/recette-routes-jglg9xo7/search.db" |
| GET | `/health` | ressource absente | 200 | {"status":"ok","backend":"sqlite","database_path":"/tmp/recette-routes-jglg9xo7/search.db" |
| GET | `/search` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]} |
| GET | `/search` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/search` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]} |
| GET | `/search` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]} |
| GET | `/metrics` | nominal | 200 | {"search_requests":0,"search_errors":0,"last_search_seconds":0.0,"slowest_search_seconds": |
| GET | `/metrics` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/metrics` | paramètre invalide | 200 | {"search_requests":0,"search_errors":0,"last_search_seconds":0.0,"slowest_search_seconds": |
| GET | `/metrics` | ressource absente | 200 | {"search_requests":0,"search_errors":0,"last_search_seconds":0.0,"slowest_search_seconds": |
| GET | `/preview` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","path"],"msg":"Field required","input":null}]} |
| GET | `/preview` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/preview` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","path"],"msg":"Field required","input":null}]} |
| GET | `/preview` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","path"],"msg":"Field required","input":null}]} |
| POST | `/open` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","path"],"msg":"Field required","input":null}]} |
| POST | `/open` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/open` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","path"],"msg":"Field required","input":null}]} |
| POST | `/open` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","path"],"msg":"Field required","input":null}]} |
| POST | `/imports` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/scan` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/scan` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/scan` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/scan` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/confirm` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/confirm` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/confirm` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/confirm` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/upload` | nominal | 422 | {"detail":[{"type":"missing","loc":["body","files"],"msg":"Field required","input":null}]} |
| POST | `/imports/upload` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/upload` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body","files"],"msg":"Field required","input":null}]} |
| POST | `/imports/upload` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body","files"],"msg":"Field required","input":null}]} |
| GET | `/imports/{import_id}` | nominal | 404 | {"detail":"Import not found."} |
| GET | `/imports/{import_id}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/imports/{import_id}` | paramètre invalide | 404 | {"detail":"Import not found."} |
| GET | `/imports/{import_id}` | ressource absente | 404 | {"detail":"Import not found."} |
| POST | `/imports/{import_id}/cancel` | nominal | 404 | {"detail":"Import not found."} |
| POST | `/imports/{import_id}/cancel` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/{import_id}/cancel` | paramètre invalide | 404 | {"detail":"Import not found."} |
| POST | `/imports/{import_id}/cancel` | ressource absente | 404 | {"detail":"Import not found."} |
| PATCH | `/imports/{import_id}` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| PATCH | `/imports/{import_id}` | non autorisé | 401 | {"detail":"Authentication required."} |
| PATCH | `/imports/{import_id}` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| PATCH | `/imports/{import_id}` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/{import_id}/retry-upload` | nominal | 404 | {"detail":"Import not found."} |
| POST | `/imports/{import_id}/retry-upload` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/{import_id}/retry-upload` | paramètre invalide | 404 | {"detail":"Import not found."} |
| POST | `/imports/{import_id}/retry-upload` | ressource absente | 404 | {"detail":"Import not found."} |
| GET | `/imports/{import_id}/artifacts/{artifact}` | nominal | 400 | {"detail":"Unknown artifact. Allowed: report_docx, report_pdf, source_excel, source_pdf"} |
| GET | `/imports/{import_id}/artifacts/{artifact}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/imports/{import_id}/artifacts/{artifact}` | paramètre invalide | 400 | {"detail":"Unknown artifact. Allowed: report_docx, report_pdf, source_excel, source_pdf"} |
| GET | `/imports/{import_id}/artifacts/{artifact}` | ressource absente | 400 | {"detail":"Unknown artifact. Allowed: report_docx, report_pdf, source_excel, source_pdf"} |
| POST | `/maintenance/replay-deadletters` | nominal | 503 ⚠️ | {"detail":"Redis not configured"} |
| POST | `/maintenance/replay-deadletters` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/maintenance/replay-deadletters` | paramètre invalide | 503 ⚠️ | {"detail":"Redis not configured"} |
| POST | `/maintenance/replay-deadletters` | ressource absente | 503 ⚠️ | {"detail":"Redis not configured"} |
| GET | `/maintenance/deadletters` | nominal | 503 ⚠️ | {"detail":"Redis not configured"} |
| GET | `/maintenance/deadletters` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/maintenance/deadletters` | paramètre invalide | 503 ⚠️ | {"detail":"Redis not configured"} |
| GET | `/maintenance/deadletters` | ressource absente | 503 ⚠️ | {"detail":"Redis not configured"} |
| POST | `/maintenance/cleanup` | nominal | 200 | {"status":"ok","cleanup":{"pruned_reports":0,"pruned_staged_uploads":0,"pruned_audit_logs" |
| POST | `/maintenance/cleanup` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/maintenance/cleanup` | paramètre invalide | 200 | {"status":"ok","cleanup":{"pruned_reports":0,"pruned_staged_uploads":0,"pruned_audit_logs" |
| POST | `/maintenance/cleanup` | ressource absente | 200 | {"status":"ok","cleanup":{"pruned_reports":0,"pruned_staged_uploads":0,"pruned_audit_logs" |
| GET | `/audit` | nominal | 200 | {"count":50,"offset":0,"limit":50,"results":[{"id":98,"timestamp":"2026-11-14T08:53:45.876 |
| GET | `/audit` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/audit` | paramètre invalide | 200 | {"count":50,"offset":0,"limit":50,"results":[{"id":100,"timestamp":"2026-11-14T08:53:45.89 |
| GET | `/audit` | ressource absente | 200 | {"count":50,"offset":0,"limit":50,"results":[{"id":101,"timestamp":"2026-11-14T08:53:45.89 |
| POST | `/approvals` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/approvals` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/approvals` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/approvals` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/approvals` | nominal | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/approvals` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/approvals` | paramètre invalide | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/approvals` | ressource absente | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/approvals/{id_demande}` | nominal | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/approvals/{id_demande}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/approvals/{id_demande}` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_demande"],"msg":"Input should be a vali |
| GET | `/approvals/{id_demande}` | ressource absente | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/approvals/{id_demande}/decide` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/approvals/{id_demande}/decide` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/approvals/{id_demande}/decide` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_demande"],"msg":"Input should be a vali |
| POST | `/approvals/{id_demande}/decide` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/approvals/{id_demande}/annuler` | nominal | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/approvals/{id_demande}/annuler` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/approvals/{id_demande}/annuler` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_demande"],"msg":"Input should be a vali |
| POST | `/approvals/{id_demande}/annuler` | ressource absente | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/notifications` | nominal | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/notifications` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/notifications` | paramètre invalide | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| GET | `/notifications` | ressource absente | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/notifications/tout-lire` | nominal | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/notifications/tout-lire` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/notifications/tout-lire` | paramètre invalide | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/notifications/tout-lire` | ressource absente | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/notifications/{id_notification}/lu` | nominal | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/notifications/{id_notification}/lu` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/notifications/{id_notification}/lu` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_notification"],"msg":"Input should be a |
| POST | `/notifications/{id_notification}/lu` | ressource absente | 503 ⚠️ | {"detail":"Approbations disponibles sur PostgreSQL uniquement (décision de couche §17.1) : |
| POST | `/assistant` | nominal | 503 ⚠️ | {"detail":{"etat":"indisponible","message":"L'assistant sourcé exige la couche métier Post |
| POST | `/assistant` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/assistant` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/assistant` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/assistant/etat` | nominal | 200 | {"etat":"occupe","postgres":false} |
| GET | `/assistant/etat` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/assistant/etat` | paramètre invalide | 200 | {"etat":"occupe","postgres":false} |
| GET | `/assistant/etat` | ressource absente | 200 | {"etat":"occupe","postgres":false} |
| GET | `/comparaison-voiles/candidats` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","id_extraction"],"msg":"Field required","input |
| GET | `/comparaison-voiles/candidats` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/comparaison-voiles/candidats` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","id_extraction"],"msg":"Field required","input |
| GET | `/comparaison-voiles/candidats` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","id_extraction"],"msg":"Field required","input |
| GET | `/comparaison-voiles` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","a"],"msg":"Field required","input":null},{"ty |
| GET | `/comparaison-voiles` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/comparaison-voiles` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","a"],"msg":"Field required","input":null},{"ty |
| GET | `/comparaison-voiles` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","a"],"msg":"Field required","input":null},{"ty |
| GET | `/comparaison-voiles/plusieurs` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","ids"],"msg":"Field required","input":null}]} |
| GET | `/comparaison-voiles/plusieurs` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/comparaison-voiles/plusieurs` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","ids"],"msg":"Field required","input":null}]} |
| GET | `/comparaison-voiles/plusieurs` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","ids"],"msg":"Field required","input":null}]} |
| POST | `/auth/connexion` | nominal | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/connexion` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/connexion` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/connexion` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/deconnexion` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/deconnexion` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/deconnexion` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/deconnexion` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/deconnexion-tout` | nominal | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/deconnexion-tout` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/deconnexion-tout` | paramètre invalide | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/deconnexion-tout` | ressource absente | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| GET | `/auth/session` | nominal | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| GET | `/auth/session` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/auth/session` | paramètre invalide | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| GET | `/auth/session` | ressource absente | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/mot-de-passe` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/mot-de-passe` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/mot-de-passe` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/mot-de-passe` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/auth/utilisateurs` | nominal | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| GET | `/auth/utilisateurs` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/auth/utilisateurs` | paramètre invalide | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| GET | `/auth/utilisateurs` | ressource absente | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/utilisateurs` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/utilisateurs` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs/{identifiant}/role` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs/{identifiant}/role` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/utilisateurs/{identifiant}/role` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs/{identifiant}/role` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs/{identifiant}/desactiver` | nominal | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/utilisateurs/{identifiant}/desactiver` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/utilisateurs/{identifiant}/desactiver` | paramètre invalide | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/utilisateurs/{identifiant}/desactiver` | ressource absente | 503 ⚠️ | {"detail":"Comptes nominatifs disponibles sur PostgreSQL uniquement (décision de couche §1 |
| POST | `/auth/utilisateurs/{identifiant}/mot-de-passe` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs/{identifiant}/mot-de-passe` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/auth/utilisateurs/{identifiant}/mot-de-passe` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/auth/utilisateurs/{identifiant}/mot-de-passe` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/confiance-reutilisation/evaluer` | nominal | 503 ⚠️ | {"detail":"Confiance de réutilisation disponible sur PostgreSQL uniquement (décision de co |
| POST | `/confiance-reutilisation/evaluer` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/confiance-reutilisation/evaluer` | paramètre invalide | 503 ⚠️ | {"detail":"Confiance de réutilisation disponible sur PostgreSQL uniquement (décision de co |
| POST | `/confiance-reutilisation/evaluer` | ressource absente | 503 ⚠️ | {"detail":"Confiance de réutilisation disponible sur PostgreSQL uniquement (décision de co |
| POST | `/confiance-reutilisation/cible` | nominal | 503 ⚠️ | {"detail":"Confiance de réutilisation disponible sur PostgreSQL uniquement (décision de co |
| POST | `/confiance-reutilisation/cible` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/confiance-reutilisation/cible` | paramètre invalide | 503 ⚠️ | {"detail":"Confiance de réutilisation disponible sur PostgreSQL uniquement (décision de co |
| POST | `/confiance-reutilisation/cible` | ressource absente | 503 ⚠️ | {"detail":"Confiance de réutilisation disponible sur PostgreSQL uniquement (décision de co |
| GET | `/confiance-reutilisation/evaluations` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","id_extraction"],"msg":"Field required","input |
| GET | `/confiance-reutilisation/evaluations` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/confiance-reutilisation/evaluations` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","id_extraction"],"msg":"Field required","input |
| GET | `/confiance-reutilisation/evaluations` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","id_extraction"],"msg":"Field required","input |
| GET | `/confiance-reutilisation/population` | nominal | 422 | {"detail":[{"type":"missing","loc":["query","boat_model_id"],"msg":"Field required","input |
| GET | `/confiance-reutilisation/population` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/confiance-reutilisation/population` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["query","boat_model_id"],"msg":"Field required","input |
| GET | `/confiance-reutilisation/population` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["query","boat_model_id"],"msg":"Field required","input |
| GET | `/fiches/doublons` | nominal | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| GET | `/fiches/doublons` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/doublons` | paramètre invalide | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| GET | `/fiches/doublons` | ressource absente | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| POST | `/fiches/doublons/scan` | nominal | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| POST | `/fiches/doublons/scan` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/fiches/doublons/scan` | paramètre invalide | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| POST | `/fiches/doublons/scan` | ressource absente | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| GET | `/fiches/{code}/doublons` | nominal | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| GET | `/fiches/{code}/doublons` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/{code}/doublons` | paramètre invalide | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| GET | `/fiches/{code}/doublons` | ressource absente | 503 ⚠️ | {"detail":"Détection de doublons disponible sur PostgreSQL uniquement (décision de couche  |
| POST | `/validation/doublons` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/validation/doublons` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/validation/doublons` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/validation/doublons` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/extractions-voile` | nominal | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| GET | `/extractions-voile` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/extractions-voile` | paramètre invalide | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| GET | `/extractions-voile` | ressource absente | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/resoudre-lot` | nominal | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/resoudre-lot` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/extractions-voile/resoudre-lot` | paramètre invalide | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/resoudre-lot` | ressource absente | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| GET | `/extractions-voile/{id_extraction}` | nominal | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| GET | `/extractions-voile/{id_extraction}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/extractions-voile/{id_extraction}` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| GET | `/extractions-voile/{id_extraction}` | ressource absente | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/{id_extraction}/resoudre` | nominal | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/{id_extraction}/resoudre` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/extractions-voile/{id_extraction}/resoudre` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| POST | `/extractions-voile/{id_extraction}/resoudre` | ressource absente | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/{id_extraction}/lier` | nominal | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/{id_extraction}/lier` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/extractions-voile/{id_extraction}/lier` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| POST | `/extractions-voile/{id_extraction}/lier` | ressource absente | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/{id_extraction}/corriger` | nominal | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| POST | `/extractions-voile/{id_extraction}/corriger` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/extractions-voile/{id_extraction}/corriger` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_extraction"],"msg":"Input should be a v |
| POST | `/extractions-voile/{id_extraction}/corriger` | ressource absente | 503 ⚠️ | {"detail":"Extractions de voiles disponibles sur PostgreSQL uniquement (décision de couche |
| GET | `/pieces` | nominal | 200 | {"total":0,"limit":100,"offset":0,"has_more":false,"pieces":[],"extensions":[],"dossiers": |
| GET | `/pieces` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/pieces` | paramètre invalide | 200 | {"total":0,"limit":100,"offset":0,"has_more":false,"pieces":[],"extensions":[],"dossiers": |
| GET | `/pieces` | ressource absente | 200 | {"total":0,"limit":100,"offset":0,"has_more":false,"pieces":[],"extensions":[],"dossiers": |
| GET | `/pieces/{piece_id}/apercu` | nominal | 404 | {"detail":"Fichier inconnu."} |
| GET | `/pieces/{piece_id}/apercu` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/pieces/{piece_id}/apercu` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","piece_id"],"msg":"Input should be a valid  |
| GET | `/pieces/{piece_id}/apercu` | ressource absente | 404 | {"detail":"Fichier inconnu."} |
| HEAD | `/pieces/{piece_id}/apercu` | nominal | 404 |  |
| HEAD | `/pieces/{piece_id}/apercu` | non autorisé | 401 |  |
| HEAD | `/pieces/{piece_id}/apercu` | paramètre invalide | 422 |  |
| HEAD | `/pieces/{piece_id}/apercu` | ressource absente | 404 |  |
| GET | `/pieces/{piece_id}/telecharger` | nominal | 404 | {"detail":"Fichier inconnu."} |
| GET | `/pieces/{piece_id}/telecharger` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/pieces/{piece_id}/telecharger` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","piece_id"],"msg":"Input should be a valid  |
| GET | `/pieces/{piece_id}/telecharger` | ressource absente | 404 | {"detail":"Fichier inconnu."} |
| HEAD | `/pieces/{piece_id}/telecharger` | nominal | 404 |  |
| HEAD | `/pieces/{piece_id}/telecharger` | non autorisé | 401 |  |
| HEAD | `/pieces/{piece_id}/telecharger` | paramètre invalide | 422 |  |
| HEAD | `/pieces/{piece_id}/telecharger` | ressource absente | 404 |  |
| GET | `/fiches/{code}` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/{code}` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/historique` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/historique` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/{code}/historique` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/historique` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/champs` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/champs` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/{code}/champs` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/champs` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/etat` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/etat` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/{code}/etat` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/etat` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/gabarits` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/{code}/versions` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/{code}/versions` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/gabarits/{code}/versions` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/{code}/versions` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/{code}/versions` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/gabarits/{code}/versions` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/gabarits/{code}/versions` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/gabarits/{code}/versions` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/imports/dossier/televersements/capacites` | nominal | 200 | {"creation_idempotente":1} |
| GET | `/imports/dossier/televersements/capacites` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/imports/dossier/televersements/capacites` | paramètre invalide | 200 | {"creation_idempotente":1} |
| GET | `/imports/dossier/televersements/capacites` | ressource absente | 200 | {"creation_idempotente":1} |
| POST | `/imports/dossier/televersements/v2` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/televersements/v2` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/dossier/televersements/v2` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/televersements/v2` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/televersements` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/televersements` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/dossier/televersements` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/televersements` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/imports/dossier/televersements/{jeton_session}` | nominal | 404 | {"detail":"Session de téléversement inconnue."} |
| GET | `/imports/dossier/televersements/{jeton_session}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/imports/dossier/televersements/{jeton_session}` | paramètre invalide | 404 | {"detail":"Session de téléversement inconnue."} |
| GET | `/imports/dossier/televersements/{jeton_session}` | ressource absente | 404 | {"detail":"Session de téléversement inconnue."} |
| POST | `/imports/dossier/televersements/{jeton_session}/fichiers` | nominal | 422 | {"detail":[{"type":"missing","loc":["body","chemins"],"msg":"Field required","input":null} |
| POST | `/imports/dossier/televersements/{jeton_session}/fichiers` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/dossier/televersements/{jeton_session}/fichiers` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body","chemins"],"msg":"Field required","input":null} |
| POST | `/imports/dossier/televersements/{jeton_session}/fichiers` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body","chemins"],"msg":"Field required","input":null} |
| POST | `/imports/dossier/televersements/{jeton_session}/terminer` | nominal | 404 | {"detail":"Session de téléversement inconnue."} |
| POST | `/imports/dossier/televersements/{jeton_session}/terminer` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/dossier/televersements/{jeton_session}/terminer` | paramètre invalide | 404 | {"detail":"Session de téléversement inconnue."} |
| POST | `/imports/dossier/televersements/{jeton_session}/terminer` | ressource absente | 404 | {"detail":"Session de téléversement inconnue."} |
| DELETE | `/imports/dossier/televersements/{jeton_session}` | nominal | 404 | {"detail":"Session de téléversement inconnue."} |
| DELETE | `/imports/dossier/televersements/{jeton_session}` | non autorisé | 401 | {"detail":"Authentication required."} |
| DELETE | `/imports/dossier/televersements/{jeton_session}` | paramètre invalide | 404 | {"detail":"Session de téléversement inconnue."} |
| DELETE | `/imports/dossier/televersements/{jeton_session}` | ressource absente | 404 | {"detail":"Session de téléversement inconnue."} |
| POST | `/imports/{id_import}/creer-fiche` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/imports/{id_import}/creer-fiche` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/{id_import}/creer-fiche` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/imports/{id_import}/creer-fiche` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/imports/dossier` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/dossier` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/lot` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/lot` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/imports/dossier/lot` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/imports/dossier/lot` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/lots` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/lots` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/lots` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/lots` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/lots/{id_lot}` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/lots/{id_lot}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/lots/{id_lot}` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_lot"],"msg":"Input should be a valid in |
| GET | `/lots/{id_lot}` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/lots/{id_lot}/annuler` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/lots/{id_lot}/annuler` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/lots/{id_lot}/annuler` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_lot"],"msg":"Input should be a valid in |
| POST | `/lots/{id_lot}/annuler` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/corrections/divergences` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/corrections/divergences` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/corrections/divergences` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/corrections/divergences` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/fiches/{code}/reconcilier-corrections` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/reconcilier-corrections` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/fiches/{code}/reconcilier-corrections` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/reconcilier-corrections` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/gabarits/detecter` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/detecter` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/gabarits/detecter` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/detecter` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/fiches/{code}/corriger` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/corriger` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/fiches/{code}/corriger` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/corriger` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/valider` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/valider` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/fiches/{code}/valider` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/valider` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/rejeter` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/rejeter` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/fiches/{code}/rejeter` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/rejeter` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/rouvrir` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/rouvrir` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/fiches/{code}/rouvrir` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/fiches/{code}/rouvrir` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/validation/file` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/validation/file` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/validation/file` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/validation/file` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/validation/lot` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/validation/lot` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/validation/lot` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/validation/lot` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| GET | `/fiches` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/pieces` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/pieces` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/fiches/{code}/pieces` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/fiches/{code}/pieces` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/brouillon/from-pdf` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/brouillon/from-pdf` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/gabarits/brouillon/from-pdf` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/brouillon/from-pdf` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/brouillons` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/brouillons` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/gabarits/brouillons` | paramètre invalide | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/brouillons` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/brouillons/{id_brouillon}` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/gabarits/brouillons/{id_brouillon}` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/gabarits/brouillons/{id_brouillon}` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_brouillon"],"msg":"Input should be a va |
| GET | `/gabarits/brouillons/{id_brouillon}` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/brouillons/{id_brouillon}/valider` | nominal | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| POST | `/gabarits/brouillons/{id_brouillon}/valider` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/gabarits/brouillons/{id_brouillon}/valider` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_brouillon"],"msg":"Input should be a va |
| POST | `/gabarits/brouillons/{id_brouillon}/valider` | ressource absente | 503 ⚠️ | {"detail":"Tables fiche_*/gabarit disponibles sur PostgreSQL uniquement (décision de couch |
| GET | `/ml/modeles` | nominal | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| GET | `/ml/modeles` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/ml/modeles` | paramètre invalide | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| GET | `/ml/modeles` | ressource absente | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| POST | `/ml/modeles` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/ml/modeles` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/ml/modeles` | paramètre invalide | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/ml/modeles` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/ml/entrainer` | nominal | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| POST | `/ml/entrainer` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/ml/entrainer` | paramètre invalide | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| POST | `/ml/entrainer` | ressource absente | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| POST | `/ml/peupler` | nominal | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| POST | `/ml/peupler` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/ml/peupler` | paramètre invalide | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| POST | `/ml/peupler` | ressource absente | 503 ⚠️ | {"detail":"Les endpoints /ml exigent la couche métier PostgreSQL (§17.1)."} |
| GET | `/qualite/tableau-de-bord` | nominal | 503 ⚠️ | {"detail":"Tableau qualité disponible sur PostgreSQL uniquement (migrations 006-009)."} |
| GET | `/qualite/tableau-de-bord` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/qualite/tableau-de-bord` | paramètre invalide | 503 ⚠️ | {"detail":"Tableau qualité disponible sur PostgreSQL uniquement (migrations 006-009)."} |
| GET | `/qualite/tableau-de-bord` | ressource absente | 503 ⚠️ | {"detail":"Tableau qualité disponible sur PostgreSQL uniquement (migrations 006-009)."} |
| GET | `/qualite/cartographie` | nominal | 503 ⚠️ | {"detail":"Cartographie disponible sur PostgreSQL uniquement (migrations 006-031)."} |
| GET | `/qualite/cartographie` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/qualite/cartographie` | paramètre invalide | 503 ⚠️ | {"detail":"Cartographie disponible sur PostgreSQL uniquement (migrations 006-031)."} |
| GET | `/qualite/cartographie` | ressource absente | 503 ⚠️ | {"detail":"Cartographie disponible sur PostgreSQL uniquement (migrations 006-031)."} |
| GET | `/recherche` | nominal | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/recherche` | paramètre invalide | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche` | ressource absente | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche/suggestions` | nominal | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche/suggestions` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/recherche/suggestions` | paramètre invalide | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche/suggestions` | ressource absente | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/search/suggestions` | nominal | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/search/suggestions` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/search/suggestions` | paramètre invalide | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/search/suggestions` | ressource absente | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche/journal` | nominal | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche/journal` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/recherche/journal` | paramètre invalide | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/recherche/journal` | ressource absente | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/referentiel/bateaux` | nominal | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/referentiel/bateaux` | non autorisé | 401 | {"detail":"Authentication required."} |
| GET | `/referentiel/bateaux` | paramètre invalide | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| GET | `/referentiel/bateaux` | ressource absente | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| POST | `/referentiel/bateaux/{id_modele}/alias` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/referentiel/bateaux/{id_modele}/alias` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/referentiel/bateaux/{id_modele}/alias` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_modele"],"msg":"Input should be a valid |
| POST | `/referentiel/bateaux/{id_modele}/alias` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/referentiel/bateaux/{id_modele}/fusion` | nominal | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/referentiel/bateaux/{id_modele}/fusion` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/referentiel/bateaux/{id_modele}/fusion` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_modele"],"msg":"Input should be a valid |
| POST | `/referentiel/bateaux/{id_modele}/fusion` | ressource absente | 422 | {"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]} |
| POST | `/referentiel/bateaux/{id_modele}/defusion` | nominal | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
| POST | `/referentiel/bateaux/{id_modele}/defusion` | non autorisé | 401 | {"detail":"Authentication required."} |
| POST | `/referentiel/bateaux/{id_modele}/defusion` | paramètre invalide | 422 | {"detail":[{"type":"int_parsing","loc":["path","id_modele"],"msg":"Input should be a valid |
| POST | `/referentiel/bateaux/{id_modele}/defusion` | ressource absente | 503 ⚠️ | {"detail":"La recherche des fiches exige la couche métier PostgreSQL (§17.1). Conséquence  |
