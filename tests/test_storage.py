import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from seamtech_search.config import AppConfig
from seamtech_search.import_pipeline import import_folder
from seamtech_search.indexer import SearchIndex
from seamtech_search.storage import S3StorageClient, StorageError, upload_artifacts_to_storage
from tests.s3_en_memoire import S3EnMemoire


def _missing_object(*args: object, **kwargs: object) -> dict:
    """What ``head_object`` answers for a key that is not in the bucket."""
    raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


def _store_and_return_key(*args: object, **kwargs: object) -> str:
    """A test double that honours the real ``upload_file`` contract.

    The real method never overwrites an occupied key and returns the key it
    actually used; callers record that key, so a double returning ``None`` (or a
    ``MagicMock``) would test a contract that does not exist.
    """
    remote_key = kwargs.get("remote_key") or (args[1] if len(args) > 1 else None)
    assert remote_key, "upload_file was called without an object key"
    return str(remote_key)


def test_s3_storage_client_configuration() -> None:
    config = AppConfig(
        root_paths=[Path(".")],
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="test-bucket",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_region="us-east-1",
        min_free_bytes=0,
    )
    client = S3StorageClient(config=config)
    assert client.is_configured() is True
    assert client.bucket_name == "test-bucket"
    assert client.endpoint_url == "http://localhost:9000"


def test_s3_storage_upload_and_presigned_url(tmp_path: Path) -> None:
    test_file = tmp_path / "plan.pdf"
    test_file.write_bytes(b"%PDF-1.4 test content")

    client = S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )

    mock_boto = MagicMock()
    mock_boto.head_object.side_effect = _missing_object
    mock_boto.generate_presigned_url.return_value = "https://minio.local/seamtech-documents/plan.pdf?token=abc"

    with patch.object(client, "_get_client", return_value=mock_boto):
        with patch.object(client, "ensure_bucket_exists"):
            key = client.upload_file(test_file, remote_key="jobs/plan.pdf")
            assert key == "jobs/plan.pdf"
            mock_boto.upload_file.assert_called_once()
            # The key was checked for occupancy before writing to it.
            mock_boto.head_object.assert_called_once_with(Bucket="seamtech-documents", Key="jobs/plan.pdf")

            url = client.get_presigned_url(key)
            assert "token=abc" in url


def test_s3_storage_upload_bytes() -> None:
    client = S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )

    mock_boto = MagicMock()
    mock_boto.head_object.side_effect = _missing_object
    with patch.object(client, "_get_client", return_value=mock_boto):
        with patch.object(client, "ensure_bucket_exists"):
            key = client.upload_bytes(b"sample data", remote_key="data.txt")
            assert key == "data.txt"
            mock_boto.put_object.assert_called_once()


def test_s3_storage_nonexistent_file_raises(tmp_path: Path) -> None:
    client = S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )
    with pytest.raises(StorageError):
        client.upload_file(tmp_path / "does_not_exist.pdf")


class _S3Branche:
    """Branche le client S3 réel sur un magasin en mémoire.

    Les anciens doubles répondaient « l'objet existe » : depuis que la
    vérification relit les OCTETS et recalcule l'empreinte, ce double ne
    testait plus rien de réel. Ici les octets sont conservés.
    """

    def __init__(self) -> None:
        self.magasin = S3EnMemoire()
        self._patchs: list = []

    def __enter__(self) -> S3EnMemoire:
        magasin = self.magasin
        self._patchs = [
            patch.object(S3StorageClient, "_get_client", lambda _instance, probe_timeout=None: magasin),
            patch.object(S3StorageClient, "ensure_bucket_exists", lambda _instance: True),
            patch.object(S3StorageClient, "first_free_key", lambda _instance, cle: cle),
        ]
        for patcheur in self._patchs:
            patcheur.start()
        return self.magasin

    def __exit__(self, *_exc: object) -> None:
        for patcheur in self._patchs:
            patcheur.stop()


