"""Release candidate — garde-fous du stockage objet (audit S3/MinIO).

Ces tests accompagnent ``docs/verite_terrain/AUDIT_STOCKAGE_S3_MINIO.md`` et
``docs/RELEASE_CANDIDATE_CHECKLIST.md``. Ils ne changent AUCUN comportement :
ils FIGENT ce que le code fait réellement aujourd'hui, pour que le jour où une
alternative à MinIO sera décidée, la bascule casse ici et pas en production.

Aucun réseau, aucun conteneur, aucun vrai secret (RG14) : le client boto3 est
remplacé par un double, et les identifiants utilisés sont des chaînes
explicitement fictives.

Couverture demandée par le lot :

1. configuration S3 complète (variables d'environnement → AppConfig) ;
2. absence de secrets dans les journaux (upload, erreurs, URL présignée) ;
3. reprise après redémarrage (les références d'objets survivent) ;
4. URL présignée (contrat d'appel, expiration, préfixe) ;
5. ``object_key`` avec espaces et accents (et normalisation Unicode) ;
6. erreurs S3 sans fuite d'identifiants ;
7. repli local documenté (S3 non configuré, ``storage_backend``) ;
8. démarrage avec une base vide ;
9. migration séquentielle jusqu'à 017.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
from seamtech_search.storage import (
    S3StorageClient,
    StorageError,
    artifact_object_key,
    upload_artifacts_to_storage,
)

RACINE = Path(__file__).resolve().parent.parent

#: Identifiants FICTIFS — aucun secret réel ne doit entrer dans le dépôt
#: (règle du lot : « ne pas utiliser de vrais secrets »). Ils sont volontairement
#: reconnaissables pour qu'une fuite dans un journal soit trouvable par grep.
CLE_ACCES_FICTIVE = "CLE-ACCES-FICTIVE-TEST"
SECRET_FICTIF = "SECRET-FICTIF-TEST-NE-PAS-UTILISER"


def _client_fictif(**extra: object) -> S3StorageClient:
    """Client S3 réel (le vrai code), identifiants fictifs, sans réseau."""
    params: dict[str, object] = {
        "endpoint_url": "http://minio.invalide:9000",
        "bucket_name": "seamtech-documents",
        "access_key_id": CLE_ACCES_FICTIVE,
        "secret_access_key": SECRET_FICTIF,
    }
    params.update(extra)
    return S3StorageClient(**params)  # type: ignore[arg-type]


def _messages(caplog: pytest.LogCaptureFixture) -> str:
    """Tout ce qui a été journalisé, rendu comme le verrait un fichier de log."""
    return "\n".join(enregistrement.getMessage() for enregistrement in caplog.records)


def _absent_objet_404() -> ClientError:
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


# ---------------------------------------------------------------------------
# 1. Configuration S3 complète
# ---------------------------------------------------------------------------


def test_configuration_stockage_objet_complete_depuis_les_variables_d_environnement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les 8 variables SEAMTECH_S3_* / stockage documentées arrivent bien dans AppConfig.

    C'est la liste EXACTE que la checklist de release demande de renseigner :
    une variable oubliée dans ``config.py`` ne serait visible qu'en production.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    fichier = config_dir / "config.json"
    fichier.write_text('{"root_paths": ["sample_data"], "min_free_bytes": 0}', encoding="utf-8")

    monkeypatch.setenv("SEAMTECH_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("SEAMTECH_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("SEAMTECH_S3_BUCKET", "seamtech-documents")
    monkeypatch.setenv("SEAMTECH_S3_ACCESS_KEY", CLE_ACCES_FICTIVE)
    monkeypatch.setenv("SEAMTECH_S3_SECRET_KEY", SECRET_FICTIF)
    monkeypatch.setenv("SEAMTECH_S3_REGION", "eu-west-3")
    monkeypatch.setenv("SEAMTECH_S3_FORCE_PATH_STYLE", "false")
    monkeypatch.setenv("SEAMTECH_S3_PREFIX", "seamtech")
    monkeypatch.setenv("SEAMTECH_DELETE_LOCAL_AFTER_UPLOAD", "true")

    config = AppConfig.load(fichier)

    assert config.storage_backend == "s3"
    assert config.s3_endpoint_url == "http://minio:9000"
    assert config.s3_bucket == "seamtech-documents"
    assert config.s3_access_key == CLE_ACCES_FICTIVE
    assert config.s3_secret_key == SECRET_FICTIF
    assert config.s3_region == "eu-west-3"
    assert config.s3_force_path_style is False
    assert config.s3_prefix == "seamtech"
    assert config.delete_local_after_upload is True

    # Le client construit depuis cette config reprend les mêmes valeurs :
    # aucun réglage n'est perdu entre la configuration et l'appel S3.
    client = S3StorageClient(config=config)
    assert (client.endpoint_url, client.bucket_name, client.region_name) == (
        "http://minio:9000",
        "seamtech-documents",
        "eu-west-3",
    )
    assert client.force_path_style is False
    assert client.prefix == "seamtech"
    assert client.is_configured() is True


def test_configuration_stockage_objet_incomplete_est_consideree_configuree(tmp_path: Path) -> None:
    """RISQUE R-2 (audit §7) : un endpoint SANS identifiants passe pour configuré.

    ``is_configured()`` répond True dès qu'il y a un bucket ET (une clé OU un
    endpoint). Conséquence mesurée : une configuration incomplète ne fait PAS
    échouer le démarrage, elle échoue au premier upload (import mis en
    quarantaine). La checklist de release impose donc un contrôle explicite des
    quatre variables avant mise en service.
    """
    config = AppConfig(
        root_paths=[tmp_path],
        s3_endpoint_url="http://minio:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=None,
        s3_secret_key=None,
        min_free_bytes=0,
    )
    assert S3StorageClient(config=config).is_configured() is True

    # Sans bucket, en revanche, le client se déclare non configuré.
    assert S3StorageClient(bucket_name="", access_key_id=CLE_ACCES_FICTIVE).is_configured() is False


def test_storage_backend_local_ne_desactive_pas_le_client_objet(tmp_path: Path) -> None:
    """RISQUE R-3 (audit §7) : ``storage_backend`` n'est qu'un libellé de /health.

    Rien dans le code ne le lit pour choisir un backend : seul le fait que S3
    soit configuré (ou non) décide. Mettre ``SEAMTECH_STORAGE_BACKEND=local``
    en croyant couper les envois ne coupe RIEN. Le test fige ce constat pour
    qu'un futur vrai backend « local » ne puisse pas être ajouté en silence.
    """
    config = AppConfig(
        root_paths=[tmp_path],
        storage_backend="local",
        s3_endpoint_url="http://minio:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=CLE_ACCES_FICTIVE,
        s3_secret_key=SECRET_FICTIF,
        min_free_bytes=0,
    )
    assert S3StorageClient(config=config).is_configured() is True

    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    with patch("seamtech_search.storage.S3StorageClient.upload_file", return_value="k") as envoi:
        with patch("seamtech_search.storage.S3StorageClient.object_exists", return_value=True):
            lot = upload_artifacts_to_storage("dossier", [fichier], config, import_id="IMP-1")
    assert envoi.called, "storage_backend=local n'empêche pas l'envoi S3 (comportement actuel)"
    assert lot.status == "uploaded"


# ---------------------------------------------------------------------------
# 2 & 6. Aucun secret dans les journaux, erreurs S3 sans fuite d'identifiants
# ---------------------------------------------------------------------------


def test_envoi_reussi_ne_journalise_aucun_secret(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Un envoi réussi journalise nom/bucket/clé — jamais les identifiants."""
    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    client = _client_fictif()
    faux_boto = MagicMock()
    faux_boto.head_object.side_effect = _absent_objet_404()

    caplog.set_level(logging.DEBUG, logger="seamtech_search.storage")
    with patch.object(client, "_get_client", return_value=faux_boto):
        with patch.object(client, "ensure_bucket_exists"):
            cle = client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf")

    journal = _messages(caplog)
    assert cle == "IMP-1/abc/plan.pdf"
    assert "plan.pdf" in journal, "le journal doit rester exploitable (nom + clé)"
    assert CLE_ACCES_FICTIVE not in journal
    assert SECRET_FICTIF not in journal


