"""Téléchargement depuis un AUTRE poste : jamais de redirection interne.

Défaut réel trouvé par le test Compose du job CI `integration` (2026-10-07) :

    GET /imports/<id>/artifacts/report_pdf → 302 vers
    http://minio:9000/...   ← nom d'hôte qui n'existe QUE dans le réseau des
    conteneurs. Depuis un poste de l'atelier, la résolution échoue :
    « Temporary failure in name resolution ». Le fichier était pourtant bien
    stocké.

Contrat vérifié ici, sans Docker (magasin S3 en mémoire conservant les vrais
octets) :

1. **sans endpoint public déclaré** — l'API sert les octets elle-même (proxy
   authentifié) : 200, contenu identique, ``Content-Disposition: attachment``,
   **aucun** en-tête ``Location``, et l'endpoint interne n'apparaît nulle part
   dans la réponse ;
2. **avec ``SEAMTECH_S3_PUBLIC_ENDPOINT_URL``** — la redirection présignée est
   autorisée, mais elle pointe alors vers CET endpoint public (celui que les
   navigateurs savent joindre), jamais vers l'endpoint interne ;
3. même règle pour ``/open`` (ouverture d'un original), qui doit en plus servir
   **sans écrire** dans l'archive (l'archive reste en lecture seule).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.indexer import SearchIndex
from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire

ENDPOINT_INTERNE = "http://minio:9000"
ENDPOINT_PUBLIC = "https://minio.atelier.local"
JETON = "jeton-de-service-test"
CONTENU = b"%PDF-1.4 rapport technique (octets reels)\n"


def _config(tmp_path: Path, **extra: object) -> AppConfig:
    parametres: dict[str, object] = {
        "root_paths": [tmp_path],
        "database_path": tmp_path / "search.db",
        "min_free_bytes": 0,
        "auth_token": JETON,
        "storage_backend": "s3",
        "s3_endpoint_url": ENDPOINT_INTERNE,
        "s3_bucket": "seamtech-documents",
        "s3_access_key": "cle-fictive",
        "s3_secret_key": "secret-fictif",
    }
    parametres.update(extra)
    return AppConfig(**parametres)  # type: ignore[arg-type]


@pytest.fixture()
def usine_app(tmp_path: Path, request: pytest.FixtureRequest):  # noqa: ANN201
    """Fabrique d'applications branchées sur un magasin S3 en mémoire.

    Le correctif du client reste actif PENDANT TOUTE LA DURÉE DU TEST : patcher
    seulement autour de ``create_app`` ne suffirait pas — les lectures d'objets
    (``head_object``, ``get_object``, ``download_file``) ont lieu au moment de
    la requête HTTP.
    """
    correctifs: list = []

    def _fabriquer(magasin: S3EnMemoire, *, url_signee: str | None = None, **extra: object) -> TestClient:
        config = _config(tmp_path, **extra)
        branchement = patch.object(
            S3StorageClient, "_get_client", lambda _instance, probe_timeout=None: magasin
        )
        branchement.start()
        correctifs.append(branchement)
        if url_signee is not None:
            signature = patch.object(
                S3StorageClient,
                "get_presigned_url",
                lambda self, key, expiration_seconds=3600, endpoint_url=None: url_signee,
            )
            signature.start()
            correctifs.append(signature)
        return TestClient(create_app(config), follow_redirects=False)

    yield _fabriquer
    for correctif in correctifs:
        correctif.stop()


def _magasin_avec_objet(cle: str) -> S3EnMemoire:
    """Magasin en mémoire contenant réellement les octets de l'objet."""
    magasin = S3EnMemoire()
    magasin.put_object(Bucket="seamtech-documents", Key=cle, Body=CONTENU, Metadata={})
    return magasin


def _semer_import(
    tmp_path: Path,
    cle: str,
    *,
    chemin_rapport: Path | None,
    nom_artefact: str = "technical-report.pdf",
) -> str:
    """Insère un import terminé dont le rapport est DANS le stockage objet."""
    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)
    import_id = "import-rapport-1"
    payload = {
        "status": "completed",
        "report_path": str(chemin_rapport) if chemin_rapport else None,
        "report_docx_path": str(chemin_rapport) if chemin_rapport else None,
        "technical_pdf": str(tmp_path / "fiche.pdf"),
        "artifacts": [{"name": nom_artefact, "key": cle}],
        "files": [],
    }
    with index.connect() as conn:
        conn.execute(
            "INSERT INTO imports (id, source_path, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (import_id, str(tmp_path / "src"), "completed", json.dumps(payload), "2026-10-07T00:00:00+00:00"),
        )
    return import_id


def _entetes_requete() -> dict[str, str]:
    return {"X-SEAMTECH-TOKEN": JETON}