def test_upload_artifacts_to_storage_helper(tmp_path: Path) -> None:
    file1 = tmp_path / "doc.pdf"
    file1.write_bytes(b"test pdf")
    file2 = tmp_path / "table.xlsx"
    file2.write_bytes(b"test xlsx")

    config = AppConfig(
        root_paths=[tmp_path],
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="seamtech-bucket",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        min_free_bytes=0,
    )

    with _S3Branche() as magasin:
        batch = upload_artifacts_to_storage(
            folder_name="REF-001",
            files_to_upload=[file1, file2],
            config=config,
            import_id="REF-001",
            source_root=tmp_path,
        )

        assert batch.status == "uploaded"
        assert batch.all_verified is True
        assert len(magasin.envois) == 2
        # La vérification a réellement relu les octets des deux objets.
        assert {a.verification for a in batch.artifacts} == {"relecture_sha256"}
        # Purge locale autorisée seulement sur preuve : le worker consulte
        # `all_verified`, jamais le simple fait que `upload_file` n'a pas levé.
        for key in [a.key for a in batch.artifacts]:
            assert magasin.objets[key]["donnees"]
        assert [a.name for a in batch.artifacts] == ["doc.pdf", "table.xlsx"]
        assert all(a.verified and a.key and a.bucket == "seamtech-bucket" for a in batch.artifacts)
        # Every file is keyed by import id + a hash of its own path, so two
        # same-named files can never share an object.
        for artifact in batch.artifacts:
            assert artifact.key is not None
            assert artifact.key.startswith("REF-001/")
            assert artifact.key.endswith(f"/{artifact.name}")


def test_import_with_s3_storage_uploads_all_files(tmp_path: Path) -> None:
    source_folder = tmp_path / "reference_order"
    source_folder.mkdir()

    fixture_pdf = Path("sample_data/CLIENT-123/fiche-technique.pdf")
    (source_folder / "fiche-technique.pdf").write_bytes(fixture_pdf.read_bytes())
    (source_folder / "plan_de_coupe.dwg").write_bytes(b"CAD_DWG_BINARY_MOCK")
    (source_folder / "notes.txt").write_text("Assembly instructions")

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        s3_endpoint_url="http://localhost:9000",
        s3_bucket="seamtech-documents",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        min_free_bytes=0,
    )
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    with _S3Branche() as magasin:
        result = import_folder(source_folder, config, index)
        mock_upload = magasin
        assert result.status == "needs_review"
        # Transport intégralement prouvé (5 objets, relus et comparés) ⇒
        # « uploaded ». « partial » n'annonce pas un échec : il dit que la
        # préservation n'est PAS prouvée, ce qui était faux ici (et rendait
        # impossible toute qualification d'envoi réussi — voir la recette sur
        # services réels). Le succès GLOBAL, lui, reste suspendu : voir
        # `all_verified` ci-dessous.
        assert result.upload_status == "uploaded"
        assert result.transport_verifie and not result.all_verified
        assert all(a["verified"] for a in result.artifacts)
        # 3 source files (pdf, dwg, txt) + 2 reports (pdf, docx) = 5 uploaded files
        assert len(mock_upload.envois) == 5
        # The keys are namespaced by this import: the same folder imported again
        # cannot land on top of these objects.
        # Note: result.files are ImportFile, artifacts are in result? Actually
        # import_folder returns ImportResult which has upload_status but not artifacts.
        # We check the upload calls for uniqueness.
        called_keys = [envoi["key"] for envoi in mock_upload.envois]
        assert len(set(called_keys)) == len(called_keys), "every artifact has its own object key"
        assert all(str(key).startswith(f"{result.import_id}/") for key in called_keys)


