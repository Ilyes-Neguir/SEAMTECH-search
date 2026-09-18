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
secrets; add `REDIS_PASSWORD`, which is required by `docker-compose.yml`:

```dotenv
POSTGRES_PASSWORD=<long-random-postgres-password>
MINIO_ROOT_USER=<long-random-minio-user>
MINIO_ROOT_PASSWORD=<long-random-minio-password>
REDIS_PASSWORD=<long-random-redis-password>
SEAMTECH_AUTH_TOKEN=<long-random-shared-token>
SEAMTECH_S3_BUCKET=seamtech-documents
SEAMTECH_ROOT_PATHS=/app/data/DesignFiles
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

## Office LAN access

The default deployment is safe for a local-only installation: all published
ports are bound to loopback. For office access, put a real TLS terminator (Caddy,
IIS, or Nginx) in front of `http://127.0.0.1:3000` and give users an HTTPS URL.
Forward the original `Host` and `X-Forwarded-Proto` headers. Install the issuing
CA certificate on office client machines when using an internal certificate.

The compose default for `SEAMTECH_BEHIND_TLS_PROXY` is `true` because the
 documented office deployment has this TLS boundary. Do not use that setting as
a reason to expose port 8000 directly. If this machine is strictly localhost-only,
set `SEAMTECH_BEHIND_TLS_PROXY=false` in `.env`.

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
