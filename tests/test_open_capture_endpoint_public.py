"""``POST /open`` sur un document à identité d'octets CAPTURÉE (P0.2).

Depuis la promotion ``uploads/lots-televerses`` → objet (P0.2), un document
stagé porte une empreinte d'octets (``documents.source_sha256``). La règle
historique de ``/open`` était alors devenue intenable :

* contrat 2026-10-07 (écart E-28) : l'API sert les octets (200) et n'émet
  **aucune** redirection vers l'endpoint INTERNE du réseau des conteneurs ;
* même contrat : si un endpoint **PUBLIC** est déclaré
  (``SEAMTECH_S3_PUBLIC_ENDPOINT_URL``), la redirection présignée est le mode
  attendu — sinon chaque octet transite par l'API.

Or la branche « empreinte de capture » renvoyait TOUJOURS 200 : la recette
corpus réel (``test_10_sauvegarde_restauration_base_neuve``, 10.6 bis) est
tombée dessus — « endpoint public déclaré : HTTP 200 (attendu 302) ».

La règle retenue est une preuve, pas une préférence : l'objet n'est servi par
redirection que si le CATALOGUE prouve qu'il est bien ces octets. La table
``documents`` ne reçoit ``object_key`` que par ``lier_objets_capture``, dont
l'``UPDATE`` exige ``source_sha256`` = l'empreinte de l'envoi VÉRIFIÉ. Une
ligne qui porte les deux prouve donc l'identité ; sinon (objet d'un autre
compartiment, envoi non vérifié, document non lié) l'API garde le repli : elle
lit et VÉRIFIE les octets contre le SHA de capture.

Ce fichier prouve les deux branches, sans réseau (boto3 doublé, vrai client de
stockage) et sans donnée réelle (contenus synthétiques, RG14).
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
from seamtech_search.storage import S3StorageClient

CLE_ACCES_FICTIVE = "CLE-ACCES-FICTIVE-TEST"
SECRET_FICTIF = "SECRET-FICTIF-TEST-NE-PAS-UTILISER"
URL_PUBLIQUE = "https://minio.public.invalide:9000"
URL_PRESIGNEE = (
    f"{URL_PUBLIQUE}/seamtech-documents/IMP-1/abc/fiche.pdf"
    f"?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential={CLE_ACCES_FICTIVE}%2F20260925"
    "%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Expires=900&X-Amz-Signature=abcdef"
)
CONTENU = b"%PDF-1.4 contenu synthetique de qualification"
JETON = "jeton-de-test-synthetique"


def _config(tmp_path: Path, nom_base: str, *, endpoint_public: str | None = URL_PUBLIQUE) -> AppConfig:
    return AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / nom_base,
        min_free_bytes=0,
        auth_token=JETON,
        s3_endpoint_url="http://minio.invalide:9000",
        s3_public_endpoint_url=endpoint_public,
        s3_bucket="seamtech-documents",
        s3_access_key=CLE_ACCES_FICTIVE,
        s3_secret_key=SECRET_FICTIF,
    )


def _double_boto3(*, url: str | None = URL_PRESIGNEE) -> MagicMock:
    double = MagicMock()
    double.generate_presigned_url.return_value = url
    double.head_object.return_value = {}
    double.get_bucket_versioning.return_value = {"Status": "Enabled"}
    return double


def _document(
    tmp_path: Path,
    config: AppConfig,
    *,
    nom: str,
    contenu: bytes,
    empreinte: str | None,
    object_key: str | None,
    object_bucket: str | None,
) -> Path:
    """Insère le document au catalogue exactement comme le ferait le produit."""
    fichier = tmp_path / nom
    fichier.write_bytes(contenu)
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.upsert_documents(
        [
            Document(
                path=fichier,
                name=fichier.name,
                parent_path=fichier.parent,
                extension=fichier.suffix,
                size=fichier.stat().st_size,
                modified_at=time.time(),
                is_dir=False,
                text="contenu synthetique",
                source_sha256=empreinte,
                object_key=object_key,
                object_bucket=object_bucket,
                upload_status="uploaded" if object_key else "pending",
            )
        ]
    )
    index.close()
    return fichier


def _appeler(config: AppConfig, double: MagicMock, chemin: Path) -> object:
    """Monte l'application avec le vrai client de stockage, boto3 doublé."""
    client_reel = S3StorageClient(
        endpoint_url="http://minio.invalide:9000",
        bucket_name="seamtech-documents",
        access_key_id=CLE_ACCES_FICTIVE,
        secret_access_key=SECRET_FICTIF,
    )
    with (
        patch.object(S3StorageClient, "_get_client", return_value=double),
        patch.object(S3StorageClient, "_get_probe_client", return_value=double),
        patch.object(S3StorageClient, "_client_pour_endpoint", return_value=double),
        patch("seamtech_search.api.S3StorageClient", return_value=client_reel),
    ):
        application = create_app(config)
        with TestClient(application, follow_redirects=False) as http:
            return http.post("/open", params={"path": str(chemin)}, headers={"X-SEAMTECH-TOKEN": JETON})


