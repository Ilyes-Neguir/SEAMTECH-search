#!/usr/bin/env bash
# Worker SEAMTECH de recette — équivalent natif du service `worker` de docker-compose.yml.
set -euo pipefail
cd /home/user/SEAMTECH-search
set -a; . /home/user/rv/recette.env; set +a
exec /home/user/rv/env/venv/bin/python -m seamtech_search.worker_service
