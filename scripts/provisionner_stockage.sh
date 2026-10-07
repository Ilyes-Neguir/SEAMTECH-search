#!/usr/bin/env bash
# Provisionne le stockage objet pour SEAMTECH Search : buckets, identités
# RESTREINTES et vérifications. À exécuter UNE fois (et à chaque rotation de
# secret) AVANT de démarrer les services applicatifs.
#
# Pourquoi ce script existe (constat de la revue indépendante du 2026-10-07) :
# `docker-compose.yml` fournissait les identifiants ADMINISTRATEUR MinIO
# (`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`) aux services `web` et `worker` :
# l'application tournait donc en root du stockage, avec le droit de lire,
# écraser ou supprimer N'IMPORTE QUEL bucket. Le moindre privilège n'était pas
# tenu, et régler `SEAMTECH_S3_ACCESS_KEY` dans `.env` ne changeait rien
# puisque la composition écrasait cette valeur.
#
# Principe retenu :
#   * les identifiants ADMINISTRATEUR ne servent QU'ICI (provisionnement) ;
#   * l'application reçoit une identité DÉDIÉE, limitée à son bucket ;
#   * la sauvegarde a SON identité, limitée au bucket hors-site ;
#   * aucune de ces valeurs n'est journalisée par ce script.
#
# Idempotent : rejouable sans effet de bord ; recrée la politique et réattache
# l'utilisateur (c'est aussi la procédure de ROTATION des identifiants).
#
# Entrées (environnement ; voir .env.example) :
#   MINIO_ROOT_USER / MINIO_ROOT_PASSWORD      administrateur (provisionnement)
#   SEAMTECH_S3_ACCESS_KEY / SEAMTECH_S3_SECRET_KEY   identité APPLICATIVE
#   SEAMTECH_BACKUP_ACCESS_KEY / SEAMTECH_BACKUP_SECRET_KEY  identité SAUVEGARDE
#   SEAMTECH_S3_BUCKET (défaut seamtech-documents)
#   SEAMTECH_BACKUP_BUCKET (défaut seamtech-backups)
#   SEAMTECH_MINIO_CONTAINER  nom du conteneur MinIO si HORS composition
#                             (la CI hors compose : « minio ») ; par défaut
#                             le service compose « minio » est utilisé.
#   SEAMTECH_MINIO_URL  URL vue DEPUIS le conteneur (défaut http://localhost:9000)
#
# Usage : bash scripts/provisionner_stockage.sh
set -euo pipefail

BUCKET="${SEAMTECH_S3_BUCKET:-seamtech-documents}"
BUCKET_SAUVEGARDE="${SEAMTECH_BACKUP_BUCKET:-seamtech-backups}"
URL_INTERNE="${SEAMTECH_MINIO_URL:-http://localhost:9000}"

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER requis (administrateur, provisionnement uniquement)}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD requis (administrateur, provisionnement uniquement)}"
: "${SEAMTECH_S3_ACCESS_KEY:?SEAMTECH_S3_ACCESS_KEY requis (identité applicative dédiée)}"
: "${SEAMTECH_S3_SECRET_KEY:?SEAMTECH_S3_SECRET_KEY requis (secret applicatif dédié)}"
: "${SEAMTECH_BACKUP_ACCESS_KEY:?SEAMTECH_BACKUP_ACCESS_KEY requis (identité sauvegarde dédiée)}"
: "${SEAMTECH_BACKUP_SECRET_KEY:?SEAMTECH_BACKUP_SECRET_KEY requis (secret sauvegarde dédié)}"

# Refus explicite : si l'exploitant réutilise l'administrateur comme identité
# applicative, ce script ne doit PAS « provisionner » en silence un compte qui
# viole le moindre privilège — il s'arrête et le dit.
if [ "$SEAMTECH_S3_ACCESS_KEY" = "$MINIO_ROOT_USER" ] || [ "$SEAMTECH_S3_SECRET_KEY" = "$MINIO_ROOT_PASSWORD" ]; then
    echo "REFUS : SEAMTECH_S3_ACCESS_KEY/SECRET_KEY sont ceux de l'administrateur MinIO." >&2
    echo "        L'application doit disposer d'une identité DÉDIÉE (moindre privilège)." >&2
    exit 2