def test_multi_technical_pdf_import_analyzes_all_sheets(tmp_path: Path) -> None:
    source_folder = tmp_path / "multi_spec_order"
    source_folder.mkdir()

    fixture_pdf = Path("sample_data/CLIENT-123/fiche-technique.pdf")
    (source_folder / "fiche_principale.pdf").write_bytes(fixture_pdf.read_bytes())
    (source_folder / "fiche_secondaire.pdf").write_bytes(fixture_pdf.read_bytes())

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
    )
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)

    result = import_folder(source_folder, config, index)
    assert result.status == "completed"
    assert result.analyzed_files == 2
    assert result.data is not None
    assert len(result.data.get("additional_sheets", [])) == 1
    assert result.data["additional_sheets"][0]["filename"] == "fiche_secondaire.pdf"
    # Verify report generated successfully
    assert result.report_path and Path(result.report_path).exists()
    assert result.report_docx_path and Path(result.report_docx_path).exists()


@pytest.mark.s3
def test_live_minio_s3_integration(tmp_path: Path) -> None:
    endpoint = os.environ.get("SEAMTECH_TEST_S3_URL")
    if not endpoint:
        pytest.skip("SEAMTECH_TEST_S3_URL not set (live MinIO integration skipped)")

    access_key = os.environ.get("SEAMTECH_S3_ACCESS_KEY", "minioadmin")
    secret_key = os.environ.get("SEAMTECH_S3_SECRET_KEY", "minioadmin")

    client = S3StorageClient(
        endpoint_url=endpoint,
        bucket_name="seamtech-integration-test",
        access_key_id=access_key,
        secret_access_key=secret_key,
    )

    test_doc = tmp_path / "live_spec.pdf"
    test_doc.write_bytes(b"%PDF-1.4 Live MinIO Integration Test Document")

    # Une clé unique évite qu'une exécution précédente fasse échouer ce test
    # par la protection anti-écrasement ; on vérifie toujours la clé exacte.
    from uuid import uuid4

    requested_key = f"integration/live_spec-{uuid4().hex}.pdf"
    key = client.upload_file(test_doc, remote_key=requested_key)
    try:
        assert key == requested_key
        download_target = tmp_path / "downloaded.pdf"
        client.download_file(key, download_target)
        assert download_target.exists()
        assert download_target.read_bytes() == test_doc.read_bytes()

        presigned = client.get_presigned_url(key)
        assert "seamtech-integration-test" in presigned
        assert "X-Amz-Signature" in presigned or "Signature" in presigned
    finally:
        assert client.delete_file(key), "nettoyage de l'objet de test impossible"


def _client_minimal() -> S3StorageClient:
    return S3StorageClient(
        endpoint_url="http://localhost:9000",
        bucket_name="seamtech-documents",
        access_key_id="test",
        secret_access_key="test",
    )


def test_storage_list_keys_pages_and_prefix() -> None:
    """list_keys (rétention des sauvegardes, Lot H.1) : pagine, applique le
    préfixe applicatif et renvoie les clés triées."""
    client = _client_minimal()
    client.prefix = "docs"
    mock_boto = MagicMock()
    paginateur = MagicMock()
    paginateur.paginate.return_value = iter(
        [
            {"Contents": [{"Key": "docs/backups/b.dump"}, {"Key": "docs/backups/a.dump"}]},
            {"Contents": [{"Key": "docs/backups/a.dump.manifest.json"}]},
            {},  # page vide — ne doit ni casser ni inventer de clés
        ]
    )
    mock_boto.get_paginator.return_value = paginateur
    with patch.object(client, "_get_client", return_value=mock_boto):
        cles = client.list_keys("backups/")
    assert cles == [
        "docs/backups/a.dump",
        "docs/backups/a.dump.manifest.json",
        "docs/backups/b.dump",
    ]
    mock_boto.get_paginator.assert_called_once_with("list_objects_v2")
    paginateur.paginate.assert_called_once_with(Bucket="seamtech-documents", Prefix="docs/backups/")


def test_storage_list_keys_failure_raises_storage_error() -> None:
    """Un listing qui échoue doit lever StorageError — la rétention ne doit
    jamais croire à tort que le bucket est vide."""
    from seamtech_search.storage import StorageError

    client = _client_minimal()
    mock_boto = MagicMock()
    paginateur = MagicMock()
    paginateur.paginate.side_effect = RuntimeError("bucket injoignable")
    mock_boto.get_paginator.return_value = paginateur
    with patch.object(client, "_get_client", return_value=mock_boto):
        with pytest.raises(StorageError, match="List failed"):
            client.list_keys("backups/")


