# Plan directeur v2 — Lot 0b

Date : 2026-10-24
Branche de travail : `arena/b02ed560-seamtech-search`
Base : Lot 0a `f36749b7b1c7990bd9650dc31936574937eb04d8`.

Ce lot ne part jamais de `main` et ne merge rien vers `main` sans autorisation
explicite du donneur d'ordre.

## Objectif du plan

- journal d'audit append-only et structuré ;
- anti-IDOR : aucun accès aux octets d'un objet sans session humaine et sans
  confinement aux zones prévues ;
- réduction de surface : les surfaces opérationnelles ne répondent plus au
  seul jeton de service ;
- deux vérifications reportées : inventaire du SQL dynamique et inventaire
  des autorisations par endpoint ;
- migration `025_audit_logs`.

## Écart réel mesuré avant implémentation

L'audit de code a d'abord séparé ce qui était déjà vrai sur la base e4/Lot 0a
de ce qui restait à faire :

- le navigateur ouvrait déjà les fichiers par **ID catalogue**
  (`/pieces/{id}`), et `/api/preview` + `/api/open` navigateur répondaient
  déjà 410 après contrôle de session ;
- les corrections/validations de fiches utilisaient déjà
  `resoudre_attribution()` et des verrous optimistes par révision ;
- en revanche, le backend acceptait le **seul jeton de service** sur les
  octets des pièces et artefacts, `/audit`, `/metrics`, `/maintenance/*`,
  `/preview`, les routes ML et le journal de recherche ;
- `POST /open` servait tout fichier présent sous une racine configurée, même
  non catalogué ;
- le chemin d'un artefact, relu depuis le payload d'import, pouvait désigner
  un fichier arbitraire de la machine ;
- le journal historique `audit_log` était une table ordinaire élaguable et ne
  contenait ni `request_id`, ni acteur structuré, ni chaînage d'intégrité ;
- les proxys Next de données n'envoyaient majoritairement que le jeton de
  service, sans la session du compte.

## Implémentation

### 1. Journal canonique `audit_logs`

- `024_sessions_redis` est enregistré comme marqueur sans DDL du Lot 0a.
- `025_audit_logs` crée en PostgreSQL la table `audit_logs` :
  horodatage, `request_id`, acteur structuré (`actor_type`, `actor`,
  `id_utilisateur`, `identifiant`, `role`, `id_session`), action, ressource
  (`resource_type` / `resource_id`), statut, code HTTP, IP, user-agent et
  `details` JSONB.
- Intégrité : `empreinte_precedente` + `empreinte` SHA-256 calculées sous
  verrou advisory, index unique sur l'empreinte, trigger qui refuse tout
  `UPDATE`/`DELETE`.
- La migration recopie l'historique `audit_log` existant dans l'ordre
  chronologique, avec les mêmes détails expurgés et une chaîne complète.
- `seamtech_search/audit.py` écrit d'abord la table historique (compatibilité
  SQLite + verrouillage des connexions), puis le journal canonique.
  `record_audit_event()` masque récursivement mots de passe, jetons, cookies,
  secrets et empreintes — y compris lorsqu'ils apparaissent dans un texte
  libre ou une URL/DSN d'erreur — et borne la taille des détails
  (4 096 octets en JSON).
- `GET /audit` lit le journal canonique dès qu'il n'est pas vide, en gardant
  les clés historiques de la réponse et en exposant en plus acteur, session,
  requête, ressource et empreintes.
- La rétention historique continue d'élaguer uniquement `audit_log` ;
  `audit_logs` n'est jamais élagué automatiquement.
- En SQLite, la migration 025 est un no-op journalisé et le comportement
  historique reste inchangé (les fiches/comptes nominatifs sont de toute
  façon PostgreSQL-only).

Un middleware ASGI journalise dans ce journal toutes les mutations, tous les
refus HTTP et les lectures sensibles (octets de pièces/artefacts, audit,
maintenance, administration, journal de recherche, métriques), avec
uniquement méthode + chemin — jamais le corps ni la query string. Les routes
de compte émettent en plus des événements métier explicites (déconnexions,
création, désactivation, changements de mot de passe).

### 2. Acteur de requête vérifié (`seamtech_search/securite_requetes.py`)

Lorsque des en-têtes de session sont présentés, ils ne sont plus crus sur
parole : jeton de service valide, session vivante via `session_valide()`
(PostgreSQL + registre Redis du Lot 0a), compte actif relu en base, et
compatibilité stricte entre les en-têtes `X-SEAMTECH-UTILISATEUR/ROLE`, la
session et le compte. Toute usurpation reçoit 401 ; un Redis obligatoire
injoignable reçoit 503. Le compte `secours` reste honoré uniquement derrière
le jeton de service. En mode service sans session :

- PostgreSQL : les en-têtes nominatifs sont traités, jamais ignorés ;
- SQLite : le contrat d'outillage historique est conservé (pas de comptes
  nominatifs possibles), et l'attribution est étiquetée « service ».

L'attribution des fiches utilise désormais cette session vérifiée en premier ;
l'ancien repli par relecture d'état ne subsiste que pour l'outillage sans
en-têtes de session.

