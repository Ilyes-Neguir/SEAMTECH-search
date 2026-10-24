# Plan directeur v2 — audit de base et Lot 0a

Date : 2026-10-24  
Branche de travail : `arena/b02ed560-seamtech-search`  
Base de départ vérifiée : `785e1b2` — merge `--no-ff` de :

- `e4c9fdfc2714b4870bf9cf06caab10810f69cc40` (`arena/c154b2fa-seamtech-search`) ;
- `99945b5` (skills ECC curatés, `.agents/` + `AGENTS.md`).

Aucun travail de ce lot ne part de `main`, et aucun merge vers `main` n'est
autorisé sans décision explicite ultérieure du donneur d'ordre.

## Décisions et réserve de confidentialité

Les décisions de cadrage enregistrées avant codage sont :

1. repartir de `e4c9fdf`, sans réutiliser la branche écartée `arena/c7c86aff…` ;
2. audit complet avant implémentation ;
3. objectif final : lots `0a → 6 + DEP`, avec préparation DEP maximale ;
4. dépôt GitHub laissé public pour l'instant sur décision explicite du
   donneur d'ordre.

**Réserve formelle.** Le plan directeur exige un dépôt strictement privé
(confidentialité des documents clients / Loi tunisienne 2004-63). Le dépôt est
actuellement public : cette dérogation est donc portée sous la responsabilité
du donneur d'ordre. Pendant cette dérogation, aucun nouveau document client ni
extrait nominatif ne doit être ajouté au Git.

## État de départ mesuré sur `785e1b2`

Les quatre workflows déclenchés par la base ont été relus dans GitHub :

| Workflow | Run | Résultat |
|---|---:|---|
| Qualification réservés | `40073997460` | success |
| Qualification native | `40073997472` | success |
| Perf comparaison | `40073997453` | success |
| CI | `40073997457` | **failure** |

La CI principale échouait sur trois causes distinctes, toutes traitées dans ce
lot sans affaiblir les garde-fous :

1. **RG14** : `scripts/sync_ecc_skills.py` contient légitimement l'URL du
   dépôt public ECC. Correction : exception RG14 explicite, marqueur
   `RG14_EXCEPTION` dans l'outil et justification « opérateur uniquement,
   jamais importé/exécuté par le service ».
2. **Python** : `pypdf 6.19.0` portait des CVE HIGH corrigeables
   (CVE-2026-115572, CVE-2026-115580, CVE-2026-115585). Correction :
   `pypdf==6.21.2` dans `requirements.txt` et `pyproject.toml`.
3. **Frontend** : `next 16.3.8` portait des advisories HIGH/CRITICAL
   corrigeables et faisait échouer `pnpm audit`. Correction : `next 16.4.3`,
   lockfile régénéré avec pnpm 9.15.9.

## Audit Lot 0a — écart réel sur la base e4

L'audit code a confirmé que plusieurs prémisses anciennes étaient déjà
corrigées sur e4 : cookie navigateur déjà `v2` avec refus de `v1`, jeton
backend CSPRNG de 32 octets stocké uniquement en SHA-256, et logout POST
nominatif déjà révocant la ligne PostgreSQL.

Les écarts réels restants, et eux seuls, ont été implémentés :

- le lien de navigation faisait un **GET** vers une route logout POST ;
- aucun timeout d'inactivité serveur, durée absolue par défaut de 12 h ;
- aucun registre Redis vivant des sessions ;
- aucun logout-all ;
- changement/réinitialisation de mot de passe et désactivation ne purgeaient
  pas un registre partagé ;
- aucune protection CSRF systématique des mutations navigateur ;
- réponses backend authentifiées sans `Cache-Control: no-store` global ;
- pas de `Clear-Site-Data` à la déconnexion.

## Implémentation Lot 0a

### Backend

- Nouveau `seamtech_search/sessions_redis.py` :
  - `seamtech:session:<id_session>` (hash, TTL = expiration absolue) ;
  - `seamtech:user_sessions:<id_utilisateur>` (ensemble des sessions vivantes) ;
  - seule l'empreinte SHA-256 du jeton est stockée ;
  - politique fail-closed : Redis exigé mais injoignable ⇒ erreur explicite,
    traduite en HTTP 503 par les routes/session.
- `AppConfig` : `SEAMTECH_SESSION_HOURS=8`,
  `SEAMTECH_SESSION_IDLE_MINUTES=30`,
  `SEAMTECH_REQUIRE_REDIS_SESSIONS` (activé en production Compose).
- `session_valide` contrôle expiration absolue, inactivité PostgreSQL et
  Redis, présence/empreinte Redis, puis rafraîchit l'activité.
- ouverture de session : si Redis échoue, la ligne PostgreSQL est révoquée et
  la connexion refuse en 503.
- révocation PostgreSQL + Redis pour : logout, logout-all, changement de mot
  de passe, réinitialisation administrateur et désactivation.
