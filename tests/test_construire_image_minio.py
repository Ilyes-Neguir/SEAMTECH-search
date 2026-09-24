"""Garde-fous de scripts/construire_image_minio.sh (image MinIO reconstruite).

Contexte : depuis le 2026-09-24 plus aucun registre ne publie MinIO
(quay.io/minio/minio supprimé, docker.io/minio/minio retiré le 2026-09-11,
dl.min.io « 410 Gone »). La CI (integration, sauvegarde) et le démarrage
local s'appuient sur une image reconstruite depuis les sources officielles
archivées. Ces tests protègent le contrat de reconstruction : même tag que
compose, alias « local » pour le healthcheck `mc ready local`, sources
officielles clonées aux tags, et build CI présent AVANT les étapes qui
consomment l'image.
"""

from __future__ import annotations

import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = RACINE / "scripts" / "construire_image_minio.sh"
CI = RACINE / ".github" / "workflows" / "ci.yml"
COMPOSE = RACINE / "docker-compose.yml"

TAG_RELEASE = re.compile(r"^RELEASE\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _ci() -> str:
    return CI.read_text(encoding="utf-8")


def _compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_tag_script_identique_au_tag_compose() -> None:
    """Le tag reconstruit doit être exactement celui épinglé dans compose.

    Tout écart (script sur une autre release que docker-compose.yml) ferait
    tirer une image non validée à l'exécution.
    """
    tag_script = re.search(r'MINIO_TAG:-([^}]+)', _script())
    tag_compose = re.search(r"image:\s*quay\.io/minio/minio:(\S+)", _compose())
    assert tag_script is not None, "épingle MINIO_TAG absente du script"
    assert tag_compose is not None, "épingle image absente de docker-compose.yml"
    assert tag_script.group(1) == tag_compose.group(1)


def test_tags_au_format_release_verifie() -> None:
    """minio et mc sont clonés sur des tags RELEASE.* (signés upstream)."""
    minio = re.search(r'MINIO_TAG:-([^}]+)', _script())
    mc = re.search(r'MC_TAG:-([^}]+)', _script())
    assert minio is not None and TAG_RELEASE.match(minio.group(1))
    assert mc is not None and TAG_RELEASE.match(mc.group(1))


def test_alias_local_pour_le_healthcheck_compose() -> None:
    """Le healthcheck compose `mc ready local` exige l'alias local préconfiguré.

    `--api s3v4` est obligatoire : sans lui, `mc alias set` sonde le serveur
    (probe-bsign-…) et échoue au build tant qu'aucun MinIO n'écoute (cause
    d'échec CI du 2026-09-24, jour 1, run 36012758232).
    """
    assert "mc alias set local" in _script()
    assert "--api s3v4" in _script()
    assert 'mc", "ready", "local"' in _compose() or "mc ready local" in _compose()


def test_entrypoint_officiel_reutilise() -> None:
    """L'entrypoint du tag officiel (dockerscripts/docker-entrypoint.sh) est repris."""
    assert "dockerscripts/docker-entrypoint.sh" in _script()


def test_sources_officieles_clonnees_aux_tags() -> None:
    """minio et mc viennent des dépôts GitHub officiels, aux tags épinglés."""
    script = _script()
    assert "https://github.com/minio/minio" in script
    assert "https://github.com/minio/mc" in script
    assert script.count("git clone --depth 1 --branch") == 2


def test_script_en_mode_strict() -> None:
    """Le script s'arrête au premier échec (pas de build « à moitié » silencieux)."""
    assert "set -euo pipefail" in _script()


def test_ci_construit_l_image_avant_de_la_consommer() -> None:
    """Les jobs integration et sauvegarde doivent bâtir l'image avant de l'employer.

    Sans cette étape, `docker compose up ... minio` et `docker run
    quay.io/minio/minio:...` retombent sur les registres morts (échec
    `minio Error unauthorized...` constaté le 2026-09-24).
    """
    ci = _ci()
    appels = [m.start() for m in re.finditer(r"run: bash scripts/construire_image_minio\.sh", ci)]
    assert len(appels) == 2, "le build MinIO doit être appelé par les 2 jobs concernés"
    pos_integration = ci.index("- name: Start infra services")
    pos_sauvegarde = ci.index("- name: Start MinIO (bucket")
    assert appels[0] < pos_integration, "build MinIO absent avant l'étape Start infra services"
    assert appels[1] < pos_sauvegarde, "build MinIO absent avant l'étape Start MinIO"
    assert appels[0] < appels[1] < pos_sauvegarde + 1 or appels[1] > pos_integration