def test_erreurs_stockage_objet_ne_fuient_pas_les_identifiants(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Les quatre chemins d'erreur (envoi, octets, téléchargement, présignée) sont muets sur les secrets.

    Le message d'exception remonté à l'appelant (``StorageError``) est lui aussi
    contrôlé : il sert au diagnostic d'exploitation, il ne doit jamais partir
    dans un ticket avec la clé secrète dedans.
    """
    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    client = _client_fictif()

    faux_boto = MagicMock()
    faux_boto.head_object.side_effect = _absent_objet_404()
    panne = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "PutObject"
    )
    faux_boto.upload_file.side_effect = panne
    faux_boto.put_object.side_effect = panne
    faux_boto.download_file.side_effect = panne
    faux_boto.generate_presigned_url.side_effect = panne

    caplog.set_level(logging.DEBUG, logger="seamtech_search.storage")
    messages_erreur: list[str] = []
    with patch.object(client, "_get_client", return_value=faux_boto):
        with patch.object(client, "ensure_bucket_exists"):
            for appel in (
                lambda: client.upload_file(fichier, remote_key="IMP-1/abc/plan.pdf"),
                lambda: client.upload_bytes(b"x", remote_key="IMP-1/abc/note.txt"),
                lambda: client.download_file("IMP-1/abc/plan.pdf", tmp_path / "copie.pdf"),
                lambda: client.get_presigned_url("IMP-1/abc/plan.pdf"),
            ):
                with pytest.raises(StorageError) as capture:
                    appel()
                messages_erreur.append(str(capture.value))

    journal = _messages(caplog)
    for texte in [journal, *messages_erreur]:
        assert CLE_ACCES_FICTIVE not in texte
        assert SECRET_FICTIF not in texte
    # Le diagnostic reste utile : le code d'erreur S3 est conservé.
    assert any("AccessDenied" in message for message in messages_erreur)


def test_url_presignee_n_est_jamais_journalisee(caplog: pytest.LogCaptureFixture) -> None:
    """Une URL présignée SigV4 porte ``X-Amz-Credential`` : elle ne doit pas atterrir dans un log.

    C'est un secret à durée limitée (900 s côté API) : quiconque lit le journal
    pourrait télécharger la pièce. Le client ne journalise donc rien en cas de
    succès, et seulement la CLÉ (pas l'URL) en cas d'échec.
    """
    client = _client_fictif()
    url = (
        "http://minio.invalide:9000/seamtech-documents/IMP-1/abc/plan.pdf"
        f"?X-Amz-Credential={CLE_ACCES_FICTIVE}%2F20260925%2Fus-east-1%2Fs3%2Faws4_request"
        "&X-Amz-Signature=faux"
    )
    faux_boto = MagicMock()
    faux_boto.generate_presigned_url.return_value = url

    caplog.set_level(logging.DEBUG, logger="seamtech_search.storage")
    with patch.object(client, "_get_client", return_value=faux_boto):
        assert client.get_presigned_url("IMP-1/abc/plan.pdf") == url

    journal = _messages(caplog)
    assert url not in journal
    assert CLE_ACCES_FICTIVE not in journal
    assert "X-Amz-Signature" not in journal


# ---------------------------------------------------------------------------
# 4. URL présignée : contrat d'appel, expiration, préfixe
# ---------------------------------------------------------------------------


def test_url_presignee_transmet_bucket_cle_et_expiration() -> None:
    """Contrat réel : ``generate_presigned_url(get_object, {Bucket, Key}, ExpiresIn)``."""
    client = _client_fictif()
    faux_boto = MagicMock()
    faux_boto.generate_presigned_url.return_value = "http://minio.invalide:9000/signe"

    with patch.object(client, "_get_client", return_value=faux_boto):
        client.get_presigned_url("IMP-1/abc/plan.pdf")
        defaut = faux_boto.generate_presigned_url.call_args.kwargs
        client.get_presigned_url("IMP-1/abc/plan.pdf", 900)
        explicite = faux_boto.generate_presigned_url.call_args.kwargs

    assert defaut["ClientMethod"] == "get_object"
    assert defaut["Params"] == {"Bucket": "seamtech-documents", "Key": "IMP-1/abc/plan.pdf"}
    assert defaut["ExpiresIn"] == 3600, "défaut du client : 1 h"
    assert explicite["ExpiresIn"] == 900, "l'API impose 900 s (15 min)"


def test_signature_publique_de_l_url_presignee_est_le_contrat_des_appelants() -> None:
    """La signature est un contrat : la renommer oblige à mettre à jour TOUS les appelants.

    Appelants recensés le 25/09/2026 : ``seamtech_search/api.py`` (routes
    ``/open`` et ``/imports/{id}/artifacts/{artifact}``),
    ``tests/test_integration_docker.py`` et ``tests/test_storage*.py``.
    """
    parametres = list(inspect.signature(S3StorageClient.get_presigned_url).parameters)
    assert parametres == ["self", "remote_key", "expiration_seconds"]


def test_ecart_d1_api_appelle_l_url_presignee_avec_un_mot_cle_inexistant() -> None:
    """ÉCART CONNU D-1 (audit §7) — mesuré le 25/09/2026, figé ici EXPRÈS.

    ``api.py`` appelle ``get_presigned_url(object_key, expires_in=900)`` alors
    que le paramètre s'appelle ``expiration_seconds``. Conséquence réelle :
    ``TypeError`` → la redirection 302 vers l'URL présignée n'est JAMAIS prise
    en production ; c'est le repli local documenté qui sert la pièce.

    Ce test est écrit pour être SUPPRIMÉ : quand le commanditaire arbitre D-1
    (``api.py`` → ``expiration_seconds=900``), il devient rouge et doit être
    remplacé par son contraire. Tant qu'il est vert, l'écart existe encore.
    """
    source = (RACINE / "seamtech_search" / "api.py").read_text(encoding="utf-8")
    mots_cles = set(re.findall(r"get_presigned_url\([^)]*?(\w+)\s*=", source))
    assert mots_cles == {"expires_in"}, f"appels mesurés dans api.py : {sorted(mots_cles)}"

    valides = set(inspect.signature(S3StorageClient.get_presigned_url).parameters)
    assert not mots_cles & valides, "D-1 corrigé : remplacer ce test par son contraire"

    # Preuve exécutée du TypeError (ce n'est pas une lecture de source).
    client = _client_fictif()
    with patch.object(client, "_get_client", return_value=MagicMock()):
        with pytest.raises(TypeError, match="expires_in"):
            client.get_presigned_url("IMP-1/abc/plan.pdf", expires_in=900)  # type: ignore[call-arg]


def test_url_presignee_n_applique_pas_le_prefixe_contrairement_a_l_envoi() -> None:
    """RISQUE R-4 (audit §7) : asymétrie du préfixe applicatif.

    ``upload_*`` / ``object_exists`` / ``list_keys`` appliquent ``s3_prefix`` ;
    ``get_presigned_url`` / ``download_file`` / ``delete_file`` NON. C'est
    cohérent tant que l'appelant repasse la clé STOCKÉE (qui contient déjà le
    préfixe) — ce que fait le code — mais une clé nue sur ``delete_file``
    viserait un objet hors préfixe. Figé pour qu'une alternative S3 ne change
    pas ce comportement par accident.
    """
    client = _client_fictif(prefix="seamtech")
    faux_boto = MagicMock()
    faux_boto.generate_presigned_url.return_value = "http://minio.invalide:9000/signe"
    faux_boto.head_object.return_value = {}

    with patch.object(client, "_get_client", return_value=faux_boto):
        client.get_presigned_url("IMP-1/abc/plan.pdf")
        client.delete_file("IMP-1/abc/plan.pdf")
        client.object_exists("IMP-1/abc/plan.pdf")

    assert faux_boto.generate_presigned_url.call_args.kwargs["Params"]["Key"] == "IMP-1/abc/plan.pdf"
    assert faux_boto.delete_object.call_args.kwargs["Key"] == "IMP-1/abc/plan.pdf"
    assert faux_boto.head_object.call_args.kwargs["Key"] == "seamtech/IMP-1/abc/plan.pdf"


def test_route_artefact_ne_tombe_jamais_en_500_ni_ne_fuit_les_identifiants(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Bout en bout avec le VRAI client S3 (boto3 doublé) : la pièce est servie, sans fuite.

    Le test ne fige pas le code de statut (200 repli local aujourd'hui à cause
    de l'écart D-1, 302 après correctif) : il fige les deux propriétés qui
    comptent pour une mise en production — aucune erreur serveur, aucun
    identifiant dans la réponse ni dans les journaux.
    """
    rapport = tmp_path / "technical-report.pdf"
    rapport.write_bytes(b"%PDF-1.4 rapport")
    source = tmp_path / "source"
    source.mkdir()
    (source / "fiche.pdf").write_bytes(b"%PDF-1.4 fiche")

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "search.db",
        min_free_bytes=0,
        s3_endpoint_url="http://minio.invalide:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=CLE_ACCES_FICTIVE,
        s3_secret_key=SECRET_FICTIF,
    )
    index = SearchIndex(config.database_path)
    index.initialize()
    index.run_migrations()
    charge = {
        "import_id": "IMP-1",
        "source_path": str(source),
        "status": "completed",
        "report_path": str(rapport),
        "artifacts": [{"name": "technical-report.pdf", "key": "IMP-1/abc/technical-report.pdf"}],
        "files": [],
    }
    with index.connect() as connexion:
        connexion.execute(
            "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            ("IMP-1", str(source), "completed", json.dumps(charge), datetime.now(timezone.utc).isoformat()),
        )
    index.close()

    url_signee = f"http://minio.invalide:9000/seamtech-documents/plan.pdf?X-Amz-Credential={CLE_ACCES_FICTIVE}"
    faux_boto = MagicMock()
    faux_boto.generate_presigned_url.return_value = url_signee
    client_reel = _client_fictif()

    caplog.set_level(logging.DEBUG)
    with patch.object(S3StorageClient, "_get_client", return_value=faux_boto):
        with patch.object(S3StorageClient, "_get_probe_client", return_value=faux_boto):
            with patch("seamtech_search.api.S3StorageClient", return_value=client_reel):
                application = create_app(config)
                with TestClient(application, follow_redirects=False) as client_http:
                    reponse = client_http.get("/imports/IMP-1/artifacts/report_pdf")

    assert reponse.status_code < 500, reponse.text
    assert reponse.status_code in (200, 302)
    corps = reponse.content.decode("utf-8", errors="ignore")
    entetes = " ".join(f"{cle}: {valeur}" for cle, valeur in reponse.headers.items())
    journal = _messages(caplog)
    for texte in (corps, entetes, journal):
        assert SECRET_FICTIF not in texte
    assert CLE_ACCES_FICTIVE not in journal, "une URL présignée journalisée = un secret journalisé"


