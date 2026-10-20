# Sécurité des sessions navigateur — Lot 0a

Ce document justifie les deux mécanismes transverses du Lot 0a : la protection
**CSRF par double-submit** et la règle **`Cache-Control: no-store` sur les
réponses authentifiées**. Il décrit ce qui est réellement dans le code, et
nomme les limites au lieu de les passer sous silence. Les chemins cités sont
ceux de la branche `arena/dc54c343-seamtech-search` au commit de livraison.

## 1. Le modèle de session en une page

- Le cookie `seamtech_session` (httpOnly, SameSite=Lax, Path=/, `Secure`
  conditionnel) porte un **`sid` opaque de 256 bits** (`secrets.token_urlsafe(32)`
  côté backend) et un instantané d'identité **signé** (HMAC-SHA256,
  `SEAMTECH_SESSION_SECRET`). La signature protège l'instantané contre la
  falsification ; elle **n'authentifie rien**.
- L'autorité est Redis : `seamtech:session:<sid>` porte `user_id`, `issued_at`,
  `last_seen_at` et `expires_at` avec un TTL égal à l'expiration absolue.
  `seamtech:user_sessions:<user_id>` indexe les sid vivants du compte.
- Un `sid` absent, révoqué, inactif depuis plus de 30 min
  (`SEAMTECH_SESSION_IDLE_MINUTES`) ou expiré au-delà de 8 h
  (`SEAMTECH_SESSION_HOURS`) répond **401**. Le `sid` change à chaque
  connexion (anti-fixation).
- Redis ou le backend indisponible ⇒ **503** explicite (fail-closed). Aucun
  repli mémoire n'existe : `seamtech_search/sessions_redis.py` ne le prévoit
  pas.
- **Invalidation durable : une GÉNÉRATION, pas un horodatage.** Chaque session
  nominative porte `etat_identifiants`, une empreinte calculée à partir de
  l'empreinte du mot de passe, de `mot_de_passe_modifie_le`, de
  `sessions_invalidees_le` et de `actif`
  (`comptes.securite.etat_identifiants`). Elle est **capturée au moment exact
  de la vérification du mot de passe** (`verifier_identifiants`), puis comparée
  à la valeur recalculée depuis PostgreSQL à **chaque** requête
  (`_session_sid`). Deux valeurs différentes ⇒ **401**, même si la clé Redis
  existe encore. La colonne `utilisateur.sessions_invalidees_le` (migration
  `024_invalidation_sessions`, une colonne, aucune table nouvelle) reste
  écrite dans la transaction de la mutation, mais elle n'est plus comparée à
  un instant : elle entre dans l'empreinte comme une valeur. Le `COMMENT` de
  cette colonne décrit encore l'ancienne comparaison — la migration 024 est
  laissée **telle quelle**, un schéma déjà migré ne la rejouerait pas ; c'est
  `etat_identifiants` qui fait foi.
  Pourquoi ce changement, demandé par la revue :
  * **course connexion / réinitialisation** — un login peut vérifier l'ANCIEN
    mot de passe puis créer sa session APRÈS qu'un changement a été validé ;
    son `issued_at` est postérieur, donc une comparaison d'instants l'accepte.
    Avec la génération capturée à la vérification, la session porte l'ancienne
    valeur et la revalidation la refuse, quel que soit son instant de création ;
  * **répliques** — une égalité de valeurs ne suppose aucune horloge
    synchronisée entre machines ; une comparaison d'instants, si ;
  * **réactivation** — `actif` entre dans l'empreinte : désactiver puis
    réactiver change la génération, donc les sessions d'avant restent mortes.
  **Choix explicite : la mutation est COMMITÉE avec la nouvelle génération, pas
  annulée.** Un rollback laisserait l'ancien mot de passe valide au moment
  précis où l'utilisateur croit l'avoir changé — et le 503 lui dirait
  seulement « réessayez ». La génération ferme l'accès sans dépendre d'un
  second système.
- **Atomicité mutation ↔ sessions historiques.** Le changement par l'intéressé
  révoque les lignes `session_ui` **dans la même transaction** que l'écriture
  de la nouvelle empreinte : les deux réussissent ou les deux sont annulées.
  La session interne (`X-SEAMTECH-SESSION` + jeton) est en outre liée à la
  génération d'identifiants de son émission
  (`empreinte_jeton(jeton, etat_identifiants=…)`) : sa validation recalcule la
  génération courante et refuse une session ouverte avant un changement, même
  si sa ligne n'a pas été révoquée (mutation interrompue, ligne oubliée par un
  ancien code).
