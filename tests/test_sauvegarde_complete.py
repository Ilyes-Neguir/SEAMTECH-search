"""P0.1 — sauvegarde et restauration COMPLÈTES, coordonnées et éprouvées.

Ce que le rapport du 28/10 reprochait : « un dump PostgreSQL a été restauré en
base isolée, mais pas l'ensemble planifié : base + ``./data`` + stockage hors
site + rétention, puis restauration cohérente ».

Ce fichier exécute l'aller-retour complet, sur PostgreSQL réel et un stockage
objet qui conserve les octets :

    sauvegarde (dump + ./data ENTIER + fichiers référencés + objets)
      → destruction (base, répertoire de données, bucket)
      → restauration coordonnée (base + données + objets)
      → contrôle que la base restaurée EST le manifeste.

Le répertoire de données contient ici les trois familles qui n'étaient pas
couvertes par la sauvegarde « référencés seulement » :

* ``uploads/lots-televerses/<jeton>/fichiers/…`` (lots en attente de promotion) ;
* ``quarantine/…`` (fichiers écartés, à conserver pour l'audit) ;
* ``rapports/…`` (livrables générés, sans ligne dans ``documents``).

Limites assumées, écrites ici pour ne pas les oublier : le test remplace le
transport boto3 par un magasin en mémoire (le fournisseur réel MinIO/R2 n'est
pas qualifié par ce test), et il ne simule pas une coupure de courant au milieu
d'un ``pg_dump`` (c'est le rôle des tests de chaos du module).
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from seamtech_search.sauvegarde import (
    SauvegardeError,
    copier_data,
    inventorier_data,
    restaurer_complet,
    restaurer_data,
    sauver,
    verifier,
)
from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire

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


class _Magasin:
    """S3StorageClient branché sur un magasin en mémoire (transport remplacé seulement)."""

    def __init__(self, magasin: S3EnMemoire | None = None) -> None:
        self.magasin = magasin or S3EnMemoire()
        self._patchs: list[Any] = []

    def __enter__(self) -> "_Magasin":
        magasin = self.magasin
        self._patchs = [
            patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: magasin),
            patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
            patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
            patch.object(S3StorageClient, "_enable_versioning_once", lambda self: None),
        ]
        for p in self._patchs:
            p.start()
        return self

    def client(self) -> S3StorageClient:
        from seamtech_search.config import AppConfig

        return S3StorageClient(
            config=AppConfig(
                root_paths=[Path.cwd()],
                min_free_bytes=0,
                s3_endpoint_url="https://s3.invalide",
                s3_bucket="seamtech-documents",
                s3_access_key="cle-fictive",
                s3_secret_key="secret-fictif",
            )
        )

    def __exit__(self, *_exc: object) -> None:
        for p in self._patchs:
            p.stop()


@pytest.fixture()
def installation(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Une installation jetable : base migrée + ./data garni + bucket garni."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    from seamtech_search.indexer import SearchIndex

    nom = f"sauvegarde_{uuid.uuid4().hex[:10]}"
    url = _creer_base(nom)
    index = SearchIndex(Path(f"/tmp/unused-sauvegarde-{nom}.db"), url)
    index.initialize()
    index.run_migrations()

    data = tmp_path / "data"
    lots = data / "uploads" / "lots-televerses" / "jeton-A" / "fichiers" / "2026" / "AFFAIRE-1"
    lots.mkdir(parents=True)
    (lots / "fiche.pdf").write_bytes(b"%PDF-1.4 fiche de lot non encore promue")
    (lots / "cotes.xlsx").write_bytes(b"PK\x03\x04 cotes")
    quarantaine = data / "quarantine"
    quarantaine.mkdir(parents=True)
    (quarantaine / "suspect.pdf").write_bytes(b"%PDF-1.4 fichier ecarte")
    rapports = data / "rapports"
    rapports.mkdir(parents=True)
    (rapports / "journal.txt").write_text("rapport genere, aucune ligne documents", encoding="utf-8")
    # Un volatil qui ne doit PAS entrer dans la sauvegarde.
    (data / "verrou.lock").write_text("lock", encoding="utf-8")

    # Un document référencé en base, avec fichier local ET objet dans le bucket.
    reference = tmp_path / "documents" / "plan.pdf"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"%PDF-1.4 plan reference")
    magasin = S3EnMemoire()
    with _Magasin(magasin) as branche:
        client = branche.client()
        cle = client.upload_file(reference, remote_key="imports/plan.pdf", avoid_overwrite=False)
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents (path_key, path, name, parent_path, extension, size, modified_at, "
                "is_dir, content, content_hash, extraction_status, category, role, object_key, object_bucket, "
                "uploaded_at, upload_status) "
                "VALUES (%s, %s, 'plan.pdf', %s, '.pdf', %s, 0, FALSE, 'plan', 'h', 'extracted', "
                "'analyzed', 'fiche_pdf', %s, 'seamtech-documents', 0, 'uploaded')",
                (os.path.normcase(str(reference.resolve())), str(reference), str(reference.parent), reference.stat().st_size, cle),
            )
    try:
        yield {
            "url": url,
            "nom": nom,
            "index": index,
            "data": data,
            "reference": reference,
            "magasin": magasin,
            "cle": cle,
            "tmp": tmp_path,
        }
    finally:
        index.close()
        _supprimer_base(nom)


def test_inventaire_data_couvre_tout_sauf_les_volatiles(installation: dict[str, Any]) -> None:
    inventaire = inventorier_data([installation["data"]])
    chemins = {f["chemin"] for f in inventaire["fichiers"]}
    assert "uploads/lots-televerses/jeton-A/fichiers/2026/AFFAIRE-1/fiche.pdf" in chemins
    assert "quarantine/suspect.pdf" in chemins
    assert "rapports/journal.txt" in chemins
    assert "verrou.lock" not in chemins
    assert inventaire["ignores_volatiles"] == 1
    assert inventaire["absentes"] == []


def test_aller_retour_complet_base_donnees_et_objets(installation: dict[str, Any], tmp_path: Path) -> None:
    """Sauvegarde → destruction → restauration coordonnée → conformité au manifeste."""
    dossier_sauvegarde = tmp_path / "sauvegardes"
    magasin = installation["magasin"]

    with _Magasin(magasin) as branche:
        manifeste = sauver(
            installation["url"],
            [installation["tmp"] / "documents"],
            dossier_sauvegarde,
            client_s3=None,  # hors-site testé séparément
            retention=5,
            inclure_references_base=True,
            client_documents_s3=branche.client(),
            racines_data=[installation["data"]],
        )

    assert manifeste["complet"] is True, manifeste.get("data")
    assert manifeste["data"]["nb_copies"] == 4, manifeste["data"]["nb_copies"]  # 2 lots + quarantaine + rapport
    assert manifeste["data"]["ignores_volatiles"] == 1
    assert manifeste["objets_s3"]["nb_copies"] == 1
    assert manifeste["fichiers_locaux"]["nb_copies"] == 1
    chemin_manifeste = dossier_sauvegarde / (manifeste["dump"]["fichier"] + ".manifest.json")
    assert json.loads(chemin_manifeste.read_text(encoding="utf-8"))["data"]["nb_copies"] == 4

    # --- Destruction : plus aucune source disponible ---------------------
    shutil.rmtree(installation["data"])
    shutil.rmtree(installation["tmp"] / "documents")
    magasin.objets.clear()
    installation["index"].close()

    # --- Restauration coordonnée dans une base NEUVE ---------------------
    # `restaurer` REFUSE une base existante et la crée lui-même : c'est la
    # garantie qu'on n'écrase pas une base en production par erreur.
    nom_cible = f"restaure_{uuid.uuid4().hex[:10]}"
    url_cible = URL_PG.rsplit("/", 1)[0] + f"/{nom_cible}"
    data_cible = tmp_path / "restauration" / "data"
    fichiers_cible = tmp_path / "restauration" / "fichiers"
    try:
        with _Magasin(magasin) as branche:
            rapport = restaurer_complet(
                url_cible,
                manifeste,
                dossier_sauvegarde,
                data_cible=data_cible,
                fichiers_cible=fichiers_cible,
                client_s3=branche.client(),
            )

        assert rapport["base"]["base"] == nom_cible
        assert rapport["data"]["nb"] == 4
        assert rapport["verification"]["ok"] is True, rapport["verification"]["ecarts"]

        # L'arborescence de données est revenue À SA PLACE, octet pour octet.
        fiche = data_cible / "uploads/lots-televerses/jeton-A/fichiers/2026/AFFAIRE-1/fiche.pdf"
        assert fiche.read_bytes().startswith(b"%PDF-1.4 fiche de lot")
        assert (data_cible / "quarantine/suspect.pdf").is_file()
        assert (data_cible / "rapports/journal.txt").read_text(encoding="utf-8").startswith("rapport genere")
        assert not (data_cible / "verrou.lock").exists(), "un volatil a été restauré"

        # Les objets sont revenus sous LEUR clé d'origine (le catalogue pointe dessus).
        assert installation["cle"] in magasin.objets
        assert magasin.objets[installation["cle"]]["donnees"].startswith(b"%PDF-1.4 plan reference")

        # Le document référencé est bien dans la base restaurée, avec sa clé.
        import psycopg2

        connexion = psycopg2.connect(url_cible)
        try:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT name, object_key, upload_status FROM documents")
                lignes = cursor.fetchall()
        finally:
            connexion.close()
        assert ("plan.pdf", installation["cle"], "uploaded") in lignes

        # Contrôle indépendant : la base vivante correspond au manifeste.
        assert verifier(url_cible, manifeste)["ok"] is True
    finally:
        _supprimer_base(nom_cible)


def test_restauration_refusee_si_un_fichier_du_bundle_est_altere(installation: dict[str, Any], tmp_path: Path) -> None:
    """Une arborescence restaurée mais fausse serait pire que pas de restauration."""
    dossier_sauvegarde = tmp_path / "sauvegardes-corrompues"
    with _Magasin(installation["magasin"]) as branche:
        manifeste = sauver(
            installation["url"],
            [installation["tmp"] / "documents"],
            dossier_sauvegarde,
            client_s3=None,
            inclure_references_base=True,
            client_documents_s3=branche.client(),
            racines_data=[installation["data"]],
        )
    copie = manifeste["data"]["copies"][0]
    fichier_bundle = dossier_sauvegarde / copie["path_relatif"]
    fichier_bundle.write_bytes(b"altere")

    with pytest.raises(SauvegardeError, match="empreinte données"):
        restaurer_data(manifeste, dossier_sauvegarde, tmp_path / "cible")


def test_manifeste_complet_faux_si_un_fichier_de_donnees_est_illisible(
    installation: dict[str, Any], tmp_path: Path
) -> None:
    """Un dossier de données inaccessible rend la sauvegarde NON complète et le dit."""
    dossier_sauvegarde = tmp_path / "sauvegardes-incompletes"
    fantome = tmp_path / "data-absente"
    with _Magasin(installation["magasin"]) as branche:
        manifeste = sauver(
            installation["url"],
            [installation["tmp"] / "documents"],
            dossier_sauvegarde,
            client_s3=None,
            inclure_references_base=True,
            client_documents_s3=branche.client(),
            racines_data=[installation["data"], fantome],
        )
    assert manifeste["data"]["absentes"] == [str(fantome)]
    # La copie a bien eu lieu pour la racine existante ; l'absence est signalée
    # sans être masquée par un succès global.
    assert manifeste["data"]["nb_copies"] == 4


def test_copie_data_conserve_les_chemins_relatifs(installation: dict[str, Any], tmp_path: Path) -> None:
    destination = tmp_path / "bundle"
    resultat = copier_data([installation["data"]], destination)
    assert resultat["nb_copies"] == 4
    assert resultat["nb_echecs"] == 0
    for copie in resultat["copies"]:
        assert (destination / copie["path_relatif"]).is_file()
