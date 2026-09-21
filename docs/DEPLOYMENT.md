# SEAMTECH Search office deployment

This is the supported deployment path for an office Windows machine running Docker
Desktop. The application stack is PostgreSQL, MinIO, Redis, the SEAMTECH API, and
the Next.js frontend. The compose file publishes the application ports on
`127.0.0.1`; expose the frontend to the office LAN only through a TLS-terminating
reverse proxy.

## Prerequisites

- Windows 10/11 with Docker Desktop using the WSL 2 backend.
- Docker Desktop has enough CPU, memory, and disk for the document collection.
- The repository is checked out on a local drive shared with Docker Desktop.
- A hostname and certificate are available if other office computers will use the
  application. Do not publish the backend or database ports directly to the LAN.

Verify the installation in PowerShell:

```powershell
docker version
docker compose version
```

## First installation

Run these commands from the repository directory. Keep the repository directory
stable: the named database and MinIO volumes survive container recreation, while
`data` contains the local working files and cache.

```powershell
New-Item -ItemType Directory -Force data, logs, data\DesignFiles | Out-Null
Copy-Item .env.example .env
notepad .env
```

Edit `.env` before starting the stack. Set unique, long values for all required
secrets; add `REDIS_PASSWORD`, which is required by `docker-compose.yml`, and the
two UI sign-in values described under [Authentication](#authentication):

```dotenv
POSTGRES_PASSWORD=<long-random-postgres-password>
MINIO_ROOT_USER=<long-random-minio-user>
MINIO_ROOT_PASSWORD=<long-random-minio-password>
REDIS_PASSWORD=<long-random-redis-password>
SEAMTECH_AUTH_TOKEN=<long-random-shared-token>
SEAMTECH_UI_PASSWORD=<the password the operator types at /login>
SEAMTECH_SESSION_SECRET=<long-random-cookie-signing-key>
SEAMTECH_S3_BUCKET=seamtech-documents
SEAMTECH_ROOT_PATHS=/app/data/DesignFiles
```

Generate the random ones in PowerShell:

```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 48 | ForEach-Object {[char]$_})
```

`SEAMTECH_ROOT_PATHS` is a path inside the container, not a Windows path. Put the
files to index below `data\DesignFiles`; Compose mounts that host directory as
`/app/data/DesignFiles`.

Start the complete stack with one command:

```powershell
docker compose up -d --build
```

The `web` service waits for healthy PostgreSQL, MinIO, and Redis. The `frontend`
service waits for a healthy `web` service. Watch the real container health state
rather than assuming that a successful `up -d` means the application is ready:

```powershell
docker compose ps
Invoke-WebRequest http://127.0.0.1:3000/api/health | Select-Object -Expand Content
```

The frontend health response should be HTTP 200 and report the backend health
payload. If a service is unhealthy, inspect its logs before restarting it:

```powershell
docker compose logs --tail 100 web frontend postgres minio redis
docker compose ps
```

## Authentication

**Decision (audit issue #5): Option B — the UI has real sign-in.** This is
stated explicitly rather than left implicit, because the audit found the
frontend forwarding `SEAMTECH_AUTH_TOKEN` to the backend for *anyone* who could
reach it, with no login screen at all.

How it works:

- `/login` collects a single shared operator password (`SEAMTECH_UI_PASSWORD`)
  and `POST`s it to `/api/auth/login`.
- On success the frontend sets an **httpOnly, SameSite=Lax** session cookie
  (`seamtech_session`) containing an HMAC-SHA256-signed token
  (`v1.<issuedAt>.<expiresAt>.<signature>`), signed with
  `SEAMTECH_SESSION_SECRET`. The cookie is unreadable from JavaScript, so an
  XSS bug cannot exfiltrate it. Sessions are stateless — no table, no Redis.
- Every route under `frontend/app/api/*` calls `requireAuth()` first and returns
  `401` without a valid session, so the backend token is never forwarded on an
  unauthenticated caller's behalf.
- `app/page.tsx` is a server component that redirects to `/login` when the
  session is missing, so the search screen is never rendered either.
- Sessions last `SEAMTECH_SESSION_HOURS` (default 12). Any `401` received by the
  UI sends the operator back to `/login` with a `?next=` return path.
- Failed sign-ins are throttled in memory: 5 attempts per 5 minutes, then a
  5-minute lockout. This is per-process, not distributed — adequate for one
  operator on one container, and not a substitute for network-level controls.

Two routes are intentionally **not** gated:

| Route | Why |
| --- | --- |
| `/api/auth/*` | `login` creates the session; `logout` must work when already signed out; `session` only reports whether the caller is authenticated. |
| `/api/health` | Target of the compose healthcheck and the CI probe. Unauthenticated callers get a static liveness payload only — **no** archive counts and **no** backend call — so the probe works without a session and leaks nothing. |

Required environment:

| Variable | Required | Purpose |
| --- | --- | --- |
| `SEAMTECH_UI_PASSWORD` | yes | The shared password typed at `/login`. Unset ⇒ login returns `503` and nobody gets in (fails closed). |
| `SEAMTECH_SESSION_SECRET` | yes | Signs the session cookie. Unset ⇒ a random per-process secret is used and sessions die on restart; a warning is logged. |
| `SEAMTECH_SESSION_HOURS` | no | Session lifetime, default `12`. |
| `SEAMTECH_SECURE_COOKIES` | no | Force the `Secure` cookie flag. Otherwise it is set automatically when `SEAMTECH_BEHIND_TLS_PROXY=true` or the request arrived over HTTPS. |

`docker-compose.yml` requires `SEAMTECH_UI_PASSWORD` and
`SEAMTECH_SESSION_SECRET` with `${VAR:?...}`, so `docker compose up` refuses to
start until they are set. Rotating `SEAMTECH_SESSION_SECRET` signs everybody out
immediately; rotating `SEAMTECH_UI_PASSWORD` takes effect on the next sign-in.

**Why not Option A (localhost-only)?** The compose file already binds every
published port to `127.0.0.1`, so Option A's binding change was already in
place, and `SEAMTECH_HOST=0.0.0.0` inside the `web` container is *required* for
the `frontend` container to reach it over the Docker network — removing it
breaks the stack rather than hardening it. That would have left only a README
warning, while this document and [TLS.md](TLS.md) both describe exposing the app
to the office LAN and to a VPS behind Caddy. Sign-in is the control that makes
those deployments safe; a warning does not.

## Office LAN access

The default deployment is safe for a local-only installation: all published
ports are bound to loopback. For office access, put a real TLS terminator (Caddy,
IIS, or Nginx) in front of `http://127.0.0.1:3000` and give users an HTTPS URL.
Forward the original `Host` and `X-Forwarded-Proto` headers. Install the issuing
CA certificate on office client machines when using an internal certificate.

The compose default for `SEAMTECH_BEHIND_TLS_PROXY` on the **web** service is
`true`: the documented office deployment is served through the TLS terminator
described above, and `web` must bind `0.0.0.0` so the frontend container can
reach it — the backend refuses a non-loopback bind + auth token +
`behind_tls_proxy=false`, so `false` would make the one-command deployment
fail to start. If you run the backend strictly localhost-only, set
`SEAMTECH_BEHIND_TLS_PROXY=false` in `.env`. Do not set it to `true` unless a
TLS proxy is actually in front of the app — doing so on an exposed, un-proxied
port lets forwarded-header spoofing bypass the app's own scheme checks.

(The frontend service defaults to `false` on purpose: its flag only decides
the session cookie's `Secure` flag, and the default loopback plain-HTTP
deployment must still let the browser keep the cookie. `auth.ts` additionally
honours `SEAMTECH_SECURE_COOKIES` and `X-Forwarded-Proto`, so a TLS
deployment gets Secure cookies either way. Set the variable explicitly to
give both services the same value.)

See [TLS.md](TLS.md) for Caddy, Nginx, and Cloudflare Tunnel examples. The
reverse proxy should publish only the frontend; keep PostgreSQL, Redis, MinIO,
and the backend on their loopback/container network endpoints.

## Updates and shutdown

Pull a reviewed revision, then rebuild the application images. Compose preserves
the named data volumes and restarts services in dependency order:

```powershell
git pull
docker compose pull
docker compose up -d --build
docker compose ps
```

To stop the stack without deleting data:

```powershell
docker compose down
```

Do **not** use `docker compose down -v` during normal maintenance. The `-v`
option deletes the PostgreSQL and MinIO named volumes.

## Backups and recovery

Back up PostgreSQL and MinIO together so the index and document objects remain
consistent. The repository includes PowerShell helpers for PostgreSQL backups;
run them from an elevated or appropriately permissioned PowerShell session and
store the resulting files off the deployment machine. Also back up the MinIO
volume or use an S3-compatible bucket with its own retention and backup policy.

Before an upgrade, record the running image and schema state:

```powershell
docker compose ps
docker compose exec postgres psql -U seamtech -d seamtech_search -c "SELECT version FROM schema_migrations ORDER BY version;"
```

Schema migrations run at API startup. Keep the old database backup until the
new stack has passed the frontend health check and a representative search.

## Troubleshooting checklist

- **Compose refuses to parse:** confirm every `:?` variable in `.env` is set,
  especially `REDIS_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, and
  `SEAMTECH_AUTH_TOKEN`; run `docker compose config --quiet`.
- **`web` is unhealthy:** inspect `docker compose logs web postgres minio redis`.
  Confirm the three dependency containers are healthy and that the credentials
  in `.env` match the first-created volumes.
- **Frontend is unhealthy:** inspect both `frontend` and `web` logs. The frontend
  health route calls the backend over the internal Compose network and requires
  the shared auth token.
- **Files are not found:** verify that host files are below `data\DesignFiles`
  and that `.env` uses the container path `/app/data/DesignFiles`.
- **Clients cannot connect:** verify the TLS proxy and its certificate, then
  check that Windows Firewall allows the proxy's HTTPS port. Do not change the
  Compose loopback bindings to expose internal services directly.

For a clean diagnostic report, collect `docker compose ps` and the relevant
service logs; do not include `.env` or credentials in bug reports.

## Privilèges PostgreSQL requis par la couche métier (Lot A, migrations 006-009)

La couche métier « fiches » (migrations 006-009) exige deux capacités du rôle PostgreSQL, par ordre de
préférence :

1. **Image préconfigurée (recommandé)** : `pgvector/pgvector:pg16` (docker-compose et CI la fournissent)
   avec le rôle superutilisateur du conteneur (`POSTGRES_USER`) — `CREATE EXTENSION` réussit alors
   directement.
2. **Extensions préinstallées par l'administrateur** (hébergement managé, rôle non superutilisateur) :
   demander à l'administrateur d'exécuter, une fois par base :
   `CREATE EXTENSION vector;` — obligatoire (les colonnes `vector(384)` de `chunk` et `documents`
   ne peuvent pas se dégrader) ;
   `CREATE EXTENSION pg_trgm;` — recommandé (tolérance aux fautes) ;
   `CREATE EXTENSION unaccent;` — recommandé (recherche insensible aux accents, migration 005).

Comportements constatés et testés (`tests/test_migrations_metier.py`) :

- **`vector` non disponible** (extension non « trusted » : refusée à un rôle non superutilisateur) :
  la migration 006 échoue FATALEMENT avec un message actionnable nommant les deux actions ci-dessus.
  C'est voulu : sans `vector`, il n'existe pas de schéma métier possible ; les migrations restent
  fatales au démarrage (choix durci du dépôt).
- **`pg_trgm` non installable** (extension « trusted », mais le privilège CREATE sur la base manque) :
  la migration 007 continue SANS les index trigrammes et journalise l'avertissement
  « pg_trgm indisponible … tolérance aux fautes désactivée » — la recherche plein-texte et les
  mots-clés restent opérationnelles.
- **`unaccent` non installable** : la configuration de recherche effective se résout au démarrage et
  se dégrade vers `simple` (choix de conception de `main`, migration 005) ; la colonne générée
  `chunk.tsv` reçoit la configuration EFFECTIVE injectée au moment de la migration — jamais un nom
  en dur. Vérification : `test_role_limite_migrations_passent_si_vector_preinstalle`.

Diagnostic : `/health` expose `schema_migrations`, `schema_metier_a_jour` et la présence effective
(`pg_extension`) de `vector` / `pg_trgm` / `unaccent` — c'est l'indicateur « installation à jour ».
