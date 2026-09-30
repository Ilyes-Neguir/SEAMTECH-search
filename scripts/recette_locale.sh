#!/usr/bin/env bash
# Recette locale SEAMTECH Search — variante Linux/CI.
#
# Une commande qui vérifie que la pile COMPLÈTE fonctionne en local :
#   prereqs → ports libres → .env (secrets aléatoires, jamais dans Git)
#   → image MinIO locale (registres morts) → docker compose up -d --build
#   → santé /live /ready /health → compte nominatif → dépôt des archives
#   → suivi des lots → fiches a_valider badgées → validation → recherches
#   (texte, dimension « 6,60 », filtres/facettes, suggestions) → PDF présigné
#   + zone surlignée → rejeu idempotent → sauvegarde/restauration
#   → persistance down/up → RAPPORT PASS/FAIL ligne par contrôle.
#
# Idempotent, rejouable, sans Internet après les builds, ne modifie JAMAIS les
# archives sources (RG13). Les contrôles fonctionnels vivent dans
# scripts/recette_verif.py (exécuté dans le conteneur web) — même source que
# la variante Windows scripts/recette_locale.ps1.
#
# Usage : scripts/recette_locale.sh [dossier_supp_1 [dossier_supp_2 ...]]
#   Les 7 ZIP du dépôt sont déposés par défaut ; tout chemin supplémentaire
#   (les dossiers du commanditaire) est copié en lecture seule dans la zone de
#   travail puis déposé.
# Code sortie : 0 = tous les contrôles PASS, 1 = au moins un FAIL.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RACINE"

IMAGE_MINIO="quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
SOURCES_CONTENEUR="/app/data/recette-sources"
SOURCES_HOTE="$RACINE/data/recette-sources"
RAPPORT_TMP="$(mktemp)"
trap 'rm -f "$RAPPORT_TMP"' EXIT
NB_FAIL=0

controle() { # <id> <PASS|FAIL> <détail>
    echo "CONTROLE|$1|$2|$3" | tee -a "$RAPPORT_TMP"
    if [ "$2" = "FAIL" ]; then
        NB_FAIL=$((NB_FAIL + 1))
    fi
}

echo "=== Recette locale SEAMTECH Search ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ==="

# ---------------------------------------------------------------------------
# 1. Prérequis : Docker, Compose v2, git (construction MinIO), bash.
# ---------------------------------------------------------------------------
MANQUANTS=""
command -v docker >/dev/null 2>&1 || MANQUANTS="$MANQUANTS docker"
docker compose version >/dev/null 2>&1 || MANQUANTS="$MANQUANTS 'docker compose v2'"
command -v git >/dev/null 2>&1 || MANQUANTS="$MANQUANTS git"
if [ -n "$MANQUANTS" ]; then
    controle "prereqs" "FAIL" "outils absents :$MANQUANTS"
    echo "=== RAPPORT FINAL : 1 FAIL (prérequis) — correction impossible sans Docker/Compose ==="
    exit 1
fi
DOCKER_V="$(docker --version 2>&1 | head -1)"
COMPOSE_V="$(docker compose version 2>&1 | head -1)"
docker info >/dev/null 2>&1 || {
    controle "prereqs" "FAIL" "Docker installé mais le démon ne répond pas (Docker Desktop démarré ?)"
    exit 1
}
controle "prereqs" "PASS" "$DOCKER_V ; $COMPOSE_V ; git $(git --version | head -1)"

# ---------------------------------------------------------------------------
# 2. Ports attendus libres (sauf s'ils appartiennent déjà à cette pile).
# ---------------------------------------------------------------------------
PORTS="8000 3000 5433 6379 9000 9001"
if [ -n "$(docker compose ps -q 2>/dev/null)" ]; then
    controle "ports-libres" "PASS" "pile déjà en cours — les ports $PORTS appartiennent à cette recette (rejeu)"
else
    OCCUPES=""
    for port in $PORTS; do
        if timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null; then
            OCCUPES="$OCCUPES $port"
        fi
    done
    if [ -n "$OCCUPES" ]; then
        controle "ports-libres" "FAIL" "ports déjà occupés :$OCCUPES (un autre service écoute)"
        echo "=== RAPPORT FINAL : ports bloqués — libérez-les puis relancez ==="
        exit 1
    fi
    controle "ports-libres" "PASS" "ports $PORTS libres"
