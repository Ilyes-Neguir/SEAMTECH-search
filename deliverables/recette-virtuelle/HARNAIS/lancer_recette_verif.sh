#!/usr/bin/env bash
# Recette fonctionnelle OFFICIELLE du dépôt (scripts/recette_verif.py — 20 contrôles).
set -uo pipefail
cd /home/user/SEAMTECH-search
set -a; . /home/user/rv/recette.env; set +a
export PYTHONPATH=/home/user/SEAMTECH-search
export RECETTE_SOURCES=/home/user/rv/corpus/recette_sources_zip
export RECETTE_TRAVAIL=/home/user/rv/data/recette-lot
export RECETTE_UTILISATEUR=recette
export RECETTE_MOT_DE_PASSE="$(cat /home/user/rv/secrets/pw_recette.txt)"
export RECETTE_TIMEOUT_LOT=1800
export RECETTE_BASE=http://127.0.0.1:8000
exec /home/user/rv/env/venv/bin/python scripts/recette_verif.py
