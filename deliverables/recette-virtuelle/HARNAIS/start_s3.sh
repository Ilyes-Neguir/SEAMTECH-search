#!/usr/bin/env bash
# Démarrage du serveur de stockage objet de recette (voir s3rver_run.js).
set -euo pipefail
set -a; . /home/user/rv/recette.env; set +a
mkdir -p "${S3_DIRECTORY:-/home/user/rv/data/objets}"
exec node /home/user/rv/harnais/s3rver_run.js