fi

# ---------------------------------------------------------------------------
# 3. .env : secrets locaux aléatoires (jamais dans Git — .gitignore).
# ---------------------------------------------------------------------------
VARS="POSTGRES_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD REDIS_PASSWORD SEAMTECH_AUTH_TOKEN SEAMTECH_UI_PASSWORD SEAMTECH_SESSION_SECRET"
if [ ! -f .env ]; then
    umask 077
    : > .env
    for var in $VARS; do
        echo "$var=seamtech-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" >> .env
    done
    echo "SEAMTECH_ROOT_PATHS=$SOURCES_CONTENEUR:/app/data/recette-lot:/app/data:/app/sample_data" >> .env
    echo "RECETTE_MOT_DE_PASSE=seamtech-recette-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" >> .env
    CREATION="créé avec 7 secrets aléatoires"
else
    CREATION="existant réutilisé (idempotence)"
    grep -q '^SEAMTECH_ROOT_PATHS=' .env || \
        echo "SEAMTECH_ROOT_PATHS=$SOURCES_CONTENEUR:/app/data/recette-lot:/app/data:/app/sample_data" >> .env
    grep -q '^RECETTE_MOT_DE_PASSE=' .env || \
        echo "RECETTE_MOT_DE_PASSE=seamtech-recette-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')" >> .env
fi
SECRETS_OK="ok"
for var in $VARS; do
    grep -q "^$var=." .env || SECRETS_OK="manquant:$var"
done
if [ "$SECRETS_OK" != "ok" ]; then
    controle "env-secrets" "FAIL" ".env incomplet ($SECRETS_OK)"
    exit 1
fi
# shellcheck disable=SC1091
set -a; . ./.env; set +a
controle "env-secrets" "PASS" ".env $CREATION — 7 secrets présents, jamais journalisés (RG9)"

# data/ est monté en bind dans le conteneur web (./data:/app/data) : le
# pré-créer en écriture pour tous AVANT compose up évite un répertoire root
# que ni le conteneur (utilisateur seamtech) ni le lanceur ne pourraient
# écrire (sauvegardes, zone de travail de la recette). Ignoré par Git.
mkdir -p data/backups data/recette-sources data/recette-lot
chmod -R a+rwX data 2>/dev/null || true

# ---------------------------------------------------------------------------
# 4. Image MinIO locale (aucun registre ne la distribue plus).
# ---------------------------------------------------------------------------
if docker image inspect "$IMAGE_MINIO" >/dev/null 2>&1; then
    controle "image-minio" "PASS" "image $IMAGE_MINIO déjà construite (idempotence)"
else
    SORTIE_MINIO="$(bash scripts/construire_image_minio.sh 2>&1)" || {
        controle "image-minio" "FAIL" "construction impossible : $(echo "$SORTIE_MINIO" | tail -3 | tr '\n' ' ')"
        exit 1
    }
    controle "image-minio" "PASS" "image $IMAGE_MINIO construite depuis les sources archivées"
fi

# ---------------------------------------------------------------------------
# 5. Pile complète : build + démarrage + santé avec timeout clair.
# ---------------------------------------------------------------------------
SORTIE_UP="$(docker compose up -d --build 2>&1)" || {
    controle "pile-sante" "FAIL" "docker compose up en échec : $(echo "$SORTIE_UP" | tail -5 | tr '\n' ' ')"
    docker compose ps || true
    docker compose logs --no-color --tail 40 web || true
    exit 1
}
SAIN=""; FIN=$((SECONDS + 600))
while [ $SECONDS -lt $FIN ]; do
    LIVE="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/live || true)"
    READY="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/ready || true)"
    SANT="$(curl -s -o /dev/null -w '%{http_code}' -H "X-SEAMTECH-TOKEN: ${SEAMTECH_AUTH_TOKEN}" http://127.0.0.1:8000/health || true)"
    UI="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/api/health || true)"
    if [ "$LIVE" = "200" ] && [ "$READY" = "200" ] && [ "$SANT" = "200" ] && [ "$UI" = "200" ]; then
        SAIN="oui"
        break
    fi
    sleep 5