def test_le_chemin_relatif_d_origine_voyage_avec_l_objet(tmp_path: Path) -> None:
    """Un objet doit permettre de retrouver D'OÙ il vient, sans la base.

    L'empreinte du chemin est dans la clé (non inversable) et le nom du fichier
    en fin de clé : sans métadonnée, le dossier d'origine était perdu si l'on ne
    consultait que le bucket. ``seamtech-chemin`` le conserve, encodé en
    pourcentage — les en-têtes S3 n'acceptent pas d'octets non-ASCII.
    """
    from seamtech_search.storage import METADATA_CHEMIN, chemin_relatif_origine

    dossier = tmp_path / "AFFAIRE 12" / "plan été"
    dossier.mkdir(parents=True)
    fichier = dossier / "Plan n°2.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")

    assert chemin_relatif_origine(fichier, tmp_path) == "AFFAIRE 12/plan été/Plan n°2.pdf"
    # Un fichier hors de la racine (rapport généré) retombe sur son nom.
    assert chemin_relatif_origine(fichier, tmp_path / "ailleurs") == "Plan n°2.pdf"

    client = S3StorageClient(
        endpoint_url="http://minio.invalide:9000",
        bucket_name="seamtech-documents",
        access_key_id="cle-fictive",
        secret_access_key="secret-fictif",
    )
    faux_boto = MagicMock()
    with patch.object(client, "_get_client", return_value=faux_boto):
        with patch.object(client, "ensure_bucket_exists"):
            with patch.object(client, "first_free_key", side_effect=lambda cle: cle):
                client.upload_file(
                    fichier,
                    remote_key="IMP-1/abc/Plan n°2.pdf",
                    relative_path=chemin_relatif_origine(fichier, tmp_path),
                )
    metadonnees = faux_boto.upload_file.call_args.kwargs["ExtraArgs"]["Metadata"]
    # URL-encodé : ni espace, ni « ° », ni accent dans l'en-tête HTTP…
    assert metadonnees[METADATA_CHEMIN] == "AFFAIRE%2012/plan%20%C3%A9t%C3%A9/Plan%20n%C2%B02.pdf"
    # …et les séparateurs de dossier restent lisibles.
    assert "/" in metadonnees[METADATA_CHEMIN]


@pytest.mark.parametrize("operation", ["head_object", "download_file"])
def test_lecture_s3_erreur_ne_divulgue_ni_cle_ni_secret(tmp_path, caplog, operation):
    from seamtech_search.storage import S3StorageClient, StorageError

    class S3:
        def head_object(self, **_kwargs):
            raise OSError("secret-synthetique")
        def download_file(self, **_kwargs):
            raise OSError("secret-synthetique")
    client = S3StorageClient(bucket_name="test-synthetique")
    client._s3 = S3()
    cle = "nom-sensible-synthetique"
    with pytest.raises(StorageError) as erreur:
        if operation == "head_object":
            client.head_object(cle)
        else:
            client.download_file(cle, tmp_path / "fichier")
    assert "secret-synthetique" not in str(erreur.value)
    assert "secret-synthetique" not in caplog.text
    assert cle not in caplog.text
    assert erreur.value.__cause__ is None


def test_telechargement_version_est_transmis_au_transport(tmp_path: Path) -> None:
    client = _client_minimal()
    transport = MagicMock()
    with patch.object(client, "_get_client", return_value=transport):
        destination = tmp_path / "piece"
        client.download_file("cle", destination, version_id="version-synthetique")
        transport.download_file.assert_called_once_with(
            Bucket=client.bucket_name, Key="cle", Filename=str(destination),
            ExtraArgs={"VersionId": "version-synthetique"},
        )


