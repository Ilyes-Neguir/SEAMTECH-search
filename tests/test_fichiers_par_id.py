"""Contrat HTTP des pièces accessibles par identifiant de catalogue.

Les tests utilisent des fichiers locaux dans une racine temporaire : pas de
PostgreSQL, de MinIO, de chemin arbitraire fourni par le navigateur ou de secret
S3 dans la réponse.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seamtech_search.api import create_app
from seamtech_search.config import AppConfig
from seamtech_search.fiches.depot import _texte_indexable_fiche
from seamtech_search.fiches.modeles import ChampExtrait, FicheExtraite
from seamtech_search.indexer import SearchIndex
from seamtech_search.models import Document
from seamtech_search.storage import S3StorageClient, StorageError

TOKEN = "test-service-token"
S3_LAST_MODIFIED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_metadata_fiche_indexables_sans_path_local_ni_cle_stockage() -> None:
    fiche = FicheExtraite(
        code="REF-TEST-17",
        client_nom="Atelier Démo",
        bateau_nom="Voilier Exemple",
        fichier_source="/srv/archives/private/client.pdf",
        champs=[
            ChampExtrait(champ="fiche.client_nom", valeur_normalisee="Atelier Démo"),
            ChampExtrait(champ="fiche.fichier_source", valeur_brute="/srv/archives/private/client.pdf"),
            ChampExtrait(champ="fiche.object_key", valeur_brute="private/secret.pdf"),
        ],
    )

    texte = _texte_indexable_fiche(fiche)

    assert "REF-TEST-17" in texte
    assert "Atelier Démo" in texte
    assert "Voilier Exemple" in texte
    assert "/srv/archives" not in texte
    assert "private/secret.pdf" not in texte


def _client_avec_fichiers(
    tmp_path: Path,
    fichiers: list[Path],
    *,
    object_keys: dict[str, str] | None = None,
) -> tuple[TestClient, dict[str, int]]:
    root = tmp_path / "archive"
    root.mkdir(exist_ok=True)
    database = tmp_path / "index.db"
    index = SearchIndex(database)
    index.initialize(rebuild=True)
    ids: dict[str, int] = {}
    for path in fichiers:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"%PDF-1.7 test content")
        document = Document(
            path=path,
            name=path.name,
            parent_path=path.parent,
            extension=path.suffix.lower(),
            size=path.stat().st_size,
            modified_at=path.stat().st_mtime,
            is_dir=False,
            object_key=(object_keys or {}).get(path.name),
            object_bucket="private-bucket" if (object_keys or {}).get(path.name) else None,
        )
        index.upsert_document(document)
        with index.connect() as connexion:
            row = connexion.execute("SELECT id FROM documents WHERE path_key = ?", (document.path_key,)).fetchone()
            ids[path.name] = int(row["id"])
    index.close()
    storage = (
        {
            "s3_endpoint_url": "https://storage.private.invalid",
            "s3_bucket": "private-bucket",
            "s3_access_key": "test-access-secret",
            "s3_secret_key": "test-secret-key",
        }
        if object_keys
        else {}
    )
    config = AppConfig(
        root_paths=[root], database_path=database, auth_token=TOKEN, min_free_bytes=0, **storage
    )
    return TestClient(create_app(config)), ids


def test_piece_preview_range_head_download_et_nom_utf8(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    pdf = root / "CLIENT" / "étude façade.pdf"
    content = b"%PDF-1.7\n0123456789\n%%EOF"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(content)
    client, ids = _client_avec_fichiers(tmp_path, [pdf])
    piece_id = ids[pdf.name]
    headers = {"X-SEAMTECH-TOKEN": TOKEN}

    catalogue = client.get("/pieces", headers=headers)
    assert catalogue.status_code == 200
    ligne = catalogue.json()["pieces"][0]
    assert ligne["id"] == piece_id
    assert not {"path", "path_key", "object_key", "chemin"} & ligne.keys()

    preview = client.get(f"/pieces/{piece_id}/apercu", headers=headers)
    assert preview.status_code == 200
    assert preview.content == content
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.headers["content-disposition"].startswith("inline;")
    assert "filename*=UTF-8''%C3%A9tude%20fa%C3%A7ade.pdf" in preview.headers["content-disposition"]
    assert preview.status_code != 501

    range_response = client.get(
        f"/pieces/{piece_id}/apercu",
        headers={**headers, "Range": "bytes=5-9"},
    )
    assert range_response.status_code == 206
    assert range_response.content == content[5:10]
    assert range_response.headers["content-range"] == f"bytes 5-9/{len(content)}"

    head = client.head(f"/pieces/{piece_id}/apercu", headers=headers)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(content))

    download = client.get(f"/pieces/{piece_id}/telecharger", headers=headers)
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "127.0.0.1" not in download.text


def test_pieces_refusent_les_ids_inconnus_les_chemins_interdits_et_les_formats_non_previsualisables(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    pdf_interdit = tmp_path / "hors-racine" / "secret.pdf"
    fichier_cao = root / "plans" / "coque.dxf"
    client, ids = _client_avec_fichiers(tmp_path, [pdf_interdit, fichier_cao])
    headers = {"X-SEAMTECH-TOKEN": TOKEN}

    inconnu = client.get("/pieces/999999/apercu", headers=headers)
    assert inconnu.status_code == 404
    assert inconnu.status_code != 501

    interdit = client.get(f"/pieces/{ids[pdf_interdit.name]}/apercu", headers=headers)
    assert interdit.status_code == 403

    non_previsualisable = client.get(f"/pieces/{ids[fichier_cao.name]}/apercu", headers=headers)
    assert non_previsualisable.status_code == 415
    assert "Téléchargez" in non_previsualisable.json()["detail"]

    sans_session = client.get(f"/pieces/{ids[fichier_cao.name]}/telecharger")
    assert sans_session.status_code == 401


def test_fichier_devenu_indisponible_retourne_404(tmp_path: Path) -> None:
    pdf = tmp_path / "archive" / "CLIENT" / "disparu.pdf"
    client, ids = _client_avec_fichiers(tmp_path, [pdf])
    pdf.unlink()

    response = client.get(
        f"/pieces/{ids[pdf.name]}/apercu",
        headers={"X-SEAMTECH-TOKEN": TOKEN},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Fichier non disponible."
    assert str(tmp_path) not in response.text


def test_piece_stockee_s3_est_servie_par_id_sans_redirection_ni_secret(tmp_path: Path, monkeypatch) -> None:
    content = b"%PDF-1.7\nS3 bytes served through the authenticated API\n%%EOF"
    object_key = "imports-private/secret/fiche.pdf"
    remote_pdf = tmp_path / "archive" / "CLIENT" / "fiche.pdf"
    fake_s3 = _FauxS3(content)
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda _self: fake_s3)
    client, ids = _client_avec_fichiers(
        tmp_path,
        [remote_pdf],
        object_keys={remote_pdf.name: object_key},
    )
    piece_id = ids[remote_pdf.name]
    headers = {"X-SEAMTECH-TOKEN": TOKEN}

    catalogue = client.get("/pieces", headers=headers)
    assert catalogue.status_code == 200
    assert catalogue.json()["pieces"][0]["id"] == piece_id
    assert not {"path", "path_key", "object_key", "chemin"} & catalogue.json()["pieces"][0].keys()
    assert "storage.private.invalid" not in catalogue.text
    assert "test-access-secret" not in catalogue.text
    assert "test-secret-key" not in catalogue.text
    assert object_key not in catalogue.text

    preview = client.get(f"/pieces/{piece_id}/apercu", headers=headers)
    assert preview.status_code == 200
    assert preview.content == content
    assert preview.headers["content-disposition"].startswith("inline;")
    assert "location" not in preview.headers
    assert "storage.private.invalid" not in preview.text
    assert object_key not in preview.text

    range_response = client.get(
        f"/pieces/{piece_id}/apercu",
        headers={**headers, "Range": "bytes=5-9"},
    )
    assert range_response.status_code == 206
    assert range_response.content == content[5:10]
    assert range_response.headers["content-range"] == f"bytes 5-9/{len(content)}"
    assert range_response.headers["last-modified"] == "Thu, 01 Oct 2026 12:00:00 GMT"

    head = client.head(f"/pieces/{piece_id}/apercu", headers=headers)
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(content))
    assert head.headers["last-modified"] == "Thu, 01 Oct 2026 12:00:00 GMT"
    assert "location" not in head.headers

    download = client.get(f"/pieces/{piece_id}/telecharger", headers=headers)
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-disposition"].startswith("attachment;")
    assert "location" not in download.headers
    assert fake_s3.get_object_calls[0] == {
        "Bucket": "private-bucket",
        "Key": object_key,
    }
    assert fake_s3.get_object_calls[1] == {
        "Bucket": "private-bucket",
        "Key": object_key,
        "Range": "bytes=5-9",
    }
    assert fake_s3.head_object_calls == [{"Bucket": "private-bucket", "Key": object_key}]


def test_catalogue_filtre_recherche_extension_dossier_et_pagination(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    fichiers = [
        root / "CLIENT-A" / "voile-1.pdf",
        root / "CLIENT-A" / "voile-2.pdf",
        root / "AUTRE" / "voile-3.pdf",
        root / "CLIENT-A" / "plan.dxf",
        root / "CLIENT-A" / "facture.xlsx",
        root / "CLIENT-A" / "mesures.csv",
        root / "CLIENT-A" / "photo.png",
    ]
    client, _ids = _client_avec_fichiers(tmp_path, fichiers)

    page_1 = client.get(
        "/pieces",
        params={"q": "voile", "extension": "pdf", "dossier": "CLIENT-A", "limit": 1, "offset": 0},
        headers={"X-SEAMTECH-TOKEN": TOKEN},
    )
    assert page_1.status_code == 200
    payload_1 = page_1.json()
    assert payload_1["total"] == 2
    assert payload_1["has_more"] is True
    assert len(payload_1["pieces"]) == 1
    assert payload_1["pieces"][0]["extension"] == ".pdf"
    assert payload_1["pieces"][0]["dossier"] == "CLIENT-A"
    assert ".pdf" in payload_1["extensions"]
    assert "CLIENT-A" in payload_1["dossiers"]
    assert str(root) not in page_1.text

    page_2 = client.get(
        "/pieces",
        params={"q": "voile", "extension": "pdf", "dossier": "CLIENT-A", "limit": 1, "offset": 1},
        headers={"X-SEAMTECH-TOKEN": TOKEN},
    )
    assert page_2.status_code == 200
    assert page_2.json()["offset"] == 1
    assert page_2.json()["has_more"] is False
    assert page_2.json()["pieces"][0]["id"] != payload_1["pieces"][0]["id"]

    headers = {"X-SEAMTECH-TOKEN": TOKEN}
    tous = client.get("/pieces", headers=headers).json()["pieces"]
    par_nom = {piece["name"]: piece for piece in tous}
    assert par_nom["plan.dxf"]["kind"] == "machine"
    assert par_nom["facture.xlsx"]["kind"] == "excel"
    assert par_nom["photo.png"]["kind"] == "other"
    assert par_nom["photo.png"]["previewable"] is True
    csv_id = par_nom["mesures.csv"]["id"]
    csv_preview = client.get(f"/pieces/{csv_id}/apercu", headers=headers)
    assert csv_preview.status_code == 200
    assert csv_preview.headers["content-type"] == "text/csv; charset=utf-8"


@pytest.mark.parametrize(
    ("range_header", "expected"),
    [("bytes=5-", b"nt"), ("bytes=-5", b"ntent"), ("bytes=5-999", b"nt")],
)
def test_plages_ouvertes_et_suffixes(tmp_path: Path, range_header: str, expected: bytes) -> None:
    pdf = tmp_path / "archive" / "CLIENT" / "plage.pdf"
    content = b"content"
    client, ids = _client_avec_fichiers(tmp_path, [pdf])
    pdf.write_bytes(content)
    response = client.get(
        f"/pieces/{ids[pdf.name]}/apercu",
        headers={"X-SEAMTECH-TOKEN": TOKEN, "Range": range_header},
    )
    assert response.status_code == 206
    assert response.content == expected
    assert response.headers["content-range"] == f"bytes {len(content) - len(expected)}-{len(content) - 1}/{len(content)}"


def test_plage_sur_fichier_vide_retourne_416(tmp_path: Path) -> None:
    pdf = tmp_path / "archive" / "CLIENT" / "vide.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"")
    client, ids = _client_avec_fichiers(tmp_path, [pdf])

    response = client.get(
        f"/pieces/{ids[pdf.name]}/apercu",
        headers={"X-SEAMTECH-TOKEN": TOKEN, "Range": "bytes=0-0"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */0"


def test_flux_fichier_sarrete_si_la_plage_depasse_le_fichier(tmp_path: Path) -> None:
    from seamtech_search.fiches.routes import _flux_fichier

    pdf = tmp_path / "reduit.pdf"
    pdf.write_bytes(b"trois")

    assert b"".join(_flux_fichier(pdf, 0, 50)) == b"trois"


@pytest.mark.parametrize("range_header", ["items=0-1", "bytes=100-200", "bytes=-0"])
def test_plages_invalides_refusees_en_416(tmp_path: Path, range_header: str) -> None:
    pdf = tmp_path / "archive" / "CLIENT" / "plage.pdf"
    client, ids = _client_avec_fichiers(tmp_path, [pdf])
    response = client.get(
        f"/pieces/{ids[pdf.name]}/apercu",
        headers={"X-SEAMTECH-TOKEN": TOKEN, "Range": range_header},
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{pdf.stat().st_size}"
    assert "path" not in response.text


def test_s3_head_et_stream_renvoient_502_sans_details_de_stockage(tmp_path: Path, monkeypatch) -> None:
    class StockageIndisponible:
        def head_object(self, **_params):
            raise RuntimeError("private-key-and-endpoint")

        def get_object(self, **_params):
            raise RuntimeError("private-key-and-endpoint")

    failing_s3 = StockageIndisponible()
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda _self: failing_s3)
    storage = S3StorageClient(bucket_name="private-bucket")
    with pytest.raises(StorageError, match="Object metadata lookup failed"):
        storage.head_object("imports/private.pdf")
    with pytest.raises(StorageError, match="Object stream lookup failed"):
        storage.get_object("imports/private.pdf", range_header="bytes=0-4")

    path = tmp_path / "archive" / "CLIENT" / "private.pdf"
    client, ids = _client_avec_fichiers(
        tmp_path,
        [path],
        object_keys={path.name: "private/key.pdf"},
    )
    headers = {"X-SEAMTECH-TOKEN": TOKEN}
    head = client.head(f"/pieces/{ids[path.name]}/apercu", headers=headers)
    get = client.get(f"/pieces/{ids[path.name]}/apercu", headers=headers)
    assert head.status_code == get.status_code == 502
    assert "private-key-and-endpoint" not in head.text + get.text
    assert "private/key.pdf" not in head.text + get.text
    assert "storage.private.invalid" not in head.text + get.text


class _FauxCorpsS3:
    def __init__(self, contenu: bytes):
        self.contenu = contenu

    def iter_chunks(self, chunk_size: int):
        for debut in range(0, len(self.contenu), chunk_size):
            yield self.contenu[debut : debut + chunk_size]


class _FauxS3:
    def __init__(self, contenu: bytes):
        self.contenu = contenu
        self.get_object_calls: list[dict[str, str]] = []
        self.head_object_calls: list[dict[str, str]] = []

    def get_object(self, **params: str):
        self.get_object_calls.append(params)
        range_header = params.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", range_header)
            assert match is not None
            debut, fin = int(match.group(1)), int(match.group(2))
            contenu = self.contenu[debut : fin + 1]
            return {
                "ContentRange": f"bytes {debut}-{fin}/{len(self.contenu)}",
                "ContentLength": len(contenu),
                "ETag": '"fake-etag"',
                "LastModified": S3_LAST_MODIFIED,
                "Body": _FauxCorpsS3(contenu),
            }
        return {
            "ContentLength": len(self.contenu),
            "ETag": '"fake-etag"',
            "LastModified": S3_LAST_MODIFIED,
            "Body": _FauxCorpsS3(self.contenu),
        }

    def head_object(self, **params: str):
        self.head_object_calls.append(params)
        return {"ContentLength": len(self.contenu), "ETag": '"fake-etag"', "LastModified": S3_LAST_MODIFIED}


@pytest.mark.postgres
def test_postgres_catalogue_apercu_head_et_range_par_id(tmp_path: Path, monkeypatch) -> None:
    """Couvre les chemins PostgreSQL de l'API fichier, avec un S3 simulé."""
    database_url = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    from seamtech_search.fiches.modeles import FicheExtraite
    from seamtech_search.fiches.persistance import ecrire_fiche

    name = f"piece_api_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(database_url)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    database_url_test = database_url.rsplit("/", 1)[0] + f"/{name}"
    root = tmp_path / "archive"
    local_pdf = root / "CLIENT" / "voile-local.pdf"
    remote_pdf = root / "CLIENT" / "voile-s3.pdf"
    local_pdf.parent.mkdir(parents=True)
    local_content = b"%PDF-1.7\nlocal source\n%%EOF"
    local_pdf.write_bytes(local_content)
    remote_pdf.write_bytes(b"%PDF-1.7 remote placeholder")
    remote_content = b"%PDF-1.7\nremote object stream\n%%EOF"
    fake_s3 = _FauxS3(remote_content)
    monkeypatch.setattr(S3StorageClient, "_get_client", lambda _self: fake_s3)

    index = SearchIndex(tmp_path / "index-unused.db", database_url_test)
    try:
        index.initialize()
        index.run_migrations()
        index.upsert_document(
            Document(
                path=local_pdf,
                name=local_pdf.name,
                parent_path=local_pdf.parent,
                extension=".pdf",
                size=local_pdf.stat().st_size,
                modified_at=local_pdf.stat().st_mtime,
                is_dir=False,
            )
        )
        index.upsert_document(
            Document(
                path=remote_pdf,
                name=remote_pdf.name,
                parent_path=remote_pdf.parent,
                extension=".pdf",
                size=len(remote_content),
                modified_at=remote_pdf.stat().st_mtime,
                is_dir=False,
                object_key="private/objects/voile-s3.pdf",
                object_bucket="private-bucket",
            )
        )
        attachment_pdf = root / "CLIENT" / "schema-atelier.pdf"
        attachment_pdf.write_bytes(b"%PDF-1.7 attachment bytes")
        index.upsert_document(
            Document(
                path=attachment_pdf,
                name=attachment_pdf.name,
                parent_path=attachment_pdf.parent,
                extension=".pdf",
                size=attachment_pdf.stat().st_size,
                modified_at=attachment_pdf.stat().st_mtime,
                is_dir=False,
                object_key="private/objects/schema-atelier.pdf",
                object_bucket="private-bucket",
            )
        )
        id_fiche, _action = ecrire_fiche(
            index,
            FicheExtraite(code="PIECE-API-001", titre="Fiche synthétique", fichier_source=str(local_pdf)),
        )
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT id FROM documents WHERE name = %s", (attachment_pdf.name,))
                attachment_id = int(cursor.fetchone()[0])
                cursor.execute(
                    "INSERT INTO fiche_piece_jointe "
                    "(id_fiche, id_document, chemin, role, empreinte_sha256, taille_octets) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (id_fiche, attachment_id, str(attachment_pdf), "plan", "sha256-synthetic", attachment_pdf.stat().st_size),
                )
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                cursor.execute("SELECT id FROM documents WHERE name = %s", (remote_pdf.name,))
                remote_id = int(cursor.fetchone()[0])

        config = AppConfig(
            root_paths=[root],
            database_path=tmp_path / "api-unused.db",
            database_url=database_url_test,
            auth_token=TOKEN,
            min_free_bytes=0,
            s3_endpoint_url="https://storage.private.invalid",
            s3_bucket="private-bucket",
            s3_access_key="test-access-secret",
            s3_secret_key="test-secret-key",
        )
        with TestClient(create_app(config)) as client:
            headers = {"X-SEAMTECH-TOKEN": TOKEN}
            catalogue = client.get(
                "/pieces",
                params={"q": "voile", "extension": "pdf", "dossier": "CLIENT", "limit": 1},
                headers=headers,
            )
            assert catalogue.status_code == 200
            assert catalogue.json()["total"] == 2
            assert catalogue.json()["has_more"] is True
            assert str(root) not in catalogue.text
            assert "private/objects/voile-s3.pdf" not in catalogue.text

            fiche_pieces = client.get("/fiches/PIECE-API-001/pieces", headers=headers)
            assert fiche_pieces.status_code == 200
            piece_rows = fiche_pieces.json()["pieces"]
            assert {piece["name"] for piece in piece_rows} == {local_pdf.name, attachment_pdf.name}
            assert sum(piece["is_primary_pdf"] for piece in piece_rows) == 1
            attachment_row = next(piece for piece in piece_rows if piece["id"] == attachment_id)
            assert attachment_row["role"] == "plan"
            assert attachment_row["empreinte_sha256"] == "sha256-synthetic"
            assert fiche_pieces.json()["pdf_source"] is None
            assert fiche_pieces.json()["fichier_source"] is None
            assert str(root) not in fiche_pieces.text
            assert "private/objects/schema-atelier.pdf" not in fiche_pieces.text

            head = client.head(f"/pieces/{remote_id}/apercu", headers=headers)
            assert head.status_code == 200
            assert head.headers["content-length"] == str(len(remote_content))
            assert head.headers["etag"] == '"fake-etag"'
            assert head.headers["last-modified"] == "Thu, 01 Oct 2026 12:00:00 GMT"

            ranged = client.get(
                f"/pieces/{remote_id}/apercu",
                headers={**headers, "Range": "bytes=5-9"},
            )
            assert ranged.status_code == 206
            assert ranged.content == remote_content[5:10]
            assert ranged.headers["content-range"] == f"bytes 5-9/{len(remote_content)}"
            assert ranged.headers["last-modified"] == "Thu, 01 Oct 2026 12:00:00 GMT"

            local_id_row = client.get("/pieces", params={"q": "voile-local.pdf"}, headers=headers).json()
            local_id = next(piece["id"] for piece in local_id_row["pieces"] if piece["name"] == local_pdf.name)
            local = client.get(f"/pieces/{local_id}/apercu", headers=headers)
            assert local.status_code == 200
            assert local.content == local_content
    finally:
        index.close()
        admin = psycopg2.connect(database_url)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                    (name,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            admin.close()
