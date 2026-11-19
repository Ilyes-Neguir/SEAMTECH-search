"""Preuve que la sauvegarde AUTOMATIQUE produit réellement un artefact.

Toutes les épreuves existantes de ``executer_si_du`` passent une fonction de
sauvegarde SIMULÉE — ``fonction_sauver=lambda: {"complet": True,
"protection_globale_prouvee": True}``. Elles prouvent QUAND le planificateur
se déclenche, jamais qu'il a produit quoi que ce soit sur le disque. Le vrai
chemin (``_sauver_reel``) n'était exercé par aucune épreuve : c'est ainsi
qu'un défaut de qualification a pu passer inaperçu malgré quinze fichiers de
tests de sauvegarde.

Ce fichier appelle le vrai chemin et constate l'artefact sur le disque. Il ne
simule rien d'autre que le transport S3.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres


def _creer_base(nom: str) -> str:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom}"')
    finally:
        admin.close()
    return URL_PG.rsplit("/", 1)[0] + f"/{nom}"


def _supprimer_base(nom: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
    finally:
        admin.close()


@pytest.fixture()
def installation(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Base PostgreSQL réelle et migrée, archive à couvrir, dossier de dépôt."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    from seamtech_search.indexer import SearchIndex

    nom = f"planifiee_{uuid.uuid4().hex[:10]}"
    url = _creer_base(nom)
    index = SearchIndex(Path(f"/tmp/unused-planifiee-{nom}.db"), url)
    index.initialize()
    index.run_migrations()

    archive = tmp_path / "archive"
    (archive / "AFFAIRE-1").mkdir(parents=True)
    (archive / "AFFAIRE-1" / "fiche.pdf").write_bytes(b"%PDF-1.4 fiche de test")

    sauvegardes = tmp_path / "backups"
    sauvegardes.mkdir()

    try:
        yield {
            "url": url,
            "nom": nom,
            "archive": archive,
            "sauvegardes": sauvegardes,
        }
    finally:
        index.close()
        _supprimer_base(nom)


def test_un_passage_planifie_ecrit_un_dump_et_un_manifeste_reels(
    installation: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le passage AUTOMATIQUE écrit un dump et son manifeste — sans simulation.

    C'est la preuve que la sauvegarde automatique s'est réellement déclenchée
    ET qu'elle a produit un artefact exploitable, et non pas seulement que le
    planificateur existe ou qu'il sait décider.
    """
    from seamtech_search import sauvegarde_planifiee
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[installation["archive"]],
        min_free_bytes=0,
        database_url=installation["url"],
    )
    # Même source pour le planificateur et pour la lecture d'âge : sans cela,
    # `sauvegarde_due` chercherait ailleurs que là où l'on écrit.
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(installation["sauvegardes"]))

    # Aucune fonction de sauvegarde simulée : le VRAI chemin s'exécute.
    sauvegarde_planifiee.executer_si_du(config, intervalle_heures=24)

    dumps = sorted(installation["sauvegardes"].glob("*.dump"))
    assert dumps, (
        "aucun dump produit par un passage planifié ; contenu du dossier : "
        f"{[p.name for p in installation['sauvegardes'].iterdir()]}"
    )
    dump = dumps[0]
    assert dump.stat().st_size > 0, "le dump est vide : l'artefact n'est pas exploitable"

    manifeste_path = dump.with_name(dump.name + ".manifest.json")
    assert manifeste_path.is_file(), f"manifeste absent : {manifeste_path.name}"
    manifeste = json.loads(manifeste_path.read_text(encoding="utf-8"))
    assert manifeste["dump"]["fichier"] == dump.name, manifeste.get("dump")
    assert manifeste["complet_perimetre_demande"] is True, manifeste
