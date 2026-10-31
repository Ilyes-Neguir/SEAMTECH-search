"""Réimport sans écrasement d'un document déjà lié à une fiche."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.fiches import depot
from seamtech_search.import_pipeline import import_folder
from tests.test_depot_transactionnel import ARCHIVE_7792, base_depot  # noqa: F401

pytestmark = pytest.mark.postgres


def test_reimport_identique_preserve_references_et_metadonnees(base_depot, tmp_path: Path):  # noqa: F811
    index = base_depot['index']
    source = tmp_path / 'archive'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    resultat = depot.deposer_dossier(index, source)
    assert resultat['statut'] == 'traite'
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id, content_hash, object_key, object_bucket, role, id_fiche FROM documents WHERE path = %s', (str(pdf),))
            avant = curseur.fetchone()
            assert avant is not None
    config = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db',
                       database_url=base_depot['url'], min_free_bytes=0)
    resultat_import = import_folder(source=source, config=config, index=index, import_id='reimport-identique')
    # Le hash historique de texte/octets est ambigu : malgré un contenu
    # identique, pas de backfill ni de liaison supposée au document protégé.
    assert resultat_import.status == 'needs_review' and not resultat_import.all_verified
    assert resultat_import.files[0].catalogue_lie is False
    assert any('catalogue' in avertissement.lower() for avertissement in resultat_import.warnings)
    with index.connect() as connexion:
        with connexion.cursor() as curseur:
            curseur.execute('SELECT id, content_hash, object_key, object_bucket, role, id_fiche FROM documents WHERE path = %s', (str(pdf),))
            assert curseur.fetchone() == avant


def test_reimport_conflit_publie_sans_remplacer_la_piece_protegee(base_depot, tmp_path: Path, monkeypatch):  # noqa: F811
    from seamtech_search.storage import S3StorageClient
    from tests.s3_en_memoire import S3EnMemoire

    index = base_depot['index']
    source = tmp_path / 'archive'
    source.mkdir()
    pdf = source / 'fiche.pdf'
    shutil.copy(ARCHIVE_7792 / 'fiche-7792-SO_ffab.pdf', pdf)
    assert depot.deposer_dossier(index, source)['statut'] == 'traite'
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT id, content_hash, source_sha256, object_key, role, id_fiche FROM documents WHERE path = %s',
                        (str(pdf),))
        avant = curseur.fetchone()
    assert avant and avant[2] is None
    # La nouvelle version ne doit ni être reliée arbitrairement à l'ancien
    # document ni empêcher la conservation de son objet propre.
    pdf.write_bytes(pdf.read_bytes() + b'\n%version-synthetique-modifiee')
    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **_kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    cfg = AppConfig(root_paths=[source], database_path=tmp_path / 'index.db', database_url=base_depot['url'],
                    min_free_bytes=0, s3_endpoint_url='https://s3.invalide', s3_bucket='bucket-synthetique',
                    s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')
    resultat = import_folder(source, cfg, index, import_id='reimport-conflit')
    with index.connect() as connexion, connexion.cursor() as curseur:
        curseur.execute('SELECT id, content_hash, source_sha256, object_key, role, id_fiche FROM documents WHERE path = %s',
                        (str(pdf),))
        assert curseur.fetchone() == avant
    fichier = resultat.files[0]
    assert fichier.object_key and fichier.catalogue_lie is False
    assert magasin.objets[fichier.object_key]['donnees'] == pdf.read_bytes()
    assert resultat.status == 'needs_review' and not resultat.all_verified
    assert any('catalogue' in avertissement.lower() for avertissement in resultat.warnings)