fi
if [ "$SEAMTECH_BACKUP_ACCESS_KEY" = "$MINIO_ROOT_USER" ] || [ "$SEAMTECH_BACKUP_SECRET_KEY" = "$MINIO_ROOT_PASSWORD" ]; then
    echo "REFUS : SEAMTECH_BACKUP_ACCESS_KEY/SECRET_KEY sont ceux de l'administrateur MinIO." >&2
    exit 2
fi
if [ "$SEAMTECH_S3_ACCESS_KEY" = "$SEAMTECH_BACKUP_ACCESS_KEY" ]; then
    echo "REFUS : l'identité applicative et l'identité de sauvegarde doivent être DISTINCTES" >&2
    echo "        (une compromission de l'application ne doit pas donner accès aux sauvegardes)." >&2
    exit 2
fi

ALIAS="provisionnement"
# Détermination du mode d'accès à `mc` : conteneur nommé (CI hors compose) ou
# service compose. `mc` est embarqué dans l'image MinIO reconstruite par
# scripts/construire_image_minio.sh — aucun binaire à installer sur l'hôte.
if [ -n "${SEAMTECH_MINIO_CONTAINER:-}" ]; then
    _mc() { docker exec -i "$SEAMTECH_MINIO_CONTAINER" mc "$@"; }
    MODE="conteneur $SEAMTECH_MINIO_CONTAINER (docker exec)"
else
    _mc() { docker compose exec -T minio mc "$@"; }
    MODE="service compose « minio »"
fi

echo "== Provisionnement du stockage SEAMTECH ($MODE)"
echo "   bucket applicatif : $BUCKET"
echo "   bucket sauvegarde : $BUCKET_SAUVEGARDE"
echo "   identités         : applicative=$SEAMTECH_S3_ACCESS_KEY, sauvegarde=$SEAMTECH_BACKUP_ACCESS_KEY (secret jamais affiché)"

# 1. Alias d'administration — le secret passe par une variable d'environnement
#    du conteneur, jamais par la ligne de commande de l'hôte.
_mc alias set "$ALIAS" "$URL_INTERNE" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null

# 2. Buckets (idempotent) + versioning (c'est le PROVISIONNEMENT qui l'active :
#    l'application, désormais restreinte, n'a plus le droit administratif
#    PutBucketVersioning — le défaut « un écrasement reste récupérable » est
#    donc porté ici, et vérifié juste après).
_mc mb --ignore-existing "$ALIAS/$BUCKET" >/dev/null
_mc mb --ignore-existing "$ALIAS/$BUCKET_SAUVEGARDE" >/dev/null
_mc version enable "$ALIAS/$BUCKET" >/dev/null
_mc version enable "$ALIAS/$BUCKET_SAUVEGARDE" >/dev/null

# 3. Politiques (moindre privilège : UN bucket, et rien d'administratif).
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
cat > "$tmpdir/app.json" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketVersioning"],
      "Resource": ["arn:aws:s3:::__BUCKET__"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                 "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": ["arn:aws:s3:::__BUCKET__/*"]
    }
  ]
}
JSON
sed "s/__BUCKET__/$BUCKET/g" "$tmpdir/app.json" > "$tmpdir/politique-app.json"
sed "s/__BUCKET__/$BUCKET_SAUVEGARDE/g" "$tmpdir/app.json" > "$tmpdir/politique-sauvegarde.json"

_mc admin policy detach "$ALIAS" "seamtech-app" --user "$SEAMTECH_S3_ACCESS_KEY" >/dev/null 2>&1 || true
_mc admin policy rm "$ALIAS" "seamtech-app" >/dev/null 2>&1 || true
_mc admin policy create "$ALIAS" "seamtech-app" "$tmpdir/politique-app.json" >/dev/null
_mc admin policy detach "$ALIAS" "seamtech-sauvegarde" --user "$SEAMTECH_BACKUP_ACCESS_KEY" >/dev/null 2>&1 || true
_mc admin policy rm "$ALIAS" "seamtech-sauvegarde" >/dev/null 2>&1 || true
_mc admin policy create "$ALIAS" "seamtech-sauvegarde" "$tmpdir/politique-sauvegarde.json" >/dev/null

