#!/usr/bin/env bash
# Suite e2e DU DÉPÔT (frontend/e2e) sur navigateur réel, en mode « live »
# (base PostgreSQL jetable semée par le script du dépôt).
set -uo pipefail
cd /home/user/SEAMTECH-search/frontend
set -a; . /home/user/rv/recette.env; set +a
export PATH=/home/user/rv/env/venv/bin:$PATH
export PYTHONPATH=/home/user/SEAMTECH-search
export LD_LIBRARY_PATH=/tmp/chromium-libs/lib:${LD_LIBRARY_PATH:-}
export RECETTE_CHROMIUM=/home/user/rv/env/chrome/chromium
export SEAMTECH_UI_PASSWORD=e2e-shared-password
export SEAMTECH_SESSION_SECRET=e2e-session-secret-not-for-production-use
export SEAMTECH_AUTH_TOKEN=e2e-service-token
# Le scénario « hors ligne » du dépôt (e2e/hors-ligne.spec.ts) est EXCLU de la suite
# par défaut (test.skip sans SEAMTECH_E2E_HORS_LIGNE=1) : il valide la fiche 7792-SO
# et ne restaure pas son état, ce qui casse 5 scénarios suivants du même run
# (constat A-05). Il se joue donc à part : E2E_HORS_LIGNE=1 bash … --grep "hors ligne".
if [ "${E2E_HORS_LIGNE:-}" = "1" ]; then export SEAMTECH_E2E_HORS_LIGNE=1; fi
unset SEAMTECH_PORT SEAMTECH_HOST SEAMTECH_DATABASE_URL SEAMTECH_TEST_DATABASE_URL
unset SEAMTECH_S3_ENDPOINT_URL SEAMTECH_S3_BUCKET SEAMTECH_S3_ACCESS_KEY SEAMTECH_S3_SECRET_KEY
# La base Redis de recette exige un mot de passe : on la donne à la pile e2e
# MAIS sur une AUTRE base logique (/1) pour que les files d'attente et les
# sessions de la suite ne touchent pas l'état de recette (/0).
unset SEAMTECH_STORAGE_BACKEND SEAMTECH_ROOT_PATHS SEAMTECH_REQUIRE_DURABLE_QUEUE
# La recette tourne avec SEAMTECH_WEB_WORKER_ENABLED=false (worker séparé). La pile e2e,
# elle, n'a PAS de worker : on rend la valeur par défaut du produit (true, worker en fil
# dans l'API) pour que les imports de la suite soient réellement traités.
unset SEAMTECH_WEB_WORKER_ENABLED
export SEAMTECH_REDIS_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:6379/1"
export SEAMTECH_E2E_DATABASE_URL="postgresql://seamtech:${PGPASSWORD:?mot de passe PostgreSQL de recette non défini (recette.env)}@127.0.0.1:5433/postgres"
echo "=== semis de la base e2e jetable (AVANT les serveurs) ==="
# La couche métier (fiches, comptes nominatifs) est PostgreSQL uniquement :
# l'IA doit donc tourner sur la base jetable SEMÉE, sinon /auth/connexion
# répond 503 (comptes indisponibles en mode SQLite) et les specs d'auth
# échouent pour une raison d'environnement, pas de produit.
SEMIS=$(python3 e2e/seed-live-pg.py)
SEAMTECH_E2E_LIVE_URL=$(echo "$SEMIS" | tail -1 | python3 -c "import json,sys; print(json.load(sys.stdin)['url'])")
export SEAMTECH_E2E_LIVE_URL
export SEAMTECH_DATABASE_URL="$SEAMTECH_E2E_LIVE_URL"
echo "base e2e : $(echo "$SEAMTECH_E2E_LIVE_URL" | sed 's|://[^@]*@|://***@|')"
echo "=== serveurs de la suite (API 8123 + frontend 3123) ==="
( cd /home/user/SEAMTECH-search && PATH=/home/user/rv/env/venv/bin:$PATH PYTHONPATH=/home/user/SEAMTECH-search \
    nohup /home/user/rv/env/venv/bin/python -m seamtech_search serve --config frontend/e2e/backend.config.json >/home/user/rv/logs/api-e2e.log 2>&1 & )
( cd /home/user/SEAMTECH-search/frontend && SEAMTECH_API_URL=http://127.0.0.1:8123 SEAMTECH_AUTH_TOKEN=e2e-service-token \
    SEAMTECH_UI_PASSWORD=e2e-shared-password SEAMTECH_SESSION_SECRET=e2e-session-secret-not-for-production-use \
    nohup ./node_modules/.bin/next start --port 3123 --hostname 127.0.0.1 >/home/user/rv/logs/frontend-e2e.log 2>&1 & )
for i in $(seq 1 60); do
  curl -s -o /dev/null http://127.0.0.1:8123/live && curl -s -o /dev/null http://127.0.0.1:3123/login && break
  sleep 1
done
curl -sS http://127.0.0.1:8123/health -H "X-SEAMTECH-TOKEN: e2e-service-token" | python3 -c "import json,sys; d=json.load(sys.stdin); print('santé API e2e :', {k: d.get(k) for k in ('base','storage_backend','redis_connected','nb_fiches')})"
echo "=== exécution de la suite (navigateur réel) ==="
exec ./node_modules/.bin/playwright test --config /home/user/rv/harnais/e2e.config.cjs "$@"
