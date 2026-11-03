"""Une erreur fournisseur ne doit pas être recopiée dans un rapport persistant."""
from pathlib import Path

from seamtech_search.config import AppConfig
from seamtech_search.storage import S3StorageClient, upload_artifacts_to_storage


def test_exception_upload_ne_revele_pas_contenu_dans_manifeste(tmp_path, monkeypatch):
    fichier = tmp_path / 'document.pdf'
    fichier.write_bytes(b'synthetique')
    cfg = AppConfig(root_paths=[tmp_path], s3_endpoint_url='http://127.0.0.1:9000',
                    s3_bucket='bucket-test', s3_access_key='cle-test', s3_secret_key='secret-test')
    secret = 'SECRET-SYNTHETIQUE-CONTENU'

    def echec(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(S3StorageClient, 'is_configured', lambda *args: True)
    monkeypatch.setattr(S3StorageClient, 'upload_file', echec)
    resultat = upload_artifacts_to_storage('dossier', [fichier], cfg, 'test', tmp_path)
    assert resultat.status == 'failed'
    assert len(resultat.artifacts) == 1
    assert resultat.artifacts[0].error
    assert secret not in str(resultat.to_dict())


def test_chemin_absent_dans_erreur_client_est_expurge(tmp_path):
    client = S3StorageClient(endpoint_url='http://127.0.0.1:9000', bucket_name='bucket-test',
                             access_key_id='cle-test', secret_access_key='secret-test')
    secret = 'SECRET-SYNTHETIQUE-CHEMIN'
    try:
        client.upload_file(Path(tmp_path / secret))
    except Exception as exc:
        assert secret not in str(exc)
    else:
        raise AssertionError('fichier absent accepté')
