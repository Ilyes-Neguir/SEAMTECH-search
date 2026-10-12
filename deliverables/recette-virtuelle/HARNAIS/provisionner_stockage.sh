#!/usr/bin/env bash
set -euo pipefail
set -a; . /home/user/rv/recette.env; set +a
exec /home/user/rv/env/venv/bin/python /home/user/rv/harnais/provisionner_stockage.py