@pytest.mark.parametrize("mode", ["fichier", "octets"])
def test_envoi_ambigu_ne_rejoue_pas_et_ne_divulgue_pas(tmp_path: Path, caplog, mode: str) -> None:
    """Une réponse perdue ne prouve pas que l'écriture a échoué côté S3."""
    from unittest.mock import patch

    fichier = tmp_path / "nom-synthetique-confidentiel.pdf"
    fichier.write_bytes(b"donnees")
    client = _client_minimal()
    s3 = MagicMock()
    appels = []
    def envoyer(**kwargs):
        appels.append(kwargs)
        if len(appels) == 1:
            raise TimeoutError("secret-synthetique")
        pytest.fail("un second PUT applicatif pourrait écraser un objet concurrent")
    if mode == "fichier":
        s3.upload_file.side_effect = envoyer
    else:
        s3.put_object.side_effect = envoyer
    with patch.object(client, "_get_client", return_value=s3), patch.object(client, "ensure_bucket_exists"):
        with pytest.raises(StorageError) as erreur:
            if mode == "fichier":
                client.upload_file(fichier, "cle-synthetique-confidentielle", avoid_overwrite=False)
            else:
                client.upload_bytes(b"donnees", "cle-synthetique-confidentielle", avoid_overwrite=False)
    assert len(appels) == 1
    assert "secret-synthetique" not in str(erreur.value) + caplog.text
    assert "cle-synthetique-confidentielle" not in str(erreur.value) + caplog.text
    assert "nom-synthetique-confidentiel" not in str(erreur.value) + caplog.text


@pytest.mark.parametrize("operation", ["signature", "lecture", "liste", "suppression", "relecture"])
def test_erreurs_operations_s3_expurgees(tmp_path: Path, caplog, operation: str) -> None:
    """Ni clé, nom ni message tiers (potentiellement URL/secret) ne sortent."""
    client = _client_minimal()
    transport = MagicMock()
    panne = OSError("secret-synthetique")
    transport.generate_presigned_url.side_effect = panne
    transport.get_object.side_effect = panne
    transport.get_paginator.side_effect = panne
    transport.delete_object.side_effect = panne
    fichier = tmp_path / "piece"
    fichier.write_bytes(b"contenu")
    cle = "cle-synthetique-confidentielle"
    with patch.object(client, "_get_client", return_value=transport):
        if operation == "suppression":
            assert client.delete_file(cle) is False
            retour = ""
        elif operation == "relecture":
            transport.head_object.return_value = {"ContentLength": len(b"contenu")}
            transport.get_object.side_effect = None
            corps = MagicMock()
            corps.read.side_effect = panne
            transport.get_object.return_value = {"Body": corps}
            result = client.verifier_integrite(cle, fichier)
            assert result.integrite_prouvee is False
            retour = result.detail
        else:
            with pytest.raises(StorageError) as erreur:
                if operation == "signature":
                    client.get_presigned_url(cle)
                elif operation == "lecture":
                    client.get_object(cle)
                else:
                    client.list_keys(cle)
            retour = str(erreur.value)
    assert "secret-synthetique" not in retour + caplog.text
    assert cle not in retour + caplog.text


@pytest.mark.parametrize("operation", ["creation_client", "client_sonde", "verification_bucket", "versioning",
                                       "cle_occupee", "objet_existe", "cle_epuisee", "statut_non_reconnu"])
