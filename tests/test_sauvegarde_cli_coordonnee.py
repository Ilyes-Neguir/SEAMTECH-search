"""P0 — la restauration COORDONNÉE éprouvée PAR LA COMMANDE, pas seulement par la fonction.

Pourquoi ce fichier existe.

``tests/test_sauvegarde_complete.py`` prouve l'aller-retour complet en appelant
directement ``restaurer_complet``. Ce que le rapport jugeait encore « non
éprouvé en situation réelle », c'est le CHEMIN QUE L'EXPLOITANTE PRENDRA
VRAIMENT : la commande

    python -m seamtech_search.sauvegarde sauver …
    python -m seamtech_search.sauvegarde restaurer --coordonnee …

Autrement dit : l'analyse des arguments, le manifeste relu depuis le disque, le
dump retrouvé à côté de lui, la base cible créée par la commande, le contrôle
final. Une fonction peut être juste et sa commande fausse (mauvais repli de
chemin, argument ignoré, code de sortie trompeur) ; c'est précisément ce que ce
fichier rend impossible.

Deux tests, deux portées, aucune redite inutile :

1. **deux processus réels** (``subprocess``), PostgreSQL réel, disque réel, sans
   stockage objet (``--sans-s3``) : base + arborescence ``./data``. Le
   manifeste est écrit par le premier processus et RELU par le second — rien
   n'est partagé en mémoire ;
2. **la commande dans le processus**, avec le transport objet en mémoire : base
   + données + fichiers référencés + objets, c'est-à-dire le périmètre complet
   de ``--coordonnee``.

Limites assumées, écrites ici : le transport S3 est remplacé en mémoire (ni
MinIO ni R2 n'est qualifié par ce fichier), et le second test n'est pas un
processus séparé — c'est le prix du magasin objet en mémoire. Aucune donnée
client : les fichiers sont fabriqués par le test.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.sauvegarde import main
from seamtech_search.storage import S3StorageClient

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
RACINE = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.postgres


def _bindir_pg() -> str:
    """Dossier des binaires ``pg_dump``/``pg_restore`` fournis par ``pgserver``."""
    import pgserver

    candidat = Path(pgserver.__file__).resolve().parent / "pginstall" / "bin"
    assert (candidat / "pg_dump").is_file(), f"pg_dump introuvable dans {candidat}"
    return str(candidat)


def _creer_base(nom: str) -> str:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as curseur:
            curseur.execute(f'CREATE DATABASE "{nom}"')
    finally:
        admin.close()
    return URL_PG.rsplit("/", 1)[0] + f"/{nom}"


def _supprimer_base(nom: str) -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as curseur:
            curseur.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
    finally:
        admin.close()


def _documents_restaurés(url_base: str) -> list[tuple[str, str | None, str]]:
    import psycopg2

    connexion = psycopg2.connect(url_base)
    try:
        with connexion.cursor() as curseur:
            curseur.execute("SELECT name, object_key, upload_status FROM documents")
            return curseur.fetchall()  # type: ignore[no-any-return]
    finally:
        connexion.close()


@pytest.fixture()
def installation(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Base migrée + ./data garni + document référencé + objet en mémoire."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    nom = f"clisauve_{uuid.uuid4().hex[:10]}"
    url = _creer_base(nom)
    index = SearchIndex(Path(f"/tmp/unused-cli-{nom}.db"), url)
    index.initialize()
    index.run_migrations()

    data = tmp_path / "data"
    lots = data / "uploads" / "lots-televerses" / "jeton-A" / "fichiers" / "2026" / "AFFAIRE-1"
    lots.mkdir(parents=True)
    (lots / "fiche.pdf").write_bytes(b"%PDF-1.4 fiche de lot non encore promue")
    (lots / "cotes.xlsx").write_bytes(b"PK\x03\x04 cotes")
    (data / "quarantine").mkdir(parents=True)
    (data / "quarantine" / "suspect.pdf").write_bytes(b"%PDF-1.4 fichier ecarte")
    (data / "rapports").mkdir(parents=True)
    (data / "rapports" / "journal.txt").write_text("rapport genere", encoding="utf-8")
    (data / "verrou.lock").write_text("lock", encoding="utf-8")  # volatil : jamais sauvegardé

    reference = tmp_path / "documents" / "plan.pdf"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"%PDF-1.4 plan reference")
    empreinte = hashlib.sha256(reference.read_bytes()).hexdigest()

    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
                "is_dir, content, content_hash, source_sha256, extraction_status, category, role, "
                "object_key, object_bucket, uploaded_at, upload_status) "
                "VALUES (%s, %s, 'plan.pdf', %s, '.pdf', %s, 0, FALSE, 'plan', %s, %s, 'extracted', "
                "'analyzed', 'fiche_pdf', 'imports/plan.pdf', 'seamtech-documents', 0, 'uploaded')",
                (
                    os.path.normcase(str(reference.resolve())),
                    str(reference),
                    str(reference.parent),
                    reference.stat().st_size,
                    empreinte,
                    empreinte,
                ),
            )
    try:
        yield {
            "nom": nom,
            "url": url,
            "index": index,
            "data": data,
            "documents": tmp_path / "documents",
            "reference": reference,
            "tmp": tmp_path,
        }
    finally:
        index.close()
        _supprimer_base(nom)


# ---------------------------------------------------------------------------
# 1. Deux processus réels : sauver, détruire, restaurer --coordonnee
# ---------------------------------------------------------------------------


def test_commande_sauver_puis_restaurer_coordonnee_en_deux_processus(
    installation: dict[str, Any], tmp_path: Path
) -> None:
    """Le chemin réel de l'exploitante : deux processus, un manifeste relu sur disque."""
    dossier = tmp_path / "sauvegardes"
    environnement = {
        **os.environ,
        "SEAMTECH_DATABASE_URL": installation["url"],
        "SEAMTECH_PG_BINDIR": _bindir_pg(),
        "PYTHONPATH": str(RACINE),
    }

    sauvegarde = subprocess.run(
        [
            sys.executable, "-m", "seamtech_search.sauvegarde", "sauver",
            "--archive", str(installation["documents"]),
            "--dossier", str(dossier),
            "--retention", "5",
            "--sans-s3",
            "--data", str(installation["data"]),
        ],
        capture_output=True, text=True, env=environnement, cwd=str(RACINE), timeout=600,
    )
    # 0 = complet ; 2 = protection globale non prouvée. Aucun autre code n'est
    # acceptable : c'est la commande qui décide si la rétention peut élaguer.
    assert sauvegarde.returncode in (0, 2), sauvegarde.stderr
    sortie = json.loads(sauvegarde.stdout)
    chemin_manifeste = Path(sortie["manifeste"])
    assert chemin_manifeste.is_file(), "la commande n'a pas écrit le manifeste annoncé"
    assert json.loads(chemin_manifeste.read_text(encoding="utf-8"))["data"]["nb_copies"] == 4

    # --- Destruction : l'arborescence de données disparaît -----------------
    shutil.rmtree(installation["data"])
    assert not installation["data"].exists()

    # --- Restauration coordonnée par la commande, dans une base NEUVE -----
    nom_cible = f"clirest_{uuid.uuid4().hex[:10]}"
    data_cible = tmp_path / "restauration" / "data"
    url_cible = URL_PG.rsplit("/", 1)[0] + f"/{nom_cible}"
    try:
        restauration = subprocess.run(
            [
                sys.executable, "-m", "seamtech_search.sauvegarde", "restaurer",
                "--coordonnee",
                "--manifeste", str(chemin_manifeste),
                "--data-cible", str(data_cible),
                "--base-cible", url_cible,
            ],
            capture_output=True, text=True,
            env={**environnement, "SEAMTECH_RESTORE_DATABASE_URL": url_cible},
            cwd=str(RACINE), timeout=600,
        )
        assert restauration.returncode == 0, restauration.stderr
        rapport = json.loads(restauration.stdout)

        assert rapport["base"] == nom_cible
        assert rapport["data"] == 4, rapport
        assert rapport["verification_ok"] is True, rapport

        # L'arborescence est revenue À SA PLACE, octet pour octet.
        fiche = data_cible / "uploads/lots-televerses/jeton-A/fichiers/2026/AFFAIRE-1/fiche.pdf"
        assert fiche.read_bytes() == b"%PDF-1.4 fiche de lot non encore promue"
        assert (data_cible / "quarantine/suspect.pdf").read_bytes() == b"%PDF-1.4 fichier ecarte"
        assert (data_cible / "rapports/journal.txt").read_text(encoding="utf-8") == "rapport genere"
        assert not (data_cible / "verrou.lock").exists(), "un fichier volatil a été restauré"

        # La base restaurée contient le document référencé.
        assert ("plan.pdf", "imports/plan.pdf", "uploaded") in _documents_restaurés(url_cible)
    finally:
        _supprimer_base(nom_cible)


