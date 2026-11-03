"""Sondes réseau pour MinIO historique isolé, jamais une qualification R2 ou actuelle."""
from __future__ import annotations

import base64
import hashlib
import os
import uuid
from urllib.parse import urlsplit

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

pytestmark = [pytest.mark.s3, pytest.mark.minio_historique]


@pytest.fixture
def serveur_minio_historique():
    endpoint = os.environ.get("SEAMTECH_TEST_MINIO_2023_URL")
    if not endpoint:
        pytest.skip("MinIO historique isolé absent")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
        pytest.fail("le banc MinIO doit être en loopback, sans identifiants dans l'URL")
    access = os.environ.get("SEAMTECH_TEST_MINIO_2023_ACCESS")
    secret = os.environ.get("SEAMTECH_TEST_MINIO_2023_SECRET")
    if not access or not secret:
        pytest.fail("identifiants synthétiques du banc absents")
    s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                      region_name="us-east-1", config=Config(proxies={}, s3={"addressing_style": "path"}))
    bucket = "seamtech-minio-test-" + uuid.uuid4().hex
    s3.create_bucket(Bucket=bucket)
    try:
        yield s3, bucket
    finally:
        # Ce bucket UUID a été créé par cette fixture exclusivement.
        pages = s3.get_paginator("list_object_versions").paginate(Bucket=bucket)
        versions = [objet for page in pages for nom in ("Versions", "DeleteMarkers") for objet in page.get(nom, [])]
        for objet in versions:
            s3.delete_object(Bucket=bucket, Key=objet["Key"], VersionId=objet["VersionId"])
        # Si le versioning n'a pas été activé, les objets possèdent VersionId=null.
        s3.delete_bucket(Bucket=bucket)


@pytest.mark.xfail(strict=True, reason="MinIO RELEASE.2023-08-23 ignore If-None-Match sur PutObject")
def test_minio_historique_creation_conditionnelle_preserve_objet(serveur_minio_historique):
    s3, bucket = serveur_minio_historique
    s3.put_object(Bucket=bucket, Key="piece", Body=b"original", IfNoneMatch="*")
    try:
        s3.put_object(Bucket=bucket, Key="piece", Body=b"substitution", IfNoneMatch="*")
    except ClientError as erreur:
        assert erreur.response["ResponseMetadata"]["HTTPStatusCode"] == 412
    else:
        pytest.fail("le fournisseur a ignoré la précondition")
    assert s3.get_object(Bucket=bucket, Key="piece")["Body"].read() == b"original"


def test_minio_historique_checksum_simple_et_multipart(serveur_minio_historique):
    s3, bucket = serveur_minio_historique
    contenu = b"contenu-synthetique"
    s3.put_object(Bucket=bucket, Key="simple", Body=contenu, ChecksumAlgorithm="SHA256")
    head = s3.head_object(Bucket=bucket, Key="simple", ChecksumMode="ENABLED")
    assert head.get("ChecksumSHA256") == base64.b64encode(hashlib.sha256(contenu).digest()).decode()
    assert head.get("ChecksumType") is None  # cette version n'identifie pas FULL_OBJECT
    upload = s3.create_multipart_upload(Bucket=bucket, Key="multipart", ChecksumAlgorithm="SHA256")
    upload_id = upload["UploadId"]
    try:
        parties = []
        digest = hashlib.sha256()
        for numero, bloc in enumerate((b"A" * (5 * 1024 * 1024), b"B" * (1024 * 1024)), 1):
            digest.update(bloc)
            part = s3.upload_part(Bucket=bucket, Key="multipart", UploadId=upload_id, PartNumber=numero,
                                  Body=bloc, ChecksumAlgorithm="SHA256")
            parties.append({"PartNumber": numero, "ETag": part["ETag"], "ChecksumSHA256": part["ChecksumSHA256"]})
        termine = s3.complete_multipart_upload(Bucket=bucket, Key="multipart", UploadId=upload_id,
                                               MultipartUpload={"Parts": parties})
        upload_id = None
        head = s3.head_object(Bucket=bucket, Key="multipart", ChecksumMode="ENABLED")
        assert head["ContentLength"] == 6 * 1024 * 1024
        assert head.get("ChecksumSHA256") is None
        assert head.get("ChecksumType") is None
        assert termine["ChecksumSHA256"].endswith("-2")
        assert termine["ChecksumSHA256"] != base64.b64encode(digest.digest()).decode()
    finally:
        if upload_id:
            s3.abort_multipart_upload(Bucket=bucket, Key="multipart", UploadId=upload_id)


def test_minio_historique_copie_objet_avec_entetes(tmp_path, serveur_minio_historique):
    from seamtech_search.sauvegarde import copier_objets_s3
    from seamtech_search.storage import S3StorageClient

    s3, bucket = serveur_minio_historique
    contenu = b"objet-synthetique"
    s3.put_object(Bucket=bucket, Key="source", Body=contenu, ChecksumAlgorithm="SHA256")
    client = S3StorageClient(endpoint_url=os.environ["SEAMTECH_TEST_MINIO_2023_URL"], bucket_name=bucket,
                             access_key_id=os.environ["SEAMTECH_TEST_MINIO_2023_ACCESS"],
                             secret_access_key=os.environ["SEAMTECH_TEST_MINIO_2023_SECRET"])
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "source", "object_bucket": bucket}]},
                               client, tmp_path / "bundle")
    assert resultat["nb_copies"] == 1 and resultat["nb_manquants"] == 0
    copie = resultat["copies"][0]
    assert (tmp_path / "bundle" / copie["path_relatif"]).read_bytes() == contenu
    assert copie["sha256"] == hashlib.sha256(contenu).hexdigest()
    assert s3.get_object(Bucket=bucket, Key="source")["Body"].read() == contenu