# ---------------------------------------------------------------------------
# 5. object_key avec espaces et accents
# ---------------------------------------------------------------------------


def test_cle_objet_conserve_espaces_et_accents(tmp_path: Path) -> None:
    """Les noms de l'atelier (« Fiche été n°1 (copie).pdf ») passent tels quels.

    Le nom de fichier est conservé À L'IDENTIQUE dans la clé : c'est lui qui
    sert de nom de téléchargement. Seul le chemin RELATIF est haché, donc deux
    fichiers homonymes dans deux sous-dossiers ne peuvent pas se recouvrir.
    """
    racine = tmp_path / "archive"
    (racine / "sous dossier").mkdir(parents=True)
    fichier = racine / "sous dossier" / "Fiche été n°1 (copie).pdf"
    fichier.write_bytes(b"%PDF-1.4 fiche")

    cle = artifact_object_key("IMP-1", fichier, source_root=racine, prefix="seamtech")

    assert cle.startswith("seamtech/IMP-1/")
    assert cle.endswith("/Fiche été n°1 (copie).pdf")
    assert cle.count("/") == 3
    # Déterminisme : deux appels → la même clé (l'empreinte porte sur le chemin relatif).
    assert cle == artifact_object_key("IMP-1", fichier, source_root=racine, prefix="seamtech")

    autre = racine / "Fiche été n°1 (copie).pdf"
    autre.write_bytes(b"%PDF-1.4 autre")
    assert artifact_object_key("IMP-1", autre, source_root=racine, prefix="seamtech") != cle


