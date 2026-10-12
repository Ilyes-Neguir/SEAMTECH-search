#!/usr/bin/env bash
# API SEAMTECH de recette — équivalent natif du service `web` de docker-compose.yml.
set -euo pipefail
cd /home/user/SEAMTECH-search
set -a; . /home/user/rv/recette.env; set +a
export LD_LIBRARY_PATH=/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib
exec /home/user/rv/env/venv/bin/python -m seamtech_search serve --config config/config.json
