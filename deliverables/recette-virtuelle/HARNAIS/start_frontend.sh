#!/usr/bin/env bash
# Frontend de recette (Next.js en mode standalone — même mode que le conteneur).
set -euo pipefail
cd /home/user/rv/env/frontend-standalone
set -a; . /home/user/rv/recette.env; set +a
export NODE_ENV=production
exec node server.js
