"""Correctif D-1 — la redirection 302 vers une URL présignée est réellement servie.

Le défaut (mesuré le 25/09/2026, audit §6) : ``api.py`` appelait
``get_presigned_url(object_key, expires_in=900)`` alors que le paramètre du
client s'appelle ``expiration_seconds``. Le ``TypeError`` était avalé par le
``except Exception`` du repli, si bien que **la redirection 302 annoncée par la
documentation n'était jamais servie** : chaque pièce transitait par l'API.

Pourquoi les tests existants ne l'attrapaient pas : ils remplacent le client de
stockage par un ``MagicMock``, qui accepte n'importe quel mot-clé. Ici, le
client est le **vrai** ``S3StorageClient`` ; seul ``boto3`` est doublé. Un
mot-clé erroné rend donc ces tests rouges, immédiatement.

Ce que ce fichier prouve, pour les DEUX routes concernées
(``POST /open`` et ``GET /imports/{id}/artifacts/{artifact}``) :

1. objet présent et présignature disponible → **302**, en-tête ``Location``,
   URL présignée réellement produite, **expiration 900 s** transmise à boto3 ;
2. présignature indisponible → repli existant respecté, aucune exception non
   contrôlée, **aucun secret** dans la réponse ni dans les journaux ;
3. objet absent → comportement inchangé, **jamais de faux 302**.

Aucun réseau, aucun conteneur, aucun vrai secret (RG14) : les identifiants sont
des chaînes fictives, reconnaissables pour qu'une fuite se trouve au ``grep``.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
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
from seamtech_search.storage import S3StorageClient

RACINE = Path(__file__).resolve().parent.parent

CLE_ACCES_FICTIVE = "CLE-ACCES-FICTIVE-TEST"
SECRET_FICTIF = "SECRET-FICTIF-TEST-NE-PAS-UTILISER"

#: Durée imposée par l'API (15 minutes). Le lot interdit de la changer : elle
#: est vérifiée jusqu'au paramètre ``ExpiresIn`` passé à boto3.
EXPIRATION_ATTENDUE = 900

#: URL rendue par le double boto3. Elle porte volontairement un
#: ``X-Amz-Credential`` : c'est le cas réel, et cela permet de vérifier qu'une
#: URL présignée ne part JAMAIS dans les journaux.
URL_PRESIGNEE = (
    "http://minio.invalide:9000/seamtech-documents/IMP-1/abc/technical-report.pdf"
    f"?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential={CLE_ACCES_FICTIVE}%2F20260925%2Fus-east-1%2Fs3"
    "%2Faws4_request&X-Amz-Expires=900&X-Amz-Signature=abcdef"
)


# ---------------------------------------------------------------------------
# Montage commun : vrai client S3, boto3 doublé, aucune sortie réseau
# ---------------------------------------------------------------------------


def _config(tmp_path: Path, nom_base: str) -> AppConfig:
    return AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / nom_base,
        min_free_bytes=0,
        s3_endpoint_url="http://minio.invalide:9000",
        s3_bucket="seamtech-documents",
        s3_access_key=CLE_ACCES_FICTIVE,
        s3_secret_key=SECRET_FICTIF,
    )


def _client_reel() -> S3StorageClient:
    """Le VRAI client (c'est lui qui vérifie le nom des mots-clés)."""
    return S3StorageClient(
        endpoint_url="http://minio.invalide:9000",
        bucket_name="seamtech-documents",
        access_key_id=CLE_ACCES_FICTIVE,
        secret_access_key=SECRET_FICTIF,
    )


def _boto3_double(*, url: str | None = URL_PRESIGNEE, erreur: Exception | None = None) -> MagicMock:
    double = MagicMock()
    if erreur is not None:
        double.generate_presigned_url.side_effect = erreur
    else:
        double.generate_presigned_url.return_value = url
    double.head_object.return_value = {}
    double.get_bucket_versioning.return_value = {"Status": "Enabled"}
    return double


def _import_en_base(config: AppConfig, tmp_path: Path, *, avec_cle: bool, rapport_existe: bool) -> Path:
    """Insère un import ``IMP-1`` et renvoie le chemin du rapport attendu."""
    rapport = tmp_path / "technical-report.pdf"
    if rapport_existe:
        rapport.write_bytes(b"%PDF-1.4 rapport local")
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "fiche.pdf").write_bytes(b"%PDF-1.4 fiche")

    index = SearchIndex(config.database_path)
    index.initialize()
    index.run_migrations()
    charge: dict[str, object] = {
        "import_id": "IMP-1",
        "source_path": str(source),
        "status": "completed",
        "report_path": str(rapport),
        "artifacts": (
            [{"name": "technical-report.pdf", "key": "IMP-1/abc/technical-report.pdf"}] if avec_cle else []
        ),
        "files": [],
    }
    with index.connect() as connexion:
        connexion.execute(
            "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            ("IMP-1", str(source), "completed", json.dumps(charge), datetime.now(timezone.utc).isoformat()),
        )
    index.close()
    return rapport