### 3. Gardes par surface (vérification #1 : autorisations par endpoint)

En PostgreSQL, le seul jeton de service ne suffit plus pour :

| Surface | Exigence |
|---|---|
| `GET /audit`, `GET /metrics`, `GET /preview`, `/maintenance/*` | administrateur humain |
| `/ml/modeles`, `/ml/entrainer`, `/ml/peupler` | administrateur humain |
| `GET /recherche/journal` | administrateur humain |
| `GET|HEAD /pieces/{id}/apercu`, `/pieces/{id}/telecharger` | humain connecté |
| `GET /imports/{id}/artifacts/{artifact}` | humain connecté |
| `POST /fiches/doublons/scan` avec `appliquer=true` | humain connecté |

Les métadonnées du catalogue (`GET /pieces`, `GET /fiches/{code}/pieces`)
reste au jeton de service, car l'outil de reconstruction serveur en dépend et
elle ne livre aucun octet de document.

Classification complète arrêtée par l'inventaire :

- **publics sans donnée** : `/live` et `/ready` uniquement ; `/docs` et
  `/openapi.json` restent désactivés dès qu'un jeton est configuré ;
- **authentification** : les routes `/auth/*` gardent leurs contrôles propres
  (session vivante, administrateur pour la gestion des comptes) ;
- **parcours métier** (recherche, fiches, imports, gabarits, qualité,
  déduplication en lecture/dry-run) : contrat backend historique « jeton de
  service du proxy de confiance », en attendant les quatre rôles du Lot 1.
  Le Lot 0b y ajoute la propagation systématique de la session, sa
  revérification pour l'attribution des écritures de fiches et l'audit HTTP
  des mutations/refus ;
- **objets et exploitation** : gardes humaines/administrateur du tableau
  ci-dessus ;
- **exceptions d'outillage serveur assumées** : `/health` (sonde détaillée
  derrière jeton), métadonnées du catalogue et `/open` — ce dernier étant
  désormais limité aux documents catalogués en PostgreSQL.

Le jeton de service reste donc une frontière de confiance interne jusqu'au
Lot 1 ; il ne doit exister que côté serveur (proxy, worker, scripts
opérateur), jamais dans le navigateur.

### 4. Anti-IDOR et confinement des chemins

- `POST /open` : en PostgreSQL, le chemin normalisé doit exister dans
  `documents.path_key` ; un fichier simplement posé sous une racine reçoit
  404. Le refus est journalisé (`action=open`, raison `document_non_catalogue`).
- Artefacts d'import : un `local_path` issu du payload qui ne tombe ni sous
  une racine configurée, ni sous le staging, ni sous le dossier des rapports
  est neutralisé avant toute lecture locale **et** avant tout cache S3.
- Dépôts par dossier (`POST /imports/dossier`, `/imports/dossier/lot`) : la
  cible doit être sous une racine configurée ou le staging, sinon 403 —
  déposer sous `/tmp` ou ailleurs n'est plus possible par l'API.

### 5. Vérification #2 : inventaire du SQL dynamique

L'inventaire a été fait par AST sur tout `seamtech_search/` (f-strings SQL,
concaténations, `.format`, fragments `WHERE`/`ORDER` assemblés, marqueurs de
dialecte et appels `execute` non constants), puis chaque famille a été relue
jusqu'à la source de ses fragments. Conclusion : les **valeurs** (requêtes,
codes, filtres, bornes, identifiants, JSON) sont liées (`%s`/`?`,
`ANY(%s)`, `psycopg2.extras.Json`) ; seuls des fragments fermés ou des
identifiants sont assemblés :

| Famille | Contrôle vérifié |
|---|---|
| Conditions conditionnelles | fragments littéraux choisis par booléen ou présence d'un filtre (`_statut_sql`, CAS de révision, `_where_archive`, clause de période du journal) ; les valeurs restent en paramètres |
| Colonnes de recherche/assistant | dictionnaires fermés `CHAMPS_COTES`, `ATTRIBUTUTS_GALON`, `CHAMPS_FICHE`, `COTES_UNITES`/`COTES_AUTORISEES`, `TRIS_AUTORISES` ; un nom hors liste n'atteint jamais le SQL |
| Corrections canoniques | le registre `CibleCanonique` ne produit que des tables/colonnes/`WHERE` littéraux énumérés ; codes, postes, jeux et rangs d'entrée sont validés puis liés |
| Rafraîchissement de fiche | tables filles limitées à la constante `_TABLES_FILLES_RAFRAICHIES` |
| CTE et facettes de recherche | gabarits constants `_FACETTES_AXES` ; seuls les noms de CTE dérivés des axes constants et le fragment de filtres ci-dessus sont assemblés |
| Configuration FTS | uniquement `seamtech_unaccent` ou `simple` (`PG_TS_CONFIGS`) ; les migrations remplacent un marqueur interne par l'un de ces deux noms |
| DDL et introspection | colonnes SQLite ajoutées depuis des dictionnaires de migration fixes ; tables/colonnes lues dans le catalogue sont désormais citées avec doublement des guillemets des deux côtés ; la table d'instantané est générée depuis le PID du processus |

