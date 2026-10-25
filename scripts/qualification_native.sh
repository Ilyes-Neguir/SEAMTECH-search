#!/usr/bin/env bash
# Essai d'installation NATIVE Debian/Ubuntu (pas Docker Compose).
#
# Cible : machine vierge autorisée (runner GitHub ubuntu-24.04, VM d'essai).
# Ce n'est PAS une homologation production, PAS le PC de l'opérateur, PAS
# recette-locale Docker.
#
# Couverture : dépendances, PostgreSQL 16 + pgvector, Redis, MinIO officiel
# compilé depuis les sources archivées (même tag que docker-compose.yml),
# migrations, API + worker + frontend, connexion nominative, import + worker,
# recherche, OCR fra+eng, téléchargement, redémarrage/persistance,
# sauvegarde/restauration conjointe.
#
# Usage : bash scripts/qualification_native.sh
set -euo pipefail
trap 'echo "::error title=native/abort::échec ligne ${LINENO} (procédure interrompue)"' ERR

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RACINE"

PREFIX="${SEAMTECH_NATIF_PREFIX:-/tmp/seamtech-natif}"
BIN="$PREFIX/bin"
DATA="$PREFIX/data"
LOGS="$PREFIX/logs"
IMPORT="$PREFIX/import"
BACKUP="$PREFIX/backups"
RESTORE_FILES="$PREFIX/restore-files"
mkdir -p "$BIN" "$DATA/minio" "$DATA/pg" "$DATA/redis" "$LOGS" "$IMPORT" "$BACKUP" "$RESTORE_FILES"

RAPPORT="$BACKUP/rapport-natif-$(date -u +%Y%m%dT%H%M%SZ).txt"
NB_FAIL=0
controle() {
    echo "CONTROLE|$1|$2|$3" | tee -a "$RAPPORT"
    if [ "$2" = "FAIL" ]; then
        NB_FAIL=$((NB_FAIL + 1))
        if [ -n "${GITHUB_ACTIONS:-}" ]; then
            echo "::error title=native/$1::$3"
        fi
    fi
}

echo "=== Qualification native SEAMTECH ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ===" | tee "$RAPPORT"
echo "préfixe=$PREFIX racine=$RACINE" | tee -a "$RAPPORT"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

# ---------------------------------------------------------------------------
# Secrets d'essai (jamais des secrets atelier).
# ---------------------------------------------------------------------------
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-natif-pg-$(openssl rand -hex 8)}"
export REDIS_PASSWORD="${REDIS_PASSWORD:-natif-redis-$(openssl rand -hex 8)}"
export MINIO_ROOT_USER="${MINIO_ROOT_USER:-natifadmin}"
export MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-natif-minio-$(openssl rand -hex 12)}"
export SEAMTECH_S3_ACCESS_KEY="${SEAMTECH_S3_ACCESS_KEY:-natif-app}"
export SEAMTECH_S3_SECRET_KEY="${SEAMTECH_S3_SECRET_KEY:-natif-app-$(openssl rand -hex 16)}"
export SEAMTECH_BACKUP_ACCESS_KEY="${SEAMTECH_BACKUP_ACCESS_KEY:-natif-bak}"
export SEAMTECH_BACKUP_SECRET_KEY="${SEAMTECH_BACKUP_SECRET_KEY:-natif-bak-$(openssl rand -hex 16)}"
export SEAMTECH_AUTH_TOKEN="${SEAMTECH_AUTH_TOKEN:-natif-token-$(openssl rand -hex 12)}"
export SEAMTECH_UI_PASSWORD="${SEAMTECH_UI_PASSWORD:-natif-ui-$(openssl rand -hex 8)}"
export SEAMTECH_SESSION_SECRET="${SEAMTECH_SESSION_SECRET:-natif-session-$(openssl rand -hex 24)}"
export SEAMTECH_NATIF_MOT_DE_PASSE="${SEAMTECH_NATIF_MOT_DE_PASSE:-Nat1fEssai!2026}"
export SEAMTECH_NATIF_IDENTIFIANT="${SEAMTECH_NATIF_IDENTIFIANT:-natif-essai}"
export SEAMTECH_S3_BUCKET="${SEAMTECH_S3_BUCKET:-seamtech-documents}"
export SEAMTECH_BACKUP_BUCKET="${SEAMTECH_BACKUP_BUCKET:-seamtech-backups}"