done
if [ "$SAIN" != "oui" ]; then
    controle "pile-sante" "FAIL" "santé non atteinte en 600 s : /live=$LIVE /ready=$READY /health=$SANT /api/health=$UI"
    docker compose ps || true
    docker compose logs --no-color --tail 60 web frontend || true
    exit 1
fi
controle "pile-sante" "PASS" "/live=$LIVE /ready=$READY /health=$SANT frontend /api/health=$UI (délai <= 600 s)"

# ---------------------------------------------------------------------------
# 6. Sources à déposer : 7 ZIP du dépôt + chemins passés en argument.
#    Copies de travail dans data/ (ignoré par Git) — sources lues, jamais
#    modifiées (RG13).
# ---------------------------------------------------------------------------
rm -rf "$SOURCES_HOTE"
mkdir -p "$SOURCES_HOTE"
NB_ZIP=0
for zip in "$RACINE"/*.zip; do
    [ -e "$zip" ] || continue
    cp -r "$zip" "$SOURCES_HOTE/"
    NB_ZIP=$((NB_ZIP + 1))
done
NB_EXTRA=0
for chemin in "$@"; do
    if [ ! -e "$chemin" ]; then
        controle "depot-archives" "FAIL" "chemin argument introuvable : $chemin"
        exit 1
    fi
    cp -r "$chemin" "$SOURCES_HOTE/"
    NB_EXTRA=$((NB_EXTRA + 1))
done
echo "Sources en zone de travail : $NB_ZIP ZIP du dépôt + $NB_EXTRA chemin(s) argument."

# ---------------------------------------------------------------------------
# 7. Vérificateur fonctionnel unique, exécuté DANS le conteneur web.
# ---------------------------------------------------------------------------
export RECETTE_MOT_DE_PASSE="${RECETTE_MOT_DE_PASSE:-seamtech-recette-$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')}"
grep -q '^RECETTE_MOT_DE_PASSE=' .env || echo "RECETTE_MOT_DE_PASSE=$RECETTE_MOT_DE_PASSE" >> .env
SORTIE_VERIF="$(docker compose exec -T \
    -e RECETTE_SOURCES="$SOURCES_CONTENEUR" \
    -e RECETTE_MOT_DE_PASSE="$RECETTE_MOT_DE_PASSE" \
    web python - < scripts/recette_verif.py 2>&1)" && CODE_VERIF=0 || CODE_VERIF=$?
echo "$SORTIE_VERIF" | tee -a "$RAPPORT_TMP"
NB_FAIL=$((NB_FAIL + $(grep -c '^CONTROLE|.*|FAIL|' <<<"$SORTIE_VERIF" || true)))
FICHE="$(grep '^INFO|fiche_pour_restauration|' <<<"$SORTIE_VERIF" | tail -1 | cut -d'|' -f3 || true)"
FICHE="${FICHE:-}"

# ---------------------------------------------------------------------------
# 8. Sauvegarde (pg_dump custom) dans data/backups (ignoré par Git).
# ---------------------------------------------------------------------------
mkdir -p data/backups
DUMP="data/backups/recette-$(date -u +%Y%m%d-%H%M%S).dump"
if docker compose exec -T postgres pg_dump -U seamtech --format=custom seamtech_search > "$DUMP" 2>/dev/null && [ -s "$DUMP" ]; then
    TAILLE=$(wc -c < "$DUMP")
    EM="$(sha256sum "$DUMP" | cut -c1-16)"
    controle "sauvegarde" "PASS" "$DUMP — $TAILLE octets, SHA-256 début=$EM"
else
    controle "sauvegarde" "FAIL" "pg_dump impossible ou dump vide ($DUMP)"
fi

# ---------------------------------------------------------------------------
# 9. Restauration rapide : la fiche validée disparaît puis revient.
# ---------------------------------------------------------------------------
if [ -z "$FICHE" ]; then
    controle "restauration" "FAIL" "aucune fiche validée connue (INFO|fiche_pour_restauration absente)"
else
    FICHE_SQL=$(printf '%s' "$FICHE" | sed "s/'/''/g")
    SUPPRIME="$(docker compose exec -T postgres psql -U seamtech -d seamtech_search -tA \
        -c "DELETE FROM fiche WHERE code = '$FICHE_SQL'" 2>&1)" || SUPPRIME="erreur: $SUPPRIME"
    docker compose stop web >/dev/null 2>&1 || true
    RESTAURE="$(docker compose exec -T postgres pg_restore --clean --if-exists --no-owner \
        -U seamtech -d seamtech_search < "$DUMP" 2>&1)" && RESTAURE_OK=0 || RESTAURE_OK=$?
    docker compose start web >/dev/null 2>&1 || true
    FIN=$((SECONDS + 180)); RETOUR="000"
    while [ $SECONDS -lt $FIN ]; do
        RETOUR="$(curl -s -o /dev/null -w '%{http_code}' -H "X-SEAMTECH-TOKEN: ${SEAMTECH_AUTH_TOKEN}" \
            "http://127.0.0.1:8000/fiches/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$FICHE")/pieces" 2>/dev/null || echo 000)"
        [ "$RETOUR" = "200" ] && break
        sleep 3
    done
    if [ "$RESTAURE_OK" = "0" ] && [ "$RETOUR" = "200" ]; then
        controle "restauration" "PASS" "fiche $FICHE supprimée ($SUPPRIME) puis restaurée depuis $DUMP (GET /fiches/$FICHE/pieces = 200)"
    else
        controle "restauration" "FAIL" "pg_restore code=$RESTAURE_OK $(echo "$RESTAURE" | tail -2 | tr '\n' ' ') ; fiche de retour HTTP $RETOUR"
    fi
fi

# ---------------------------------------------------------------------------
# 10. Persistance : docker compose down && up → les données reviennent.
# ---------------------------------------------------------------------------
docker compose down >/dev/null 2>&1 || true
docker compose up -d >/dev/null 2>&1 || true
FIN=$((SECONDS + 300)); PERSIST="000"
while [ $SECONDS -lt $FIN ]; do
    PERSIST="$(curl -s -o /dev/null -w '%{http_code}' -H "X-SEAMTECH-TOKEN: ${SEAMTECH_AUTH_TOKEN}" "http://127.0.0.1:8000/health" 2>/dev/null || echo 000)"
    if [ "$PERSIST" = "200" ]; then break; fi
    sleep 5
done
if [ -n "$FICHE" ]; then
    RETOUR="$(curl -s -o /dev/null -w '%{http_code}' -H "X-SEAMTECH-TOKEN: ${SEAMTECH_AUTH_TOKEN}" \
        "http://127.0.0.1:8000/fiches/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$FICHE")/pieces" 2>/dev/null || echo 000)"
else
    RETOUR="200"
fi
if [ "$PERSIST" = "200" ] && [ "$RETOUR" = "200" ]; then
    controle "persistance-volumes" "PASS" "down && up : santé HTTP $PERSIST, fiche $FICHE toujours présente ($RETOUR) — volumes nommés préservés"
else
    controle "persistance-volumes" "FAIL" "down && up : santé HTTP $PERSIST, fiche HTTP $RETOUR"
fi

# ---------------------------------------------------------------------------
# RAPPORT FINAL : chaque contrôle PASS/FAIL + sortie brute ci-dessus.
# ---------------------------------------------------------------------------
TOTAL=$(grep -c '^CONTROLE|' "$RAPPORT_TMP" || true)
echo ""
echo "=== RAPPORT FINAL — recette locale ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ==="
grep '^CONTROLE|' "$RAPPORT_TMP" | awk -F'|' '{printf "%-22s %-4s %s\n", $2, $3, $4}'
echo "---"
echo "$TOTAL contrôle(s) — $NB_FAIL FAIL — code sortie $([ "$NB_FAIL" -gt 0 ] && echo 1 || echo 0)"
cp "$RAPPORT_TMP" "data/backups/rapport-recette-$(date -u +%Y%m%d-%H%M%S).txt" 2>/dev/null || true
[ "$NB_FAIL" -gt 0 ] && exit 1
exit 0
