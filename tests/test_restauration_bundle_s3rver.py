"""Qualification réseau de la restauration d'objet avec émulateur isolé, pas MinIO/R2."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from seamtech_search.sauvegarde import restaurer_objets_s3
from seamtech_search.storage import S3StorageClient

pytestmark = pytest.mark.s3rver


@pytest.mark.s3
@pytest.mark.xfail(strict=True, reason="S3rver n'offre pas de création exclusive qualifiée pour la restauration")
def test_bundle_objet_fige_puis_transfere_par_reseau(tmp_path: Path):
    endpoint = os.environ.get("SEAMTECH_TEST_S3RVER_URL")
    if not endpoint:
        pytest.skip("émulateur réseau isolé absent")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        pytest.fail("l'émulateur doit être isolé sur loopback")
    bucket = "seamtech-restaure-" + uuid.uuid4().hex
    client = S3StorageClient(endpoint_url=endpoint, bucket_name=bucket, access_key_id="S3RVER",
                             secret_access_key="S3RVER", force_path_style=True)
    s3 = client._get_client()
    s3.create_bucket(Bucket=bucket)
    try:
        source = tmp_path / "piece"
        source.write_bytes(b"original-synthetique")
        cle = "essai/" + uuid.uuid4().hex
        manifeste = {"objets_s3": {"copies": [{"id": 1, "path_relatif": "piece", "object_key": cle,
                                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}}
        class Substitution:
            def upload_file(self, chemin, key, **options):
                source.write_bytes(b"modification-synthetique")
                return client.upload_file(chemin, key, **options)
            def download_file(self, key, destination):
                return client.download_file(key, destination)
        assert restaurer_objets_s3(manifeste, tmp_path, Substitution())["nb"] == 1
        recu = tmp_path / "recu"
        client.download_file(cle, recu)
        assert recu.read_bytes() == b"original-synthetique"
        assert source.read_bytes() == b"modification-synthetique"
        client.delete_file(cle)
    finally:
        s3.delete_bucket(Bucket=bucket)


@pytest.mark.s3
@pytest.mark.xfail(strict=True, reason="S3rver 2.2.9 ignore If-None-Match sur PutObject")
def test_precondition_atomique_de_creation_supportee_par_emulateur():
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    endpoint = os.environ.get("SEAMTECH_TEST_S3RVER_URL")
    if not endpoint:
        pytest.skip("émulateur réseau isolé absent")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        pytest.fail("l'émulateur doit être isolé sur loopback")
    bucket = "seamtech-condition-" + uuid.uuid4().hex
    s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id="S3RVER", aws_secret_access_key="S3RVER",
                      region_name="us-east-1", config=Config(proxies={}, s3={"addressing_style": "path"}))
    s3.create_bucket(Bucket=bucket)
    try:
        s3.put_object(Bucket=bucket, Key="probe", Body=b"original", IfNoneMatch="*")
        try:
            s3.put_object(Bucket=bucket, Key="probe", Body=b"substitution", IfNoneMatch="*")
        except ClientError as erreur:
            assert erreur.response["ResponseMetadata"]["HTTPStatusCode"] == 412
        else:
            pytest.fail("la précondition If-None-Match a été ignorée")
        assert s3.get_object(Bucket=bucket, Key="probe")["Body"].read() == b"original"
    finally:
        s3.delete_object(Bucket=bucket, Key="probe")
        s3.delete_bucket(Bucket=bucket)


@pytest.mark.s3
@pytest.mark.xfail(strict=True, reason="S3rver n'offre pas de création exclusive qualifiée pour la restauration")
def test_references_objet_dupliquees_sur_reseau_isole(tmp_path: Path):
    endpoint = os.environ.get("SEAMTECH_TEST_S3RVER_URL")
    if not endpoint:
        pytest.skip("émulateur réseau isolé absent")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        pytest.fail("l'émulateur doit être isolé sur loopback")
    bucket = "seamtech-duplique-" + uuid.uuid4().hex
    client = S3StorageClient(endpoint_url=endpoint, bucket_name=bucket, access_key_id="S3RVER",
                             secret_access_key="S3RVER", force_path_style=True)
    s3 = client._get_client()
    s3.create_bucket(Bucket=bucket)
    cle = "synthese/" + uuid.uuid4().hex
    try:
        for nom in ("a", "b"):
            (tmp_path / nom).write_bytes(b"contenu-identique")
        empreinte = hashlib.sha256(b"contenu-identique").hexdigest()
        copies = [{"id": i, "path_relatif": nom, "object_key": cle, "sha256": empreinte}
                  for i, nom in enumerate(("a", "b"), start=1)]
        class Compteur:
            appels = 0
            def _apply_prefix(self, key):
                return client._apply_prefix(key)
            def upload_file(self, chemin, key, **options):
                self.appels += 1
                return client.upload_file(chemin, key, **options)
            def download_file(self, key, destination):
                return client.download_file(key, destination)
        compteur = Compteur()
        resultat = restaurer_objets_s3({"objets_s3": {"copies": copies}}, tmp_path, compteur)
        assert resultat["nb"] == 2 and compteur.appels == 1
        recu = tmp_path / "recu"
        client.download_file(cle, recu)
        assert recu.read_bytes() == b"contenu-identique"
    finally:
        s3.delete_object(Bucket=bucket, Key=cle)
        s3.delete_bucket(Bucket=bucket)


@pytest.mark.s3
@pytest.mark.xfail(strict=True, reason="S3rver ne fournit pas le SHA-256 serveur FULL_OBJECT requis pour la copie")
def test_sauvegarde_copie_objet_par_reseau_isole(tmp_path: Path):
    from seamtech_search.sauvegarde import copier_objets_s3

    endpoint = os.environ.get("SEAMTECH_TEST_S3RVER_URL")
    if not endpoint:
        pytest.skip("émulateur réseau isolé absent")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        pytest.fail("l'émulateur doit être isolé sur loopback")
    bucket = "seamtech-copie-" + uuid.uuid4().hex
    client = S3StorageClient(endpoint_url=endpoint, bucket_name=bucket, access_key_id="S3RVER",
                             secret_access_key="S3RVER", force_path_style=True)
    s3 = client._get_client()
    s3.create_bucket(Bucket=bucket)
    cle = "source/" + uuid.uuid4().hex
    try:
        s3.put_object(Bucket=bucket, Key=cle, Body=b"objet-synthetique")
        resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": cle, "object_bucket": bucket}]},
                                   client, tmp_path / "bundle")
        assert resultat["nb_copies"] == 1 and resultat["nb_manquants"] == 0
        copie = resultat["copies"][0]
        assert (tmp_path / "bundle" / copie["path_relatif"]).read_bytes() == b"objet-synthetique"
        assert copie["sha256"] == hashlib.sha256(b"objet-synthetique").hexdigest()
        assert s3.get_object(Bucket=bucket, Key=cle)["Body"].read() == b"objet-synthetique"
    finally:
        s3.delete_object(Bucket=bucket, Key=cle)
        s3.delete_bucket(Bucket=bucket)


@pytest.mark.s3
def test_s3rver_restauration_objet_refusee_sans_ecraser(tmp_path):
    from seamtech_search.sauvegarde import SauvegardeError

    endpoint = os.environ.get("SEAMTECH_TEST_S3RVER_URL")
    if not endpoint:
        pytest.skip("émulateur réseau isolé absent")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        pytest.fail("l'émulateur doit être isolé sur loopback")
    bucket = "seamtech-refus-" + uuid.uuid4().hex
    client = S3StorageClient(endpoint_url=endpoint, bucket_name=bucket, access_key_id="S3RVER",
                             secret_access_key="S3RVER", force_path_style=True)
    s3 = client._get_client()
    s3.create_bucket(Bucket=bucket)
    try:
        s3.put_object(Bucket=bucket, Key="piece", Body=b"temoin")
        (tmp_path / "piece").write_bytes(b"sauvegarde")
        manifeste = {"objets_s3": {"copies": [{"id": 1, "path_relatif": "piece", "object_key": "piece",
                                                "sha256": hashlib.sha256(b"sauvegarde").hexdigest()}]}}
        with pytest.raises(SauvegardeError, match="exclusive"):
            restaurer_objets_s3(manifeste, tmp_path, client)
        assert s3.get_object(Bucket=bucket, Key="piece")["Body"].read() == b"temoin"
    finally:
        s3.delete_object(Bucket=bucket, Key="piece")
        s3.delete_bucket(Bucket=bucket)