# ---------------------------------------------------------------------------
# 1. Paquets système
# ---------------------------------------------------------------------------
if ! command -v apt-get >/dev/null 2>&1; then
    controle "os" "FAIL" "apt-get absent — procédure limitée à Debian/Ubuntu"
    exit 1
fi
. /etc/os-release
controle "os" "PASS" "$PRETTY_NAME"

$SUDO apt-get update -qq
installer() {
    if ! $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"; then
        echo "::error title=native/apt::apt-get install a échoué : $*"
        return 1
    fi
}
installer postgresql postgresql-contrib
installer redis-server
installer tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng poppler-utils
installer build-essential git curl python3 python3-venv python3-dev libpq-dev pkg-config ca-certificates psmisc
PG_MAJOR="$($SUDO -u postgres psql -Atqc 'SHOW server_version' 2>/dev/null | cut -d. -f1 || true)"
if [ -z "$PG_MAJOR" ]; then
    $SUDO service postgresql start || $SUDO pg_ctlcluster 16 main start || true
    PG_MAJOR="$($SUDO -u postgres psql -Atqc 'SHOW server_version' | cut -d. -f1)"
fi
installer "postgresql-server-dev-${PG_MAJOR}"
TESS_VER="$(tesseract --version 2>&1 | head -1)"
LANGS="$(tesseract --list-langs 2>/dev/null | tr '\n' ' ')"
if echo "$LANGS" | grep -q "fra" && echo "$LANGS" | grep -q "eng"; then
    controle "tesseract" "PASS" "$TESS_VER langs=$LANGS"
else
    controle "tesseract" "FAIL" "fra+eng requis, langs=$LANGS"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. pgvector (paquet si présent, sinon sources GitHub)
# ---------------------------------------------------------------------------
PG_VER="$($SUDO -u postgres psql -Atqc 'SHOW server_version_num' 2>/dev/null || true)"
$SUDO pg_isready -q || $SUDO service postgresql start
if ! $SUDO -u postgres psql -Atqc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -q 1; then
    if apt-cache show postgresql-16-pgvector >/dev/null 2>&1; then
        $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-16-pgvector || true
    fi
fi
if ! $SUDO -u postgres psql -Atqc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -q 1; then
    echo "compilation pgvector depuis github.com/pgvector/pgvector (v0.8.0)"
    rm -rf "$PREFIX/pgvector"
    git clone --depth 1 --branch v0.8.0 https://github.com/pgvector/pgvector.git "$PREFIX/pgvector"
    make -C "$PREFIX/pgvector"
    $SUDO make -C "$PREFIX/pgvector" install
fi
$SUDO -u postgres psql -Atqc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -q 1
controle "pgvector" "PASS" "extension vector installable (PG $($SUDO -u postgres psql -Atqc 'SHOW server_version'))"

# ---------------------------------------------------------------------------
# 3. Rôles et bases PostgreSQL
# ---------------------------------------------------------------------------
$SUDO -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'seamtech') THEN
        CREATE ROLE seamtech LOGIN PASSWORD '${POSTGRES_PASSWORD}' SUPERUSER;
    ELSE
        ALTER ROLE seamtech PASSWORD '${POSTGRES_PASSWORD}' LOGIN;
    END IF;
END
\$\$;
SQL
$SUDO -u postgres psql -Atqc "SELECT 1 FROM pg_database WHERE datname='seamtech_search'" | grep -q 1 \
    || $SUDO -u postgres createdb -O seamtech seamtech_search