def test_capture_prouvee_et_endpoint_public_redirigent_en_302(tmp_path: Path) -> None:
    """Objet lié à l'identité capturée + endpoint public → 302, hôte PUBLIC."""
    config = _config(tmp_path, "capture_prouvee.db")
    empreinte = hashlib.sha256(CONTENU).hexdigest()
    fichier = _document(
        tmp_path,
        config,
        nom="fiche.pdf",
        contenu=CONTENU,
        empreinte=empreinte,
        object_key="IMP-1/abc/fiche.pdf",
        object_bucket="seamtech-documents",
    )
    double = _double_boto3()

    reponse = _appeler(config, double, fichier)

    assert reponse.status_code == 302, (
        f"endpoint public déclaré et objet prouvé : HTTP {reponse.status_code} (attendu 302) — "
        f"{reponse.content[:300]!r}"
    )
    hote = urllib.parse.urlparse(reponse.headers.get("location") or "").hostname or ""
    assert hote == "minio.public.invalide", f"redirection vers {hote!r} au lieu de l'endpoint public déclaré"
    assert hote not in {"minio", "postgres", "redis", "web", "worker", "frontend"}, (
        f"redirection vers un hôte INTERNE ({hote}) : injoignable depuis un poste de l'atelier"
    )
    arguments = double.generate_presigned_url.call_args.kwargs
    assert arguments["ExpiresIn"] == 900, "la durée de 900 s ne doit pas changer"
    assert arguments["Params"]["Key"] == "IMP-1/abc/fiche.pdf"


def test_capture_sans_objet_prouve_sert_les_octets_verifies(tmp_path: Path) -> None:
    """Objet d'un AUTRE compartiment : aucune preuve → l'API sert et VÉRIFIE."""
    config = _config(tmp_path, "capture_non_prouvee.db")
    empreinte = hashlib.sha256(CONTENU).hexdigest()
    fichier = _document(
        tmp_path,
        config,
        nom="fiche.pdf",
        contenu=CONTENU,
        empreinte=empreinte,
        object_key="IMP-1/abc/fiche.pdf",
        object_bucket="autre-compartiment",
    )
    double = _double_boto3()

    reponse = _appeler(config, double, fichier)

    assert reponse.status_code == 200, (
        f"objet non prouvé : les octets doivent être servis et vérifiés (HTTP {reponse.status_code})"
    )
    assert reponse.content == CONTENU, "octets servis différents du fichier conforme"
    assert "location" not in {cle.lower() for cle in reponse.headers}, (
        "redirection émise alors que l'identité n'est pas prouvée"
    )
    double.generate_presigned_url.assert_not_called()


def test_capture_sans_endpoint_public_sert_les_octets(tmp_path: Path) -> None:
    """Sans endpoint public déclaré, aucune redirection (contrat E-28)."""
    config = _config(tmp_path, "sans_public.db", endpoint_public=None)
    empreinte = hashlib.sha256(CONTENU).hexdigest()
    fichier = _document(
        tmp_path,
        config,
        nom="fiche.pdf",
        contenu=CONTENU,
        empreinte=empreinte,
        object_key="IMP-1/abc/fiche.pdf",
        object_bucket="seamtech-documents",
    )
    double = _double_boto3()

    reponse = _appeler(config, double, fichier)

    assert reponse.status_code == 200, f"attendu 200 (octets servis par l'API), obtenu {reponse.status_code}"
    assert reponse.content == CONTENU
    assert "location" not in {cle.lower() for cle in reponse.headers}
    double.generate_presigned_url.assert_not_called()


def test_capture_non_conforme_est_refusee_sans_redirection(tmp_path: Path) -> None:
    """Un fichier dont les octets ne sont plus la capture n'est JAMAIS servi."""
    config = _config(tmp_path, "capture_fausse.db")
    fichier = _document(
        tmp_path,
        config,
        nom="fiche.pdf",
        contenu=b"contenu modifie apres capture",
        empreinte=hashlib.sha256(CONTENU).hexdigest(),
        object_key=None,
        object_bucket=None,
    )
    double = _double_boto3()

    reponse = _appeler(config, double, fichier)

    assert reponse.status_code == 404, (
        f"un fichier non conforme ne doit pas être servi (HTTP {reponse.status_code})"
    )
    double.generate_presigned_url.assert_not_called()


def test_la_preuve_est_portee_par_la_ligne_elle_meme(tmp_path: Path) -> None:
    """Garde-fou : `lier_objets_capture` n'attache une clé QUE sous l'empreinte.

    Sans cette exigence, la redirection du premier test servirait n'importe
    quel objet. On rejoue donc l'UPDATE sur une ligne dont l'empreinte
    diffère et l'on vérifie qu'aucune clé n'est attachée.
    """
    from seamtech_search.indexer import SearchIndex

    config = _config(tmp_path, "preuve_ligne.db")
    fichier = _document(
        tmp_path,
        config,
        nom="fiche.pdf",
        contenu=CONTENU,
        empreinte=hashlib.sha256(CONTENU).hexdigest(),
        object_key=None,
        object_bucket=None,
    )
    index = SearchIndex(config.database_path)
    try:
        liees = index.lier_objets_capture(
            [(os.path.normcase(str(fichier)), "0" * 64, "IMP-1/abc/fiche.pdf", "seamtech-documents", time.time())]
        )
    finally:
        index.close()
    assert liees == set(), "une clé a été attachée à un document d'une AUTRE empreinte : la preuve serait fausse"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
