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
# `postgresql-client` (OBS-04) : le service `sauvegarde` partage cette image
# et seamtech_search.sauvegarde appelle les binaires pg_dump/pg_restore —
# sans eux, chaque sauvegarde planifiée échouait (« binaire introuvable »)
# alors que le reste de la pile semblait sain. Le client Debian (majeure >=
# serveur 16) sait dumper le serveur pgvector/pg16 de la composition.
RUN --mount=type=cache,target=/var/cache/apt,id=seamtech-apt-cache,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,id=seamtech-apt-lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache \
    && apt-get -o Acquire::Retries=${APT_RETRIES} update \
    && apt-get -o Acquire::Retries=${APT_RETRIES} install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-fra \
        tesseract-ocr-eng \
        poppler-utils \
        postgresql-client \
    && tesseract --list-langs 2>&1 | grep -q fra \
    && tesseract --list-langs 2>&1 | grep -q eng \
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