def test_cle_avec_espaces_et_accents_transmise_telle_quelle_a_boto3(tmp_path: Path) -> None:
    """L'encodage d'URL est le travail de botocore : la clé stockée reste lisible.

    Vérifié sur les trois opérations qui écrivent ou lisent l'objet — si une
    couche ajoutait un ``quote()``, la clé en base ne désignerait plus l'objet.
    """
    fichier = tmp_path / "Plan été n°2.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    cle = "IMP-1/abc/Plan été n°2.pdf"
    client = _client_fictif()

    faux_boto = MagicMock()
    faux_boto.head_object.side_effect = _absent_objet_404()
    faux_boto.generate_presigned_url.return_value = "http://minio.invalide:9000/signe"

    with patch.object(client, "_get_client", return_value=faux_boto):
        with patch.object(client, "ensure_bucket_exists"):
            assert client.upload_file(fichier, remote_key=cle) == cle
            client.get_presigned_url(cle)
            client.download_file(cle, tmp_path / "copie.pdf")

    assert faux_boto.upload_file.call_args.kwargs["Key"] == cle
    assert faux_boto.generate_presigned_url.call_args.kwargs["Params"]["Key"] == cle
    assert faux_boto.download_file.call_args.kwargs["Key"] == cle
    # Le type MIME est déduit de l'extension malgré les accents.
    assert faux_boto.upload_file.call_args.kwargs["ExtraArgs"] == {"ContentType": "application/pdf"}