- nouvelle route backend `POST /auth/deconnexion-tout`, protégée par le jeton
  de service **et** une session vivante.
- middleware FastAPI : `Cache-Control: no-store, no-cache, must-revalidate,
  private` sur les réponses non exemptées.

Aucune migration SQL n'est introduite par ce lot.

### Frontend

- `frontend/lib/security-cookies.ts` centralise `seamtech_session`,
  `seamtech_csrf` et `x-csrf-token`.
- login nominatif **et** secours : nouveau jeton CSRF aléatoire de 256 bits
  dans un cookie non-httpOnly `seamtech_csrf` (double-submit).
- `frontend/proxy.ts` : mutations `/api/*` authentifiées refusées en 403 si
  l'en-tête CSRF ne correspond pas au cookie ; login exempté ; requêtes sans
  cookie de session laissées aux route handlers (401 normal).
- `authedFetch` ajoute automatiquement `X-CSRF-Token` aux méthodes
  mutantes ; les POST directs de `gabarits-app.tsx` y sont convertis.
- navigation : suppression du lien GET de logout, boutons POST « Se
  déconnecter » et « Tous mes appareils ».
- nouvelles routes/effets navigateur : `POST /api/auth/logout-all`, effacement
  session + CSRF et `Clear-Site-Data: "cache", "cookies", "storage"` sur
  logout et logout-all.
- durée absolue navigateur/backend harmonisée à 8 h ;
  `.env.example` et `docker-compose.yml` exposent les trois variables de
  session et exigent Redis pour les sessions en production.

### Tests ajoutés/renforcés

- `tests/test_sessions_redis.py` — PostgreSQL + Redis réels en CI : publication
  d'empreinte, révocation, clé absente, inactivité, expiration absolue,
  logout-all, changement de mot de passe et fail-closed 503.
- `tests/test_config.py` — défauts 8 h/30 min et surcharge par environnement.
- `frontend/e2e/auth.spec.ts` — logout sans CSRF ⇒ 403 et session toujours
  vivante ; logout avec CSRF ⇒ 200 + `Clear-Site-Data` ; logout nominatif mis à
  jour avec le vrai header.
- `frontend/e2e/helpers.ts` + `concurrence.spec.ts` — les appels API directs
  des postes connectés envoient le vrai jeton CSRF du navigateur au lieu de
  contourner le proxy.

## Preuves locales obtenues avant push

Commandes réellement exécutées dans le bac à sable de développement :

- compilation Python et `ruff check` des modules/tests modifiés : **OK** ;
- `pytest tests/test_config.py` + 3 tests RG14 ciblés : **14 passed** ;
- `pytest -m redis_queue tests/test_sessions_redis.py` en local :
  **1 passed, 7 skipped** (aucun PostgreSQL/Redis local dans le bac à sable ;
  les 7 épreuves services réels sont donc prouvées par l'étape CI dédiée, pas
  simulées ici) ;
- `pip-audit -r requirements.txt` : **No known vulnerabilities found** ;
- `pnpm audit --prod --audit-level high` : **No known vulnerabilities found** ;
- `pnpm exec tsc --noEmit` : **OK** ;
- `pnpm build` avec Next.js 16.4.3 : **OK**, route `/api/auth/logout-all`
  présente et `Proxy (Middleware)` compilé.

Le lot n'est déclaré terminé qu'après lecture des quatre workflows GitHub du
commit poussé. Les identifiants de runs sont rapportés dans le compte rendu du
lot ; ils ne sont jamais pré-écrits dans ce document.

## Déploiement et rollback

### Déploiement

1. Déployer backend + frontend du même commit.
2. Vérifier `SEAMTECH_REDIS_URL`, `SEAMTECH_SESSION_HOURS=8`,
   `SEAMTECH_SESSION_IDLE_MINUTES=30` et
   `SEAMTECH_REQUIRE_REDIS_SESSIONS=true` côté backend.
3. Tous les utilisateurs doivent se reconnecter : les sessions antérieures au
   Lot 0a n'ont ni clé Redis ni cookie CSRF et sont donc refusées.

### Rollback

1. `git revert <commit-du-lot-0a>` puis redéployer le commit résultant.
   Aucune migration inverse n'est nécessaire (le lot n'ajoute aucune migration).
2. Forcer une reconnexion globale est recommandé. En fenêtre de maintenance,
   l'opérateur peut supprimer les clés `seamtech:session:*` et
   `seamtech:user_sessions:*` par balayage (`SCAN`, jamais `KEYS`) avec les
   identifiants Redis de production.
3. Les lignes `session_ui` restent l'historique PostgreSQL ; ne pas les
   supprimer.

## Périmètre non prétendu

Ce document clôt uniquement le **Lot 0a**. Les lots `0b`, `2`, `4a`, `1`, `3a`,
`3b`, `4b`, `5`, `6` et `DEP` restent à implémenter et à prouver séparément,
chacun avec son commit et sa CI complète.