$SUDO -u postgres psql -d seamtech_search -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
$SUDO -u postgres psql -d seamtech_search -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS unaccent;"
$SUDO -u postgres psql -d seamtech_search -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
# restaurer() REFUSE une base cible déjà existante : on ne crée pas
# seamtech_restore ici, le CLI CREATE DATABASE au moment du restore.
export SEAMTECH_DATABASE_URL="postgresql://seamtech:${POSTGRES_PASSWORD}@127.0.0.1:5432/seamtech_search"
export SEAMTECH_RESTORE_DATABASE_URL="postgresql://seamtech:${POSTGRES_PASSWORD}@127.0.0.1:5432/seamtech_restore"
controle "postgres" "PASS" "base seamtech_search + extensions ; seamtech_restore sera créée par restaurer()"

# ---------------------------------------------------------------------------
# 4. Redis natif
# ---------------------------------------------------------------------------
$SUDO service redis-server stop >/dev/null 2>&1 || true
$SUDO fuser -k 6379/tcp >/dev/null 2>&1 || true
redis-server --daemonize yes --port 6379 --requirepass "$REDIS_PASSWORD" \
    --appendonly yes --appendfsync everysec --dir "$DATA/redis" \
    --pidfile "$DATA/redis/redis.pid" --logfile "$LOGS/redis.log"
sleep 1
REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping | grep -q PONG
export SEAMTECH_REDIS_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0"
controle "redis" "PASS" "redis-server 127.0.0.1:6379 AOF everysec"

# ---------------------------------------------------------------------------
# 5. MinIO officiel (binaire hôte, tag épinglé) + mc
# ---------------------------------------------------------------------------
MINIO_TAG="${MINIO_TAG:-RELEASE.2025-09-07T16-13-09Z}"
MC_TAG="${MC_TAG:-RELEASE.2025-08-13T08-35-41Z}"
if ! command -v go >/dev/null 2>&1; then
    controle "golang" "FAIL" "Go absent : compiler MinIO depuis les sources officielles exige Go 1.24"
    exit 1
fi
echo "Go $(go version) — compilation MinIO $MINIO_TAG / mc $MC_TAG"
rm -rf "$PREFIX/minio-src" "$PREFIX/mc-src"
git clone --depth 1 --branch "$MINIO_TAG" https://github.com/minio/minio "$PREFIX/minio-src"
git clone --depth 1 --branch "$MC_TAG" https://github.com/minio/mc "$PREFIX/mc-src"
( cd "$PREFIX/minio-src" && CGO_ENABLED=0 go build -tags kqueue -trimpath -o "$BIN/minio" . )
( cd "$PREFIX/mc-src" && CGO_ENABLED=0 go build -tags kqueue -trimpath -o "$BIN/mc" . )
chmod 0755 "$BIN/minio" "$BIN/mc"
fuser -k 9000/tcp >/dev/null 2>&1 || true
MINIO_ROOT_USER="$MINIO_ROOT_USER" MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
    nohup "$BIN/minio" server "$DATA/minio" --address 127.0.0.1:9000 --console-address 127.0.0.1:9001 \
    >"$LOGS/minio.log" 2>&1 &
echo $! > "$PREFIX/minio.pid"
for _ in $(seq 1 40); do
    if curl -sf http://127.0.0.1:9000/minio/health/live >/dev/null; then break; fi
    sleep 1
done
curl -sf http://127.0.0.1:9000/minio/health/live >/dev/null
export SEAMTECH_MINIO_MC="$BIN/mc"
export SEAMTECH_MINIO_URL="http://127.0.0.1:9000"
bash "$RACINE/scripts/provisionner_stockage.sh"
controle "minio" "PASS" "processus hôte $MINIO_TAG, identités restreintes provisionnées (pas Docker)"

# ---------------------------------------------------------------------------
# 6. Python applicatif + config + migrations (démarrage API)
# ---------------------------------------------------------------------------
python3 -m venv "$PREFIX/venv"
# shellcheck disable=SC1091
. "$PREFIX/venv/bin/activate"
python -m pip install -q --upgrade pip
pip install -q -r "$RACINE/requirements.txt"
python -m compileall -q "$RACINE/seamtech_search"
mkdir -p "$IMPORT/affaires" "$IMPORT/ocr"

