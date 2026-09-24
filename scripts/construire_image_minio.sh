#!/usr/bin/env bash
# Construit l'image MinIO locale attendue par docker-compose.yml et la CI.
#
# Pourquoi ce script existe (constats du 2026-09-24, reproductibles) :
#   * docker.io/minio/minio  — retiré de Docker Hub le 2026-09-11 (voir
#     commentaire de docker-compose.yml) ;
#   * quay.io/minio/minio   — « Repository not found » (dépôt supprimé le
#     2026-09-24 : signature exacte d'un dépôt retiré, pas d'un rate-limit —
#     redis/nginx se tiraient en parallèle) ;
#   * dl.min.io             — « 410 Gone — The open-source MinIO Server ...
#     archived and no longer maintained ... These files are no longer served
#     from this site. » ;
#   * github.com/minio/minio — archivé le 2026-04-25 (lecture seule), mais les
#     sources des tags RELEASE.* restent publiques.
# Consigne des auteurs (notes de RELEASE.2025-10-15) : « clone the source and
# build the latest container ». Ce script applique exactement cela, sur le
# MÊME tag que l'image historiquement validée par la CI : sources identiques,
# comportement identique — seule la chaîne de distribution change.
#
# Produit : l'image locale quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z.
# docker compose et la CI la trouvent ensuite sans pull. L'image contient
# minio + mc et l'entrypoint officiel (dockerscripts/docker-entrypoint.sh) ;
# l'alias « local » est préconfiguré pour le healthcheck compose
# `mc ready local` (sonde de disponibilité, non tributaire des identifiants).
#
# Usage : bash scripts/construire_image_minio.sh   (docker + réseau requis)
set -euo pipefail

# Tag MinIO = celui épinglé dans docker-compose.yml et le job sauvegarde de la
# CI ; tests/test_construire_image_minio.py verifie l'identité des épingles.
MINIO_TAG="${MINIO_TAG:-RELEASE.2025-09-07T16-13-09Z}"
MC_TAG="${MC_TAG:-RELEASE.2025-08-13T08-35-41Z}"
IMAGE="quay.io/minio/minio:${MINIO_TAG}"

TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$TRAVAIL"' EXIT

echo "Clonage des sources officielles archivées (minio ${MINIO_TAG}, mc ${MC_TAG})…"
git clone --depth 1 --branch "$MINIO_TAG" https://github.com/minio/minio "$TRAVAIL/minio"
git clone --depth 1 --branch "$MC_TAG" https://github.com/minio/mc "$TRAVAIL/mc"

echo "Construction de ${IMAGE}…"
docker build -t "$IMAGE" -f - "$TRAVAIL" <<'DOCKERFILE'
FROM golang:1.24-bookworm AS construction
WORKDIR /src
COPY minio/ /src/minio/
COPY mc/ /src/mc/
RUN cd /src/minio && CGO_ENABLED=0 go build -tags kqueue -trimpath -o /out/minio .
RUN cd /src/mc && CGO_ENABLED=0 go build -tags kqueue -trimpath -o /out/mc .

FROM debian:bookworm-slim
COPY --from=construction /out/minio /usr/bin/minio
COPY --from=construction /out/mc /usr/bin/mc
COPY minio/dockerscripts/docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/bin/minio /usr/bin/mc /usr/bin/docker-entrypoint.sh \
    && /usr/bin/mc alias set local http://localhost:9000 "" "" --api s3v4
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
VOLUME ["/data"]
CMD ["minio"]
DOCKERFILE

# Fumigène : les binaires de l'image répondent et l'alias « local » (requis par
# le healthcheck compose `mc ready local`) est bien présent. Aucun pipeline vers
# `grep -q` ici : sous `set -o pipefail`, grep -q sortant au premier match
# envoyait SIGPIPE à `docker run` (exit 141, course aléatoire constatée en CI).
docker run --rm --entrypoint /usr/bin/minio "$IMAGE" --help >/dev/null
alias_list="$(docker run --rm --entrypoint /usr/bin/mc "$IMAGE" alias list 2>&1)"
echo "$alias_list"
case "$alias_list" in
  *local*) : ;;
  *)
    echo "alias local absent de l'image reconstruite — le healthcheck compose échouerait" >&2
    exit 1
    ;;
esac

echo "Image ${IMAGE} prête (minio ${MINIO_TAG} + mc ${MC_TAG}, sources officielles archivées)."