def test_normalisation_unicode_differente_produit_des_cles_differentes(tmp_path: Path) -> None:
    """RISQUE R-5 (audit §7) : « été » en NFC et en NFD ne donnent pas la même clé.

    L'empreinte porte sur les OCTETS du chemin relatif. Une archive recopiée
    depuis un poste macOS (NFD) après un premier import depuis Windows (NFC)
    produirait donc des clés différentes pour le même document : doublon
    silencieux dans le bucket, jamais un écrasement (l'objet existant est
    intact). Aucune normalisation n'est appliquée aujourd'hui : c'est un point
    à trancher AVANT l'import de l'archive réelle.
    """
    racine = tmp_path / "archive"
    racine.mkdir()
    nfc = unicodedata.normalize("NFC", "été.pdf")
    nfd = unicodedata.normalize("NFD", "été.pdf")
    assert nfc != nfd and len(nfd) > len(nfc)

    (racine / nfc).write_bytes(b"a")
    cle_nfc = artifact_object_key("IMP-1", racine / nfc, source_root=racine)
    cle_nfd = artifact_object_key("IMP-1", racine / nfd, source_root=racine)

    assert cle_nfc != cle_nfd
    assert cle_nfc.split("/")[1] != cle_nfd.split("/")[1], "empreintes distinctes"