- **La déconnexion ne déclare jamais un succès qu'elle n'a pas obtenu.** Si un
  `sid` est présenté et que la révocation n'aboutit pas — `SEAMTECH_API_URL`
  absent, backend injoignable, ou réponse non-2xx (Redis arrêté → 503) —
  `POST /api/auth/logout` répond **503** avec `ok: false` et
  `revoquee: false`, en indiquant `reason` (`backend_non_configure`,
  `backend_injoignable`, `revocation_refusee`) et, le cas échéant,
  `statut_backend`. Les deux cookies sont **quand même** effacés avec leurs
  attributs exacts et `Clear-Site-Data: "cache", "cookies", "storage"` est
  envoyé : le navigateur est déconnecté localement, mais la session serveur est
  rapportée comme toujours valide jusqu'à son expiration. Un client ne peut
  donc pas lire « 200 / ok » alors que le `sid` travaille encore. Sans `sid`
  présenté, la réponse reste 200 (`deja_deconnecte: true`) : il n'y a rien à
  révoquer.

## 2. CSRF : pourquoi le double-submit, et ce qu'il ne protège pas

### 2.1 Mécanisme réellement implémenté

1. À la connexion, `frontend/app/api/auth/login/route.ts` pose **deux**
   cookies : la session (httpOnly) et `seamtech_csrf` (lisible par JavaScript,
   `httpOnly: false`, SameSite=Lax, Path=/, `Secure` conditionnel). La valeur
   du jeton CSRF est `randomBytes(32).toString("base64url")`, soit 256 bits.
2. Chaque requête mutante construite par le client passe par `withCsrf()`
   (`frontend/lib/csrf.ts`) : `authedFetch` (`frontend/lib/authed-fetch.ts`),
   la barre de navigation (`frontend/components/nav.tsx`), le bouton de
   déconnexion et les gabarits. Le cookie est recopié dans l'en-tête
   `X-CSRF-Token`.
3. `frontend/proxy.ts` intercepte les `POST`/`PUT`/`PATCH`/`DELETE` sous
   `/api/*` **lorsqu'un cookie de session est présent**. Il refuse avec
   **403** si le cookie CSRF manque, si l'en-tête manque, ou si les deux
   diffèrent. Le contrôle a lieu **avant** le handler de route. Seul
   `/api/auth/login` est exempté (il n'y a pas encore de session, et il doit
   pouvoir remplacer un cookie périmé).
4. `POST /api/auth/logout` et `POST /api/auth/logout-all` **ne sont pas
   exemptés** : c'est précisément là qu'un POST cross-site ferait mal.
5. La comparaison est une égalité de chaînes dans le proxy (runtime Edge). Le
   jeton fait 256 bits et n'est jamais réutilisé comme secret de session ; une
   attaque temporelle sur cette comparaison supposerait de mesurer des
   différences de l'ordre de la nanoseconde à travers le réseau, pour un jeton
   qu'on ne peut pas deviner. Ce n'est pas `timingSafeEqual`, et ce document ne
   prétend pas le contraire.

### 2.2 Pourquoi ce choix plutôt qu'un jeton synchronisé

- Les handlers de routes sont des fonctions Next.js (runtime Edge pour le
  proxy) : ils n'ont **pas d'accès Redis**. Un jeton synchronisé stocké côté
  serveur imposerait soit un appel réseau supplémentaire par requête au
  backend, soit un second magasin d'état dont l'expiration devrait être
  maintenue en phase avec les sessions — deux sources de vérité pour un
  contrôle qui doit rester vérifiable avant le handler.
- Le double-submit ne stocke aucun état supplémentaire : le cookie et l'en-tête
  sont comparés. Il est régénéré à chaque connexion et n'est pas dérivé du
  `sid`.
- **SameSite=Lax est la première barrière** : un POST cross-site ne transporte
  pas le cookie de session dans les navigateurs modernes. Le double-submit est
  une défense **indépendante** qui couvre les clients qui ignorent SameSite et
  les scénarios same-site (sous-domaines), où SameSite ne sépare pas les
  origines.

### 2.3 Portée exacte et limites assumées