def test_diagnostics_s3_de_configuration_et_sonde_sont_expurges(caplog, operation: str) -> None:
    client = _client_minimal()
    transport = MagicMock()
    panne = OSError("secret-synthetique")
    cle = "cle-synthetique-confidentielle"
    retour = ""
    with patch.object(client, "_get_client", return_value=transport):
        if operation == "verification_bucket":
            transport.head_bucket.side_effect = panne
            with patch.object(client, "_enable_versioning_once"):
                client.ensure_bucket_exists()
        elif operation == "versioning":
            with patch.object(client, "_get_probe_client", side_effect=panne):
                retour = str(client.versioning_status(refresh=True))
        elif operation == "cle_occupee":
            transport.head_object.side_effect = panne
            with pytest.raises(StorageError) as erreur:
                client.object_key_taken(cle)
            retour = str(erreur.value)
        elif operation == "objet_existe":
            transport.head_object.side_effect = panne
            assert client.object_exists(cle) is False
        elif operation == "cle_epuisee":
            with patch.object(client, "object_key_taken", return_value=True):
                with pytest.raises(StorageError) as erreur:
                    client.first_free_key(cle, max_candidates=1)
            retour = str(erreur.value)
        elif operation == "statut_non_reconnu":
            sonde = MagicMock()
            sonde.get_bucket_versioning.return_value = {"Status": "secret-synthetique"}
            with patch.object(client, "_get_probe_client", return_value=sonde):
                etat = client.versioning_status(refresh=True)
            assert etat["versioning_available"] is None
            retour = str(etat)
        else:
            with patch.object(client, "_make_client", side_effect=panne):
                with pytest.raises(StorageError) as erreur:
                    if operation == "creation_client":
                        # La méthode est patchée au niveau de l'instance plus haut.
                        type(client)._get_client(client)
                    else:
                        client._get_probe_client()
                retour = str(erreur.value)
    assert "secret-synthetique" not in retour + caplog.text
    assert cle not in retour + caplog.text


def test_import_sync_s3_ne_declare_pas_termine_sans_snapshot(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from tests.test_import_workflow import make_source

    source = make_source(tmp_path)
    cfg = AppConfig(root_paths=[tmp_path / 'roots'], database_path=tmp_path / 'data' / 'search.db',
                    min_free_bytes=0, s3_endpoint_url='https://s3.invalide', s3_bucket='bucket-test',
                    s3_access_key='cle-synthetique', s3_secret_key='secret-synthetique')
    magasin = S3EnMemoire()
    monkeypatch.setattr(S3StorageClient, '_get_client', lambda self, **kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, 'ensure_bucket_exists', lambda self: True)
    client = TestClient(create_app(cfg))
    reponse = client.post('/imports?wait=true', json={'source_path': str(source)})
    assert reponse.status_code == 200
    data = reponse.json()
    assert data['transport_verifie'] and not data['all_verified']
    assert data['status'] == 'needs_review'
    assert client.get('/imports/' + data['import_id']).json()['status'] == 'needs_review'


# ---------------------------------------------------------------------------
# Création exclusive et preuves de relecture : chemins d'erreur réellement
# exécutés (aucun n'est un « beau temps » — chacun est un refus exigé).
# ---------------------------------------------------------------------------


def _client_double(monkeypatch, magasin, tmp_path: Path) -> S3StorageClient:
    cfg = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "data" / "search.db",
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="bucket-test",
        s3_access_key="cle-synthetique",
        s3_secret_key="secret-synthetique",
    )
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda self, **kwargs: magasin)
    monkeypatch.setattr(S3StorageClient, "ensure_bucket_exists", lambda self: True)
    return S3StorageClient(config=cfg)


def test_creation_exclusive_refuse_cle_occupee_sans_rien_ecraser(tmp_path: Path, monkeypatch) -> None:
    """La clé existe déjà : refus net et AUCUNE écriture (la copie vivante survit)."""
    from seamtech_search.storage import StorageCleExistante

    magasin = S3EnMemoire()
    client = _client_double(monkeypatch, magasin, tmp_path)
    source = tmp_path / "piece.pdf"
    source.write_bytes(b"contenu-deja-en-place")
    assert client.creer_fichier_exclusif(source, "imports/piece.pdf") == "imports/piece.pdf"

    autre = tmp_path / "autre.pdf"
    autre.write_bytes(b"contenu-different-et-plus-long")
    with pytest.raises(StorageCleExistante):
        client.creer_fichier_exclusif(autre, "imports/piece.pdf")
    assert magasin.objets["imports/piece.pdf"]["donnees"] == b"contenu-deja-en-place"