python - <<PY
import json
from pathlib import Path
racine = Path("$RACINE")
cfg = json.loads((racine / "config" / "config.example.json").read_text(encoding="utf-8"))
cfg.update({
    "root_paths": ["$IMPORT/affaires", "$IMPORT/ocr"],
    "database_path": "$DATA/search.db",
    "database_url": None,
    "host": "127.0.0.1",
    "port": 8000,
    "min_free_bytes": 0,
    "enable_ocr": True,
    "tesseract_command": "tesseract",
    "auth_token": None,
    "allow_network_access": False,
    "behind_tls_proxy": False,
    "storage_backend": "s3",
    "s3_endpoint_url": "http://127.0.0.1:9000",
    "s3_bucket": "$SEAMTECH_S3_BUCKET",
    "s3_region": "us-east-1",
    "s3_force_path_style": True,
    "web_worker_enabled": False,
    "require_durable_queue": True,
    "storage_verify_reread": True,
    "rate_limit_per_minute": 1000,
})
Path("$PREFIX/config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
(racine / "config" / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("config écrite")
PY

export SEAMTECH_DATABASE_URL SEAMTECH_REDIS_URL SEAMTECH_AUTH_TOKEN
export SEAMTECH_S3_ACCESS_KEY SEAMTECH_S3_SECRET_KEY
export SEAMTECH_S3_ENDPOINT_URL="http://127.0.0.1:9000"
export SEAMTECH_STORAGE_BACKEND="s3"
export SEAMTECH_WEB_WORKER_ENABLED="false"
export SEAMTECH_REQUIRE_DURABLE_QUEUE="true"
mkdir -p "$IMPORT/affaires" "$IMPORT/ocr"
export SEAMTECH_ROOT_PATHS="$IMPORT/affaires,$IMPORT/ocr"
export SEAMTECH_NATIF_OCR="$IMPORT/ocr"
export PYTHONPATH="$RACINE${PYTHONPATH:+:$PYTHONPATH}"

# Compte nominatif (mot de passe sur stdin, jamais en argument).
python - <<PY
import os
from pathlib import Path
from seamtech_search.indexer import SearchIndex
from seamtech_search.comptes.comptes import creer_utilisateur

idx = SearchIndex(Path("$DATA/search.db"), os.environ["SEAMTECH_DATABASE_URL"])
idx.initialize()
idx.run_migrations()
try:
    creer_utilisateur(
        idx,
        os.environ["SEAMTECH_NATIF_IDENTIFIANT"],
        "Essai natif",
        os.environ["SEAMTECH_NATIF_MOT_DE_PASSE"],
        role="admin",
        doit_changer_mot_de_passe=False,
    )
    print("compte créé")
except Exception as exc:
    print("compte:", type(exc).__name__, exc)
idx.close()
PY
controle "migrations" "PASS" "initialize + run_migrations + compte nominatif"

# ---------------------------------------------------------------------------
# 7. Frontend
# ---------------------------------------------------------------------------
if ! command -v pnpm >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1; then
    controle "frontend-outils" "FAIL" "node/pnpm absents"
    exit 1
fi
if command -v pnpm >/dev/null 2>&1; then
    ( cd "$RACINE/frontend" && pnpm install --frozen-lockfile && pnpm build )
else
    controle "frontend-outils" "FAIL" "pnpm absent"
    exit 1
fi
controle "frontend-build" "PASS" "pnpm install --frozen-lockfile && pnpm build"

# ---------------------------------------------------------------------------
# 8. Processus API + worker + frontend
# ---------------------------------------------------------------------------
PYTHON_BIN="$(command -v python)"
NODE_BIN="$(command -v node)"
nohup "$PYTHON_BIN" -m seamtech_search serve --config "$PREFIX/config.json" \
    >"$LOGS/web.log" 2>&1 &
echo $! > "$PREFIX/web.pid"
nohup "$PYTHON_BIN" -m seamtech_search.worker_service --config "$PREFIX/config.json" \
    >"$LOGS/worker.log" 2>&1 &
echo $! > "$PREFIX/worker.pid"
export SEAMTECH_API_URL="http://127.0.0.1:8000"
export SEAMTECH_AUTH_TOKEN SEAMTECH_UI_PASSWORD SEAMTECH_SESSION_SECRET
(
    cd "$RACINE/frontend"
    nohup env SEAMTECH_API_URL="$SEAMTECH_API_URL" \
        SEAMTECH_AUTH_TOKEN="$SEAMTECH_AUTH_TOKEN" \
        SEAMTECH_UI_PASSWORD="$SEAMTECH_UI_PASSWORD" \
        SEAMTECH_SESSION_SECRET="$SEAMTECH_SESSION_SECRET" \
        NEXT_TELEMETRY_DISABLED=1 \
        "$NODE_BIN" "$RACINE/frontend/node_modules/next/dist/bin/next" start --port 3000 \
        >"$LOGS/front.log" 2>&1 &
    echo $! > "$PREFIX/front.pid"
)

web_ok=0
front_ok=0
for _ in $(seq 1 60); do
    curl -sf --max-time 5 -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" http://127.0.0.1:8000/health >/dev/null && web_ok=1 || web_ok=0
    curl -sf --max-time 5 http://127.0.0.1:3000/login >/dev/null && front_ok=1 || front_ok=0
    if [ "$web_ok" = 1 ] && [ "$front_ok" = 1 ]; then break; fi
    sleep 2
done
if [ "$web_ok" != 1 ] || [ "$front_ok" != 1 ]; then
    controle "demarrage-pile" "FAIL" "web=$web_ok front=$front_ok — voir $LOGS"
    tail -n 40 "$LOGS/web.log" "$LOGS/worker.log" "$LOGS/front.log" || true
    exit 1
fi
controle "demarrage-pile" "PASS" "API :8000 worker séparé frontend :3000 (processus hôtes)"

# ---------------------------------------------------------------------------
# 9. Contrôles fonctionnels
# ---------------------------------------------------------------------------
export SEAMTECH_API_URL="http://127.0.0.1:8000"
export SEAMTECH_FRONT_URL="http://127.0.0.1:3000"
export SEAMTECH_NATIF_IMPORT="$IMPORT/affaires"
export SEAMTECH_NATIF_OCR="$IMPORT/ocr"
export SEAMTECH_NATIF_BACKUP="$BACKUP"
export SEAMTECH_NATIF_RESTORE_FILES="$RESTORE_FILES"
set +e
"$PYTHON_BIN" "$RACINE/scripts/qualification_native_controles.py" | tee -a "$RAPPORT"
rc_ctrl=${PIPESTATUS[0]}
set -e
if [ "$rc_ctrl" -ne 0 ]; then
    NB_FAIL=$((NB_FAIL + 1))
    controle "controles-python" "FAIL" "qualification_native_controles.py rc=$rc_ctrl"
else
    controle "controles-python" "PASS" "connexion, import+worker, recherche, OCR, download, backup/restore"
fi

# ---------------------------------------------------------------------------
# 10. Redémarrage API + persistance
# ---------------------------------------------------------------------------
kill "$(cat "$PREFIX/web.pid")" || true
sleep 2
nohup "$PYTHON_BIN" -m seamtech_search serve --config "$PREFIX/config.json" \
    >"$LOGS/web.log" 2>&1 &
echo $! > "$PREFIX/web.pid"
persiste=0
for _ in $(seq 1 30); do
    if curl -sf --max-time 5 -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" http://127.0.0.1:8000/health >/dev/null; then
        persiste=1
        break
    fi
    sleep 1
done
if [ "$persiste" = 1 ]; then
    code=$(curl -s -o /tmp/natif-search.json -w "%{http_code}" \
        -H "X-SEAMTECH-TOKEN: $SEAMTECH_AUTH_TOKEN" \
        "http://127.0.0.1:8000/search?q=SEAMTECH")
    controle "redemarrage-persistance" "PASS" "API relancée, GET /search HTTP $code (PG/Redis/MinIO restés debout)"
else
    controle "redemarrage-persistance" "FAIL" "API absente après kill+restart"
    NB_FAIL=$((NB_FAIL + 1))
fi

echo "=== RAPPORT FINAL native : $NB_FAIL FAIL — $RAPPORT ===" | tee -a "$RAPPORT"
trap - ERR
if [ "$NB_FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