| Point | Réalité dans le code | Conséquence |
| --- | --- | --- |
| XSS | Le cookie CSRF est lisible par script et l'en-tête peut être posé par script | Un XSS **contourne** le double-submit. La protection XSS n'est pas revendiquée ; le cookie de session reste httpOnly, ce qui limite l'exfiltration mais pas les actions. |
| Sous-domaines | Le cookie n'a pas le préfixe `__Host-` | Un sous-domaine compromis pourrait poser un cookie `seamtech_csrf` (cookie tossing). Mitigation d'exploitation : héberger l'application sur une origine dédiée, sans sous-domaines partageant le domaine parent. |
| `/api/auth/login` | Exempté de la vérification | Le **login CSRF** (forcer la victime dans le compte de l'attaquant) n'est pas empêché par le double-submit. Risque accepté pour une archive mono-tenant : l'attaquant doit fournir ses propres identifiants, et la victime verrait l'identité de l'attaquant. Une vérification d'`Origin` pourrait être ajoutée plus tard ; elle n'est **pas** implémentée. |
| `GET` | Non couverts par la vérification CSRF | Aucune route `GET` authentifiée ne modifie l'état : `GET /api/auth/logout` et `GET /api/auth/logout-all` répondent **405** avec `Allow: POST`. |
| `Origin`/`Referer` | Non vérifiés | Non revendiqué : la protection repose sur SameSite + double-submit, pas sur l'en-tête `Origin`. |
| Liaison au `sid` | Le jeton CSRF n'est pas lié à la session | C'est le propre du double-submit. Un attaquant capable de poser à la fois le cookie et l'en-tête a déjà une exécution de script ou un contrôle du domaine. |
| Comparaison | Égalité simple | Pas de comparaison à temps constant, cf. §2.1. |

### 2.4 Preuves

- `frontend/e2e/auth.spec.ts` : « POST /api/auth/logout sans jeton CSRF est
  rejeté (403) et ne déconnecte pas » (la session reste vivante, puis la
  déconnexion avec le jeton aboutit), et « GET /api/auth/logout is refused with
  405 (POST only) ».
- Les quatre `POST` directs de `frontend/e2e/concurrence.spec.ts` passent par
  le helper `csrfHeaders()` : sans lui, le proxy répondait 403 (mesuré en CI,
  run 39267854884) — preuve que la vérification s'applique aussi aux clients
  scriptés qui réutilisent les cookies du navigateur.

### 2.5 Preuve de la déconnexion en panne de Redis (aucun mock)

`frontend/e2e/panne-redis.spec.ts` arrête **réellement** le serveur Redis qui
sert la session : il lui envoie sa propre commande d'arrêt (`SHUTDOWN NOSAVE`,
protocole RESP sur une socket TCP), puis attend que le port **refuse** les
connexions avant d'observer quoi que ce soit. Aucun client n'est doublé,
aucune panne n'est simulée au niveau applicatif. Le test exige alors :

1. `POST /api/auth/logout` → **503**, `ok` ≠ `true`, `revoquee` ≠ `true`,
   `reason: "revocation_refusee"`, `statut_backend: 503` ;
2. les deux cookies effacés (`max-age=0`, `Path=/`, `HttpOnly`,
   `SameSite=Lax`) et `Clear-Site-Data` présent **malgré** la panne ;
3. `/api/search`, `/api/pieces` et `/api/auth/session` → **503** (fail-closed)
   pendant la panne, jamais 200 avec des données.

Garde-fou d'exécution : la spec refuse de tourner si
`SEAMTECH_E2E_PANNE_REDIS=1` n'est pas positionné, si l'URL ne vise pas une
instance locale, ou si elle vise le port **6379** partagé. L'étape CI
« panne Redis réelle » démarre une instance jetable sur 6380, et la supprime
ensuite ; le service Redis du job n'est jamais touché. La suite ordinaire
ignore ce fichier (`testIgnore` de `playwright.config.ts`) : il ne peut donc
ni être ramassé par erreur, ni compter comme un test sauté.

### 2.6 Preuve : la révocation Redis peut échouer, la session meurt quand même

`tests/test_sessions_redis_postgres.py` provoque la panne **dans un Redis
réel**, sans aucun double : un utilisateur ACL (`+@all -del -srem`) peut lire
et écrire, mais le serveur refuse `DEL`/`SREM` avec une vraie erreur `NOPERM`.
C'est exactement ce que fait une révocation. Les tests exigent alors :

1. `POST /auth/mot-de-passe` (et la réinitialisation administrateur, et la
   désactivation) → **503** : la révocation n'a pas eu lieu, l'API ne prétend
   pas le contraire ;
2. les clés `seamtech:session:<sid>` sont **toujours présentes** dans Redis —
   l'état laissé par une panne suivie d'un retour sans perte ;
3. après retour du service (client Redis normal), les anciens `sid` répondent
   **401** avec `Session invalidée…` : c'est PostgreSQL, pas Redis, qui refuse,
   parce que la génération d'identifiants de la session ne correspond plus ;
4. le nouveau mot de passe ouvre une session, l'ancien est refusé ; pour une
   réinitialisation, `doit_changer_mot_de_passe` vaut `true` ; pour une
   désactivation, la connexion est refusée.

Un test complémentaire vérifie que `revoquer_toutes_sessions_pour_identifiant()`
**lève** `SessionStoreIndisponible` quand Redis est injoignable : son retour
`0` ne peut plus être lu comme « rien à révoquer, confirmé ».