def test_creation_exclusive_sans_condition_ne_bascule_pas_en_ecriture(tmp_path: Path, monkeypatch) -> None:
    """Fournisseur sans écriture conditionnelle : refus, jamais de PUT inconditionnel."""
    from botocore.exceptions import ParamValidationError

    from seamtech_search.storage import StockageExclusiviteIndisponible

    class SansCondition:
        def put_object(self, **_kwargs):
            raise ParamValidationError(report="IfNoneMatch")

    client = _client_double(monkeypatch, SansCondition(), tmp_path)
    source = tmp_path / "piece.pdf"
    source.write_bytes(b"contenu")
    with pytest.raises(StockageExclusiviteIndisponible):
        client.creer_fichier_exclusif(source, "imports/piece.pdf")


def test_creation_exclusive_incertaine_est_signalee(tmp_path: Path, monkeypatch) -> None:
    """Une panne inconnue laisse l'état distant incertain : jamais un succès déclaré."""
    from seamtech_search.storage import StorageWriteUncertain

    class Panne:
        def put_object(self, **_kwargs):
            raise RuntimeError("reseau coupe en cours d'ecriture")

    client = _client_double(monkeypatch, Panne(), tmp_path)
    source = tmp_path / "piece.pdf"
    source.write_bytes(b"contenu")
    with pytest.raises(StorageWriteUncertain):
        client.creer_fichier_exclusif(source, "imports/piece.pdf")


def test_creation_exclusive_fichier_local_absent(tmp_path: Path, monkeypatch) -> None:
    """Sans fichier local, aucune tentative d'écriture."""
    from seamtech_search.storage import StoragePreflightError

    client = _client_double(monkeypatch, S3EnMemoire(), tmp_path)
    with pytest.raises(StoragePreflightError):
        client.creer_fichier_exclusif(tmp_path / "introuvable.pdf", "imports/piece.pdf")


def test_versioning_deja_actif_n_est_pas_redemande(tmp_path: Path, monkeypatch) -> None:
    """Un bucket déjà versionné n'entraîne aucune demande de configuration."""
    cfg = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "data" / "search.db",
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="bucket-test",
        s3_access_key="cle-synthetique",
        s3_secret_key="secret-synthetique",
    )
    monkeypatch.setattr(
        S3StorageClient,
        "_read_versioning",
        lambda self: {"versioning_available": True, "versioning_detail": "bucket versioning is enabled"},
    )
    client = S3StorageClient(config=cfg)
    client._enable_versioning_once()
    etat = client.versioning_status()
    assert etat["versioning_available"] is True


def test_head_object_avec_checksum_illisitable(tmp_path: Path, monkeypatch) -> None:
    """Unhead illisible remonte en erreur explicite, sans divulguer la clé."""

    class Illisible:
        def head_object(self, **_kwargs):
            raise RuntimeError("panne de lecture")

    client = _client_double(monkeypatch, Illisible(), tmp_path)
    with pytest.raises(StorageError):
        client.head_object_avec_checksum("imports/piece.pdf")


def _magasin_trompeur(taille_annoncee: int, corps: bytes):
    """Tête conforme, corps réellement different : le pire cas d'un envoi."""
    from io import BytesIO

    class Magasin:
        def head_object(self, Bucket, Key, ChecksumMode=None, **_kwargs):  # noqa: N803, ANN201
            return {"ContentLength": taille_annoncee, "ETag": '"etag-quelconque"'}

        def get_object(self, Bucket, Key, **_kwargs):  # noqa: N803, ANN201
            return {"Body": BytesIO(corps)}

    return Magasin()


def test_relecture_corps_plus_long_que_la_source_est_un_echec(tmp_path: Path, monkeypatch) -> None:
    """Le service renvoie plus d'octets que la source : échec, pas une troncature acceptée."""
    local = tmp_path / "piece.bin"
    local.write_bytes(b"0123456789")
    client = _client_double(monkeypatch, _magasin_trompeur(10, b"0123456789-EN-TROP"), tmp_path)
    resultat = client.verifier_integrite("imports/piece.bin", local)
    assert resultat.integrite_prouvee is False and resultat.methode == "echec"
    assert "supérieure" in (resultat.detail or "")


