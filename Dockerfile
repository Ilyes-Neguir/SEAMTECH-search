# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Réseau d'atelier parfois lent : ces valeurs restent surchargeables au build
# (`--build-arg PIP_DEFAULT_TIMEOUT=180`, par exemple) sans modifier le dépôt.
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=10
ARG APT_RETRIES=5

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Majeure du client PostgreSQL : ELLE DOIT ÊTRE CELLE DU SERVEUR
# (docker-compose.yml → `pgvector/pgvector:pg16`), pas « au moins » la sienne.
ARG PG_MAJEURE=16

WORKDIR /app

# Create non-root user
RUN groupadd -r seamtech && useradd -r -g seamtech -d /app -s /bin/false seamtech

# Dépendances SYSTÈME d'abord, avant toute copie de source : une mise à jour
# applicative réutilise cette couche au lieu de retélécharger les paquets.
#
# OCR métier (Phase 2.3) : l'extraction appelle le binaire `tesseract -l fra`
# (seamtech_search/ocr) et pdftoppm (poppler-utils) pour rendre les pages
# scannées avant OCR. Sans ces paquets, tout PDF scanné est dégradé en silence
# dans la pile Docker (« tesseract absent ») alors que la CI les installe —
# l'image doit porter le MÊME outillage que la CI. `tesseract-ocr-fra` fournit
# fra.traineddata (la langue par défaut de l'OCR est « fra »), eng pour les scans anglais.
#
# `postgresql-client-${PG_MAJEURE}` (OBS-04) : le service `sauvegarde`
# partage cette image et seamtech_search.sauvegarde appelle les binaires
# pg_dump/pg_restore — sans eux, chaque sauvegarde planifiée échouait
# (« binaire introuvable ») alors que le reste de la pile semblait sain.
#
# LA MAJEURE EST ÉPINGLÉE, et l'ancien commentaire disait le contraire de la
# réalité : « le client Debian (majeure >= serveur 16) sait dumper ». Il sait
# DUMPER, en effet. Il ne sait pas RESTAURER : un client 17+ écrit dans
# l'archive `SET transaction_timeout`, réglage inconnu de PostgreSQL 16, et
# `pg_restore` échoue alors par « unrecognized configuration parameter ».
# La sauvegarde réussit, la restauration échoue, et personne ne le découvre
# avant le jour du sinistre — constaté le 2026-11-13 par la preuve réelle
# (job `integration`), qui s'est arrêtée net sur ce mur.
#
# Debian ne fournit que des clients 17+ : on prend donc le dépôt PGDG, qui
# sert la majeure 16 quelle que soit la suite Debian de l'image de base.
#
# ÉCHEC FERMÉ : les deux assertions `grep -q ") ${PG_MAJEURE}."` sont le point
# le plus important de ce bloc. Sans elles, un dépôt PGDG injoignable
# laisserait l'image avec le client Debian par défaut — celui-là même qui
# casse la restauration — et rien ne le signalerait avant le sinistre. Une
# image qui ne peut pas prouver la majeure de son client ne se construit pas.
# Dépôt PGDG : seule source d'un client de majeure 16 quand Debian n'en sert
# plus. La clé est épinglée par empreinte de dépôt, pas importée depuis un
# serveur de clés — un import distant ajouterait un tiers de confiance
# supplémentaire dans la chaîne de construction de l'image.
# Les commentaires restent HORS du bloc RUN : à l'intérieur d'une instruction
# à continuation, le parseur ne les garantit pas.
RUN --mount=type=cache,target=/var/cache/apt,id=seamtech-apt-cache,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,id=seamtech-apt-lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache \
    && apt-get -o Acquire::Retries=${APT_RETRIES} update \
    && apt-get -o Acquire::Retries=${APT_RETRIES} install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
    && . /etc/os-release \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor > /usr/share/keyrings/pgdg.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt \
        ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get -o Acquire::Retries=${APT_RETRIES} update \
    && apt-get -o Acquire::Retries=${APT_RETRIES} install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-fra \
        tesseract-ocr-eng \
        poppler-utils \
        postgresql-client-${PG_MAJEURE} \
    && tesseract --list-langs 2>&1 | grep -q fra \
    && tesseract --list-langs 2>&1 | grep -q eng \
    && pg_dump --version | grep -q ") ${PG_MAJEURE}\." \
    && pg_restore --version | grep -q ") ${PG_MAJEURE}\." \
    && pg_dump --version \
    && pg_restore --version

# Dépendances Python ensuite, elles aussi avant les sources. Le cache pip
# BuildKit survit à un échec réseau : un nouvel essai ne repart pas de zéro
# pour les wheels déjà téléchargés intégralement.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip,id=seamtech-pip-cache,sharing=locked \
    pip install --default-timeout ${PIP_DEFAULT_TIMEOUT} --retries ${PIP_RETRIES} -r requirements.txt

COPY pyproject.toml README.md ./
COPY seamtech_search ./seamtech_search
COPY config ./config

# Lot 18 — le provisionnement des extensions voyage AVEC l'image : le service
# one-shot `provisionner-extensions` de docker-compose.yml l'exécute au
# démarrage de la pile. Sans ce fichier dans l'image, ce service rendait
# « exit 2 » (`python` ne trouvait pas le script) et `docker compose up`
# refusait de démarrer l'application — ce qui est déjà arrivé, d'où la ligne.
COPY scripts/provisionner_extensions.py ./scripts/

# The server refuses to start without config/config.json (by design, to avoid
# silent misconfiguration on the desktop app). Container deployments configure
# the app through SEAMTECH_* environment variables, which override the values
# below — so seed the baseline from the committed example.
RUN cp config/config.example.json config/config.json

# Healthcheck hits the liveness probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=3).getcode()==200 else 1)" || exit 1

RUN chown -R seamtech:seamtech /app
USER seamtech

EXPOSE 8000

CMD ["python", "-m", "seamtech_search", "serve", "--config", "config/config.json"]