# ---------------------------------------------------------------------------
# 2. Le périmètre complet, objets compris, par la même commande
# ---------------------------------------------------------------------------


def test_commande_restauration_coordonnee_base_donnees_fichiers_et_objets(
    installation: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``restaurer --coordonnee --restaurer-objets`` : les quatre composants, contrôlés."""
    from tests.s3_en_memoire import S3EnMemoire

    magasin = S3EnMemoire()
    config = AppConfig(
        root_paths=[installation["documents"]],
        database_path=tmp_path / "inutile.db",
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="seamtech-documents",
        s3_access_key="cle-fictive",
        s3_secret_key="secret-fictif",
    )
    dossier = tmp_path / "sauvegardes"
    environnement = {
        "SEAMTECH_DATABASE_URL": installation["url"],
        "SEAMTECH_PG_BINDIR": _bindir_pg(),
    }
    for cle, valeur in environnement.items():
        monkeypatch.setenv(cle, valeur)

    with (
        patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
        patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
        patch.object(S3StorageClient, "_enable_versioning_once", lambda self: None),
        patch("seamtech_search.config.AppConfig.load", staticmethod(lambda *a, **k: config)),
    ):
        client = S3StorageClient(config=config)
        client.upload_file(installation["reference"], remote_key="imports/plan.pdf", avoid_overwrite=False)

        assert main([
            "sauver",
            "--archive", str(installation["documents"]),
            "--dossier", str(dossier),
            "--retention", "5",
            "--sans-s3",
            "--avec-references",
            "--data", str(installation["data"]),
        ]) in (0, 2)

        chemin_manifeste = dossier / sorted(
            p.name for p in dossier.glob("*.manifest.json")
        )[-1]
        manifeste = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
        # Le périmètre demandé doit être couvert, sinon le test ne prouve rien.
        assert manifeste["objets_s3"]["nb_copies"] == 1, manifeste["objets_s3"]
        assert manifeste["fichiers_locaux"]["nb_copies"] == 1, manifeste["fichiers_locaux"]
        assert manifeste["data"]["nb_copies"] == 4, manifeste["data"]

        # --- Destruction totale : base, données, documents, objets --------
        shutil.rmtree(installation["data"])
        shutil.rmtree(installation["documents"])
        magasin.objets.clear()

        nom_cible = f"clirest2_{uuid.uuid4().hex[:10]}"
        url_cible = URL_PG.rsplit("/", 1)[0] + f"/{nom_cible}"
        data_cible = tmp_path / "restauration" / "data"
        fichiers_cible = tmp_path / "restauration" / "fichiers"
        monkeypatch.setenv("SEAMTECH_RESTORE_DATABASE_URL", url_cible)
        try:
            assert main([
                "restaurer",
                "--coordonnee",
                "--manifeste", str(chemin_manifeste),
                "--data-cible", str(data_cible),
                "--fichiers-cible", str(fichiers_cible),
                "--restaurer-objets",
                "--base-cible", url_cible,
            ]) == 0

            # Le manifeste ne devient « protection globale prouvée » qu'ici :
            # la preuve n'est pas déclarative, elle suit le contrôle.
            manifeste_apres = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
            assert manifeste_apres["complet"] is True, manifeste_apres
            assert manifeste_apres["protection_globale_prouvee"] is True, manifeste_apres

            # Les quatre composants sont revenus.
            assert (data_cible / "quarantine/suspect.pdf").is_file()
            # Les fichiers locaux sont restaurés sous <destination>/<id>/<nom> :
            # jamais à leur emplacement d'origine, jamais à côté d'un original.
            restaures = sorted(fichiers_cible.glob("*/plan.pdf"))
            assert len(restaures) == 1, [str(f) for f in restaures]
            assert restaures[0].read_bytes() == b"%PDF-1.4 plan reference"
            assert "imports/plan.pdf" in magasin.objets
            assert magasin.objets["imports/plan.pdf"]["donnees"] == b"%PDF-1.4 plan reference"
            assert ("plan.pdf", "imports/plan.pdf", "uploaded") in _documents_restaurés(url_cible)
        finally:
            _supprimer_base(nom_cible)