# ---------------------------------------------------------------------------
# 7. Repli local documenté
# ---------------------------------------------------------------------------


def test_sans_stockage_objet_configure_l_envoi_renvoie_not_configured(tmp_path: Path) -> None:
    """Sans stockage objet : statut ``not_configured``, aucune exception, fichiers gardés.

    C'est le repli documenté : l'import continue, les pièces restent sur le
    disque local et ne sont JAMAIS purgées (la purge exige
    ``all_verified``). Un déploiement sans bucket reste utilisable — sans
    protection hors-site, ce que la checklist de release rappelle.
    """
    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    config = AppConfig(root_paths=[tmp_path], min_free_bytes=0)

    lot = upload_artifacts_to_storage("dossier", [fichier], config, import_id="IMP-1")

    assert lot.status == "not_configured"
    assert lot.artifacts == []
    assert lot.all_verified is False, "rien n'est vérifié → aucune purge locale autorisée"
    assert fichier.exists()

    # Aucun fichier exploitable → « not_applicable » (et non « not_configured »).
    assert upload_artifacts_to_storage("dossier", [tmp_path / "absent.pdf"], config).status == "not_applicable"


def test_echec_partiel_interdit_la_purge_locale(tmp_path: Path) -> None:
    """Un seul artefact non vérifié ⇒ ``all_verified`` faux ⇒ pas de purge.

    C'est la garantie qui protège un plan client : ce qui n'est pas relu dans
    le bucket n'autorise jamais la suppression de la copie locale.
    """
    premier = tmp_path / "plan.pdf"
    premier.write_bytes(b"%PDF-1.4 plan")
    second = tmp_path / "tableau.xlsx"
    second.write_bytes(b"xlsx")
    config = AppConfig(
        root_paths=[tmp_path],
        s3_endpoint_url="http://minio.invalide:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=CLE_ACCES_FICTIVE,
        s3_secret_key=SECRET_FICTIF,
        min_free_bytes=0,
    )

    def _envoi(self: S3StorageClient, chemin: Path, remote_key: str | None = None, *a: object, **k: object) -> str:
        return str(remote_key)

    with patch("seamtech_search.storage.S3StorageClient.upload_file", _envoi):
        with patch("seamtech_search.storage.S3StorageClient.object_exists", side_effect=[True, False]):
            lot = upload_artifacts_to_storage("dossier", [premier, second], config, import_id="IMP-1")

    assert lot.status == "partial"
    assert lot.all_verified is False
    assert [a.verified for a in lot.artifacts] == [True, False]
    assert premier.exists() and second.exists()


