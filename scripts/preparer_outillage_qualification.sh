#!/usr/bin/env bash
# Prépare tesseract + tessdata + MinIO DANS LE BAC À SABLE (pas sur le PC utilisateur).
# Réseau autorisé ici : github.com / api.github.com / codeload.github.com / pypi.org.
# Les binaires GitHub Releases passent par release-assets.githubusercontent.com
# (souvent BLOQUÉ) — dans ce cas le script échoue clairement, sans mock.
set -euo pipefail

RACINE="${SEAMTECH_OUTILLAGE:-/tmp/outillage-seamtech}"
mkdir -p "$RACINE"
echo "[outillage] racine=$RACINE"

# --- tessdata (git/codeload : autorisé) ---
if [[ ! -f "$RACINE/tessdata/fra.traineddata" ]]; then
  echo "[outillage] clone tessdata_fast (fra/eng/osd)"
  git clone --depth 1 https://github.com/tesseract-ocr/tessdata_fast.git "$RACINE/tessdata_fast"
  mkdir -p "$RACINE/tessdata"
  cp "$RACINE/tessdata_fast/fra.traineddata" "$RACINE/tessdata/"
  cp "$RACINE/tessdata_fast/eng.traineddata" "$RACINE/tessdata/"
  cp "$RACINE/tessdata_fast/osd.traineddata" "$RACINE/tessdata/" || true
fi
export TESSDATA_PREFIX="$RACINE/tessdata"
echo "[outillage] TESSDATA_PREFIX=$TESSDATA_PREFIX"

# --- tesseract static (GitHub Release → CDN souvent bloqué) ---
TESS_TAG="tesseract-5.5.3-rebuild"
TESS_BIN="$RACINE/tesseract"
if [[ ! -x "$TESS_BIN" ]]; then
  echo "[outillage] tentative tesseract-static $TESS_TAG"
  if ! gh release download "$TESS_TAG" -R DanielMYT/tesseract-static -p 'tesseract.x86_64' -D "$RACINE" ; then
    echo "[outillage] ÉCHEC tesseract : CDN GitHub Releases injoignable." >&2
    echo "[outillage] Ressource nécessaire : accès HTTPS à release-assets.githubusercontent.com" >&2
    echo "[outillage]   OU paquet système tesseract-ocr + tesseract-ocr-fra" >&2
    echo "[outillage]   OU toolchain cmake+libpng-dev+libleptonica-dev pour compiler depuis" >&2
    echo "[outillage]      https://github.com/tesseract-ocr/tesseract" >&2
  else
    mv "$RACINE/tesseract.x86_64" "$TESS_BIN"
    chmod +x "$TESS_BIN"
  fi
fi

# --- MinIO officiel (même CDN) ---
MINIO_TAG="RELEASE.2025-09-07T16-13-09Z"
MINIO_BIN="$RACINE/minio"
if [[ ! -x "$MINIO_BIN" ]]; then
  echo "[outillage] tentative MinIO $MINIO_TAG linux-amd64"
  if ! gh release download "$MINIO_TAG" -R minio/minio -p 'minio.linux-amd64.*' -D "$RACINE" ; then
    echo "[outillage] ÉCHEC MinIO : CDN GitHub Releases injoignable ; go n'est pas présent pour compiler." >&2
    echo "[outillage] Ressource nécessaire : le même CDN, OU un binaire MinIO déjà sur la machine," >&2
    echo "[outillage]   OU golang pour 'go install github.com/minio/minio@${MINIO_TAG}'." >&2
    echo "[outillage] s3rver/moto ne seront PAS utilisés comme substitut MinIO." >&2
  else
    mv "$RACINE"/minio.linux-amd64.* "$MINIO_BIN"
    chmod +x "$MINIO_BIN"
  fi
fi

echo "---- résumé ----"
command -v tesseract >/dev/null && echo "tesseract PATH=$(command -v tesseract)" || true
[[ -x "$TESS_BIN" ]] && echo "tesseract local=$TESS_BIN" && "$TESS_BIN" --version | head -3 || echo "tesseract : ABSENT"
[[ -x "$MINIO_BIN" ]] && echo "minio local=$MINIO_BIN" || echo "minio : ABSENT"
ls -l "$RACINE/tessdata"/*.traineddata 2>/dev/null || true