Deux durcissements ont été ajoutés pendant cette vérification :

- `seamtech_search/sauvegarde.py` : les identifiants de tables (comptages de
  restauration) et le nom de la base cible passent par
  `psycopg2.sql.Identifier` ;
- `seamtech_search/jobs.py` : les colonnes `UPDATE` de `import_jobs` passent
  par la liste fermée `COLONNES_MAJ_JOB_AUTORISEES` ; toute autre clé lève
  `ValueError`. `update_job_source_path` n'assemble que trois clauses
  littérales fixes ;
- `seamtech_search/indexer.py` : la garde PostgreSQL de suppression et les
  requêtes SQLite d'introspection citent systématiquement les identifiants
  relus dans le schéma.

### 6. Propagation frontend et outillage

- Tous les proxys Next de données utilisent `await backendHeaders()` (jeton
  de service + identité/session recalculées depuis le cookie signé) ; seuls
  les endpoints d'authentification et `/api/health` restent sur
  `authHeaders()`. Un test garde-fou scanne désormais le source pour
  empêcher toute régression (`headers = authHeaders()` hors auth/health).
- `scripts/recette_verif.py` conserve la session nominative ouverte à la
  connexion et l'envoie sur tous les appels suivants ; le jeton de session
  n'est plus jamais imprimé dans son compte rendu.
- `scripts/verifier_hors_ligne.py` envoyait déjà une vraie session ; aucun
  changement n'était requis.

## Preuves

`tests/test_lot0b_audit_securite.py` (service jetable PostgreSQL réel en CI,
sans mock sur les briques critiques) :

- redaction récursive des secrets et bornage des détails (SQLite) ;
- refus des sessions/en-têtes incohérents, 401/403/200 par surface ;
- le middleware journalise les mutations avec contexte, sans secret ;
- un `local_path` hors zone est neutralisé (404, aucun octet livré) ;
- garde-fou source de propagation `backendHeaders()` ;
- sur PostgreSQL jetable : migrations `001…025`, backfill de l'historique,
  chaîne SHA-256 complète (lien précédent + recalcul de chaque empreinte),
  refusal `UPDATE`/`DELETE` sur `audit_logs`, `GET /audit` canonique
  réservé à l'administrateur, et `/open` qui refuse un fichier non catalogué
  puis sert le même fichier une fois catalogué.

Les suites existantes touchées ont été migrées vers une vraie session ou
vers l'acteur d'outillage documenté plutôt qu'affaiblies
(`test_fichiers_par_id`, `test_modele_maison`, `test_journal_recherche`,
`test_scan_browser_upload_ocr`, `test_recette_corpus_reel`,
`test_compose_partage_worker`).

Preuves locales obtenues avant push (bac à sable, sans PostgreSQL local) :

- `py_compile` + `ruff check` des modules modifiés : **OK** ;
- tests ciblés SQLite/audit/fichiers par ID : **27 passed** (2 épreuves
  PostgreSQL désélectionnées, prouvées en CI) ;
- suites API/jobs/gate : **58 passed** ;
- `pnpm exec tsc --noEmit` et `pnpm build` (Next.js 16.4.3) : **OK**.

Le lot n'est déclaré terminé qu'après lecture des quatre workflows GitHub du
commit poussé ; leurs identifiants sont rapportés dans le compte rendu, jamais
pré-écrits ici.

## Déploiement et rollback

### Déploiement

1. Déployer backend + frontend du même commit.
2. Au démarrage, les migrations 024 (marqueur) et 025 s'appliquent
   automatiquement ; 025 recopie l'historique `audit_log` dans `audit_logs`.
3. Les scripts opérateur qui appellent le backend en direct doivent utiliser
   une session administrateur (ou `secours` derrière le jeton de service)
   pour les surfaces opérationnelles listées ci-dessus.
4. Inclure `audit_logs` dans les sauvegardes : c'est le journal de sécurité
   canonique et il n'est pas élagué.

### Rollback

1. `git revert <commit-du-lot-0b>` puis redéployer le commit résultant.
2. La table additive `audit_logs` peut rester en place : l'ancien code
   continue d'utiliser `audit_log`. Ne pas la supprimer (preuve).
3. Un redéploiement ultérieur du code Lot 0b reprend la chaîne à la dernière
   ligne présente.

## Réserve de confidentialité

Le dépôt GitHub reste public sur décision explicite du donneur d'ordre,
contrairement à l'exigence du plan (repo strictement privé, documents clients
/ Loi 2004-63). Cette dérogation reste portée sous sa responsabilité ; aucun
nouveau document client ni extrait nominatif n'a été ajouté pendant ce lot.

## Périmètre non prétendu

Ce document clôt uniquement le **Lot 0b**. Le modèle d'autorisation reste
celui d'une archive partagée derrière jeton de service + comptes
`operateur`/`administrateur` ; les quatre rôles métier et les approbations
sont le Lot 1. Les lots `2`, `4a`, `1`, `3a`, `3b`, `4b`, `5`, `6` et `DEP`
restent à implémenter et à prouver séparément.
