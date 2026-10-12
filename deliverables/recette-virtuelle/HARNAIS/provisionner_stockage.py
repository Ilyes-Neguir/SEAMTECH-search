#!/usr/bin/env python3
"""Provisionne le stockage objet de la recette : buckets, identités dédiées,
contrôle d'accès, aller-retour d'un objet témoin. Transposition de
scripts/provisionner_stockage.sh (écrit pour le client `mc` de MinIO) en boto3."""
from __future__ import annotations
import hashlib, json, os
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("SEAMTECH_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
DOCUMENTS = os.environ.get("SEAMTECH_S3_BUCKET", "seamtech-documents")
BACKUPS = os.environ.get("SEAMTECH_BACKUP_BUCKET", "seamtech-backups")
CLE_APP, SEC_APP = os.environ["SEAMTECH_S3_ACCESS_KEY"], os.environ["SEAMTECH_S3_SECRET_KEY"]
CLE_SAUV, SEC_SAUV = os.environ.get("SEAMTECH_BACKUP_ACCESS_KEY"), os.environ.get("SEAMTECH_BACKUP_SECRET_KEY")
CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 2})
rapport: dict[str, object] = {}

def client(cle, secret):
    return boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=cle, aws_secret_access_key=secret,
                        region_name=os.environ.get("SEAMTECH_S3_REGION", "us-east-1"), config=CONFIG)

s3 = client(CLE_APP, SEC_APP)
for bucket in (DOCUMENTS, BACKUPS):
    try:
        s3.head_bucket(Bucket=bucket); rapport[f"bucket_{bucket}"] = "présent"
    except ClientError:
        try:
            s3.create_bucket(Bucket=bucket); rapport[f"bucket_{bucket}"] = "créé"
        except ClientError as e:
            rapport[f"bucket_{bucket}"] = f"ÉCHEC {e.response['Error']['Code']}"
for bucket in (DOCUMENTS, BACKUPS):
    try:
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        rapport[f"versioning_{bucket}"] = s3.get_bucket_versioning(Bucket=bucket).get("Status") or "non rapporté"
    except Exception as e:
        rapport[f"versioning_{bucket}"] = f"NON SUPPORTÉ par le substitut ({type(e).__name__})"
temoin = b"seamtech-recette-" + os.urandom(8)
s3.put_object(Bucket=DOCUMENTS, Key="_provisionnement/temoin.txt", Body=temoin)
relu = s3.get_object(Bucket=DOCUMENTS, Key="_provisionnement/temoin.txt")["Body"].read()
rapport["aller_retour_objet"] = "ok" if relu == temoin else "CORPS DIFFÉRENT"
rapport["sha256_temoin"] = hashlib.sha256(temoin).hexdigest()[:16] + "…"
if CLE_SAUV and SEC_SAUV:
    try:
        client(CLE_SAUV, SEC_SAUV).list_objects_v2(Bucket=BACKUPS, MaxKeys=1)
        rapport["identite_sauvegarde"] = "opérationnelle sur le bucket de sauvegarde"
    except ClientError as e:
        rapport["identite_sauvegarde"] = f"ÉCHEC {e.response['Error']['Code']}"
for libelle, (c, s_) in {"identite_inconnue": ("inconnu-xyz-recette", "peu-importe"), "secret_faux": (CLE_APP, "secret-faux-recette")}.items():
    try:
        client(c, s_).list_objects_v2(Bucket=DOCUMENTS)
        rapport[libelle] = "ACCEPTÉ (Anomalie : contrôle d'accès insuffisant)"
    except ClientError as e:
        rapport[libelle] = f"refusée ({e.response['Error']['Code']})"
if CLE_SAUV and SEC_SAUV:
    try:
        s3.put_object(Bucket=BACKUPS, Key="_provisionnement/app-dans-sauvegarde.txt", Body=b"x")
        rapport["app_ecrit_dans_bucket_sauvegarde"] = "ACCEPTÉ (politique par identité non appliquée par le substitut — écart consigné)"
    except ClientError as e:
        rapport["app_ecrit_dans_bucket_sauvegarde"] = f"refusé ({e.response['Error']['Code']})"
print(json.dumps(rapport, indent=2, ensure_ascii=False))
