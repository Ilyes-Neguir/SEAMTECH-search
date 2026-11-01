"""P0 — le worker purge la copie locale : câblage réel, pas seulement l'unité.

``tests/test_purge_locale.py`` prouve le protocole fichier par fichier. Ce
fichier prouve le CÂBLAGE : à la fin d'un import dont l'intégrité est prouvée,
le worker appelle bien le protocole, la copie locale disparaît, et le payload
du job dit ce qui a été fait.

Il prouve aussi l'inverse, qui est le point sensible : dès que la preuve
manque (objet absent du stockage, fichier modifié après inventaire, stockage
non configuré), le payload le DIT et RIEN n'est supprimé. Le serveur qui se
remplit en silence était une panne ; un serveur vidé de sa seule copie en est
une autre, pire.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire

CONTENU = b"%PDF-1.4 fiche de fabrication inventoriee"
CLE = "objets/fiche.pdf"


def _sha(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def _magasin_patch(avec_objet: bool = True, *, octets: bytes | None = None) -> tuple[S3EnMemoire, list[Any]]:
    """Patche le transport S3 et, au besoin, dépose l'objet attendu."""
    magasin = S3EnMemoire()
    if avec_objet:
        magasin.put_object(Bucket="seamtech-documents", Key=CLE, Body=octets if octets is not None else CONTENU)
    patchs = [
        patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
        patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
        patch.object(S3StorageClient, "_enable_versioning_once", lambda self: None),
    ]
    return magasin, patchs


class _Installation:
    """Une installation jetable : staging garni, job créé et revendiqué."""

    def __init__(self, tmp_path: Path) -> None:
        from seamtech_search.config import AppConfig
        from seamtech_search.import_pipeline import staging_root
        from seamtech_search.indexer import SearchIndex
        from seamtech_search.jobs import create_job, marquer_job_claim

        self.tmp_path = tmp_path
        self.config = AppConfig(
            root_paths=[tmp_path],
            database_path=tmp_path / "index.db",
            min_free_bytes=0,
            s3_endpoint_url="https://s3.invalide",
            s3_bucket="seamtech-documents",
            s3_access_key="cle-fictive",
            s3_secret_key="secret-fictif",
        )
        self.index = SearchIndex(tmp_path / "index.db")
        self.index.initialize()
        self.source = staging_root(self.config) / "envoi-purge"
        self.source.mkdir(parents=True)
        self.piece = self.source / "fiche.pdf"
        self.piece.write_bytes(CONTENU)
        self.job_id = "job-purge-1"
        create_job(self.index, self.job_id, str(self.source), durability="durable")
        assert marquer_job_claim(self.index, self.job_id, "worker-A")

    def resultat(self, *, sans_cle: bool = False) -> Any:
        """Un ImportResult « intégrité prouvée », tel que la chaîne le produit."""
        from seamtech_search.import_pipeline import ImportFile, ImportResult

        return ImportResult(
            import_id=self.job_id,
            source_path=str(self.source),
            status="completed",
            files_detected=1,
            analyzed_files=1,
            technical_pdf=str(self.piece),
            data={"reference": "X"},
            report_path=None,
            upload_status="uploaded",
            warnings=[],
            files=[
                ImportFile(
                    path=str(self.piece),
                    name="fiche.pdf",
                    category="storage_direct",
                    size=len(CONTENU),
                    extension=".pdf",
                    extraction_status="success",
                    report_path=None,
                    upload_status="uploaded",
                    object_key=None if sans_cle else CLE,
                    object_bucket=None if sans_cle else "seamtech-documents",
                    uploaded_at=None if sans_cle else time.time(),
                    source_sha256=_sha(CONTENU),
                    catalogue_lie=True,
                )
            ],
            all_verified=True,
            transport_verifie=True,
        )

    def executer(self, monkeypatch: pytest.MonkeyPatch, resultat: Any) -> dict[str, Any]:
        from seamtech_search import worker

        monkeypatch.setattr(worker, "import_folder", lambda **_kwargs: resultat)
        return worker.process_import_task(
            {"job_id": self.job_id, "source_path": str(self.source)},
            self.config,
            self.index,
            None,
            worker_id="worker-A",
        )


@pytest.fixture()
def installation(tmp_path: Path) -> Iterator[_Installation]:
    inst = _Installation(tmp_path)
    try:
        yield inst
    finally:
        inst.index.close()


