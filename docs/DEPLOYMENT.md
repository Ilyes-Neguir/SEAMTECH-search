# SEAMTECH Search office deployment

This is the supported deployment path for an office machine running Docker Desktop
(Windows) — or a workshop server/VM. The stack is SIX compose services: `postgres`,
`redis`, `minio`, `web` (API), `worker` (separate process: imports survive a
restart) and `frontend` (Next.js); `docker compose ps` must show all six as
`running (healthy)`. For object storage, **MinIO is the supported provider for this
release** (the compose file ships it and CI proves the six-service stack);
S3-compatible endpoints such as Cloudflare R2 remain optional — do not reopen the
architecture while an archive is being imported. Whether this machine is a local
workshop server or a remote VPS is a design decision with consequences (a remote
host makes workshop access depend on the internet link): see
`docs/verite_terrain/DECISION_MATERIEL.md`. The compose file publishes the
application ports on `127.0.0.1`; expose the frontend to the office LAN only
through a TLS-terminating reverse proxy.

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
two UI sign-in values described under [Authentication](#authentication).

Two of these values are embedded in **connection URLs**: `docker-compose.yml`
builds `SEAMTECH_DATABASE_URL` from `POSTGRES_PASSWORD` and `SEAMTECH_REDIS_URL`
from `REDIS_PASSWORD`. Generate them **URL-safe** (`openssl rand -hex 24`, or
`openssl rand -base64 24 | tr '+/' '-_'`). A raw base64 secret can contain `/`,
which breaks the URL — in a Redis URL the `/` silently becomes the database
selector. The application refuses to start with an explicit message when a
password in either URL is not encoded (`AppConfig.validate_connection_urls`).

```dotenv
POSTGRES_PASSWORD=<long-random-postgres-password>
MINIO_ROOT_USER=<long-random-minio-admin-user>
MINIO_ROOT_PASSWORD=<long-random-minio-admin-password>
REDIS_PASSWORD=<long-random-redis-password>
SEAMTECH_AUTH_TOKEN=<long-random-shared-token>
SEAMTECH_UI_PASSWORD=<the password the operator types at /login>
SEAMTECH_SESSION_SECRET=<long-random-cookie-signing-key>
SEAMTECH_S3_BUCKET=seamtech-documents
SEAMTECH_ROOT_PATHS=/app/data/DesignFiles
# Dedicated APPLICATION identity: restricted to SEAMTECH_S3_BUCKET only.
SEAMTECH_S3_ACCESS_KEY=seamtech-app
SEAMTECH_S3_SECRET_KEY=<long-random-app-secret>
# Dedicated BACKUP identity: restricted to SEAMTECH_BACKUP_BUCKET only.
SEAMTECH_BACKUP_ACCESS_KEY=seamtech-sauvegarde
SEAMTECH_BACKUP_SECRET_KEY=<long-random-backup-secret>
SEAMTECH_BACKUP_BUCKET=seamtech-backups
```

### Storage identities: administrator vs application vs backup

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` are **provisioning credentials**.
They are given to the MinIO service and to
`scripts/provisionner_stockage.sh`, and to nothing else: the `web` and `worker`
services receive the dedicated application identity above, which can read and
write objects in `SEAMTECH_S3_BUCKET` only — no other bucket, no
administration operation, no permission. `docker-compose.yml` refuses to start
if `SEAMTECH_S3_ACCESS_KEY` / `SEAMTECH_S3_SECRET_KEY` are missing
(`${VAR:?message}`), so there is no silent fallback to the administrator. The
backup tool (`python -m seamtech_search.sauvegarde`) uses the separate backup
identity when `SEAMTECH_BACKUP_*` are set, so a compromised application cannot
read or rewrite the off-site backups; without them it falls back to the
application identity and the upload fails loudly (`AccessDenied`) if that
identity has no rights on the backup bucket.

Provision the bucket, the versioning and both restricted identities **before**
the first `docker compose up` — and, more importantly, **before any normal use of
the application** (a first real import is not the moment to discover that the
bucket does not exist). Then VERIFY, not just provision: with the application
identity, store one object and read its bytes back; `/health` must report
`s3_credentials: "dedie"`. Re-run after rotating any of these secrets:

```powershell
docker compose up -d minio          # MinIO must be running first
bash scripts/provisionner_stockage.sh
```

The script is idempotent and repeatable; it creates `SEAMTECH_S3_BUCKET` and
`SEAMTECH_BACKUP_BUCKET`, enables object versioning on both (the restricted
application identity cannot enable it itself), creates the two policies
(object read/write + bucket listing on their own bucket only), recreates the
two users with the secrets you supplied (that is the rotation procedure) and
checks anonymously that neither bucket is public. It refuses to run if the
application or backup credentials are the administrator ones, or if the two
identities are the same. It never prints a secret.

`GET /health` reports `s3_credentials` as `dedie`, `root_like` or `absent`:
`dedie` is the expected value, `root_like` means the administrator credentials
were reused or the demonstration `minioadmin` values are still in place.

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

The `web` service waits for healthy PostgreSQL, MinIO, and Redis. The `worker`
service (which actually executes imports) waits for the same three. The
`frontend` service waits for a healthy `web` service. Watch the real container
health state rather than assuming that a successful `up -d` means the
application is ready:

```powershell
docker compose ps
Invoke-WebRequest http://127.0.0.1:3000/api/health | Select-Object -Expand Content
```

The frontend health response should be HTTP 200 and report the backend health
payload. If a service is unhealthy, inspect its logs before restarting it:

```powershell
docker compose logs --tail 100 web worker frontend postgres minio redis
docker compose ps
```

### The worker service (why imports survive a restart)

`web` accepts imports but does **not** execute them: the environment sets
`SEAMTECH_WEB_WORKER_ENABLED=false`. Execution belongs to the `worker` service
(`python -m seamtech_search.worker_service`), which consumes the durable Redis
queue. Three consequences the operator should know:

* restarting or upgrading `web` (including `docker compose up -d --build web`)
  does not interrupt a running import;
* if Redis is unreachable, `SEAMTECH_REQUIRE_DURABLE_QUEUE=true` makes the API
  answer **503 and refuse the import** instead of accepting it and losing it —
  an honest refusal, visible in the UI;
* the `worker` healthcheck runs `--verifier`, which exits non-zero when Redis
  cannot be reached and prints the queue depth, the jobs per status, and the
  live workers. A worker that cannot work is therefore *unhealthy*, not green.
* `SEAMTECH_STORAGE_VERIFY_REREAD` (default `true`, passed to both `web` and
  `worker` by `docker-compose.yml`) controls whether an upload is verified by
  **reading the stored bytes back** (`GET` + SHA-256) before the local draft is
  deleted. Setting it to `false` does **not** speed anything up safely: with no
  read-back, every artifact is only "metadata echoed by us", which is not proof,
  so **the worker refuses to purge the local copy** and says why
  (`intégrité non prouvée (…) — copie locale conservée`). Leave it `true` unless
  you have a provider-side whole-object checksum and know that is what you rely
  on.

**Downloads from a workshop PC (do not expose the internal endpoint).** By
default the API serves report/download/`open` bytes itself, through the
authenticated endpoint the browser already talks to. That is deliberate: a
presigned URL is signed for the S3 endpoint the application uses, which in this
stack is `http://minio:9000` — a name that only exists inside the compose
network. Redirecting a workshop PC there fails with *name resolution* (this was
found by the two-container Compose test). If you *want* the cheaper presigned
redirect, expose MinIO on the LAN (TLS terminator in front) and declare the
endpoint browsers can reach:

```bash
SEAMTECH_S3_PUBLIC_ENDPOINT_URL=https://minio.atelier.local
```

Leave it empty and every download goes through the app; credentials and the
internal endpoint then never reach browser code, and downloads work from any
workshop PC.

**Directories the container user must be able to write to (real deployment
requirement).** `./data` and `./logs` are bind-mounted into `web` and `worker`,
which run as the non-root `seamtech` user of the image. If Docker creates those
directories for you, they belong to `root` and the application cannot write the
uploaded drafts, the generated reports or the quarantine — imports then fail
with a permission error that looks like a code bug. Create them and give the
container user ownership, once, before the first start:

```bash
mkdir -p data logs
uid=$(docker compose exec -T web id -u)   # once `web` is up, or read it from the image
gid=$(docker compose exec -T web id -g)
sudo chown -R "$uid:$gid" data logs
```

The CI `integration` job does exactly this before running the two-container
test, and asserts the container can really write into `/app/data` and
`/app/logs`.

Useful commands:

```powershell
# Is the worker able to work? (queue depth, jobs per status, live workers)
docker compose exec -T worker python -m seamtech_search.worker_service --verifier

# Failed imports, with the recorded reason (never a silent failure)
docker compose exec -T postgres psql -U seamtech -d seamtech_search -c `
  "SELECT id, status, attempts, claimed_by, failure_reason FROM import_jobs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 20;"

# Queue depth as the API reports it
docker compose exec -T web python -c "import json,os,urllib.request;r=urllib.request.Request('http://127.0.0.1:8000/health',headers={'X-SEAMTECH-TOKEN':os.environ['SEAMTECH_AUTH_TOKEN']});print(json.dumps(json.load(urllib.request.urlopen(r))['queue'],indent=2))"
```

Full contract, limits and what is *not* guaranteed:
`docs/FILE_DURABLE.md`.

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

## Optimistic locking between workstations (mandatory by default)

`SEAMTECH_REQUIRE_REVISION=true` (compose default, and the safe default of
`AppConfig`) makes the revision a **precondition**:

* a correction without `revision` → **428**, nothing written (a failed revision
  read on a workstation must never silently disable the anti-overwrite guard);
* a correction with a stale revision → **409**, the colleague's value is kept;
* a DECISION (valider / rejeter / rouvrir / batch) with a stale revision → **409**,
  nothing written to the audit journal: a worker can never approve a value they
  did not re-read;
* `GET /fiches/{code}/etat` returns status + revision + fields **from one
  snapshot**, so the revision on screen always matches the values on screen.

Setting it to `false` is only for an identified legacy script; the workshop UI
always sends the revision it displays, so it needs no relaxation.

## Offline verification before the workshop (RG14_EXCEPTION, tooling only)

The workshop server has no Internet access. Reading the configuration does not
prove that: the automated check **executes** the essential workflows with every
outbound Python connection blocked and reports what happened.

**Scope of that script (do not overstate it).** It patches Python `socket`
calls inside ITS OWN process: it covers neither the browser, nor Next.js, nor a
separate worker process, nor native libraries (part of libpq) and subprocesses.
A green run means "this Python process reached nothing external", nothing more.
The full-stack proof is separate: the CI job `hors-ligne-reel` runs the real
stack (web + **separate worker** + PostgreSQL + Redis + local MinIO) with the
application account's egress actually REJECTED by iptables and drives the
browser through import, search, PDF preview, original + generated-report
downloads and validation; it then asserts the reject counter is still 0 (no
hidden external dependency was even attempted) and requires the negative
control (an outbound request must fail) to hold first. Neither replaces the
workshop acceptance (real workstations, cable unplugged).

```bash
# 1. What is provisioned? (no blocking) — says REQUIRED vs OPTIONAL per capability
python scripts/verifier_hors_ligne.py --inventaire

# 2. Run the essential workflows with the outside world blocked
export SEAMTECH_DATABASE_URL=postgresql://seamtech:...@127.0.0.1:5432/seamtech_search
export SEAMTECH_REDIS_URL=redis://127.0.0.1:6379/0
python scripts/verifier_hors_ligne.py --executer --rapport /tmp/hors-ligne.json

# 3. Diagnostic only: do NOT block, but LIST every external connection attempted
python scripts/verifier_hors_ligne.py --executer --autoriser-externe
```

What it exercises, against the real local stack (PostgreSQL + Redis, plus MinIO
when `SEAMTECH_S3_ENDPOINT_URL` is set): application start-up, nominative
sign-in, a real folder import with file accounting, report download, dossier
deposit (fiche + pieces), human correction with the optimistic lock (a stale
correction is refused), search visibility of the deposited fiche, PDF preview
and original download, and `/open`. Zero external connections are expected; if
any is attempted, the target host:port is **named** in the report.

Capabilities that are absent are reported as OPTIONAL with their exact
consequence (OCR tier 3 without `tesseract -l fra`, image rendering without
`pdftoppm`, vector search without the e5 models, container images without
Docker) — never silently worked around. Provision everything the workshop needs
**before** the network is cut:

```bash
pnpm install --frozen-lockfile && pnpm build     # frontend
python -m seamtech_search.ml.telecharger         # e5-small ONNX weights (operator command)
bash scripts/construire_image_minio.sh           # MinIO image, if built locally
```

Two limits are stated in the tool itself and must not be glossed over:
the guard intercepts **Python** connections (urllib, boto3, redis-py…), not
C-library ones (libpq), so `--inventaire` additionally checks that the
configured database/Redis/S3 endpoints are loopback or private; and this
automated check **does not replace** the workshop acceptance (real server,
physically unplugged network, three real workstations, human judgement).

The tool imports `socket`/`urllib` on purpose — its job is to intercept and
block outbound connections. That is the documented `RG14_EXCEPTION` (see
`tests/test_garde_fous_preparation.py`); it is never imported by the service.

## Updates and shutdown

Pull a reviewed revision, then rebuild the application images. Compose preserves
the named data volumes and restarts services in dependency order. The `worker`
service is rebuilt and restarted too; an import in progress is finished by the
old container or, if it is killed, resumed by the new one once its claim
expires (≤ 5 min by default):

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
  especially `REDIS_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`,
  `SEAMTECH_S3_ACCESS_KEY`, `SEAMTECH_S3_SECRET_KEY`, and `SEAMTECH_AUTH_TOKEN`;
  run `docker compose config --quiet`.
- **Uploads fail with `AccessDenied` / an empty `seamtech-documents`:** the
  buckets and restricted identities have not been provisioned in this
  environment. Run `bash scripts/provisionner_stockage.sh` (see
  [Storage identities](#storage-identities-administrator-vs-application-vs-backup)),
  then `docker compose up -d --build web worker`.
- **`/health` reports `s3_credentials: root_like`:** the administrator
  credentials were reused for the application. Provision the dedicated
  identity and put it in `.env`; do not "fix" this by granting the
  administrator credentials to `web`/`worker`.
- **Imports stay `pending` / the queue grows:** the `worker` service is the only
  consumer. Check `docker compose ps worker` and
  `docker compose exec -T worker python -m seamtech_search.worker_service --verifier`.
  Exit code 2 means Redis is unreachable from the worker; fix Redis before
  relaunching imports.
- **An import is refused with HTTP 503:** this is deliberate. `web` requires a
  durable queue (`SEAMTECH_REQUIRE_DURABLE_QUEUE=true`) and Redis is down, so
  nothing was accepted. Bring Redis back and submit again — no partial job was
  created.
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