Deux preuves de plus, ajoutées par la revue suivante :

* **course connexion / réinitialisation** — le test force l'ordre exact
  (vérifier l'ancien mot de passe → réinitialiser → créer les sessions SQL et
  Redis avec la génération capturée) et exige **401** sur la session
  navigateur ET sur la session historique, puis vérifie qu'avec le nouveau mot
  de passe les mêmes étapes donnent **200** (contrôle positif) ;
* **atomicité** — un trigger PostgreSQL réel refuse toute révocation de
  `session_ui` ; le changement de mot de passe doit alors échouer **en
  entier** : l'ancien mot de passe reste valide, le nouveau est refusé, la
  ligne n'est pas à moitié révoquée et la session historique fonctionne
  toujours.

## 3. `Cache-Control: no-store` sur les réponses authentifiées

### 3.1 Les trois couches

1. **Proxy Next** (`frontend/proxy.ts`) : si le cookie `seamtech_session` est
   présent, toute réponse — pages HTML **et** `/api/*` — reçoit
   `Cache-Control: no-store` et `Vary: Cookie`. Le matcher couvre `/api/:path*`
   et toutes les pages ; seuls les fichiers statiques (`_next/static`,
   images, CSS, JS, polices) sont exclus, car ils ne contiennent aucune donnée
   d'archive.

   **Mode développement vs production.** En production (`next start`, le mode
   déployé par le conteneur), la valeur `no-store` posée par le proxy est
   conservée sur les pages (vérifié par `curl` et par le test e2e live). En
   mode `next dev`, Next réécrit l'en-tête des réponses HTML en
   `no-cache, must-revalidate` : la page n'est alors pas réutilisable sans
   revalidation, mais ce n'est pas littéralement `no-store`. La suite e2e
   SQLite (qui tourne en dev) accepte cet équivalent pour la PAGE et exige
   `no-store` strict sur les API ; le test live (production) exige `no-store`
   sur la page, l'API métier et le téléchargement.
2. **Backend FastAPI** (`seamtech_search/api.py`, middleware) : toute requête
   portant un justificatif de session (`X-SEAMTECH-SESSION-SID` ou
   `X-SEAMTECH-SESSION`), le jeton de service, ou visant `/auth/*`, reçoit
   `Cache-Control: no-store` — quel que soit le statut (200, 206, 401, 403,
   503). Comme le frontend envoie toujours le jeton de service au backend,
   **toutes** les réponses backend consommées par l'interface sont couvertes,
   y compris celles qui ne passent pas par le cookie (outils internes).
3. **Téléchargement de pièces** (`frontend/lib/piece-proxy.ts`) : le proxy
   reconstruit les en-têtes avec `Cache-Control: no-store` et ne recopie pas le
   `cache-control` amont. Un `206 Partial Content` (requête `Range`) ou un
   `HEAD` conserve `no-store`.

Les routes de session (`/api/auth/session`, `/api/auth/logout`,
`/api/auth/logout-all`) posent en plus explicitement `no-store` + `Vary:
Cookie` dans leur propre réponse.

### 3.2 Preuves automatisées

- `tests/test_api.py::test_authenticated_responses_are_no_store` : API métier
  (`/search`, `/pieces`) authentifiée par jeton de service → 200 +
  `no-store`.
- `tests/test_sessions_redis_postgres.py` : `/auth/session` avant et après
  révocation → `no-store` (401 inclus).
- `frontend/e2e/auth.spec.ts` : page protégée `/fichiers`, API métier
  `/api/search`, `/api/auth/session`.
- `frontend/e2e/auth.spec.ts` (mode live, PostgreSQL, front de production) :
  page protégée `/fichiers` → `no-store` ; téléchargement réel
  `/api/pieces/<id>/telecharger` → 200, `no-store`, `application/pdf`, corps
  commençant par `%PDF`, et 401 sans session.

### 3.3 Ce que `no-store` ne fait pas

- Il n'empêche pas un **cache mémoire de moteur** de réafficher un document
  lors d'un `Retour` (bfcache) : les navigateurs ont des politiques
  différentes. Le test e2e du Retour vérifie le comportement réel de Chromium
  (redirection vers `/login` ; rechargement forcé si un document en cache
  mémoire est réaffiché) et exige qu'**aucune donnée protégée** ne soit
  visible.
- Il ne protège pas les routes non authentifiées (`/login` sans session,
  `/api/health` anonyme) : elles ne contiennent pas de donnée d'archive.
- Il ne remplace ni TLS ni les contrôles d'accès : il empêche seulement la
  conservation d'une réponse authentifiée par un cache intermédiaire.