def test_apres_preuve_la_copie_locale_est_purgee(installation: _Installation, monkeypatch: pytest.MonkeyPatch) -> None:
    _, patchs = _magasin_patch()
    for p in patchs:
        p.start()
    try:
        reponse = installation.executer(monkeypatch, installation.resultat())
    finally:
        for p in patchs:
            p.stop()

    nettoyage = reponse["cleanup"]
    assert nettoyage["purge"] is True, nettoyage
    assert nettoyage["nb_supprimes"] == 1
    assert nettoyage["octets_liberes"] == len(CONTENU)
    assert nettoyage["dossier_supprime"] is True
    assert not installation.piece.exists()
    assert not installation.source.exists()


def test_objet_absent_la_copie_locale_reste(installation: _Installation, monkeypatch: pytest.MonkeyPatch) -> None:
    _, patchs = _magasin_patch(avec_objet=False)
    for p in patchs:
        p.start()
    try:
        reponse = installation.executer(monkeypatch, installation.resultat())
    finally:
        for p in patchs:
            p.stop()

    nettoyage = reponse["cleanup"]
    assert nettoyage["purge"] is False
    assert nettoyage["nb_supprimes"] == 0
    assert installation.piece.read_bytes() == CONTENU


def test_objet_corrompu_la_copie_locale_reste(installation: _Installation, monkeypatch: pytest.MonkeyPatch) -> None:
    magasin, patchs = _magasin_patch(octets=b"OCTETS TRONQUES")
    magasin.corrompre(CLE, octets=b"TRONQUE")
    for p in patchs:
        p.start()
    try:
        reponse = installation.executer(monkeypatch, installation.resultat())
    finally:
        for p in patchs:
            p.stop()

    nettoyage = reponse["cleanup"]
    assert nettoyage["purge"] is False
    assert len(nettoyage["objets_illisibles"]) == 1
    assert installation.piece.read_bytes() == CONTENU


def test_fichier_modifie_apres_inventaire_est_conserve(installation: _Installation, monkeypatch: pytest.MonkeyPatch) -> None:
    _, patchs = _magasin_patch()
    # L'objet correspond aux anciens octets ; le disque porte les nouveaux.
    installation.piece.write_bytes(b"%PDF-1.4 version modifiee apres inventaire")
    for p in patchs:
        p.start()
    try:
        reponse = installation.executer(monkeypatch, installation.resultat())
    finally:
        for p in patchs:
            p.stop()

    nettoyage = reponse["cleanup"]
    assert nettoyage["purge"] is False
    assert len(nettoyage["modifies"]) == 1
    assert installation.piece.exists()


def test_stockage_non_configure_rien_n_est_supprime(installation: _Installation, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans destination durable, la copie locale est la SEULE copie."""
    from seamtech_search.config import AppConfig

    installation.config = AppConfig(
        root_paths=[installation.tmp_path],
        database_path=installation.tmp_path / "index-sans-s3.db",
        min_free_bytes=0,
    )
    # Aucune destination : aucun objet n'a été envoyé, donc aucune clé.
    reponse = installation.executer(monkeypatch, installation.resultat(sans_cle=True))

    nettoyage = reponse["cleanup"]
    assert nettoyage["purge"] is False
    assert "sans clé" in nettoyage["raison"] or "destination" in nettoyage["raison"], nettoyage["raison"]
    assert nettoyage["nb_supprimes"] == 0
    assert installation.piece.read_bytes() == CONTENU


def test_payload_declare_uploaded_sans_moyen_de_verifier_rien_ne_part(
    installation: _Installation, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ultime filet : un payload qui SE DIT vérifié, sans stockage pour le prouver.

    La chaîne peut annoncer ``uploaded`` avec une clé ; si le stockage est
    absent de la configuration, il n'existe aucun moyen de relire l'objet. Ce
    n'est pas « on ne sait pas » qui décide — c'est « je ne peux pas vérifier »
    qui interdit. Rien n'est supprimé, et la raison est dite.
    """
    from seamtech_search.config import AppConfig

    installation.config = AppConfig(
        root_paths=[installation.tmp_path],
        database_path=installation.tmp_path / "index-sans-s3.db",
        min_free_bytes=0,
    )
    reponse = installation.executer(monkeypatch, installation.resultat())

    nettoyage = reponse["cleanup"]
    assert nettoyage["purge"] is False
    assert nettoyage["verification_indisponible"] is True
    assert nettoyage["nb_supprimes"] == 0
    assert installation.piece.read_bytes() == CONTENU