def _document_en_base(config: AppConfig, fichier: Path, *, object_key: str | None) -> None:
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
                text="contenu",
                object_key=object_key,
                object_bucket="seamtech-documents" if object_key else None,
                uploaded_at=time.time() if object_key else None,
                upload_status="uploaded" if object_key else "pending",
            )
        ]
    )
    index.close()


def _appeler(config: AppConfig, double_boto3: MagicMock, appel) -> object:
    """Monte l'application avec le vrai client S3 branché sur le double boto3."""
    client_reel = _client_reel()
    with patch.object(S3StorageClient, "_get_client", return_value=double_boto3):
        with patch.object(S3StorageClient, "_get_probe_client", return_value=double_boto3):
            with patch("seamtech_search.api.S3StorageClient", return_value=client_reel):
                application = create_app(config)
                with TestClient(application, follow_redirects=False) as http:
                    return appel(http)


def _journal(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(enregistrement.getMessage() for enregistrement in caplog.records)


def _aucun_secret(reponse, journal: str) -> None:
    corps = reponse.content.decode("utf-8", errors="ignore")
    entetes = " ".join(f"{cle}: {valeur}" for cle, valeur in reponse.headers.items())
    assert SECRET_FICTIF not in corps, "secret dans le corps de la réponse"
    assert SECRET_FICTIF not in entetes, "secret dans les en-têtes"
    assert SECRET_FICTIF not in journal, "secret dans les journaux"
    assert CLE_ACCES_FICTIVE not in journal, "URL présignée (donc X-Amz-Credential) journalisée"


# ---------------------------------------------------------------------------
# 1. Route artefacts — api.py l.988
# ---------------------------------------------------------------------------


def test_artefact_objet_present_redirige_en_302_vers_l_url_presignee(tmp_path: Path) -> None:
    """Cas nominal : 302, ``Location``, URL présignée, expiration 900 s.

    Le fichier local existe AUSSI : s'il était servi (200) au lieu de la
    redirection, le test échouerait — c'est précisément le symptôme de D-1.
    """
    config = _config(tmp_path, "artefact_302.db")
    _import_en_base(config, tmp_path, avec_cle=True, rapport_existe=True)
    double = _boto3_double()

    reponse = _appeler(config, double, lambda http: http.get("/imports/IMP-1/artifacts/report_pdf"))

    assert reponse.status_code == 302, f"D-1 : la redirection présignée n'est pas servie ({reponse.status_code})"
    assert reponse.headers["location"] == URL_PRESIGNEE
    double.generate_presigned_url.assert_called_once()
    arguments = double.generate_presigned_url.call_args.kwargs
    assert arguments["ClientMethod"] == "get_object"
    assert arguments["Params"] == {"Bucket": "seamtech-documents", "Key": "IMP-1/abc/technical-report.pdf"}
    assert arguments["ExpiresIn"] == EXPIRATION_ATTENDUE, "la durée de 900 s ne doit pas changer"


def test_artefact_302_est_tracee_dans_le_journal_d_audit(tmp_path: Path) -> None:
    """La redirection reste traçable : l'audit enregistre bien un 302."""
    config = _config(tmp_path, "artefact_audit.db")
    _import_en_base(config, tmp_path, avec_cle=True, rapport_existe=True)

    def scenario(http: TestClient) -> dict:
        http.get("/imports/IMP-1/artifacts/report_pdf")
        return http.get("/audit").json()

    audit = _appeler(config, _boto3_double(), scenario)
    evenements = [e for e in audit["results"] if e.get("action") == "artifact_download"]
    assert evenements, "aucun événement d'audit pour le téléchargement d'artefact"
    assert evenements[0]["status"] == "302"


def test_artefact_presignature_indisponible_sert_le_repli_sans_exception(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Présignature impossible → repli local (200), sans 500 ni fuite d'identifiant."""
    config = _config(tmp_path, "artefact_repli.db")
    rapport = _import_en_base(config, tmp_path, avec_cle=True, rapport_existe=True)
    refus = ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject")

    caplog.set_level(logging.DEBUG)
    reponse = _appeler(
        config, _boto3_double(erreur=refus), lambda http: http.get("/imports/IMP-1/artifacts/report_pdf")
    )

    assert reponse.status_code == 200, "le repli documenté doit servir la pièce"
    assert reponse.content == rapport.read_bytes()
    assert "location" not in {cle.lower() for cle in reponse.headers}
    journal = _journal(caplog)
    assert "Failed to generate presigned URL" in journal, "l'échec doit rester visible dans les journaux"
    _aucun_secret(reponse, journal)


def test_artefact_absent_ne_produit_jamais_un_faux_302(tmp_path: Path) -> None:
    """Ni clé d'objet, ni fichier local → 404 franc, jamais une redirection."""
    config = _config(tmp_path, "artefact_absent.db")
    _import_en_base(config, tmp_path, avec_cle=False, rapport_existe=False)
    double = _boto3_double()

    reponse = _appeler(config, double, lambda http: http.get("/imports/IMP-1/artifacts/report_pdf"))

    assert reponse.status_code == 404
    assert "location" not in {cle.lower() for cle in reponse.headers}
    double.generate_presigned_url.assert_not_called()


def test_artefact_inconnu_de_la_liste_blanche_reste_refuse(tmp_path: Path) -> None:
    """Le contrat d'API ne change pas : seuls 4 artefacts sont servis."""
    config = _config(tmp_path, "artefact_liste.db")
    _import_en_base(config, tmp_path, avec_cle=True, rapport_existe=True)

    reponse = _appeler(config, _boto3_double(), lambda http: http.get("/imports/IMP-1/artifacts/../etc/passwd"))

    assert reponse.status_code in (400, 404), reponse.status_code
    assert "location" not in {cle.lower() for cle in reponse.headers}


# ---------------------------------------------------------------------------
# 2. Route /open — api.py l.477
# ---------------------------------------------------------------------------


def test_open_objet_present_redirige_en_302_vers_l_url_presignee(tmp_path: Path) -> None:
    """Même correctif, même preuve, sur la seconde route signalée par l'audit."""
    config = _config(tmp_path, "open_302.db")
    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    _document_en_base(config, fichier, object_key="IMP-1/abc/plan.pdf")
    double = _boto3_double()

    reponse = _appeler(config, double, lambda http: http.post(f"/open?path={fichier}"))

    assert reponse.status_code == 302, f"D-1 : /open ne redirige pas ({reponse.status_code})"
    assert reponse.headers["location"] == URL_PRESIGNEE
    arguments = double.generate_presigned_url.call_args.kwargs
    assert arguments["Params"] == {"Bucket": "seamtech-documents", "Key": "IMP-1/abc/plan.pdf"}
    assert arguments["ExpiresIn"] == EXPIRATION_ATTENDUE


def test_open_presignature_indisponible_sert_le_fichier_local(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Repli inchangé : le fichier local est servi, sans exception ni fuite."""
    config = _config(tmp_path, "open_repli.db")
    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    _document_en_base(config, fichier, object_key="IMP-1/abc/plan.pdf")
    refus = ClientError({"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "GetObject")

    caplog.set_level(logging.DEBUG)
    reponse = _appeler(config, _boto3_double(erreur=refus), lambda http: http.post(f"/open?path={fichier}"))

    assert reponse.status_code == 200
    assert reponse.content == fichier.read_bytes()
    journal = _journal(caplog)
    assert "Failed presigned URL for open" in journal
    _aucun_secret(reponse, journal)


def test_open_sans_cle_d_objet_ne_redirige_pas(tmp_path: Path) -> None:
    """Document connu mais sans ``object_key`` : fichier servi, aucun faux 302."""
    config = _config(tmp_path, "open_sans_cle.db")
    fichier = tmp_path / "plan.pdf"
    fichier.write_bytes(b"%PDF-1.4 plan")
    _document_en_base(config, fichier, object_key=None)
    double = _boto3_double()

    reponse = _appeler(config, double, lambda http: http.post(f"/open?path={fichier}"))

    assert reponse.status_code == 200
    double.generate_presigned_url.assert_not_called()


def test_open_chemin_inexistant_reste_un_404(tmp_path: Path) -> None:
    """Comportement existant préservé : un chemin absent est refusé avant tout S3."""
    config = _config(tmp_path, "open_404.db")
    index = SearchIndex(config.database_path)
    index.initialize(rebuild=True)
    index.close()
    double = _boto3_double()

    reponse = _appeler(config, double, lambda http: http.post(f"/open?path={tmp_path / 'fantome.pdf'}"))

    assert reponse.status_code == 404
    double.generate_presigned_url.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Contrat d'appel : plus aucun mot-clé inexistant (remplace le test d'écart D-1)
# ---------------------------------------------------------------------------


def test_tous_les_appels_de_get_presigned_url_utilisent_le_mot_cle_reel() -> None:
    """Balayage du code : aucun appelant n'invente de nom de paramètre.

    Ce test remplace ``test_ecart_d1_…``, qui figeait le défaut. Il couvre
    TOUTES les occurrences du paquet, pas seulement les deux routes signalées
    par l'audit (l.477 et l.988).
    """
    valides = set(inspect.signature(S3StorageClient.get_presigned_url).parameters)
    fautifs: list[str] = []
    sites: list[str] = []
    for fichier in sorted((RACINE / "seamtech_search").rglob("*.py")):
        source = fichier.read_text(encoding="utf-8")
        for appel in re.findall(r"(?<!def )get_presigned_url\(([^)]*)\)", source):
            sites.append(str(fichier.relative_to(RACINE)))
            for mot_cle in re.findall(r"(\w+)\s*=", appel):
                if mot_cle not in valides:
                    fautifs.append(f"{fichier.relative_to(RACINE)} : get_presigned_url(..., {mot_cle}=...)")
    assert sites == ["seamtech_search/api.py", "seamtech_search/api.py"], (
        "les appelants recensés le 25/09/2026 sont les deux routes de api.py ; "
        f"appelants trouvés : {sites} — un nouvel appelant doit être ajouté ici ET testé"
    )
    assert not fautifs, "mot-clé inexistant (le TypeError serait avalé par le repli) :\n" + "\n".join(fautifs)


def test_les_deux_routes_signalees_par_l_audit_passent_bien_900_secondes() -> None:
    """La durée de 900 s est inchangée par le correctif (exigence du lot)."""
    source = (RACINE / "seamtech_search" / "api.py").read_text(encoding="utf-8")
    appels = re.findall(r"get_presigned_url\(object_key,\s*expiration_seconds=(\d+)\)", source)
    assert appels == ["900", "900"], f"attendu deux appels à 900 s (/open et artefacts), trouvé : {appels}"


def test_un_mot_cle_inexistant_leve_bien_une_erreur_sur_le_vrai_client() -> None:
    """Preuve exécutée du mécanisme : ce n'est pas une lecture de source.

    Si un jour quelqu'un réintroduit ``expires_in=``, c'est ce ``TypeError``
    qui sera avalé par le repli — d'où le test précédent.
    """
    client = _client_reel()
    with patch.object(S3StorageClient, "_get_client", return_value=_boto3_double()):
        with pytest.raises(TypeError, match="expires_in"):
            client.get_presigned_url("IMP-1/abc/plan.pdf", expires_in=900)  # type: ignore[call-arg]
        assert client.get_presigned_url("IMP-1/abc/plan.pdf", expiration_seconds=900) == URL_PRESIGNEE