# ---------------------------------------------------------------------------
# 3 & 8. Démarrage avec une base vide, puis redémarrage
# ---------------------------------------------------------------------------


def test_demarrage_avec_une_base_vide(tmp_path: Path) -> None:
    """Base neuve, aucun stockage objet : /health répond et dit la vérité."""
    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "vide.db",
        min_free_bytes=0,
    )
    application = create_app(config)
    with TestClient(application) as client_http:
        reponse = client_http.get("/health")

    assert reponse.status_code == 200
    sante = reponse.json()
    assert sante["status"] == "ok"
    assert sante["documents"] == 0
    assert sante["s3_configured"] is False
    assert sante["storage_backend"] == "s3", "libellé de configuration, pas un backend réellement actif"
    assert sante["versioning_available"] is None
    assert "not configured" in sante["versioning_detail"]


def test_references_objets_survivent_au_redemarrage(tmp_path: Path) -> None:
    """Redémarrage (docker compose restart) : les clés d'objets restent exploitables.

    Le bucket est la source de vérité, mais c'est la base qui dit OÙ est
    chaque document. Ce test coupe l'index, en rouvre un autre sur le même
    fichier, rejoue les migrations et vérifie que ``object_key`` /
    ``object_bucket`` / ``upload_status`` sont intacts et que la recherche
    retrouve le document.
    """
    racine = tmp_path / "archive"
    racine.mkdir()
    fichier = racine / "plan été.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    cle = "seamtech/IMP-1/abc/plan été.pdf"
    horodatage = time.time()

    index = SearchIndex(tmp_path / "search.db")
    index.initialize()
    index.run_migrations()
    index.upsert_documents(
        [
            Document(
                path=fichier,
                name=fichier.name,
                parent_path=fichier.parent,
                extension=".pdf",
                size=fichier.stat().st_size,
                modified_at=fichier.stat().st_mtime,
                is_dir=False,
                text="grand voile monofilm",
                object_key=cle,
                object_bucket="seamtech-documents",
                uploaded_at=horodatage,
                upload_status="uploaded",
            )
        ]
    )
    index.close()

    redemarre = SearchIndex(tmp_path / "search.db")
    redemarre.initialize()
    redemarre.run_migrations()
    try:
        with redemarre.connect() as connexion:
            ligne = connexion.execute(
                "SELECT object_key, object_bucket, upload_status, uploaded_at FROM documents"
            ).fetchone()
        assert ligne["object_key"] == cle
        assert ligne["object_bucket"] == "seamtech-documents"
        assert ligne["upload_status"] == "uploaded"
        assert ligne["uploaded_at"] == pytest.approx(horodatage, abs=1)
        assert [resultat["name"] for resultat in redemarre.search("monofilm")] == ["plan été.pdf"]
    finally:
        redemarre.close()


