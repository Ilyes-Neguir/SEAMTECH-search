#!/usr/bin/env bash
# Préparation du transfert hors ligne SEAMTECH (chantier DEP).
#
# À exécuter sur un poste CONNECTÉ (jamais sur le PC personnel du
# commanditaire pour la production : poste de développement ou CI). Produit,
# dans le dossier de sortie choisi, un paquet transférable par support
# amovible vers le serveur d'entreprise SANS réseau :
#
#   seamtech-<sha>.bundle     — dépôt Git complet au SHA figé (git bundle)
#   images-<sha>.tar          — toutes les images Docker requises
#                                (infra tirées + images applicatives
#                                construites via docker compose build)
#   MANIFESTE.txt             — SHA du code, images et tailles, date
#   SHA256SUMS                — empreintes du paquet (intégrité au transfert)
#
# Côté serveur : `bash scripts/preparer_transfert_hors_ligne.sh verifier
# DOSSIER` contrôle SHA256SUMS puis `git bundle verify`, et
# `bash scripts/preparer_transfert_hors_ligne.sh charger DOSSIER` fait
# `docker load`. Le reste (secrets, démarrage, ingestion) suit
# docs/PLAN_DIRECTEUR_V2_DEP.md. Aucun secret n'entre dans le paquet :
# .env se crée sur le serveur, jamais transporté.
set -euo pipefail

MODE="${1:-preparer}"
SORTIE="${2:-}"
if [ -z "$SORTIE" ]; then
  echo "Usage : $0 preparer|verifier|charger DOSSIER_SORTIE" >&2
  exit 2
fi

exiger() {
  command -v "$1" >/dev/null 2>&1 || { echo "Outil requis introuvable : $1" >&2; exit 2; }
}

case "$MODE" in
  preparer)
    exiger git
    exiger docker
    exiger sha256sum
    RACINE="$(git rev-parse --show-toplevel)"
    cd "$RACINE"
    SHA="$(git rev-parse HEAD)"
    SHA_COURT="$(git rev-parse --short=12 HEAD)"
    if [ -n "$(git status --porcelain)" ]; then
      echo "Arbre Git non propre : le paquet doit figer un SHA commité (refus)." >&2
      exit 2
    fi
    mkdir -p "$SORTIE"
    SORTIE="$(cd "$SORTIE" && pwd)"
    BUNDLE="$SORTIE/seamtech-$SHA_COURT.bundle"
    TAR_IMAGES="$SORTIE/images-$SHA_COURT.tar"

    echo "== Construction des images applicatives (docker compose build) =="
    docker compose build

    echo "== Récupération des images d'infrastructure =="
    IMAGES_INFRA="pgvector/pgvector:pg16 redis:7-alpine"
    for image in $IMAGES_INFRA; do docker pull "$image"; done
    IMAGE_MINIO="quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
    if ! docker image inspect "$IMAGE_MINIO" >/dev/null 2>&1; then
      # MinIO n'est plus distribué par un registre : construction locale
      # documentée (scripts/construire_image_minio.sh).
      bash scripts/construire_image_minio.sh
    fi

    echo "== Inventaire des images du projet Compose =="
    IMAGES_PROJET="$(docker compose images --format '{{.Repository}}:{{.Tag}}' | sort -u)"
    TOUTES="$(printf '%s\n%s\n%s\n' "$IMAGES_PROJET" "$IMAGES_INFRA" "$IMAGE_MINIO" | tr ' ' '\n' | sort -u | grep -v '^$')"
    echo "$TOUTES"

    echo "== Export des images (docker save) =="
    # shellcheck disable=SC2086
    docker save -o "$TAR_IMAGES" $TOUTES

    echo "== Bundle Git au SHA $SHA =="
    git bundle create "$BUNDLE" "$SHA" --branches --tags

    {
      echo "SEAMTECH — paquet de transfert hors ligne (DEP)"
      echo "code_sha=$SHA"
      echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "bundle=$(basename "$BUNDLE")"
      echo "images_tar=$(basename "$TAR_IMAGES")"
      echo "images:"
      echo "$TOUTES" | sed 's/^/  - /'
      echo "tailles:"
      du -h "$BUNDLE" "$TAR_IMAGES" | sed 's/^/  /'
      echo "procedure_serveur: docs/PLAN_DIRECTEUR_V2_DEP.md"
    } > "$SORTIE/MANIFESTE.txt"

    ( cd "$SORTIE" && sha256sum "$(basename "$BUNDLE")" "$(basename "$TAR_IMAGES")" MANIFESTE.txt > SHA256SUMS )
    echo "Paquet prêt dans $SORTIE (vérification : $0 verifier $SORTIE)"
    ;;
  verifier)
    exiger git
    exiger sha256sum
    cd "$SORTIE"
    echo "== Empreintes =="
    sha256sum -c SHA256SUMS
    echo "== Bundle Git =="
    git bundle verify seamtech-*.bundle
    echo "== Archive d'images =="
    tar -tf images-*.tar >/dev/null && echo "archive lisible"
    cat MANIFESTE.txt
    ;;
  charger)
    exiger docker
    exiger sha256sum
    cd "$SORTIE"
    sha256sum -c SHA256SUMS
    docker load -i images-*.tar
    echo "Images chargées. Étape suivante : cloner le bundle puis suivre docs/PLAN_DIRECTEUR_V2_DEP.md (§3)."
    ;;
  *)
    echo "Mode inconnu : $MODE (preparer | verifier | charger)" >&2
    exit 2
    ;;
esac