def test_relecture_corps_plus_court_que_la_source_est_un_echec(tmp_path: Path, monkeypatch) -> None:
    """Objet tronqué : la taille annoncée ne suffit pas, les octets comptent."""
    local = tmp_path / "piece.bin"
    local.write_bytes(b"0123456789")
    client = _client_double(monkeypatch, _magasin_trompeur(10, b"0123"), tmp_path)
    resultat = client.verifier_integrite("imports/piece.bin", local)
    assert resultat.integrite_prouvee is False and resultat.methode == "echec"
    assert "inférieure" in (resultat.detail or "")


def test_checksum_serveur_different_est_un_echec(tmp_path: Path, monkeypatch) -> None:
    """Le fournisseur annonce un checksum qui n'est pas celui du fichier : échec."""
    import base64
    import hashlib

    class Magasin:
        def head_object(self, Bucket, Key, ChecksumMode=None, **_kwargs):  # noqa: N803, ANN201
            return {
                "ContentLength": 10,
                "ETag": '"etag-quelconque"',
                "ChecksumSHA256": base64.b64encode(hashlib.sha256(b"autre-chose").digest()).decode("ascii"),
                "ChecksumType": "FULL_OBJECT",
            }

    local = tmp_path / "piece.bin"
    local.write_bytes(b"0123456789")
    client = _client_double(monkeypatch, Magasin(), tmp_path)
    resultat = client.verifier_integrite("imports/piece.bin", local, relire=False)
    assert resultat.integrite_prouvee is False and resultat.methode == "echec"
    assert "checksum serveur" in (resultat.detail or "")


def test_envoi_sans_fichier_n_est_pas_un_envoi(tmp_path: Path) -> None:
    """Aucun fichier à envoyer : « not_applicable », pas « uploaded » vide de sens."""
    cfg = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "data" / "search.db",
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="bucket-test",
        s3_access_key="cle-synthetique",
        s3_secret_key="secret-synthetique",
    )
    lot = upload_artifacts_to_storage(folder_name="vide", files_to_upload=[None], config=cfg)
    assert lot.status == "not_applicable"


def test_panne_d_initialisation_deja_typee_n_est_pas_requalifiee(tmp_path: Path, monkeypatch) -> None:
    """Un ``StorageError`` explicite traverse le client tel quel : pas de message générique par-dessus."""
    from seamtech_search.storage import StorageError

    cfg = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "data" / "search.db",
        min_free_bytes=0,
        s3_endpoint_url="https://s3.invalide",
        s3_bucket="bucket-test",
        s3_access_key="cle-synthetique",
        s3_secret_key="secret-synthetique",
    )

    def _echouer(self) -> None:
        raise StorageError("identifiants refusés par le fournisseur")

    monkeypatch.setattr(S3StorageClient, "_make_client", _echouer)
    client = S3StorageClient(config=cfg)
    with pytest.raises(StorageError, match="identifiants refusés"):
        client._get_client()


def test_metadonnee_divergente_est_un_echec(tmp_path: Path, monkeypatch) -> None:
    """Métadonnée présente mais différente : divergence prouvée, jamais une présomption."""
    import hashlib

    from seamtech_search.storage import METADATA_SHA256

    class Magasin:
        def head_object(self, Bucket, Key, ChecksumMode=None, **_kwargs):  # noqa: N803, ANN201
            return {
                "ContentLength": 10,
                "ETag": '"etag-quelconque"',
                "Metadata": {METADATA_SHA256: hashlib.sha256(b"un-autre-contenu").hexdigest()},
            }

    local = tmp_path / "piece.bin"
    local.write_bytes(b"0123456789")
    client = _client_double(monkeypatch, Magasin(), tmp_path)
    resultat = client.verifier_integrite("imports/piece.bin", local, relire=False)
    assert resultat.integrite_prouvee is False
    assert resultat.methode == "echec"
    assert "divergente" in (resultat.detail or "")