@pytest.mark.parametrize(
    ("artifact", "nom_artefact"),
    [("report_pdf", "technical-report.pdf"), ("report_docx", "technical-report.docx")],
)
def test_artefact_servi_par_l_api_sans_endpoint_public(
    tmp_path: Path, artifact: str, nom_artefact: str, usine_app
) -> None:
    """Copie locale ABSENTE : l'objet doit venir du stockage, par l'API."""
    cle = f"reports/import-rapport-1/{nom_artefact}"
    magasin = _magasin_avec_objet(cle)
    import_id = _semer_import(
        tmp_path, cle, chemin_rapport=tmp_path / "reports" / "absent.pdf", nom_artefact=nom_artefact
    )
    client = usine_app(magasin)

    reponse = client.get(f"/imports/{import_id}/artifacts/{artifact}", headers=_entetes_requete())
    assert reponse.status_code == 200, reponse.text[:400]
    assert reponse.content == CONTENU, "les octets servis doivent être ceux du stockage"
    assert reponse.headers["Content-Disposition"].startswith("attachment")
    assert "Location" not in reponse.headers, "aucune redirection ne doit être émise"
    assert "minio" not in json.dumps(dict(reponse.headers)).lower(), (
        "l'endpoint interne ne doit jamais apparaître dans une réponse navigateur"
    )
    # Le cache local a été reconstitué au passage : le stockage reste la source.
    assert (tmp_path / "reports" / "absent.pdf").read_bytes() == CONTENU


def test_artefact_local_servi_directement_sans_redirection(tmp_path: Path, usine_app) -> None:
    """Copie locale PRÉSENTE : on sert le fichier, toujours sans redirection."""
    rapport = tmp_path / "reports" / "present.pdf"
    rapport.parent.mkdir(parents=True, exist_ok=True)
    rapport.write_bytes(CONTENU)
    cle = "reports/import-rapport-1/technical-report.pdf"
    magasin = _magasin_avec_objet(cle)
    import_id = _semer_import(tmp_path, cle, chemin_rapport=rapport)
    client = usine_app(magasin)

    reponse = client.get(f"/imports/{import_id}/artifacts/report_pdf", headers=_entetes_requete())
    assert reponse.status_code == 200
    assert reponse.content == CONTENU
    assert "Location" not in reponse.headers


def test_artefact_redirige_vers_l_endpoint_public_quand_il_est_declare(tmp_path: Path, usine_app) -> None:
    """Endpoints publics déclarés : la redirection est permise — et publique."""
    cle = "reports/import-rapport-1/technical-report.pdf"
    magasin = _magasin_avec_objet(cle)
    url_signee = f"{ENDPOINT_PUBLIC}/seamtech-documents/{cle}?X-Amz-Signature=fictive"
    import_id = _semer_import(tmp_path, cle, chemin_rapport=tmp_path / "reports" / "absent.pdf")

    client = usine_app(magasin, url_signee=url_signee, s3_public_endpoint_url=ENDPOINT_PUBLIC)
    reponse = client.get(f"/imports/{import_id}/artifacts/report_pdf", headers=_entetes_requete())

    assert reponse.status_code == 302
    assert reponse.headers["Location"].startswith(ENDPOINT_PUBLIC)
    assert "minio:9000" not in reponse.headers["Location"]


def test_ouverture_original_sert_les_octets_sans_ecrire_dans_l_archive(tmp_path: Path, usine_app) -> None:
    """``/open`` sans endpoint public : octets servis, archive non modifiée."""
    from seamtech_search.models import Document

    archive = tmp_path / "archive"
    archive.mkdir()
    original = archive / "plan.pdf"
    original.write_bytes(b"%PDF-1.4 plan initial")

    cle = "documents/plan.pdf"
    magasin = _magasin_avec_objet(cle)

    index = SearchIndex(tmp_path / "search.db")
    index.initialize(rebuild=True)
    index.upsert_documents(
        [
            Document(
                path=original,
                name="plan.pdf",
                parent_path=archive,
                extension=".pdf",
                size=original.stat().st_size,
                modified_at=time.time(),
                is_dir=False,
                text="plan",
                object_key=cle,
                object_bucket="seamtech-documents",
                uploaded_at=time.time(),
                upload_status="uploaded",
            )
        ]
    )
    # L'original est retiré : c'est le cas d'une restauration, où seule la copie
    # objet subsiste. Le fichier NE DOIT PAS être réécrit dans l'archive.
    original.unlink()
    empreinte_arbre_avant = sorted(p.name for p in archive.iterdir())

    client = usine_app(magasin)
    reponse = client.post("/open", params={"path": str(original)}, headers=_entetes_requete())

    assert reponse.status_code == 200, reponse.text[:400]
    assert reponse.content == CONTENU
    assert "Location" not in reponse.headers
    assert sorted(p.name for p in archive.iterdir()) == empreinte_arbre_avant == [], (
        "l'ouverture ne doit JAMAIS écrire dans l'archive (lecture seule)"
    )