def test_minio_historique_version_epinglee(serveur_minio_historique):
    s3, bucket = serveur_minio_historique
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    ancien = s3.put_object(Bucket=bucket, Key="versionne", Body=b"original")["VersionId"]
    nouveau = s3.put_object(Bucket=bucket, Key="versionne", Body=b"remplacement")["VersionId"]
    assert ancien != nouveau
    assert s3.head_object(Bucket=bucket, Key="versionne")["VersionId"] == nouveau
    assert s3.get_object(Bucket=bucket, Key="versionne", VersionId=ancien)["Body"].read() == b"original"
    assert s3.get_object(Bucket=bucket, Key="versionne")["Body"].read() == b"remplacement"


def test_minio_historique_aba_get_version_epinglee(tmp_path, serveur_minio_historique):
    from seamtech_search.sauvegarde import copier_objets_s3
    from seamtech_search.storage import S3StorageClient

    s3, bucket = serveur_minio_historique
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    version_initiale = s3.put_object(Bucket=bucket, Key="piece", Body=b"original")["VersionId"]

    class Interstice(S3StorageClient):
        version_demandee = None

        def download_file(self, remote_key, destination_path, *, version_id=None):
            self.version_demandee = version_id
            nouvelle = s3.put_object(Bucket=bucket, Key=remote_key, Body=b"intrus!!")["VersionId"]
            try:
                return super().download_file(remote_key, destination_path, version_id=version_id)
            finally:
                s3.delete_object(Bucket=bucket, Key=remote_key, VersionId=nouvelle)

    client = Interstice(endpoint_url=os.environ["SEAMTECH_TEST_MINIO_2023_URL"], bucket_name=bucket,
                        access_key_id=os.environ["SEAMTECH_TEST_MINIO_2023_ACCESS"],
                        secret_access_key=os.environ["SEAMTECH_TEST_MINIO_2023_SECRET"])
    resultat = copier_objets_s3({"documents": [{"id": 1, "object_key": "piece"}]}, client, tmp_path / "bundle")
    assert client.version_demandee == version_initiale
    assert resultat["nb_copies"] == 1 and resultat["nb_manquants"] == 0
    assert (tmp_path / "bundle" / resultat["copies"][0]["path_relatif"]).read_bytes() == b"original"
    assert s3.head_object(Bucket=bucket, Key="piece")["VersionId"] == version_initiale


def test_minio_historique_envoi_simple_et_multipart_checksum(tmp_path, serveur_minio_historique):
    from seamtech_search.storage import S3StorageClient

    s3, bucket = serveur_minio_historique
    client = S3StorageClient(endpoint_url=os.environ["SEAMTECH_TEST_MINIO_2023_URL"], bucket_name=bucket,
                             access_key_id=os.environ["SEAMTECH_TEST_MINIO_2023_ACCESS"],
                             secret_access_key=os.environ["SEAMTECH_TEST_MINIO_2023_SECRET"])
    simple = b"piece-synthetique"
    assert client.upload_bytes(simple, "simple") == "simple"
    assert s3.get_object(Bucket=bucket, Key="simple")["Body"].read() == simple
    contenu = b"A" * (9 * 1024 * 1024)
    source = tmp_path / "multipart.bin"
    source.write_bytes(contenu)
    assert client.upload_file(source, "multipart") == "multipart"
    corps = s3.get_object(Bucket=bucket, Key="multipart")["Body"]
    try:
        assert hashlib.sha256(corps.read()).digest() == hashlib.sha256(contenu).digest()
    finally:
        corps.close()


def test_minio_historique_restauration_interdite_preserve_cle(tmp_path, serveur_minio_historique):
    from seamtech_search.sauvegarde import SauvegardeError, restaurer_objets_s3
    from seamtech_search.storage import S3StorageClient

    s3, bucket = serveur_minio_historique
    s3.put_object(Bucket=bucket, Key="piece", Body=b"temoin")
    (tmp_path / "piece").write_bytes(b"sauvegarde")
    manifeste = {"objets_s3": {"copies": [{"id": 1, "path_relatif": "piece", "object_key": "piece",
                                            "sha256": hashlib.sha256(b"sauvegarde").hexdigest()}]}}
    client = S3StorageClient(endpoint_url=os.environ["SEAMTECH_TEST_MINIO_2023_URL"], bucket_name=bucket,
                             access_key_id=os.environ["SEAMTECH_TEST_MINIO_2023_ACCESS"],
                             secret_access_key=os.environ["SEAMTECH_TEST_MINIO_2023_SECRET"])
    with pytest.raises(SauvegardeError, match="exclusive"):
        restaurer_objets_s3(manifeste, tmp_path, client)
    assert s3.get_object(Bucket=bucket, Key="piece")["Body"].read() == b"temoin"