# 4. Utilisateurs : recréés pour que le SECRET fourni soit réellement celui
#    appliqué (rotation idempotente). Un compte retiré puis recréé ne conserve
#    aucun privilège implicite.
for utilisateur in "$SEAMTECH_S3_ACCESS_KEY" "$SEAMTECH_BACKUP_ACCESS_KEY"; do
    _mc admin user remove "$ALIAS" "$utilisateur" >/dev/null 2>&1 || true
done
_mc admin user add "$ALIAS" "$SEAMTECH_S3_ACCESS_KEY" "$SEAMTECH_S3_SECRET_KEY" >/dev/null
_mc admin user add "$ALIAS" "$SEAMTECH_BACKUP_ACCESS_KEY" "$SEAMTECH_BACKUP_SECRET_KEY" >/dev/null
_mc admin policy attach "$ALIAS" "seamtech-app" --user "$SEAMTECH_S3_ACCESS_KEY" >/dev/null
_mc admin policy attach "$ALIAS" "seamtech-sauvegarde" --user "$SEAMTECH_BACKUP_ACCESS_KEY" >/dev/null
_mc admin user enable "$ALIAS" "$SEAMTECH_S3_ACCESS_KEY" >/dev/null
_mc admin user enable "$ALIAS" "$SEAMTECH_BACKUP_ACCESS_KEY" >/dev/null

# 5. Vérifications par le provisionnement lui-même (les tests applicatifs, eux,
#    vérifient l'ALLOW/DENY réel avec boto3 — voir tests/test_credentials_s3_restreintes.py).
echo "-- vérification : utilisateurs et politiques"
_mc admin user info "$ALIAS" "$SEAMTECH_S3_ACCESS_KEY" | sed -n '1,6p'
_mc admin user info "$ALIAS" "$SEAMTECH_BACKUP_ACCESS_KEY" | sed -n '1,6p'

echo "-- vérification : les buckets sont PRIVÉS (accès anonyme refusé)"
# Preuve structurelle, indépendante de la sortie texte de `mc` : une requête
# HTTP SANS identifiants doit être refusée (403) sur une clé inexistante —
# MinIO répond 403 pour un bucket privé, et 404 pour un bucket public.
code_anonyme="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:9000/$BUCKET/sonde-privee" || true)"
if [ "$code_anonyme" != "403" ]; then
    echo "ATTENTION : accès anonyme au bucket $BUCKET renvoie HTTP ${code_anonyme:-?} (attendu 403)" >&2
    echo "            Vérifier qu'aucune politique anonyme n'a été posée sur ce bucket." >&2
    exit 1
fi
code_anonyme_sauv="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:9000/$BUCKET_SAUVEGARDE/sonde-privee" || true)"
if [ "$code_anonyme_sauv" != "403" ]; then
    echo "ATTENTION : accès anonyme au bucket $BUCKET_SAUVEGARDE renvoie HTTP ${code_anonyme_sauv:-?} (attendu 403)" >&2
    exit 1
fi

cat <<FIN
== Provisionnement terminé.
   À reporter dans .env (jamais dans Git) :
     SEAMTECH_S3_ACCESS_KEY / SEAMTECH_S3_SECRET_KEY      → services web et worker
     SEAMTECH_BACKUP_ACCESS_KEY / SEAMTECH_BACKUP_SECRET_KEY → sauvegarde hors-site
     MINIO_ROOT_USER / MINIO_ROOT_PASSWORD                 → provisionnement UNIQUEMENT
   Droits accordés :
     $SEAMTECH_S3_ACCESS_KEY : lecture/écriture des OBJETS de $BUCKET, listage de ce bucket ; aucun autre bucket, aucune opération d'administration.
     $SEAMTECH_BACKUP_ACCESS_KEY : lecture/écriture des OBJETS de $BUCKET_SAUVEGARDE uniquement.
   Versioning : activé par ce script sur les deux buckets (l'application restreinte n'a plus le droit de l'activer elle-même).
FIN