# ---------------------------------------------------------------------------
# 9. Migration séquentielle jusqu'à 017
# ---------------------------------------------------------------------------


def test_migrations_sequentielles_001_a_017_sur_base_vide(tmp_path: Path) -> None:
    """Base vide → 001..017 appliquées DANS L'ORDRE, sans trou, et rejouables.

    Le trou, c'est l'incident des Lots K puis M : une migration écrite mais non
    enregistrée dans la liste ⇒ job ``sauvegarde`` rouge. Ce test tourne sans
    PostgreSQL (les migrations métier sont des no-op enregistrés en SQLite),
    donc il protège aussi la suite « non-PostgreSQL ».
    """
    from seamtech_search.schema_metier import MIGRATIONS_METIER, VERSION_SCHEMA_METIER

    index = SearchIndex(tmp_path / "search.db")
    index.initialize()
    index.run_migrations()
    try:
        with index.connect() as connexion:
            versions = [ligne[0] for ligne in connexion.execute("SELECT version FROM schema_migrations")]
        numeros = [int(version.split("_", 1)[0]) for version in sorted(versions)]

        assert numeros == list(range(1, 18)), f"séquence attendue 001..017, mesurée {numeros}"
        assert sorted(versions)[-1] == VERSION_SCHEMA_METIER == "017_ocr_etage3"
        # Toute migration métier déclarée DOIT être enregistrée à l'exécution.
        manquantes = {version for version, _sql in MIGRATIONS_METIER} - set(versions)
        assert manquantes == set(), f"migrations métier déclarées mais jamais appliquées : {manquantes}"

        index.run_migrations()  # rejeu : idempotent, aucun doublon
        with index.connect() as connexion:
            apres = [ligne[0] for ligne in connexion.execute("SELECT version FROM schema_migrations")]
        assert sorted(apres) == sorted(versions)
        assert len(apres) == len(set(apres))
    finally:
        index.close()


def test_version_schema_metier_est_la_derniere_migration_declaree() -> None:
    """``VERSION_SCHEMA_METIER`` doit suivre la dernière migration métier.

    C'est cette valeur que la sauvegarde exige dans ``schema_migrations`` avant
    d'accepter de faire un dump : si elle prend du retard sur la liste, la
    sauvegarde refuse toute base pourtant à jour.
    """
    from seamtech_search.schema_metier import MIGRATIONS_METIER, TABLES_METIER, VERSION_SCHEMA_METIER

    declarees = [version for version, _sql in MIGRATIONS_METIER]
    assert declarees == sorted(declarees), "les migrations métier doivent être déclarées dans l'ordre"
    assert declarees[-1] == VERSION_SCHEMA_METIER
    assert len(TABLES_METIER) == 33, "33 tables métier depuis 017 (ocr_etage3)"
